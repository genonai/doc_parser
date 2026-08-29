# doc_parser

docling(v2.41.0) 포크 위에 GenOn 전처리기(genon/preprocessor)를 올린 문서 파싱·청킹 서비스.

## 저장소 지도

| 경로 | 역할 |
|---|---|
| `main.py` | 통합·로컬 실행 진입점. FastAPI 앱, 업무 API 7개와 health를 정의하고 facade를 호출 |
| `genon/preprocessor/facade/` | 처리 로직 본체 (아래 참조) |
| `genon/preprocessor/src/` | 공통 모듈 (`common`, `logger`, `config`, `utils`) — `main.py`가 sys.path 최상단에 넣음 |
| `genon/preprocessor/resource/` | 운영 YAML 설정 (프로세서 설정 + `custom_field_*.yaml`) |
| `genon/preprocessor/resource_dev/` | 로컬개발 YAML 설정 (프로세서 설정 + `custom_field_*.yaml`) |
| `genon/preprocessor/tests/` | **전처리기 테스트** (`unit/`, `smoke/`, `regression/`) |
| `docling/` | 포크된 docling 본체. 백엔드·파이프라인 수정은 여기 |
| `tests/` | docling 업스트림 테스트 |
| `build-script/` | 도커 이미지 빌드, 코드서빙 저장소 동기화 |

**수정 금지 / 탐색 제외** (모두 gitignore됨): `reference/`, `shkim_labs/`, `dist/`, `build/`, `debug/`, `tmp/`, `code-serving/`.
저장소 전체는 11G / 6만 개 이상 파일이므로 검색은 작업과 관련된 경로만 지정한다. 특히 추적된 대형 fixture와 노트북은 기본 검색에서 제외한다.

```bash
rg '<pattern>' main.py genon docling tests build-script \
  --glob '!tests/data/**' \
  --glob '!tests/data_scanned/**' \
  --glob '!**/*.pages.json' \
  --glob '!**/*.ipynb'
```

## 엔드포인트 → facade 매핑

`main.py` 의 라우트는 각각 대응하는 facade 프로세서를 호출한다.

| 라우트 | facade |
|---|---|
| `/preprocess` | `intelligent_processor.py` (`/preprocess_intelligent` 와 동일 대상, 하위호환 별칭) |
| `/preprocess_attachment` | `attachment_processor.py` |
| `/preprocess_intelligent` | `intelligent_processor.py` |
| `/preprocess_convert` | `convert_processor.py` |
| `/parser`, `/parser_upload` | `parser_processor.py` |
| `/chunker` | `chunking_processor.py` |

## 두 번째 진입점: 단일 프로세서 배포 (`genon/preprocessor/src/main.py`)

루트 `main.py` 는 facade 여러 개를 한 서버에 올리는 통합·로컬 실행 형태이고, **실제 외부 사이트 적용 환경은 이와 다르다.**

- 배포 대상 facade **한 개**만 `preprocessor.py` 라는 이름으로 바뀌어 마운트된다.
- 진입점은 `genon/preprocessor/src/main.py` 이며, `from preprocessor import DocumentProcessor` 로 그 하나를 로드해 `/run` 으로 진입한다.
- `/parser`, `/chunker` 는 프로세서의 `IS_PARSER` / `IS_CHUNKER` 속성으로 게이팅된다. 속성이 없으면 "지원하지 않습니다" 응답을 돌려준다.
- `legacy/` 아래 코드들(BOK 적재용, 법령, 코레일 등)은 로컬 진입점이 없다. 이 경로로만 배포·실행된다.

여기서 나오는 결론이 앞서의 제약들이다.

- **processor가 self-contained여야 하는 이유**: 배포 대상 `*_processor.py` 는 하나이므로 최상위 processor 파일끼리 서로 import하면 배포본에서 깨진다. 배포본에 포함되는 `facade/enrichment`, `facade/chunking`, `guardrail` 같은 공용 하위 모듈은 공유할 수 있다.
- **청킹 로직이 6곳에 복제된 이유**: 활성 processor 3종과 BOK 배포본 3종이 각각 독립적인 사본을 유지한다.
- `main.py` 계층 기능(요청 deadline, 에러 envelope의 `stage`/`error_kind` 등)을 바꿀 때는 **루트 `main.py` 와 `src/main.py` 두 파일을 모두** 확인해야 한다.

## 큰 파일 취급 규칙 (중요)

facade 프로세서는 파일 하나가 매우 크다.

| 파일 | 크기 | 전체 Read 비용 |
|---|---|---|
| `intelligent_processor.py` | 190KB / 3,922줄 | 약 54k 토큰 |
| `convert_processor.py` | 190KB / 3,956줄 | 약 54k 토큰 |
| `docling/backend/html_backend.py` | 175KB / 4,441줄 | 약 50k 토큰 |
| `chunking_processor.py` | 162KB / 3,366줄 | 약 46k 토큰 |
| `parser_processor.py` | 156KB / 3,327줄 | 약 44k 토큰 |
| `attachment_processor.py` | 122KB / 2,712줄 | 약 35k 토큰 |

**이 파일들을 통째로 Read 하지 말 것.** `Grep` 으로 심볼·문자열 위치를 먼저 찾고, `Read` 의 `offset`/`limit` 으로 해당 구간만 읽는다. 6개를 다 읽으면 약 280k 토큰으로 컨텍스트의 상당 부분이 날아간다.

## 아키텍처 제약

