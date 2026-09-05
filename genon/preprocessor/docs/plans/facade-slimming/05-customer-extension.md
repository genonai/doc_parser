# 05. 지금 기능으로 어디까지 되는가 · 어디서부터 코드인가


> **상태: 완료 (2026-09-06).** 이 문서의 조사 결과를 고객 문서로 옮겼다.
>
> `gitbook_doc/parser_processor.md` 의 **"새 문서 유형 추가하기"** 절에 9항목이 전부 들어갔다 —
> extractor 4종 지원 매트릭스(v2 표기, v1 괄호 병기), `transform` 9종과 체이닝, 별칭 탐색
> 범위, 조용히 실패하는 자리, 설정으로 안 되는 8가지와 우회법, 고칠 자리 3개,
> `_load_json_payload` 정규화 훅과 doc_type 게이팅, 파서→청커 element 계약,
> 고객이 자기 골든을 만드는 절차. 청커 몫은 신설한 `gitbook_doc/chunking_processor.md` 다.
>
> 06 드릴이 이 문서에 **한 절을 추가하게 만들었다** — "경고조차 없이 **틀린 값**이 실리는 자리".
> 동명 키 충돌과 `records_at` 최초 매칭은 실패가 아니라 조용한 오답이라, 기존의
> "조용히 실패하는 자리" 표와 성격이 다르다.

전제: [04](04-user-surface.md) 완료.

**이 문서는 조사와 문서화다. 새 기능을 만들지 않는다.**
목적은 고객이 새 문서 유형을 만났을 때 "설정으로 되는 것" 과 "코드가 필요한 것" 의 경계를
분명히 하고, 코드가 필요할 때 그 자리가 어디인지 알려주는 것이다.

> 이 문서는 독립 검증을 거쳐 전면 개정됐다. 초판은 표기 체계(v1/v2)를 섞었고,
> `transform` 을 2종으로 적었으며, 경계를 4가지로 축소했다. 아래가 실측 결과다.

## 표기 — v2 가 이미 출고 설정이다

`resource/custom_field_*.yaml` **17/17 이 `schema: v2`** 다(커밋 `2a28195d`, 2026-09-04).
`resource_dev/` 17개와 템플릿 4종도 전부 v2 다. **고객이 실제로 쓸 표기는 v2 다.**

v1 표기도 계속 동작한다 — 로더가 `config_v2.is_v2()` 로만 갈리고, v1 은 normalize 를
아예 거치지 않는다. 다만 **한 파일 안에서 섞으면 기동에 실패한다.**

아래 표는 전부 **v2 표기**이고 괄호가 v1 대응이다.

주의할 동음이의: v2 의 `template`(필드 결합)은 v1 의 `derive` 다.
v1 에도 `template` 이 따로 있는데 그것은 **llm 전용 프롬프트 변수 치환 모드**로 뜻이 다르다.

## 고객이 주로 하는 일 — 지원 매트릭스

✔ = 됨 / ✗ = 그 키를 적으면 **기동 실패** / ⚠ = 통과하지만 무효이거나 제한

### 원천에서 값 가져오기 (`fields:`)

| 기능 | v2 (v1) | rows<br>`tabular_mapping` | records<br>`json_mapping` | sections<br>`json_semantic` | document<br>`llm` |
|---|---|:-:|:-:|:-:|:-:|
| 별칭 | `alias` (kind별 4블록) | ✔ | ✔ | ✔ | ✔ |
| 반복 key 전부 수집 | `collect` (`collect_key_map`) | ✗ | ✔ | ✗ | ✗ |
| 상수 | `const` (`constants`) | ✔ | ✔ | ✔ | ✔ |
| 기본값(빈 값만) | `default` (`defaults`) | ✔ | ✔ | ✔ | ✔ |
| 값 접기 | `values` (`value_map`) | ✔ | ✔ | ✔ | ✔ |
| 변환(9종 체이닝) | `transform` (`transforms`) | ✔ 표 살림 | ✔ 표 살림 | ⚠ **표 뭉갬** | ⚠ **표 뭉갬** |
| 필드 결합 | `template` (**`derive`**) | ✔ | ✔ | ✔ | ✔ |

값 적용 순서는 kind 공통이다: `default`(빈 값만) → `const`(덮어씀) → `values` → `transform` → `template`

### 청크 본문에 싣기 (`body:`)

