# 10. intelligent 에 `records` / `sections` / `source.pre` 배선

전제: [README.md](README.md), [07-intelligent-parity.md](07-intelligent-parity.md),
그리고 **[09-intelligent-format-routing.md](09-intelligent-format-routing.md) 가 끝나 있어야 한다.**
07 로드맵의 **3단계**.

## 남은 격차

09 가 원천 포맷을 살려 두면, 이제 실제로 소비할 배선이 필요하다.

| yaml 기능 | 대응 모듈 | parser 의 배선 지점 |
|---|---|---|
| `kind: records` | `enrichment/json_records.py` | `parser_processor.py:1023`, `_parse_json_records` 1640 |
| `kind: sections` | `enrichment/json_semantic.py` | `parser_processor.py:82` import, 매퍼 빌드 |
| `source.pre.markdown.front_matter` | `enrichment/markdown_front_matter.py` | `parser_processor.py:999` |
| `source.pre.markdown.text_fence` | `converters/md_text_fence.py` | `parser_processor.py:1006` |
| `source.pre.html.marker_headings` | `converters/` | `parser_processor.py:1013` |
| `json:` 블록 | `converters/json_text.py` | `parser_processor.py:995` |
| `formats.html.flatten` | `converters/html_flatten.py` | `parser_processor.py:991` |

intelligent 는 이 중 **하나도 import 하지 않는다.**

## 바꿀 것

### 원칙 — 배선을 복제하지 말고 공용 모듈로 뺀다

parser 의 `__init__` 995~1029 은 "custom_fields 설정 목록 → spec/매퍼 묶음" 을 만드는
연속된 블록이다. 이것을 intelligent 에 복사하면 두 facade 가 lockstep 이 되고,
설정 키를 하나 늘릴 때마다 두 곳을 고쳐야 한다.

`facade/enrichment/` 에 **묶음 빌더 하나**를 두고 두 facade 가 각 1줄로 호출한다.

```python
# 예 — 이름과 필드는 구현 시 정한다
specs = build_custom_fields_specs(ec.custom_fields_cfgs)
# specs.json_records_mappers / semantic_mappers / front_matter / text_fence /
# marker_heading_doc_types / json_text
```

parser 도 이 빌더를 쓰게 바꾼다. **parser 를 그대로 두고 intelligent 에만 새로 만들면
그것이 곧 두 번째 사본이다.**

### 소비 지점

빌드(무엇이 있는가)와 소비(언제 쓰는가)는 다르다. 소비는 각 facade 의 라우팅에 붙는다.

- `records`/`sections`: 09 가 만든 네이티브 `.json` 경로에서 매퍼를 돌려
  parse-format elements 를 만들고, 그것을 벡터로 만든다. **08 이 xlsx 에 대해 만든
  "elements → vectors" 입구를 그대로 쓴다** — 새 벡터 조립 코드를 만들지 않는다.
- `source.pre.markdown`/`html`, `json:` 블록: docling 변환 **직전**의 원천 전처리다.
  09 의 네이티브 경로에서 파일을 읽은 직후에 건다.
- `formats.html.flatten`: 같은 자리. 설정은 `formats.html.flatten` 이라 custom_fields 와
  무관하게 켜지므로, intelligent 의 `formats` 설정 해석에도 추가한다.

### 순서 제안

한 커밋에 다 넣지 않는다. 각각 독립적으로 검증할 수 있다.

1. 묶음 빌더 추출 + parser 를 그것으로 전환 (**동작 무변화**가 조건)
2. `records`
3. `sections`
4. `source.pre.markdown` (front_matter, text_fence)
5. `source.pre.html` + `formats.html.flatten`
6. `json:` 블록

1번이 끝난 시점에 parser 산출이 완전히 같아야 한다. 여기서 어긋나면 나머지를 진행하지 않는다.

## 바꾸지 말 것

- **매퍼·컨버터 자체를 고치지 않는다.** 이 작업은 배선이다. `json_records`/`json_semantic` 의
  동작이 부족해 보이면 별도 이슈다.
