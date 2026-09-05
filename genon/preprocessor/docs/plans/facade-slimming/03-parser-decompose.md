# 03. parser_processor 분해

전제: [00](00-golden-baseline.md) 기준선, [02](02-docling-runtime.md) 완료. 절차는 [WORKFLOW.md](WORKFLOW.md).

## 현재 구성 (2,497줄)

| 구간 | 줄수 | 성격 | 처리 |
|---|---:|---|---|
| 1-137, 158-168 | 148 | import (**두 블록이다** — 158-168 이 공용 모듈 import) | 정리 |
| 139-290 | 152 | 모듈 헬퍼 + Text/Audio 로더 | 대부분 이미 common 별칭 |
| 292-678 | 386 | 임베드 docling 런타임 | **02 에서 제거됨** |
| 679-874 | 196 | `HwpDocumentLoader` / `DocxDocumentLoader` / `GenericDocumentLoader` | 3a |
| 876-894 | 19 | `GenosServiceException` | 유지 |
| 896-1216 | 321 | `__init__`(125줄, 남음) + 설정 빌더 **17개** | 3b |
| 1218-1863 | 646 | 라우팅 + **LLM 동시성·에러정책 오케스트레이션 약 250줄** | facade 에 남긴다(주의) |
| 1865-2247 | 383 | docling → parse-format 직렬화 | 3c |
| 2249-2497 | 249 | `setup_logging` + `__call__` | **facade 에 남긴다** |

## 3a. 로더 → `common/loaders.py`

`common/loaders.py` 에 이미 `TextLoaderBase` / `TabularLoaderBase` / `AudioLoaderBase` 가 있다.

**검증에서 계획의 전제가 틀린 것이 드러났다.**

- `intelligent_processor.py` 에는 `get_loader` 도 로더 클래스도 **하나도 없다.**
  실제 대조 상대는 `attachment_processor.py:1255` 다(파서 파일 주석도 출처를 attachment 로 명시한다).
- `GenericDocumentLoader` 만 베이스 추출이 실제로 가능하다 — `get_real_file_type` 은 attachment 판과
  **완전 동일**하고 `get_loader` 분기 골격도 같다. 차이는 의도(`use_pdf_sdk`/`image_ocr_languages`)와
  드리프트(텍스트 폴백은 parser 에만, OCR 언어 정규화는 attachment 에만)가 섞여 있다. **합치지 않는다.**
- `HwpDocumentLoader` / `DocxDocumentLoader` 는 **parser 단독 소유**다. 옮겨도 서브클래스에 남길
  차이가 존재하지 않아 빈 껍데기만 남는다(`TextLoader`/`AudioLoader` 의 기존 패턴과 같으므로 무리는 없다).

**걸림돌 2가지 — 결정이 필요하다.**

1. `common/loaders.py` 는 현재 **docling import 가 없다.** `HwpDocumentLoader` 를 옮기면
   `DocumentConverter` / `HwpxFormatOption` / `GenosHwpDocumentBackend` 등 8개 심볼이 들어와
   "공용 모듈은 docling 무의존" 원칙과 충돌한다(`facade/enrichment/*` 는 이미 `docling_core` 를
   직접 import 하므로 절대 금칙은 아니다 — 결정을 명시하면 된다).
2. `GenericDocumentLoader.load_documents`(852)가 **facade 로컬 `GenosServiceException`**(876)을
   raise 한다. 공용 모듈에서 어느 예외를 쓸지 정해야 한다(22곳에 복제돼 있다).

## 3b. 설정 빌더 20개

`_build_json_text_specs` / `_build_markdown_front_matter_specs` /
`_build_markdown_text_fence_specs` / `_build_marker_heading_doc_types` /
`_build_html_marker_heading_doc_types` / `_build_tabular_custom_fields_mappers` /
`_build_json_records_mappers` …

**검증 결과 "전부 얇은 래퍼" 는 거짓이다.** 1031-1216 구간에 17개가 있고:

