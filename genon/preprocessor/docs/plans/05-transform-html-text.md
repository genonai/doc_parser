# 05. `from`/`as` 를 없애고 `transform` 으로 통합

전제: [README.md](README.md) 의 "공통 규칙" 을 먼저 읽는다.
**이 작업만 시한이 있다 — v2 를 출고 설정으로 옮기기 전에 끝내야 한다.**

## 보고된 요구

> json mapping: transform 에 html 파싱 기능 추가 검토. 현재 from-as 를 사용하고 있지만
> 직접 변환시키는 것이 더 명확한 것 같음.
> 새로운 항목을 만들지 않고 `DETAIL_DESC: {transform: html_text}` 로 사용하고 싶다.

## 현재 동작

`from`/`as` 는 **항상 파생 필드를 새로 만든다.** 제자리 변환이 불가능하다.

```python
# facade/enrichment/config_v2.py
_AS_TO_BLOCK = {"auto": "text_from", "html": "html_text_fields"}
```

```python
# facade/enrichment/tabular_custom_fields.py:357
def apply_text_from(fields, specs, html_renderer=None):
    for target, source, forced in specs:
        fields[target] = render_field_text(fields.get(source), kind=forced, html_renderer=html_renderer)
```

`source` 는 **목표필드명**이다. 그래서 평문 필드 하나를 만들려면 원본 HTML 을 반드시
목표필드로 선언해야 하고, 그게 그대로 적재 컬럼이 된다.

출고 설정 3개가 쓰고 있다.

| 설정 | 원본(metadata 전용) | 파생(body) | `as` |
|---|---|---|---|
| `custom_field_stock_insight.yaml` | `DETAIL_DESC` (TB 정의상 원천 JSON 원본 보관) | `DETAIL_TEXT` | `auto` |
| `custom_field_monimo_event.yaml` | `DETAIL_HTML` | `DETAIL_TEXT` | `html` |
| `custom_field_monimo_news.yaml` | `DETAIL_HTML` | `DETAIL_TEXT` | `html` |

셋 다 원본과 평문 사본을 **동시에** 유지한다. 이 요구를 잃으면 안 된다.

## 바꿀 것

`from` 과 `as` 를 필드 스펙에서 **삭제**하고 `transform` 하나로 접는다.
"원본도 함께 남기는" 경우는 **같은 alias 를 두 필드에 붙여** 표현한다.

```yaml
# 전
DETAIL_HTML: {alias: [cmp_desc, htmlText, 상세내용]}
DETAIL_TEXT: {from: DETAIL_HTML, as: html}

# 후 — 원본도 남기는 경우
DETAIL_HTML: {alias: [cmp_desc, htmlText, 상세내용]}
DETAIL_TEXT: {alias: [cmp_desc, htmlText, 상세내용], transform: html_text}

# 후 — 원본이 필요 없는 경우 (지금은 표현 불가능한 것)
DETAIL_DESC: {alias: [cmp_desc, htmlText], transform: html_text}
```

중복 alias 가 `from` 보다 넓다. `from` 은 목표필드만 참조할 수 있어서 원본 컬럼 생성을
강제하지만, 중복 alias 는 원본 컬럼 없이도 평문만 뽑을 수 있고 파이프라인 순서 의존
(`text_from` 이 맨 뒤에 도는 이유)도 사라진다.

### 새 변환기 2개

| 이름 | 뜻 | 구 표기 |
|---|---|---|
| `html_text` | HTML 로 강제 평문화 | `as: html` |
| `text` | 종류 자동 판별(JSON / HTML / 평문) | `as: auto` |

`stock_insight` 는 같은 컬럼에 JSON·HTML·평문이 섞여 오므로 `text`(자동 판별)가 필요하다.
둘 다 있어야 한다.

### 구현 순서

1. `field_transforms.py` 에 `html_text` / `text` 를 등록한다. `render_field_text` 를 그대로 쓴다.
2. **`apply_transforms` 가 `html_renderer` 를 받도록 시그니처를 넓힌다.**
   `html_to_text` 는 `table_format`/`compact_tables` 런타임 옵션이 필요한데 지금은
   `apply_text_from` 으로만 주입된다.
   호출부는 2곳이다 — `json_records.py:645`, `tabular_custom_fields.py:1106`
   (+ `tests/unit/test_custom_fields_routing.py`). 기본값을 둬서 기존 호출을 깨지 않는다.
3. `config_v2` 에서 `from`/`as` 를 `FIELD_SPEC_KEYS` 에서 빼고, v1 의
   `text_from`/`html_text_fields` 를 **중복 alias + transform** 으로 번역해 흡수한다
   (원천 alias 는 v1 `column_map`/`key_map` 에서 가져온다).
4. `text_from`/`html_text_fields` 내부 블록과 `compile_text_from`/`apply_text_from` 을 제거한다.
   `collect_target_field_names`(tabular_custom_fields.py:390)의 참조도 함께 정리한다.
