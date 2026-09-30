# examples

고객·개발자가 읽고 그대로 붙여 쓰는 최소 예제를 둔다.

| 폴더 | 내용 |
|---|---|
| `facade_hooks/` | 파사드 훅(`edit_chunk`, `ROUTES` 등)과 Python extractor 를 붙여 쓰는 예 |
| `text_cleanup/` | 설정(`cleanup_rules.yaml`)과 훅으로 청크 텍스트를 정제하는 예. `run_cleanup_examples.sh` 로 두 방식을 비교해 실행할 수 있다 |
| `code_serving/` | 게이트웨이·분리 배포 서비스를 curl 로 호출하는 예 |

검증·개발 도구(설정 점검, 골든 기준선, doc_type 자동 검증, 서빙 테스트 스크립트 등)는
`../tools/` 에 있다. 이전 릴리스에서 `examples/` 아래에 있던 `parse_chunk/`, `config_precheck/`,
`chunk_validation/`, `chunk_bbox/` 와 `code_serving/` 의 Python·셸 테스트 스크립트는 모두 `tools/` 로 옮겼다.
