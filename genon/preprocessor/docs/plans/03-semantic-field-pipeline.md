# 03. sections 의 `fields` 에 값 파이프라인 개방

전제: [README.md](README.md) 의 "공통 규칙" 을 먼저 읽는다.

## 보고된 요구

> semantic 타입: fields 에 왜 values, transform, from/as, template 이 없어? 있어야 할 것 같아.
> 다른 타입의 fields 와 차이를 두지 않는 게 좋을 것 같은데.

## 현재 동작

`config_v2.FIELD_SPEC_KEYS` 에는 8개가 있지만 sections 는 `alias`/`const`/`default` 만 실제로
동작한다. 나머지를 적으면 v1 블록(`value_map`/`transforms`/`derive`)으로 번역된 뒤
`config_schema.EXTRACTOR_KEYS["json_semantic"]` 에 없어서 **기동이 실패**한다.

근거로 적힌 이유는 "이 kind 는 본문을 섹션 워커가 만들기 때문" 인데, 이는 **본문에 대한
이유이지 metadata 필드에 대한 이유가 아니다.** `shared_fields` 는 그대로 적재 DB 컬럼이 되므로
값 접기·변환·결합이 다른 kind 와 똑같이 필요하다. 지금은 semantic 만 못 해서 원천을 고치거나
LLM 에 맡기게 된다.

값 확정 지점은 이미 한 곳에 모여 있다.

```python
# facade/enrichment/json_semantic.py:711~724
identity = {target: None for target in self.shared_fields}
for target, aliases in self.shared_fields.items():
    value = _find_root_field(payload, aliases)          # 원천값
    ...
for key, value in self.defaults.items(): ...            # default
identity.update(self.constants)                          # const
```

여기 `const` **뒤**에 값 파이프라인이 빠져 있다.

계획 초안은 "`default` 와 `const` 사이" 라고 썼는데 **틀렸다.** rows/records 의 실제 순서는
`원천값 → default → const → values → transform → template` 다(`json_records.apply_value_pipeline`
과 `tabular_custom_fields` 3단계 모두 `fields.update(constants)` 뒤에 `apply_value_map` 을
부른다 — 실측). `const` 를 파이프라인 뒤로 옮기면 sections 만 다른 순서가 되어 이번 요구
("다른 타입의 fields 와 차이를 두지 않는다")를 정면으로 어긴다. 지금 코드의
`default → const` 를 그대로 두고 그 **뒤에** 세 줄을 붙이는 것이 맞다.

## 바꿀 것

`tabular_custom_fields` 의 순수 함수를 그대로 재사용한다. 새 로직을 쓰지 않는다.

```python
# __init__ 에서 컴파일 (기동 시 오류를 잡기 위해)
self.value_map  = compile_value_map(cfg.get("value_map"))
self.transforms = compile_transforms(cfg.get("transforms"), label=label)
self.derive     = compile_derive(cfg, label=label)

# identity 확정부: 원천값 -> default -> const 뒤에 이어 붙인다
#   (rows/records 와 같은 순서. 앞의 두 줄은 이미 있는 코드다)
identity.update(self.constants)                          # 기존
apply_value_map(identity, self.value_map)
apply_transforms(identity, self.transforms)
apply_derive(identity, self.derive)
```

1. 위 4개 블록을 `config_schema.EXTRACTOR_KEYS["json_semantic"]` 에 추가한다
   (`value_map`, `transforms`, `derive`).
2. `config_v2` 는 **고칠 것이 없다.** `_normalize_fields` 가 kind 를 보고 막는 것은
   `document`(`DOCUMENT_FIELD_SPEC_KEYS`)와 `collect`(records 전용)뿐이라,
   sections 의 `values`/`transform`/`template` 은 `_SPEC_TO_BLOCK` 을 타고 이미
   `value_map`/`transforms`/`derive` 로 번역된다. 기동을 막는 것은 `config_schema` 한 곳뿐이다(확인함).
3. `required` 검사가 파이프라인 **뒤**에 오는 순서를 유지한다. 기본값·상수로 충족된 필드가
   정상 통과해야 한다(현재 주석에 명시된 계약).
