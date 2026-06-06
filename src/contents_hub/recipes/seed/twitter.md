# Twitter/X Recipe

## AUTH_CHECK (run before LIST_STRATEGY)
Tools: `chromux_navigate`, `chromux_extract`.

X 공개 프로필은 비로그인 상태에서도 일부 article 이 렌더될 수 있지만, 그 목록은 최신순 보장이 약하다. 구독 fetch 는 최신 글 감지가 목적이므로 `contents-hub` chromux 프로필의 로그인 세션을 요구한다. Bash 는 사용하지 않는다.

- 먼저 `chromux_navigate` 로 `https://x.com/{handle}` 프로필 페이지를 열고 3000~5000ms 기다린다.
- 로그인된 신호: 좌측 primary navigation 에 `/home`, `/notifications`, `/messages` 또는 `/i/chat`, `/compose/post`, 현재 계정 프로필 링크 중 하나 이상이 보인다.
- 비로그인 신호: `"Don’t miss what’s happening"`, `"Log in"`, `"Sign up"`, `a[href="/login"]`, `a[href="/i/flow/login"]`, `a[href="/i/flow/signup"]` 가 보이고 로그인된 신호가 없다.
- 비로그인으로 판정되면, article 이 일부 보여도 최신순 신뢰가 없으므로 즉시 다음 JSON 을 반환한다:
  `{"error": "not_authenticated", "message": "x.com 프로필이 로그인 벽에 막힘. contents-hub chromux 프로필에 로그인 필요.", "source": "twitter", "failure_reason": "login_required"}`
- 필요할 때만 보조 확인으로 `chromux_extract(session_id=<session>, selector='nav a', attributes=['href', 'aria-label', 'text'], multiple=true, limit=80)` 를 호출한다.

## LIST_STRATEGY
Tools: `chromux_navigate`, `chromux_extract`.

`chromux_navigate` 로 `https://x.com/{handle}` 프로필 페이지로 이동하고 `chromux_extract` 로 타임라인을 스캔한다. 로그인 상태에서는 프로필 타임라인이 고정 트윗 다음에 최신 일반 글을 렌더한다.

1. **타임라인 로딩 대기**: `wait_ms` 를 3000~5000ms 로 지정해 렌더링을 기다린다.

2. **후보 트윗 수집**:
   - 먼저 `chromux_extract(session_id=<session>, selector='article[data-testid="tweet"]', attributes=['text', 'outerHTML'], multiple=true, limit=<max_items + 12>)` 로 첫 화면의 트윗 article 후보를 얻는다.
   - 반드시 article 단위로 `a[href*="/status/"]` 와 `time[datetime]` 을 같은 record 안에서 맞춘다. 전역 링크 목록, 전역 time 목록, 전역 socialContext 목록을 각각 뽑은 뒤 index 로 맞추지 않는다.
   - 각 article 에서 `/{handle}/status/<id>` 또는 `/i/web/status/<id>` 패턴만 원 작성자의 permalink 후보로 사용한다. `/analytics`, `/photo/1`, 인용 트윗/리포스트 작성자의 status 링크는 제외한다.
   - URL 은 `https://x.com/{handle}/status/<id>` 로 정규화한다. `published_hint` 는 article 안의 첫 번째 `time[datetime]` 값을 사용한다.

3. **고정 트윗(Pinned) 감지·제외 — 필수 / 리포스트는 포함**:
   - article visible text 의 첫 줄 또는 article 과 직접 붙은 label 에 `Pinned` / `고정된 게시물` / `고정` 이 있으면 해당 permalink 를 후보 목록에서 제거한다.
   - `[data-testid="socialContext"]` 는 sparse 하게 나타날 수 있으므로 article 과 1:1 순서로 정렬된다고 가정하지 않는다.
   - socialContext 를 보조로 확인할 때도 article 의 위치/근접성을 기준으로 매칭한다. 전역 socialContext 배열의 N번째 값을 N번째 article 에 적용하지 않는다.
   - `reposted`/`리포스트` 류는 제외하지 않는다. 구독 목적은 작성자가 최근 타임라인에 노출한 항목 감지이므로, 리포스트 article 은 원글 status URL 을 후보로 사용하고 `source_payload.is_repost=true`, `source_payload.reposted_by=<handle>` 같은 메타데이터를 남긴다.
   - X DOM의 `<time datetime>` 은 리포스트 시각이 아니라 원글 작성 시각일 수 있다. 리포스트 최신성은 `published_hint` 로 정렬하지 말고 프로필 타임라인 DOM 순서를 우선한다.

4. **최신순 정렬과 범위 제한**: subscription fetch 는 탐색이 아니라 구독 체크다. 기본적으로 추가 로딩/스크롤/click pagination 을 하지 말고, 3번에서 Pinned 만 제거한 뒤 프로필 타임라인 DOM 순서를 유지한다. 단, 첫 화면 article 이 모두 Pinned 라서 후보가 0개면 recovery 용으로만 1~2회 짧게 스크롤해 다시 같은 article-scoped 추출을 수행한다. 중복 제거 후 `max_items` 개만 상세 수집한다. 이미 본 글은 저장 계층의 URL identity dedup 이 skip 한다.

5. **그 밖의 제외 규칙**: 광고/프로모티드(`Promoted`/`Ad`) 텍스트가 포함된 article 은 제외한다. 1년 이상 오래된 트윗만 후보로 남았다면 (대개 고정 트윗을 못 걸렀다는 신호) 다시 socialContext 를 확인해 Pinned 를 색출한다.

## CONTENT_STRATEGY
Tools: `chromux_navigate`, `chromux_extract`.

`chromux_navigate` 로 각 트윗 permalink로 이동해 `chromux_extract` 로 본문 article 에서 텍스트, 이미지 URL, 인용 트윗, 첨부 링크를 추출한다.
- 본문: `chromux_extract(session_id=<session>, selector='article[data-testid="tweet"]', mode='text')`.
- 이미지: `chromux_extract(..., selector='article[data-testid="tweet"] img', attributes=['src', 'alt'], multiple=true, limit=20)`.
- 첨부 링크/인용: `chromux_extract(..., selector='article[data-testid="tweet"]', mode='links')`.
- 스레드 self-reply 체인은 따라가지 않는다. 구독 체크는 최신 개별 트윗의 안정적 수집을 우선한다.
- 본문이 비어 있으면 `body_status='empty'`, 일부만 보이면 `body_status='partial'` 로 표시한다.

## METADATA
Tools: `chromux_extract`, `extract_metadata`. 수집된 항목은 마지막에 `persist_raw` 로 저장한다.

- title: 본문 첫 80자 + 말줄임
- author: `@handle` (프로필에서)
- published_at: article 내 `<time datetime="...">` 값

## OUTPUT_RULES

- 결과는 반드시 `{"items": [...], "errors": [], "failure_reason": null}` JSON 으로 직접 반환한다.
- 정상 흐름에서는 Bash 를 사용하지 않는다. `chromux_extract` 가 `ok=false` 를 반환해 더 진행할 수 없는 경우에만 Bash fallback 을 고려한다.
- `url` 은 정규화한 status permalink 를 stable identity 로 사용한다. 새 글/중복 판정은 `raw_items(subscription_id, url)` 저장 계층이 처리한다.
