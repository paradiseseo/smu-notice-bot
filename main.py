# main.py
import os, re, json, time, hashlib
from urllib.parse import urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup

BASE = "https://www.smu.ac.kr"
LIST_URL = "https://www.smu.ac.kr/kor/life/notice.do"
USER_AGENT = "Mozilla/5.0 (compatible; smu-notice-bot/1.0; +https://www.smu.ac.kr)"
TIMEOUT = 20

STATE_PATH = "state.json"
MAX_SEND_PER_RUN = 10      # 1회 실행 시 최대 전송 개수 (스팸 방지)
KEYWORDS = [               # (선택) 필터링 키워드 — 초기에는 모두 전송하려면 빈 리스트로 두세요.
    # r"장학", r"등록", r"수강", r"채용", r"모집", r"공모전", r"대회", r"행사"
]

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
if not WEBHOOK_URL:
    raise SystemExit("환경변수 DISCORD_WEBHOOK_URL 이 설정되지 않았습니다.")

def send_test():
    import requests
    requests.post(WEBHOOK_URL, json={"content":"🧪 FORCE_SEND 테스트"}, timeout=TIMEOUT)

if os.environ.get("FORCE_SEND") == "1":
    print("[DEBUG] FORCE_SEND=1 → 테스트 메시지 전송")
    send_test()

def load_seen():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            return set(data.get("seen", []))
    except FileNotFoundError:
        return set()

def save_seen(seen_set):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_set), f, ensure_ascii=False, indent=2)

def http_get(url, **kwargs):
    headers = kwargs.pop("headers", {})
    headers["User-Agent"] = USER_AGENT
    return requests.get(url, headers=headers, timeout=TIMEOUT, **kwargs)

def extract_id_from_url(href: str) -> str:
    """
    URL 쿼리의 no, bbsNo 같은 파라미터가 있으면 그걸 쓰고,
    없으면 href를 해시로 ID화.
    """
    try:
        qs = parse_qs(urlparse(href).query)
        for k in ("no", "bbsNo", "articleNo", "nttNo"):
            if k in qs and qs[k]:
                return f"{k}:{qs[k][0]}"
    except Exception:
        pass
    return "hash:" + hashlib.sha1(href.encode("utf-8")).hexdigest()[:16]

def guess_category(title: str) -> str:
    cats = [
        ("[장학]", r"장학|scholar"),
        ("[학사]", r"수강|휴학|복학|등록|학점|성적|졸업|학사|수업"),
        ("[채용]", r"채용|인턴|모집"),
        ("[행사]", r"행사|설명회|세미나|특강|박람회"),
        ("[공모전]", r"공모전|대회|콘테스트|챌린지"),
        ("[공지]", r".*"),  # fallback
    ]
    for tag, pattern in cats:
        if re.search(pattern, title, flags=re.I):
            return tag
    return "[공지]"

def match_keywords(title: str) -> bool:
    if not KEYWORDS:
        return True
    return any(re.search(p, title, flags=re.I) for p in KEYWORDS)

