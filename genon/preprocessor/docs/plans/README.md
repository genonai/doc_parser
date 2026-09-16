# custom_fields 정리 — 작업 계획

**intelligent 기능 동등화** (07~10) — parser 의 custom_fields yaml 을 intelligent 도
전부 처리하게 만든다. 07 이 분석·로드맵이고 08~10 이 구현이다.

각 파일은 다른 세션에서 단독으로 열어 그것만 보고 진행할 수 있게 썼다.

먼저 있던 **v2 스키마 정리**(01~06)는 2026-09 에 끝나 계획 문서를 걷어냈다
(`task/360`). 같은 자리에 있던 facade 정리(#363) 계획도 마찬가지다 —
실전 드릴 결과만 `examples/parse_chunk/drill/RESULTS.md` 로 옮겼다.
둘 다 필요하면 git 이력에서 꺼낸다.

## 배경

v2(`processing/enrichment/config_v2.py`)는 v1 내부 dict 로 정규화하는 **번역 계층**이고,
매퍼 4종(tabular / json_records / json_semantic / custom_fields_enricher)은 그대로다.
그래서 "표기만 다르다"가 구조로 보장된다. 번역 결함은 `config_v2.COVERED_V1_KEYS` 드리프트
가드(`test_config_v2_unit.py`)와 `precheck_custom_fields.sh` 가 잡는다.

옛 v1 표기와 그 이관 도구(`to_v2` / `migrate_to_v2.sh` / `verify_v2_equivalence.sh`)는
2026-09-10 에 걷어냈다 — 표기는 `schema: v2` 한 가지뿐이다.

`kind` 마다 다르게 생겼던 스펙(`alias`·`values`·`transform`·`template`)은
01~06 에서 rows/records/sections/document 네 종류에 같은 모양으로 맞춰 놓았다.
현재 어느 kind 가 무엇을 받는지는 `facade/gitbook_doc/parser_processor.md` 의
매트릭스가 단일 출처다.

값 적용 순서는 네 kind 가 같다.

```
원천값 → default(빈 값만 채움) → const(덮어씀) → values → transform → template
```

`const` 가 `default` 보다 먼저 오는 것이 아니라 **`default` 를 덮는 위치**에 있고,
값 파이프라인은 그 **뒤**다. `constants: {X: ""}` 로 빈 값을 못 박았을 때 default 가
그것을 되살리지 않게 하려는 계약이다.

## 다른 세션에서 쓰는 법

**세션 하나 = 작업 하나.** 여러 개를 한 세션에서 하면 CLAUDE.md "수정 범위" 의 기준 1
(이걸 빼면 보고된 증상이 남는가)을 스스로 적용할 수 없게 된다.

### 시작

```
/issue-start <작업 번호와 제목>
```

그다음 첫 프롬프트로 아래를 준다. **README 를 먼저 읽히는 것이 중요하다** — 공통 규칙과
검증 3원칙이 여기 있고, 작업 파일은 그것을 전제로 쓰였다.

```
genon/preprocessor/docs/plans/README.md 와 <작업파일>.md 를 읽고 그 작업만 진행해줘.
"바꾸지 말 것" 과 "테스트 데이터" 절을 그대로 지켜.
```

### 진행 중

- **범위 밖 결함을 발견하면** 고치지 말고 README 의 "이번 범위에서 뺀 것 (별도 이슈)" 에
  한 줄 추가하고 넘어간다.
- **계획과 코드가 다르면 계획 파일을 고치고 보고한다.** 계획이 단일 출처다.
  이 문서들도 실측으로 세 번 틀린 적이 있다(01·03 의 `config_v2` 서술, 07 의 "동기 처리" 주석).
- 커밋은 `/wip`, 마무리는 `/open-pr`.

### 끝난 뒤

위 표의 **상태 열을 갱신한다**(`미착수` → `진행중 #<이슈>` → `완료 #<PR>`).
세션끼리 겹치지 않게 하는 유일한 장치다.

### 지금 바로 시작할 수 있는 것

07 과 08 은 바로 열 수 있고 서로 독립이다. **10 은 09 없이 불가능**하다.

## intelligent 기능 동등화

parser 가 처리하는 custom_fields yaml 중 intelligent 가 **무음으로 무시하는 것**이 7가지다
(`records`·`sections`·`source.pre.*`·`json:`·`rows` 의 `llm:` 등). 근본 원인은 배선 누락이
아니라 **입력 라우팅 모델 차이**다 — intelligent 는 xlsx/PDF 를 뺀 전부를 PDF 로 변환해서,
원천 payload 가 필요한 기능이 구조적으로 도달 불가능하다. 격차 표와 근거는 07 에 있다.

| # | 파일 | 단계 | 위험 | 상태 |
|---|---|---|:-:|---|
| 07 | [07-intelligent-parity.md](07-intelligent-parity.md) | 분석·로드맵 + 0단계(무음→경고, 주석 정정) | 낮음 | 미착수 |
| 08 | [08-intelligent-tabular-llm-fields.md](08-intelligent-tabular-llm-fields.md) | 1단계: `rows` 의 `llm:` 실행 | 낮음 | 미착수 |
| 09 | [09-intelligent-format-routing.md](09-intelligent-format-routing.md) | 2단계: 원천 포맷 네이티브 라우팅 | **높음** | 미착수 |
| 10 | [10-intelligent-record-kinds.md](10-intelligent-record-kinds.md) | 3단계: `records`/`sections`/`source.pre` 배선 | 중간 | 미착수 |

## 의존과 순서

```
07 (분석·0단계) ─→ 08 ─┐
                       ├─ 08 과 09 는 서로 독립
                  09 ──┴─→ 10   (10 은 09 없이는 불가능)
```

## 공통 규칙 (모든 작업)

- **보고된 것만 고친다.** CLAUDE.md "수정 범위" 절의 5개 기준을 적용 전에 스스로 통과시킨다.
  조사 중 눈에 띈 다른 결함은 아래 "별도 이슈" 에 적고 이번 변경에 끌어들이지 않는다.
- **docling 은 건드리지 않는다.** 네 작업 모두 `genon/preprocessor/` 안에서 끝난다.
  docling 수정이 필요해 보이면 그 판단 자체가 틀렸을 가능성이 높으니 먼저 보고한다.
- **하위호환 흡수는 v1·v2 두 경로가 합류하는 지점에 한 벌만 둔다.** 초안은 `normalize()` 라고
  적었는데 틀렸다 — `config_v2.load` 는 `is_v2()` 로만 갈려서 **v1 표기 설정은 `normalize()` 를
  아예 거치지 않는다.** 거기 두면 v1 의 옛 표기가 조용히 무효가 되고, 그걸 막으려면 소비
  지점에 사본이 또 필요해 두 벌이 된다.
  - 하위 표기(`sections.include`, `front_matter.metadata_fields`)는 `normalize()` 가
    안쪽을 보지 않고 통과시키는 자리에 있으므로 **소비 지점**(매퍼·spec 생성자)에서
    흡수한다.
  - 최상위 표기면 `normalize()` 가 맞다.
  - 어휘 자체를 없애는 경우(선례: `from`/`as`)는 흡수하지 않고 **제거**한다 — 흡수하면
    매퍼가 두 벌을 계속 들고 있어야 해 정리의 목적이 사라진다. 없어진 최상위 키는
    `precheck_custom_fields.py` 의 `REMOVED_KEYS` 에 갈아탈 표기와 함께 적는다.
  - 템플릿(`resource/templates/custom_field_TEMPLATE_*.yaml`)에서는 어느 경우든 옛 표기를 지운다.
- **`config_schema.EXTRACTOR_KEYS` 를 함께 고친다.** 매퍼가 새로 읽는 키를 여기 넣지 않으면
  그 키를 쓴 설정이 기동에 실패한다. 반대로 안 읽는 키를 넣으면 조용히 무시된다.
- **`COVERED_V1_KEYS` 를 함께 고친다.** `test_config_v2_unit.py` 가 이 집합이 `EXTRACTOR_KEYS` 를
  덮는지 지킨다.
- 소스 주석·docstring 에 이모지를 쓰지 않는다. 보고는 한국어로.

## 공통 검증

### 원칙 1 — 검증은 항상 실제 문서 데이터로 한다

**mock 이나 손으로 만든 dict 만으로 확인하고 끝내지 않는다.** 설정이 약속한 필드가 실제 청크에
실렸는지는 원천 파일을 실제로 파싱·청킹해 봐야만 알 수 있다. 지금까지 놓친 결함은 대부분
"단위 테스트는 통과하는데 실제 문서에서는 값이 비는" 형태였다.

각 작업 파일의 "검증" 절에 그 작업이 써야 할 **샘플 파일 경로**를 적어 두었다. 대부분
`genon/preprocessor/sample_files/monimo/` 에 있고, `parse_chunk_verify.py` 의 `CASES` 가
doc_type 과 샘플의 짝을 관리한다.

### 원칙 2 — 쓸 샘플이 없으면 만든다

새 기능은 그것을 태울 원천이 저장소에 없는 경우가 많다. 그때는 **가상 문서를 만들어 커밋한다.**
손으로 파일을 만지지 말고 **생성 스크립트를 두고 그 스크립트를 커밋한다.**

선례: `examples/parse_chunk/make_stock_insight_sample.py`
(원천의 어떤 특징을 왜 흉내 내는지 docstring 에 적고, 샘플이 바뀌면 스크립트를 고쳐 다시 돌린다).

지킬 것.
- 생성 스크립트는 `examples/parse_chunk/make_<이름>_sample.py`, 산출물은 `sample_files/` 아래.
- docstring 에 **무엇을 재현하는 샘플인지**와 실행 명령을 적는다.
- 실 고객 데이터를 그대로 넣지 않는다. 구조만 흉내 내고 값은 지어낸다.
- 만든 샘플은 `parse_chunk_verify.py` 의 `CASES` 에 등록해 이후 회귀망에 남긴다.

### 원칙 3 — 단위 테스트는 필요한 부분만 돌린다

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit/<바꾼 모듈을 쓰는 파일> -q -p no:randomly --color=no
```

**`tests/unit` 전체(201초)를 돌리지 않는다.** 사전 실패도 섞여 있어 신호가 묻힌다.
파일을 특정하기 어려우면 `-k '<키워드>'` 로 좁힌다 — 각 작업 파일에 쓸 키워드를 적어 두었다.

회귀인지 사전 실패인지 헷갈리면 `git stash push -- <바꾼 파일>` 로 그 테스트만 대조하고
곧바로 `git stash pop` 한다.

단위 테스트에서 **외부 LLM 을 호출하지 않는다.** CI 는 사내망에 접근하지 못한다.
스텁과 더미 URL 을 쓰고, 실호출 검증은 smoke + skipif 로 둔다.

```bash
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh  # 지원 키·재색인 영향
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only <doc_type…>
```

`--only` 에 쓸 doc_type 은 작업마다 파일에 적어 뒀다. 전체 이름은
`examples/parse_chunk/parse_chunk_verify.py` 의 `CASES` 에 있다(케이스 23건 / doc_type 14종).

## 재색인 영향

청크 본문이 바뀌면 이미 적재된 임베딩과 어긋난다. 비교 전에 **같은 버전으로 2회 돌려
노이즈 기준선**을 먼저 잡는다(LLM 응답 흔들림 때문에 1회 비교로는 변경분과 노이즈를
구분할 수 없다).

**실측한 노이즈는 두 가지뿐이다**(01~05 에서 반복 확인): `reg_date`(생성 타임스탬프)와
`[표 검색 설명]` 문단(LLM 이 표마다 새로 쓰는 설명, 그리고 그 길이 `n_char`/`n_word`).
그 둘을 걷어내면 나머지는 완전히 결정적이라 `*.chunks.json` 전량 대조가 가능하다 —
PASS/FAIL 은 사전 실패에 가려지므로 전량 대조 쪽이 신뢰할 만하다.

## intelligent 경로의 검증 특이점

`parse_chunk_verify.sh` 는 **parser 경로만** 돈다. intelligent 경로는 이것으로 확인할 수 없다.
엔드포인트 확인은 새 pytest 를 만들기보다
`examples/code_serving/serving_gateway_test.sh` 에 모드를 추가하는 방식을 쓴다.

최종 목표는 **같은 원천·같은 doc_type 이면 parser 와 intelligent 의 청크 본문이
같을 것**이다. 이 대조 자체를 게이트웨이 스크립트의 한 모드로 만들어 두면 이후 회귀망이 된다.

## 이번 범위에서 뺀 것 (별도 이슈)

검토 중 나왔지만 보고된 요구에 필요하지 않아 제외한 것들이다. 해당 작업에 끌어들이지 말 것.

- **`sections` 키에 fnmatch 허용** — 지금은 정확 일치라 `"*Img*": 이미지` 를 못 쓴다.
  `ignore_keys`(fnmatch)와 `sections`(정확 일치)의 매칭 규칙이 갈린 채 남아 있다.
- **`collect` 를 `alias: [...] , multi: true` 로 통합** — records 전용으로 남는 유일한 스펙.
  `alias`(먼저 찾은 하나)와 개념이 겹치지만, 통합해도 표현력이 늘지 않고 출고 설정 전부의
  의미가 바뀐다.
- **`resource_dev/` 에 커밋된 실 API 키** — 이 작업들과 무관하지만 rotate 가 필요하다.
- **convert_processor 의 기능 동등화** — intelligent 와 완전히 같은 모양이다(같은 두 줄 배선,
  같은 PDF 우선 라우팅). 08 은 코드가 동일해 convert 도 함께 고치지만, 09·10 은 intelligent
  만 대상으로 한다. 라우팅 변경의 위험이 커서 한 번에 두 프로세서를 바꾸지 않는다.
- **HWP/PPT 등 parser 가 네이티브로 다루는 다른 포맷을 intelligent 로 옮기는 것** — 09 의
  라우팅 일반화가 길을 열어 주지만, 이번 요구는 custom_fields yaml 에 관한 것이다.
