# Generic Webpage Recipe

## LIST_STRATEGY
Tools: `chromux_navigate`, `chromux_extract`.

주어진 URL을 browser session으로 연다. 이 recipe는 단일 웹페이지나 공개 목록 페이지를 최소한으로 검증하기 위한 fallback이다.
- 페이지 안의 주요 article/card/list 항목 링크를 최신순으로 수집한다.
- 목록 구조가 없으면 현재 페이지 자체를 하나의 item URL로 사용한다.
- 로그인 벽, 404, CAPTCHA, 강한 차단이 보이면 적절한 `failure_reason`을 보고한다.

## CONTENT_STRATEGY
Tools: `chromux_navigate`, `chromux_extract`.

각 URL을 열고 본문 영역을 추출한다.
- `<article>`, main landmark, 가장 긴 본문 블록 순서로 우선한다.
- 본문이 너무 짧으면 meta description과 title을 summary로 사용하고 `body_status=partial` 로 표시한다.
- 본문은 항목당 최대 20000자로 제한한다.

## METADATA
Tools: `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- title: `<title>`, `og:title`, 또는 본문 첫 heading
- author: meta author 또는 비워 둠
- published_at: `article:published_time`, `<time datetime>`, JSON-LD datePublished
