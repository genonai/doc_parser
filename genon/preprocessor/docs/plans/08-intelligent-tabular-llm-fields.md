# 08. `kind: rows` 의 `llm:` 을 intelligent / convert 에서 실행

전제: [README.md](README.md) 와 [07-intelligent-parity.md](07-intelligent-parity.md) 를 먼저 읽는다.
07 로드맵의 **1단계**. 2·3단계와 독립이라 먼저 해도 되고 따로 배포해도 된다.

## 보고된 요구

07 의 격차 표에서 `kind: rows` 의 `llm:` 행. 지금은 기동 시 경고만 남고 필드가 비어서 나온다.

## 현재 동작 — 막고 있는 것은 함수 하나뿐이다

템플릿 주석은 "적재용/변환용 경로는 동기 처리라 LLM 호출 자리가 없어" 라고 적고 있지만
**사실이 아니다.** 호출부는 양쪽 다 async 다.

```python
# intelligent_processor.py:1102 / convert_processor.py:1319
async def _process_xlsx(self, request, file_path, **kwargs):
    ...
    vectors = build_tabular_custom_fields_vectors(file_path, matching_mappers[0], ...)  # 이것만 동기
```

동기인 것은 `converters/xlsx_processor.py:626` 하나이고, 그 안에서 매퍼를 한 번에 부른다.

```python
# converters/xlsx_processor.py:648
result = mapper.to_parse_format(data_dict, runtime_doc_type)   # build_fields 를 안에서 한다
```

parser 는 같은 매퍼를 **두 단계로** 쓴다. 그 사이가 LLM 자리다.

```python
# parser_processor.py:1688~1697  _parse_tabular_records
fields_list = mapper.build_fields(data_dict, runtime_doc_type, ...)
fields_list = await self._apply_llm_fields(mapper, fields_list)      # <- 이 줄
results.append(mapper.to_parse_format_from_fields(fields_list, runtime_doc_type))
```

**필요한 API 는 이미 있다**(`build_fields`, `to_parse_format_from_fields`).
막고 있는 것은 `build_tabular_custom_fields_vectors` 가 그 둘을 한 덩어리로 묶어 놓은 것뿐이다.

## 바꿀 것

### 1) `_apply_llm_fields` 를 공용 모듈로 뺀다

지금은 `parser_processor.py:1546` 의 메서드다. intelligent 에 복사하면
"facade 끼리 import 금지" 를 우회한 복제가 되고 그 자체가 새 lockstep 부채다
(CLAUDE.md 아키텍처 제약). `facade/enrichment/` 에 순수 함수로 옮긴다.

함께 옮길 것: `_apply_llm_fields_document_scope`(1607), `_llm_field_enricher`(1520,
enricher 캐시). 캐시는 프로세서 인스턴스에 붙어 있으므로 **캐시 dict 를 인자로 받는 형태**로
바꾼다(공용 모듈이 프로세서 속성을 알면 안 된다).

`getattr(..., 기본값)` 으로 속성 부재를 견뎌야 한다 — `object.__new__` 로 `__init__` 을
우회해 만든 인스턴스를 쓰는 단위 테스트가 있다.

### 2) `build_tabular_custom_fields_vectors` 에서 elements 조립을 분리한다

`converters/` 는 `facade/` 를 import 할 수 없다(단방향). 그래서 async LLM 적용은 facade 에서
하고, converter 에는 **이미 만들어진 elements 를 받는 입구**를 연다.
`expand_elements` / `text_fields_hook` 을 함수로 주입받는 기존 선례와 같은 방식이다.

```python
# 예 — 이름은 구현 시 정한다
def build_tabular_custom_fields_vectors(file_path, mapper, runtime_doc_type, *, elements=None, ...):
    if elements is None:                       # 기존 호출부는 그대로 동작한다
        data_dict = build_tabular_data_dict(...)
        elements = mapper.to_parse_format(data_dict, runtime_doc_type).get("elements")
    ...
```

### 3) intelligent / convert 의 `_process_xlsx` 를 세 단계로 바꾼다

```python
data_dict   = build_tabular_data_dict(file_path, header_row=..., multi_table=...)
fields_list = mapper.build_fields(data_dict, runtime_doc_type, table_format=..., compact_tables=...)
fields_list = await apply_llm_fields(mapper, fields_list, cache=self._llm_field_enrichers)
elements    = mapper.to_parse_format_from_fields(fields_list, runtime_doc_type)["elements"]
vectors     = build_tabular_custom_fields_vectors(..., elements=elements, ...)
```

