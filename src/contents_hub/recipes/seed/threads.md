# Threads Profile Recipe

## LIST_STRATEGY
Tools: `chromux_navigate`, `chromux_extract`.

주어진 Threads 프로필 URL을 browser session으로 연다. 로그인 벽이나 차단 페이지가 보이면 `failure_reason=login_required` 또는 `failure_reason=blocked` 로 보고한다.
- 프로필 타임라인의 최신 게시물 링크를 위에서부터 수집한다.
- 같은 게시물 URL은 중복 제거한다.
- 페이지가 동적으로 로딩되면 짧게 한 번 스크롤하고, 최초 화면 + 추가 로드된 게시물까지만 검증한다.

## CONTENT_STRATEGY
Tools: `chromux_navigate`, `chromux_extract`.

각 게시물 URL을 열어 본문, 작성자, 작성 시간을 추출한다.
- 이미지/동영상만 있고 텍스트가 짧으면 접근 가능한 alt text 또는 주변 캡션을 요약 본문으로 사용한다.
- 본문은 게시물당 최대 20000자로 제한한다.
- 댓글/답글 전체 스레드는 기본 수집하지 않고 원 게시물 중심으로 저장한다.

## METADATA
Tools: `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- title: 본문 첫 문장 또는 프로필명 + 게시 시간
- author: 프로필 표시 이름 또는 handle
- published_at: 게시물 timestamp를 ISO 8601로 정규화
