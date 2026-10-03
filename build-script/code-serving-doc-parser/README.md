# code-serving-doc-parser — doc-parser 코드서빙 base 이미지 빌드

doc-parser 의 코드서빙(code-serving) base 이미지를 빌드한다. 런타임에 시스템이 이 이미지를 띄우고
git 소스를 `/app/src/service` 로 clone 해 `main.py`(facade) 를 실행한다. facade 의 무거운 deps 와
다운로드 아티팩트를 **빌드 시점에 pre-bake** 해 콜드스타트 단축 + 에어갭(오프라인) 동작을 보장한다.

> **전처리기(preprocessor) 이미지와 별개**다. 전처리기 빌드/등록은
> [`genon/README.md` "전처리기 빌드 및 등록"](../../genon/README.md#전처리기-빌드-및-등록) /
> [`genon/preprocessor/docker/README.md`](../../genon/preprocessor/docker/README.md) 참고.

## 빌드 방식 (요약)

- **라이브러리는 `uv.lock` 기준으로 설치한다(CPU `Dockerfile`)**: `uv venv --seed` 후 `uv export --frozen` 으로
  뽑은 고정 목록을 `uv pip install --no-deps --torch-backend=cpu -r` 로 설치한다 → venv `/app/.venv`.
  lock 의 버전이 곧 이미지의 버전이므로 CVE 수정본과 핀(numpy 등)이 그대로 실린다.
  export 에서 `opencv-python`(headless 와 중복)과 `nvidia-*`/`triton`/`cuda-*`(CUDA 휠)는 뺀다.
  `Dockerfile.gpu` 는 아직 `uv pip install .` 방식이다.
- **docling 은 이미지에 넣지 않는다**: 런타임에 clone 된 배포본의 `packages/*.whl`(genon-docling)을 `init.sh` 가
  `pip install --no-deps --no-index --force-reinstall` 로 설치한다(네트워크 불필요). wheel 의 의존(저장소 루트
  `pyproject.toml` 의 `[project].dependencies`)이 venv 에 모두 있는지는 이미지 빌드 중 `pip install --dry-run
  --no-index` 로 검사하고, 빠지면 빌드가 실패한다. `requirements.txt` 의 나머지 줄은 `--no-index` 로 먼저 시도하고
  실패할 때만 인덱스로 폴백한다.
- **런타임 `pip install` 도 같은 venv 로**: PATH 상 `pip` 이 `/app/.venv/bin/pip`(`--seed` 가 venv 에 심어둠) 으로
  해석되어 base deps 와 **동일한 `/app/.venv`** 에 설치된다. (별도 시스템 site-packages 로 갈라지지 않음.)
- **의존성 목록은 `genon/preprocessor` 의 uv 정보 기반**(`pyproject.toml`): preprocessor 직접 deps
  (− vendored `docling`) ∪ repo 루트 docling deps ∪ harness 필수(pydantic-settings, python-dotenv).
- **다운로드 아티팩트는 `Dockerfile.standard` 와 full parity** 로 복제(아래).
- **build context = repo 루트** (자기완결 아님): `genon/preprocessor/docker/assets`(HCRBatang 폰트, docling-parse
  패치 헤더) 와 `build-script/hf_private_token.env`(HWP_SDK_TOKEN) 를 재사용.
- 구조: `Dockerfile`(CPU, python:3.12-slim) / `Dockerfile.gpu`(GPU, nvidia/cuda 12.4.1).

## pre-bake 되는 다운로드 아티팩트 (Dockerfile.standard parity)

| 아티팩트 | 위치/환경변수 | 출처 |
|---|---|---|
| Docling 모델 | `/models` (`DOCLING_ARTIFACTS_PATH`) | `docling-tools models download` (models 스테이지서 docling 임시설치) |
| MiniLM 토크나이저 (가중치 제외) | `/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2` | `mncai/doc_parser_models` |
| unstructured yolox 레이아웃 모델 | `/models/unstructured/yolo_x_layout` (`UNSTRUCTURED_DEFAULT_MODEL_INITIALIZE_PARAMS_JSON_PATH`) | HF `unstructuredio/yolo_x_layout` |
| HWP SDK (convtext) | `/app/hwp_sdk` (+ venv 심링크) | HF `genon-search/hwp_sdk` (**HWP_SDK_TOKEN 필요**) |
| rhwp 바이너리 | `/usr/local/bin/rhwp` (`RHWP_BIN`) | `genonai/genos-rhwp` rust 빌드 |
| H2Orestart.oxt | LibreOffice 확장 | GitHub 릴리스 |
| 폰트 | HCRBatang/additional + noto-cjk/nanum/dejavu | docker/assets + HF + apt |
| NLTK 데이터 (`punkt_tab`, `averaged_perceptron_tagger_eng`) | `/app/nltk_data` (`NLTK_DATA`) | `nltk.download` |
| EasyOCR korean_g2 | `/models/EasyOcr` | JaidedAI 릴리스 |
| docling-parse 4.1.0 패치 | `/app/.venv/.../site-packages/docling_parse` | 소스 패치 빌드(#245), venv 인터프리터로 설치해 wheel 덮어씀 |

## 빌드

### 1) 토큰 설정 (1회, Git 미추적)

HWP SDK 다운로드용 HF 토큰을 전처리기와 **동일한 파일**에 둔다:

```bash
echo "HWP_SDK_TOKEN=hf_xxx" >> build-script/hf_private_token.env
```

(토큰 발급은 [genon/README "전처리기 빌드 및 등록" 1번](../../genon/README.md#전처리기-빌드-및-등록) 참고.)

### 2) 설정 & 실행

```bash
# build.config 에서 HW_VARIANT(cpu|gpu)/버전/푸시여부 설정 후
bash build-script/code-serving-doc-parser/build.sh
```

- 태그: cpu → `mncregistry:30500/mnc/doc-parser-serving:<버전>`, gpu → `...:<버전>-gpu`.
- 빌드 직후 smoke: deps import + torch GPU/CPU variant + 아티팩트 존재 + **`docling_parse` 가 `/app/.venv`
  하위에 설치됐는지**(패치본 위치) 검증.
- 먼저 `PUSH_IMAGE=false` 로 로컬 검증 후 `true` 로 push.

## 의존성/아티팩트 동기화 주의

- `pyproject.toml` 의 deps 는 `genon/preprocessor/pyproject.toml` + repo 루트 docling deps 기반이다.
  그쪽이 바뀌면 여기 `pyproject.toml` 갱신 후 `uv lock` 재생성. CPU 이미지는 lock 그대로 설치되므로
  보안 수정본을 반영하려면 `uv lock --upgrade`(또는 `--upgrade-package <이름>`)로 lock 을 갱신해야 한다.
- repo 루트 docling deps 를 바꾸고 이 목록에 반영하지 않으면 이미지 빌드의 docling wheel 의존 충족 검사가 실패한다.
- 다운로드 단계는 `genon/preprocessor/docker/Dockerfile.standard` 와 ~중복이다. standard 의 아티팩트
  버전(모델/H2Orestart/rhwp ref 등)이 바뀌면 이 Dockerfile 들도 **수동 동기화** 필요.
- GPU(`Dockerfile.gpu`) 는 ubuntu 22.04(jammy) base 라 apt 패키지명/버전(특히 `openjdk-21`,
  `libgdk-pixbuf`)이 다를 수 있다. 첫 빌드 실패 시 해당 패키지명만 조정.
- 스테이지 큐(Temporal activity) 워커용 SDK `temporalio` 를 CPU·GPU 이미지 모두에 미리 설치한다.
  워커 코드(`genon/preprocessor/stage_worker/`)는 배포본과 함께 런타임에 들어오므로 이미지에는 SDK 만 둔다.
  워커는 `TEMPORAL_HOST` 를 줄 때만 띄우도록 설계되어 있다(스테이지 큐 적용계획 7-10, 런처는 별도 작업).
  base 의 supervisord 설정은 바꾸지 않는다(기동 명령은 현장이 `START_COMMAND` 로 지정한다).

## 구성

- `Dockerfile` / `Dockerfile.gpu` — CPU / GPU base 이미지.
- `pyproject.toml` / `uv.lock` — uv 환경(의존성). preprocessor uv 정보 기반.
- `build.sh` / `build.config` — 빌드 스크립트/설정 (context = repo 루트, HWP_SDK_TOKEN secret).
- `scripts/` `supervisor/` `src/` `gunicorn/` — 코드서빙 harness (genon/code-serving 형태).
