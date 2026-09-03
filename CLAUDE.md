# doc_parser

docling(v2.41.0) 포크 위에 GenOn 전처리기(genon/preprocessor)를 올린 문서 파싱·청킹 서비스.

## 저장소 지도

| 경로 | 역할 |
|---|---|
| `main.py` | 통합·로컬 실행 진입점. FastAPI 앱, 업무 API 7개와 health를 정의하고 facade를 호출 |
| `genon/preprocessor/facade/` | 처리 로직 본체 — 최상위 `*_processor.py` 5종 (아래 참조) |
| `genon/preprocessor/facade/common/`, `chunking/`, `enrichment/`, `guardrail/` | facade가 공유하는 공용 하위 모듈. 배포본에 포함된다 (아래 참조) |
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
- **BOK 배포본이 청킹 로직 사본을 갖는 이유**: `legacy/BOK_적재용_*` 3종은 별도 배포 단위라 자체 사본과 모듈 상수 설정을 유지한다. 활성 processor 3종은 `facade/chunking/smart_chunker.py` 로 통합되어 사본이 없다.
- `main.py` 계층 기능(요청 deadline, 에러 envelope의 `stage`/`error_kind` 등)을 바꿀 때는 **루트 `main.py` 와 `src/main.py` 두 파일을 모두** 확인해야 한다.

## 공용 하위 모듈 지도

facade가 공유하는 로직은 아래에 한 벌씩만 둔다. 최상위 processor는 별칭이나 얇은 호출부만 갖는다.
새 로직을 어디 둘지 고민되면 이 표에서 가장 가까운 모듈을 먼저 찾는다.

| 모듈 | 역할 |
|---|---|
| `common/config_parse.py` | yaml/kwargs 값 파싱(`parse_optional_bool/int/float`), `load_config`, 토크나이저·청크크기·`compact_tables` 등 설정 해석 |
| `common/file_probe.py` | 파일 종류 판별(PDF/텍스트/암호화/HWP 보호), PDF 경로 변환, 변환기 가용성 |
| `common/pdf_convert.py` | 비-PDF 입력의 PDF 변환 진입점. backend chain 순서 결정 + 이슈 #286 사전 체크 |
| `common/loaders.py` | `install_packages`, Text/Tabular/Audio 로더 |
| `common/docling_ops.py` | docling 배관 — OCR 옵션, 컨버터 생성, 표 이미지 저장, 글리프·빈 텍스트 검사 |
| `common/pipeline_setup.py` | `__init__` 의 OCR·PDF·layout 설정 해석 |
| `common/runtime_kwargs.py` | 런타임 kwargs 정규화, 이미지 모드 배선 |
| `common/vector_meta.py` | `GenOSVectorMeta` 빌더 공통 코어 |
| `common/runtime.py`, `common/appendix.py` | 로깅 초기화, 별첨 키워드 판정 |
| `chunking/smart_chunker.py` | `GenosSmartChunker` 본체. 활성 3종이 ClassVar 플래그만 다른 서브클래스로 상속 |
| `chunking/hybrid_chunker.py` | docling_core `HybridChunker`/`HierarchicalChunker` 포크본. 이름을 `TokenAwareHybridChunker`/`HierarchicalDocChunker` 로 달리해 업스트림과 구분한다. 업스트림과 갈라진 축은 모듈 docstring 참조 |
| `chunking/header_path.py`, `page_split.py`, `table_splitter.py`, `text_norm.py` | 청크 헤더 경로, 페이지 분할, 표 행 분할, 청크 텍스트 정제 |
| `enrichment/`, `guardrail/` | custom_fields·LLM 보강, 민감정보 처리 |

## 큰 파일 취급 규칙 (중요)

facade 프로세서는 공용 모듈 추출로 절반 이하로 줄었지만(2026-08-30 기준 5종 합계 8,393줄) 여전히 크다.

| 파일 | 크기 | 전체 Read 비용 |
|---|---|---|
| `docling/backend/html_backend.py` | 175KB / 4,441줄 | 약 50k 토큰 |
| `parser_processor.py` | 110KB / 2,353줄 | 약 31k 토큰 |
| `convert_processor.py` | 77KB / 1,613줄 | 약 22k 토큰 |
| `attachment_processor.py` | 76KB / 1,641줄 | 약 22k 토큰 |
| `intelligent_processor.py` | 72KB / 1,464줄 | 약 21k 토큰 |
| `chunking_processor.py` | 70KB / 1,436줄 | 약 20k 토큰 |
| `facade/chunking/smart_chunker.py` | 71KB / 1,498줄 | 약 20k 토큰 |