4. `collect_target_field_names` 가 sections 설정도 인식하는지 확인한다
   (`derive` 가 만드는 목표필드가 `body.labels`/`require` 에서 미지 필드로 오탐되지 않게).
5. `resource/templates/custom_field_TEMPLATE_semantic.yaml` 의
   "rows/records 와 달리 `values`·`transform`·`from/as`·`template` 은 읽지 않는다" 문단을 고친다.

### yaml 표기 예

```yaml
fields:
  PRODUCT_C:  {alias: [productCode, cardCode, code]}
  PRODUCT_NM: {alias: [cardTitle, productName, name]}
  SALE_STATUS:
    alias: [saleStatus]
    default: ON_SALE
    values: {"1": ON_SALE, "0": OFF_SALE}        # 신규
  ANNUAL_FEE:
    alias: [feeAmount]
    transform: [{name: regex_sub, pattern: "[^0-9]", repl: ""}, {name: to_int}]   # 신규
  DISPLAY_NM:
    template: "{{BRAND}} {{PRODUCT_NM}}"          # 신규
```

## 바꾸지 말 것

- **`from`/`as` 는 여기서 다루지 않는다.** 05 에서 어휘 자체가 없어진다.
  05 가 끝나면 sections 도 `transform: html_text` 로 같은 일을 할 수 있게 되므로,
  이 작업에서 `text_from`/`html_text_fields` 를 sections 에 열지 않는다.
- `collect` 는 records 전용으로 둔다.
- 섹션 본문 조립(`_walk`, 규칙 1~13)을 건드리지 않는다. 이 작업은 **공통 필드 값**만 다룬다.

## 영향 파일

- `facade/enrichment/json_semantic.py` (`__init__` 577~630, identity 확정 706~726)
- `facade/enrichment/config_schema.py` (`EXTRACTOR_KEYS["json_semantic"]`)
- `facade/enrichment/config_v2.py` (`COVERED_V1_KEYS` 확인)
- `resource/templates/custom_field_TEMPLATE_semantic.yaml`

`compile_*`/`apply_*` 는 `tabular_custom_fields.py` 에 있는 순수 함수다. **시그니처를 바꾸지 않는다**
(json_records 와 tabular 가 이미 호출 중이다 — 호출부 각 1곳).

## 검증

### 테스트 데이터 (없으면 만든다)

기존 `product_hpp` 3건으로는 **부족하다.** 이 작업이 여는 `values`/`transform`/`template` 을
실제로 태울 값(코드값·금액 문자열·결합 대상)이 그 원천에 없다.

| 용도 | 파일 | 상태 |
|---|---|---|
| 회귀(기존 동작 무변화) | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_*.json` 3건 | 있음 |
| 신기능(값 파이프라인) | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_fields_sample.json` | **만들어야 함** |

`examples/parse_chunk/make_product_hpp_fields_sample.py` 를 만들어 생성한다
(`make_stock_insight_sample.py` 와 같은 관례 — README "원칙 2" 참조).
넣을 것: 코드값 필드(`saleStatus: "1"`), 단위가 붙은 금액 문자열(`"18,000원"`),
`template` 로 합칠 두 필드(브랜드 + 상품명). **실 고객 값을 그대로 쓰지 않는다.**

만든 샘플은 `parse_chunk_verify.py` 의 `CASES` 에 등록해 이후 회귀망에 남긴다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'semantic or config_v2 or custom_fields_routing or transform'
```

`values`/`transform`/`template` 각각에 대해 **적용 순서를 고정하는 단정문 테스트**를 새로 넣는다
(`원천값 -> default -> const -> values -> transform -> template`). 다른 kind 와 순서가
어긋나는 것이 이 작업에서 가장 나기 쉬운 결함이다.

```bash
genon/preprocessor/examples/config_precheck/verify_v2_equivalence.sh
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only product_hpp
```

## 재색인

**기능 추가만 하고 출고 설정은 바꾸지 않는다.** 그러면 청크는 동일하다.
새 스펙을 실제로 쓰는 것은 별건이다.
