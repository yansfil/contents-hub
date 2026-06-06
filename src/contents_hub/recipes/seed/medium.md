# Medium Recipe

## LIST_STRATEGY
Tools: `fetch_url`, `parse_rss`.

Medium 작가/퍼블리케이션은 RSS 피드를 제공한다.
- 작가: `https://medium.com/feed/@{handle}`
- 퍼블리케이션: `https://medium.com/feed/{publication}`
- `fetch_url` 로 피드를 받아 `parse_rss` 로 각 `<item><link>` 를 수집한다.

## CONTENT_STRATEGY
Tools: `fetch_url`, `parse_html`.

RSS 에는 본문이 잘려 올 수 있으므로 article URL 을 `fetch_url` 로 직접 HTTP GET 해 HTML 을 받은 뒤
`parse_html` 로 `article` 또는 `section[data-field="body"]` 내부를 추출해 markdown 으로 변환한다.
- 멤버 전용 페이지는 요약만 가능하니 body_status=partial 로 표시.

## METADATA
Tools: `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- title: `<item><title>` 또는 HTML `<h1>`
- author: `<dc:creator>` 또는 article 상단 프로필 이름
- published_at: `<pubDate>` 또는 `<time datetime>`
