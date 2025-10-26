
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
        ("[공지]", r".*"),
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
    resp = http_get(LIST_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []

    rows = soup.select("table.board-list tbody tr")
    for r in rows:
        a = r.select_one("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = urljoin(BASE, a.get("href", ""))
        date_el = r.select_one(".date") or r.select_one("td:nth-last-child(1)")
        date_text = date_el.get_text(strip=True) if date_el else ""
        nid = extract_id_from_url(href)
        items.append({"id": nid, "title": title, "url": href, "date": date_text})

    if not items:
        lis = soup.select("ul.board-list > li, div.board-list .board-item")
        for li in lis:
            a = li.select_one("a")
            if not a:
                continue
            title = a.get_text(strip=True)
            href = urljoin(BASE, a.get("href", ""))
            date_el = li.select_one(".date, .regdate, time")
            date_text = date_el.get_text(strip=True) if date_el else ""
            nid = extract_id_from_url(href)
            items.append({"id": nid, "title": title, "url": href, "date": date_text})

    return items

def send_discord(item):
    tag = guess_category(item["title"])
    content = (
        f"\uD83D\uDCE2 **새 공지** {tag}\n"
        f"**제목**: {item['title']}\n"
        f"**게시일**: {item['date'] or '미표기'}\n"
        f"\ud83d\udd17 {item['url']}"
    )
    r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=TIMEOUT)
    if r.status_code == 429:
        try:
            retry_after = r.json().get("retry_after", 2)
        except Exception:
            retry_after = 2
        time.sleep(float(retry_after))
        requests.post(WEBHOOK_URL, json={"content": content}, timeout=TIMEOUT)
    else:
        r.raise_for_status()

def main():
    seen = load_seen()
    items = fetch_list_items()
    items = list(items)
    new_items = [it for it in items if it["id"] not in seen and match_keywords(it["title"])]
    if not new_items:
        print("새 공지 없음")
        return
    to_send = new_items[:MAX_SEND_PER_RUN]
    for it in reversed(to_send):
        try:
            send_discord(it)
            seen.add(it["id"])
            time.sleep(0.6)
        except Exception as e:
            print("전송 실패:", e)
    save_seen(seen)
    print(f"전송 완료: {len(to_send)}건")

if __name__ == "__main__":
    main()
