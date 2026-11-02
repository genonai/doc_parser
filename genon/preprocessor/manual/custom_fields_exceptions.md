# 예외 목록

- **기동**: 설정 로드 시점. 서비스가 뜨지 않음
- **런타임**: 문서 처리 중. 해당 요청(액티비티 호출)이 실패
- 실패가 호출 측에 보이는 모양은 진입점마다 다릅니다.
  - 코드서빙(전처리기 Temporal 워커): 액티비티 오류(`ApplicationError`). `type` 은 아래 `error_kind` 와 같은 값
    (`permanent`/`transient`/`timeout`, 없으면 예외 이름), `non_retryable` 은 `permanent` 일 때 `true`. 메시지는
    `<slug> <원본 이름>: <예외 이름>: <메시지>` 입니다. 아래 응답 봉투의 필드(`reason_code`·`stage` 등)는 따로 실리지 않습니다
  - GenOS 전처리기(기본 전처리기 서비스 `/run`): HTTP 200 + `code: 1` 응답 봉투(아래)

custom_fields 설정(yaml)은 **parser 만 읽습니다.** chunker 는 파서가 문서 메타로 실어 보낸
값을 소비할 뿐이므로, 설정 검증 예외는 전부 parser 쪽입니다.

---

## 응답 봉투

GenOS 전처리기(`/run`)의 실패 응답입니다.