5. 출고 설정 3개 + `resource_dev/` 사본을 새 표기로 옮긴다.
6. 템플릿 `custom_field_TEMPLATE_json.yaml`, `custom_field_TEMPLATE_tabular.yaml` 의
   `from`/`as` 설명을 `transform` 으로 고친다.

## 바꾸지 말 것

- `transform` 체이닝 문법(`[{name: regex_sub, pattern: …}, {name: to_int}]`)을 바꾸지 않는다.
- 값 파이프라인 순서(`default -> 원천값 -> values -> transform -> template -> const`)를 바꾸지 않는다.
- `row_merge`(병합이 값 파이프라인보다 먼저)를 건드리지 않는다. `stock_insight` 는
  `merge_rows.concat: [DETAIL_DESC]` 로 조각을 이어붙인 뒤 변환하므로, 중복 alias 로 만든
  `DETAIL_TEXT` 도 `concat` 목록에 들어가야 같은 결과가 된다. **이 지점이 이 작업에서 가장
  틀리기 쉬운 곳이다.**
- 03 에서 sections 에 값 파이프라인이 열렸더라도, 여기서 sections 용 배선을 추가하지 않는다.

### 알고 있는 손실 하나

`from` 은 `template`(derive)이 만든 파생 필드를 다시 참조할 수 있는데, 중복 alias 는 원천 키만
가리킨다. 저장소 안팎 어느 설정도 그렇게 쓰지 않는다. 필요해지면 `template` 을 한 번 더 쓴다.
이 사실을 커밋 메시지에 남긴다.

## 영향 파일

- `facade/enrichment/field_transforms.py` (`PARAM_TRANSFORMS` / `VALUE_TRANSFORMS` 등록)
- `facade/enrichment/tabular_custom_fields.py` (`apply_transforms` 593, `compile/apply_text_from`
  335~365, `collect_target_field_names` 390, 호출부 1106~1111)
- `facade/enrichment/json_records.py` (호출부 645~652, `__init__` 517~522)
- `facade/enrichment/config_v2.py` (`FIELD_SPEC_KEYS`, `_AS_TO_BLOCK`, `to_v2`, `COVERED_V1_KEYS`)
- `facade/enrichment/config_schema.py` (`_RECORD_COMMON` 에서 두 블록 제거)
- `resource/custom_field_{stock_insight,monimo_event,monimo_news}.yaml` + `resource_dev/` 사본
- `resource/templates/custom_field_TEMPLATE_{json,tabular}.yaml`

## 검증 — 여기만 방식이 다르다

### 테스트 데이터 (실제 문서로 검증한다)

`from`/`as` 를 실제로 쓰는 출고 설정 3개의 원천이 전부 저장소에 있다. **새로 만들 것은 없다.**

| doc_type | 파일 | 무엇을 보는가 |
|---|---|---|
| `stock_insight` | `genon/preprocessor/sample_files/monimo/monimo_stock_insight_sample.xlsx` | `as: auto` 자동판별 + `row_merge` 상호작용 |
| `monimo_event` | `genon/preprocessor/sample_files/json/monimo_event_sample.json` | 협의용 한글 키 표기 |
| `monimo_event` | `genon/preprocessor/sample_files/monimo/monimo_event_real_sample.json` | 실 payload 스키마 |
| `monimo_event` | `genon/preprocessor/sample_files/monimo/monimo_event_table_sample.json` | 5열 표 빈 셀 보존 |
| `monimo_news` | `genon/preprocessor/sample_files/monimo/monimo_news_sample.json` | `as: html` |

`stock_insight` 샘플은 손으로 만든 것이 아니라
`examples/parse_chunk/make_stock_insight_sample.py` 가 생성한다. 원천 특징을 바꿔 확인하고
싶으면 엑셀을 열지 말고 **그 스크립트를 고쳐 다시 돌린다.**

`row_merge` 와의 상호작용이 이 작업에서 가장 틀리기 쉬운 곳이므로,
`stock_insight` 는 dict 단정이 아니라 **실제 xlsx 를 돌려** 확인한다.

**`verify_v2_equivalence.sh` 의 키 단위 왕복 대조로는 검증할 수 없다.** 내부 표현에서
`text_from`/`html_text_fields` 블록이 사라지므로 "v2 는 표기만 다르다" 불변식이 깨진다.
이 작업은 **출력 단위**로 대조한다.

```bash
# 변경 전 — 기준선 (같은 버전 2회로 노이즈 기준선도 함께 잡는다)
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only stock_insight monimo_event monimo_news

# 변경 후 — 청크 본문이 같아야 한다
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only stock_insight monimo_event monimo_news
```

케이스는 `monimo_event` 3건(협의용 한글 키 / 실 payload / 5열 표 빈 셀), `monimo_news` 1건,
`stock_insight` 1건(row_merge 추가 단정 `check_stock_insight_row_merge` 포함)이다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'transform or text_from or custom_fields_routing or config_v2 or json_records or tabular'
```

## 재색인

정확히 옮기면 청크는 **동일**해야 한다. 달라지면 번역이 틀린 것이다.
특히 `stock_insight` 의 `as: auto` -> `transform: text` 는 자동 판별 경로를 그대로 타야 한다.
