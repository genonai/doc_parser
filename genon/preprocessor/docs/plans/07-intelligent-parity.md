# 07. intelligent 가 parser 의 custom_fields yaml 을 다 처리하게 — 분석과 로드맵

전제: [README.md](README.md) 의 "공통 규칙" 을 먼저 읽는다.
**이 파일은 분석과 단계 정의다. 구현은 08 / 09 / 10 에 있다.**

## 보고된 요구

> `resource/templates` 의 주석 설명 중 parser processor 에서만 적용된다고 기술된 부분이 있어.
> intelligent 에서도 parser processor 에서 사용하는 yaml 의 내용을 모두 다 처리할 수 있어야 해.

## 지목된 주석

```yaml
# custom_field_TEMPLATE_json.yaml:157
# **parser 경로에서만 실행된다.** kind: records 자체가 parser 의 .json 분기 전용이므로
#    적재용(intelligent)/변환용(convert) 설정에 넣으면 **경고도 없이 아무 일도 일어나지 않는다.**

# custom_field_TEMPLATE_tabular.yaml:170
# **parser 경로에서만 실행된다.** 적재용(intelligent)/변환용(convert) 경로는 동기 처리라
#    LLM 호출 자리가 없어 기동 시 경고만 남기고 이 필드는 비어서 나온다.
```

## 실측한 격차

배선을 전부 대조한 결과다. 주석이 말하는 것보다 넓다.

| yaml 기능 | parser | intelligent | convert |
|---|:-:|:-:|:-:|
| `kind: document` (extractor `llm`) | O | **O** | O |
| `kind: rows` (`tabular_mapping`) | O | O (xlsx/csv 만) | O (xlsx/csv 만) |
| `kind: rows` 의 `llm:` (`llm_fields`) | O | **X** (경고만) | **X** (경고만) |
| `kind: records` (`json_mapping`) | O | **X** (무음) | **X** (무음) |
| `kind: sections` (`json_semantic`) | O | **X** (무음) | **X** (무음) |
| `source.pre.markdown.front_matter` | O | **X** (무음) | **X** (무음) |
| `source.pre.markdown.text_fence` | O | **X** (무음) | **X** (무음) |
| `source.pre.html.marker_headings` | O | **X** (무음) | **X** (무음) |
| `json:` 블록 (`JsonTextSpec`) | O | **X** (무음) | **X** (무음) |
| `formats.html.flatten` | O | **X** | **X** |

근거 — intelligent 는 custom_fields 관련으로 **두 가지만** 배선한다.

```python
# intelligent_processor.py:636
self.custom_fields_enrichers = _build_document_custom_fields_enrichers(...)   # kind: document
# intelligent_processor.py:641
self._tabular_custom_fields_mappers = _build_tabular_custom_fields_mappers(...)  # kind: rows
_warn_tabular_llm_fields_unsupported(self._tabular_custom_fields_mappers, "intelligent")
```

`json_records` / `json_semantic` / `markdown_front_matter` / `json_text` 를 **import 조차 하지 않는다.**

## 근본 원인 — 입력 라우팅 모델이 다르다

배선 누락이 아니다. 두 프로세서는 입력을 다루는 방식 자체가 다르다.

```python
# intelligent_processor.py:1418  __call__
if ext not in _XLSX_DIRECT_EXTS and kwargs.get('auto_convert_to_pdf', True) and not _is_pdf(file_path):
    file_path, converted_pdf_path = self._convert_to_pdf(file_path, **kwargs)
```

**intelligent 는 xlsx/csv 와 PDF 를 뺀 전부를 PDF 로 변환한다.** `.json` 이 들어오면 PDF 가 되므로
`kind: records`/`sections` 가 필요로 하는 **원천 payload 가 그 시점에 이미 없다.**
`.md` 의 front matter 도, `.html` 의 마커 구조도 마찬가지다.

parser 는 반대다 — 확장자별 네이티브 경로로 먼저 보내고 PDF 변환은 폴백이다
(`parser_processor.py` 의 `.hwp`/`.ppt`/`.json`/`.md`/`.html` 분기).

그래서 "intelligent 에 배선만 추가" 로는 끝나지 않는다. **라우팅을 먼저 고쳐야 나머지가 가능해진다.**

## 주석에 사실과 다른 부분

`custom_field_TEMPLATE_tabular.yaml:170` 의 "적재용/변환용 경로는 동기 처리라 LLM 호출 자리가
없어" 는 정확하지 않다. **`_process_xlsx` 는 양쪽 다 `async def` 다**
(`intelligent_processor.py:1102`, `convert_processor.py:1319`). 동기인 것은 그 안에서 부르는
`build_tabular_custom_fields_vectors` 하나뿐이고, 매퍼에는 이미 쪼갠 API 가 있다
(`build_fields` → `to_parse_format_from_fields`). 즉 **자리는 있다.**

이 오해가 격차를 실제보다 크게 보이게 만들었다. 08 이 가장 먼저인 이유다.

## 로드맵

작고 독립적인 것부터 간다. 각 단계는 따로 배포할 수 있다.

