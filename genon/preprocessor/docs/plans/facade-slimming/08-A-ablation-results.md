# 08-A. 설정 최소화 ablation — 1차 실행 결과 (2026-09-06)

[08-customer-facade.md](08-customer-facade.md) 의 검증 설계대로 **현행 코드 위에서** 돌린 기록이다.
리팩터링 전에 돌리는 이유는 이 결과가 `core/toolbox.py` 의 설계 입력이기 때문이다.

## 재현

```bash
# 실행 위치: genon/preprocessor
.venv/bin/python examples/parse_chunk/ablation/run_ablation.py                 # 전체
.venv/bin/python examples/parse_chunk/ablation/run_ablation.py --only cs_hpp   # 일부
.venv/bin/python examples/parse_chunk/ablation/run_ablation.py --step diff     # 대조만
```

배포 설정을 건드리지 않는다. `resource_dev/` 를 임시 디렉터리로 두 벌 복사해 한 쪽만
최소화하고 각각 `--config` 로 넘긴다. LLM 캐시 스코프는 두 실행이 같다(00 결정 2).

대상 25케이스 / 16 doc_type. `research_report` 는 폐기되어 제외했다.

## 하네스 자체에서 나온 결함 2건 (수정 완료)

**① `--output-format json` 강제가 다른 코드 경로를 탔다.** 06 드릴에서 그대로 물려받은
인자였는데, 이것을 주면 청커가 parse-format 경로(`_chunk_parse_format`)로 빠져 **문서 단위
custom_fields 가 실리지 않는다.** 실측: `cs_hpp` 가 5청크·필드 채움에서 **18청크·0채움**으로
바뀌었다. 공식 검증기(`parse_chunk_verify.py`)는 이 인자를 주지 않는다.

이것을 못 잡았으면 `extractor: llm` 4종(card, cs_hpp, product_slf, product_ssf)의 기준선이
전부 빈 값이 되어, 계획이 경고한 **"LLM soft-fail 이 빈 값으로 일치를 만든다"** 가 그대로
재현될 뻔했다. 실제로 첫 실행에서 cs_hpp 4건이 "동일" 로 통과했고, 파고들어 보니
`CS_CATEGORY`/`TITLE`/`CONTENT` 가 **기준선에서 0/11** 이었다.

**교훈: "동일" 판정은 그 필드가 기준선에서 비어 있지 않았음을 함께 확인해야 성립한다.**
하네스가 `[기준선 공백: …]` 을 항상 함께 출력하는 이유다.

**② doc_type 하나에 yaml 이 둘인 경우를 잘못 귀속했다.** `faq` 는 xlsx→`custom_field_faq`,
json→`custom_field_faq_json` 이고 `product_hpp` 는 md→llm, json→`product_hpp_semantic` 이다.
`verify.pick_block` 으로 확장자별 지배 설정을 붙여 해결했다.

## 노이즈 대조군 — 이 하네스의 유용한 성질

**최소화 대상이 아닌 설정의 케이스가 곧 노이즈 대조군이다.** 제거할 고급 키가 없으면
양쪽 실행이 같은 설정을 쓰므로 차이 0 이 나와야 한다.

| 설정 | 결과 |
|---|---|
| card (274청크) · cs_slf · faq(xlsx) · menu · term | **차이 0** |
| **cs_ssf** | **차이** — `SUMMARY_TEXT` `text` `n_char` `n_word` `n_line` `text_table_*` 각 **1청크** |

**6건 중 1건이 흔들렸다.** 캐시 스코프를 맞췄는데도 LLM 값이 1청크 달라졌다.
따라서 **"1청크 규모의 `SUMMARY_TEXT`/`text` 계열 차이"는 신호로 보지 않는다.**
청크 수가 바뀌거나 여러 청크가 함께 바뀐 것만 신호로 센다.

## 결과 — 제거가 산출을 바꾼 것

