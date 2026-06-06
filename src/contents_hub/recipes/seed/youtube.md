# YouTube Recipe

## LIST_STRATEGY
Tools: `fetch_url`, `parse_html`, `parse_rss`.

채널의 RSS 피드에서 최신 영상 URL 목록을 얻는다.
- 채널 URL이 `youtube.com/@handle` 형태면 먼저 `fetch_url` 로 페이지를 받아 `channel_id` 후보를 추출한다. 후보는 `channel_id=UC...`, `"externalId":"UC..."`, `"browseId":"UC..."`, `"channelId":"UC..."` 처럼 채널 메타데이터에 연결된 값만 사용한다. 페이지에 섞인 임의의 첫 `UC...` regex match 를 바로 확정하지 마라.
- 각 `channel_id` 후보마다 `https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}` 를 `fetch_url` 로 받아 본다. HTTP 404/빈 body 후보는 버리고 같은 후보를 반복 재시도하지 마라.
- `fetch_url` 결과 JSON에서 `body` 필드를 꺼내서 `parse_rss(xml=<body>, feed_url=<rss_url>, max_items=15)` 로 파싱한다. `parse_rss` 에는 `url`, `source`, `content`, `xml_text`, `xml_content` 같은 다른 인자명을 쓰지 마라.
- 각 `<entry>`의 `<link href>`가 영상 URL, `<published>`가 발행 시간이다.
- RSS 가 404 이거나 파싱 결과가 비어 있으면 fallback 으로 채널의 `/videos` 페이지를 `fetch_url` 로 받고 HTML 안의 `"videoId":"..."` 값을 문서 순서대로 중복 제거해 `https://www.youtube.com/watch?v={videoId}` URL 목록을 만든다.

## CONTENT_STRATEGY
Tools: `fetch_url`, `parse_html`.

**기본 경로 — 빠르게.** 구독 체크는 per-tick 예산 안에서 끝나야 한다 (영상당 자막 다운로드/파싱은 너무 느려서 전체 fetch 를 600s 벽시계에 부딪히게 만든다).

- 각 영상 URL에 대해 `fetch_url` 로 watch 페이지 HTML 을 받고 `parse_html` 로 영상 제목 + description 을 추출한다.
- 본문(`body_markdown`)은 **description 텍스트만** 정리해서 사용하고 `body_status=partial` 로 표시한다.
- description 도 못 가져오면 `body_markdown=""`, `body_status=empty`.
- **자막(transcript)·`yt-dlp` 호출은 기본 경로에서 하지 않는다.** description 으로 충분하다. (전사가 정말 필요하면 별도 도구/후속 단계에서 한 영상씩 보강하라 — 구독 fetch 안에서는 안 한다.)
- shorts URL(`youtube.com/shorts/<id>`) 도 동일하게 watch 페이지처럼 description 만 추출한다.
- **본문 길이는 영상당 최대 20000자로 제한.**

## METADATA
Tools: `extract_metadata`.

- title: `<entry><title>` (LIST 단계 RSS 값) 또는 watch 페이지 `<title>` / `og:title`
- author: 채널명 (`<author><name>`)
- published_at: `<published>` (ISO 8601 UTC)

**저장은 executor 가 한다.** CONTENT 에이전트는 수집 결과를 JSON 으로 반환하면 끝이다. `persist_raw` 도구를 호출하지 마라 — CONTENT 컨텍스트에는 DB 커넥션이 없어서 실패하고, 실패하면 절대 Bash 로 `state.db` 를 찾거나 `sqlite3` 로 직접 쓰려 하지 마라. 그냥 detail_prompt 스키마대로 `{"items": [...], "errors": [...], "failure_reason": null}` JSON 만 반환한다.
