# 코드 서빙(Code Serving) 사용자 매뉴얼

doc_parser 전처리기를 GenOS **코드 서빙** 플랫폼에 배포하면 **전처리기 Temporal 워커**가 뜹니다.
문서 **파싱(`parse`)** 과 **청킹(`chunk`)** 은 HTTP 가 아니라 **Temporal 액티비티**로 호출합니다.
이 문서는 배포된 코드 서빙을 **기동·호출·확인**하는 방법을 설명합니다.

> **개발환경 세팅 · 코드 이해 · 수정 · 재배포**는 별도 문서인
> [Genos 코드서빙 전처리기 개발 매뉴얼](code_serving_dev_manual.md)에 있습니다
> (Genos 를 처음 접하는 개발자용. parser·chunker 코드 수정 중심).

> #577 에서 코드서빙 HTTP 업무 경로(`/preprocess*`·`/parser`·`/chunker`)를 없앴습니다. 적재/첨부/변환
> 파사드(intelligent·attachment·convert)는 GenOS 전처리기(기본 전처리기 서비스 `genon/preprocessor/src/main.py`)에
> 올려 `POST /run` 으로 씁니다 — [installation.md](installation.md) 참고.

## 목차

- [개요](#개요)
- [사전 준비](#사전-준비)
- [HTTP 엔드포인트](#http-엔드포인트)
- [파싱·청킹 호출 — Temporal 액티비티](#파싱청킹-호출--temporal-액티비티)
- [단건 확인 — CLI](#단건-확인--cli)
- [LLM 캐시 / 실패 정책 / 요청 deadline (#329)](#llm-캐시--실패-정책--요청-deadline-329)
- [설정 참고](#설정-참고)
- [주의사항 / FAQ](#주의사항--faq)

## 개요

- **코드 서빙 = 전처리기 Temporal 워커 하나**입니다. 리비전 시작 명령 `python -m genon.preprocessor.worker` 로 뜹니다.
- 한 프로세스 안에서 uvicorn 이 HTTP(`/health`·`/healthcheck`·`/version`)만 받고, 같은 프로세스의 감시자가
  **자식 프로세스 `WORKER_PROCESSES` 개**를 spawn 으로 띄웁니다. 자식마다 Temporal 워커 1개가
  **`WORKER_THREADS` 스레드**로 `WORKER_TASK_QUEUE` 에서 액티비티를 받습니다(동시 문서 수 = N × M).
- 자식이 죽으면 그 자식만 다시 띄웁니다. 300초 안에 5번을 넘게 죽으면 전체를 내리고 시작 명령이 1 로 끝나며,
  supervisord(PID1)가 시작 명령을 다시 실행합니다.
- 액티비티는 `genon/preprocessor/activities/` 의 폴더 이름(slug)으로 등록됩니다. **폴더 이름 = slug = 액티비티 이름,
  첫 단어 = 단계**입니다. `parse`·`chunk`(원형)와 `parse_…`·`chunk_…`(Studio 사본) 중 `processor.py`·`config.yaml`
  이 있는 폴더만 등록됩니다.
- 파싱과 청킹은 **분리된 2단계**입니다.
  - **파싱(`parse`)**: 원본 문서(PDF/HWP/DOCX 등)를 읽어 파싱 결과 JSON 을 산출물 자리에 씁니다.
    포맷에 따라 DoclingDocument 또는 parse-format(`{"elements": [...]}`)입니다.
  - **청킹(`chunk`)**: 파싱 결과 JSON 을 읽어 벡터 적재용 **청크 리스트**(`GenOSVectorMeta`)를 씁니다.
    청커가 입력 형태를 자동 판별합니다.

처리 흐름(E2E — 호출자는 보통 data-ingestion-pipeline 워커):

```
원본 문서(NFS_ROOT)         파싱 결과(ARTIFACT_ROOT/<ref>.json)       청크 리스트(ARTIFACT_ROOT/<ref>.json)
read_ref ──[parse 액티비티]──▶  DoclingDocument | {elements}  ──[chunk 액티비티]──▶  [ {...}, ... ]
```

> 청킹 단계는 파싱 결과만 입력으로 받습니다. OCR·레이아웃·enrichment 등 무거운
> 처리는 모두 파싱 단계에서 끝나므로, 청킹은 가볍고 빠르게 반복 호출할 수 있습니다.
> docling 포맷(pdf/html/htm/docx/hwp/hwpx)은 구조 인식 청킹(GenosSmartChunker)을, 그 외
> 비-docling 포맷은 parse-format 공통 청킹(audio→`[AUDIO]` 단일, csv/xlsx→행마다 청크 1개,
> 그 외 텍스트→문자 기반 splitter)을 수행합니다.

## 사전 준비

**리비전 시작 명령(START_COMMAND) — 필수**

```
python -m genon.preprocessor.worker
```

시작 명령이 비어 있으면 base 이미지가 루트 `main.py` 를 띄우고, 그 파일은
"시작 명령(START_COMMAND)이 비어 있습니다" 기동 오류만 내고 끝납니다.

**리비전 env** (`genon/preprocessor/worker/settings.py`)

| env | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `TEMPORAL_HOST` | 필수 | — | Temporal frontend 주소 |
| `TEMPORAL_PORT` | | `7233` | |
| `TEMPORAL_NAMESPACE` | | `ns-ifa-app` | 파이프라인과 같은 기본값 |
| `WORKER_TASK_QUEUE` | 필수 | — | 이 워커가 폴링할 큐. 파이프라인의 `DOC_PARSER_TASK_QUEUE` 와 같아야 합니다 |
| `WORKER_PROCESSES` | | `1` | 워커 프로세스 수(N). 메모리는 프로세스 수에 비례합니다 |
| `WORKER_THREADS` | | `1` | 프로세스마다 동시에 처리할 문서 수(M) |
| `NFS_ROOT` | 필수 | — | `read_ref.root="nfs"`(원본·업로드 하드링크)를 여는 루트 |
| `ARTIFACT_ROOT` | 필수 | — | 산출물 ref 와 `read_ref.root="artifact"` 를 여는 루트. 파이프라인 `INTERIM_ROOT` 와 같은 저장소를 이 파드에 마운트한 경로 |
| `LLM_CACHE_ROOT` | | (없음) | `params.llm_cache` 가 켜진 요청의 LLM 캐시 루트. 없으면 캐시를 쓰지 않습니다 |
| `PREVIEW_CHARS` | | `2000` | 반환 `preview` 길이 |
| `HEARTBEAT_INTERVAL` | | `10` | 하트비트 간격(초) |
| `PORT` | | `8080` | `/health`·`/version` 포트 |
| `OMP_NUM_THREADS` | | 자동 | 없으면 "파드 CPU 한도 ÷ 프로세스 합"의 내림(최소 1)으로 정해 자식에 물려줍니다(#514) |
| `GENOS_MODEL_PRESETS_FILE` | | | 모델 접속 실값 파일 |

- 필수 env 가 하나라도 없으면 기동 때 이름을 모두 적은 오류로 멈춥니다.
- `INTERIM_ROOT` 는 쓰지 않습니다(전처리기에서 그 이름은 LLM 캐시 루트라 헷갈립니다 — `LLM_CACHE_ROOT` 를 씁니다).
- docling 포맷 파일(pdf/html/htm/docx/hwp/hwpx)은 `activities/parse/config.yaml` 이 **`output.format: "docling"`**
  이어야 파싱 결과가 DoclingDocument 로 저장됩니다. 그 외 비-docling 포맷은 항상 parse-format(`elements`)입니다.

## HTTP 엔드포인트

HTTP 는 코드서빙 계약용 둘뿐입니다(`genon/preprocessor/worker/app.py`). 게이트웨이로 부를 때 URL 은
`{base}/api/gateway/code_serving/{serving_id}/{route}`, 헤더는 `Authorization: Bearer {auth_key}` 입니다.

| 메서드 | 경로 | 응답 |
| --- | --- | --- |
| `GET` | `/health` (`/healthcheck` 별칭) | 자식 프로세스가 모두 살아 있으면 `200 {"status":"ok"}`, 아니면 `503 {"status":"down"}`. 코드서빙 startupProbe |
| `GET` | `/version` | 배포 스탬프(`VERSION`) · `UPDATED_AT` 첫 줄 · 기동 시각 |

```bash
GW="https://<GENOS_HOST>/api/gateway/code_serving/<SERVING_ID>"
curl -s "${GW}/health"  --header "Authorization: Bearer <AUTH_KEY>"
curl -s "${GW}/version" --header "Authorization: Bearer <AUTH_KEY>"
```

> 코드서빙 startupProbe(`GET /health :8080`)는 기동 때 한 번 200 을 받으면 그 뒤로는 보지 않습니다.
> `/health` 는 워커가 Temporal 에 붙었는지까지는 보지 않습니다 — 자식 프로세스가 살아 있는지만 봅니다.

## 파싱·청킹 호출 — Temporal 액티비티

워크플로우에서 `WORKER_TASK_QUEUE` 큐에 slug 이름으로 액티비티를 겁니다. 인자는 dict 하나, 반환은 요약 dict 입니다
(페이로드 2MB 한계 — 결과 본문은 산출물 파일로만 오갑니다). 파드 사이에는 **상대 참조만** 넘기고, 루트는 워커 파드의
env 로 해석합니다(`worker/paths.py`). 절대경로나 `..` 로 루트 밖을 가리키면 거부합니다.

**액티비티 이름**: `parse`·`chunk`, 그리고 `activities/` 에 있는 사본 slug(`parse_…`·`chunk_…`).
기동 로그 `액티비티 N개: chunk(chunk), parse(parse), …` 에 등록된 이름이 찍힙니다.

**인자** (`worker/runtime.py`)

| 단계 | 인자 | 설명 |
| --- | --- | --- |
| parse | `read_ref` | 읽을 원본. `{"root": "nfs" \| "artifact", "path": "<루트 기준 상대경로>"}` |
| parse·chunk | `output_ref` | 산출물 자리 `<문서키>/<name>` → `ARTIFACT_ROOT/<문서키>/<name>.json` |
| chunk | `parsed_ref` | 파싱 결과 ref(`ARTIFACT_ROOT/<parsed_ref>.json`). 청커에 `params.document` 로 넘어갑니다 |
| parse·chunk | `source_name` | 선택. 원본 이름 — 로그와 오류 메시지 앞에 붙습니다 |
| parse·chunk | `params` | 선택. 처리기 파라미터(`chunk_size`·`doc_type` 등) + 전처리 정책(`llm_cache`·`workflow_id`·`error_policy`·`request_deadline`) |

```python
# 워크플로우 안에서 — 액티비티 이름(slug)·큐로 건다. 타임아웃 값은 예시다
parsed = await workflow.execute_activity(
    "parse",
    {"read_ref": {"root": "nfs", "path": "docs/report.pdf"},
     "output_ref": "<문서키>/parse",
     "source_name": "docs/report.pdf",
     "params": {"error_policy": "strict"}},
    task_queue="<WORKER_TASK_QUEUE>",
    start_to_close_timeout=timedelta(hours=1), heartbeat_timeout=timedelta(minutes=2))
chunked = await workflow.execute_activity(
    "chunk",
    {"parsed_ref": parsed["ref"], "output_ref": "<문서키>/chunk", "params": {"chunk_size": 0}},
    task_queue="<WORKER_TASK_QUEUE>",
    start_to_close_timeout=timedelta(hours=1), heartbeat_timeout=timedelta(minutes=2))
```

**반환값** (요약 dict)

```json
{"stage": "parse", "activity": "parse", "ref": "<문서키>/parse", "bytes": 123456, "count": 87,
 "summary": "파싱 120KB · 요소 87", "preview": "본문 앞부분 …", "duration_ms": 41230}
```

- `ref` 는 쓴 산출물 자리(`output_ref` 와 같다), `count` 는 parse 면 요소 수(`elements` 또는 `texts`), chunk 면 청크 수입니다.
- `preview` 는 `PREVIEW_CHARS` 자까지 자릅니다. `GENOS_PROFILE=1` 이면 하위 단계 시간 `stages` 가 함께 실립니다.
- 저장 모양: parse 는 처리기 결과에 `document` 가 있으면 그것(DoclingDocument), 없으면 `elements` 를 가진 결과 전체.
  chunk 는 청크 배열입니다.
- 워커는 처리 중 `HEARTBEAT_INTERVAL` 초마다 하트비트를 칩니다. 호출 측은 `heartbeat_timeout` 을 거는 것이 좋습니다.
- 청크 검증 요약(`chunk_validation`, [chunking_processor.md](chunking_processor.md))은 반환값에 실리지 않습니다 — 로그의
  `[chunk_validation]` 줄로 봅니다.

**실패** — 모든 실패는 `ApplicationError` 로 올라옵니다(`worker/errors.py`). 메시지는 `<slug> <source_name 또는 output_ref>: <예외 타입>: <사유>` 입니다(프로세서 준비 중 실패면 `<slug> 프로세서 준비: …`).

| `type` | `non_retryable` | 언제 |
| --- | --- | --- |
| `permanent` | `true` | 처리기가 `error_type="permanent"` 로 던진 것, 입력 오류(`ValueError`·`KeyError`·`FileNotFoundError`·권한 등), 경로·용량 `OSError` |
| `transient` | `false` | 처리기가 `error_type="transient"` 로 던진 것, NFS 순단 errno(`ESTALE`·`EIO`·`ETIMEDOUT` 등) |
| `timeout` | `false` | `request_deadline` 초과, 처리기 안의 `TimeoutError` |
| `worker_restarting` | `false` | 처리 스레드를 멈출 수 없어 재기동을 기다리는 프로세스가 받은 작업 — 재시도로 돌려보냅니다 |
| 그 밖의 예외 이름 | `false` | 분류되지 않은 예외 |

> 취소·마감 초과로 처리 스레드가 남으면(동기 파싱은 중간에 멈출 수 없다) 그 자식 프로세스는 돌던 작업이 모두
> 끝난 뒤 스스로 끝나고, 감시자가 새로 띄웁니다. 사이트 배포인데 그 slug 의 사이트 설정이 없으면 워커는 뜨되
> 그 slug 호출마다 `permanent` 로 실패합니다(#522·#561).

## 단건 확인 — CLI

Temporal 서버 없이 **워커와 같은 본문**(등록부의 그 slug 래퍼)을 한 건 돌립니다. 인자 해석·설정·저장·오류 분류가
큐 왕복과 같습니다(`genon/preprocessor/activities/__main__.py`). 저장소(배포본) 루트에서 실행합니다.

```bash
# 파싱: 원본 문서 → 파싱 결과 JSON (-o 없으면 ./<파일명>.<slug>.json)
python -m genon.preprocessor.activities run --activity parse --input sample_files/pdf_sample.pdf -o /tmp/doc.json

# 청킹: 파싱 결과 JSON → 청크 배열 JSON
python -m genon.preprocessor.activities run --activity chunk --input /tmp/doc.json \
  --params '{"chunk_size": 0}' -o /tmp/chunks.json

# 문서 유형 지정(params.doc_type 보다 우선)
python -m genon.preprocessor.activities run --activity parse --input a.pdf --doc-type <유형>
```

- 진행 로그는 stderr, stdout **마지막 줄**이 결과입니다:
  `@@RESULT@@ {"ok": true, "stage", "activity", "duration_ms", "data": {…반환값…, "output": "<결과 파일>"}}`.
  실패면 `"ok": false, "error": {"type", "message", "non_retryable"}` 이고 exit 1 입니다.
- 입력은 임시 루트에 복사해 상대 참조로 넘기므로 원본 폴더에 아무것도 쓰지 않습니다.
- `LLM_CACHE_ROOT` env 가 있으면 워커와 같게 LLM 캐시 루트로 씁니다.

큐 왕복(실제 워커 진입점·동시 실행)은 smoke 시험 `genon/preprocessor/tests/smoke/test_worker_concurrency_smoke.py`
가 봅니다(`TEMPORAL_TEST_TARGET=local` 기본은 SDK 개발 서버를 내려받는다 — 인터넷 필요, `scp` 는 배포 환경 Temporal 의
시험 전용 큐). 배포된 워커 파드의 동시 처리 측정은 `genon/preprocessor/tools/code_serving/worker_concurrency_test.py` 입니다.

## LLM 캐시 / 실패 정책 / 요청 deadline (#329)

액티비티 `params` 로 opt-in 하는 공통 옵션입니다. 대용량 배치 중 문서가 중간 실패해도 그때까지 성공한 LLM 호출을
캐시해 재시도 시 재사용하고, 실패/행잉을 액티비티 실패로 돌려줍니다. **미지정 시 캐시·strict·상한 없이 동작**합니다.
파이프라인은 `DOC_PARSER_LLM_CACHE`·`DOC_PARSER_ERROR_POLICY`·`DOC_PARSER_TIMEOUT` 설정에 따라
`llm_cache`·`workflow_id`·`error_policy`·`request_deadline` 을 실어 보냅니다(`app/pipeline/stepargs.policy`).

| 파라미터 | 위치 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `llm_cache` | `params` | `false` | `true` 일 때만 LLM 호출 입출력을 파일 캐시(0/1 표기도 수용) |
| `workflow_id` | `params` | (없음) | 캐시 스코프. **없으면 캐시 비활성** |
| `run_id` | `params` | `"default"` | 캐시 스코프 |
| `error_policy` | `params` | `"lenient"` | `"strict"` 면 enrichment 실패를 액티비티 실패로 올림 |
| `request_deadline` | `params` | (없음) | 처리 전체 상한(초). 초과 시 `timeout` 실패 |

- 캐시 활성 조건(워커): `llm_cache` **AND** `workflow_id` **AND** 워커 env `LLM_CACHE_ROOT`.
  `params.interim_root` 는 다른 파드의 절대경로라 워커가 버리고, `LLM_CACHE_ROOT` 로 채웁니다. `LLM_CACHE_ROOT` 가
  없으면 `llm_cache` 를 지워 캐시 없이 돕니다. 경로는 `<LLM_CACHE_ROOT>/<workflow_id>/<run_id>/llm_cache/<key>.json`.
- chunk 는 `interim_ref`(=`"<workflow_id>/<run_id>"`)로도 스코프를 유도할 수 있습니다
  (청킹은 LLM 호출이 없어 실질 no-op).

요청 예시(parse 액티비티 인자):
```json
{
  "read_ref": {"root": "nfs", "path": "docs/report.pdf"},
  "output_ref": "<문서키>/parse",
  "params": {"llm_cache": true, "workflow_id": "wf-123", "run_id": "run-1"}
}
```

### 캐시 키(`<key>`) 생성 방식

LLM 호출 1건 = 파일 1개(`<key>.json`). 키는 **그 호출의 지문(fingerprint)** 으로, 엔드포인트와
요청 payload 를 함께 해싱해 만든다(`docling/utils/llm_cache.py` `cache_key`).

```python
def cache_key(endpoint, payload):
    h = hashlib.sha256()
    h.update(str(endpoint).encode("utf-8"))          # ① 호출 엔드포인트 URL
    h.update(b"\x00")                                # ② 구분자(널바이트)
    h.update(_canonical(payload).encode("utf-8"))    # ③ 정규화된 요청 payload
    return h.hexdigest()                             # 64자 hex → <key>.json

def _canonical(payload):   # 결정적 정규화
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
```

| 구성 | 내용 |
| --- | --- |
| ① `endpoint` | 그 호출의 URL(모델 서빙 URL). **모델/URL 이 다르면 키가 갈림**(충돌 방지) |
| ② `\x00` | endpoint 와 payload 경계 구분자 |
| ③ `payload` | 그 호출의 **전체 요청 본문**: `model`, `messages`(텍스트/이미지 base64), 샘플링 파라미터(`temperature`/`top_p`/`max_tokens`/`seed`/`repetition_penalty`), `chat_template_kwargs` 등 |

- **결정적**: 같은 `(endpoint, payload)` → 항상 같은 64자 hex → 재실행 시 같은 파일을 찾아 **HIT**.
- **키 순서 무관**: `sort_keys=True` 라 payload dict 키 순서가 달라도 같은 키.
- 입력이 하나라도 바뀌면(프롬프트·이미지·모델·샘플링 등) 다른 키 → **MISS**(새로 호출·저장).

### 저장되는 데이터 (파일 내용)

각 `<key>.json` 은 forward-compat envelope 로 감싼다.
```json
{ "v": 1, "value": <LLM 응답 직렬화값> }
```
- `v`: envelope 스키마 버전(현재 1). 값이 바뀌면 기존 캐시는 miss 처리.
- `value`: 그 호출의 **결과**. 호출 유형별 형태:

| LLM 작업 | 공유 헬퍼 | `value` 형태 |
| --- | --- | --- |
| OCR / layout VLM(DotsOCR, 페이지별) | `call_vlm_server` | `{"content": <텍스트>, "usage": <토큰정보\|null>, "finish_reason": <문자열\|null>}` |
| 텍스트 프롬프트(품질검사·TOC 등) | `call_ai_model` | 문자열(응답 본문, thinking 제거 후) |
| 이미지 설명 · 표 설명 | `api_image_request` | 문자열(생성 텍스트) |
| 문서 본문요약(doc_summary) | `summarize_body` | 문자열(요약) |
| 메타데이터 · 커스텀 필드 | `_call_llm`(async) | 문자열(정규화 content) |

- 저장·읽기 안전성: temp 파일 기록 후 **atomic rename**(부분 쓰기 방지). 읽을 때 JSON 파싱 실패면
  miss 로 처리해 재호출·덮어쓴다. 같은 키 동시 쓰기는 내용이 같아 락이 필요 없다.
- **요청(payload)은 파일에 저장하지 않는다** — payload 는 키(해시)로만 반영되고, 파일엔 결과(`value`)만 담긴다.

### 저장 제외 (캐시 안 됨)
- `None` / 빈 문자열 결과 — "실패성 성공"은 저장 안 함(재호출 기회 보존).
- 예외로 실패한 호출(402/500 등) — 저장 안 되고 miss 로 남아 **재시도 시 다시 호출**(캐시 오염 없음).
- 활성 조건 미충족(캐시 off).
- PaddleOCR `/ocr` 텍스트 엔드포인트(강제 재OCR) — 현재 캐시 범위 밖.
- chunk 액티비티 — LLM 호출 자체가 없음.

### 로그
호출마다 캐시 사용 여부가 로그에 남는다.
```
[llm_cache] MISS — 캐시 없음, LLM 실제 호출 (endpoint=... key=...)
[llm_cache] STORE — LLM 결과 캐시 저장 (key=...)
[llm_cache] HIT  — 캐시 재사용, LLM 호출 안 함 (endpoint=... key=...)
```
요청 종료 시 요약: `[llm_cache] hit=.. miss=.. save_fail=.. dir=...`

### 재사용 단위(스코프)
캐시는 `<workflow_id>/<run_id>` 디렉토리 단위다. Temporal activity 재시도는 같은
workflow_id/run_id 를 유지하므로 **재시도 간 성공분을 그대로 재사용**한다. 서로 다른
문서/워크플로우는 다른 디렉토리라 격리된다.

### 실패 정책 (`error_policy`)

| 값 | 동작 |
| --- | --- |
| `lenient`(기본) | enrichment 실패를 삼키고 null 로 채운 뒤 성공 처리 — 기존 하위호환 동작 |
| `strict` | enrichment 실패 시 삼키지 않고 액티비티 실패(`ApplicationError`)로 올림 |

`strict` 실패는 처리기가 실패 단계·성격을 실어 던지고(`GenosServiceException(stage, error_type, reason_code)`),
워커가 성격(`error_type`)을 `ApplicationError.type`·`non_retryable` 로 옮깁니다 — `transient`(연결/429/5xx)는 재시도,
`permanent`(그 밖의 4xx/파싱)는 즉시 실패입니다. 메시지에 `[<단계>] <사유>` 가 남습니다
(단계: `doc_summary`/`image_description`/`table_description`/`metadata`/`custom_fields`/`enrichment`).

> **주의**: `strict` 는 캐시가 선행되어야 실용적이다. 캐시 없이 하드페일하면 재시도마다 성공분이 전부 재과금된다.

> **예외 — base enrichment(TOC/문서 품질검사)는 정책과 무관하게 하드페일**: `enrichment()` 내부의
> TOC/품질검사 LLM 호출 실패(`LLMApiError`)는 `error_policy` 이전부터 항상 실패(`stage="enrichment"`)로
> 전파된다(기존 동작 보존). `error_policy` 는 그 다음 단계 enricher(doc_summary/image/table/metadata/custom_fields)
> 실패를 관장한다.

### 요청 deadline (`request_deadline`)
- **처리 전체 상한**(초): 워커가 처리 스레드를 기다리다 상한을 넘으면 `type="timeout"`(재시도 가능)으로 실패시킵니다.
  처리 스레드는 중간에 멈출 수 없으므로 그 자식 프로세스는 돌던 작업이 끝난 뒤 다시 뜹니다.
- **per-call timeout**: 개별 LLM 호출도 남은 deadline 으로 소켓 timeout 을 좁혀 조기 종료한다.
- 미지정 시 상한 없음. 호출 측 액티비티 타임아웃(`start_to_close_timeout`)은 이와 별개입니다.

### 배포 요건
- 워커 파드에 **`LLM_CACHE_ROOT` env** 가 있고, 그 경로가 **재시도 간 공유되는 NFS** 여야 재사용이 성립합니다
  (파이프라인 `DOC_PARSER_CACHE_ROOT` 가 가리키던 NFS 자리).
- `llm_cache`·`workflow_id`·`LLM_CACHE_ROOT` 중 하나라도 없으면 캐시는 켜지지 않고(안전 no-op) 그대로 동작한다.

### 테스트 방법
```bash
# 단건 CLI: 같은 스코프로 parse 2회 → 1회차 MISS(저장), 2회차 HIT(재사용)
export LLM_CACHE_ROOT=/tmp/llm_cache
python -m genon.preprocessor.activities run --activity parse --input report.pdf \
  --params '{"llm_cache": true, "workflow_id": "wf-123", "run_id": "run-1"}' -o /tmp/doc_run1.json
python -m genon.preprocessor.activities run --activity parse --input report.pdf \
  --params '{"llm_cache": true, "workflow_id": "wf-123", "run_id": "run-1"}' -o /tmp/doc_run2.json

# in-process 파싱→청킹: tools/parse_chunk/parse_chunk_test.sh
python parse_chunk_test.py --llm_cache --interim_root <경로> \
  --workflow_id wf-1 --run_id run-1 <input.pdf> <out>/

# in-process 적재(/run): tools/facade_test/facade_test.sh
python facade_test.py --kw llm_cache=1 --kw interim_root=<경로> \
  --kw workflow_id=<id> --kw run_id=<id> <input> <out>/
```
로그(stderr)의 1회차 `MISS→STORE`, 2회차 `HIT` 및 요약 `hit=.. miss=..` 로 재사용을 확인한다.

## 설정 참고

워커 동작은 배포된 전처리기의 `activities/<slug>/config.yaml` 로 결정됩니다(slug 마다 필수 — 없으면 등록 오류).
사이트 배포처는 사이트의 `activities/<slug>/` 설정이 이미 그 자리에 덮여 있습니다. 주요 항목:

| 단계 | config 파일 | 핵심 항목 | 설명 |
| --- | --- | --- | --- |
| 파싱 | `activities/parse/config.yaml` | `output.format` | docling 포맷 파일은 `"docling"` 이어야 DoclingDocument 로 저장. 그 외 `json`/`html`/`markdown`. 비-docling 포맷은 항상 parse-format(`elements`) |
| 청킹 | `activities/chunk/config.yaml` | `chunking.chunk_size` | docling 청킹(GenosSmartChunker) 최대 크기(0=분할 안 함). `params.chunk_size` 가 우선 |
| 청킹 | `activities/chunk/config.yaml` | `chunking.tokenizer_type` | `"char"`(문자 수) 또는 `"huggingface"`(토크나이저 기준) |
| 청킹 | `activities/chunk/config.yaml` | `chunking.generic.chunk_size` / `chunk_overlap` | 비-docling(parse-format) 일반 텍스트 splitter 기본값(문자 단위, 기본 1000/100). `params.chunk_size`/`chunk_overlap` 가 우선. audio/csv·xlsx 단일 벡터엔 미적용 |

- `params.log_level` 은 parse·chunk 가 무시합니다 — 로깅 레벨은 프로세스 기동 때 yaml `defaults.log_level` 로 한 번 정합니다(#481).

> 파싱 옵션(OCR·레이아웃·enrichment 등)의 상세 설명은 [parser_processor.md](parser_processor.md) 를 참고하세요.

## 주의사항 / FAQ

- **기동하자마자 "시작 명령(START_COMMAND)이 비어 있습니다"로 끝남**: 리비전 시작 명령에
  `python -m genon.preprocessor.worker` 를 넣으세요.
- **"워커 필수 env 가 없습니다: …"**: `TEMPORAL_HOST`·`WORKER_TASK_QUEUE`·`NFS_ROOT`·`ARTIFACT_ROOT` 를 넣으세요.
- **액티비티가 큐에서 안 집힘(호출 측이 timeout 만 봄)**: `WORKER_TASK_QUEUE`·`TEMPORAL_NAMESPACE` 가 호출 측
  (파이프라인 `DOC_PARSER_TASK_QUEUE`)과 같은지, 액티비티 이름이 기동 로그의 등록 목록에 있는지 보세요.
- **`read_ref.path 는 루트 기준 상대경로여야 합니다` / `루트 밖을 가리킵니다`**: 절대경로나 `..` 를 넘긴 경우입니다.
  `NFS_ROOT`·`ARTIFACT_ROOT` 기준 상대경로를 넘기세요.
- **파싱 결과가 DoclingDocument 가 아님**: docling 포맷 파일(pdf/html/htm/docx/hwp/hwpx)인데 `elements` 로 저장되면
  `activities/parse/config.yaml` 의 `output.format` 이 `"docling"` 인지 확인하세요.
  비-docling 포맷(csv/xlsx/txt/md/ppt/이미지/오디오)은 parse-format(`elements`)으로 저장되며, 그대로 청킹됩니다
  (audio→`[AUDIO]` 단일, csv/xlsx→행마다 청크 1개, 그 외 텍스트→`chunking.generic` splitter).
- **타임아웃**: 큰 문서 파싱은 시간이 걸립니다. 호출 측 `start_to_close_timeout` 을 넉넉히 두고,
  `heartbeat_timeout` 은 `HEARTBEAT_INTERVAL` 보다 길게 두세요.
- **LLM 캐시(#329)가 안 켜져요**: 캐시는 `llm_cache=true` **AND** `workflow_id` **AND** 워커 env `LLM_CACHE_ROOT`
  가 있어야 동작합니다. 재사용은 그 경로가 재시도 간 공유되는 NFS 일 때 성립합니다. 로그의 `[llm_cache] hit=.. miss=..`
  요약으로 확인하세요. 상세: 위 「LLM 캐시 / 실패 정책 / 요청 deadline (#329)」 절.