- parser 의 라우팅을 건드리지 않는다. 1번의 전환은 `__init__` 의 빌드 부분만이다.
- convert_processor 는 대상이 아니다.
- `kind: document`(이미 동작)와 `kind: rows`(08 에서 완료)를 다시 손대지 않는다.
- LLM 을 호출하는 새 자리를 만들지 않는다. `records` 의 `llm:` 은 08 이 뺀 공용
  `apply_llm_fields` 를 재사용한다.

## 영향 파일

- `facade/enrichment/` 에 묶음 빌더 (신규)
- `facade/parser_processor.py` (`__init__` 986~1029 을 호출 1줄로 축소)
- `facade/intelligent_processor.py` (`__init__` 636~644, 09 가 만든 네이티브 경로)
- `converters/` 는 읽기만 한다(단방향 유지 — `converters` 는 `facade` 를 import 하지 않는다)
- `resource/templates/custom_field_TEMPLATE_{json,semantic}.yaml` (지원 현황 주석)
- `resource/intelligent_processor_config.yaml` + `resource_dev/` 사본 (`formats.html.flatten` 등)

## 검증

### 테스트 데이터 (실제 문서로 검증한다)

단계마다 재료가 다르다. 전부 저장소에 있고 `CASES` 에 등록돼 있다.

| 단계 | 파일 |
|---|---|
| 1 빌더 추출(parser 무변화) | `parse_chunk_verify.sh` 전 케이스 23건 |
| 2 `records` | `genon/preprocessor/sample_files/monimo/monimo_faq_json_sample.json`, `monimo_cs_sss_sample.json`, `monimo_news_sample.json` |
| 3 `sections` | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_wcms_sample.json`, `monimo_product_hpp_sample.json` |
| 4 `source.pre.markdown` | `genon/preprocessor/sample_files/monimo/monimo_product_slf_sample.md`, `monimo_product_slf_fence_sample.md` |
| 5 `source.pre.html` + flatten | `genon/preprocessor/sample_files/monimo/monimo_cs_hpp_marker_sections_sample.html`, `monimo_cs_hpp_marker_real_dom_sample.html`, `monimo_cs_hpp_marker_nospace_sample.html` |
| 6 `json:` 블록 | `genon/preprocessor/sample_files/json/monimo_card_sample.json` |

**같은 파일을 parser 와 intelligent 두 경로로 돌려 청크 본문을 대조하는 것**이 목표이자 판정이다.
새로 만들 샘플은 없다 — 이 작업은 기능 추가가 아니라 배선이므로, parser 가 이미 쓰는 재료를
그대로 쓴다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'json_records or semantic or front_matter or json_text or flatten or intelligent'
```

단계 1(빌더 추출) 직후:

```bash
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh     # 전 케이스. parser 무변화 확인
```

단계 2~6 은 intelligent 경로라 `parse_chunk_verify` 가 잡지 못한다.
**게이트웨이 스크립트에 intelligent 모드를 추가**해 같은 샘플을 두 경로로 돌리고 대조한다.

```bash
genon/preprocessor/examples/code_serving/serving_gateway_test.sh
```

목표는 **같은 원천·같은 doc_type 이면 parser 와 intelligent 의 청크 본문이 같을 것**이다.
차이가 나면 어느 쪽이 옳은지부터 정하고 진행한다(둘 다 고치는 상황이 될 수 있다).

`parse_chunk_verify` 는 실제로 LLM 을 부른다. 비교 전에 같은 버전 2회로 노이즈 기준선을
먼저 잡는다.

## 재색인

새로 동작하기 시작한 doc_type 은 전부 재색인 대상이다. 지금은 값이 아예 없거나
PDF 변환된 텍스트가 실려 있으므로 "달라진다" 가 아니라 "처음 제대로 실린다" 에 가깝다.

## 위험

작업 범위가 넓다. 단계 1(빌더 추출)이 parser 를 건드리므로, **여기서 실패하면
지금 정상 동작하는 유일한 경로가 깨진다.** 단계 1만 따로 커밋하고 따로 검증한다.
