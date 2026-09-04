# 04. document 의 `fields` 에 `alias` + 값 파이프라인 개방

전제: [README.md](README.md) 의 "공통 규칙" 을 먼저 읽는다.

## 보고된 요구

> document 타입: 프론트메타에서 추출한 항목에 대해서 transform 기능 필요.
> field 선언 섹션에서 지정하는 것을 검토. 또는 더 좋은 방향을 제시해줘.
>
> document 타입은 왜 transform 이 없어? fields 의 기능을 최대한 기능차이 없게 검토해줘.

두 항목이 같은 뿌리라 하나로 묶는다.

## 현재 동작

```python
# facade/enrichment/config_v2.py:69
DOCUMENT_FIELD_SPEC_KEYS = frozenset({"const", "default"})
```

근거로 적힌 이유는 "이 kind 는 원천을 고르고 값을 접는 단계가 없다(LLM 응답이 유일한 원천)"
인데, **그 전제가 틀렸다.** front matter 가 두 번째 원천이고 우선순위까지 이미 구현돼 있다.

```python
# facade/enrichment/custom_fields_enricher.py:605~629
# 우선순위: default < LLM < front matter < const
```

그리고 front matter 의 `metadata_fields` 는 **이름만 다른 alias 맵**이다.

```python
# facade/enrichment/markdown_front_matter.py:145
"""`metadata_fields` -> `{원천키: [목표필드…]}`."""
```

같은 개념이 두 자리(`source.pre.markdown.front_matter.metadata_fields` 와 `fields`)에 있고,
그래서 한 필드의 규칙이 흩어진다 — v2 가 없애려던 바로 그 문제다.

## 바꿀 것

`fields:` 를 document kind 의 **단일 필드 선언 자리**로 만든다.

```yaml
# 전
source:
  pre:
    markdown:
      front_matter:
        metadata_fields:
          author: AUTHOR
          created_at: created_date
        exclude_text_fields: ["*"]
fields:
  GROUP_C: {const: HPP}

# 후
source:
  pre:
    markdown:
      front_matter:
        exclude_text_fields: ["*"]     # 본문에 남길지 — 텍스트 정책이라 여기 남는다
fields:
  AUTHOR:       {alias: [author]}
  created_date: {alias: [created_at], transform: date_int_flex}
  STATUS:       {alias: [state], values: {draft: "N"}}
  DISPLAY_NM:   {template: "{{TITLE}} ({{AUTHOR}})"}
  GROUP_C:      {const: HPP}
```

1. `DOCUMENT_FIELD_SPEC_KEYS` 상수를 **제거**한다. document 도 다른 kind 와 같은
   `FIELD_SPEC_KEYS` 를 쓴다(`collect` 제외).
2. `alias` 를 front matter 키 조회로 배선한다. `_ALIAS_BLOCK` 에 document 항목이 없으므로
   새 v1 블록 이름이 하나 필요하다(예: `front_matter_map`). `metadata_fields` 는
   `normalize()` 에서 이 블록으로 번역해 하위호환을 유지한다.
3. 값 병합(605~629행) 뒤에 `apply_value_map -> apply_transforms -> apply_derive` 를 끼운다.
   `const` 는 그 **뒤**에 와야 한다(네 kind 공통 계약).
4. `config_schema.EXTRACTOR_KEYS["llm"]` 에 `value_map`, `transforms`, `derive`,
   새 alias 블록을 추가한다.
5. `_RESERVED_TARGETS`(markdown_front_matter.py:64) 검사가 `fields` 경로에서도 걸리게 한다.
   `created_date` 가 일부러 빠져 있는 이유(청커 field_transform 이 소비)를 그대로 유지한다.
6. `resource/templates/custom_field_TEMPLATE_llm.yaml` 과
   `resource/custom_field_product_slf.yaml` / `product_ssf.yaml`(+ `resource_dev/` 사본)을 옮긴다.

### 배선 주의 — alias 를 spec 까지 실어 보내는 곳

front matter spec 은 등록 블록만 받고 자식 yaml 의 필드 선언을 못 본다. 확인한 경로는 이렇다.

```python
# markdown_front_matter.py  resolve_format_cfg()
child_cfg, _extractor = cv2.load(child_cfg, label=...)   # v2 정규화는 이미 여기서 일어난다
child_block = child_cfg.get(block)                        # 그런데 markdown 블록 하나만 꺼내고 버린다

# markdown_front_matter.py:478  build_markdown_front_matter_specs()
effective_config = dict(config)          # config = 등록 블록(enable/doc_type/config_file …)
effective_config["markdown"] = markdown_cfg
specs.append(MarkdownFrontMatterSpec.from_config(effective_config))
```

