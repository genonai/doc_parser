# doc_parser 코드 서빙 — 사용 가이드

이 저장소는 **doc_parser 전처리기의 코드 서빙 배포본**입니다. GenOS 코드 서빙이 이 repo를 clone한 뒤,
리비전의 시작 명령에 따라 둘 중 하나를 띄웁니다.

| 리비전 | 시작 명령 | 제공하는 것 |
| --- | --- | --- |
| **전처리기 워커** | `python -m genon.preprocessor.worker` | Temporal 액티비티(액티비티 폴더 slug 마다 하나). HTTP 는 `/health` 하나 — [전처리기 워커 리비전](#전처리기-워커-리비전) |
| **HTTP 서빙** | 비움(기본 — `main.py`, FastAPI) | 적재/첨부/변환·파싱·청킹·헬스체크 엔드포인트 — [엔드포인트](#엔드포인트) |

> 이 repo는 코드서빙용 **배포 산출물**입니다. docling은 소스 대신 `packages/`의 wheel로 동봉되어
> 런타임에 설치됩니다.

문서는 **① GenOS 배포·등록 → ② 호출(사전 준비·엔드포인트·사용 예시)** 순서로 구성됩니다.

**독자별로 읽는 곳이 다릅니다.**

| 독자 | 읽을 곳 |
|---|---|
| **코드서빙을 설치·배포하는 엔지니어** | [배포 / GenOS 코드서빙 등록](#배포--genos-코드서빙-등록) 1~5번 (최초 설치는 1번부터) → 워커는 [전처리기 워커 리비전](#전처리기-워커-리비전), HTTP 는 [사용 예시](#사용-예시) 로 동작 확인 |
| **전처리기를 호출하는 개발자** | [사전 준비](#사전-준비) → [엔드포인트](#엔드포인트) → [사용 예시](#사용-예시) (HTTP 서빙) |
| **액티비티를 추가·수정하는 개발자** | [액티비티 폴더](#액티비티-폴더) → [브랜치 역할과 새 버전 받기](#브랜치-역할과-새-버전-받기) |
| **전처리기 코드를 수정하는 개발자** | 아래 개발 매뉴얼 |

> **전처리기 코드를 직접 수정하려면** 이 문서 대신
> [`genon/preprocessor/manual/code_serving_dev_manual.md`](genon/preprocessor/manual/code_serving_dev_manual.md)
> 를 보세요 — Genos 개념부터 로컬 개발환경 세팅, parser/chunker 코드 이해·수정, 재배포까지 한 문서로
> 안내합니다. 이 저장소(공개 배포본)만으로 따라갈 수 있게 쓰여 있습니다.

## 개요

이 절과 [사전 준비](#사전-준비)·[엔드포인트](#엔드포인트)·[사용 예시](#사용-예시)는 **HTTP 서빙** 리비전의 내용입니다.

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

## 버전 정합

이 공개 배포본의 릴리스는 배포 스크립트로 생성됩니다. 배포처(Genos 코드서빙용 gitea 복사본, 또는 외부 Git 의
작업 브랜치)에서는 액티비티 폴더(`genon/preprocessor/activities/<slug>/`)의 설정·프롬프트와 HTTP 서빙 설정
(`genon/preprocessor/resource/`)을 업무 요구사항에 맞게 수정하거나 사본 액티비티를 추가할 수 있습니다
([액티비티 폴더](#액티비티-폴더)).

> ⚠️ **이 공개 배포본의 파일은 릴리스마다 배포 스크립트가 다시 생성합니다.** 여기에서 직접 고친 내용은
> 다음 릴리스에 덮어써집니다. **수정은 항상 배포처에서** 하세요 — gitea 복사본, 또는 외부 Git 에 두고 쓰는
> 경우 작업 브랜치(아래 「브랜치 역할과 새 버전 받기」)입니다.

- **버전 소스**: 원본의 git 릴리스 태그(예: `2.2.5`)가 버전의 단일 진실 소스입니다.
  배포본 repo에도 **동일한 미러 태그**가 붙으므로, 배포본 태그 = 원본 릴리스 태그입니다.
- **`VERSION` 파일**: 배포본 루트의 `VERSION`(JSON)이 이 산출물이 어느 원본에서 나왔는지 새깁니다.
  - `source_version` — 원본 릴리스 태그
  - `source_commit` / `source_commit_date` — 원본 커밋 SHA·날짜
  - `docling_wheel` — 동봉된 docling wheel 파일명(`packages/`)
- 특정 배포 인스턴스가 어느 버전인지 확인하려면 배포본의 `VERSION` 또는 태그를 보면 됩니다.

## 액티비티 폴더

파싱·청킹 처리기는 **액티비티 하나 = 폴더 하나**입니다. 폴더 이름이 slug 이고, 워커에 등록되는 액티비티 이름입니다.

```
genon/preprocessor/activities/<slug>/
  processor.py              액티비티 처리기 코드(첫 줄 주석 = 화면에 보이는 별명)
  config.yaml               필수 — 이 액티비티의 설정
  prompt_*.md               설정이 파일명으로 부르는 프롬프트
  custom_field_*.yaml       설정이 파일명으로 부르는 문서유형 설정
```

- **원형**은 `parse`(파싱)·`chunk`(청킹)이고, **사본**은 `parse_<이름>`·`chunk_<이름>` 입니다(slug 의 첫 단어 = 단계).
- 파싱·청킹 설정은 `activities/parse/config.yaml`·`activities/chunk/config.yaml` 입니다. HTTP 서빙의
  `/parser`·`/chunker` 도 이 두 파일을 씁니다.
- 설정이 부르는 프롬프트·custom_field 는 **상대 경로로 그 폴더 안의 파일**을 씁니다(폴더 밖으로 나가는 상대 경로는 거부, 절대 경로는 지정한 그대로 읽음 — 사본에는 쓰지 않습니다). 모델 접속 정보는 폴더에 복사하지 않고
  `genon/preprocessor/resource/model_presets.yaml` 의 프리셋을 이름(`model_preset: <이름>`)으로 참조합니다.
- 못 찾으면 오류로 알리고 다른 파일로 대신하지 않습니다.
  - `config.yaml` 이 없으면 액티비티 등록(워커 기동)이 실패합니다.
  - 설정에 적은 프롬프트·custom_field 파일이 없으면 오류입니다. 프롬프트 파일이 비어 있어도 오류입니다.
  - 내장 기본 프롬프트는 설정에 프롬프트를 적지 않았을 때만 씁니다.

### 사이트 설정이 들어간 배포본

사이트용으로 만든 배포본은 커밋 제목 끝에 ` · site=<site>` 가 붙습니다. 이 배포본은 사이트의 액티비티
설정(`config.yaml`·custom_field·프롬프트)이 `genon/preprocessor/activities/<slug>/` 에 이미 들어 있고, 코드와
`genon/preprocessor/resource/` 는 표준 배포본과 같습니다. 그 위에서 사본 액티비티를 만들면 사본도 사이트 설정에서
출발합니다. 모델 접속 실값은 리비전 환경변수 `GENOS_MODEL_PRESETS_FILE` 로 넣습니다([배포](#배포--genos-코드서빙-등록) 3·4번).

## 브랜치 역할과 새 버전 받기

이 배포본을 외부 Git 저장소(GitHub·Bitbucket 등)에 그대로 두고 쓰는 경우의 규칙입니다. gitea 복사본에
복사해 쓰는 경우는 아래 「배포」 3번을 따릅니다.

| 브랜치 | 쓰는 쪽 | 내용 |
| --- | --- | --- |
| `main`(릴리스, 미러 태그) · 개발 빌드 브랜치(지금은 `studio/dev` — 임시) | 배포 스크립트만 | 원본에서 생성한 배포본. 커밋 메시지는 `sync from doc_parser <원본 커밋> (docling→wheel)`, 사이트 설정이 들어간 배포본은 끝에 ` · site=<site>` |
| 작업 브랜치(예: `work/<이름>`) | 사람·전처리 Studio | 액티비티 추가, 설정 수정. 코드서빙 리비전은 이 브랜치를 가리킵니다 |

- **배포 스크립트 브랜치에는 직접 커밋하지 않습니다.** 배포 스크립트는 파일을 통째로 다시 생성해 다음 커밋으로
  올립니다. 그래서 그 브랜치에 사람이 올린 커밋이 있으면, 그 내용은 충돌 없이 지워지거나 원본 내용으로 바뀝니다.
  merge 가 아니라서 IDE 에도 드러나지 않습니다. 이를 막으려고 배포 스크립트는 그런 커밋을 발견하면 멈춥니다.
- **작업은 작업 브랜치에서 합니다.**
  ```bash
  git switch -c work/<이름> origin/main        # 개발 빌드를 쓰면 origin/studio/dev
  ```
  개발 빌드 브랜치가 없어지면(원본의 개발 내용이 릴리스로 합쳐지면) 그 위에서 만든 작업 브랜치는 그때부터
  `origin/main` 을 merge 해 받습니다.
- **액티비티는 작업 브랜치에서 만들고 커밋합니다.** 저장소 루트에서 실행하며, 결과는 stdout 마지막 줄
  `@@RESULT@@ {…}` 한 줄입니다(실패 시 exit 1). 명령은 파일만 다루므로 커밋·push 는 직접 합니다.
  ```bash
  # 원형(또는 다른 사본) 폴더를 복사해 새 액티비티 — slug 는 parse_<이름>·chunk_<이름>, 단계는 원본과 같아야 합니다
  python -m genon.preprocessor.activities new --from parse --slug parse_x [--title "<별명>"]
  # 단건 실행 — 워커와 같은 본문을 Temporal 서버 없이 돌립니다(parse: 원본 문서, chunk: 파싱 결과 JSON)
  python -m genon.preprocessor.activities run --activity parse_x <원본 문서> [-o <결과 JSON>]
  python -m genon.preprocessor.activities run --activity chunk <파싱 결과 JSON>
  # 사본을 복사한 뒤 원형이 바뀐 내용(가져올 변경은 사람이 고릅니다)
  python -m genon.preprocessor.activities diff parse_x
  # 사본 삭제(원형 parse·chunk 는 지우지 않습니다)
  python -m genon.preprocessor.activities remove --slug parse_x

  git add genon/preprocessor/activities/parse_x && git commit -m "<메시지>" && git push
  ```
  push 한 커밋으로 코드서빙 리비전을 만들면 워커가 새 slug 를 액티비티로 등록합니다.
  `run` 은 Temporal SDK(`temporalio`)가 필요합니다 — 코드서빙 base 이미지에는 포함되어 있고, 로컬 venv 에서는
  따로 설치합니다.
- **새 버전·hotfix 는 작업 브랜치로 merge 해서 받습니다.**
  ```bash
  git fetch origin
  git merge origin/main                         # 또는 git rebase origin/main
  ```
  - 작업 브랜치에서 **새로 만든 파일**(예: 새 액티비티)은 배포본이 건드리지 않으므로 그대로 남습니다.
  - **양쪽이 함께 고친 파일**(배포본이 기본 액티비티나 설정을 바꿨는데 작업 브랜치도 같은 파일을 고친 경우)은,
    같은 곳을 고쳤으면 충돌로 표시되고 IDE 에서 어느 쪽을 남길지 정합니다. 서로 다른 곳을 고쳤으면 git 이 자동으로
    합치므로, merge 뒤 `git diff ORIG_HEAD` 로 바뀐 내용을 확인합니다.
  - 배포본에 **같은 이름의 파일이 새로 생긴 경우**도 충돌로 표시됩니다.
  - `packages/` 의 wheel, `requirements*.txt`, `VERSION`, `README.md` 처럼 배포 스크립트가 만드는 파일은
    작업 브랜치에서 고치지 않습니다. 그래야 merge 할 때 충돌이 생기지 않습니다.

## 배포 / GenOS 코드서빙 등록

이 배포본을 GenOS 코드서빙으로 올리는 절차입니다.

- **사이트에 코드서빙을 처음 올리는 경우** — 1번부터 순서대로 진행합니다.
- **이미 코드서빙이 배포되어 동작 중인 경우** — 1·2번은 건너뛰고 3번(소스 갱신)부터 진행합니다.

1. **base 이미지 확보 · GenOS 등록** — *설치 담당 엔지니어용(사이트당 1회). 이미지가 이미 등록되어
   있다면 건너뜁니다.*
   - 이미 코드서빙 리비전이 돌고 있으면 **그 리비전이 쓰는 이미지를 그대로** 사용합니다.
   - 레지스트리에 이미지가 있는지 확인:
     ```bash
     curl http://192.168.74.164:30500/v2/mnc/template-code-serving-doc-parser/tags/list
     ```
   - GenOS **도커 이미지**에 등록하고, 이미지 타입은 반드시 **`Code_Serving`** 으로 지정합니다.
     (이 타입이 아니면 4번 리비전 생성 화면의 이미지 목록에 나타나지 않습니다.)
   - 레지스트리에 없으면 **사내 원본 저장소의 `build-script/code-serving-doc-parser/README.md`** 절차로
     빌드·푸시합니다. 공개 배포본에는 빌드 도구가 포함되지 않습니다.

2. **GenOS 코드서빙 생성** — [genos docs · 코드서빙](https://genos-docs.gitbook.io/default/v1.8.6/basic-tutorials/guides/development/code_serving)
   참고. 저장소 유형은 **Gitea** 를 선택합니다(생성 시 gitea repo가 함께 만들어짐).

3. **배포본을 gitea repo에 올리기** — [코드스페이스](https://genos-docs.gitbook.io/default/v1.8.6/basic-tutorials/guides/development/code_space)를 생성해 vscode에서 아래를 수행합니다.
   ```bash
   # 코드서빙 생성 시 만들어진 gitea repo clone (gitea id 는 코드서빙 페이지에서 확인)
   # id와 pass는 GenOS의 id와 pass를 입력해줍니다.
   git clone http://llmops-gitea-service:3000/llmops/<코드서빙 gitea id>.git <gitea_dir>

   # 공개 배포본 clone — 인증 불필요
   git clone https://github.com/genonai/doc_parser_code_serving.git
   cd doc_parser_code_serving

   # 배포본 내용을 gitea repo 로 복사 (.git 제외)
   tar --exclude=.git -cf - . | (cd <gitea_dir> && tar -xf -)

   # (필요할 때) gitea repo에서 업무에 맞게 설정 수정 — 용도별 대상 파일(genon/preprocessor/manual/ 의 매뉴얼 참고):
   #   genon/preprocessor/activities/parse/config.yaml   (파싱 — 워커 액티비티 parse, HTTP /parser)
   #   genon/preprocessor/activities/chunk/config.yaml   (청킹 — 워커 액티비티 chunk, HTTP /chunker)
   #   genon/preprocessor/resource/intelligent_processor_config.yaml   (HTTP /preprocess·/preprocess_intelligent)
   #   genon/preprocessor/resource/attachment_processor_config.yaml    (HTTP /preprocess_attachment)
   #   genon/preprocessor/resource/convert_processor_config.yaml       (HTTP /preprocess_convert)
   # LLM 모델 주소·키는 이 파일들에 적지 않습니다. 설정은 resource/model_presets.yaml 의 프리셋을
   # 이름(model_preset: <이름>)으로 부르고, 설치 환경의 실값은 4번 리비전 환경변수
   # GENOS_MODEL_PRESETS_FILE 로 넣습니다.

   # commit/push (push 시 GenOS id/pass 입력)
   cd <gitea_dir> && git add . && git commit -m "deploy doc_parser code-serving" && git push
   ```
   - ⚠️ config는 이 gitea repo(배포처)에서 수정합니다.
   - 모델 접속 실값 파일은 `resource/model_presets.yaml` 과 같은 형식(최상위 `model_presets:`)이고, 바꿀 프리셋의
     바꿀 키(예: `url`·`api_key`)만 적어도 됩니다 — 같은 이름의 프리셋 위에 키 단위로 덮입니다.

4. **리비전 생성/배포** — 코드서빙 매뉴얼대로 리비전을 생성하면 gitea 소스(레포 URL/commit)가 런타임에 `/app/src/service`로 clone되고
   시작 명령(`START_COMMAND`)이 실행됩니다. 비우면 `main.py`(HTTP 서빙)가 실행됩니다.
   - 이미지: `mnc/template-code-serving-doc-parser` 선택.
   - HTTP 서빙: GPU 미할당, **medium(1 CPU Core, 16GB Memory)** 수준 인스턴스.
   - 전처리기 워커: `START_COMMAND` 와 환경변수를 [전처리기 워커 리비전](#전처리기-워커-리비전)대로 줍니다.
   - 모델 접속 실값: 환경변수 `GENOS_MODEL_PRESETS_FILE` 에 값을 채운 `model_presets.yaml` 의 경로(파드에서 읽을 수 있는 경로)를 줍니다.

5. **호출/테스트** — 아래 [사용 예시](#사용-예시) 및 동봉된
   `genon/preprocessor/tools/code_serving/serving_gateway_test.py` 참고.

## 전처리기 워커 리비전

같은 배포본으로 코드서빙 리비전을 만들고 시작 명령만 워커로 줍니다. 워커는 Temporal 큐에서 파싱·청킹
액티비티를 받아 처리하며, 액티비티 이름은 [액티비티 폴더](#액티비티-폴더)의 slug(`parse`·`chunk`·사본 slug)입니다.

- **시작 명령**(`START_COMMAND`): `python -m genon.preprocessor.worker`
- **헬스 체크**: `GET /health`(포트 `PORT`, 기본 8080) 하나만 제공합니다 — 코드서빙 startupProbe 용입니다.
  워커 프로세스가 모두 살아 있으면 200 `{"status":"ok"}`, 아니면 503 입니다.
- **액티비티 인자**는 파일 내용이 아니라 상대 참조(`read_ref`·`parsed_ref`·`output_ref`)입니다. 루트는 이 파드의
  `NFS_ROOT`·`ARTIFACT_ROOT` 로 해석하고, 결과 JSON 은 `ARTIFACT_ROOT` 아래 `output_ref` 자리에 씁니다.

| 환경변수 | 구분 | 설명 |
| --- | --- | --- |
| `TEMPORAL_HOST` | 필수 | Temporal frontend 주소 |
| `WORKER_TASK_QUEUE` | 필수 | 이 워커가 폴링할 큐 — 액티비티를 보내는 쪽과 같은 이름 |
| `NFS_ROOT` | 필수 | `read_ref` 의 `root: "nfs"`(원본 문서)를 여는 루트 |
| `ARTIFACT_ROOT` | 필수 | 산출물 ref 와 `read_ref` 의 `root: "artifact"` 를 여는 루트 |
| `TEMPORAL_NAMESPACE` | 지정 | Temporal 네임스페이스 — 설치 환경의 값을 줍니다 |
| `GENOS_MODEL_PRESETS_FILE` | 지정 | 모델 접속 실값 파일([배포](#배포--genos-코드서빙-등록) 3·4번) |
| `TEMPORAL_PORT` | 선택 | 기본 `7233` |
| `WORKER_PROCESSES` | 선택 | 워커 프로세스 수(N), 기본 `1` |
| `WORKER_THREADS` | 선택 | 프로세스마다 동시에 처리할 문서 수(M), 기본 `1`. 동시 문서 수 = N × M |
| `LLM_CACHE_ROOT` | 선택 | `params.llm_cache` 를 켠 요청의 LLM 캐시 루트. 없으면 캐시를 쓰지 않습니다 |
| `HEARTBEAT_INTERVAL` | 선택 | 하트비트 간격(초), 기본 `10` |
| `PREVIEW_CHARS` | 선택 | 반환 preview 길이, 기본 `2000` |

- 필수 값이 없으면 워커가 기동하지 않고 빠진 이름을 모두 로그에 남깁니다.
- 메모리는 프로세스 수와 처리 중인 문서 수에 함께 비례합니다.

## 사전 준비

위 [배포 / GenOS 코드서빙 등록](#배포--genos-코드서빙-등록)으로 코드서빙을 만들면 `serving_id`가 발급됩니다.
이를 게이트웨이 base URL·`auth_key`(Bearer 토큰)와 함께 호출에 사용합니다.

| 항목 | 설명 | 예시 |
| --- | --- | --- |
| `base URL` | 게이트웨이 base URL | `https://<GENOS_HOST>` |
| `serving_id` | 배포된 코드 서빙 ID | `<SERVING_ID>` |
| `auth_key` | 게이트웨이 인증 토큰(Bearer) | `<AUTH_KEY>` |

- **`/parser`의 `file_path`는 서빙 컨테이너 내부의 로컬 경로**입니다(MinIO 키 아님). 서버가 접근 가능한 경로를 넣으세요.
- docling 포맷은 파싱 서빙의 `activities/parse/config.yaml`이 `output.format: "docling"`이어야 응답에 `data.document`가 생성됩니다.
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
BASE="https://<GENOS_HOST>"; SERVING_ID="<SERVING_ID>"; AUTH="<AUTH_KEY>"
GW="${BASE}/api/gateway/code_serving/${SERVING_ID}"
FILE_PATH="/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf"

# health
curl --location "${GW}/health" -H 'Content-Type: application/json' -H "Authorization: Bearer ${AUTH}"

# parser
curl --location "${GW}/parser" -H 'Content-Type: application/json' -H "Authorization: Bearer ${AUTH}" \
  --data "{\"file_path\": \"${FILE_PATH}\", \"params\": {}}"
```

### Python (표준 라이브러리만 사용)
동봉된 `genon/preprocessor/tools/code_serving/serving_gateway_test.py`로 동일 호출:
```bash
# 접속 정보는 환경변수 또는 인자로 전달합니다(스크립트에 기본값이 없어 없으면 실행을 거부합니다).
export GENOS_BASE_URL="https://<GENOS_HOST>"
export GENOS_SERVING_ID="<SERVING_ID>"
export GENOS_AUTH_KEY="<AUTH_KEY>"

python serving_gateway_test.py --mode health
python serving_gateway_test.py --mode e2e     --file-path /data/documents/report.pdf --out /tmp/chunks.json --chunk-size 10000
python serving_gateway_test.py --mode parser  --file-path /data/documents/report.pdf --out-doc /tmp/doc.json
python serving_gateway_test.py --mode chunker --doc-json /tmp/doc.json --chunk-size 10000
```
주요 인자: `--mode`(health/parser/parser_upload/chunker/e2e), `--base-url`, `--serving-id`, `--auth-key`,
`--file-path`, `--chunk-size`, `--param KEY=VALUE`(임의 `params` 오버라이드, 반복 가능).

> `--chunk-size` 를 생략하면 필드를 아예 보내지 않아 **서빙 config 의 `chunking.chunk_size` 가
> 적용**됩니다. 크기 기반 병합·분할을 끄려면 `--chunk-size 0` 을 명시하세요.

## 에러 응답

- HTTP 상태는 항상 `200`, 성공 여부는 `code`로 판단(`0`=성공).
- 실패 시 `errMsg`·`error_code`가 담기고, `error_policy: "strict"` 또는 요청 deadline 초과 시
  `stage`(실패 단계)·`error_kind`(`transient`/`permanent`/`timeout`)가 추가됩니다.

## 설정 / 고급 옵션

- 프로세서 동작·옵션 상세: `genon/preprocessor/manual/`의
  [intelligent_processor.md] · [attachment_processor.md] · [convert_processor.md] · [parser_processor.md].
- **LLM 캐시 / 실패 정책(`error_policy`) / 요청 deadline(`request_deadline`)** 등 `params` opt-in 옵션과
  전체 상세는 **전체 매뉴얼**을 참고하세요:
  → [`genon/preprocessor/manual/code_serving.md`](genon/preprocessor/manual/code_serving.md)

## 부록: 로컬에서 `facade_run.py` 직접 실행 (개발·디버깅용)

게이트웨이 HTTP 테스트(`serving_gateway_test.py`, 위 [사용 예시](#사용-예시))와 달리, **서빙 배포 없이 이
repo를 clone한 로컬에서 전처리기를 직접 호출**해 보는 개발용 절차입니다. 대상은
`genon/preprocessor/examples/facade_run/facade_run.py`(지능형 프로세서, PDF).

> `uv sync` 는 이 배포본에서 **실패**합니다 — 동봉된 `genon/preprocessor/pyproject.toml` 의 docling 의존성
> source 가 소스가 없는(wheel만 있는) 이 repo 루트를 가리켜 docling 을 소스빌드하려다 깨집니다. 그래서 아래처럼
> **동봉 wheel 을 직접 설치**합니다.

**① 동작환경 설정 (uv)** — repo 루트에서:
```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r requirements.txt        # docling(fork) wheel + docling 계열 deps
uv pip install -r requirements-dev.txt     # 로컬 실행 전용 추가 deps
```
> `requirements-dev.txt` 는 `sync-serving-repo.sh` 가 생성합니다. 공통 deps(fastapi·httpx·grpcio·protobuf)와
> **parser·chunker facade 실행에 필요한 deps**(pymupdf·langchain-community·langchain-core·
> langchain-text-splitters·markdown2·pydub·chardet)가 함께 담깁니다. 이 deps 는 **운영 base 이미지엔 이미
> 포함**되어 있어 운영 런타임은 `requirements.txt`(docling wheel)만 설치합니다 — 로컬 bare-metal 실행 시에만
> 필요합니다.

**② 모델 서빙 연결** — 설정 파일(`intelligent_processor_config.yaml` 등)은 고치지 않습니다. 모델 접속 정보는
`genon/preprocessor/resource/model_presets.yaml` 의 프리셋을 이름으로 부르므로, 로컬 접속값만 담은 파일을 저장소
밖에 만들고 환경변수 `GENOS_MODEL_PRESETS_FILE` 로 가리킵니다. 모델 URL은 외부 게이트웨이 형태로, API 키는 모델
서빙 상세 화면에서 확인한 값으로 적습니다. 바꿀 프리셋의 바꿀 키만 적으면 같은 이름의 프리셋 위에 키 단위로 덮입니다.

```yaml
# 예: ~/model_presets.local.yaml  →  export GENOS_MODEL_PRESETS_FILE=~/model_presets.local.yaml
model_presets:
  default:
    url: "https://<GENOS_HOST>/api/gateway/rep/serving/<MODEL_SERVING_ID>/v1/chat/completions"
    api_key: "<MODEL_SERVING_API_KEY>"
```

프리셋 이름과 인터넷 단절 환경 준비는
[`code_serving_dev_manual.md`](genon/preprocessor/manual/code_serving_dev_manual.md)의 3.6(모델 서빙 연결 설정)·3.2를
참고하세요.

**③ 실행** — 경로를 파일 위치 기준으로 잡으므로 어느 디렉토리에서 실행해도 됩니다(`genon.*` 절대 import 도 스크립트가 해결합니다):
```bash
python genon/preprocessor/examples/facade_run/facade_run.py    # 입력: genon/preprocessor/sample_files/pdf_sample.pdf → 결과: 같은 폴더의 result.json
```
> 로컬 외부 게이트웨이 URL·API 키가 들어간 파일과 `.venv`·`result.json`·`__pycache__` 등 로컬
> 산출물이 코드 변경 패치에 포함되지 않도록 확인하세요. 배포본에는 기본 `.gitignore`가 포함됩니다.

---
※ 이 README는 배포 스크립트가 생성하는 파일입니다. 공개 배포본에서는 직접 편집하지 않습니다.
