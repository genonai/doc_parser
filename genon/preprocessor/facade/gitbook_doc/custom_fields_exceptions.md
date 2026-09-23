# 예외 목록

- **기동**: 설정 로드 시점. 서비스가 뜨지 않음
- **런타임**: 문서 처리 중. 해당 요청이 `code: 1` 로 실패
- 모든 오류는 HTTP 200 + `code: 1`

custom_fields 설정(yaml)은 **parser 만 읽습니다.** chunker 는 파서가 문서 메타로 실어 보낸
값을 소비할 뿐이므로, 설정 검증 예외는 전부 parser 쪽입니다.

---

## 응답 봉투

| 필드 | 값 |
|---|---|
| `code` | `0` 성공 / `1` 실패 |
| `errMsg` · `error_msg` | `[엔드포인트] 예외이름: 메시지`. 두 필드에 같은 값이 들어갑니다 |
| `error_code` | 아래 설명 참조 |
| `error_type` | 예외 클래스명 |
| `tag` | 실패한 엔드포인트 (`parser` / `parser_upload` / `chunker`) |
| `file_path` | 대상 파일. 파서 결과를 본문에 담아 호출하면 빈 값 |
| `stage` | `custom_fields` / `metadata` / `doc_summary` / `image_description` / `table_description` / `table_text_description` / `doc_type_stamp` / `enrichment` / `request`. **값이 있을 때만 실립니다** |
| `error_kind` | `timeout` / `transient` / `permanent`. **값이 있을 때만 실립니다** |
| `traceback` | 호출 스택 마지막 8줄 |
| `data` | 실패 시 항상 `null` |

### error_code 는 대부분 `1` 입니다

`GenosServiceException` 은 생성자 첫 인자가 그대로 `error_code` 가 되고 `main.py` 가 그 값을
우선합니다. 따라서 **서비스가 감싸 던진 오류는 `"1"`**(chunker 는 정수 `1`)로 나갑니다.
`INPUT_ERROR` / `TIMEOUT_ERROR` / `INTERNAL_ERROR` 로 분류되는 것은 **감싸지 않고 올라온
raw 예외**(마크다운 머리말 오류 등)뿐입니다.

원인 구분은 `error_code` 가 아니라 **`errMsg` 문구와 `stage`** 로 하십시오.

### error_kind 는 두 경우에만 실립니다

- 문서형 extractor 실패가 `error_policy: strict` 로 올라온 경우(`_handle_stage_error`)
- 요청 전체 deadline 초과(`stage: request`)

레코드형 하드페일과 chunker 예외는 `stage` 만 싣고 `error_kind` 는 실리지 않습니다.
판정 규칙은 `timeout`(deadline·Timeout 계열) / `transient`(5xx·연결 오류) /
`permanent`(4xx·값 오류) 입니다.

### 실패 정책

`error_policy: lenient`(기본)에서 문서형 extractor(`llm`/`python`/`html_select`)의 런타임
예외는 WARNING 후 성공 응답. `strict` 에서만 `code: 1`.
레코드형(`rows`/`records`/`sections`)은 정책과 무관하게 실패합니다.

### 응답 예시

엑셀 필수 컬럼 매핑 실패(`/parser`).

```json
{
  "code": 1,
  "errMsg": "[parser] GenosServiceException: 필수 Excel 컬럼 매핑 실패(sheet=FAQ): ['질문']; available=['번호', '답변', '카테고리']",
  "error_msg": "[parser] GenosServiceException: 필수 Excel 컬럼 매핑 실패(sheet=FAQ): ['질문']; available=['번호', '답변', '카테고리']",
  "error_code": "1",
  "error_type": "GenosServiceException",
  "tag": "parser",
  "file_path": "/data/faq.xlsx",
  "data": null,
  "traceback": "...(마지막 8줄)",
  "stage": "custom_fields"
}
```

`errMsg` 는 `[parser] GenosServiceException:` 접두가 붙으므로, 아래 표의 에러메시지와
대조할 때는 콜론 뒤만 보면 됩니다.