| 단계 | 파일 | 내용 | 위험 |
|---|---|---|---|
| 0 | 이 파일 (아래) | 무음 실패를 경고로 드러내고 템플릿 주석을 사실에 맞춘다 | 낮음 |
| 1 | [08-intelligent-tabular-llm-fields.md](08-intelligent-tabular-llm-fields.md) | `kind: rows` 의 `llm:` 을 intelligent/convert 에서 실행 | 낮음 |
| 2 | [09-intelligent-format-routing.md](09-intelligent-format-routing.md) | 원천 포맷을 PDF 로 바꾸지 않고 받는 경로 | **높음** |
| 3 | [10-intelligent-record-kinds.md](10-intelligent-record-kinds.md) | `records`/`sections`/`source.pre` 배선 | 중간 |

2단계 없이 3단계를 할 수 없다. 1단계는 2·3과 독립이다.

## 0단계 — 무음 실패를 없앤다 (이 파일에서 함께 처리)

가장 나쁜 것은 "경고도 없이 아무 일도 일어나지 않는" 상태다. 설정은 요약본문을 약속했는데
결과에 없고, 그 사실을 데이터에서 나중에 발견한다. 기능 구현 전에 **드러내는 것**부터 한다.

1. `warn_tabular_llm_fields_unsupported` 를 일반화한다. 지금은 tabular 의 `llm_fields` 하나만
   본다. 이 프로세서가 **읽지 않는 extractor 로 등록된 custom_fields 항목 전체**를 기동 시
   경고하도록 넓힌다.
   - 판정은 `config_schema.canonical_extractor` 와 "이 프로세서가 지원하는 extractor 집합" 으로 한다.
   - 새 함수는 `facade/enrichment/` 에 둔다. facade 3곳이 각 1줄로 호출한다.
2. `source.pre` / `json:` 블록이 있는데 그 프로세서가 소비하지 않으면 같은 방식으로 경고한다.
3. 템플릿 4개의 주석을 실측에 맞춘다.
   - "동기 처리라 LLM 호출 자리가 없어" → 사실이 아니므로 지운다(08 에서 실제로 지원한다).
   - `kind: records`/`sections` 는 "현재 parser 경로에서만 동작한다" 로 두되,
     **경고가 나온다**는 사실을 함께 적는다.
   - 지원 현황은 이 파일의 격차 표를 단일 출처로 삼고 템플릿에서는 요약만 적는다
     (네 파일에 표를 복제하면 다음 단계마다 네 곳을 고쳐야 한다).

### 0단계 바꾸지 말 것

- **동작을 바꾸지 않는다.** 경고와 문서만 손댄다. 지금 돌아가는 설정이 기동에 실패하면 안 된다
  (경고이지 오류가 아니다).
- `GENOS_CUSTOM_FIELDS_VALIDATION` 정책과 섞지 않는다. 그것은 "쓸 수 없는 키" 판정이고,
  이건 "쓸 수 있지만 이 프로세서가 안 읽는다" 판정이다.

### 0단계 영향 파일

- `facade/enrichment/tabular_custom_fields.py` (`warn_tabular_llm_fields_unsupported` 일반화)
  또는 `facade/enrichment/config_schema.py` (판정을 여기 두는 편이 자연스러우면)
- `facade/intelligent_processor.py:644`, `convert_processor.py:615`, `chunking_processor.py` (각 1줄)
- `resource/templates/custom_field_TEMPLATE_{json,tabular,semantic,llm}.yaml`

### 0단계 검증

#### 테스트 데이터 (실제 문서로 검증한다)

0단계는 경고와 문서만 바꾸지만, **"동작이 안 바뀐다"를 실제 문서로 확인해야** 한다.
경고 추가가 기동을 막거나 정상 설정을 걸러 버리는 사고가 이 종류에서 가장 흔하다.

| 용도 | 파일 |
|---|---|
| intelligent 가 지금 처리하는 rows | `genon/preprocessor/sample_files/monimo/monimo_faq_sample.xlsx` |
| intelligent 가 지금 처리하는 document | `genon/preprocessor/sample_files/monimo/monimo_cs_hpp_sample.html` |
| intelligent 가 무시하는 records (경고가 떠야 함) | `genon/preprocessor/sample_files/monimo/monimo_faq_json_sample.json` |
| intelligent 가 무시하는 sections (경고가 떠야 함) | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_sample.json` |

앞 2건은 **경고 없이 종전과 같은 산출**, 뒤 2건은 **기동 시 경고 + 종전과 같은 산출**이어야 한다.
새로 만들 샘플은 없다.


```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no -k 'custom_fields or routing'
```

경고 문구 자체를 단정하는 테스트를 넣는다(무음 회귀 방지가 목적이므로 문구가 아니라
**경고가 났다는 사실**과 대상 doc_type 을 단정한다).

## convert 는 어떻게 하나

convert 는 intelligent 와 같은 모양이다(같은 두 줄, 같은 PDF 우선 라우팅, `_process_xlsx` 도 async).
**08 은 convert 도 함께 고친다** — 코드가 동일해 따로 하면 두 번 짠다.
09·10 은 intelligent 만 대상으로 하고, convert 는 그 결과를 보고 별도로 판단한다
(요구가 intelligent 에 대해 나왔고, 라우팅 변경의 위험이 크다).
