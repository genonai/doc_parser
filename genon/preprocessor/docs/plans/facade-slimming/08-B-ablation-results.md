# 08-B. 설정 최소화 ablation — 2차 실행 결과 (2026-09-06)

리팩터링본(08-1~08-3) 위에서 [08-A](08-A-ablation-results.md) 와 같은 시나리오를 돌리고,
**이번에는 차이를 훅으로 실제로 메웠다.** 이 결과가 08 의 최종 판정이다.

## 재현

```bash
# 실행 위치: genon/preprocessor
.venv/bin/python examples/parse_chunk/ablation/run_ablation.py                    # 전체
.venv/bin/python examples/parse_chunk/ablation/run_ablation.py --only cs_hpp --step min diff
```

드릴 훅은 `facade/parser_processor.py` 에 넣었다가 되돌렸다(06 과 같은 방식).
그 diff 는 이 문서에 그대로 옮겨 두었다.

## 하네스 개정 — 제거 단위를 잘게 쪼갰다

08-A 는 `source.pre` 를 통째로 지웠다. 그 안에는 **서로 다른 세 기능**이 들어 있다.

| 하위 키 | 하는 일 |
|---|---|
| `markdown.front_matter` | 머리말 값을 metadata 로 **승격** + 본문에서 제외 |
| `markdown.text_fence` | 레이아웃 보존 펜스를 단락으로 복원 |
| `{html,markdown}.marker_headings` | 도형 마커 줄을 섹션 헤더로 승격 |

뭉뚱그려 지우면 무엇이 차이를 만들었는지 못 가른다. 실제로 처음엔 `text_fence` 탓으로
오판했고, 훅으로 머리말까지 걷어냈다가 **승격이 사라져 metadata 가 어긋났다.**
`prune` 을 임의 깊이로 고쳐 셋을 따로 재고 나서야 원인이 갈렸다.

## 노이즈 대조군

제거 대상이 없는 설정 6종(card · cs_slf · cs_ssf · faq · menu · term)이 **전부 차이 0** 이다.
08-A 에서 흔들렸던 `cs_ssf` 도 이번엔 일치했다(캐시가 warm). 하네스와 기준선은 건전하다.

## 결과 — 훅으로 메운 것

**5 doc_type / 13 케이스가 훅 26줄로 차이 0 이 됐다.**

| 설정 | 제거된 키 | 훅 | 줄수 | 결과 |
|---|---|---|---:|---|
| `faq_json` `monimo_news` `monimo_event` | `records_at` `on_missing` | `pre_source` | 5 | 차이 0 (5케이스) |
| `product_slf` `product_ssf` | `markdown.text_fence` | `pre_source` | 3 | — |
| 〃 | `body.once` `labels` | `post_parse` | 6 | 차이 0 (3케이스) |
| `cs_hpp` | `markdown.marker_headings` | `pre_source` | 3 | — |
| 〃 | `body.once` `body.mirror_to` | `post_parse` | 6 | 차이 0 (5케이스) |

doc_type 당 평균 5줄, 최대 9줄이다. 합격 기준(30줄 이하)에 크게 못 미친다.

### `records_at` 대체 — 5줄

```python
        if ext == ".json" and isinstance(data, dict):
            for key in ("faqMenuList", "eventList"):
                if isinstance(data.get(key), list):
                    return data[key]
```

`collect_records` 는 키가 없으면 "payload 가 목록이면 그 안의 dict 들, dict 면 1건" 이다.
최상위를 목록으로 만들면 그대로 풀린다.

### `body.once` / `mirror_to` 대체 — 6줄

```python
        if doc_type == "cs_hpp":
            tb.set_chunk_metadata(result, {
                tb.FIRST_CHUNK_FIELDS_KEY: ["CS_CATEGORY", "TITLE"],
                tb.BODY_FIELDS_KEY: ["CONTENT"],
            })
```

## 이 드릴이 드러낸 설계 구멍 — `post_parse` 가 청크에 닿지 않았다

**`result["metadata"]` 에 써도 청커가 읽지 않는다.** 파서와 청커는 별도 API 라 문서 단위
메타는 `DoclingDocument` 의 `KeyValueItem` 에 실려 경계를 넘고(레코드/표 경로는 각 element 의
`metadata`), 응답 봉투의 `metadata` 는 호출자용 정보다.