---

# parser — 기동 예외

설정을 읽고 컴파일하는 단계입니다. 전부 설정 오류이며 재시도로 낫지 않습니다.
레코드형 경로의 예외는 `custom_fields <라벨> 설정 오류:` 로 감싸져 어느 설정 블록이
문제인지 드러나고, 문서형(`llm`/`python`/`html_select`)은 감싸지 않아 raw 예외가 올라옵니다.

## ConfigV2Error

설정 파일이 v2 스키마를 벗어난 경우.

| 발생 조건 |
|---|
| 최상위에 `schema: v2` 없음 |
| 스키마에 없는 키 |
| 키를 쓸 수 없는 `kind` 에 사용 (`filter`·`merge_rows`·`records_at`, `select`·`attr`, `collect`, `python`) |
| 값의 형태가 스키마와 다름 (object·목록이어야 할 자리, `fields.<이름>` 단축 표기) |
| 함께 쓸 수 없는 조합 (`llm` + `python`, `llm[].scope`) |
| 필수 값 누락 (`source.pre.json.body_from`) |
| 미구현 키 사용 (`source.table_at`) |

## ValueError

| 발생 조건 |
|---|
| `config_file` 지정 없음 / yaml 최상위가 object 아님 |
| 해당 extractor 가 읽지 않는 키 (오타 / 다른 extractor 전용) |
| 목표필드명이 벡터 예약 필드와 충돌하거나 property 명명규칙 위반 |
| 참조한 필드를 만드는 설정이 없음 (`body.repeat`·`derive`·`pack`·`filter`·`merge_rows`·`seq`·`meta`) |
| 값 변환 정의 오류 (미등록 `transform`, 인자 불일치, 정규식, `to_json` 위치) |
| 배타적인 설정을 함께 사용 (`require` + LLM 생성 필드, `to_json`·`pack` 필드를 본문·필터에) |
| 값의 형태 오류 (`values` 별칭 중복, 정수·boolean 아님, 필수 하위 키 누락) |
| CSS 선택자 문법 오류 / 프롬프트 템플릿에 정의되지 않은 변수 |
| 외부 파이썬 파일 경로가 허용 범위 밖 |

## FileNotFoundError

| 발생 조건 |
|---|
| `config_file` |
| 프롬프트 파일 (`system_prompt_file`·`user_prompt_file`) |
| 외부 파이썬 파일 (`python.file`·`parser.file`) |

## ImportError

외부 파이썬 모듈 로딩 실패(파일 내 문법 오류 등).

## TypeError

`python.callable` · `parser.callable` 로 지정한 이름이 호출 가능하지 않음.

---

# parser — 런타임 예외

문서를 처리하는 중 발생합니다. 에러메시지로 원인이 갈립니다.

