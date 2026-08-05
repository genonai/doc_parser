
# python serving_gateway_test.py --mode e2e --file-path "/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf" --out result_serving_gateway_test/
# python serving_gateway_test.py --mode parser_upload --upload-file "../../../../shkim_labs/20260609_convert_test/10.여비규정_20240129_인사경영국_20240129.pdf" --out-doc result_serving_gateway_test/doc.json
python serving_gateway_test.py --mode parser --file-path "/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf" --out-doc result_serving_gateway_test/doc.json
python serving_gateway_test.py --mode chunker --doc-json result_serving_gateway_test/doc.json --out result_serving_gateway_test/chunks.json

# python serving_gateway_test.py --mode parser_upload --upload-file "../../../../shkim_labs/20260803_monimo/01_card/card01.flat.html" --out-doc result_serving_gateway_test/doc.json --doc-type card
# python serving_gateway_test.py --mode parser_upload --upload-file "../../../../shkim_labs/20260803_monimo/02_faq/생명FAQ_260712.xlsx" --out-doc result_serving_gateway_test/doc.json --doc-type faq
# python serving_gateway_test.py --mode chunker --doc-json result_serving_gateway_test/doc.json --out result_serving_gateway_test/chunks.json --doc-type faq


# ── 문서유형(doc_type) 지정: FAQ 엑셀(행별 custom_fields) / 카드(문서 metadata 스탬프) ──────────
# --doc-type 으로 전달(= --param doc_type=.. 와 동일, 둘 다 주면 --param 우선).
# FAQ xlsx: parser 가 doc_type=faq 로 행별 custom_fields_row 파싱 → chunker 가 행마다 1청크.

# python serving_gateway_test.py --mode parser --file-path "/app/src/service/genon/preprocessor/sample_files/생명FAQ_260712.xlsx" \
#   --doc-type faq --out-doc result_serving_gateway_test/faq_doc.json
# python serving_gateway_test.py --mode chunker --doc-json result_serving_gateway_test/faq_doc.json \
#   --out result_serving_gateway_test/faq_chunks.json

# 또는 단일콜(run: intelligent/convert facade, 파싱+청킹 일괄):
# python serving_gateway_test.py --mode run --file-path "/app/src/service/genon/preprocessor/sample_files/생명FAQ_260712.xlsx" \
#   --doc-type faq --out result_serving_gateway_test/faq_run.json --serving-id <RUN_SERVING_ID> --auth-key <KEY>

# 카드 HTML(flatten 후): 모든 청크에 doc_type=card + 카드 12필드.
# python serving_gateway_test.py --mode run --file-path "/app/src/service/genon/preprocessor/sample_files/card.flat.html" \
#   --doc-type card --out result_serving_gateway_test/card_run.json --serving-id <RUN_SERVING_ID> --auth-key <KEY>


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

