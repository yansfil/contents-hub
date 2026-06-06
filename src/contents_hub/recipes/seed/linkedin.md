# LinkedIn Recipe

## AUTH_CHECK (run before LIST_STRATEGY)

Tools: `chromux_navigate`, `chromux_extract`.

`chromux_navigate` 로 `https://www.linkedin.com/feed/` 로 이동한 뒤 `chromux_extract` 로 로그인 여부를 확인한다. Bash 는 사용하지 않는다.

- 권장 호출: `chromux_extract(session_id=<session>, selector='nav a', attributes=['href', 'aria-label', 'text'], multiple=true, limit=80)`.
- **로그인된 신호**: `messaging`, `notifications`, `mynetwork`, `jobs`, `profile` 계열 href/label/text 중 하나 이상 존재.
- **비로그인 신호**: URL 이 `/login` 또는 `/uas/login` 으로 리다이렉트, 상단에 `Sign in` 버튼.
- 비로그인으로 판정되면 즉시 중단하고 다음 JSON 을 반환:
  `{"error": "not_authenticated", "message": "linkedin.com 세션이 로그인되어 있지 않음. contents-hub chromux 프로필에 로그인 필요.", "source": "linkedin"}`.

## LIST_STRATEGY

Tools: `chromux_navigate`, `chromux_extract`.

`chromux_navigate` 로 로그인된 세션에서 활동 피드 URL(`https://www.linkedin.com/in/{handle}/recent-activity/all/`)을 연 뒤 `chromux_extract` 로 카드를 수집한다.

1. **카드 로딩 대기**: `wait_ms` 를 3000~5000ms 로 지정해 렌더링을 기다린다. 이후 `[data-urn]` 요소를 수집한다.

2. **카드 수집**: 반드시 `chromux_extract(session_id=<session>, selector='[data-urn]', attributes=['data-urn', 'text'], multiple=true, limit=<max_items + 10>)` 로 각 요소의 `data-urn` 속성값과 카드 visible text 를 같은 record 안에서 읽는다.
   ```
   data-urn 값: urn:li:activity:7448514963196911616
   → URL: https://www.linkedin.com/feed/update/urn:li:activity:7448514963196911616/
   ```
   > ⚠️ `a[href*="/posts/"]` 패턴은 현재 사용하지 않는다. `urn:li:activity:<id>` → `/feed/update/urn:li:activity:<id>/` 변환을 stable identity 로 사용한다.

3. **범위 제한**: subscription fetch 는 탐색이 아니라 구독 체크다. 추가 로딩/스크롤/click pagination 을 하지 말고, 처음 렌더된 최신 후보에서 중복 제거 후 `max_items` 개만 수집한다. 이미 본 글은 저장 계층의 URL identity dedup 이 skip 한다.

## CONTENT_STRATEGY

Tools: `chromux_navigate`, `chromux_extract`.

LIST_STRATEGY 에서 얻은 카드 visible text 를 기본 본문으로 사용한다. LinkedIn permalink 상세 페이지는 selector 가 자주 비어 안정성이 떨어지므로 기본 흐름에서는 열지 않는다.

1. **본문 텍스트**:
   - 카드의 `text` 값에서 작성자/액션 버튼/노출 수/분석 보기 같은 UI 라인을 가능한 한 제거하고 본문 중심으로 정리한다.
   - 카드 text 가 충분하면 `body_status='full'` 로 둔다.
   - 카드 text 가 비어 있거나 80자 미만이면 그때만 permalink 를 열어 `chromux_extract(session_id=<session>, mode='text')` 로 visible text 일부를 수집하고 `body_status='partial'` 로 표시한다.
   - show-more 버튼 click 은 하지 않는다. 구독 체크는 안정성을 우선한다.

2. **첨부 미디어**:
   - 기본 흐름에서는 별도 상세 페이지 미디어 수집을 생략한다.
   - 이미지: `chromux_extract(..., selector='[class*="update-components-image"] img', attributes=['src', 'alt'], multiple=true, limit=20)`.
   - 외부 링크 프리뷰: `chromux_extract(..., selector='[class*="update-components-article"] a[href]', attributes=['href', 'text'], multiple=true, limit=20)`.
   - 동영상: `[class*="update-components-linkedin-video"]` 존재 여부 표시

## METADATA

Tools: `chromux_extract`, `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- **title**: 본문 첫 80자 + 말줄임 (`…`)
- **author**: 카드 text 상단의 프로필 이름을 사용한다. 비어 있으면 subscription 대상 프로필 이름을 사용한다.
- **published_at**: 카드 text 의 상대시간 텍스트에서 파싱한다.
  - `"N분 전"` → `now - N minutes`
  - `"N시간 전"` → `now - N hours`
  - `"N일 전"` → `now - N days`
  - `"N주 전"` → `now - N weeks`
  - `"N개월 전"` → `now - N months`
  - 텍스트에 `•` 구분자 이후 내용(공개 범위)은 무시한다.
  > ⚠️ `<time datetime="...">` 태그는 포스트 카드에서 제공되지 않음 (댓글에만 존재). 상대시간 파싱이 유일한 방법.

## OUTPUT_RULES

- 결과는 반드시 `{"items": [...], "errors": [], "failure_reason": null}` JSON 으로 직접 반환한다.
- 정상 흐름에서는 Bash 를 사용하지 않는다. `chromux_extract` 가 `ok=false` 를 반환해 더 진행할 수 없는 경우에만 Bash fallback 을 고려한다.
- `url` 은 permalink URL 을 stable identity 로 사용한다. 새 글/중복 판정은 `raw_items(subscription_id, url)` 저장 계층이 처리한다.
