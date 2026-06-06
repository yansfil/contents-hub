# Generic RSS/Atom Recipe

## LIST_STRATEGY
Tools: `fetch_url`, `parse_rss`.

주어진 피드 URL 을 `fetch_url` 로 HTTP GET 하여 `parse_rss` 로 XML 을 파싱한다.
- RSS 2.0: `<channel><item>` 반복, URL 은 `<link>`
- Atom: `<feed><entry>`, URL 은 `<link href="..."/>`
- `<pubDate>` / `<updated>` 기준 내림차순.
- ETag / Last-Modified 헤더를 저장해 다음 호출 시 `If-None-Match` / `If-Modified-Since` 로 사용.

## CONTENT_STRATEGY
Tools: `parse_rss`, `fetch_url`, `parse_html`.

`parse_rss` 결과에서 본문 후보를 순서대로 시도한다:
1. `<content:encoded>` (RSS 확장)
2. Atom `<content type="html">`
3. `<description>` (요약일 수 있음)
- 모두 없거나 너무 짧으면 항목 URL 을 `fetch_url` 로 HTTP GET 해 `parse_html` 로 `<article>` / readability 추출로 본문을 얻는다.
- HTML → markdown 변환 후 저장.

## METADATA
Tools: `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- title: `<title>`
- author: `<dc:creator>` 또는 `<author><name>` (없으면 feed 레벨 title)
- published_at: `<pubDate>` / `<updated>` → ISO 8601