| 필드 | 값 |
|---|---|
| `code` | `0` 성공 / `1` 실패 |
| `errMsg` · `error_msg` | 예외 메시지. `error_msg` 는 `error_code` 가 있을 때 같은 값으로 함께 실립니다 |
| `error_code` | 아래 설명 참조 |
| `stage` | `custom_fields` / `metadata` / `doc_summary` / `image_description` / `table_description` / `table_text_description` / `doc_type_stamp` / `enrichment` / `request` / `chunk_validation`. **값이 있을 때만 실립니다** |
| `error_kind` | `timeout` / `transient` / `permanent`. **값이 있을 때만 실립니다** |
| `reason_code` | 사유 코드(`PARSE_D001` 형식). 아래 [사유 코드](#사유-코드) 참조. 실패 응답에는 항상 실립니다 |
| `error_category` | `data`(원천데이터) / `preprocess`(전처리) / `system`(시스템). `reason_code` 의 분류문자에서 정해집니다 |
| `data` | 실패 시 항상 `null` |

### error_code 는 대부분 `1` 입니다

`GenosServiceException` 은 생성자 첫 인자가 그대로 `error_code` 가 됩니다. 따라서 **서비스가 감싸 던진
오류는 `"1"`** 로 나갑니다. **감싸지 않고 올라온 raw 예외**(마크다운 머리말 오류 등)에는 `error_code` 가 실리지 않습니다.

원인 구분은 `error_code` 가 아니라 **`reason_code`** 로 하십시오. 호출 측이 이미 `error_code` 를
쓰고 있을 수 있으므로 `error_code` 의 값은 바꾸지 않았습니다.

### error_kind

아래 사유 코드표의 성격 열이 `error_kind` 로 실립니다. 원천데이터(D)·전처리(P) 오류는 다시 시도해도
결과가 같으므로 `permanent` 이고, 시스템(S) 오류는 코드 번호대로 나뉩니다. 사유 코드가 없는
미분류 오류(`*_P999`)의 `error_kind` 는 감싸는 자리가 정합니다. 매핑 단계처럼 감싸는 자리가 결정적 오류로
보는 곳에서는 `permanent` 가 실리고, 진입점의 일반 예외 경로에서는 실리지 않습니다.

Temporal 워커 경로에서는 같은 세 값이 액티비티 오류(`ApplicationError`)의 `details[0]` 에 `reason_code`·`error_category`·`error_kind` 로
실립니다. 사유 코드가 없는 예외는 `null` 이며 `*_P999` 로 바꾸지 않습니다(자세한 모양은 `code_serving_dev_manual_monimo.md` 6.4).

## 사유 코드

형식은 `<단계>_<분류문자><3자리 번호>` 입니다.

| 단계 | 범위 |
|---|---|
| `PARSE` | 파서 API |
| `CHUNK` | 청커 API |
| `COMMON` | 진입점 공통(요청 형식, 요청 deadline) |

| 분류문자 | `error_category` | 뜻 | 1차 조치 |
|---|---|---|---|
| D | `data` | 원천 파일의 내용·구조가 문서유형 설정의 기대와 다름 | 원천 제공 측. 원천 형식이 정당하게 바뀌었으면 문서유형 설정을 고침 |
| P | `preprocess` | 전처리 설정 오기입, 처리 로직 실패, 호출 규약 위반 | 전처리 운영자 |
| S | `system` | 실행 환경·파일시스템·네트워크·외부 서비스 | 인프라 운영자 |

시스템(S) 코드의 번호대는 `S0xx` 환경·권한·접속 설정(`permanent`), `S1xx` 네트워크·시간 초과·입출력
일시 장애(`transient`/`timeout`), `S2xx` 외부 서비스 응답 이상(`permanent`) 입니다.

| 코드 | `error_kind` | 내용 |
|---|---|---|
| `PARSE_D001` | permanent | 엑셀·CSV 필수 열 부재 |
| `PARSE_D002` | permanent | JSON 레코드 배열(`records_at`) 부재 |
| `PARSE_D003` | permanent | 필수 공통 필드 공백 |
| `PARSE_D004` | permanent | 정규화 후 열 이름 중복 |
| `PARSE_D005` | permanent | 마크다운 front matter 형식 오류(중복 키, YAML 문법, 종료 구분자 없음, 크기 초과 등) |
| `PARSE_D006` | permanent | front matter 별칭 순환 |
| `PARSE_D007` | permanent | front matter 중첩 한도 초과 |
| `PARSE_D008` | permanent | JSON 본문 텍스트 키(`body_from`) 부재 |
| `PARSE_D009` | permanent | 빈 파일, 지원하지 않거나 손상·암호화된 파일 |
| `PARSE_D010` | permanent | JSON 문법 손상 |
| `PARSE_D011` | permanent | 구분자로 나뉜 레코드가 없음 |
| `PARSE_D012` | permanent | `markdown.sections` 적용 후 본문 없음 |
| `PARSE_D013` | permanent | `source.sheets` 로 지정한 시트가 워크북에 없음 |
| `PARSE_P001` | permanent | LLM 입력 길이 사전 검사 초과(목차·메타데이터 추출의 `precheck`). 표 설명의 길이 초과(`overflow_policy: error`)는 경고 후 진행합니다 |
| `PARSE_P002` | permanent | 등록되지 않은 doc_type(`defaults.unknown_doc_type: fail`) |
| `PARSE_P003` | permanent | 같은 doc_type 에 설정이 여러 개 |
| `PARSE_P004` | permanent | `llm_fields` 설정 오류 |
| `PARSE_P005` | permanent | `records_at` 없이 레코드 배열을 고를 수 없음 |
| `PARSE_P999` | (없음) | 분류되지 않은 파서 오류 |
| `PARSE_S001` | permanent | LLM 인증·권한·요청 거부(429 를 제외한 4xx) |
| `PARSE_S002` | permanent | 실행 환경 구성 누락(LibreOffice, unstructured) |
| `PARSE_S003` | permanent | 원천 파일 없음·권한 거부 |
| `PARSE_S101` | transient | LLM 일시 장애(429, 5xx, 연결 실패). 호출자가 나중에 다시 시도할 만하다는 뜻이며, 전처리기의 자체 재호출 대상(429·502·503·504)과는 범위가 다릅니다 |
| `PARSE_S102` | timeout | LLM 호출 시간 초과 |
| `PARSE_S103` | transient | 그 밖의 파일 입출력 실패 |
| `PARSE_S201` | permanent | LLM 응답 형식 이상 |
| `CHUNK_D001` | permanent | 청크로 만들 본문 없음(`chunk length is 0`) |
| `CHUNK_D002` | permanent | 전체 청크 불량(`CHUNK_ALL_REJECTED`) |
| `CHUNK_D003` | permanent | 불량 청크 존재, `fail_on_any: true`(`CHUNK_REJECTED`) |
| `CHUNK_P001` | permanent | 행 메타를 청크 속성으로 옮기지 못함 |
| `CHUNK_P002` | permanent | 입력 누락 |
| `CHUNK_P003` | permanent | 파서 결과가 아닌 입력 |
| `CHUNK_P004` | permanent | 파서 결과 손상(JSON 문법, 문서 복원 실패) |
| `CHUNK_P005` | permanent | 청크 판정 처리 중 예외(`CHUNK_VALIDATION_ERROR`) |
| `CHUNK_P999` | (없음) | 분류되지 않은 청커 오류 |
| `CHUNK_S003` | permanent | 입력 파일 없음·권한 거부 |
| `CHUNK_S103` | transient | 그 밖의 입력 파일 입출력 실패 |
| `COMMON_P002` | (없음) | 요청 형식 오류(필수 본문 키 누락 등) |
| `COMMON_P999` | (없음) | 분류되지 않은 오류(`/run`) |
| `COMMON_S102` | timeout | 요청 deadline 초과 |

청크 검증의 메시지 앞머리(`CHUNK_ALL_REJECTED:` 등)는 하위호환을 위해 그대로 둡니다.

### 실패 정책

문서형 extractor(`llm`/`python`/`html_select`)의 LLM 호출 실패·응답 해석 실패는 `error_policy` 와 무관하게
WARNING 을 남기고 해당 필드를 null 로 둔 채 성공 응답합니다. 문서 요약·표 설명 단계도 같습니다.
LLM 오류를 실패 응답(`code: 1`, `PARSE_S0xx`·`S1xx`·`S2xx`)으로 받는 경로는 docling 기본 enrichment(목차·메타데이터
추출)입니다. 레코드형(`rows`/`records`/`sections`)의 매핑 오류는 정책과 무관하게 실패합니다.

### 응답 예시

엑셀 필수 컬럼 매핑 실패(파싱 처리기를 올린 GenOS 전처리기 `/run`).

```json
{
  "code": 1,
  "errMsg": "필수 Excel 컬럼 매핑 실패(sheet=FAQ): ['질문']; available=['번호', '답변', '카테고리']",
  "error_msg": "필수 Excel 컬럼 매핑 실패(sheet=FAQ): ['질문']; available=['번호', '답변', '카테고리']",
  "error_code": "1",
  "data": null,
  "stage": "custom_fields",
  "error_kind": "permanent",
  "reason_code": "PARSE_D001",
  "error_category": "data"
}
```

코드서빙 워커에서는 같은 실패가 액티비티 오류 `type: permanent`, 메시지
`parse <원본 이름>: GenosServiceException: 필수 Excel 컬럼 매핑 실패(…)` 로 옵니다. 아래 표의 에러메시지와
대조할 때는 접두 뒤만 보면 됩니다.

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
| 액티비티 폴더(`activities/<slug>/`)에 `config.yaml` 이 없음 — 등록 오류. 기본 설정으로 대신하지 않습니다 |
| 프롬프트 파일·`config_file` 의 상대 경로가 설정 폴더 밖(`../` 등). 설정 폴더 안의 사본만 읽습니다 |
| 프롬프트 파일이 비어 있음 |

## FileNotFoundError

| 발생 조건 |
|---|
| `config_file` |
| 프롬프트 파일 (`system_prompt_file`·`user_prompt_file`·`prompt_template_file`·`prompt_file`). 설정에 적은 파일이 없으면 내장 기본 프롬프트로 대신하지 않습니다 |
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
| `ValueError` | `레코드 배열 후보 […] 를 fields 의 alias 로 구별할 수 없습니다. source.records_at 에 배열 키를 지정하세요.` | 문서 구조가 모호해 어느 값을 써야 할지 정할 수 없음<br>· `records_at` 을 적지 않으면 JSON 안의 목록을 자동으로 찾음 → 후보 목록이 여럿인데 설정의 항목 이름만으로는 어느 것인지 가릴 수 없음<br>· 어느 후보에서도 설정의 항목 이름에 해당하는 값을 찾지 못한 경우도 포함<br>· `missing_policy` 와 무관하게 실패 → `records_at` 을 지정하면 해소 |
| `ValueError` | `Markdown front matter 중복 키: …` | 문서 구조가 모호해 어느 값을 써야 할지 정할 수 없음<br>· 마크다운 머리말에 같은 항목이 두 번 적힘<br>· 기본 설정은 머리말을 무시하고 진행 → `on_invalid: error` 로 바꾼 경우에만 발생 |
| `ValueError` | `Markdown front matter에 순환 YAML alias가 있습니다.` | 위와 같음<br>· 머리말의 YAML 별칭이 서로를 가리켜 순환 |
| `ValueError` | `Markdown front matter 중첩 깊이가 32를 초과했습니다.` | 위와 같음<br>· 머리말의 중첩이 32단을 넘음 |
| `ValueError` | `json.text_fields […] 에 해당하는 텍스트를 JSON 에서 찾지 못했습니다` | JSON 입력을 파싱용 문서(HTML)로 바꾸는 단계에서 실패<br>· JSON 은 문서 형식이 아니라 파서가 직접 읽지 못함 → 본문 텍스트를 꺼내 HTML 로 합친 뒤 일반 문서와 같은 경로로 파싱<br>· 이 변환을 거치지 않으면 원문이 통째로 코드 블록처럼 취급되어 표·제목 구조가 사라짐 |
| `GenosServiceException` | `{"object": "error", "message": "프롬프트 입력 토큰 (…) 초과 하였습니다. (… - reserved …).", "type": "BadRequestError", …}` | LLM 호출 전 길이 검사에 걸려 호출하지 않고 중단<br>· 사전 길이 검사를 켠 경우에만 동작하며, 그 문서 처리는 실패로 끝남<br>· 검사 한도를 올리거나 검사를 끄면 호출은 진행 → 단, 모델이 길이를 이유로 거부할 수 있음 |
| `LLMApiError` | LLM 서버가 돌려준 응답 원문 (고정 문구 없음) | LLM 호출 자체가 실패<br>· 인증·권한 문제나 잘못된 요청 → 연결 설정을 고쳐야 함<br>· 서버 내부 오류·연결 실패·응답 시간 초과 → 일시적 장애일 수 있어 재시도가 유효 |
| `LLMResponseError` | `LLM 응답이 chat completion 형식이 아닙니다: …` | LLM 호출은 성공했지만 응답 내용이 약속된 형식이 아님<br>· LLM 앞단 게이트웨이가 오류를 정상 응답(200)에 담아 보내는 경우가 대표적 |
| `TypeError` | `extractor: python 의 결과는 dict 이어야 합니다: <타입>` / `custom_fields parser 결과는 dict 이어야 합니다.` | 직접 작성한 처리 함수의 반환값이 약속된 형식이 아님<br>· python 추출 함수나 LLM 응답 해석 함수(parser)가 dict 가 아닌 값을 돌려줌<br>· 기본 설정(`error_policy: lenient`)은 경고만 남기고 진행 → `strict` 로 바꾼 경우에만 실패 |

**구분 단서**

- 위 표의 원인별 사유 코드는 [사유 코드](#사유-코드) 표에 있습니다. 매핑 오류(`ValueError`)는 `stage: custom_fields` 를 싣습니다.
- 일시적 실패 중 429·502·503·504·연결 오류는 전처리기가 1회만 자체 재호출합니다(`Retry-After` 존중, 최대 10초). 500 은 자체 재호출하지 않지만 응답의 `error_kind` 는 `transient` 입니다.
- 표 설명 프롬프트가 `model_context_tokens` 를 넘는 경우도 `ValueError` 로 나지만, 기본 설정은 내용을 나눠 여러 번 호출하므로 발생하지 않습니다.

---

# chunker — 런타임 예외

custom_fields 설정 검증 단계가 없습니다. 파서 산출물을 소비하다 실패하며 전부 `GenosServiceException` 입니다.
청크 검증 실패(`CHUNK_ALL_REJECTED`·`CHUNK_VALIDATION_ERROR`)는 그 하위 클래스인 `ChunkValidationError` 라 코드서빙
워커의 오류 메시지에 `ChunkValidationError:` 가 붙습니다(`/run` 응답의 필드는 같습니다).
청커 자체 설정인 청크 검증(`chunking.validation`)의 오기입은 요청 때가 아니라 기동 시 `ValueError` 로 실패합니다.

| 에러메시지 | 발생 상황 |
|---|---|
| `행 metadata 를 청크 property 로 변환하지 못했습니다(element #N). 예약 필드와 겹치는 목표필드: […]` | 파서가 뽑은 항목을 청크 정보로 옮기지 못함<br>· 항목 이름이 시스템 예약 이름(`title`, `text`, `created_date`, `file_path` 등)과 겹치면 옮길 수 없음<br>· 청커에서 발생하지만 고칠 곳은 파서의 문서 유형 설정 |
| `chunker API: 'document'(인라인 JSON) 또는 file_path(.json) 입력이 필요합니다` | 청커에 들어온 입력을 읽지 못함<br>· 입력이 비어 있음 |
| `chunker 입력 형식을 인식할 수 없습니다(docling/parse-format 아님).` | 청커에 들어온 입력을 읽지 못함<br>· 파서 결과가 아닌 원본 문서를 그대로 넣음 |
| `chunker 입력 파일 로드 실패(<경로>): …` | 청커에 들어온 입력을 읽지 못함<br>· 파일을 열지 못함 (경로·권한·저장소 상태 확인) |
| `docling document 복원 실패: …` | 청커에 들어온 입력을 읽지 못함<br>· 파서 결과 JSON 이 잘리거나 깨져 되살릴 수 없음 |
| `chunk length is 0` | 입력은 정상적으로 읽었지만 청크가 하나도 만들어지지 않음<br>· 파싱 결과에 청크로 만들 본문이 없음<br>· 설정의 필수값 조건을 채우지 못해 파서 단계에서 모든 항목이 제외된 경우도 여기로 이어짐 |
| `CHUNK_ALL_REJECTED: 모든 청크가 검증 기준에 걸려 제외됐습니다(<파일>, 입력 N건, 사유별 {…}).` | 청크 검증에서 모든 청크가 불량으로 판정되어 적재할 청크가 남지 않음<br>· 일부 청크만 불량이면 오류 없이 그 청크만 빼고 적재하며, 제외된 청크의 정보(제외 건수, 청크별 페이지·사유·설명, 최대 20건)를 성공 응답의 `chunk_validation` 으로 알려 줌. 제외된 청크마다 마스킹을 거친 본문 앞 200자(`preview`)와 원래 청크 순번(`index`)을, 요약에는 사유별 건수(`reasons`)를 함께 실음<br>· 사유(`blank`·`no_content`·`broken_chars`·`repetition`·`min_chars`)별 건수가 함께 표시<br>· 재시도로 해결되지 않음 → 원문 상태나 검증 기준(`min_chars` 등)을 확인<br>· 어떤 청크가 불량인지는 아래 [청크 검증 판정 기준](#청크-검증-판정-기준) 참조 |
| `CHUNK_VALIDATION_ERROR: 청크 판정 중 오류가 났습니다(<파일> #N): …` | 청크 검증 기능 자체가 판정 도중 실패<br>· 판정하지 못한 청크를 그대로 적재하지 않기 위해 문서 전체를 실패로 처리<br>· 서비스 내부 결함일 가능성이 높으므로 로그와 함께 개발 측에 전달<br>· 청크가 판정 기준에 걸린 것이 아니라 판정 과정 자체의 오류 |

**구분 단서** — 원인은 `reason_code` 로 가릅니다([사유 코드](#사유-코드)). 청커 예외 중 `stage` 가
실리는 것은 **첫 항목**(`stage: custom_fields`)과 **청크 검증**(`stage: chunk_validation`)뿐입니다.

첫 항목은 **파서의 기동 시 검증을 문서형 extractor 가 받지 않아** 청킹까지 흘러온 결과입니다
(아래 검증 범위 차이 참조).

## 청크 검증 판정 기준

청크 검증 예외 2종은 `chunking.validation` 의 `action` 이 `drop` 일 때 발생합니다. 표준 설정과
monimo 설정은 `action: drop` 이라 걸린 청크를 빼고 적재하며, 판정 결과를 로그(`[chunk_validation]`)와
성공 응답의 `chunk_validation` 요약에 남깁니다. 요약 형식은 `chunking_processor.md` 의 "응답 요약"
절을 참조합니다.

| `action` | 판정에 걸린 청크 |
|---|---|
| `report` (키 생략 시) | 로그와 응답 요약에 남기고 그대로 적재. 예외 없음 |
| `drop` (표준·monimo 설정) | 걸린 청크를 빼고 적재하며 응답 요약에 남김. 모든 청크가 걸리면 `CHUNK_ALL_REJECTED` |

청크마다 아래 순서로 검사하며, 앞의 사유에 걸리면 그 사유가 대표 사유입니다.
판정은 `HEADER:` 경로와 문서 접두를 뺀 본문으로 합니다.

| 순서 | 사유 | 판정 (설정 키 = 기본값) | 걸리는 예 | 통과하는 예 |
|---|---|---|---|---|
| 1 | `blank` | 본문이 공백뿐 | 빈 본문, 공백·줄바꿈만 | - |
| 2 | `no_content` | 내용 문자(글자·숫자, 언어 무관) 0자. HTML 태그, 표 구분선(`\| --- \|`), 표 칸 경계(`\|`)는 내용 문자로 세지 않음 | `<div></div>` 10번 반복, `- - - * * * ###` | 일본어·중국어 문장, `<가입대상>`·`0 < 금리 < 5%` 같은 꺾쇠 표기 |
| 3 | `broken_chars` | 깨진 문자(U+FFFD `�`, 제어 문자, `GLYPH<…>`, 잘못된 인코딩으로 바뀐 한글 `占쏙옙`·`ë³´í—˜`) 개수 ≥ `broken_min_count = 3` **그리고** 점유율(깨진 문자 ÷ 공백 제외 문자) ≥ `broken_max_share = 0.3`. `GLYPH<…>` 는 표식 전체 글자 수를 셈 | `���가나다라` (3자, 43%), `占쏙옙占쏙옙 안내` | `��가나다라` (2자), 문장 중간의 `�` 1자, 한자 `占有` |
| 4 | `repetition` | 동일 문자 연속·동일 줄·동일 문구(4~50자) 연속 중 하나가 `repeat_min_count = 10` 회 이상 **그리고** 중복 점유율(반복으로 겹친 문자 ÷ 공백 제외 문자) ≥ `repeat_max_share = 0.5` | `ㅁ` 10자 연속, `처리 중 오류가 발생했습니다. ` 30번, 같은 줄 15번 | `ㅁ` 9자 연속, `-----` 같은 구분선, `10000000000원`(숫자 연속은 세지 않음) |
| 5 | `min_chars` | 내용 문자 수 < 하한. 문서·평문 청크 `min_chars = 4`, 행 청크 `row_min_chars = 0`(검사 안 함) | `1.`, `가나다` | `가나다라`, 행 청크 `카드 > 결제` |

판정을 면제·완화받는 청크는 다음과 같습니다.

- 표가 든 청크는 길이 하한을 적용하지 않습니다(`table_min_chars: true` 로 켤 수 있습니다).
- 코드·수식 청크(docling 라벨 `code`·`formula`)는 길이 하한과 반복 판정을 적용하지 않습니다.
- 행 청크(custom_fields 레코드형이 만든 청크)는 `row_min_chars` 를 씁니다. FAQ·메뉴처럼 짧은 행을 보호하기 위해 기본은 검사하지 않습니다.
- 오디오(`[AUDIO]`)·legacy 표(`[DA]`) 단일 청크는 검증하지 않습니다.
- `by_doc_type` 으로 doc_type 별로 위 기준값을 덮어쓸 수 있습니다.

검사는 청크가 만들어진 직후와 `edit_output` 훅이 끝난 뒤 응답 직전, 두 번 합니다. 훅이 바꾼 본문도
검사합니다. 설정 키 전체와 모드 전환 절차는
[chunking_processor.md 의 이상 청크 검증](chunking_processor.md#이상-청크-검증)을 참조하십시오.

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
| 동명 레코드 배열이 여러 곳에 존재 | `records_at` 지정 시 가장 얕은 곳의 매칭 1개만 사용 (로그 없음) |
| `records_at` 생략 | object 안의 dict 배열이 하나면 그 배열, 여럿이면 `fields` alias 가 더 많이 맞는 배열(동점이면 얕은 쪽), 없으면 object 전체를 1건으로 사용 (배열을 고른 경우만 INFO 로그) |

## 기동 시 검증 범위 차이 (parser)

| 검사 | `rows`/`records`/`sections` | `llm`/`python`/`html_select` |
|---|---|---|
| 모르는 키·오배치 키 | O | O |
| 설정 구조(object/list) | O | v2 스키마 범위만 |
| 목표필드명 예약어·명명규칙 | O | X |
| 값 파이프라인 참조 정합 | O | X |
| 본문 필드(`body.*`) 정합 | O | X |
