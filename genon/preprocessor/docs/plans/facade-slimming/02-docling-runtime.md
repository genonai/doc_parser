# 02. docling 런타임을 공용 모듈로 추출

전제: [00](00-golden-baseline.md) 기준선, [01](01-chunking-dead-code.md) 완료. 절차는 [WORKFLOW.md](WORKFLOW.md).

> 독립 검증을 마쳤다(2026-09-05). **핵심 전제가 틀렸다.**
> "경계는 이미 잘려 있다 / 있던 것을 제자리로 옮기는 작업" 은 **메서드 집합에서만** 참이고,
> 속성 집합은 parser 임베드본 고유 3개 · intelligent 고유 24개로 갈라져 있다.
> 단순 이동이 아니며, base 의 모양을 먼저 정해야 한다. 아래에 반영했다.


## 무엇이 문제인가

같은 docling 배관이 facade 마다 복제돼 있다. 실측:

- 메서드 단위 유사도 0.85 이상인 사실상 동일 코드 **502줄**
  (`_create_converters` 1.00, `enrich_metadata` 1.00, `enrich_custom_fields` 1.00,
  `check_glyphs` 1.00, `ocr_all_table_cells` 0.92, `load_documents_with_docling` 0.84)
- `__init__` 중복 **약 680줄**

| 대조 | 유사도 | 동일 줄 |
|---|---:|---:|
| intelligent vs convert | 0.97 | 331줄 중 303 |
| intelligent vs chunking | 0.62 | 244줄 중 205 |
| intelligent vs parser(임베드본) | 0.49 | 242줄 중 174 |

복제의 대가는 이미 치렀다. parser 의 임베드 사본이 원본을 따라가지 못해
`check_empty_text` 가 빠진 채로 남았고 크래시로 드러났다.

## 경계는 이미 잘려 있다

`parser_processor.py:292-678` 의 `IntelligentDocumentProcessor`(386줄)와
`intelligent_processor.py` 의 `DocumentProcessor` 를 메서드 집합으로 대조하면:

```
공통 21개 / parser 임베드본 고유 0개 / intelligent 고유 15개(__call__, compose_vectors,
split_documents, _process_pdf, _process_xlsx …)
```

메서드 집합만 보면 경계가 깔끔하다. **그러나 속성 집합은 갈라져 있다.**

| | 개수 | 예 |
|---|---:|---|
| parser 임베드본에만 있음 | **3** | `_ext_aliases`, `_md_cfg`, `custom_fields_cfgs` |
| intelligent 에만 있음 | **24** | `_chunk_size`, `_tokenizer`, `_table_format`, `_guardrail_*`, `_page_desc_options`, `_hwp_recovery`, `_load_document` … |

parser 의 `DocumentProcessor` 는 그 3개를 전부 읽는다(`self._intel._ext_aliases` 918,
`_md_cfg` 917, `custom_fields_cfgs` 922·995·1000·1007·1014·1018·1023).
**base 를 intelligent 모양으로 만들면 parser 가 기동 시 `AttributeError` 로 죽는다** —
메모리에 남은 `check_empty_text` 누락 크래시와 정확히 같은 사고 유형이다.

`__init__` 시그니처도 다르다. parser 임베드본은 `(config: dict|None, config_path: str|None)`,
intelligent/convert 는 `(config_path: str|None)` 로 yaml 을 스스로 읽는다.

**따라서 "옮기기" 가 아니라 "base 모양 결정" 이 이 단계의 첫 작업이다.**

## 만들 것

`facade/common/docling_runtime.py` — `DoclingRuntimeBase`

담는 것(위 공통 21개):

- `__init__` 의 OCR · PDF 파이프라인 · layout · models · converters 배선
- `_build_ocr_options` / `_create_converters` / `load_documents*`
- `enrichment` / `enrich_image_descriptions` / `enrich_doc_summary` /
  `enrich_table_descriptions` / `enrich_metadata` / `enrich_custom_fields` /
  `_get_or_create_*_enricher`
- `check_glyph_text` / `check_glyphs` / `check_empty_text` / `ocr_all_table_cells`
- `_normalize_runtime_kwargs` / `_configure_runtime_image_mode`

