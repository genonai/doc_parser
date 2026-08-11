# 실행 전 접속 정보를 환경변수로 지정하세요 (스크립트에 키를 넣지 않습니다):
#   export GENOS_BASE_URL=https://<GENOS_HOST>
#   export GENOS_SERVING_ID=<SERVING_ID>
#   export GENOS_AUTH_KEY=<AUTH_KEY>
#
# 인증키는 인자로 넘기지 않습니다 — 스크립트가 읽는 python 이 GENOS_AUTH_KEY 를 직접 읽으므로,
# 넘기면 `ps` 등 프로세스 목록에 토큰이 그대로 보입니다.


# python serving_gateway_test.py --mode e2e --file-path "/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf" --out result_serving_gateway_test/
# python serving_gateway_test.py --mode parser_upload --upload-file "../sample_files/hwp_sample_table.hwp" --out-doc result_serving_gateway_test/doc.json --serving-id "$GENOS_SERVING_ID"
python serving_gateway_test.py --mode parser --file-path "/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf" --out-doc result_serving_gateway_test/doc.json --base-url "$GENOS_BASE_URL" --serving-id "$GENOS_SERVING_ID"
python serving_gateway_test.py --mode chunker --doc-json result_serving_gateway_test/doc.json --out result_serving_gateway_test/chunks.json --base-url "$GENOS_BASE_URL" --serving-id "$GENOS_SERVING_ID"

# ── 문서유형(doc_type) 지정: FAQ 엑셀(행별 custom_fields) / 카드(문서 metadata 스탬프) ──────────
# --doc-type 으로 전달(= --param doc_type=.. 와 동일, 둘 다 주면 --param 우선).
# FAQ xlsx: parser 가 doc_type=faq 로 행별 custom_fields_row 파싱 → chunker 가 행마다 1청크.

# python serving_gateway_test.py --mode parser --file-path "/app/src/service/genon/preprocessor/sample_files/생명FAQ_260712.xlsx" \
#   --doc-type faq --out-doc result_serving_gateway_test/faq_doc.json
# python serving_gateway_test.py --mode chunker --doc-json result_serving_gateway_test/faq_doc.json \
#   --out result_serving_gateway_test/faq_chunks.json

# 또는 단일콜(run: intelligent/convert facade, 파싱+청킹 일괄):
# run 은 다른 서빙을 쓰므로 그 서빙의 id/인증키가 필요하다. 인증키는 인자가 아니라 env 로 넘긴다
# (인자로 주면 프로세스 목록에 토큰이 노출됨).
# GENOS_AUTH_KEY=<RUN_KEY> python serving_gateway_test.py --mode run --file-path "/app/src/service/genon/preprocessor/sample_files/생명FAQ_260712.xlsx" \
#   --doc-type faq --out result_serving_gateway_test/faq_run.json --serving-id <RUN_SERVING_ID>

# 카드 HTML(flatten 후): 모든 청크에 doc_type=card + 카드 12필드.
# GENOS_AUTH_KEY=<RUN_KEY> python serving_gateway_test.py --mode run --file-path "/app/src/service/genon/preprocessor/sample_files/card.flat.html" \
#   --doc-type card --out result_serving_gateway_test/card_run.json --serving-id <RUN_SERVING_ID>


# ── LLM 캐시 테스트 ──────────────────────────────────────────────────────
# 캐시는 별도 모드가 아니라 parser 에 --param 으로 opt-in. 같은 스코프(workflow_id/run_id/interim_root)로
# 파싱을 2회 호출 → 1회차 MISS(실제 호출+저장), 2회차 HIT(캐시 재사용). 두 doc 산출물이 동일하면 OK.
# 전제: 서빙 컨테이너에 INTERIM_ROOT env(또는 --param interim_root)와 공유 NFS 가 있어야 캐시가 켜짐.
# 서버 로그의 "[llm_cache] HIT/MISS ..." 및 "[llm_cache] hit=.. miss=.." 요약으로 확인.

# FILE="/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf"
# INTERIM="/nfs-root/interim"      # 서빙이 접근 가능한 공유 NFS 경로로 지정
# python serving_gateway_test.py --mode parser --file-path "${FILE}" \
#   --param llm_cache=1 --param interim_root="${INTERIM}" \
#   --param workflow_id=wf-gw-001 --param run_id=run-1 \
#   --out-doc result_serving_gateway_test/doc_run1.json
# python serving_gateway_test.py --mode parser --file-path "${FILE}" \
#   --param llm_cache=1 --param interim_root="${INTERIM}" \
#   --param workflow_id=wf-gw-001 --param run_id=run-1 \
#   --out-doc result_serving_gateway_test/doc_run2.json

# error_policy=strict (enrichment 실패 시 code=1 + stage/error_kind):
# python serving_gateway_test.py --mode parser --file-path "${FILE}" \
#   --param llm_cache=1 --param interim_root="${INTERIM}" \
#   --param workflow_id=wf-gw-001 --param run_id=run-1 --param error_policy=strict

# 요청 deadline(초) — 초과 시 timeout 응답(행잉 방지):
# python serving_gateway_test.py --mode parser --file-path "${FILE}" --param request_deadline=60

