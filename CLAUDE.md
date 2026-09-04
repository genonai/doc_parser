# doc_parser

docling(v2.41.0) 포크 위에 GenOn 전처리기(genon/preprocessor)를 올린 문서 파싱·청킹 서비스.

## 저장소 지도

| 경로 | 역할 |
|---|---|
| `main.py` | 통합·로컬 실행 진입점. FastAPI 앱, 업무 API 7개와 health |
| `genon/preprocessor/facade/` | 처리 로직 본체 — 최상위 `*_processor.py` 5종 |
| `genon/preprocessor/facade/{common,chunking,enrichment,guardrail}/` | facade가 공유하는 공용 하위 모듈. 배포본에 포함된다 |
| `genon/preprocessor/src/` | 공통 모듈(`common`, `logger`, `config`, `utils`) + 단일 프로세서 진입점 |
| `genon/preprocessor/resource/`, `resource_dev/` | 운영 / 로컬개발 YAML 설정 (프로세서 설정 + `custom_field_*.yaml`) |
| `genon/preprocessor/converters/` | 입력 전처리 변환기 (`html_flatten`, `json_text`, `md_marker_headings` 등) |
| `genon/preprocessor/tests/` | **전처리기 테스트** (`unit/`, `smoke/`, `regression/`) |
| `genon/preprocessor/examples/` | 손으로 돌리는 검증 스크립트 (테스트 절 참조) |
| `docling/` | 포크된 docling 본체. 백엔드·파이프라인 수정은 여기 |
| `tests/` | docling 업스트림 테스트 |
| `build-script/` | 도커 이미지 빌드, 코드서빙 저장소 동기화, 핫픽스 패치 번들 생성. 저장소의 셸 스크립트는 전부 여기 둔다 |

**수정 금지 / 탐색 제외** (모두 gitignore됨): `reference/`, `shkim_labs/`, `dist/`, `build/`, `debug/`, `tmp/`, `code-serving/`.
`facade/legacy/` 는 **기본적으로 참조하지 않는다** — 별도 배포 단위이므로 활성 경로 작업에 끌어들이지 말고, 사용자가 명시적으로 언급할 때만 읽는다.
저장소 전체가 11G / 6만 파일 이상이다. 대형 fixture·노트북 제외는 루트 `.ignore` 가
자동으로 해주므로(`rg` 와 `Grep` 툴 양쪽에 적용) 글롭을 손으로 붙일 필요는 없다.

```bash
rg '<pattern>' main.py genon docling tests build-script
```

`.ignore` 가 빼는 것은 `tests/data/`, `tests/data_scanned/`, `*.pages.json`, `*.ipynb` 이고
`reference/`·`shkim_labs/`·`code-serving/` 은 gitignore 로 이미 빠진다. 일반적인 패턴에서
히트의 20~47% 가 이 노이즈다(실측: `table` 562→298, `ocr` 185→129).
그 안을 봐야 하면 `rg --no-ignore-dot` 을 쓴다 — `-u`/`--no-ignore` 는 gitignore 까지 풀려
`shkim_labs`(5.0G) 를 훑으므로 쓰지 않는다.

## 진입점 2개

**루트 `main.py`** — facade 5종을 한 서버에 올리는 통합·로컬 실행 형태. 라우트별 대응 facade:

| 라우트 | facade |
|---|---|
| `/preprocess`, `/preprocess_intelligent` | `intelligent_processor.py` (전자는 하위호환 별칭) |
| `/preprocess_attachment` | `attachment_processor.py` |
| `/preprocess_convert` | `convert_processor.py` |
| `/parser`, `/parser_upload` | `parser_processor.py` |
| `/chunker` | `chunking_processor.py` |

**`genon/preprocessor/src/main.py`** — **실제 외부 사이트 적용 환경**은 이쪽이다.

- 배포 대상 facade **한 개**만 `preprocessor.py` 로 이름이 바뀌어 마운트되고, `from preprocessor import DocumentProcessor` 로 로드되어 `/run` 으로 진입한다.
- `/parser`, `/chunker` 는 프로세서의 `IS_PARSER` / `IS_CHUNKER` 속성으로 게이팅된다. 속성이 없으면 "지원하지 않습니다" 응답.
- `legacy/` 코드들(BOK 적재용, 법령, 코레일 등)은 로컬 진입점이 없고 이 경로로만 배포·실행된다.