좋은 소식은 **정규화가 이미 일어난다는 것**이다(`cv2.load` 호출이 있다). 그래서 새 alias 블록은
`child_cfg` 안에 이미 들어 있고, 그것을 버리지 않고 `effective_config` 에 함께 실으면 된다.
`resolve_format_cfg` 의 반환을 바꾸면 `html` 블록 경로(`resolve_html_cfg`)까지 영향을 받으므로,
**반환 계약을 바꾸지 말고** 빌더 쪽에서 자식 config 를 한 번 더 얻는 편이 좁다
(CLAUDE.md 기준 3 — 고칠 자리는 쓰는 쪽).

## 바꾸지 말 것

- **`exclude_text_fields` 는 `front_matter` 에 남긴다.** "값을 어디서 가져오나" 가 아니라
  "본문에 남길까" 라 자리가 다르다.
- **우선순위 `default < LLM < front matter < const` 를 바꾸지 않는다.** 값 파이프라인은
  이 병합이 끝난 뒤에 도는 후처리다.
- `llm:` 블록(엔드포인트·프롬프트·`out`/`in`)은 이 작업과 무관하다.
- `collect` 는 열지 않는다.
- HTML front matter 같은 새 원천을 만들지 않는다.

## 영향 파일

- `facade/enrichment/config_v2.py` (`DOCUMENT_FIELD_SPEC_KEYS` 제거, `_ALIAS_BLOCK`,
  `metadata_fields` 번역, `COVERED_V1_KEYS`)
- `facade/enrichment/custom_fields_enricher.py` (`__init__` 269~343, 값 병합 605~641)
- `facade/enrichment/markdown_front_matter.py` (`metadata_fields` 소비부 145~184, 340~370)
- `facade/enrichment/config_schema.py` (`EXTRACTOR_KEYS["llm"]`)
- `resource/custom_field_product_slf.yaml`, `product_ssf.yaml` + `resource_dev/` 사본
- `resource/templates/custom_field_TEMPLATE_llm.yaml`

## 검증

### 테스트 데이터 (실제 문서로 검증한다)

| 용도 | 파일 | 상태 |
|---|---|---|
| md front matter (핵심) | `genon/preprocessor/sample_files/monimo/monimo_product_slf_sample.md` | 있음 |
| md front matter | `genon/preprocessor/sample_files/monimo/monimo_product_ssf_sample.md` | 있음 |
| front matter + text_fence 동시 | `genon/preprocessor/sample_files/monimo/monimo_product_slf_fence_sample.md` | 있음 |
| front matter **없는** 순수 LLM 경로 | `genon/preprocessor/sample_files/monimo/monimo_cs_hpp_sample.html` | 있음 |
| 카드 12필드(LLM) | `genon/preprocessor/sample_files/json/monimo_card_sample.json` | 있음 |
| `values`/`template` 을 태울 front matter | `genon/preprocessor/sample_files/monimo/monimo_product_slf_fields_sample.md` | **만들어야 함** |

마지막 1건은 만든다 — 기존 front matter 7줄(file/document_type/source_file/source_pages/
author/created_at/conversion_note)에는 접을 코드값이나 결합할 필드가 없다.
`examples/parse_chunk/make_product_slf_fields_sample.py` 로 생성하고 `CASES` 에 등록한다.

**front matter 가 없는 2건(`cs_hpp`, `card`)이 깨지지 않는 것**이 핵심 회귀 조건이다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'front_matter or custom_fields or config_v2'
```

```bash
genon/preprocessor/examples/config_precheck/verify_v2_equivalence.sh
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only product_slf product_ssf cs_hpp card
```

`product_slf`/`product_ssf` 가 md front matter 경로이고, `cs_hpp`/`card` 는 front matter 없는
순수 LLM 경로다. **후자가 깨지지 않는 것**이 이 작업의 핵심 회귀 조건이다 —
front matter 가 없어도 `fields.alias` 선언이 에러 없이 빈 값으로 지나가야 한다.

`created_date` 는 청커가 YYYYMMDD 정수로 바꿔 벡터 필드에 싣는다. `transform: date_int_flex`
를 yaml 에 적으면 **이중 변환**이 될 수 있으니, 실제 벡터 값이 그대로인지 확인한다.

## 재색인

`metadata_fields` 를 `fields.alias` 로 옮기기만 하면 값은 동일하다.
`exclude_text_fields` 를 건드리지 않으면 청크 본문도 동일하다.
