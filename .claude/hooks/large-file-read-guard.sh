#!/usr/bin/env bash
# PreToolUse(Read) 훅 — 큰 파일을 통째로 Read 하려는 시도를 막는다.
#
# 왜: 아래 파일들은 전체 Read 가 20k~50k 토큰씩 든다. 다 읽으면 190k 토큰으로
# 컨텍스트의 상당 부분이 날아간다. CLAUDE.md 가 금지하고 있지만 텍스트 규칙에는
# 강제력이 없어서 훅으로 고정한다.
#
# 동작: 대상 파일에 offset/limit 없이 들어온 Read 만 거부하고, 이유를 반환한다.
# offset 또는 limit 이 하나라도 있으면 통과시킨다(구간 읽기는 정상 사용법).
# 그 외 파일은 줄 수를 세서 임계값을 넘을 때만 같은 판정을 적용한다.

set -euo pipefail

THRESHOLD=1200   # 이 줄 수를 넘는 파일은 구간 읽기를 요구한다

input=$(cat)

tool=$(printf '%s' "$input" | jq -r '.tool_name // empty')
[[ "$tool" != "Read" ]] && { echo '{}'; exit 0; }

path=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
offset=$(printf '%s' "$input" | jq -r '.tool_input.offset // empty')
limit=$(printf '%s' "$input" | jq -r '.tool_input.limit // empty')

# 구간 읽기는 통과
[[ -n "$offset" || -n "$limit" ]] && { echo '{}'; exit 0; }

[[ -z "$path" || ! -f "$path" ]] && { echo '{}'; exit 0; }

# 텍스트 소스만 대상. 이미지·PDF·노트북은 구간 읽기 개념이 다르므로 건드리지 않는다.
case "$path" in
  *.py|*.md|*.yaml|*.yml|*.sh|*.json) ;;
  *) echo '{}'; exit 0 ;;
esac

lines=$(wc -l < "$path" | tr -d ' ')
[[ "$lines" -le "$THRESHOLD" ]] && { echo '{}'; exit 0; }

reason="$path 은 ${lines}줄이라 전체 Read 가 컨텍스트를 크게 먹는다. \
Grep 으로 심볼·문자열 위치를 먼저 찾고 Read 의 offset/limit 으로 해당 구간만 읽어라. \
파일 전체 구조만 필요하면 rg -n '^(class|def|    def) ' \"$path\" 로 목차를 뽑는 편이 싸다. \
전체가 정말 필요하면 offset=1 limit=${lines} 로 의도를 명시해서 다시 호출하면 통과한다."

jq -n --arg r "$reason" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $r}}'