이 구조가 아래 "아키텍처 제약"의 근거다. 특히 `main.py` 계층 기능(요청 deadline, 에러 envelope 의 `stage`/`error_kind` 등)을 바꿀 때는 **두 파일 모두** 확인해야 한다.

## 공용 하위 모듈 지도

facade가 공유하는 로직은 아래에 한 벌씩만 둔다. 최상위 processor는 별칭이나 얇은 호출부만 갖는다.
새 로직을 어디 둘지 고민되면 이 표에서 가장 가까운 모듈을 먼저 찾는다 (표는 요약이므로 실제 파일 목록은 `ls` 로 확인).

| 모듈 | 역할 |
|---|---|
| `common/config_parse.py` | yaml/kwargs 값 파싱(`parse_optional_bool/int/float`), `load_config`, 토크나이저·청크크기·`compact_tables` 등 설정 해석 |
| `common/file_probe.py`, `format_alias.py` | 파일 종류 판별(PDF/텍스트/암호화/HWP 보호), PDF 경로 변환, 확장자 별칭 |
| `common/pdf_convert.py` | 비-PDF 입력의 PDF 변환 진입점. backend chain 순서 + 이슈 #286 사전 체크 |
| `common/docling_ops.py` | docling 배관 — OCR 옵션, 컨버터 생성, 표 이미지 저장, 글리프·빈 텍스트 검사 |
| `common/pipeline_setup.py`, `runtime_kwargs.py` | `__init__` 의 OCR·PDF·layout 해석 / 런타임 kwargs 정규화·이미지 모드 배선 |
| `common/vector_meta.py`, `doc_meta.py` | `GenOSVectorMeta` 빌더 공통 코어, 문서 메타 |
| `common/loaders.py`, `markdown_export.py`, `runtime.py`, `appendix.py` | 로더(`install_packages`, Text/Tabular/Audio), 마크다운 내보내기, 로깅 초기화, 별첨 키워드 판정 |
| `chunking/smart_chunker.py` | `GenosSmartChunker` 본체. 활성 3종이 ClassVar 플래그만 다른 서브클래스로 상속 |
| `chunking/hybrid_chunker.py` | docling_core `HybridChunker`/`HierarchicalChunker` 포크본. 이름을 `TokenAwareHybridChunker`/`HierarchicalDocChunker` 로 달리해 업스트림과 구분한다. 갈라진 축은 모듈 docstring 참조 |
| `chunking/table_*.py`, `rich_cells.py` | 표 행 분할·모양 판정·HTML 표 직렬화·변형 처리 |
| `chunking/header_path.py`, `page_split.py`, `doc_prefix.py`, `text_norm.py` | 청크 헤더 경로, 페이지 분할, 문서 접두, 청크 텍스트 정제 |
| `enrichment/`, `guardrail/` | custom_fields·LLM 보강, 민감정보 처리 |

## 큰 파일 취급 규칙 (중요)

facade 프로세서는 공용 모듈 추출로 절반 이하로 줄었지만(2026-09-04 기준 5종 합계 8,702줄) 여전히 크다.
아래는 전체 Read 가 **20k~50k 토큰**씩 드는 파일들이고, 다 읽으면 190k 토큰으로 컨텍스트의 상당 부분이 날아간다.

- `docling/backend/html_backend.py` (~4,500줄, 단독 50k 토큰)
- `facade/parser_processor.py` (~2,500줄)
- `facade/chunking/smart_chunker.py`, `facade/{convert,attachment,intelligent,chunking}_processor.py` (각 ~1,500줄)

**통째로 Read 하지 말 것.** `Grep` 으로 심볼·문자열 위치를 먼저 찾고 `Read` 의 `offset`/`limit` 으로 해당 구간만 읽는다.

## 수정 범위 (중요)

**보고된 결함 하나를 고치는 데 필요한 최소 변경만 한다.** 조사 중 다른 결함이 눈에 띄는 것은
정상이지만, 그것을 같은 변경에 끌어들이지 않는다. 발견 사실은 보고하고 별도 이슈로 넘긴다.

계획을 세운 뒤 **적용 전에** 아래를 스스로 검증한다.

