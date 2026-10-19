# 모니모 파싱·청킹 호출부 오류 처리 안내

모니모 코드서빙의 `/parser`·`/parser_upload`·`/chunker` 를 호출하는 쪽(적재 파이프라인, Temporal activity 등)을 위한
문서입니다. 응답을 받았을 때 무엇을 보고, 다시 시도할지, 누구에게 넘길지를 정리합니다.

사유 코드 전체 목록과 오류 메시지별 발생 조건은 [custom_fields_exceptions.md 의 사유 코드](custom_fields_exceptions.md#사유-코드)가
정본입니다. 이 문서는 그 코드표를 호출부의 판단 기준으로 묶어 설명합니다.

---

## 1. 응답 판정 순서

1. **HTTP 응답을 받지 못했으면**(연결 실패·끊김) 연결 장애로 보고 정책에 따라 재시도합니다.
2. **HTTP 상태가 200이 아니면** 전처리기의 공통 응답 봉투를 가정하지 않고 상태와 본문을 그대로 보존합니다.
   - 404·405 처럼 경로·메서드가 잘못된 호출은 재시도해도 해결되지 않으므로 호출부 수정 대상으로 넘깁니다.
   - 게이트웨이의 일시 장애 상태(502·503·504 등)만 정책에 따라 재시도합니다.
3. **HTTP 200이면 `code` 를 봅니다.** `0` 이면 성공, `1` 이면 실패입니다. 전처리기는 업무 실패와 요청 본문 형식 오류(`COMMON_P002`)도
   HTTP 200으로 돌려줍니다. `code` 가 없거나 다른 값이면 응답 규약 오류로 따로 기록합니다.
4. 실패면 아래 세 필드로 판단합니다.
   - `error_kind`: 재시도 여부(`transient` / `timeout` / `permanent`)
   - `error_category`: 넘길 곳(`data` / `preprocess` / `system`)
   - `reason_code`: 원인(예: `PARSE_D001`). 기록과 원인 분석에 씁니다
5. 성공이어도 최상위에 `chunk_validation` 이 있으면 제외·누락 사실을 기록합니다([4장](#4-성공이지만-확인이-필요한-경우)).

```python
def judge(http_status: int | None, body: dict | None) -> str:
    if http_status is None:
        return "retry_connection"              # HTTP 응답 자체를 받지 못함
    if http_status != 200:
        if http_status in (502, 503, 504):
            return "retry_connection"
        return "escalate:caller"               # 404·405 등 호출 규약 오류, 상태·본문 보존
    code = (body or {}).get("code")
    if code == 0:
        return "ok"                            # body.get("chunk_validation") 은 별도로 기록
    if code != 1:
        return "escalate:protocol"             # 전처리기 응답 규약을 벗어난 응답
    kind = body.get("error_kind")
    if kind in ("transient", "timeout"):
        return "retry"
    if kind is None:
        return "escalate:preprocess"           # 3장 참조
    return "escalate:" + (body.get("error_category") or "preprocess")
```

## 2. 실패 응답 필드

```json
{
  "code": 1,
  "errMsg": "[parser] GenosServiceException: 필수 Excel 컬럼 매핑 실패(sheet=FAQ): ['질문']; available=['번호', '답변', '카테고리']",
  "error_msg": "(errMsg 와 같음)",
  "error_code": "1",
  "error_type": "GenosServiceException",
  "tag": "parser",
  "file_path": "/data/faq.xlsx",
  "data": null,
  "traceback": "...(마지막 8줄)",
  "stage": "custom_fields",
  "error_kind": "permanent",
  "reason_code": "PARSE_D001",
  "error_category": "data"
}
```

| 필드 | 실리는 조건 | 호출부 용도 |
|---|---|---|
| `code` | 항상 | 성공·실패 판정 |
| `reason_code` | 항상 | 원인 기록·분석. 원인별 분기가 필요하면 이 값으로 작성합니다 |
| `error_category` | 항상 | 담당 부서 결정 |
| `errMsg` · `error_msg` | 항상 | 사람이 읽는 메시지. 예외로 생긴 실패는 `[엔드포인트] 예외이름: 메시지` 형식이고, 미지원 API(`COMMON_P001`)는 안내 문장만 실립니다 |
| `error_code` | 항상 | **분기 조건으로 쓰지 않습니다**(아래) |
| `error_kind` | 값이 있을 때만 | 재시도 여부. 없을 때의 처리는 [3장](#3-재시도와-담당-정책) |
| `stage` | 값이 있을 때만 | 실패 단계(`custom_fields`, `enrichment`, `chunk_validation`, `request` 등). 기록용 |
| `tag` · `file_path` · `traceback` · `error_type` | 예외로 생긴 실패. 미지원 API(`COMMON_P001`) 응답에는 없습니다 | `tag` 는 실패한 엔드포인트입니다. 요청 본문 검증 오류는 `app`, `/parser_upload` 의 `params` JSON 오류는 `parser_upload` 입니다. `traceback` 은 전처리 운영자에게 넘길 때 함께 전달합니다 |

### `error_code` 를 쓰지 않는 이유

같은 성격의 실패가 문자열 `"1"`, 정수 `1`(청커·미지원 API), `INPUT_ERROR`·`TIMEOUT_ERROR`·`INTERNAL_ERROR`
로 섞여 나옵니다. 기존 호출부와의 호환을 위해 값을 바꾸지 않았을 뿐이며 원인을 나타내지 않습니다.
`error_type` 은 예외 클래스 이름이라 같은 클래스가 여러 원인에 쓰입니다.

메시지 문자열로 분기하는 것도 피합니다. 메시지는 원인 설명을 보강하면서 바뀔 수 있습니다.

## 3. 재시도와 담당 정책

### 재시도

재시도 여부는 **응답에 실린 `error_kind` 를 우선**합니다. 사유 코드와 `error_kind` 는 따로 정해지므로, 아래 코드 열은
일반적인 대응일 뿐입니다.

| `error_kind` | 일반적으로 해당하는 코드 | 조치 |
|---|---|---|
| `transient` | `PARSE_S101`·`PARSE_S103`·`CHUNK_S103` | 간격을 늘려 가며 재시도합니다 |
| `timeout` | `PARSE_S102`·`COMMON_S102` | 재시도할 수 있습니다. 같은 입력이 반복해서 걸리면 `request_deadline` 과 입력 크기를 검토합니다 |
| `permanent` | 사유 코드가 정해진 D·P 코드, `S0xx`, `S2xx` | 재시도하지 않습니다. 같은 입력이면 결과가 같습니다 |
| (없음) | `COMMON_P001`, `COMMON_P002`, 일부 `*_P999` | **재시도하지 않고** 전처리 운영자에게 넘깁니다(아래) |

- 공용 LLM 호출은 429·502·503·504·연결 오류에 기본 최대 1회 자체 재호출을 합니다. 다만 남은 시간이 부족하면
  재호출하지 않을 수 있습니다.
- `*_P999` 는 **사유 코드를 분류하지 못했다**는 뜻이며 `error_kind` 의 유무와는 별개입니다. `P999` 에 `timeout` 이나
  `permanent` 가 함께 실리면 그 `error_kind` 를 따릅니다. `P999` 가 반복되면 코드 분류를 보강할 대상이므로 전처리 운영자에게 알립니다.
- `error_kind` 가 없는 실패는 미분류 오류이거나 호출 규약 위반(`COMMON_P001` 미지원 API, `COMMON_P002` 요청 형식 오류)입니다.
  다시 보내서 해결될 가능성이 낮으므로 `permanent` 와 같이 처리하고, `errMsg`·`traceback` 을 함께 전달합니다.

### 넘길 곳

| `error_category` | 분류문자 | 뜻 | 넘길 곳 |
|---|---|---|---|
| `data` | D | 원천 파일의 내용·구조가 문서유형 설정의 기대와 다름 | 원천 데이터 제공 측. 원천 형식이 정당하게 바뀐 것이면 전처리 운영자가 문서유형 설정을 고칩니다 |
| `preprocess` | P | 전처리 설정 오기입, 처리 로직 실패, 호출 규약 위반 | 전처리 운영자 |
| `system` | S | 실행 환경·파일시스템·네트워크·외부 서비스 | 인프라 운영자 |

시스템 코드의 번호대는 `S0xx` 환경·권한·접속 설정, `S1xx` 네트워크·시간 초과·입출력 일시 장애, `S2xx` 외부 서비스
응답 이상입니다.

## 4. 성공이지만 확인이 필요한 경우

`code: 0` 이어도 결과가 온전하지 않을 수 있습니다.

| 상황 | 응답 | 호출부 조치 |
|---|---|---|
| 일부 청크가 품질 검증에 걸림 | `chunk_validation.count`·`reasons`·`items` | 모니모 설정(`action: drop`)은 걸린 청크를 `data` 에서 빼고 돌려줍니다. 제외 건수를 적재 이력에 남깁니다 |
| 파서가 넘긴 메타가 청크에 실리지 않음 | `chunk_validation.missing_fields` | 청크는 그대로 적재됩니다. 반복되면 전처리 운영자에게 알립니다 |
| 청크가 글자 수 상한을 넘음 | `chunk_validation.max_length` | 청크는 그대로 적재됩니다. 모니모 설정은 상한(`max_chars`)을 넣지 않아 현재는 나오지 않습니다 |
| LLM 보강 실패(문서 요약, 표 설명, custom_fields LLM 추출 등) | 기본 정책(`error_policy: lenient`)에서는 성공 응답이며, 값이나 설명이 비거나 일부만 남을 수 있습니다. **응답에 따로 표시되지 않고** 로그에만 남습니다 | 적재에 꼭 필요한 필드는 호출부에서 값이 있는지 검사합니다. 실패 응답으로 받는 방법은 바로 아래에 있습니다 |

### 보강 실패를 실패 응답으로 받으려면

요청 `params` 에 `error_policy: strict` 를 넣으면 문서 요약·custom_fields 등 보강 단계의 상위 오류가 `stage`·`error_kind`·`reason_code`
를 실은 실패 응답으로 옵니다. 다만 아래는 strict 에서도 실패 응답이 되지 않습니다.

- 표 설명 생성 실패: 경고를 남기고 건너뜁니다.
- `rows`·`records` 문서유형의 건별 LLM 생성 필드: 설정의 `on_error` 정책을 따릅니다.
- `sections` 문서유형의 문서 단위 LLM 생성 필드: 호출이 실패하면 그 필드를 null 로 두고 섹션 처리를 계속합니다.
- custom_fields 문서형 추출기(`llm`·`python`·`html_select`) 내부에서 처리한 실패: 추출값은 비어 있을 수 있으나
  기본값·상수·front matter 처럼 LLM 과 무관한 값은 채워진 채 진행합니다.

### 응답 예시

```json
{"code": 0, "errMsg": "success", "data": [ ... ],
 "chunk_validation": {"action": "drop", "count": 3, "reasons": {"min_chars": 2, "repetition": 1},
  "items": [{"index": 7, "page": 4, "reason": "min_chars",
             "message": "본문이 지나치게 짧음(글자·숫자 1자, 최소 4자)", "preview": "1."}]}}
```

`chunk_validation` 은 위 세 항목 중 하나라도 있을 때만 붙습니다. 필드 설명과 판정 기준은
[chunking_processor.md 의 응답 요약](chunking_processor.md#응답-요약)에 있습니다. 모든 청크가 걸리면 성공이 아니라
실패 응답(`CHUNK_D002`, `stage: chunk_validation`)입니다.

## 5. 시간 제한과 연결 끊김

- `params.request_deadline`(초, 양수)은 전처리기가 문서를 처리하는 구간의 시간 상한입니다. `/parser_upload` 의 업로드 수신과
  임시 파일 저장은 포함하지 않습니다. 지정하지 않거나 0 이하·숫자가 아닌 값이면 이 상한을 적용하지 않습니다.
- 상한 초과가 감지되면 `COMMON_S102`(`error_kind: timeout`, `stage: request`)로 응답합니다. 파일 변환처럼 동기로 실행되는 구간은
  즉시 중단되지 않으므로 응답이 상한보다 늦게 올 수 있습니다. 호출부의 HTTP 타임아웃은 `request_deadline` 보다 넉넉하게 설정합니다.
- 전처리기에는 호출부의 연결 끊김을 감지해 처리를 멈추는 기능이 없습니다. 끊은 직후 다시 보내면 같은 문서를 동시에
  두 번 처리할 수 있으므로, 재시도 간격을 두거나 호출부 타임아웃을 늘립니다.

## 6. 파서에서 청커로 넘기는 계약

```
원본 문서 ──POST /parser──▶ 파싱 결과 ──POST /chunker──▶ 청크 목록
```

- 파서 응답이 `code: 0` 인지 먼저 확인합니다. 실패한 파서 응답을 청커에 넘기지 않습니다.
- 파서 응답의 `data` 를 **변형하지 않고** 청커 요청의 `params.document` 에 그대로 넣고, `params.doc_type` 은 파서에 보낸 값과 같게 둡니다.
- 파서 응답의 형태는 문서유형에 따라 두 가지입니다.
  - 문서 경로(card, product_slf 등): 모니모 설정 `format: docling` 에 따라 본문이 `data.document` 에 실립니다.
    이때 `data.elements` 는 정상 문서에서도 빈 목록이므로 **`elements` 가 비었다는 이유로 문서를 거르지 않습니다.**
  - 행·레코드 경로(menu, term, faq, cs_* 등): `format` 설정과 관계없이 `data.elements` 로 응답하며 `data.document` 는 없습니다.
    필수값을 채우지 못한 레코드는 건너뛰므로 파서가 성공하고도 `elements` 가 빈 목록일 수 있습니다. 이 응답을 청커에 넘기면
    `CHUNK_D001` 로 실패하므로, `document` 가 없고 `elements` 가 빈 응답은 적재할 본문이 없는 것으로 보고 원천 제공 측에 확인합니다.
- 인라인 `document` 를 넘기면 `file_path` 는 원본 식별·메타데이터 용도입니다. 인라인 입력이 없고 `file_path` 가 `.json` 으로
  끝나면 청커는 **전처리기 서버에 있는 파서 결과 파일**을 읽습니다.

```python
parsed = parser_body["data"]
if "document" not in parsed and not parsed.get("elements"):
    ...  # 적재할 본문 없음. 청커를 호출하지 않는다
chunk_request = {"file_path": original_path,
                 "params": {"doc_type": doc_type, "document": parsed}}
```

아래 코드는 대부분 이 계약을 어겼을 때 나옵니다. 원천이나 전처리 설정보다 호출부 코드를 먼저 확인합니다.

| 코드 | 뜻 | 확인할 것 |
|---|---|---|
| `CHUNK_P002` | 사용할 입력이 없음 | 인라인 `document` 를 넣었는지. `file_path` 가 `.json` 이면 이 코드 대신 아래 파일 오류가 먼저 날 수 있습니다 |
| `CHUNK_S003`·`CHUNK_S103` | 서버의 입력 파일을 읽지 못함 | 인라인 `document` 를 빠뜨려 `file_path`(`.json`)를 서버 파일로 읽으려 하지 않았는지 |
| `CHUNK_P003` | 파서 결과가 아닌 입력 | 원본 문서나 다른 형식의 JSON 을 넣지 않았는지 |
| `CHUNK_P004` | 파서 결과 손상 | 저장·전달 중에 JSON 이 잘리거나 변형되지 않았는지, 파서와 청커의 배포 버전이 같은지 |
| `CHUNK_D001` | 청크로 만들 본문 없음 | 행·레코드 경로의 빈 `elements` 응답을 넘기지 않았는지 |

## 7. 모니모 문서유형별로 나올 수 있는 코드

모니모 설정 기준으로 원천데이터(D)·전처리(P) 코드 중 실제로 나올 수 있는 것입니다.

| 문서유형(`doc_type`) | 입력 형태 | 나올 수 있는 코드 |
|---|---|---|
| menu, term | 엑셀 행 | `PARSE_D001` 필수 열 부재(시트를 지정하지 않아 모든 시트를 읽으므로 필수 열이 없는 시트가 하나라도 있으면 발생), `PARSE_D004` 열 이름 중복. 설정에 `source.sheets` 를 적으면 `PARSE_D013` 지정 시트 없음도 나옵니다 |
| faq, cs_slf, monimo_event | JSON 레코드(배열 위치 지정) | `PARSE_D010` JSON 문법 손상, `PARSE_D002` 지정한 레코드 배열 부재 |
| cs_hpp, cs_sss | JSON 레코드(배열 위치 자동 선택) | `PARSE_D010`, `PARSE_D002`, `PARSE_P005` 레코드 배열 후보를 구별할 수 없음 |
| cs_ssf, stock_insight | 구분자로 나뉜 레코드 | `PARSE_D011` 비어 있지 않은 원천에서 지정 구분자로 레코드를 얻지 못함 |
| product_hpp | 중첩 JSON 섹션 | `PARSE_D010`, `PARSE_D003` 필수 공통 필드 공백 |
| product_slf, product_ssf | 마크다운 | `PARSE_D012` 섹션 지정 후 본문 없음 |
| card, research_report, monimo_news | 본문 추출용 JSON | `PARSE_D010`. JSON 문법은 정상이고 지정한 본문 키만 없으면 경고 후 진행합니다 |
| 모든 문서유형 | — | `PARSE_D009` 빈 파일·손상·암호화 파일(파일 머리 검사로 판별되는 경우), `PARSE_P002` 등록되지 않은 doc_type |

- 등록되지 않은 `doc_type` 은 모니모 설정(`unknown_doc_type: fail`)에서 실패합니다. 오타를 먼저 확인합니다.
- 목차·메타데이터 LLM 추출은 모니모 설정에서 꺼져 있지만, LLM 관련 사유 코드(`PARSE_P001`, `PARSE_S001`·`S101`·`S102`·`S201`)가
  그때만 나오는 것은 아닙니다. 요청 `params` 로 이 기능을 켜거나 `error_policy: strict` 를 쓰면 다른 보강 단계의 LLM 실패도
  실패 응답으로 올 수 있습니다([4장](#4-성공이지만-확인이-필요한-경우)).
- 시스템 코드는 문서유형과 관계없이 나올 수 있습니다. `PARSE_S002`(변환 도구 누락), `PARSE_S003`·`CHUNK_S003`(파일 없음·권한),
  `PARSE_S103`·`CHUNK_S103`(파일 입출력 일시 장애), `COMMON_S102`(요청 시간 초과)입니다.
- 청크 검증 실패(`CHUNK_D002` 모든 청크 불량, `CHUNK_P005` 판정 중 오류)는 품질 검증을 거치는 청크에 적용됩니다.
  `CHUNK_P001` 은 행 기반 파서 산출물(menu·term·faq 등 레코드형)의 메타데이터 타입 검증 실패입니다.

## 8. 전처리 운영자에게 넘길 때 함께 전달할 것

응답에는 요청 식별자가 없습니다. 전처리기 로그와 맞춰 보려면 아래를 함께 전달합니다.

- 엔드포인트, 요청 시각(초 단위), `file_path`, `params.doc_type`
- 응답 전체(`reason_code`, `errMsg`, `traceback` 포함)
- 원천 파일 또는 파서 결과(청커 실패일 때)