def fetch_list_items():
    """
    상명대 통합공지 전용 파서(강화판)
    - 제목 a 태그의 href 또는 onclick에서 articleNo를 추출
    - href=...mode=view&articleNo=... 이면 그대로 사용
    - href="#" 이고 onclick="fnView('760387')" 등인 경우 숫자 추출 후 URL 구성
    - 날짜는 같은 tr/인접 블록에서 yyyy.mm.dd, yyyy-mm-dd, yyyy/mm/dd 패턴 탐색
    반환: [{id, title, url, date}]
    """
    import re
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin, urlparse, parse_qs

    resp = http_get(LIST_URL)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    items = []
    date_pat = re.compile(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}")

    def extract_article_no_from_href(href: str):
        if not href:
            return None
        qs = parse_qs(urlparse(urljoin(BASE, href)).query)
        if "articleNo" in qs and qs["articleNo"]:
            return qs["articleNo"][0]
        return None

    def extract_article_no_from_onclick(attr: str):
        if not attr:
            return None
        # fnView('760387') / goView(760387) / view( '760387' ) 등 숫자 6자리 이상
        m = re.search(r"(?<!\d)(\d{5,})(?!\d)", attr)
        return m.group(1) if m else None

    def build_view_url(article_no: str):
        # 목록에서 쓰던 기본 파라미터는 없어도 상세는 열립니다. 최소 구성으로 생성
        return f"{LIST_URL}?mode=view&articleNo={article_no}"

    # 제목이 들어있는 앵커 후보 넓게 수집 (게시판 영역 우선)
    # 테이블/리스트 모두 커버: 제목 텍스트가 2글자 이상인 a 태그
    anchors = soup.select("table a[href], table a[onclick], .board-list a, a")
    for a in anchors:
        title = a.get_text(strip=True)
        if not title or len(title) < 2:
            continue

        href = a.get("href") or ""
        onclick = a.get("onclick") or ""

        # 1) href에서 articleNo 추출
        art_no = extract_article_no_from_href(href)

        # 2) href가 없거나 '#'이고, onclick에 숫자가 있으면 추출
        if not art_no and (href in ("", "#", "javascript:void(0)", "javascript:;") or "mode=view" not in href):
            art_no = extract_article_no_from_onclick(onclick)

        # articleNo가 없으면 게시글로 보지 않음
        if not art_no:
            continue

        full = build_view_url(art_no)

        # 날짜 추출: 같은 tr 우선, 없으면 부모 블록들에서 검색
        date_text = ""
        tr = a.find_parent("tr")
        if tr:
            for td in reversed(tr.find_all("td")):
                txt = td.get_text(" ", strip=True)
                m = date_pat.search(txt)
                if m:
                    date_text = m.group(0)
                    break
        if not date_text:
            parent = a.find_parent(["li", "div", "article", "section"]) or a.parent
            if parent:
                cand = parent.select_one(".date, .regdate, time")
                if cand:
                    date_text = cand.get_text(strip=True)
                else:
                    m = date_pat.search(parent.get_text(" ", strip=True))
                    if m:
                        date_text = m.group(0)

        items.append({
            "id": f"articleNo:{art_no}",
            "title": title,
            "url": full,
            "date": date_text
        })

    # 메뉴/중복 제거: articleNo 기준으로 dedup
    dedup = {}
    for it in items:
        # 목록 루트(자기 자신)나 메뉴 텍스트(대학생활/통합공지) 필터
        if it["url"].split("?")[0].rstrip("/") == LIST_URL.rstrip("/"):
            continue
        if it["title"] in ("대학생활", "통합공지"):
            continue
        dedup[it["id"]] = it

    # 원래 화면 순서 근사 유지
    ordered = list(dedup.values())
    return ordered

def send_discord(item):
    tag = guess_category(item["title"])
    content = (
        f"📢 **새 공지** {tag}\n"
        f"**제목**: {item['title']}\n"
        f"**게시일**: {item['date'] or '미표기'}\n"
        f"🔗 {item['url']}"
    )
    # 간단한 rate-limit 대응 (Webhook는 보통 5req/2s)
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=TIMEOUT)
    if r.status_code == 429:
        retry_after = r.json().get("retry_after", 2)
        time.sleep(retry_after)
        requests.post(WEBHOOK_URL, json={"content": content}, timeout=TIMEOUT)
    else:
        r.raise_for_status()

print(f"[DEBUG] Parsing list from: {LIST_URL}")
def main():
    seen = load_seen()

    # [1] 지금 파싱 중인 URL 표시
    print(f"[DEBUG] Parsing list from: {LIST_URL}")

    # [2] 실제 HTML 파싱 후 몇 개 항목을 찾았는지 표시
    items = fetch_list_items()
    
    print(f"[DEBUG] Fetched items: {len(items)}")

    items = list(items)

    # [3] '새 공지'로 분류된 항목 개수 표시
    new_items = [it for it in items if it["id"] not in seen and match_keywords(it["title"])]
    print(f"[DEBUG] New items: {len(new_items)} (seen={len(seen)})")

    if not new_items:
        print("새 공지 없음")
        return

    # 너무 많으면 상한
    to_send = new_items[:MAX_SEND_PER_RUN]

    for it in reversed(to_send):  # 오래된 순으로 차분히 전송
        try:
            send_discord(it)
            seen.add(it["id"])
            time.sleep(0.6)  # 과도한 연속 호출 방지
        except Exception as e:
            print("전송 실패:", e)

    save_seen(seen)
    print(f"전송 완료: {len(to_send)}건")

if __name__ == "__main__":
    main()