1. **각 변경이 보고된 결함에 필요한가** — "이걸 빼면 보고된 증상이 남는가?" 로 판정한다.
   남지 않으면 그 변경은 이번 작업의 것이 아니다.
2. **근본 원인 한 곳을 고쳤는가, 증상마다 덧댔는가** — 배선 한 줄이면 되는 것을 새 모듈로
   풀고 있지 않은지 본다.
3. **고칠 자리가 맞는가** — 파괴적 동작은 값을 만드는 쪽이 아니라 **쓰는 쪽**에서 막는 편이
   작고 안전한 경우가 많다. 공유 헬퍼의 반환 계약을 바꾸면 그 계약에 기대는 호출부 전부가
   영향권이다. 바꾸기 전에 `rg` 로 호출부를 세어 본다.
4. **영향 범위가 결함 범위보다 넓지 않은가** — 특정 doc_type/확장자 하나를 고치는데 모든
   문서의 파싱 결과가 바뀌면 과하다.
5. **일반화가 새 위험을 만들지 않는가** — "무조건 처리" 류의 확장은 의도하지 않은 입력까지
   끌어온다. 설정 한 줄로 되는 일을 코드 판정 로직으로 풀지 않는다
   (`formats.extension_aliases` 가 그 목적의 기구다).

실례(2026-09-04, `*.parsed` 확장자 처리): 실제 원인은 `DocumentProcessor.__init__` 이
`_ext_aliases` 를 임베디드 intel 사본에서 가져오지 않은 **한 줄**이었는데, 곁가지까지 함께 고쳐
205줄(6파일 + 신규 모듈 2개)이 됐다. 되돌린 4건과 그 이유는 각각 위 기준에 대응한다.

- 내용으로 포맷 판정(74줄) — 설정 별칭이 이미 하는 일. `- item` 으로 시작하는 yaml/평문이 md 백엔드로 잘못 가 본문이 소실됐다(기준 5)
- `get_pdf_path` 반환 규칙 변경 — 원본 파괴를 다른 파괴로 옮겼다(파생 PDF 잔류·덮어쓰기). 고칠 자리는 쓰는 쪽이었다(기준 3)
- docling `html_backend.get_text`(40줄) — 표기 차이일 뿐 유실이 아니었고 영향 범위가 모든 HTML 표 파싱(기준 1·4)
- artifacts 경로 공용 모듈 + 파사드 5곳 배선(70줄) — 필요한 것은 파사드 한 곳의 3줄이었다(기준 2)

최종은 `parser_processor.py` 11줄 + yaml 설정이었다. **판정을 서브에이전트에게 한 번 검증받는
것이 값을 했다** — 위 4건 중 3건은 자체 검토에서 놓쳤다.

## 아키텍처 제약

- **최상위 processor는 self-contained.** 배포 대상 `*_processor.py` 는 하나이므로 최상위 processor 파일끼리 서로 import하면 배포본에서 깨진다. 배포본에 포함되는 공용 하위 모듈은 공유할 수 있으니 무조건 복제하지 말고 `build-script/sync-serving-repo.sh` 의 배포 범위를 먼저 확인한다.
- **신규 기능은 공용 하위 모듈에 구현하고 facade는 호출만 한다.** 여러 facade가 쓸 수 있는 로직이면 processor 파일에 직접 쓰거나 복붙하지 말고 `facade/{common,chunking,enrichment,guardrail}/` 에 모듈을 만든다. facade에는 설정 읽기 한 줄과 호출부만 남긴다. 판정 기준은 "두 번째 facade에 같은 코드를 넣고 싶어지는가"이며, 그렇다면 이미 공용 모듈 대상이다.
  - 설정 해석(yaml/kwargs 우선순위), 판정 헬퍼, 텍스트 변환 같은 부수 로직도 함께 공용 모듈에 둔다. facade마다 `_resolve_*` 헬퍼를 복제하면 그 자체가 새 lockstep 부채다.
  - 공용 모듈은 docling 타입 import를 피하고 duck typing으로 처리한다. 배포본이 docling 버전에 묶이지 않게 한다.
  - `object.__new__` 로 `__init__` 을 우회해 만든 인스턴스를 쓰는 단위 테스트가 있다. processor 속성을 읽는 공용 헬퍼는 `getattr(..., 기본값)` 으로 속성 부재를 견뎌야 한다.
  - 예: `facade/chunking/text_norm.py`(청크 텍스트 정제) — 활성 processor 3종의 출력 경로 9곳이 이 모듈 하나를 호출한다.