| 예외 | 에러메시지 | 발생 상황 |
|---|---|---|
| `ValueError` | `필수 Excel 컬럼 매핑 실패(sheet=…): [찾는 열]; available=[실제 열]` | 탐색대상 필드를 문서에서 찾지 못함<br>· 엑셀은 제목 행의 열 이름으로 값을 찾음 → 열 이름이 바뀌거나 빠지면 발생<br>· 찾는 열과 시트에 실제로 있는 열이 함께 표시 |
| `ValueError` | `records 키 '…' 를 JSON 에서 찾지 못했습니다` | 탐색대상 필드를 문서에서 찾지 못함<br>· 설정이 지정한 위치에서 여러 건이 담긴 목록을 찾지 못함<br>· JSON 구조가 바뀌면 발생 |
| `ValueError` | `[json_semantic] 필수 공통 필드 누락: […]` | 탐색대상 필드를 문서에서 찾지 못함<br>· 설정에서 필수로 지정한 값이 비어 있음<br>· 문서에 한 번만 나오면서 모든 청크에 공통으로 붙는 값이라, 비면 청크를 식별할 수 없어 실패로 처리<br>· 필수 지정을 풀면 경고만 남고 섹션 0건으로 진행 |
| `ValueError` | `정규화 후 중복되는 Excel 컬럼이 있습니다: …` | 문서 구조가 모호해 어느 값을 써야 할지 정할 수 없음<br>· 엑셀 제목 행에 같은 열 이름이 둘 이상 있음 |
| `ValueError` | `Markdown front matter 중복 키: …` | 문서 구조가 모호해 어느 값을 써야 할지 정할 수 없음<br>· 마크다운 머리말에 같은 항목이 두 번 적힘<br>· 기본 설정은 머리말을 무시하고 진행 → `on_invalid: error` 로 바꾼 경우에만 발생 |
| `ValueError` | `Markdown front matter에 순환 YAML alias가 있습니다.` | 위와 같음<br>· 머리말의 YAML 별칭이 서로를 가리켜 순환 |
| `ValueError` | `Markdown front matter 중첩 깊이가 32를 초과했습니다.` | 위와 같음<br>· 머리말의 중첩이 32단을 넘음 |
| `ValueError` | `json.text_fields […] 에 해당하는 텍스트를 JSON 에서 찾지 못했습니다` | JSON 입력을 파싱용 문서(HTML)로 바꾸는 단계에서 실패<br>· JSON 은 문서 형식이 아니라 파서가 직접 읽지 못함 → 본문 텍스트를 꺼내 HTML 로 합친 뒤 일반 문서와 같은 경로로 파싱<br>· 이 변환을 거치지 않으면 원문이 통째로 코드 블록처럼 취급되어 표·제목 구조가 사라짐 |
| `GenosServiceException` | `{"object": "error", "message": "프롬프트 입력 토큰 (…) 초과 하였습니다. (… - reserved …).", "type": "BadRequestError", …}` | LLM 호출 전 길이 검사에 걸려 호출하지 않고 중단<br>· 사전 길이 검사를 켠 경우에만 동작하며, 그 문서 처리는 실패로 끝남<br>· 검사 한도를 올리거나 검사를 끄면 호출은 진행 → 단, 모델이 길이를 이유로 거부할 수 있음 |
| `LLMApiError` | LLM 서버가 돌려준 응답 원문 (고정 문구 없음) | LLM 호출 자체가 실패<br>· 인증·권한 문제나 잘못된 요청 → 연결 설정을 고쳐야 함<br>· 서버 내부 오류·연결 실패·응답 시간 초과 → 일시적 장애일 수 있어 재시도가 유효 |
| `LLMResponseError` | `LLM 응답이 chat completion 형식이 아닙니다: …` | LLM 호출은 성공했지만 응답 내용이 약속된 형식이 아님<br>· LLM 앞단 게이트웨이가 오류를 정상 응답(200)에 담아 보내는 경우가 대표적 |
| `TypeError` | (파이썬 함수 반환값 관련) | 직접 작성한 처리 함수가 dict 가 아닌 값을 돌려줌 |

**구분 단서**

- 마크다운 머리말 오류 3종은 감싸지 않고 올라와 **`stage` 가 없고 `error_code` 가 `INPUT_ERROR`** 입니다. 나머지 parser 런타임 예외는 `stage: custom_fields` 를 싣습니다.
- 일시적 실패는 전처리기가 1회만 자체 재호출합니다(`Retry-After` 존중, 최대 10초).
- 표 설명 프롬프트가 `model_context_tokens` 를 넘는 경우도 `ValueError` 로 나지만, 기본 설정은 내용을 나눠 여러 번 호출하므로 발생하지 않습니다.

---

# chunker — 런타임 예외

설정 검증 단계가 없습니다. 파서 산출물을 소비하다 실패하며 전부 `GenosServiceException` 입니다.

