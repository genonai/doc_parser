# 01. chunking_processor 의 미사용 파싱 런타임 제거


> **상태: 완료 (2026-09-06).** 1,536 → 1,217줄. 개정판의 유지 목록을 그대로 따랐고
> `settings.perf.page_batch_size` 는 남겼다(전역값 32 유지를 실측 확인).
> "함께 걷어낼 죽은 코드"(별칭 8개, `safe_join`, 모듈 `convert_to_pdf`)도 함께 지웠다.
> 잃는 안전망은 `examples/config_precheck/precheck_custom_fields.py` 의
> `PROCESSOR_CONFIGS` 가 이미 `chunking_processor_config.yaml` 을 포함하고 있어 그쪽이 받는다.

전제: [00](00-golden-baseline.md) 기준선. 작업 절차는 [WORKFLOW.md](WORKFLOW.md) 를 따른다.

> 독립 검증을 마쳤다(2026-09-05). **"미호출" 판정 15건은 전부 참**으로 확인됐다 —
> 호출 그래프 전수 추적, `getattr` 9곳 전부 속성이며 메서드 디스패치 없음,
> `globals()`/`eval`/`importlib`/`setattr`/`__getattr__` 0건, 부모 클래스와 공용 모듈이
> processor 인스턴스를 받지 않음.
> **다만 삭제 범위와 유지 목록이 틀렸고, "관측 가능 변화" 단언도 틀렸다.** 아래에 반영했다.


## 무엇이 문제인가

`chunking_processor.py` 는 **파싱 결과를 입력받아 청킹만** 한다(Chunk API). 그런데 docling
파싱 런타임 전체를 기동 시 구성한다 — OCR 옵션, PDF 파이프라인, layout, 컨버터 4개,
enrichment 옵션까지.

호출부를 전수 조사한 결과 아래는 `__call__` 경로에서 **한 번도 호출되지 않는다.**

| 대상 | 위치 |
|---|---|
| `load_documents` / `load_documents_with_docling` / `_with_docling_ocr` / `_create_converters` / `_build_ocr_options` | 631-679 |
| `enrichment` / `enrich_image_descriptions` / `enrich_metadata` / `enrich_custom_fields` / `_get_or_create_image_description_enricher` | 727-761 |
| `check_glyphs` / `check_glyph_text` / `ocr_all_table_cells` / `_save_table_images` | 919-947 |
| 모듈 최상위 `convert_to_pdf` | 30-43 |
| `__init__` 배선 — **아래 6개 구간만** | `407-410`, `460-475`, `478-487`, `507-526`, `528-554`, `555-589`, `594-630` |
| `_ACCELERATOR_DEVICE_MAP` / `_TABLE_FORMER_MODE_MAP` 과 딸린 docling import | 상단 |

설정 쪽 근거가 더 분명하다. `resource/chunking_processor_config.yaml` 은 최상위에
`defaults` / `chunking` / `table_image` / `output` / `guardrail` **5개 섹션만** 두고,
주석에 "ocr/layout/pdf_pipeline/enrichment 섹션은 두지 않는다" 고 적혀 있다.
즉 **없는 설정을 파싱해 안 쓰는 객체를 만들고 있다.**

## 유지할 것 — 삭제 범위 안에 섞여 있다

**초판이 `452-630` 을 통째로 삭제 범위로 잡았는데, 그 안에 반드시 살아야 하는 대입이 있다.**
줄 단위로 못박는다.

| 줄 | 속성 | 사용처 | 지우면 |
|---:|---|---|---|
| 452 | `_gr_cfg` | `__call__:1457` | 크래시 |
| **458** | **`_recursive_chunk_overlap`** | `_resolve_recursive_split_params:1018` | **`getattr(...,100)` 이라 조용히 기본값으로 되돌아간다.** 사이트가 다른 값을 설정했으면 말없이 무시된다 |
| **476** | **`page_chunk_counts`** | `split_documents:719`, `compose_vectors:895` | 직접 접근이라 `AttributeError` |
| 488-505 | `table_image_enabled`, `_table_format`, `_compact_tables`, `_table_row_serialization`, `_table_text_formats` | compose_vectors / config_parse duck typing | |
| 527 | `settings.perf.page_batch_size` | **아래 "전역" 절 참조 — 이번엔 남긴다** | |
| 587-590 | `_metadata_field_transforms` | compose_vectors:781 | |

굵게 표시한 둘이 **초판의 "유지할 것" 목록에 없었다.**

`_config_dir`(398)과 `EnrichmentConfig.from_raw`(411) 호출도 유지한다 — 후자에서
`_metadata_field_transforms` 만 꺼내 쓴다.

그 밖에 `GenosSmartChunker` ClassVar 와 헤더 구분자 상수는 사이트 조정 지점이라 유지한다.

## `settings.perf.page_batch_size` — 이번에는 지우지 않는다

`chunking_processor.py:527` 이 docling **전역 싱글턴**을 건드린다.

