# Substack Recipe

## LIST_STRATEGY
Tools: `fetch_url`, `parse_rss`.

Substack 뉴스레터는 모두 `{name}.substack.com/feed` 에 RSS 피드를 제공한다.
- `fetch_url` 로 피드 URL 을 HTTP GET 하여 `parse_rss` 로 RSS(Atom) XML 을 파싱한다.
- 각 `<item>`의 `<link>` 가 글 URL, `<pubDate>` 가 발행 시간이다.

## CONTENT_STRATEGY
Tools: `parse_rss`, `parse_html`.

`parse_rss` 결과의 `<item>` 내부 `<content:encoded>` 에 본문 HTML 이 포함되어 있는 경우 `parse_html` 로 그대로 변환해 사용한다.
- 유료 구독 전용 글은 본문이 잘려 있을 수 있으니 그 경우 body_status=partial 로 표시한다.
- HTML → markdown 변환 후 저장.

## METADATA
Tools: `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- title: `<item><title>`
- author: `<dc:creator>` 또는 feed `<title>`
- published_at: `<pubDate>` (RFC 2822 → ISO 8601 변환)