- **최상위 processor는 self-contained.** `intelligent_processor.py`, `convert_processor.py` 같은 최상위 processor 파일끼리는 서로 import하지 않는다. 배포본에 포함되는 공용 하위 모듈은 공유할 수 있으므로, 공통 로직을 무조건 복제하지 말고 `build-script/sync-serving-repo.sh` 의 배포 범위를 먼저 확인한다.
- **청킹 파이프라인은 6곳에 복제되어 있다.** `GenosSmartChunker` 청킹 로직은 활성 3종 + BOK 적재용 3종에 사본이 존재하므로 lockstep으로 함께 수정해야 한다.
- **`GenosServiceException` 은 활성 경로 6곳과 여러 legacy 파일에 복제**되어 있다. 고정 개수를 가정하지 말고 시그니처 변경 전에 `rg -n '^class GenosServiceException' genon/preprocessor/src genon/preprocessor/facade --glob '*.py'` 로 전체 대상을 확인한다. facade가 던진 로컬 예외는 `main.py` 의 제네릭 핸들러가 받는다.
- **docling 안의 결함은 docling 안에서 고친다.** genon 쪽 우회책을 기본 해법으로 삼지 말 것.
- 설정은 스키마별 키를 늘리기보다 일반화된 메커니즘으로 푼다. allowlist보다 blocklist. YAML은 초보자가 읽을 수 있게 개념 수를 줄인다.

## 테스트

전처리기 테스트는 `genon/preprocessor/` 에서 실행한다 (루트 `pytest.ini` 는 docling 업스트림용이며 `testpaths` 가 존재하지 않는 디렉터리를 가리킨다).

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no
```

마커: `unit`, `smoke`, `regression`, `update_baseline`(일반 실행 제외).

주의:
- `addopts` 에 `-p no:cacheprovider` 가 있어 **`--lf` 를 쓸 수 없다.**
- 출력에 ANSI 색상이 섞여 `grep '^FAILED'` 가 매치되지 않도록 항상 `--color=no` 를 유지한다.
- 검증은 토큰 절약이 우선이다. 본문 전문을 출력하지 말고, 영향 범위에 대한 단정문 테스트로 고정한다.

## 로컬 실행

루트 `.venv` 와 `genon/preprocessor/.venv` 두 개가 있다. facade를 직접 실행할 때는 하이브리드 PYTHONPATH(루트 venv 인터프리터 + preprocessor site-packages를 뒤에)를 쓴다.

```bash
PYTHONPATH=<repo>:<repo>/genon/preprocessor:<repo>/genon/preprocessor/src:<repo>/genon/preprocessor/.venv/lib/python3.13/site-packages \
  .venv/bin/python <script>
```

엔드포인트 확인은 새 pytest를 만들기보다 `genon/preprocessor/examples/code_serving/serving_gateway_test.sh` 에 모드를 추가하는 방식을 선호한다.

## 배포 (code-serving)

- 배포본은 별도 Private 저장소(`doc_parser_code_serving`)이며 로컬 `code-serving/` 은 그 클론이다(gitignore).
- 동기화는 `build-script/sync-serving-repo.sh`. **클론이 이미 존재하면 dry-run이 아니라 실제 커밋이 남는다** — 검증은 throwaway `SERVING_DIR` 로 할 것.
- docling은 wheel로 `packages/` 에 동봉된다. 핫픽스 overlay(`apply-patch.sh`)는 genon 전용이므로, docling을 고쳤다면 wheel 재빌드가 필수다.

## 작업 방식

- 진행 상황과 결과 보고는 한국어로.
- 소스 주석·docstring에 이모지를 쓰지 않는다. 강조는 문장으로 한다.
- 서브에이전트는 작업이 독립적인 하위 문제로 명확히 분할되고 병렬 실행의 이점이 있을 때만 사용한다. 단순·순차 작업은 직접 처리한다.
- 병렬 서브에이전트에게 `git checkout`/`git restore` 등 작업 트리를 되돌리는 명령을 주지 않는다. 담당 경계를 모르는 되돌리기로 미커밋 작업물이 소실된 전례가 있다.

## .claude/ 도구 설정

- `settings.json` — 빌드 산출물·캐시·`code-serving/`·`shkim_labs/` 를 Read 에서 차단하고, `reference/` 와 `code-serving/` 은 Edit 에서 차단한다(`reference/` 는 읽기 전용 참조용이라 Read 는 허용).
- `hooks/pytest-filter.sh` — PreToolUse(Bash) 훅. 단순한 pytest 명령에 `--color=no -p no:randomly` 를 붙이고 PASSED/SKIPPED 줄을 걷어낸다. `set -o pipefail` 로 종료 코드는 보존된다. 실측 11,949바이트 출력이 653바이트로 줄었다. 파이프·`&&` 등이 섞인 복합 명령은 건드리지 않는다.
- `skills/deploy-code-serving`, `skills/create-patch-bundle` — 배포·패치 절차. 필요할 때만 로드된다.
- `pyright-lsp` 플러그인(선택). 설치되어 있으면 심볼 정의를 찾을 때 Grep 대신 LSP 탐색을 우선한다. 개인 설정이므로 공유되지 않는다. 쓰려면 `npm install -g pyright` 로 `pyright-langserver` 를 깔고 `claude plugin install pyright-lsp@claude-plugins-official --scope local` 을 실행한다.

## Compact instructions

압축할 때는 변경한 파일 경로와 그 이유, 실행한 검증 명령과 그 결과, 아직 남은 작업을 우선 보존한다. 탐색 과정에서 읽은 파일 내용은 버려도 된다.