| 설정 | 제거된 키 | 증상 |
|---|---|---|
| `faq_json` | `source.records_at` `on_missing` | 청크 **2 → 1** |
| `monimo_news` | `source.records_at` `on_missing` | 청크 **2 → 1** |
| `monimo_event` | `source.records_at` `on_missing` | `monimo_event_sample.json` **3 → 2** |
| `stock_insight` | `source.merge_rows` | 청크 **13 → 21** |
| `cs_hpp` | `source.pre` `body.once` `body.mirror_to` | `CONTENT` 값이 **5·3·9·4청크**에서 변화, `.parsed` 는 **11 → 12** |
| `product_slf` | `source.pre` `body.once` | **7 → 8**, **3 → 4** |
| `product_ssf` | `source.pre` `body.once` | **7 → 8** |
| `product_hpp_semantic` | `source.ignore_keys` `sections` `body.once` | **8 → 13**, **6 → 8**, **12 → 17**, `fields_sample` 은 청크 수 유지하고 `text`·`SECTION_NM` 변화 |

## 결과 — 제거해도 산출이 같은 것

| 설정 | 제거된 키 | 판정 |
|---|---|---|
| `cs_sss` | `source.on_missing` | 차이 0 (2청크) |
| `monimo_event` | `records_at` `on_missing` | `real_sample`·`table_sample` 차이 0 (각 3청크) |

**`on_missing` 은 단독으로는 어느 케이스에서도 산출을 바꾸지 않았다** — 레코드가 실제로
빠지는 원천이 표본에 없다. 제거 후보 1순위다.

**`records_at` 은 원천에 따라 갈린다.** `monimo_event` 는 같은 `records_at: eventList` 인데
`real_sample`·`table_sample` 은 이름 검색이 알아서 찾아내 차이가 없었고, 협의용
`monimo_event_sample.json` 만 3→2 로 줄었다. 05 의 경계 ①(동명 키 충돌)과 같은 성격이다.

## 여기서 나오는 toolbox 요구 (잠정)

08-B 에서 훅으로 메울 때 실제 필요가 확정된다. 지금 시점의 예상은 이렇다.

| 차이 | 메우는 방법 | toolbox 필요 |
|---|---|---|
| `records_at` (3건) | `pre_source` 에서 payload 재구성 1줄 | 없음 (06 B3 선례) |
| `on_missing` | 무영향 — 메울 것 없음 | 없음 |
| `merge_rows` (stock_insight) | `pre_source` 에서 행 그룹 병합 루프 | 없음 (파이썬 기본) |
| `body.once` `mirror_to` | `post_parse` 에서 필드 복사·접두 | 없음 |
| `source.pre.markdown.text_fence` | 펜스 해체 로직이 Spec 기반이라 함수로 안 꺼내진다 | **`tb.unfence_text()`** |
| `source.pre.{html,markdown}.marker_headings` | `promote_markdown_marker_headings` 는 함수로 있다. html 쪽은 `marker_heading_match` | 재수출만 |
| `source.sections` `ignore_keys` (json_semantic) | 섹션 라벨 맵은 훅으로 재현 불가 | **기본 집합에 남기는 것이 유력** |

## 판정

**08-A 는 목적을 달성했다.** 산출은 합격/불합격이 아니라 두 가지다.

1. **하네스가 검증 가능한 상태가 됐다** — 결함 2건을 잡았고, 노이즈 바닥(1청크 LLM 흔들림)을
   측정했다. 이 하네스는 08-B 에서 그대로 쓴다.
2. **메워야 할 차이 8건이 특정됐다** — 그중 6건은 훅만으로, 1건은 toolbox 함수 1개 추가로,
   1건(`json_semantic` 의 `sections`)은 설정에 남기는 쪽이 유력하다.

## 다음

08-1(core/parser 이동)로 넘어간다. 위 표의 toolbox 요구를 `core/toolbox.py` 설계에 반영한다.
08-B 는 같은 명령을 리팩터링본 위에서 다시 돌리고, 이번에는 **차이를 훅으로 메운 뒤**
차이 0 과 합격 기준 4개를 함께 본다.