| 기능 | v2 (v1) | rows | records | sections | document |
|---|---|:-:|:-:|:-:|:-:|
| 본문 구성 필드 | `body.fields` (`text_fields`) | ✔ 선택 | ✔ **필수** | ⚠ 뜻이 다름¹ | ✗ |
| 항목명 | `body.labels` (`field_labels`) | ✔ | ✔ | ✔ | ✔ |
| 과대 본문 분할 | `body.split` (`split`) | ✔ | ✔ | ⚠ 항상 분할 | ✗ |
| 모든 청크에 반복 접두 | `body.repeat` (`chunk_prefix_fields`) | ✔ split 시만 | ✔ split 시만 | ⚠ 자동 생성 | ✔ |
| 첫 청크에만 1회 | `body.once` (`first_chunk_fields`) | ✗ | ✗ | ✔ | ✔ |
| 본문을 메타 필드에 복사 | `body.mirror_to` (`body_fields`) | ✗ | ✗ | ✗ | ✔ |

¹ sections 의 `body.fields` 는 본문 구성이 아니라 **공통 필드를 청크 접두에 실을지 정하는 스위치**다.
본문은 트리 순회가 만든다.

### 원천 구조 · 필터 · LLM

| 기능 | v2 (v1) | rows | records | sections | document |
|---|---|:-:|:-:|:-:|:-:|
| 레코드 배열 위치 | `source.records_at` (`records`) | ✗ | ✔ | ✗ | ✗ |
| 못 찾을 때 정책 | `source.on_missing` (`missing_policy`) | ✗ | ✔ | ✔ | ✗ |
| 여러 행 접기 | `source.merge_rows` (`row_merge`) | ✔ | ✔ | ✗ | ✗ |
| 섹션 표시 이름 | `source.sections` | ✗ | ✗ | ✔ | ✗ |
| 서브트리 제외 | `source.ignore_keys` | ✗ | ✗ | ✔ | ✗ |
| 포맷 전처리 | `source.pre.markdown` / `.html` | ⚠ **무시** | ⚠ **무시** | ⚠ **무시** | ✔ |
| 필수값(빈 값 제외) | `require.fields` (`required`) | ✔ 건별 | ✔ 건별 | ✔ 문서 전체 | ✗ |
| 값 기반 제외 | `filter` | ✔ | ✔ | ✗ | ✗ |
| 필드별 LLM 생성 | `llm:` (`llm_fields`) | ✔ 행별² | ✔ 레코드별 | ✔ 문서 1회 | (본체가 LLM) |

² rows 의 LLM 필드는 **parser 경로에서만** 실행된다. intelligent/convert 는 기동 시 경고만 남긴다.

### 청크텍스트 정제 (extractor 무관)

`chunking.text_cleanup` 은 **doc_type 별 설정이 아니라 프로세서 config** 다.
`chunking` / `intelligent` / `convert` 에 있고 **`parser_processor.py` 에는 없다.**
파서와 청커를 나눠 배포하면 **청커 쪽 yaml** 에 적어야 한다.

## `transform` 은 9종이다 — 그리고 체이닝된다

초판이 2종만 적은 것이 실무적으로 가장 손해가 컸다. 전체 목록:

| 이름 | 인자 | 하는 일 |
|---|---|---|
| `date_int` | — | 날짜 텍스트 → `YYYYMMDD` 정수 |
| `date_int_flex` | — | 위 + 2자리 연도(`26.07.01`)·구분자 없는 `260701` |
| `text_norm` | — | NFKC + 공백 축약 + casefold (중복 판정용) |
| `regex_sub` | `pattern`, `repl` | 정규식 치환 (`"18,000원"` → `"18000"`) |
| `regex_extract` | `pattern`, `group` | 정규식 오려내기. 미매칭 시 `None` |
| `to_int` | `on_error` | 숫자만 남겨 정수화 |
| `truncate` | `length`, `suffix` | 길이 자르기(적재 컬럼 길이 맞춤) |
| `html_text` | (런타임 렌더러) | HTML 로 **강제** 평문화. 표·목록 유지 |
| `text` | (런타임 렌더러) | JSON/HTML/평문 **자동 판별** 후 평문화 |

체이닝이 "새 요건 = 코드 수정" 을 막는 핵심 수단이다.

```yaml
fields:
  FEE_AMT:
    alias: [수수료]
    transform:
      - {name: regex_sub, pattern: "[^0-9]", repl: ""}
      - {name: to_int}
```