| 에러메시지 | 발생 상황 |
|---|---|
| `행 metadata 를 청크 property 로 변환하지 못했습니다(element #N). 예약 필드와 겹치는 목표필드: […]` | 파서가 뽑은 항목을 청크 정보로 옮기지 못함<br>· 항목 이름이 시스템 예약 이름(`title`, `text`, `created_date`, `file_path` 등)과 겹치면 옮길 수 없음<br>· 청커에서 발생하지만 고칠 곳은 파서의 문서 유형 설정 |
| `chunker API: 'document'(인라인 JSON) 또는 file_path(.json) 입력이 필요합니다` | 청커에 들어온 입력을 읽지 못함<br>· 입력이 비어 있음 |
| `chunker 입력 형식을 인식할 수 없습니다(docling/parse-format 아님).` | 청커에 들어온 입력을 읽지 못함<br>· 파서 결과가 아닌 원본 문서를 그대로 넣음 |
| `chunker 입력 파일 로드 실패(<경로>): …` | 청커에 들어온 입력을 읽지 못함<br>· 파일을 열지 못함 (경로·권한·저장소 상태 확인) |
| `docling document 복원 실패: …` | 청커에 들어온 입력을 읽지 못함<br>· 파서 결과 JSON 이 잘리거나 깨져 되살릴 수 없음 |
| `chunk length is 0` | 입력은 정상적으로 읽었지만 청크가 하나도 만들어지지 않음<br>· 파싱 결과에 청크로 만들 본문이 없음<br>· 설정의 필수값 조건을 채우지 못해 파서 단계에서 모든 항목이 제외된 경우도 여기로 이어짐 |
| `Cancelled` | 요청 처리 중 호출한 쪽이 연결을 끊어 처리를 중단<br>· 호출 측 대기 시간 만료 또는 요청 취소<br>· 서버 오류가 아니므로 호출 측 타임아웃 설정을 확인 |

**구분 단서** — 청커 예외 중 `stage` 가 실리는 것은 **첫 항목 하나뿐**(`stage: custom_fields`)입니다.
`stage` 유무만으로 "파서 설정 문제 / 청커 입력 문제" 가 갈립니다.

그 항목은 **파서의 기동 시 검증을 문서형 extractor 가 받지 않아** 청킹까지 흘러온 결과입니다
(아래 검증 범위 차이 참조).

## RuntimeError

`chunking` extra 미설치(`semchunk`·`transformers` import 실패). 모듈 로드 시점이라 런타임
예외가 아닙니다.

---

# 예외가 발생하지 않는 실패

parser 기준입니다.

| 상황 | 결과 |
|---|---|
| `body.fields`·`body.labels` 에 없는 필드 | 경고, 해당 필드 누락 |
| `require` 미충족 | 그 건만 skip. 전 건이면 청크 0건 + 성공 응답 |
| `values` 미등록 값 | 경고, 원값 통과 |
| `source.pre.*` 를 문서형 아닌 kind 에 사용 | 검증 통과 + 무시 |
| front matter 없음·깨짐 | `on_missing`·`on_invalid` 기본값 `ignore` 로 조용히 누락 |
| 등록 블록(`- custom_fields:`) 키 오타 | 키 검증 대상 아님, 무시 |
| `GENOS_CUSTOM_FIELDS_VALIDATION=warn` | 모든 키 검증이 경고로 격하, 해당 설정 무시 |
| 문서형 extractor 런타임 실패 | `error_policy: lenient`(기본)에서 경고 + 성공 응답 |
| 표 설명 프롬프트 길이 초과 | 기본 설정(`overflow_policy: batch`)은 나눠 호출 |
| 동명 키가 얕은 곳·깊은 곳에 존재 | 얕은 쪽 값이 실림 (로그 없음) |
| 동명 레코드 배열이 여러 곳에 존재 | `records_at` 최초 매칭 1개만 사용 (로그 없음) |

## 기동 시 검증 범위 차이 (parser)

| 검사 | `rows`/`records`/`sections` | `llm`/`python`/`html_select` |
|---|---|---|
| 모르는 키·오배치 키 | O | O |
| 설정 구조(object/list) | O | v2 스키마 범위만 |
| 목표필드명 예약어·명명규칙 | O | X |
| 값 파이프라인 참조 정합 | O | X |
| 본문 필드(`body.*`) 정합 | O | X |