- **청킹 파이프라인은 `facade/chunking/smart_chunker.py` 한 벌이다.** 활성 3종은 ClassVar 플래그만 다른 얇은 서브클래스이므로 여기만 고치면 된다. `legacy/BOK_적재용_*` 3종은 별도 배포 단위라 자체 사본과 모듈 상수 설정을 유지하니, 변경이 거기까지 반영돼야 하는지 먼저 판단한다.
- **`GenosServiceException` 은 활성 경로 7곳과 legacy 포함 총 22곳에 복제**되어 있다. 고정 개수를 가정하지 말고 시그니처 변경 전에 `rg -n '^class GenosServiceException' genon/preprocessor/src genon/preprocessor/facade --glob '*.py'` 로 전체 대상을 확인한다. facade가 던진 로컬 예외는 `main.py` 의 제네릭 핸들러가 받는다.
- **docling 은 되도록 수정하지 않는다.** 포크 본체를 건드리면 영향 범위가 그 백엔드를 쓰는 모든 문서로 퍼지고 배포에 wheel 재빌드가 강제된다(핫픽스 overlay 는 genon 전용). **그 docling 결함을 고치는 것이 이번 작업의 목표일 때만** 손댄다. 조사 중 우연히 발견한 docling 결함은 별도 이슈로 분리한다.
  - 고쳐야 할 때가 되면 **genon 우회책이 아니라 docling 안에서** 고친다 — 우회책을 기본 해법으로 삼으면 나중에 진짜 수정을 가로챈다. 백엔드가 고쳐지면 그 자리를 메우던 우회책은 걷어낸다.
  - 실례: `html_flatten._lift_table_captions` 가 `<caption>` 을 표 앞 `<p>` 로 옮기는 바람에 백엔드가 만든 `TableItem.captions` 가 계속 비었고, 표 설명이 사라졌다.
- 설정은 스키마별 키를 늘리기보다 일반화된 메커니즘으로 푼다. allowlist보다 blocklist. YAML은 초보자가 읽을 수 있게 개념 수를 줄인다.

## 테스트

전처리기 테스트는 `genon/preprocessor/` 에서 실행한다 (루트 `pytest.ini` 는 docling 업스트림용이며 `testpaths` 가 존재하지 않는 디렉터리를 가리킨다). 마커는 `unit`, `smoke`, `regression`, `update_baseline`(일반 실행 제외).

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no
```

`tests/unit` 전체는 실측 201초다. **바꾼 모듈을 쓰는 테스트 파일만 지정해서 돌린다.**
회귀인지 사전 실패인지 헷갈리면 `git stash push -- <바꾼 파일>` 로 그 테스트만 베이스라인과
대조하고 곧바로 `git stash pop` 한다.

주의:
- `addopts` 에 `-p no:cacheprovider` 가 있어 **`--lf` 를 쓸 수 없다.**
- 출력에 ANSI 색상이 섞여 `grep '^FAILED'` 가 매치되지 않도록 항상 `--color=no` 를 유지한다.
- 검증은 토큰 절약이 우선이다. 본문 전문을 출력하지 말고, 영향 범위에 대한 단정문 테스트로 고정한다.

**doc_type 자동 검증** — custom_fields 를 건드렸거나 facade 파싱·청킹 경로를 바꿨으면 함께 돌린다. 실제로 파싱·청킹하고 LLM 을 호출해 유닛보다 느리지만, yaml 이 약속한 필드가 실제 청크에 실렸는지는 이것으로만 확인된다.

```bash
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh          # 케이스 23건 / doc_type 14종
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only faq menu
```

**설정 점검(파싱·LLM 없음)** — 설정만 바꿨거나 **배포 전 현장 설정을 검사할 때** 쓴다. extractor 별 지원 키(`facade/enrichment/config_schema.py`)와 같은 판정을 공유하므로 기동 실패를 미리 드러내고, 청크 본문이 바뀌는 필드(재색인 판단)도 알려준다.

```bash
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh   # 인자로 현장 설정 경로 지정 가능
genon/preprocessor/examples/config_precheck/verify_v2_equivalence.sh    # v1↔v2 왕복·매퍼 산출 대조
```

v2 스키마(`schema: v2`)는 아직 **병행 검증 단계다. 전환하지 말 것** — 위 검증이 전부 통과한 뒤에 출고 설정을 옮긴다.
설정 오기입은 기본적으로 **기동 실패**다. 현장 설정을 미리 검사하지 못한 첫 릴리스에 한해 `GENOS_CUSTOM_FIELDS_VALIDATION=warn` 으로 낮추면 경고만 남기고 기동한다(그 설정은 무시된다).

같은 디렉터리의 `parse_chunk_test.sh` 는 손으로 돌려보는 놀이터다. 대부분 주석 상태이고 개인 경로에 의존하므로, 자동 검증에 넣을 것은 `parse_chunk_verify.py` 쪽에 단정문으로 옮긴다.

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

이슈 등록부터 PR 까지 스킬 3개로 자동화되어 있다(`gh auth login` 전제).
`/issue-start <작업 설명>` (이슈 등록 + 브랜치 생성·체크아웃) → `/wip [힌트]` (커밋·푸시, 반복) → `/open-pr [--draft]` (로컬 테스트 → PR → CI 확인).

컨벤션: 브랜치는 `<type>/<이슈번호>-<slug>`(slug 는 영문), 커밋 메시지는 한국어 한 줄에
conventional prefix 없음, PR 대상은 `develop`, 본문에 `Resolves #N` 을 넣어 머지 시 이슈가 닫히게 한다.

