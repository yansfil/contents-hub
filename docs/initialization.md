# contents-hub Initialization

이 문서는 로컬 checkout을 전역 CLI로 설치하고, repo 밖에서도 같은 vault를 기본 대상으로 쓰는 최소 초기화 절차를 설명한다.

## 1. Vault 초기화

새 vault에서 처음 시작할 때만 실행한다.

```bash
contents-hub --vault /path/to/vault init /path/to/vault
```

새 vault는 `.contents-hub/`와 `.contents-hub.yaml`을 canonical metadata로 사용한다.
기존 vault에 `.llm-wiki/` 또는 `.llm-wiki.yaml`만 있으면 호환 fallback으로 읽을 수 있으므로
다시 초기화하거나 기존 state를 지울 필요가 없다.
이 checkout의 로컬 runtime state도 `.contents-hub/`와 `.contents-hub.yaml`로 마이그레이션되어 있다.

## 2. 전역 CLI 설치

로컬 checkout을 editable tool로 설치한다.

```bash
uv tool install -e /Users/hoyeonlee/team-attention/llm-wiki
uv tool update-shell
```

설치 후 새 shell에서 다음 명령이 동작해야 한다.

```bash
contents-hub --help
```

`llm-wiki` executable은 호환 기간 동안 legacy alias로 남아 같은 CLI 구현으로 dispatch된다.
새 문서와 스크립트는 `contents-hub`를 우선 사용한다.

## 3. 기본 Vault 경로

repo 밖에서 실행할 때 현재 작업 디렉터리를 vault로 오인하지 않도록 `CONTENTS_HUB_VAULT`를 shell 환경에 고정한다.

```bash
export CONTENTS_HUB_VAULT="/Users/hoyeonlee/team-attention/llm-wiki"
```

우선순위는 `--vault`, `CONTENTS_HUB_VAULT`, legacy `LLM_WIKI_VAULT`, 현재 작업 디렉터리 순서다.

이 값이 있으면 어디서든 다음처럼 실행할 수 있다.

```bash
contents-hub sub list
contents-hub sub add https://x.com/karpathy
contents-hub fetch 15
contents-hub digest
```

명시적으로 다른 vault를 대상으로 실행할 때는 `--vault`가 환경변수보다 우선한다.

```bash
contents-hub --vault /path/to/other-vault sub list
```

## 4. Background Fetch

구독 fetch loop는 launchd daemon으로 설치할 수 있다.

```bash
contents-hub daemon install
contents-hub daemon status
```

daemon은 fetch만 반복한다. digest는 현재 독립 one-shot 명령이라 별도 스케줄러에서 호출해야 한다.

```bash
contents-hub digest
```

## 5. Agent skill 설치 (Claude · Codex · Hermes)

이 repo는 `contents-hub` CLI 사용법을 안내하는 agent skill을 `skills/contents-hub/SKILL.md` 에 source of truth 로 둔다. 각 runtime은 자기 `skills/` 디렉터리 안의 `SKILL.md` 를 자동 로드한다. 한 곳만 수정해도 모든 runtime이 같이 갱신되도록 symlink로 설치한다.

```bash
REPO=/Users/hoyeonlee/team-attention/llm-wiki

# Claude Code — 기존 user-scope 사본이 있으면 먼저 제거
[ -e "$HOME/.claude/skills/contents-hub" ] && [ ! -L "$HOME/.claude/skills/contents-hub" ] \
  && rm -rf "$HOME/.claude/skills/contents-hub"
mkdir -p "$HOME/.claude/skills"
ln -sfn "$REPO/skills/contents-hub" "$HOME/.claude/skills/contents-hub"

# Codex CLI
[ -e "$HOME/.codex/skills/contents-hub" ] && [ ! -L "$HOME/.codex/skills/contents-hub" ] \
  && rm -rf "$HOME/.codex/skills/contents-hub"
mkdir -p "$HOME/.codex/skills"
ln -sfn "$REPO/skills/contents-hub" "$HOME/.codex/skills/contents-hub"

# Hermes (설치돼 있을 때만; convention: ~/.hermes/skills/<name>/SKILL.md)
[ -e "$HOME/.hermes/skills/contents-hub" ] && [ ! -L "$HOME/.hermes/skills/contents-hub" ] \
  && rm -rf "$HOME/.hermes/skills/contents-hub"
mkdir -p "$HOME/.hermes/skills"
ln -sfn "$REPO/skills/contents-hub" "$HOME/.hermes/skills/contents-hub"
```

설치 후 확인:

```bash
ls -l "$HOME/.claude/skills/contents-hub"  # → repo의 skills/contents-hub 로 symlink
ls -l "$HOME/.codex/skills/contents-hub"
ls -l "$HOME/.hermes/skills/contents-hub"
```

skill 내용을 바꾸면 `skills/contents-hub/SKILL.md` 한 파일만 수정하고 commit 하면 끝이다 — symlink 덕분에 각 runtime이 최신 버전을 본다.

> CLI 명령어 surface(`contents-hub --help`, `sub`, `fetch`, `fetch-all`, `tick`, `daemon`, `digest`, `explore`, `exploration`, `lens`)가 바뀌면 `skills/contents-hub/SKILL.md`도 같은 변경 안에서 함께 업데이트한다.
