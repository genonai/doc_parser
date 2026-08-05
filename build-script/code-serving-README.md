# doc_parser 코드 서빙 — 사용 가이드

이 저장소는 **doc_parser 전처리기의 코드 서빙 배포본**입니다. GenOS 코드 서빙이 이 repo를 clone해
`main.py`(FastAPI)를 기동하며, 단일 서빙이 적재/첨부/변환·파싱·청킹·헬스체크 엔드포인트를 제공합니다.

> 이 repo는 **빌드 산출물**입니다(원본·빌드 도구는 별개의 비공개 repo). docling은 소스 대신
> `packages/`의 wheel로 동봉되어 런타임에 설치됩니다.

문서는 **① GenOS 배포·등록 → ② 호출(사전 준비·엔드포인트·사용 예시)** 순서로 구성됩니다.

## 개요

- 엔드포인트: `/health`, `/preprocess*`(적재/첨부/변환), `/parser`(파싱), `/chunker`(청킹).
- 적재/첨부/변환은 문서를 한 번에 처리하는 **단일 단계** API입니다.
- 파싱·청킹은 **분리된 2단계**입니다.

```
원본 문서                  파싱 결과 JSON                        청크 리스트
(report.pdf) ──POST /parser──▶ data.document(docling)   ──POST /chunker──▶ data[ {...}, ... ]
(sheet.csv)  ──POST /parser──▶ data.elements(parse-format) ┘
```

> 무거운 처리(OCR·레이아웃·enrichment)는 파싱에서 끝나므로 청킹은 가볍게 호출할 수 있습니다.
> docling 포맷(pdf/html/htm/docx/hwp/hwpx)은 구조 인식 청킹, 그 외 포맷은 parse-format 공통 청킹입니다.

## 배포 / GenOS 코드서빙 등록

이 배포본을 GenOS 코드서빙으로 올리는 절차입니다. (배포본은 원본 repo에서 `sync-serving-repo.sh`로
빌드됩니다 — genon 코드 + `main.py` + docling wheel(`packages/`)만 담기며 docling 소스·dev/legacy 폴더는 제외됩니다.)

1. **base 이미지 준비/등록**
   - 코드서빙 base 이미지 `mnc/template-code-serving-doc-parser` 를 GenOS 도커 이미지에 등록하고 이미지 타입은 **`Code_Serving`** 으로 지정합니다.
   - base 이미지는 사내 도커레지스트리에 있습니다.
     확인: `curl http://192.168.74.164:30500/v2/mnc/template-code-serving-doc-parser/tags/list`
     (없으면 원본 repo의 `build-script/code-serving-doc-parser/README.md` 로 빌드/푸시.)