## .claude/ 도구 설정

- `settings.json` — 빌드 산출물·캐시·`code-serving/`·`shkim_labs/` 를 Read 에서 차단하고, `reference/` 와 `code-serving/` 은 Edit 에서 차단한다(`reference/` 는 읽기 전용 참조용이라 Read 는 허용).
- `.ignore` — `rg`/`Grep` 전용 제외 목록. 위 "저장소 지도" 절 참조.
- `hooks/pytest-filter.sh` — PreToolUse(Bash) 훅. 단순한 pytest 명령에 `--color=no -p no:randomly` 를 붙이고 PASSED/SKIPPED 줄을 걷어낸다(실측 11,949→653바이트, 종료 코드는 `pipefail` 로 보존). 파이프·`&&` 가 섞인 복합 명령은 건드리지 않는다.
  - 실행 위치도 교정한다. `tests/unit|smoke|regression` 인자가 실행 기준 디렉터리에 없고 `genon/preprocessor` 아래에 있으면 절대경로 `cd` 를 앞에 붙인다. 훅은 Bash 툴이 유지하는 cwd 를 볼 수 없어서(입력의 `cwd` 는 늘 루트) 차단 대신 교정으로 처리한다 — 차단 방식은 cwd 가 이미 `genon/preprocessor` 인 정상 명령을 막았다. 복합 명령에는 적용되지 않는다.
- `hooks/large-file-read-guard.sh` — PreToolUse(Read) 훅. 1,200줄 초과 텍스트 파일을 `offset`/`limit` 없이 Read 하려 하면 거부하고 Grep 선행을 요구한다. 활성 `.py` 393개 중 26개가 대상이며 "큰 파일 취급 규칙" 절의 7개 파일이 모두 포함된다. 전체가 정말 필요하면 `offset=1 limit=<줄수>` 로 의도를 명시하면 통과한다.
- `agents/scope-check.md` — "수정 범위" 절의 판정 기준 5개를 고정한 서브에이전트. 변경 적용 전이나 PR 직전에 diff/계획의 범위 과잉만 판정한다(버그 사냥은 범위 밖).
- 스킬 — `deploy-code-serving`, `create-patch-bundle`(배포·패치 절차), `issue-start`/`wip`/`open-pr`(위 GitHub 흐름). 필요할 때만 로드된다.
- `pyright-lsp` 플러그인(선택, 개인 설정이라 미공유). 설치되어 있으면 심볼 정의 탐색에 Grep 대신 LSP 를 우선한다. `npm install -g pyright` 후 `claude plugin install pyright-lsp@claude-plugins-official --scope local`.

## Compact instructions

압축할 때는 변경한 파일 경로와 그 이유, 실행한 검증 명령과 그 결과, 아직 남은 작업을 우선 보존한다. 탐색 과정에서 읽은 파일 내용은 버려도 된다.
