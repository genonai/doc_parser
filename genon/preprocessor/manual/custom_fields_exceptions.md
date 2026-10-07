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
| `stage` | `custom_fields` / `metadata` / `doc_summary` / `image_description` / `table_description` / `table_text_description` / `doc_type_stamp` / `enrichment` / `request` / `chunk_validation`. **값이 있을 때만 실립니다** |
| `error_kind` | `timeout` / `transient` / `permanent`. **값이 있을 때만 실립니다** |
| `traceback` | 호출 스택 마지막 8줄 |
| `data` | 실패 시 항상 `null` |

### error_code 는 대부분 `1` 입니다

`GenosServiceException` 은 생성자 첫 인자가 그대로 `error_code` 가 되고 `main.py` 가 그 값을
우선합니다. 따라서 **서비스가 감싸 던진 오류는 `"1"`**(chunker 는 정수 `1`, 청크 검증 예외만 `"1"`)로 나갑니다.
`INPUT_ERROR` / `TIMEOUT_ERROR` / `INTERNAL_ERROR` 로 분류되는 것은 **감싸지 않고 올라온
raw 예외**(마크다운 머리말 오류 등)뿐입니다.

원인 구분은 `error_code` 가 아니라 **`errMsg` 문구와 `stage`** 로 하십시오.

### error_kind 는 세 경우에만 실립니다

- 문서형 extractor 실패가 `error_policy: strict` 로 올라온 경우(`_handle_stage_error`)
- 요청 전체 deadline 초과(`stage: request`)
- 청크 검증 실패(`stage: chunk_validation`, 항상 `permanent`)

레코드형 하드페일과 그 밖의 chunker 예외는 `stage` 만 싣고 `error_kind` 는 실리지 않습니다.
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

- 마크다운 머리말 오류 3종은 감싸지 않고 올라와 **`stage` 가 없고 `error_code` 가 `INPUT_ERROR`** 입니다. 나머지 parser 런타임 예외는 `stage: custom_fields` 를 싣습니다.
- 일시적 실패는 전처리기가 1회만 자체 재호출합니다(`Retry-After` 존중, 최대 10초).
- 표 설명 프롬프트가 `model_context_tokens` 를 넘는 경우도 `ValueError` 로 나지만, 기본 설정은 내용을 나눠 여러 번 호출하므로 발생하지 않습니다.

---

# chunker — 런타임 예외

custom_fields 설정 검증 단계가 없습니다. 파서 산출물을 소비하다 실패하며 전부 `GenosServiceException` 입니다.
청크 검증 실패(`CHUNK_ALL_REJECTED`·`CHUNK_VALIDATION_ERROR`)는 그 하위 클래스인 `ChunkValidationError` 라 코드서빙 응답의
`error_type` 이 `ChunkValidationError` 입니다(응답의 나머지 필드는 같습니다).
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
| `Cancelled` | 요청 처리 중 호출한 쪽이 연결을 끊어 처리를 중단<br>· 호출 측 대기 시간 만료 또는 요청 취소<br>· 서버 오류가 아니므로 호출 측 타임아웃 설정을 확인 |

**구분 단서** — 청커 예외 중 `stage` 가 실리는 것은 **첫 항목**(`stage: custom_fields`)과
**청크 검증 2종**(`stage: chunk_validation`)뿐입니다. `stage` 값으로 "파서 설정 문제 / 청크 검증 /
청커 입력 문제" 가 갈립니다.

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