- `_build_json_text_specs`(1054-1071)는 20줄 실로직이다(루프 + `normalize_doc_types` + spec 생성)
- 나머지 6개는 `try/except → GenosServiceException(stage="custom_fields")` **변환이 본체**다.
  코드 주석이 "어느 설정이 문제인지 드러내는 것이 목적" 이라고 명시한다 — 통째로 사라지지 않는다

따라서 3b 의 실제 감소는 계획이 적은 것보다 작다. 얇은 것만 걷어내고 예외 변환은 유지한다.

`_json_records_mappers_for` / `_json_text_spec_for` / `_markdown_front_matter_spec_for` 같은
"런타임 doc_type 으로 고르는" 헬퍼는 성격이 다르다 — 이건 `common/parser_config.py` 로 옮긴다.

## 3c. 직렬화 → `facade/output/parse_format.py`

`_docling_to_parse_format` / `_item_to_html` / `_export_table_content` /
`_get_normalized_coords` / `_docling_sheet_prefix` / `_serialize_docling_document` /
`_replace_markdown_tables_with_html` / `_docling_to_content` / `_docling_page_count` /
`_audio_to_parse_format` / `_tabular_to_parse_format` / `_langchain_to_parse_format` /
`_normalize_response` / `_content_response` / `_build_docling_response`

**검증 결과 "전부 정적 변환 함수, 위험이 가장 낮다" 는 거짓이다. 3c 는 03 에서 가장 위험하다.**

**순수하지 않은 것 2개**

- `_docling_to_content`(2107) — 인스턴스 메서드. `self._output_format` / `self._table_format` 을 읽는다
- `_build_docling_response`(2162) — **직렬화 함수가 아니라 응답 조립 오케스트레이터**다.
  `self._gr_cfg` 등 4종을 읽고, `strip_enricher_meta(doc)` 로 **문서를 변이**하며,
  `gr.classify_document(...)` 로 **외부 HTTP 를 호출**한다. `**kwargs` 도 소비한다.
  "순수 변환" 모듈에 네트워크 호출이 들어가서는 안 된다 — guardrail 부분을 파라미터로 뽑거나
  이 함수는 facade 에 남긴다

**죽은 코드 1개**

- `_item_to_html`(1884) — 유일한 호출부(2006)가 주석 처리돼 있다. 옮길 게 아니라 **지운다**

**연쇄 이동**

- `_docling_to_parse_format` 이 `DocumentProcessor._get_normalized_coords`(1997) 등 **클래스명을
  하드코딩**해 4곳을 호출한다. 이동 시 전부 재작성이 필요하고, 그 순간 `patch.object` 계열 간접
  패치가 무력화된다
- `_export_table_content` 는 모듈 전역 `_doc_is_html_origin`(264)에 의존한다. 3c 목록 밖이고
  3a/3b 어디에도 안 잡혀 있다 — 같이 옮길지 import 할지 결정이 필요하다

**테스트가 조용히 깨지는 지점 (가장 중요)**

| 위치 | 패턴 | 이동 시 |
|---|---|---|
| `test_parser_processor_unit.py:574,585,647,658` | `patch("facade.parser_processor.export_markdown")` | **네 곳 모두 `assert_called_once*` 로 끝나므로 시끄럽게 실패한다**(초판의 "조용히 통과" 는 오판). 안전한 쪽 오차라 계획은 유효 |
| `test_extension_alias_unit.py:108,109,191` | `dp._build_docling_response = MagicMock(...)` | facade 가 얇은 래퍼조차 안 남기면 mock 우회 |
| `test_chunking_processor_unit.py:41` | `parser._build_docling_response(doc)` | **크로스 facade 호출** |
| `test_parser_processor_unit.py:338-341` 외 6곳 | `patch.object(DocumentProcessor, ...)` | 속성이 사라지면 AttributeError |

**결론: facade 에 얇은 래퍼를 남겨야 테스트가 산다.** 실제 감소분은 383줄보다 훨씬 작다.