2. **GenOS 코드서빙 생성** — [genos docs · 코드서빙](https://genos-docs.gitbook.io/default/v1.8.6/basic-tutorials/guides/development/code_serving)
   참고. 저장소 유형은 **Gitea** 를 선택합니다(생성 시 gitea repo가 함께 만들어짐).

3. **배포본을 gitea repo에 올리기** — [코드스페이스](https://genos-docs.gitbook.io/default/v1.8.6/basic-tutorials/guides/development/code_space)를 생성해 vscode에서 아래를 수행합니다.
   ```bash
   # 코드서빙 생성 시 만들어진 gitea repo clone (gitea id 는 코드서빙 페이지에서 확인)
   # id와 pass는 GenOS의 id와 pass를 입력해줍니다.
   git clone http://llmops-gitea-service:3000/llmops/<코드서빙 gitea id>.git <gitea_dir>

   # 빌드된 배포본 clone
   # HTTPS clone 시 GitHub id와 personal access token(pass 자리에 토큰)을 입력합니다.
   # 토큰을 URL 에 직접 포함하지 마십시오. 토큰 발급방법은 아래의 발급방법을 참고바랍니다.
   git clone https://github.com/genonai/doc_parser_code_serving.git
   cd doc_parser_code_serving

   # 배포본 내용을 gitea repo 로 복사 (.git 제외)
   tar --exclude=.git -cf - . | (cd <gitea_dir> && tar -xf -)

   # gitea repo에서 환경에 맞게 config yaml 수정 (특히 LLM 모델 주소) — 용도별 대상 파일:
   # genon/preprocessor/facade/gitbook_doc/ 의 매뉴얼 참고바람.
   #   genon/preprocessor/resource/parser_processor_config.yaml
   #   genon/preprocessor/resource/chunking_processor_config.yaml
   #   genon/preprocessor/resource/intelligent_processor_config.yaml
   #   genon/preprocessor/resource/attachment_processor_config.yaml
   #   genon/preprocessor/resource/convert_processor_config.yaml

   # commit/push (push 시 GenOS id/pass 입력)
   cd <gitea_dir> && git add . && git commit -m "deploy doc_parser code-serving" && git push
   ```
   - github token 발급방법
     - github에서 다음 경로로 이동
       - Settings → Developer settings → Personal access tokens → Fine-grained tokens
     - 토큰 생성시 설정
       - Resource owner: genonai
       - Repository access:
         - Selected repositories → doc_parser_code_serving
       - Repository permissions:
         - Contents: Read-only
   - ⚠️ config는 이 gitea repo(배포처)에서 수정합니다.

4. **리비전 생성/배포** — 코드서빙 매뉴얼대로 리비전을 생성하면 gitea 소스(레포 URL/commit)가 런타임에 `/app/src/service`로 clone되고 `main.py`가 실행됩니다.
   - 이미지: `mnc/template-code-serving-doc-parser` 선택.
   - GPU 미할당, **medium(1 CPU Core, 16GB Memory)** 수준 인스턴스.

5. **호출/테스트** — 아래 [사용 예시](#사용-예시) 및 동봉된
   `genon/preprocessor/examples/code_serving/serving_gateway_test.py` 참고.

## 사전 준비

위 [배포 / GenOS 코드서빙 등록](#배포--genos-코드서빙-등록)으로 코드서빙을 만들면 `serving_id`가 발급됩니다.
이를 게이트웨이 base URL·`auth_key`(Bearer 토큰)와 함께 호출에 사용합니다.

| 항목 | 설명 | 예시 |
| --- | --- | --- |
| `base URL` | 게이트웨이 base URL | `https://genos.genon.ai` |
| `serving_id` | 배포된 코드 서빙 ID | `139` |
| `auth_key` | 게이트웨이 인증 토큰(Bearer) | `b8c0b48f...` |

- **`/parser`의 `file_path`는 서빙 컨테이너 내부의 로컬 경로**입니다(MinIO 키 아님). 서버가 접근 가능한 경로를 넣으세요.
- docling 포맷은 파싱 서빙의 `parser_processor_config.yaml`이 `output.format: "docling"`이어야 응답에 `data.document`가 생성됩니다.
- 그 외 포맷은 설정과 무관하게 parse-format(`data.elements`)으로 반환되며 chunker가 그대로 청킹합니다.

## 엔드포인트

**공통 URL** `{base}/api/gateway/code_serving/{serving_id}/{route}` — `route`는 **단일 세그먼트만**(슬래시 중첩 불가).

**공통 헤더**
```
Content-Type: application/json
Authorization: Bearer {auth_key}
```

**공통 요청/응답 envelope** (모든 POST 공통)
```json
{ "file_path": "<문서 경로>", "params": { } }
```
```json
{ "code": 0, "errMsg": "success", "data": { } }
```
- 성공 여부는 HTTP 상태가 아니라 **`code` 값**으로 판단하세요(예외 시에도 HTTP 200, `code`≠0).

| 메서드 | 경로 | 용도 | 비고 |
| --- | --- | --- | --- |
| `GET` | `/health` | 헬스 체크 | `{"status":"ok"}` |
| `POST` | `/preprocess` | 적재용(지능형) | `/preprocess_intelligent` 하위호환 별칭 |
| `POST` | `/preprocess_attachment` | 첨부용 | |
| `POST` | `/preprocess_intelligent` | 적재용(지능형) | |
| `POST` | `/preprocess_convert` | 변환용 | |
| `POST` | `/parser` | 문서 파싱 → DoclingDocument JSON | `IS_PARSER` 지원 전처리기 필요 |
| `POST` | `/chunker` | 파싱 결과 JSON → 청크 리스트 | `IS_CHUNKER` 지원 전처리기 필요 |

> `/parser`·`/chunker`는 설치된 전처리기가 해당 기능을 지원할 때만 동작합니다(미지원 시 `code:1` 안내).

### POST /parser
요청 `{"file_path": "...", "params": {}}` → 응답:
```json
{ "code": 0, "errMsg": "success",
  "data": { "document": { "schema_name": "DoclingDocument", "...": "..." }, "usage": { "pages": 10 } } }
```
- `data.document`: 청킹 입력으로 쓰는 DoclingDocument JSON · `data.usage.pages`: 처리 페이지 수.

### POST /chunker
파싱 결과를 `params.document`로 인라인 전달(docling `{"document":...}` 또는 parse-format `{"elements":[...]}` 자동 판별).
```json
{ "file_path": "report.pdf",
  "params": { "document": { "schema_name": "DoclingDocument", "...": "..." }, "chunk_size": 0 } }
```
응답 `data`는 `GenOSVectorMeta` 청크 리스트(`i_chunk_on_doc`, `i_page`, `text`, `chunk_token_count` ...).

| 파라미터 | 위치 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `document` | `params` | (필수) | 파싱 결과 JSON(docling/parse-format 자동 판별) |
| `chunk_size` | `params` | `0` | 청크 최대 크기(0=분할 안 함, config 기본값 덮어씀) |
| `log_level` | `params` | config | 런타임 로깅 레벨(5=DEBUG ~ 1=CRITICAL, 0=NOLOG) |

> 청킹 단계의 `file_path`는 청크 메타데이터 기록용이며 실제 입력은 `params.document`입니다.

## 사용 예시

### curl
```bash
BASE="https://genos.genon.ai"; SERVING_ID="139"; AUTH="b8c0b48f..."
GW="${BASE}/api/gateway/code_serving/${SERVING_ID}"
FILE_PATH="/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf"

# health
curl --location "${GW}/health" -H 'Content-Type: application/json' -H "Authorization: Bearer ${AUTH}"

# parser
curl --location "${GW}/parser" -H 'Content-Type: application/json' -H "Authorization: Bearer ${AUTH}" \
  --data "{\"file_path\": \"${FILE_PATH}\", \"params\": {}}"
```

### Python (표준 라이브러리만 사용)
동봉된 `genon/preprocessor/examples/code_serving/serving_gateway_test.py`로 동일 호출:
```bash
python serving_gateway_test.py --mode health
python serving_gateway_test.py --mode e2e --file-path /data/documents/report.pdf --out /tmp/chunks.json
python serving_gateway_test.py --mode parser --file-path /data/documents/report.pdf --out-doc /tmp/doc.json
python serving_gateway_test.py --mode chunker --doc-json /tmp/doc.json
```
주요 인자: `--mode`(health/parser/parser_upload/chunker/e2e), `--base-url`, `--serving-id`, `--auth-key`,
`--file-path`, `--chunk-size`, `--param KEY=VALUE`(임의 `params` 오버라이드, 반복 가능).

## 에러 응답

- HTTP 상태는 항상 `200`, 성공 여부는 `code`로 판단(`0`=성공).
- 실패 시 `errMsg`·`error_code`가 담기고, `error_policy: "strict"`(#329) 또는 요청 deadline 초과 시
  `stage`(실패 단계)·`error_kind`(`transient`/`permanent`/`timeout`)가 추가됩니다.

## 설정 / 고급 옵션

- 프로세서 동작·옵션 상세: `genon/preprocessor/facade/gitbook_doc/`의
  [intelligent_processor.md] · [attachment_processor.md] · [convert_processor.md] · [parser_processor.md].
- **LLM 캐시 / 실패 정책(`error_policy`) / 요청 deadline(`request_deadline`)** 등 `params` opt-in 옵션과
  전체 상세는 **전체 매뉴얼**을 참고하세요:
  → [`genon/preprocessor/facade/gitbook_doc/code_serving.md`](genon/preprocessor/facade/gitbook_doc/code_serving.md)

## 부록: 로컬에서 `facade/test.py` 직접 실행 (개발·디버깅용)

게이트웨이 HTTP 테스트(`serving_gateway_test.py`, 위 [사용 예시](#사용-예시))와 달리, **서빙 배포 없이 이
repo를 clone한 로컬에서 전처리기를 직접 호출**해 보는 개발용 절차입니다. 대상은
`genon/preprocessor/facade/test.py`(지능형 프로세서, PDF).

> `uv sync` 는 이 배포본에서 **실패**합니다 — 동봉된 `genon/preprocessor/pyproject.toml` 의 docling 의존성
> source 가 소스가 없는(wheel만 있는) 이 repo 루트를 가리켜 docling 을 소스빌드하려다 깨집니다. 그래서 아래처럼
> **동봉 wheel 을 직접 설치**합니다.

**① 동작환경 설정 (uv)** — repo 루트에서:
```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt        # docling(fork) wheel + docling 계열 deps
uv pip install -r requirements-dev.txt     # 로컬 실행 전용 추가 deps (fastapi httpx grpcio protobuf)
```
> `requirements-dev.txt` 는 `sync-serving-repo.sh` 가 생성합니다. 여기 담긴 deps 는 **운영 base 이미지엔 이미
> 포함**되어 있어 운영 런타임은 `requirements.txt`(docling wheel)만 설치합니다 — 로컬 bare-metal 실행 시에만
> 필요합니다.

**② config 수정** — `genon/preprocessor/resource/intelligent_processor_config.yaml` 을 직접 편집:
- `layout.layout_model_type:` → **`docling_layout`** (외부 layout VLM 없이 로컬 모델 사용)
- `enrichment` 의 `toc.enable`·`metadata.enable` → **`false`** (외부 LLM 호출 차단)
- (권장) `ocr.ocr_mode:` → **`disable`**, `formats.ppt.page_description.enable:` → **`false`**

> ↔ 사내망에서 실제 모델서빙이 닿는 환경이면, 위 대신 config 의 placeholder(`<LAYOUT_SERVING_ID>`,
> `<ENRICHMENT_SERVING_ID>`, `<OCR_ENDPOINT>` …)를 실제 주소로 채우면 원 설정 그대로 동작합니다.

**③ 실행** — 반드시 `facade/` 디렉토리에서 (test.py 의 `sys.path` 처리가 `genon.*` 절대 import 를 해결):
```bash
cd genon/preprocessor/facade && python test.py    # 입력: ../sample_files/pdf_sample.pdf → 결과: result.json
```
- 최초 1회 docling layout/TableFormer 모델을 HuggingFace 에서 다운로드합니다(네트워크 필요, 이후 캐시).

> ⚠️ config(`resource/*.yaml`) 편집분과 `.venv`·`result.json`·`__pycache__` 등 로컬 산출물은 **개발용**입니다.
> 실제 배포 gitea repo 로는 push 하지 마세요(`resource/` 는 배포 시 실제 서빙 주소로 채워야 함).

---
※ 이 README는 원본 repo의 `build-script/code-serving-README.md`에서 `sync-serving-repo.sh` 실행 시 복사됩니다(직접 편집 금지).