잘못된 이름·빠진 인자·컴파일 안 되는 정규식은 **기동 시** 잡는다.

## 탐색 범위 — 이름으로 찾는다, 경로로는 못 찾는다

| 대상 | 탐색 범위 | 우선순위 |
|---|---|---|
| records `source.records_at` | 문서 **임의 깊이 BFS** | 최초 매칭 1개 |
| records `alias` | **레코드 안 임의 깊이 BFS** | **깊이 우선**, 같은 레벨에서만 선언 순서 |
| records `collect` | 레코드 안 BFS **레벨 순서**, **중복 제거** | 전부(중복 제외) |
| sections `alias` | **문서 루트의 스칼라만** | 루트 1회 확정 후 불변 |
| sections 본문 | 트리 **자동 전수 순회**(깊이≤12, 노드≤5000) | 미설정 key 도 자동 포함 |
| sections `ignore_keys` | **key 이름 glob**, 경로 아님 | 매칭 시 서브트리 통째 제외 |
| rows `alias` | **시트 첫 행 헤더(깊이 없음)** + 시트 컨텍스트 | 선언 순서, 실제 컬럼 우선 |
| document `alias` | **front matter 최상위 키만**(중첩 미탐색) | **순수 선언 순서** |

**`alias` 우선순위 규칙이 kind 마다 다르다.** "여러 개 적으면 먼저 찾은 것" 이라는 설명은
document 에만 문자 그대로 맞다. records/rows/sections 는 깊이·헤더가 먼저 좌우한다.

## 설정으로 안 되는 경계 — 8가지

### 우회 가능한 것

**① 동명 키 충돌** — 얕은 것이 이겨서 깊은 것을 못 고른다.
- 우회 A: 원하는 값의 컨테이너가 이름 있는 배열/객체면 `source.records_at` 을 그쪽으로 내린다. 대가: 상위 레벨 필드를 못 읽는다
- 우회 B: `records_at` 이 다른 매퍼를 2개 등록한다
- 남는 경계: 같은 배열 안 같은 이름, 컨테이너가 무명일 때

**② 동적 키**(`{"P001":{…},"P002":{…}}`) — **json_semantic 이면 설정 0줄로 본문이 들어온다.**
key 이름을 모른 채 전수 순회하고 key 를 섹션명으로 쓴다. 초판이 이것을 놓쳤다.
- 남는 경계: "동적 키마다 레코드 1건 + 그 키에 딸린 메타데이터" 는 불가

**③ 조건부 선택** — 형제 값에 따라 갈리는 경우.
- 우회 A(배타적일 때만): `template: "{{A}}{{B}}"`. **둘 다 있으면 붙어버린다**
- 우회 B: `filter` + `records_at` 이 다른 매퍼 2개
- 남는 경계: 형제 필드 값에 따라 **필드 값**을 고르기. `filter` 는 `in`/`not_in` 뿐이고 AND 만 된다

### 우회 불가

**④ json_semantic 의 루트 밖 공통 필드** — "1 파일 = 1 대상" 전제를 지키려는 의도된 제약.

**⑤ sections/document 에서 표 구조를 살린 평문화가 안 된다** — `html_text`/`text` 는
런타임 렌더러 주입이 필요한데 그 호출부가 넘기지 않는다. 실측: `<table>…a…b…</table>` → `"ab"`.
카드 상품의 혜택 표가 공통 필드로 오면 한 줄로 뭉개진다.
**코드 한 줄(호출부에 렌더러 주입)이면 닫히는 진짜 결함이다 — 별도 이슈 후보.**

**⑥ `merge_rows` 는 연속 런만 접는다** — 같은 키가 떨어져 오면 별개 레코드로 남는다(의도된 안전장치).

**⑦ `values` 는 fail-open** — 미등록 값은 경고만 남기고 원값 통과. "열거 밖은 전부 X 로" 를 표현할 수 없다.

**⑧ `source.pre.*` 는 `extractor: llm` 에서만 소비된다** — rows/records/sections 설정에 적으면
**검증도 통과하고 조용히 무시**된다. `.json` 문서모드 `json:` 블록은 v2 문법에 자리가 없어
등록 블록에 직접 쓴다(출고 설정이 그렇게 하고 있다).

## 조용히 실패하는 자리

"오타는 기동 실패로 드러난다" 는 절반만 맞다. 아래는 **경고만 남기고 진행**한다 —
고객이 가장 오래 헤매는 지점이다.