`post_parse` 는 직렬화 **뒤**에 있어 살아 있는 문서를 못 만진다. 즉 **docling 경로에서
post_parse 로는 청크 메타를 바꿀 수 없었다** — 대부분의 문서가 그 경로다.

`toolbox.set_chunk_metadata(result, {...})` 를 열어 막았다. 두 경로를 알아서 가르고,
docling 경로는 문서를 한 번 왕복시킨다(공짜가 아니므로 쓸 때만 부른다).

**이 구멍은 골든이 못 잡는다.** 훅이 아무 것도 안 하면 차이 0 으로 통과하기 때문이다.
차이를 실제로 메워 보는 08-B 가 아니었으면 출고 뒤에 드러났을 것이다.

## 결과 — 훅으로 못 메운 것 (= 기본 기능 집합에 남긴다)

| 설정 | 기능 | 판정 근거 |
|---|---|---|
| `stock_insight` | `source.merge_rows` | 14줄로 **청크 수는 맞췄지만** `DETAIL_DESC`/`DETAIL_TEXT` 값이 13청크에서 어긋났다. `concat` 이 값 파이프라인 **이전**에 값을 목록으로 이어붙여 렌더까지 바꾸므로, 재현하려면 구현을 역설계해야 한다 |
| `product_hpp_semantic` | `source.sections` `ignore_keys` | 섹션 라벨 맵이 `json_semantic`(1,014줄) 순회를 좌우한다. 훅은 payload 모양만 바꾼다 |
| 모든 md 원천 | `markdown.front_matter` | 본문 제외는 훅으로 되지만 **metadata 승격**은 안 된다. `pre_source` 와 `post_parse` 는 상태를 공유할 수 없다 |

## 최종 기본 기능 집합 (08-A 잠정 → 08-B 확정)

| 계층 | 키 |
|---|---|
| **기본** (남긴다) | `schema` `kind` `source` `fields` `alias` `default` `transform` `values` `const` `body` `require` `labels` `split` `template` **+ `merge_rows` · `sections` · `ignore_keys` · `markdown.front_matter`** |
| **제거 가능** (훅으로 됨) | `records_at` `on_missing` `body.once` `body.mirror_to` `markdown.text_fence` `{html,markdown}.marker_headings` |

08-A 의 잠정 분류에서 **4개가 기본으로 되돌아왔다.** 전부 "설정이 값 파이프라인 안쪽이나
순회 자체를 바꾸는" 것들이고, 훅이 다루는 것은 그 바깥(원천 모양, 산출 모양)이다.
**이 경계가 08-B 의 산출이다.**

## 합격 기준 판정

| 기준 | 목표 | 실측 | 판정 |
|---|---|---|---|
| 산출 동일성 | 파싱·청킹까지 차이 0 | 훅 대상 13케이스 **전부 차이 0** | **통과** |
| 고객 코드량 | doc_type 당 30줄 이하 | 평균 5줄 / 최대 9줄 | **통과** |
| toolbox 밖 import 0 | 0 | **0** — 드릴이 쓴 것은 `tb.*` 뿐 | **통과** |
| core 수정 0 | 0 | `toolbox.set_chunk_metadata` **1개 추가** | **조건부 통과** |

네 번째만 조건부다. 고친 것은 core 로직이 아니라 **toolbox 에 통로를 연 것**이고,
"toolbox 결손 → toolbox 에 추가" 라는 08-A 의 해석 규칙에 해당한다. 다만 그 결손이
설계 구멍이었다는 사실은 위에 남긴다.

## 남은 위험

- `markdown.front_matter` 의 승격을 훅으로 열려면 `pre_source` 가 `(데이터, 메타)` 를
  돌려줄 수 있어야 한다. **A3 로 훅이 고정 API 가 되므로 지금 정할지 판단이 필요하다.**
  이번에는 열지 않았다 — 요구가 실측으로 확인된 것은 이 한 건뿐이다.
- 08-B 는 **기존 doc_type** 으로만 쟀다. 신규 문서(json·xlsx·md·html)는 08-4 다.
