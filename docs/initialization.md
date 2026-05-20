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
성공한 digest는 SQLite에 저장되며, 웹 대시보드의 `/digests`에서 최신순으로 볼 수 있다.
개별 원문 저장은 `/saved` 탭에서 확인한다.

```bash
contents-hub digest
contents-hub web --port 8585
```

## 5. Agent skill 설치 (Claude · Codex · Hermes)

Agent skill 설치의 자세한 source of truth는 repo 루트의 `install.md`다. 이 repo는 두 skill을 별도로 제공한다.

- `skills/contents-hub/SKILL.md` — CLI, vault, subscription, daemon, digest, registered exploration 실행
- `skills/contents-hub-explore/SKILL.md` — exploration 설계, chromux probe, recipe 작성, persistent 등록/실행 전 사용자 확인 gate

Codex는 symlink된 `SKILL.md`를 model-visible skill 목록에서 누락할 수 있으므로 실제 파일로 copy한다. Claude Code와 Hermes는 symlink로 repo copy를 가리켜도 된다.

```bash
REPO=/Users/hoyeonlee/team-attention/llm-wiki

# Codex CLI — SKILL.md는 symlink가 아니라 실제 파일로 copy
mkdir -p "$HOME/.codex/skills/contents-hub"
[ -L "$HOME/.codex/skills/contents-hub/SKILL.md" ] && rm "$HOME/.codex/skills/contents-hub/SKILL.md"
cp "$REPO/skills/contents-hub/SKILL.md" "$HOME/.codex/skills/contents-hub/SKILL.md"
mkdir -p "$HOME/.codex/skills/contents-hub-explore"
[ -L "$HOME/.codex/skills/contents-hub-explore/SKILL.md" ] && rm "$HOME/.codex/skills/contents-hub-explore/SKILL.md"
cp "$REPO/skills/contents-hub-explore/SKILL.md" "$HOME/.codex/skills/contents-hub-explore/SKILL.md"

# Claude Code
mkdir -p "$HOME/.claude/skills/contents-hub"
ln -sf "$REPO/skills/contents-hub/SKILL.md" "$HOME/.claude/skills/contents-hub/SKILL.md"
mkdir -p "$HOME/.claude/skills/contents-hub-explore"
ln -sf "$REPO/skills/contents-hub-explore/SKILL.md" "$HOME/.claude/skills/contents-hub-explore/SKILL.md"

# Hermes (설치돼 있을 때만; convention: ~/.hermes/skills/<name>/SKILL.md)
mkdir -p "$HOME/.hermes/skills/contents-hub"
ln -sf "$REPO/skills/contents-hub/SKILL.md" "$HOME/.hermes/skills/contents-hub/SKILL.md"
mkdir -p "$HOME/.hermes/skills/contents-hub-explore"
ln -sf "$REPO/skills/contents-hub-explore/SKILL.md" "$HOME/.hermes/skills/contents-hub-explore/SKILL.md"
```

설치 후 확인:

```bash
ls -l "$HOME/.codex/skills/contents-hub/SKILL.md"          # regular file
ls -l "$HOME/.codex/skills/contents-hub-explore/SKILL.md"  # regular file
ls -l "$HOME/.claude/skills/contents-hub/SKILL.md"         # symlink to repo
ls -l "$HOME/.claude/skills/contents-hub-explore/SKILL.md" # symlink to repo
ls -l "$HOME/.hermes/skills/contents-hub/SKILL.md"         # symlink to repo
ls -l "$HOME/.hermes/skills/contents-hub-explore/SKILL.md" # symlink to repo
```

Codex는 copy 설치이므로 skill 내용을 바꾼 뒤 새 Codex 세션에서 바로 보이게 하려면 위 copy 명령을 다시 실행하거나 `install.md`의 one-pass setup을 다시 실행한다. 가능하면 다음으로 model-visible 상태를 확인한다.

```bash
codex debug prompt-input | rg "contents-hub|contents-hub-explore"
```

> CLI 명령어 surface(`contents-hub --help`, `sub`, `fetch`, `fetch-all`, `tick`, `daemon`, `digest`, `explore`, `exploration`, `lens`)가 바뀌면 `skills/contents-hub/SKILL.md`도 같은 변경 안에서 함께 업데이트한다. Exploration 설계/등록 lifecycle이 바뀌면 `skills/contents-hub-explore/SKILL.md`도 함께 업데이트한다.