| 상황 | 결과 |
|---|---|
| `body.fields` 에 없는 필드 | 경고만, 본문에서 조용히 빠짐 |
| `body.labels` 에 없는 필드 | 경고만, 라벨 없이 값만 |
| rows/records 의 `require` 미충족 | 그 건만 skip. **전건이면 청크 0건인데 요청은 성공** |
| `values` 미등록 값 | 경고만, 원값 통과 |
| `source.pre.*` 를 llm 아닌 kind 에 | 검증 통과 + 무시 |
| `GENOS_CUSTOM_FIELDS_VALIDATION=warn` | 모든 키 검증이 경고로 격하 |

## 코드가 필요할 때 고칠 자리

### 핵심 — `_load_json_payload` 가 정규화 훅이다

`parser_processor.py:1510` 의 `_load_json_payload` 는 **두 JSON 경로 모두의 유일한 입구**다
(`:1646` `_parse_json_records`, `:1724` `_parse_json`). 저장소 전체 참조가 이 3곳뿐이고
테스트 참조는 0건이다.

**여기서 payload 모양을 바꾸면 경계 ①②③ 이 10~15줄 facade 변경으로 풀린다.**
이 사실이 이 문서의 가장 중요한 내용이다 — 안내가 없으면 고객은 경계 조사가 지목한
"이름 검색" 을 따라 `facade/enrichment/json_records.py`(공용 모듈)로 가게 된다.

참고로 JSON 처리 로직의 실질 총량은 공용 모듈 쪽이 훨씬 크다
(`json_records.py` 786 + `json_semantic.py` 1,014 + `converters/json_text.py` 213 +
`field_transforms.py` 765 ≈ 2,778줄). facade 쪽은 얇은 호출부다.

**반드시 `doc_type` 으로 게이팅한다.** 지금 시그니처가 `@staticmethod(file_path)` 라
doc_type 을 못 받는다. 게이팅 없이 정규화를 넣으면 **모든 JSON doc_type 의 산출이 바뀌어**
06 의 합격 기준(골든 차이 0)이 자동으로 깨진다.
04 가 이 훅을 정식 "고칠 자리" 로 삼는다면 **시그니처 조정을 04 산출에 포함**한다.

### 그 밖의 자리

- `__call__` 의 확장자 라우팅 — 진입점
- `source.pre` 소비 게이팅 — `markdown_front_matter.py:495`
- `json:` 블록 빌더 — `parser_processor.py:1055`

### 파서 → 청커 element 계약 (반드시 문서화할 것)

새 JSON 경로를 넣으면 결국 청커가 그것을 소비한다. 실측한 계약:

- element 의 `category` 가 `{"tabular_row", "custom_fields_row", "faq_row"}` 중 하나면 행 기반 경로로 간다
- 그 경로는 **비-행 element 를 경고 한 줄 남기고 버린다**
- 그 category 가 아니면 조용히 일반 텍스트 분할로 빠진다

**category 문자열 하나를 잘못 쓰면 metadata 가 청크에 안 실리거나 element 가 통째로 사라진다.**
06 이 "facade 1개" 를 기준으로 삼는 이상, 이 계약을 지켜 청커 무수정으로 끝내는 것이 합격의 전제다.

## 04 에 반영할 것

`gitbook_doc/parser_processor.md` 와 신설할 `chunking_processor.md` 에 넣는다.

1. 신규 doc_type 추가 절차(설정만으로 되는 경우)
2. extractor 4종 지원 매트릭스 — 위 3개 표
3. `transform` 9종과 체이닝
4. 탐색 범위 표
5. 설정으로 안 되는 경계 8가지와 우회법
6. 조용히 실패하는 자리
7. `_load_json_payload` 정규화 훅과 doc_type 게이팅
8. 파서 → 청커 element 계약
9. 고객이 자기 골든을 기록하는 절차 → [07](07-customer-change-lifecycle.md) D 항목

## 향후 제안 — 이번 범위 아님

- **⑤ 렌더러 주입 한 줄** — 경계 중 유일하게 코드 한 줄로 닫히는 진짜 결함. 별도 이슈 1순위
- 별칭에 경로 문법 허용 — 경계 ①③ 의 상당수가 설정으로 넘어온다. 다만 신규 문법이다
- `converters/` 를 사용자 등록 지점으로 여는 원천 어댑터 — 신규 메커니즘이다