**디렉터리 이름**

`facade/output/` 은 쓰지 않는다 — `output:` 은 프로세서 설정 YAML **12곳의 최상위 섹션명**이라
"output 을 고쳐라" 가 코드인지 설정인지 모호해진다. **`facade/serialize/`** 를 쓴다.

배포 범위는 문제없다. `sync-serving-repo.sh:170` 이 `git archive` 로 `genon` 전체를 뽑은 뒤
`EXCLUDE_PATHS` 만 지우므로 **신규 하위 디렉터리는 스크립트 수정 없이 자동 포함**된다.
단 `git archive`/`git ls-files` 는 **추적 파일만** 보므로, 커밋하지 않으면 배포본과 패치 번들에서
**에러 없이 조용히 빠진다.**

## facade 에 남기는 것

- 포맷별 파싱 라우팅(1218-1863) — "어떤 확장자를 어느 경로로 보내는가". 사이트가 읽는 곳이다
- `__call__` — 요청 진입점
- `GenOSVectorMeta` 계열, `GenosServiceException`

## 커밋 단위

3a / 3b / 3c 각각 1커밋. 셋은 서로 독립이므로 순서를 바꿔도 되고, 하나가 문제되면 그것만 되돌린다.

**순서는 3a → 3b → 3c 로 고정한다.** 3c 가 가장 위험하므로 마지막이고, 00 이 동작한 뒤여야 한다.
신규 디렉터리는 **만든 커밋에서 함께 커밋**한다 — 중간 상태로 sync 를 돌리면 `__init__.py`
누락 같은 사고가 난다.

## 검증

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit/test_parser_processor_unit.py \
  tests/unit/test_hwp_routing_unit.py tests/unit/test_extension_alias_unit.py \
  tests/unit/test_md_text_fence_unit.py tests/unit/test_markdown_front_matter_unit.py \
  tests/unit/test_custom_fields_routing.py \
  -q -p no:randomly --color=no
examples/parse_chunk/parse_chunk_golden.py --check
```

**주의 1: `parse_chunk_golden.py` 는 아직 존재하지 않는다.** 00 이 만든다고 예고만 한 상태다.

**주의 2 (더 중요): 지금 형태의 골든은 3c 대상 15개 중 5개만 실행한다.**
`parse_chunk_test.py:91` 이 `_output_format = "docling"` 을 강제해
`_docling_to_parse_format` · `_export_table_content` · `_get_normalized_coords` ·
`_docling_sheet_prefix` · `_docling_to_content` · `_content_response` ·
`_replace_markdown_tables_with_html` 이 **전부 죽은 경로**가 된다.
PDF 케이스를 추가해도 이 구멍은 그대로다 — 포맷 축이 다르다.

→ **00 이 `--output-format` 을 추가해 `json` 골든을 함께 기록한 뒤에만 3c 에 착수한다.**
그 전까지 3c 의 안전망은 `test_parser_processor_unit.py` 의 MagicMock 유닛뿐이다.

검증 명령에 **`tests/unit/test_chunking_processor_unit.py` 를 반드시 넣는다** —
`:34-46` 이 `object.__new__(pp.DocumentProcessor)` 로 parser 인스턴스를 만들어
`parser._build_docling_response(doc)` 를 **크로스 facade 로** 호출한다.

## 예상 결과

**2,110 → 약 1,400줄** (초판의 "~1,000" 은 350~450줄 낙관이었다).

산술: 2,497 − 387(02) = 2,110. 여기서 3a(196) + 3b(186) + 3c(383) = 765 를 빼면 1,345 이고,
테스트가 요구하는 래퍼 15개와 신규 import 를 더하면 1,400~1,450 이 현실적이다.

**이 저장소 자신의 대용량 파일 가드 임계가 1,200줄이다.** 리팩터링 후에도 그 선을 넘으므로
"고객이 읽을 수 있는 크기" 는 04 의 표면 정리로 확보해야 한다.