**이 파일들을 통째로 Read 하지 말 것.** `Grep` 으로 심볼·문자열 위치를 먼저 찾고, `Read` 의 `offset`/`limit` 으로 해당 구간만 읽는다. 다 읽으면 약 180k 토큰으로 컨텍스트의 상당 부분이 날아간다.

## 아키텍처 제약

- **최상위 processor는 self-contained.** `intelligent_processor.py`, `convert_processor.py` 같은 최상위 processor 파일끼리는 서로 import하지 않는다. 배포본에 포함되는 공용 하위 모듈은 공유할 수 있으므로, 공통 로직을 무조건 복제하지 말고 `build-script/sync-serving-repo.sh` 의 배포 범위를 먼저 확인한다.
- **신규 기능은 공용 하위 모듈에 구현하고 facade는 호출만 한다.** 여러 facade가 쓸 수 있는 로직이면 processor 파일 안에 직접 쓰거나 복붙하지 말고 `facade/chunking/`, `facade/enrichment/`, `facade/guardrail/` 같은 공용 패키지에 모듈을 만든다. facade에는 설정 읽기 한 줄과 호출부만 남긴다. 판정 기준은 "두 번째 facade에 같은 코드를 넣고 싶어지는가"이며, 그렇다면 이미 공용 모듈 대상이다.
  - 설정 해석(yaml/kwargs 우선순위), 판정 헬퍼, 텍스트 변환 같은 부수 로직도 함께 공용 모듈에 둔다. facade마다 `_resolve_*` 헬퍼를 복제하면 그 자체가 새 lockstep 부채가 된다.
  - 공용 모듈은 docling 타입 import를 피하고 duck typing으로 처리한다. 배포본에서 docling 버전에 묶이지 않게 한다.
  - `object.__new__` 로 `__init__` 을 우회해 만든 인스턴스를 쓰는 단위 테스트가 있다. processor 속성을 읽는 공용 헬퍼는 `getattr(..., 기본값)` 으로 속성 부재를 견뎌야 한다.
  - 예: `facade/chunking/text_norm.py`(청크 텍스트 정제) — 활성 processor 3종의 출력 경로 9곳이 이 모듈 하나를 호출한다.
- **청킹 파이프라인은 `facade/chunking/smart_chunker.py` 한 벌이다.** 활성 3종은 ClassVar 플래그만 다른 얇은 서브클래스를 두므로 여기만 고치면 된다. `legacy/BOK_적재용_*` 3종에는 아직 독립 사본이 있으니, 변경이 거기까지 반영돼야 하는지 먼저 판단한다.
- **`GenosServiceException` 은 활성 경로 7곳과 legacy 포함 총 22곳에 복제**되어 있다. 고정 개수를 가정하지 말고 시그니처 변경 전에 `rg -n '^class GenosServiceException' genon/preprocessor/src genon/preprocessor/facade --glob '*.py'` 로 전체 대상을 확인한다. facade가 던진 로컬 예외는 `main.py` 의 제네릭 핸들러가 받는다.
- **docling 안의 결함은 docling 안에서 고친다.** genon 쪽 우회책을 기본 해법으로 삼지 말 것.
  백엔드가 고쳐지면 그 자리를 메우던 genon 우회책은 걷어낸다 — 남겨두면 수정을 가로챈다.
  실례: `html_flatten._lift_table_captions` 가 `<caption>` 을 표 앞 `<p>` 로 옮기는 바람에
  백엔드가 만든 `TableItem.captions` 가 계속 비었고, 표 설명이 사라졌다.
- 설정은 스키마별 키를 늘리기보다 일반화된 메커니즘으로 푼다. allowlist보다 blocklist. YAML은 초보자가 읽을 수 있게 개념 수를 줄인다.

## 테스트

