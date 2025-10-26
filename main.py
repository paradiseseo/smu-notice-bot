# main.py
import os, re, json, time, hashlib
from urllib.parse import urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup

BASE = "https://www.smu.ac.kr"
LIST_URL = "https://www.smu.ac.kr/kor/life/notice.do"
# 목록을 안정적으로 보여주는 쿼리 셋(캠퍼스/기간/상단공지 포함)
LIST_URL_WITH_PARAMS = (
    LIST_URL
    + "?srCampus=smu&srUpperNoticeYn=on"
    + "&srStartDt=2024-03-01&srEndDt=2026-02-28"
    + "&article.offset=0&articleLimit=10"
)
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

def clean_title(raw: str) -> str:
    """'통합공지 게시판읽기(...)' 같은 공통 프리픽스 제거 + 괄호 내용만 추출"""
    t = (raw or "").strip()
    # 통합공지 게시판읽기 / 게시판 읽기 / 공백 변형 제거
    t = re.sub(r'^\s*통합공지\s*게시판\s*읽기\s*', '', t)
    t = re.sub(r'^\s*통합공지\s*게시판읽기\s*', '', t)
    t = re.sub(r'^\s*\[\s*통합공지\s*\]\s*', '', t)
    # 전체가 괄호로 둘러싸인 형태면 안쪽만
    m = re.match(r'^\((.+)\)$', t)
    if m:
        t = m.group(1).strip()
    # 공백 정리
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def match_keywords(title: str) -> bool:
    if not KEYWORDS:
        return True
    return any(re.search(p, title, flags=re.I) for p in KEYWORDS)

def fetch_list_items():
    """
    목록 HTML에서 articleNo를 정규식으로 수집하고,
    각 상세 페이지(/kor/life/notice.do?mode=view&articleNo=...)로 들어가
    제목/날짜를 파싱한다.
    반환: [{id, title, url, date}]
    """
    import re
    from bs4 import BeautifulSoup
    from urllib.parse import urljoin

    # 1) 목록 HTML 받기 (파라미터 포함 버전 사용)
    resp = http_get(LIST_URL_WITH_PARAMS, headers={
        "Referer": LIST_URL,
        "Accept-Language": "ko,en;q=0.8",
    })
    resp.raise_for_status()
    html = resp.text

    # 2) 목록 HTML에서 articleNo 수집 (href, onclick 모두 커버)
    artnos = set(re.findall(r"articleNo=(\d+)", html))
    artnos.update(re.findall(r"fnView\(['\"]?(\d{5,})['\"]?\)", html))
    found = sorted(artnos, reverse=True)[:10]  # 최신 몇 건만
    print(f"[DEBUG] articleNo candidates: {found}")

    items = []
    date_pat = re.compile(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}")

    # 🔸 상세 제목 추출기 (for 루프 앞에 정의)
    def extract_title_from_detail(dsoup):
        BAD_TITLES = {"Calendar", "통합공지", "대학생활", "공지", "게시판"}

        # 1) og:title
        og = dsoup.select_one('meta[property="og:title"]')
        if og and og.get("content"):
            t = og["content"].strip()
            if t and t not in BAD_TITLES:
                return clean_title(t)

        # 2) "제목" 라벨 옆 값(th/td, dt/dd)
        for row in dsoup.select("table tr"):
            th = row.find("th")
            if th and "제목" in th.get_text(strip=True):
                td = row.find("td")
                if td:
                    t = td.get_text(" ", strip=True)
                    if t and t not in BAD_TITLES:
                        return clean_title(t)
        for dt in dsoup.select("dt"):
            if "제목" in dt.get_text(strip=True):
                dd = dt.find_next("dd")
                if dd:
                    t = dd.get_text(" ", strip=True)
                    if t and t not in BAD_TITLES:
                        return clean_title(t)

        # 3) 본문 상단 타이틀 후보
        CANDS = [
            ".board_view .title", ".boardView .title", ".view_title", ".view-title",
            ".bbs_view .tit", ".bbs-view .tit", ".post-title", ".board .title",
            "article h1", "article h2", "#content h1", "#content h2"
        ]
        for sel in CANDS:
            el = dsoup.select_one(sel)
            if el:
                t = el.get_text(" ", strip=True)
                if t and t not in BAD_TITLES and len(t) > 1:
                    return clean_title(t)

        # 4) <title> fallback
        page_title = dsoup.title.get_text(" ", strip=True) if dsoup.title else ""
        if page_title:
            parts = re.split(r"[|\-·•»«]+", page_title)
            parts = [p.strip() for p in parts if p.strip()]
            if parts:
                parts.sort(key=len, reverse=True)
                t = parts[0]
                if t and t not in BAD_TITLES:
                    return clean_title(t)
        return ""

    # 3) 각 상세 페이지에서 제목/날짜 파싱
    for no in found:
        view_url = f"{LIST_URL}?mode=view&articleNo={no}"
        try:
            d = http_get(view_url, headers={
                "Referer": LIST_URL_WITH_PARAMS,
                "Accept-Language": "ko,en;q=0.8",
            })
            d.raise_for_status()
        except Exception as e:
            print(f"[DEBUG] detail fetch failed for {no}: {e}")
            continue

        dsoup = BeautifulSoup(d.text, "html.parser")
        title_text = extract_title_from_detail(dsoup)

        # 날짜: 메타 영역 우선, 없으면 본문에서 패턴
        date_text = ""
        meta = dsoup.select_one(".date, .regdate, time, .write, .info")
        if meta:
            date_text = meta.get_text(" ", strip=True)
            m = date_pat.search(date_text)
            if m:
                date_text = m.group(0)
        if not date_text:
            m = date_pat.search(dsoup.get_text(" ", strip=True))
            if m:
                date_text = m.group(0)

        if not title_text:
            title_text = f"articleNo {no}"

        items.append({
            "id": f"articleNo:{no}",
            "title": title_text,
            "url": view_url,
            "date": date_text
        })

    # 중복 제거
    dedup = {}
    for it in items:
        dedup[it["id"]] = it
    return list(dedup.values())

def send_discord(item):
    # 1️⃣ 제목 정리
    base_title = clean_title(item["title"])

    # 2️⃣ 부서명([학생복지팀] 등)을 찾아 굵게(**…**) 표시
    dept_match = re.match(r'^\[(.*?)\]', base_title)
    if dept_match:
        dept_name = dept_match.group(1)
        base_title = base_title.replace(f"[{dept_name}]", f"**[{dept_name}]**", 1)

    # 3️⃣ 제목이 너무 길면 잘라주기 (가독성)
    MAX_TITLE = 140
    if len(base_title) > MAX_TITLE:
        base_title = base_title[:MAX_TITLE - 1] + "…"

    # 4️⃣ 카드형 메시지 포맷
    content = (
        f"📢 {base_title}\n"
        f"📅 **게시일:** {item['date'] or '미표기'}\n"
        f"🔗 <{item['url']}>"
    )

    # 5️⃣ 디스코드 전송 (레이트 리밋 대응)
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=TIMEOUT)
    if r.status_code == 429:
        retry_after = r.json().get("retry_after", 2)
        time.sleep(retry_after)
        requests.post(WEBHOOK_URL, json={"content": content}, timeout=TIMEOUT)
    else:
        r.raise_for_status()

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
