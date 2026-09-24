#!/usr/bin/env bash
# PreToolUse(Bash) 훅 — 미커밋 작업물을 지우는 git 명령을 거부한다.
#
# 왜: 같은 작업 트리를 여러 세션·병렬 에이전트가 함께 쓰는데, 담당 경계를 모르는
# 되돌리기 명령으로 다른 쪽의 미커밋 작업물이 소실된 전례가 있다. CLAUDE.md 가 금지하고
# 있지만 텍스트 규칙에는 강제력이 없고, settings.local.json 의 ask 는 개인 설정이라
# 공유되지 않으며 권한 확인을 건너뛰는 모드에서는 동작하지 않는다.
#
# 거부 대상 (복합 명령이면 ; && || | 로 나눈 각 구간을 본다):
#   git checkout ... -- <경로> / git checkout .   작업 트리 파일을 되돌린다
#   git restore ...                               단, --staged 만 있고 --worktree 가 없으면 허용
#   git reset --hard
#   git clean -f / --force                        -n / --dry-run 이 있으면 허용
#
# 사용자가 직접 실행하는 `! <명령>` 은 이 훅을 거치지 않으므로, 거부 사유에서 그 방법을 안내한다.

set -euo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[[ -z "$cmd" ]] && { echo '{}'; exit 0; }

hit=""
while IFS= read -r seg; do
  [[ "$seg" =~ (^|[[:space:]])git[[:space:]] ]] || continue
  seg=" $seg "
  if [[ "$seg" =~ [[:space:]]checkout[[:space:]] && ( "$seg" =~ [[:space:]]--[[:space:]] || "$seg" =~ [[:space:]]\.[[:space:]] ) ]]; then
    hit="git checkout -- (작업 트리 되돌리기)"
  elif [[ "$seg" =~ [[:space:]]restore[[:space:]] ]]; then
    if ! [[ "$seg" =~ [[:space:]](--staged|-S)[[:space:]] ]] || [[ "$seg" =~ [[:space:]](--worktree|-W)[[:space:]] ]]; then
      hit="git restore (작업 트리 되돌리기)"
    fi
  elif [[ "$seg" =~ [[:space:]]reset[[:space:]] && "$seg" =~ [[:space:]]--hard[[:space:]] ]]; then
    hit="git reset --hard"
  elif [[ "$seg" =~ [[:space:]]clean[[:space:]] && "$seg" =~ [[:space:]](-[a-zA-Z]*f[a-zA-Z]*|--force)[[:space:]] ]]; then
    [[ "$seg" =~ [[:space:]](-[a-zA-Z]*n[a-zA-Z]*|--dry-run)[[:space:]] ]] || hit="git clean -f"
  fi
  [[ -n "$hit" ]] && break
done < <(printf '%s\n' "$cmd" | sed -E 's/(&&|\|\||;|\|)/\n/g')

[[ -z "$hit" ]] && { echo '{}'; exit 0; }

reason="$hit 은 미커밋 작업물을 지운다. 같은 작업 트리를 다른 세션·에이전트가 함께 쓸 수 있어 \
공유 훅으로 막아 두었다. 되돌릴 파일이 정말 이번 작업의 것이면 사용자에게 명령을 보여 주고 \
'! <명령>' 으로 직접 실행해 달라고 요청하라. 되돌리기 대신 git stash 나 파일 단위 Edit 로 풀 수 있는지 먼저 검토한다."

jq -n --arg r "$reason" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
