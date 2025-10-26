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
    상명대 통합공지 전용 파서:
    - 실제 게시글 링크만: /kor/life/notice.do?mode=view&articleNo=...
    - 내비/목록 루트 링크(통합공지/대학생활)는 자동 배제
    - 날짜는 같은 행(tr)/인접 블록에서 yyyy.mm.dd 패턴 탐색
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
    date_pat = re.compile(r"\d{4}[.\-\/]\d{1,2}[.\-\/]\d{1,2}")

    def is_notice_view_href(href: str) -> bool:
        if not href:
            return False
        full = urljoin(BASE, href)
        # 목록 루트는 제외
        if full.split("?")[0].rstrip("/") == LIST_URL.rstrip("/"):
            return False
        # 반드시 mode=view & articleNo= 포함
        if ("/kor/life/notice.do" in full) and ("mode=view" in full) and ("articleNo=" in full):
            return True
        return False

    def extract_article_no(href: str) -> str:
        qs = parse_qs(urlparse(urljoin(BASE, href)).query)
        if "articleNo" in qs and qs["articleNo"]:
            return qs["articleNo"][0]
        return None

    # 1) a[href*="notice.do"][href*="mode=view"][href*="articleNo="] 만 수집
    anchors = soup.select('a[href*="notice.do"][href*="mode=view"][href*="articleNo="]')
    for a in anchors:
        href = a.get("href", "")
        if not is_notice_view_href(href):
            continue

        full = urljoin(BASE, href)
        art_no = extract_article_no(href)
        if not art_no:
            continue  # articleNo가 없는 경우 스킵

        # 제목 추출
        title = a.get_text(strip=True)

        # 게시판이 테이블이면 같은 tr에서 날짜 찾기
        date_text = ""
        tr = a.find_parent("tr")
        if tr:
            # 뒤쪽 td부터 날짜 형태 탐색
            for td in reversed(tr.find_all("td")):
                txt = td.get_text(" ", strip=True)
                m = date_pat.search(txt)
                if m:
                    date_text = m.group(0)
                    break

        # 리스트/카드형이면 주변에서 날짜 후보 찾기
        if not date_text:
            parent = a.find_parent(["li", "div"]) or a.parent
            if parent:
                # class로 흔히 쓰이는 것들
                cand = parent.select_one(".date, .regdate, time")
                if cand:
                    date_text = cand.get_text(strip=True)
                else:
                    # 텍스트에서 패턴 탐색
                    m = date_pat.search(parent.get_text(" ", strip=True))
                    if m:
                        date_text = m.group(0)

        items.append({
            "id": f"articleNo:{art_no}",
            "title": title if title else f"articleNo {art_no}",
            "url": full,
            "date": date_text
        })

    # 중복 제거(같은 articleNo는 하나만)
    dedup = {}
    for it in items:
        dedup[it["id"]] = it

    # 원래 목록 순서 보장: anchors 순회 순서 유지
    ordered = []
    seen_ids = set()
    for a in anchors:
        href = a.get("href", "")
        art_no = extract_article_no(href)
        if not art_no:
            continue
        key = f"articleNo:{art_no}"
        if key in dedup and key not in seen_ids:
            ordered.append(dedup[key])
            seen_ids.add(key)

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
