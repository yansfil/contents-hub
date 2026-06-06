# Reddit Recipe

## LIST_STRATEGY
Tools: `fetch_url`, `parse_json`.

Reddit 은 공식 JSON 엔드포인트를 제공한다.
- 서브레딧: `https://www.reddit.com/r/{sub}/new.json?limit=50`
- 유저: `https://www.reddit.com/user/{name}/submitted.json?limit=50`
- `fetch_url` 로 JSON 을 받아 `parse_json` 으로 `data.children[].data.permalink` 를 절대 URL(`https://www.reddit.com{permalink}`) 로 만든다.
- User-Agent 헤더 설정 필수 (기본 python-requests 는 429 당하기 쉬움).

## CONTENT_STRATEGY
Tools: `fetch_url`, `parse_json`.

각 permalink 에 `.json` 을 붙인 엔드포인트(`{permalink}.json`) 를 `fetch_url` 로 호출하고 `parse_json` 으로 본문을 분리한다.
- 응답의 첫 요소가 post, 두 번째가 댓글 트리.
- post `selftext` (markdown) 또는 외부 링크(`url`) 을 본문으로 사용한다.
- 이미지/비디오는 메타로, 텍스트 포스트는 selftext 전체를 저장한다.

## METADATA
Tools: `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- title: post `title`
- author: post `author` (u/ 접두)
- published_at: `created_utc` (unix → ISO 8601)