### 4) 기동 시 enricher 를 미리 만든다

parser 는 `__init__`(1026~1029)에서 `llm_field_specs` 마다 enricher 를 만들어
**설정·프롬프트 파일 오류가 첫 요청이 아니라 기동 시** 드러나게 한다. intelligent/convert 도 같게 한다.

### 5) 경고를 걷어낸다

`warn_tabular_llm_fields_unsupported` 호출을 intelligent/convert 에서 제거한다.
07 의 0단계로 일반화된 경고가 있다면 rows 는 그 대상에서 뺀다.

## 바꾸지 말 것

- **`_process_xlsx` 의 나머지 경로(`build_tabular_vectors`, docling 모드)를 건드리지 않는다.**
  custom_fields 매퍼가 매칭될 때만 새 경로다.
- `build_tabular_custom_fields_vectors` 의 **기존 시그니처와 기본 동작을 유지**한다.
  `elements` 를 안 주면 지금과 똑같이 동작해야 한다. 호출부를 세면 intelligent 1곳,
  convert 1곳이고 테스트가 더 있다 — 계약을 바꾸면 전부 영향권이다(CLAUDE.md 기준 3).
- `kind: records`/`sections` 를 여기서 배선하지 않는다. 그것은 09·10 이다.
- guardrail 미적용(#315)·인덱스 계산 규칙을 건드리지 않는다.

## 영향 파일

- `facade/enrichment/` 에 새 모듈(또는 기존 `custom_fields_enricher.py` 에 함수 추가)
- `facade/parser_processor.py` (1520~1640 을 공용 함수 호출로 축소)
- `facade/intelligent_processor.py` (`__init__` 641, `_process_xlsx` 1102~)
- `facade/convert_processor.py` (`__init__` 612, `_process_xlsx` 1319~)
- `converters/xlsx_processor.py` (626 `elements` 입구)
- `resource/templates/custom_field_TEMPLATE_tabular.yaml` (주석 정정)

## 검증

### 테스트 데이터 (실제 문서로 검증한다)

`kind: rows` 원천이 전부 저장소에 있다. **새로 만들 것은 없다.**

| doc_type | 파일 | 무엇을 보는가 |
|---|---|---|
| `stock_insight` | `genon/preprocessor/sample_files/monimo/monimo_stock_insight_sample.xlsx` | `llm:` 선언이 있는 유일한 rows 설정 |
| `faq` | `genon/preprocessor/sample_files/monimo/monimo_faq_sample.xlsx` | llm 없는 rows 가 안 깨지는지 |
| `menu` | `genon/preprocessor/sample_files/monimo/monimo_menu_sample.xlsx` | 행 1개 = 청크 1개 |
| `term` | `genon/preprocessor/sample_files/monimo/monimo_term_sample.xlsx` | 행 1개 = 청크 1개 |
| csv 경로 | `genon/preprocessor/sample_files/csv_sample.csv` | xlsx 아닌 rows 입력 |

**같은 xlsx 를 parser 와 intelligent 두 경로로 돌려 대조하는 것**이 이 작업의 판정이다.
`parse_chunk_verify.sh` 는 parser 만 돌리므로, intelligent 쪽은 게이트웨이 스크립트에
모드를 추가해 확인한다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'tabular or xlsx or llm_field or custom_fields'
```

**단위 테스트에서 실제 LLM 을 부르지 않는다.** CI 는 사내망에 접근하지 못한다.
기존 스텁 패턴을 따르고 더미 URL 을 쓴다. 실호출 검증은 smoke + skipif 로 둔다.

```bash
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only stock_insight faq menu term
```

`parse_chunk_verify` 는 parser 경로를 돈다. **intelligent 경로의 확인은 게이트웨이 스크립트에
모드를 추가**하는 편을 선호한다(새 pytest 를 만들기보다).

```bash
genon/preprocessor/examples/code_serving/serving_gateway_test.sh
```

핵심 회귀 조건: **parser 경로의 결과가 변하지 않을 것.** 공용 함수 추출이 이 작업의 절반이라
parser 가 먼저 깨진다.

## 재색인

parser 산출은 동일해야 한다. intelligent 산출은 **비어 있던 LLM 필드가 채워지므로** 달라진다 —
그게 이 작업의 목적이고, 해당 doc_type 은 재색인 대상이다.
