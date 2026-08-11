#!/usr/bin/env bash
# 분리 배포된 parser/chunker 전처리기를 직접 호출하는 curl 테스트 스크립트.
#
# 처리 흐름:
#   1. parser /run 호출
#   2. parser 응답의 data.document를 doc.json으로 저장
#   3. doc.json을 chunker의 params.document로 전달
#   4. chunker 응답의 data를 chunks.json으로 저장

set -euo pipefail

PARSER_URL="${PARSER_URL:-http://preprocessor-695:8080/run}"
CHUNKER_URL="${CHUNKER_URL:-http://preprocessor-698:8080/run}"
FILE_PATH="${FILE_PATH:-/nfs-root/DEV/volume/50/file_test/test_small_page.pdf}"
RESULT_DIR="${RESULT_DIR:-./result_serving_direct_parser_chunker_test}"

PARSER_HEALTH_URL="${PARSER_URL%/run}/healthcheck"
CHUNKER_HEALTH_URL="${CHUNKER_URL%/run}/healthcheck"

for command in curl jq; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "필수 명령을 찾을 수 없습니다: ${command}" >&2
    exit 1
  fi
done

mkdir -p "${RESULT_DIR}"

PARSER_RESPONSE="${RESULT_DIR}/parser_response.json"
DOC_JSON="${RESULT_DIR}/doc.json"
CHUNKER_RESPONSE="${RESULT_DIR}/chunker_response.json"
CHUNKS_JSON="${RESULT_DIR}/chunks.json"

echo "[1/4] parser healthcheck: ${PARSER_HEALTH_URL}"
curl --silent --show-error --fail "${PARSER_HEALTH_URL}" | jq .

echo "[2/4] chunker healthcheck: ${CHUNKER_HEALTH_URL}"
curl --silent --show-error --fail "${CHUNKER_HEALTH_URL}" | jq .

echo "[3/4] parser 실행: ${FILE_PATH}"
jq -nc --arg file_path "${FILE_PATH}" \
  '{file_path:$file_path, params:{}}' \
| curl --silent --show-error --fail \
    --request POST "${PARSER_URL}" \
    --header 'Content-Type: application/json' \
    --data-binary @- \
    --output "${PARSER_RESPONSE}"

jq -e '
  if .code == 0 and (.data.document | type) == "object" then
    .data.document
  else
    error("parser 실패: " + (.errMsg // "유효한 data.document가 없습니다"))
  end
' "${PARSER_RESPONSE}" > "${DOC_JSON}"

PAGES="$(jq -r '.data.usage.pages // "unknown"' "${PARSER_RESPONSE}")"
echo "parser 완료: pages=${PAGES}, document=${DOC_JSON}"

echo "[4/4] chunker 실행"
# 문서 JSON이 크더라도 ARG_MAX를 넘지 않도록 요청 본문을 stdin으로 전달한다.
jq -nc --slurpfile document "${DOC_JSON}" \
  '{file_path:"test_small_page.pdf", params:{document:$document[0], chunk_size:0}}' \
| curl --silent --show-error --fail \
    --request POST "${CHUNKER_URL}" \
    --header 'Content-Type: application/json' \
    --data-binary @- \
    --output "${CHUNKER_RESPONSE}"

jq -e '
  if .code == 0 and (.data | type) == "array" then
    .data
  else
    error("chunker 실패: " + (.errMsg // "유효한 data 배열이 없습니다"))
  end
' "${CHUNKER_RESPONSE}" > "${CHUNKS_JSON}"

CHUNK_COUNT="$(jq 'length' "${CHUNKS_JSON}")"
echo "chunker 완료: chunks=${CHUNK_COUNT}, result=${CHUNKS_JSON}"

