# 01. sections 본문 포함 규칙을 명시적으로

전제: [README.md](README.md) 의 "공통 규칙" 을 먼저 읽는다.

## 보고된 요구

> semantic 타입: 왜 기본으로 청크텍스트에 포함되는 항목이 존재해? 항상 body에 정의한 내용만
> 청크텍스트에 포함되도록 해줘. 기능을 최대한 일반화 해줘.

## 현재 동작

두 가지가 걸려 있다.

**(1) 특정 고객 필드명이 범용 매퍼에 하드코딩되어 있다.**

```python
# facade/enrichment/json_semantic.py:109
_SHARED_FIELD_LABELS = {
    "PRODUCT_NM": "상품명",
    "PRODUCT_C": "상품코드",
}
```

이 dict 가 `self.field_labels` 의 기본값으로 깔린다(같은 파일 588행). 설정에 아무 것도 안 적어도
이 두 필드는 청크 접두에 `상품명: …` 으로 실린다. 다른 사이트에서는 의미 없는 이름인데도
그 필드를 만들면 본문에 나타난다.

실제로 `resource/custom_field_product_hpp_semantic.yaml` 의 `body.labels` 에는
`PRODUCT_ATTRS` 하나뿐이고, `PRODUCT_NM`/`PRODUCT_C` 는 전적으로 이 하드코딩에 기대고 있다.

**(2) `body.labels` 가 표시 이름과 포함 스위치를 겸한다.**

```python
# json_semantic.py:796 부근 — 규칙 10
label = self.field_labels.get(target)   # 라벨이 없으면 접두에서 빠진다
```

다른 kind 는 `body.fields`(무엇을 실을까)와 `body.labels`(뭐라고 부를까)가 분리돼 있는데
sections 만 합쳐져 있다. `config_schema.EXTRACTOR_KEYS["json_semantic"]` 에 `text_fields` 가
없어서 그렇다.

## 바꿀 것

1. `_SHARED_FIELD_LABELS` 를 **제거**한다. `self.field_labels` 는 설정의 `body.labels` 만으로 만든다.
2. `body.fields`(내부 `text_fields`)를 sections 에서도 읽어 **접두 포함 여부의 단일 스위치**로
   삼는다. 선언이 아예 없으면 종전처럼 `labels` 에 이름이 있는 필드를 싣는다(하위호환).
3. `config_schema.EXTRACTOR_KEYS["json_semantic"]` 에 `text_fields` 를 추가한다.
4. `config_v2` 는 **고칠 것이 없다.** `_normalize_body` 에는 kind 제약이 아예 없어
   `body.fields → text_fields` 번역이 이미 sections 에도 적용된다. 지금 막고 있는 것은
   `config_schema` 한 곳뿐이다(확인함).
5. `resource/custom_field_product_hpp_semantic.yaml` 과 `resource_dev/` 사본에
   없어진 기본 라벨을 명시한다.
6. `resource/templates/custom_field_TEMPLATE_semantic.yaml` 에서
   "PRODUCT_NM → 상품명, PRODUCT_C → 상품코드 는 기본 제공 라벨" 문단과
   "`body.fields` 는 이 kind 가 읽지 않는다" 문단을 고친다.

### yaml 표기

```yaml
# 전 — 상품명·상품코드는 아무 데도 안 적혀 있는데 본문에 나온다
body:
  once: [PRODUCT_ATTRS]
  labels:
    PRODUCT_ATTRS: 연회비

# 후 — 본문에 나가는 것이 전부 여기 적혀 있다
body:
  fields: [PRODUCT_NM, PRODUCT_C]     # 접두에 실을 것. 없으면 metadata 전용
  once:   [PRODUCT_ATTRS]
  labels:
    PRODUCT_NM: 상품명
    PRODUCT_C: 상품코드
    PRODUCT_ATTRS: 연회비
```

## 바꾸지 말 것

- **섹션 본문(JSON 트리에서 자동 생성되는 부분)은 그대로 둔다.** "body 에 정의한 것만" 을
  섹션 본문까지 적용하면 allowlist 가 되어, 원천에 key 가 늘 때마다 조용히 누락된다.
  이 kind 의 계약("여기 적지 않은 key 도 자동으로 검색에 들어간다")과 CLAUDE.md 의
  "allowlist 보다 blocklist" 원칙에 정면으로 어긋난다. 제외 수단은 02 에서 하나로 합친다.
- `body.once`(`first_chunk_fields`), `body.split`, `body.repeat`, `body.mirror_to` 의 현 상태를
  건드리지 않는다. `split`/`repeat`/`mirror_to` 는 sections 가 읽지 않는 것이 맞다
  (본문은 섹션 워커가 만들고 과대 섹션 분할은 항상 켜져 있다).

## 영향 파일

- `facade/enrichment/json_semantic.py` (상수 109, 라벨 조립 588, 접두 조립 791~799)
- `facade/enrichment/config_schema.py` (`EXTRACTOR_KEYS["json_semantic"]`)
- `facade/enrichment/config_v2.py` (`COVERED_V1_KEYS` 확인만. `_normalize_body` 는 손대지 않는다)
- `resource/custom_field_product_hpp_semantic.yaml` + `resource_dev/` 사본
- `resource/templates/custom_field_TEMPLATE_semantic.yaml`

## 검증

### 테스트 데이터 (실제 문서로 검증한다)

| 용도 | 파일 |
|---|---|
| json_semantic 풀 캡처 | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_wcms_sample.json` |
| 최소 구조 | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_sample.json` |
| rich cell 표(연회비·적립) | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_rich_table_sample.json` |

셋 다 `parse_chunk_verify.py` 의 `CASES` 에 `product_hpp` 로 등록돼 있다. 새로 만들 것은 없다.

**접두 2줄(`상품명:`·`상품코드:`)의 유무가 이 작업의 판정 기준**이므로, dict 단정만으로 끝내지
말고 위 3건을 실제로 파싱·청킹해 첫 청크 접두를 확인한다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'semantic or config_v2 or custom_fields_routing'
```

```bash
genon/preprocessor/examples/config_precheck/verify_v2_equivalence.sh
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only product_hpp
```

`product_hpp` 케이스 3건(wcms 풀 캡처 / 최소 / rich table)이 이 작업의 회귀망이다.
**접두에서 `상품명:`·`상품코드:` 줄이 사라지지 않았는지** 를 반드시 눈으로 확인한다 —
yaml 에 라벨을 안 적으면 조용히 없어지고, 그러면 청크 본문이 바뀌어 재색인 대상이 된다.

## 재색인

yaml 을 위 "후" 처럼 적으면 청크 본문은 **동일**하다(같은 라벨을 명시로 옮긴 것뿐).
적지 않으면 접두 2줄이 사라져 재색인이 필요하다. 의도적으로 뺄 때만 그렇게 한다.