**`setup_logging` 과 `safe_join` 은 빼야 한다** — 이 둘은 parser 임베드본에 없어서
"공통 21개" 에 포함되지 않는다. 초판 목록이 자기모순이었다.

**`enrichment` 도 그대로 담으면 안 된다**(아래 "결정할 것" 2번).

한 가지 더 — **21개의 실제 로직은 이미 `common/docling_ops.py` 에 한 벌만 있다.**
`_create_converters`→`dops.create_converters`, `check_glyphs`→`dops.check_glyphs`,
`ocr_all_table_cells`→`dops.ocr_all_table_cells` 처럼 대부분이 1~8줄 래퍼다.
**02 가 없애는 것은 래퍼 스텁과 `__init__` 배선이지 "중복 로직" 이 아니다.**

## 배선

| facade | 방식 | 이유 |
|---|---|---|
| intelligent, convert | `class DocumentProcessor(DoclingRuntimeBase)` 상속 | `__init__` 유사도 0.97 |
| parser | **현행대로 합성** — `self._intel = DoclingRuntimeBase(cfg, config_path=...)` | 지금이 이미 합성이다. 상속 전환은 범위 밖 |
| chunking | 대상 아님 | 01 에서 제거됨 |
| attachment | 대상 아님 | PyMuPDF 경로라 docling 런타임이 없다 |

## 결정할 것 — 착수 전에 문서에 적는다

### 1. base 의 모양

| 안 | 내용 | 대가 |
|---|---|---|
| **(A) parser 모양** — docling 런타임만 | intelligent/convert 가 `super().__init__()` 뒤에 자기 배선 ~130줄 유지 | parser 가 쓰는 `_ext_aliases`/`_md_cfg`/`custom_fields_cfgs` 를 base 가 세우도록 추가 |
| (B) intelligent 모양 | parser 에게 과하고, 위 3개를 별도로 넣어야 하며 parser `_intel` 이 chunking/guardrail 설정을 이중 파싱 | 위험이 크다 |

**(A) + 훅 2개**(`_default_config_name` ClassVar, `_after_pipeline_options()`)가 위험이 작다.

### 2. `enrichment` 는 base 에 넣지 않는다

본문이 세 곳 다르다.

- **시그니처** — parser(582)는 `(document, **kwargs)`, intelligent(809)·convert(903)는
  `(document, is_ppt=False, **kwargs)` 에 PPT TOC 비활성 블록 4줄이 더 있다
- **예외** — intelligent(832)만 `stage="enrichment", error_type=_classify_error(e)` 로 raise
- **None 가드** — intelligent 의 `enrich_*` 3개에는 `if enricher is None: return document` 가
  있는데 parser 판에는 없다

**결정적 위험**: `convert_processor.py:1586` 의 로컬 `GenosServiceException` 은
`(error_code, error_msg, msg_params)` 만 받는다. **`stage`/`error_type` 을 안 받는다.**
base 가 intelligent 판을 담으면 convert 에서 `TypeError` 가 난다 —
**LLM API 오류가 실제로 났을 때만** 터지고 원래 오류 메시지를 뭉갠다.
유닛에도 골든에도 잡히지 않는다.

→ `enrichment` 는 base 에서 빼거나, 넣더라도 `stage`/`error_type` 없이 raise 하고
스탬핑은 서브클래스 override 에 남긴다.

### 3. convert 의 `load_documents` override 를 유지한다

