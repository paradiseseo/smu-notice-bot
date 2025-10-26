def fetch_list_items():
    """
    통합공지 목록 파싱 (마크업 친화형).
    핵심: 'articleNo'를 포함한 공지 링크를 전부 수집.
    반환: [{id, title, url, date}]
    """
    resp = http_get(LIST_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    # 공지 상세로 가는 모든 링크 수집
    links = soup.select('a[href*="/kor/life/notice.do"][href*="articleNo"]')
    seen_urls = set()

    for a in links:
        href = a.get("href", "").strip()
        if not href:
            continue
        # 절대경로화
        url = urljoin(BASE, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = a.get_text(strip=True)
        if not title:
            # a 태그 안쪽에 공백만 있을 경우 부모에서 텍스트 보조 추출
            title = (a.get("title") or a.parent.get_text(" ", strip=True) or "").strip()
        # 공지 ID 추출
        nid = extract_id_from_url(url)

        # 날짜는 주변 텍스트에서 '작성일 YYYY-MM-DD' 패턴 탐색
        date_text = ""
        container = a
        for _ in range(4):  # 최대 4단계 부모까지 탐색
            container = container.parent
            if not container:
                break
            txt = container.get_text(" ", strip=True)
            m = re.search(r"(작성일\s*)?(\d{4}-\d{2}-\d{2})", txt)
            if m:
                date_text = m.group(2)
                break

        items.append({"id": nid, "title": title, "url": url, "date": date_text})

    return items

def extract_id_from_url(href: str) -> str:
    qs = parse_qs(urlparse(href).query)
    for k in ("articleNo", "no", "bbsNo", "nttNo"):
        if k in qs and qs[k]:
            return f"{k}:{qs[k][0]}"
    return "hash:" + hashlib.sha1(href.encode("utf-8")).hexdigest()[:16]

print(f"[DEBUG] Parsing list from: {LIST_URL}")
items = fetch_list_items()
print(f"[DEBUG] Fetched items: {len(items)}")
new_items = [it for it in items if it["id"] not in seen and match_keywords(it["title"])]
print(f"[DEBUG] New items: {len(new_items)} (seen={len(seen)})")
