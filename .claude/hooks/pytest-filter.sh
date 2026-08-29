#!/usr/bin/env bash
# PreToolUse(Bash) 훅 — pytest 실행 명령을 토큰 절약형으로 다시 쓴다.
#
# 하는 일:
#   1) --color=no 부착      : ANSI 색상 때문에 '^FAILED' grep 이 빗나가는 문제를 없앤다.
#   2) -p no:randomly 부착  : 실행 순서를 고정해 재현 가능한 결과를 얻는다.
#   3) PASSED/SKIPPED 줄 제거 + 마지막 250줄만 남김
#      pytest 는 FAILURES 상세와 요약을 출력 끝에 쓰므로, 실패 원인은 보존되고
#      통과 테스트 나열(-v 환경에서 수백 줄)만 사라진다.
#   4) set -o pipefail      : 파이프를 붙여도 pytest 종료 코드가 보존된다.
#
# 안전장치: 아래 조건을 모두 만족하는 "단순한" pytest 명령만 건드린다.
#   - 선택적 'cd <경로> && ' 접두사 하나까지만 허용
#   - 나머지 부분에 ; | & ` $( > < 가 없을 것 (복합 명령은 원본 그대로 통과시킨다)
# 조건에 맞지 않으면 빈 JSON({})을 돌려주어 아무것도 바꾸지 않는다.

set -euo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')

[[ -z "$cmd" ]] && { echo '{}'; exit 0; }

# 이미 이 훅이 처리했거나 사용자가 직접 파이프를 건 명령은 건드리지 않는다.
case "$cmd" in
  *pipefail*|*--color=no*) echo '{}'; exit 0 ;;
esac

prefix=""
rest="$cmd"

# 'cd <경로> && ' 접두사 1회 허용
if [[ "$rest" =~ ^(cd[[:space:]]+[^;\&\|\<\>\`]+[[:space:]]+\&\&[[:space:]]+)(.*)$ ]]; then
  prefix="${BASH_REMATCH[1]}"
  rest="${BASH_REMATCH[2]}"
fi

# 나머지에 셸 메타문자가 있으면 복합 명령이므로 원본 유지
case "$rest" in
  *';'*|*'|'*|*'&'*|*'`'*|*'$('*|*'>'*|*'<'*) echo '{}'; exit 0 ;;
esac

# pytest 호출인지 확인 (pytest ... / python -m pytest ... / <venv>/bin/python -m pytest ...)
if [[ ! "$rest" =~ (^|[[:space:]/])pytest([[:space:]]|$) ]]; then
  echo '{}'; exit 0
fi

filtered="set -o pipefail; ${prefix}${rest} --color=no -p no:randomly 2>&1 | grep -vE '(PASSED|SKIPPED|XFAIL|XPASS)' | tail -250"

jq -n --arg c "$filtered" --argjson orig "$(printf '%s' "$input" | jq '.tool_input')" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "allow", updatedInput: ($orig + {command: $c})}}'