intelligent(759)는 2줄이지만 **convert(746-793)는 48줄** — HWP/HWPX 레거시 백엔드 폴백과
`.hml` 빈 문서 처리(#323)가 들어 있다. base 로 덮으면 HWP 폴백이 통째로 사라지고
**증상은 특정 HWP 파일에서만** 나온다.

### 4. `IntelligentDocumentProcessor` 이름을 남긴다

`tests/unit/test_parser_processor_unit.py:28` 과 `test_processor_enrichment_unit.py:49` 가
**모듈 최상위에서 import** 한다. 이름을 지우면 skip 이 아니라 **collect 단계 에러**로 죽는다.
`IntelligentDocumentProcessor = DoclingRuntimeBase` 별칭을 남긴다.

## 주의

- **`object.__new__` 로 `__init__` 을 우회해 만든 인스턴스를 쓰는 유닛 테스트가 있다.**
  프로세서 속성을 읽는 코드는 `getattr(..., 기본값)` 으로 속성 부재를 견뎌야 한다.
- 공용 모듈의 docling import 는 이미 관례다 — `common/` 14개 중 **4개**(`docling_ops`,
  `markdown_export`, `pipeline_setup`, `vector_meta`)가 이미 docling 을 import 한다.
  `docling_runtime.py` 를 `common/` 에 두는 것은 기존 관례와 일치한다.
- **옮긴 메서드가 참조하는 이름이 base 모듈의 전역이 된다** — `_log`, `_classify_error`,
  `enrich_document`, `LLMApiError`, `ImageDescriptionOptions` 등. facade 파일에서 이 이름을
  바꿔도 더 이상 효과가 없다. **facade 한 파일을 열어 사이트별로 손보는 운용 방식(04·05)과
  정면으로 부딪히므로**, 어떤 이름이 더 이상 facade 에서 조정 불가가 되는지 04 에 기록한다.
  로그 레코드의 `name` 도 `...facade.intelligent_processor` →
  `...facade.common.docling_runtime` 로 바뀌어 운영 로그 grep 이 깨진다.
- **`generate_page_images` 강제 보정의 순서 의존** — intelligent·convert 는
  `if table_image_enabled or page_desc.enabled: generate_page_images = True` 를
  `ocr_pipe_line_options = ...model_copy(deep=True)` **보다 먼저** 실행한다.
  parser 임베드본에는 이 보정이 아예 없다. base 를 parser 모양으로 놓고 서브클래스가
  `super().__init__()` **뒤에** 강제하면 OCR 컨버터 옵션에는 반영되지 않는다.
  `test_table_image_unit.py:123-129` 는 `pipe_line_options` 만 검사해 이 구멍을 못 잡는다.
- `GenosServiceException` 은 활성 경로 7곳과 legacy 포함 총 22곳에 복제돼 있다.
  이번 작업에서 **통합하지 않는다** — 영향 범위가 legacy 까지 퍼지고 이번 결함 범위 밖이다.
  고정 개수를 가정하지 말 것.

## 커밋 단위

2개로 나눈다.

1. `common/docling_runtime.py` 신설 (기존 파일 무변경)
2. **속성 존재 단정 테스트 추가** — `_ext_aliases` / `_md_cfg` / `custom_fields_cfgs` 3개가
   base 인스턴스에 있는지 단정한다. 이것이 `check_empty_text` 사고의 재발 방지책이다
3. intelligent / convert / parser 배선 교체 + 중복 삭제

1·2번만으로는 아무것도 깨지지 않으므로 되돌림 지점이 둘 더 생긴다.

## 검증

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit/test_intelligent_processor_unit.py \
  tests/unit/test_convert_processor_unit.py tests/unit/test_parser_processor_unit.py \
  tests/unit/test_processor_enrichment_unit.py tests/unit/test_extension_alias_unit.py \
  tests/unit/test_enrichment_yaml_unit.py tests/unit/test_table_image_unit.py \
  tests/unit/test_pii_masking_unit.py tests/unit/test_chunker_tokenizer_type.py \
  tests/unit/test_table_text_description_standalone.py \
  -q --color=no
examples/parse_chunk/parse_chunk_golden.py --check
```

뒤 5개는 검증에서 추가됐다. 특히 `test_table_image_unit.py` 는 위 `generate_page_images`
강제 보정을, `test_table_text_description_standalone.py` 는 `object.__new__` 경로를 짚는다.

`pytest-randomly` 는 **설치돼 있지 않다**(실측). `-p no:randomly` 는 no-op 이라 뺐다.

이 단계는 4개 facade 를 동시에 건드리므로 **종료 시 전체 유닛 1회**를 돌린다.

## 예상 결과

초판의 "약 1,100줄 감소" 는 **신규 모듈을 계산에 넣지 않았다.**

| 항목 | 줄수 |
|---|---:|
| facade 합계 감소 | 약 −950 |
| 신규 `common/docling_runtime.py` | +450 ~ +500 |
| **저장소 순감** | **약 −500** |

convert 의 −350 도 과대다 — 메서드 133줄 + `__init__` 흡수분 약 150 = **약 283** 이 현실적이다.