chunking 설정에는 `layout:` 섹션이 없어 기본값 **32** 가 들어간다. 그런데 루트 `main.py:151-155`
가 attachment → intelligent → convert → parser → **chunking 순으로 생성**하므로,
통합 실행 프로세스의 최종 전역값은 **chunking 이 마지막에 덮어쓴 32** 다
(다른 셋의 설정은 전부 `page_batch_size: 128`).

**이 줄을 지우면 전역이 128 로 바뀌어 `/parser` 와 `/preprocess*` 의 docling 페이지 배치
동작이 달라진다.** 코드서빙(단일 facade 마운트)에서는 영향이 없지만 통합·로컬 실행에서는
골든 대조가 흔들린다.

따라서 **527행은 남긴다.** "이걸 빼면 보고된 증상이 남는가" 기준으로 보면 이 줄의 제거는
이번 작업에 필요하지 않고, 영향 범위만 넓힌다. **전역 오염 자체는 별도 이슈로 분리한다.**

## 함께 걷어낼 죽은 코드 (검증에서 추가 발견)

계획에 없었지만 참조가 0인 것들이다. 지울지는 선택이며, 지우면 목표치에 더 가까워진다.

`safe_join`(722), 별칭 8개(`_is_pdf` 65, `_filename_title_candidates` 64,
`_normalize_filename_title` 66, `_parse_optional_float` 68, `_union_paths` 71,
`_warn_unresolved_placeholders` 72, `_collapse_paths` 82, `_render_header_paths` 88),
그리고 이 별칭들만 쓰는 `fp`/`pc` import.

## 잃는 것 하나 — 명시해 둔다

지금은 `_build_document_custom_fields_enrichers`(565)가 기동 시
`지원하지 않는 custom_fields extractor` 로 `ValueError` 를 던진다. 이 배선을 지우면
**chunking 설정의 `enrichment.custom_fields` 오기입이 기동에서 안 걸린다.**

CLAUDE.md 의 "설정 오기입은 기본적으로 기동 실패" 원칙과 어긋나는 방향이다.
대체 안전망으로 `examples/config_precheck/precheck_custom_fields.py:61-62` 가
`chunking_processor_config.yaml` 을 검사 대상에 포함하고 있는지 확인하고, 그것으로 충분한지 판단한다.

## 왜 지금 이 순서인가

- 위험이 가장 낮다. 되돌리기가 쉽고, 여기서 골든 대조 절차의 실제 비용을 먼저 잰다.
- 02(공용 런타임 추출)의 대상을 미리 줄인다. 청커를 상속 대상에서 빼면 02 가 단순해진다.

## 검증

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit/test_chunking_processor_unit.py \
  tests/unit/test_table_as_chunk.py tests/unit/test_chunk_size_config.py \
  tests/unit/test_chunk_prefix_fields_unit.py tests/unit/test_body_fields_unit.py \
  tests/unit/test_custom_fields_routing.py \
  -q -p no:randomly --color=no
examples/parse_chunk/parse_chunk_golden.py --check
```

뒤 3개는 검증에서 추가된 것이다.

- `test_chunk_prefix_fields_unit.py:86,92` 와 `test_body_fields_unit.py:38` 은
  **`chunking_processor.py` 를 텍스트로 읽어 문자열을 단정한다**(`reserved_keys = {` 블록).
  삭제 편집이 그 근처를 스치면 조용히 깨진다
- `test_custom_fields_routing.py` 는 `object.__new__` 로 `_chunk_parse_format` 를 직접 호출한다

chunking 프로세서에 대해 삭제 대상 메서드를 참조하는 테스트는 **0건**이다(parser 쪽에서 나온
문자열 patch 패턴도 chunking 에는 없다). 실제 안전망은 골든 대조다.

**`parse_chunk_golden.py` 는 아직 존재하지 않는다. 00 이 동작한 뒤에 착수한다.**

## 허용되는 관측 가능 변화

청커 기동 시 `DocumentConverter` 4개 생성이 사라진다(모델 로딩 경로 포함).
**산출물에는 나타나지 않아야 한다.** 골든 대조에서 차이가 나오면 회귀다.

초판은 "이것이 유일한 변화" 라고 단언했는데 **`page_batch_size` 전역이 두 번째 변화가 될 뻔했다.**
527행을 남기기로 해서 다시 하나가 됐다.

## 예상 결과

**1,536 → 약 1,220줄** (초판 "~1,180" 은 낙관이었다).

구간 합산: 25-43(19) + 205-218(14) + 407-410(4) + 460-475(16) + 478-487(10) +
507-554(48, 527 제외) + 555-589(35) + 594-630(37) + 631-679(49) + 727-761(35) +
919-926(8) + 930-935(6) + 939-947(9) + import 정리(약 22) = **약 312줄**.

위 "함께 걷어낼 죽은 코드" 까지 지우면 1,200 근처가 된다.