전처리기 테스트는 `genon/preprocessor/` 에서 실행한다 (루트 `pytest.ini` 는 docling 업스트림용이며 `testpaths` 가 존재하지 않는 디렉터리를 가리킨다).

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no
```

마커: `unit`, `smoke`, `regression`, `update_baseline`(일반 실행 제외).

`tests/unit` 전체는 실측 201초다. **바꾼 모듈을 쓰는 테스트 파일만 지정해서 돌린다.**
회귀인지 사전 실패인지 헷갈리면 `git stash push -- <바꾼 파일>` 로 그 테스트만 베이스라인과
대조하고 곧바로 `git stash pop` 한다.

주의:
- `addopts` 에 `-p no:cacheprovider` 가 있어 **`--lf` 를 쓸 수 없다.**
- 출력에 ANSI 색상이 섞여 `grep '^FAILED'` 가 매치되지 않도록 항상 `--color=no` 를 유지한다.
- 검증은 토큰 절약이 우선이다. 본문 전문을 출력하지 말고, 영향 범위에 대한 단정문 테스트로 고정한다.

custom_fields 를 건드렸거나 facade 파싱·청킹 경로를 바꿨으면 doc_type 자동 검증도 함께 돌린다.
실제로 파싱·청킹하고 LLM 을 호출하므로 유닛보다 느리지만, yaml 이 약속한 필드가 실제 청크에
실렸는지는 이것으로만 확인된다.

```bash
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh          # doc_type 20종
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only faq menu
```

설정만 바꿨거나 **배포 전 현장 설정을 검사할 때**는 파싱·LLM 없이 yaml 만 읽는 점검을 쓴다.
extractor 별 지원 키(`facade/enrichment/config_schema.py`)와 같은 판정을 공유하므로 기동 실패를
미리 드러낸다. 청크 본문이 바뀌는 필드(재색인 판단)도 함께 알려준다.

```bash
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh                 # 저장소 resource/
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh <현장_설정_경로>
```

v2 스키마(`schema: v2`)는 아직 **병행 검증 단계**다. 전환하지 말 것 — 아래가 전부 통과한 뒤에
출고 설정을 옮긴다. v1↔v2 왕복과 매퍼 산출을 대조한다(LLM 호출 없음).

```bash
genon/preprocessor/examples/config_precheck/verify_v2_equivalence.sh
```

설정 오기입은 기본적으로 **기동 실패**다. 현장 설정을 미리 검사하지 못한 첫 릴리스에 한해
`GENOS_CUSTOM_FIELDS_VALIDATION=warn` 으로 낮추면 경고만 남기고 기동한다(그 설정은 무시된다).

같은 디렉터리의 `parse_chunk_test.sh` 는 손으로 돌려보는 놀이터다. 대부분이 주석 상태이고
개인 경로에 의존하므로, 자동 검증에 넣을 것은 `parse_chunk_verify.py` 쪽에 단정문으로 옮긴다.

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

### GitHub 작업 흐름

이슈 등록부터 PR 까지는 스킬 3개로 자동화되어 있다. `gh` CLI 인증(`gh auth login`)이 전제조건이다.

| 단계 | 스킬 |
|---|---|
| 이슈 등록 + 작업 브랜치 생성·체크아웃 | `/issue-start <작업 설명>` |
| 작업 중 커밋·푸시 (반복) | `/wip [힌트]` |
| 로컬 테스트 → PR 생성 → CI 확인 | `/open-pr [--draft]` |

컨벤션: 브랜치는 `<type>/<이슈번호>-<slug>` (slug 는 영문), 커밋 메시지는 한국어 한 줄에
conventional prefix 없음, PR 대상은 `develop`, 본문에 `Resolves #N` 을 넣어 머지 시 이슈가 닫히게 한다.

## .claude/ 도구 설정

- `settings.json` — 빌드 산출물·캐시·`code-serving/`·`shkim_labs/` 를 Read 에서 차단하고, `reference/` 와 `code-serving/` 은 Edit 에서 차단한다(`reference/` 는 읽기 전용 참조용이라 Read 는 허용).
- `hooks/pytest-filter.sh` — PreToolUse(Bash) 훅. 단순한 pytest 명령에 `--color=no -p no:randomly` 를 붙이고 PASSED/SKIPPED 줄을 걷어낸다. `set -o pipefail` 로 종료 코드는 보존된다. 실측 11,949바이트 출력이 653바이트로 줄었다. 파이프·`&&` 등이 섞인 복합 명령은 건드리지 않는다.
- `skills/deploy-code-serving`, `skills/create-patch-bundle` — 배포·패치 절차. 필요할 때만 로드된다.
- `skills/issue-start`, `skills/wip`, `skills/open-pr` — GitHub 작업 흐름. 위 "GitHub 작업 흐름" 참조.
- `pyright-lsp` 플러그인(선택). 설치되어 있으면 심볼 정의를 찾을 때 Grep 대신 LSP 탐색을 우선한다. 개인 설정이므로 공유되지 않는다. 쓰려면 `npm install -g pyright` 로 `pyright-langserver` 를 깔고 `claude plugin install pyright-lsp@claude-plugins-official --scope local` 을 실행한다.

## Compact instructions

압축할 때는 변경한 파일 경로와 그 이유, 실행한 검증 명령과 그 결과, 아직 남은 작업을 우선 보존한다. 탐색 과정에서 읽은 파일 내용은 버려도 된다.
