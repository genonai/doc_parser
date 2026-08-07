# syntax=docker/dockerfile:1.7
#
# code-serving-doc-parser (CPU) — doc-parser 코드서빙 base 이미지.
#
# 라이브러리 설치 방식은 genon/code-serving 과 동일: `uv venv --seed` + `uv pip install .` → /app/.venv.
# 다운로드 아티팩트(모델/HWP SDK/rhwp/H2Orestart/폰트/NLTK/EasyOCR)는
# genon/preprocessor/docker/Dockerfile.standard 와 full parity 로 복제한다.
#
# build context = repo 루트 (build-script/code-serving-doc-parser/build.sh 가 ROOT_DIR=repo 루트 사용).
# 따라서 모든 COPY 는 repo 루트 기준 경로. HWP_SDK_TOKEN 은 --secret 로 주입.

############################
# 0) 공통 ARG/ENV
############################
ARG PY_VER=3.12.12
ARG DEBIAN_DIST=trixie
ARG H2ORESTART_URL="https://github.com/ebandal/H2Orestart/releases/latest/download/H2Orestart.oxt"
ARG APP_VENV=/app/.venv
ARG NLTK_PACKAGES=all
ARG UID=1000
ARG GID=1000
ARG UNAME=genos
ARG GNAME=genos
ARG HW_VARIANT=cpu
ARG TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG RHWP_GIT_URL=https://github.com/genonai/genos-rhwp.git
ARG RHWP_GIT_REF=genos/main
# models_artifacts 단계에서 모델 다운로드용으로만 임시 설치하는 docling 패키지 버전
# (vendored docling 과 동일 계열. 런타임 venv 에는 포함되지 않음)
ARG DOCLING_PKG_VER=2.41.0

############################
# 1) Base (APT + LibreOffice) — Dockerfile.standard 와 동일 세트
############################
FROM python:${PY_VER}-slim-${DEBIAN_DIST} AS base
# NOTE: libspatialindex 는 rtree 가 libspatialindex_c.so 를 dlopen 할 때만 필요하므로
#   런타임 패키지(-c8)로 충분하다 (-dev 는 헤더뿐이라 런타임 이미지에 둘 이유가 없다).
#   ⚠️ 패키지명이 배포판마다 다르다: Debian trixie = libspatialindex-c8,
#      Ubuntu 22.04(jammy) = libspatialindex-c6 (Dockerfile.gpu 가 쓰는 이름).
#      서로 바꿔 쓰면 apt 가 패키지를 못 찾아 빌드가 깨진다.
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    HOME=/app \
    XDG_CACHE_HOME=/app/.cache \
    XDG_CONFIG_HOME=/app/.config \
    XDG_DATA_HOME=/app/.local/share \
    XDG_RUNTIME_DIR=/tmp/xdg \
    TMPDIR=/app/tmp \
    HF_HOME=/app/.cache/huggingface \
    NLTK_DATA=/app/nltk_data
# NOTE: /var/cache/apt 와 /var/lib/apt/lists 는 cache mount 라 **이미지 레이어에 포함되지 않는다.**
#   예전에는 이 RUN 끝에서 `rm -rf /var/lib/apt/lists/*` 를 했는데, 이미지 크기에는 아무 이득이 없고
#   다음 빌드가 재사용할 apt 인덱스 캐시만 파괴했다. 그래서 제거했다.
#   ⚠️ cache mount 가 없는 다른 apt RUN(rhwp 빌더 등)의 rm 은 실제로 레이어를 줄이므로 유지해야 한다.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    echo "deb http://deb.debian.org/debian sid main" > /etc/apt/sources.list.d/sid.list && \
    printf "Package: python3.13 python3.13-minimal libpython3.13 libpython3.13-minimal libpython3.13-stdlib\nPin: release n=sid\nPin-Priority: 990\n\nPackage: *\nPin: release n=sid\nPin-Priority: 100\n" > /etc/apt/preferences.d/pin-python3.13 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
      net-tools curl wget git fontconfig procps supervisor vim \
      libmagic1 libmagic-dev \
      libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-xlib-2.0-0 \
      libffi-dev libgl1 \
      libreoffice ffmpeg unzip \
      openjdk-21-jre-headless ca-certificates-java \
      libreoffice-java-common libreoffice-writer libreoffice-impress libreoffice-l10n-ko \
      fonts-dejavu fonts-noto-cjk fonts-noto-cjk-extra fonts-nanum \
      imagemagick poppler-utils \
      tesseract-ocr tesseract-ocr-eng tesseract-ocr-kor \
      libspatialindex-c8 \
    && apt-get install -y --no-install-recommends --only-upgrade \
      imagemagick imagemagick-7.q16 libmagickcore-7.q16-10 libmagickwand-7.q16-10 \
    && mkdir -p /app/.cache/huggingface /app/.cache/fontconfig /app/.config /app/.local/share /app/.fontconfig /app/nltk_data /app/tmp /tmp/xdg
RUN sed -i 's/# \(ko_KR.UTF-8\)/\1/' /etc/locale.gen \
    && locale-gen \
    && fc-cache -f -v || true

############################
# 2) LibreOffice 확장 + HCRBatang 폰트 (Dockerfile.standard 와 동일)
#    resources 에서 HCRBatang 폰트가 들어온다.
############################
FROM base AS loext
ARG H2ORESTART_URL
RUN --mount=type=cache,target=/opt/h2o_cache \
    set -eux; \
    mkdir -p /opt/h2orestart /usr/lib/libreoffice/share/extensions/h2orestart; \
    if [ ! -f /opt/h2o_cache/H2Orestart.oxt ]; then \
      wget -O /opt/h2o_cache/H2Orestart.oxt "${H2ORESTART_URL}"; \
    fi; \
    cp /opt/h2o_cache/H2Orestart.oxt /opt/h2orestart/H2Orestart.oxt; \
    unzip -q -o /opt/h2orestart/H2Orestart.oxt -d /usr/lib/libreoffice/share/extensions/h2orestart; \
    soffice --headless --nologo --nodefault --nofirststartwizard --terminate_after_init
# HCRBatang 폰트 — bind mount 로 필요한 파일만 읽는다.
# COPY 로 가져오면 다음 레이어에서 rm 해도 COPY 레이어(11.6MB)가 회수되지 않는다.
RUN --mount=type=bind,source=genon/preprocessor/resources/HCRBatang.ttf.tar.gz,target=/tmp/HCRBatang.ttf.tar.gz \
    set -eux; \
    mkdir -p /usr/share/fonts && \
    tar zxf /tmp/HCRBatang.ttf.tar.gz -C /usr/share/fonts/ && \
    fc-cache -f -v

############################
# 3) Builder — venv 설치는 genon/code-serving 방식 (uv venv --seed + uv pip install .)
############################
FROM loext AS builder
ARG APP_VENV
ARG HW_VARIANT
ARG TORCH_CPU_INDEX_URL
ENV VIRTUAL_ENV=${APP_VENV} \
    PATH="${APP_VENV}/bin:${PATH}" \
    HOME=/app \
    XDG_CACHE_HOME=/app/.cache \
    HF_HOME=/app/.cache/huggingface \
    NLTK_DATA=/app/nltk_data

# uv 버전을 핀한다. 아래 `uv pip install . --torch-backend=cpu` 가 --torch-backend 에 의존하므로
# uv 버전이 빌드 동작에 직접 영향을 준다(0.8.0 에 존재함을 확인).
ARG UV_VERSION=0.8.0
RUN pip install "uv==${UV_VERSION}"

WORKDIR /app
# 의존성 정의는 build-script/code-serving-doc-parser 의 pyproject/uv.lock (preprocessor uv 정보 기반).
COPY build-script/code-serving-doc-parser/pyproject.toml build-script/code-serving-doc-parser/uv.lock /app/

# genon/code-serving 와 동일: uv venv --seed 로 /app/.venv 생성 후 uv pip install . 로 venv 에 설치.
#
# --torch-backend=cpu: 처음부터 PyTorch CPU 인덱스에서 받는다.
#   이게 없으면 PyPI 기본(CUDA) torch 가 잡혀 nvidia 휠 15종 + triton 약 4GB 가 함께 내려오고,
#   아래 HW_VARIANT=cpu 단계에서 전부 버려진다. 콜드 빌드 시간의 대부분이 이 다운로드였다.
#   (preprocessor 쪽은 uv sync 를 써서 --torch-backend 가 없어 --no-install-package 로 처리한다.)
# ⚠️ /app/tmp 를 지운 뒤 반드시 다시 만들어야 한다. base 스테이지가 ENV TMPDIR=/app/tmp 를
#    설정하므로, 디렉토리가 없으면 uv 가 임시 파일을 만들지 못해
#    `error: No such file or directory (os error 2) at path "/app/tmp/.tmpXXXX"` 로 죽는다.
#    (uv 버전을 0.8.0 으로 핀하면서 드러났다 — 이전 미핀 uv 는 TMPDIR 을 자동 생성했다.)
RUN --mount=type=cache,target=/root/.cache/uv \
    rm -rf /app/tmp /app/nltk_data && \
    mkdir -p /app/tmp && \
    uv venv --seed && \
    uv pip install . --torch-backend=cpu

# HW_VARIANT=cpu — nvidia-*/triton 잔존 제거 (안전망).
#
# 위 `uv pip install . --torch-backend=cpu` 가 이미 CPU torch 를 넣으므로 여기서 torch 를
# 재설치하지 않는다(예전에는 --reinstall-package 로 같은 CPU 휠을 약 190MB 다시 받았다).
# 이 블록은 --torch-backend 가 어떤 이유로 CUDA 휠을 들여보냈을 때를 대비한 **안전망**이다.
#
# ⚠️ 제거 목록을 하드코딩하지 말 것. 예전에는 `nvidia-cublas-cu12` 처럼 `-cu12` 접미사
#    목록을 박아두었는데, 버전 핀이 없어 torch 2.13 이 잡히면서 CUDA 13 휠로 패키지명이
#    바뀌었다(`nvidia_cublas` 접미사 없음, `nvidia_cudnn_cu13`, `nvidia_nccl_cu13` …).
#    이름이 하나도 일치하지 않아 uninstall 이 전부 실패했고, 끝의 `|| true` 가 그 실패를
#    삼켜서 CPU 이미지에 CUDA 라이브러리 2.7GB 가 조용히 실려 나갔다.
#    → 설치된 목록에서 이름을 뽑아 지우고, `|| true` 로 실패를 감추지 않는다.
#    (지울 게 없으면 xargs -r 이 uv 를 호출하지 않으므로 그대로 성공한다.)
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    if [ "${HW_VARIANT}" = "cpu" ]; then \
      echo "[INFO] HW_VARIANT=cpu — nvidia/triton 잔존 확인 및 제거"; \
      uv pip list --python ${APP_VENV}/bin/python --format=freeze \
        | sed -n 's/^\(nvidia[A-Za-z0-9._-]*\|triton\)==.*/\1/p' \
        | tee /tmp/cuda_pkgs.txt; \
      xargs -r uv pip uninstall --python ${APP_VENV}/bin/python < /tmp/cuda_pkgs.txt; \
      rm -f /tmp/cuda_pkgs.txt; \
      echo "[INFO] 남은 nvidia/triton 패키지 확인:"; \
      uv pip list --python ${APP_VENV}/bin/python --format=freeze \
        | grep -iE '^(nvidia|triton)' && { echo "[ERROR] nvidia/triton 잔존"; exit 1; } || true; \
    else \
      echo "[INFO] HW_VARIANT=${HW_VARIANT} — keeping torch as-is"; \
    fi

# torch 의 런타임 미사용 자산 제거 (약 141MB: test 81M + include 60M).
# C++ 헤더와 테스트 픽스처로, 추론 경로에서는 쓰이지 않는다.
# ⚠️ torch/bin(50MB)은 지우면 안 된다. torch/__init__.py 가 import 시점에 _manager_path() 로
#    torch/bin/torch_shm_manager 존재를 무조건 확인하고, 없으면 RuntimeError 로 죽는다
#    (DataLoader 를 쓰지 않아도 발생).
# ⚠️ 코드서빙 특유의 위험: 런타임에 git clone 되는 **외부 서비스 코드**가
#    `torch.utils.cpp_extension`(JIT C++ 확장 컴파일)을 쓰면 torch/include 가 없어 실패한다.
#    현재 이 레포에는 사용처가 없지만, 서비스 코드는 이 이미지 밖에서 오므로 보장할 수 없다.
#    그런 서비스를 붙이려면 이 RUN 에서 include 제거를 빼야 한다.
RUN rm -rf ${APP_VENV}/lib/python3*/site-packages/torch/test \
           ${APP_VENV}/lib/python3*/site-packages/torch/include

# opencv 를 headless 로 통일한다 (Dockerfile.standard 와 동일 조치).
#   opencv-python(non-headless)과 opencv-python-headless 가 둘 다 들어와 `opencv_python.libs`
#   (약 116MB)가 중복이고, non-headless 가 libGL.so.1 에 링크해 apt `libgl1` → Mesa 소프트웨어
#   GL 스택(약 208MB)까지 끌어온다. 헤드리스 컨테이너에는 불필요해 apt 에서도 libgl1 을 뺐다.
# ⚠️ 순서 중요: opencv-python 을 지우면 공유되는 cv2/ 파일까지 사라지므로 뒤에 --reinstall 필수.
# ⚠️ 버전을 uv.lock 에 맞춰 핀한다. `uv pip install` 은 lock 을 참조하지 않으므로 버전을 안 주면
#    최신(현재 5.0.x)을 가져온다. 이 파일은 설치 자체가 lock 기반이 아니지만(위 uv pip install .),
#    최소한 opencv 만이라도 결정적으로 고정해 빌드마다 달라지지 않게 한다.
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    OCV=$(grep -A1 '^name = "opencv-python-headless"' uv.lock | awk -F'"' '/^version/{print $2}'); \
    if [ -z "${OCV}" ]; then echo "[ERROR] uv.lock 에서 opencv-python-headless 버전을 못 찾음"; exit 1; fi; \
    echo "[INFO] opencv-python-headless==${OCV} (uv.lock 기준)"; \
    if uv pip list --python ${APP_VENV}/bin/python --format=freeze | grep -q '^opencv-python=='; then \
      uv pip uninstall --python ${APP_VENV}/bin/python opencv-python; \
    fi; \
    uv pip install --python ${APP_VENV}/bin/python --reinstall "opencv-python-headless==${OCV}"; \
    ${APP_VENV}/bin/python -c "import cv2; print('[INFO] cv2', cv2.__version__)"; \
    ${APP_VENV}/bin/python -c "import unstructured_inference; print('[INFO] unstructured_inference ok')"; \
    if ls -d ${APP_VENV}/lib/python3*/site-packages/opencv_python.libs >/dev/null 2>&1; then \
      echo "[ERROR] opencv_python.libs 잔존"; exit 1; \
    fi

############################
# 4-1) HWP SDK (HF private, 무료 자산) — Dockerfile.standard 와 동일
############################
FROM builder AS sdk_artifacts
RUN --mount=type=cache,target=${HF_HOME} \
    --mount=type=secret,id=HWP_SDK_TOKEN \
    echo "[INFO] Downloading HWP SDK from Private HF Repo..."; \
    HWP_SDK_TOKEN_VAL=$(cat /run/secrets/HWP_SDK_TOKEN) && \
    mkdir -p /app/hwp_sdk && \
    huggingface-cli download genon-search/hwp_sdk \
      --repo-type dataset --local-dir /app/hwp_sdk \
      --local-dir-use-symlinks False --token "${HWP_SDK_TOKEN_VAL}" && \
    chmod +x /app/hwp_sdk/convtext

############################
# 4-2) 추가 폰트 (HF public)
############################
FROM builder AS font_artifacts
RUN --mount=type=cache,target=${HF_HOME} \
    echo "[INFO] Downloading extra fonts from HF (public)..." && \
    mkdir -p /app/extra_fonts && \
    huggingface-cli download genon-search/additional_fonts \
      --repo-type dataset --local-dir /app/extra_fonts --local-dir-use-symlinks False

############################
# 4-2b) rhwp 바이너리 (rust 빌드 → 결과만 COPY)
############################
FROM rust:1-slim AS rhwp_builder
ARG RHWP_GIT_URL
ARG RHWP_GIT_REF
# rust:latest(약 1.5GB)는 buildpack-deps 기반이라 git 이 들어 있지만 rust:slim 에는 없다.
# 산출물이 바이너리 하나뿐이라 slim 으로 낮추고 git 만 따로 설치한다 — 최종 이미지 크기는 그대로이고
# 빌드 중 다운로드/디스크만 줄어든다.
# ⚠️ genos-rhwp 가 pkg-config / libssl-dev / cmake 같은 시스템 의존을 요구하면 cargo build 에서
#    깨진다. 그때는 아래 목록에 추가하거나 rust:latest 로 되돌린다.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    git clone --depth 1 --branch "${RHWP_GIT_REF}" "${RHWP_GIT_URL}" /src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    --mount=type=cache,target=/src/target \
    cargo build --release --bin rhwp \
 && cp /src/target/release/rhwp /usr/local/bin/rhwp

############################
# 4-3) 모델/NLTK/EasyOCR
#  ⚠️ docling-tools 는 docling 패키지 CLI 이므로 이 스테이지에서만 docling 임시 설치 후 모델 다운로드.
#     (이 stage 의 venv 는 runtime 에 COPY 되지 않음 — /models, /app/nltk_data 만 가져감)
############################
FROM builder AS models_artifacts
ARG APP_VENV
ARG NLTK_PACKAGES
ARG DOCLING_PKG_VER
# 받을 docling 모델 목록. 인자 없이 `docling-tools models download` 를 호출하면 기본 세트
# (layout, tableformer, code_formula, picture_classifier, easyocr)를 전부 받는데, 그중
# code_formula(ds4sd--CodeFormula, 506MB)는 이 레포에서 절대 로드되지 않는다:
#   do_code_enrichment / do_formula_enrichment 기본값 False(pipeline_options.py)이고
#   이를 켜는 config 키·코드가 없어 CodeFormulaModel 은 enabled=False 로만 생성된다.
#   ⚠️ 이 판단은 "현재 제품 기능 기준"이다. docling CLI 자체에는 code/formula enrichment 를 켜는
#      옵션이 있으므로, 향후 facade 가 이를 노출하거나(코드서빙이라면 런타임에 clone 되는 서비스
#      코드가 켜면) **오프라인 환경에서 모델이 없어 실패**한다. 그때는 DOCLING_MODELS 에
#      code_formula 를 다시 넣어야 한다.
# 나머지는 유지한다 (docling/utils/model_downloader.py 의 with_* 플래그와 1:1 대응):
#   layout            → ds4sd--docling-layout-old. 이름이 old 지만 layout_model_specs.py 의
#                       "Default Docling Layout Model"(DOCLING_LAYOUT_V2)이다. PDF 는
#                       genos_layout(dotsocr)을 쓰지만, PPT/PPTX 경량 파이프라인이 bare
#                       PdfPipelineOptions() 로 LayoutModel 을 만들어 실제 로드하므로 필수.
#   tableformer       → ds4sd--docling-models. layout_model_type: docling_layout 배포에서 필요.
#   picture_classifier→ ds4sd--DocumentFigureClassifier. chart.enable opt-in (16MB).
#   easyocr           → /models/EasyOcr 의 craft_mlt_25k(83MB)/english_g2/latin_g2.
#                       ⚠️ with_easyocr 도 기본 True 라, 목록에서 빼면 아래 korean_g2.zip
#                       단계만 남아 CRAFT 검출기가 사라진다(EasyOCR 사용 시 필수).
ARG DOCLING_MODELS="layout tableformer picture_classifier easyocr"
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python ${APP_VENV}/bin/python "docling==${DOCLING_PKG_VER}"
# NOTE: 예전에는 `--mount=type=cache,target=/root/.cache/models` 가 붙어 있었지만 아무도 그 경로를
#   쓰지 않았다. docling-tools 는 `-o /models`(이미지 레이어)와 HF_HOME 을 쓴다. 오해를 남기지
#   않도록 제거했다. (preprocessor 쪽도 동일하게 정리돼 있다.)
RUN if [ ! -d /models ] || [ -z "$(ls -A /models)" ]; then \
        docling-tools models download -o /models ${DOCLING_MODELS}; \
    fi
# mncai/doc_parser_models 에서 실제로 쓰는 것은 MiniLM 토크나이저 폴더 하나뿐이다.
# repo 전체(846MB)를 받으면 docling-models(506MB)/docling-layout-old(164MB)가 함께 오는데
# 둘 다 코드에서 로드되지 않는다(TableFormer 는 ds4sd--docling-models, layout 기본값은
# ds4sd--docling-layout-old 를 쓴다). 또 두 번째 download 의 --local-dir 이 이미 받은 폴더를
# 다시 가리켜 동명 하위 디렉토리가 중첩 생성됐다(88MB 중복) — 코드는 바깥쪽만 사용한다.
RUN --mount=type=cache,target=${HF_HOME} \
    mkdir -p /models/doc_parser_models && \
    huggingface-cli download mncai/doc_parser_models \
      --include "sentence-transformers-all-MiniLM-L6-v2/*" \
      --local-dir /models/doc_parser_models
RUN --mount=type=cache,target=/root/.cache/nltk \
    python - <<'PY'
import os, nltk
target = os.environ.get("NLTK_DATA", "/app/nltk_data")
os.makedirs(target, exist_ok=True)
packages = os.environ.get("NLTK_PACKAGES", "all")
if packages == "all":
    nltk.download("all", download_dir=target)
else:
    for pkg in [p.strip() for p in packages.split(",") if p.strip()]:
        nltk.download(pkg, download_dir=target)
PY
RUN --mount=type=cache,target=/root/.cache/easyocr_korean \
    mkdir -p /models/EasyOcr && \
    if [ ! -f /root/.cache/easyocr_korean/korean_g2.zip ]; then \
      curl -L -o /root/.cache/easyocr_korean/korean_g2.zip \
        "https://github.com/JaidedAI/EasyOCR/releases/download/v1.3/korean_g2.zip"; \
    fi; \
    unzip -q -o /root/.cache/easyocr_korean/korean_g2.zip -d /models/EasyOcr

############################
# 5) Runtime — genon/code-serving harness + 아티팩트 + ENV parity
############################
FROM loext AS runtime
ARG APP_VENV
ARG UID
ARG GID
ARG UNAME
ARG GNAME
ENV VIRTUAL_ENV=${APP_VENV} \
    PATH="${APP_VENV}/bin:${PATH}" \
    HOME=/app \
    XDG_CACHE_HOME=/app/.cache \
    XDG_CONFIG_HOME=/app/.config \
    XDG_DATA_HOME=/app/.local/share \
    XDG_RUNTIME_DIR=/tmp/xdg \
    TMPDIR=/app/tmp \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
    HF_HOME=/app/.cache/huggingface \
    NLTK_DATA=/app/nltk_data \
    LANG=ko_KR.UTF-8 \
    LC_ALL=ko_KR.UTF-8 \
    LC_CTYPE=ko_KR.UTF-8 \
    DOCLING_ARTIFACTS_PATH=/models \
    DOC_PARSER_BUILD_VARIANT=standard \
    RHWP_BIN=/usr/local/bin/rhwp \
    HWP_SDK_DIR=/app/hwp_sdk

WORKDIR /app

# 사용자 생성 + 디렉토리/권한
RUN groupadd -g $GID $GNAME && \
    useradd --create-home --no-log-init -u $UID -g $GID $UNAME && \
    mkdir -p /var/log/supervisor /var/run/supervisor /app/.cache/huggingface \
      /app/.config/libreoffice /app/.cache/libreoffice /app/.local/share /app/tmp /tmp/xdg

# venv (패치된 docling_parse 포함) + 아티팩트
# 🔴 --chown 으로 처음부터 올바른 소유자로 복사한다. 예전에는 아래에서
#    `chown -R $UID:$GID /app … /models /app/nltk_data` 를 한 번에 돌렸는데, 이미 복사된
#    .venv + /models + nltk_data + hwp_sdk 의 소유권만 바꾸는 것도 overlayfs 에서는
#    전 파일 copy-up 이라 같은 데이터를 담은 수 GB 레이어가 하나 더 생겼다.
COPY --chown=${UID}:${GID} --from=builder ${APP_VENV} ${APP_VENV}
COPY --chown=${UID}:${GID} --from=sdk_artifacts /app/hwp_sdk /app/hwp_sdk
COPY --from=rhwp_builder /usr/local/bin/rhwp /usr/local/bin/rhwp
COPY --from=font_artifacts /app/extra_fonts /usr/share/fonts/additional
RUN fc-cache -f -v
COPY --chown=${UID}:${GID} --from=models_artifacts /models /models
COPY --chown=${UID}:${GID} --from=models_artifacts /app/nltk_data /app/nltk_data

# hwp_sdk import 경로 (venv site-packages 심링크, 표준 빌드 파리티용). python3.12 고정(slim 3.12.x).
# ⚠️ docling 은 런타임에 clone 된 복사본 packages/ 의 wheel(genon-docling, import name: docling)을
#    requirements.txt 경로 참조로 pip install 해 /app/.venv 에 설치한다(복사본엔 docling 소스 없음, index 불필요).
#    코드서빙 런타임은 ENV HWP_SDK_DIR=/app/hwp_sdk 로 SDK 경로를 잡는다(genos_hwp_backend.py).
RUN ln -s /app/hwp_sdk ${APP_VENV}/lib/python3.12/site-packages/hwp_sdk

# 코드서빙 harness (genon/code-serving 와 동일 구성)
COPY --chown=${UID}:${GID} build-script/code-serving-doc-parser/supervisor/non-root-base.conf /etc/supervisor/supervisord.conf
COPY --chown=${UID}:${GID} build-script/code-serving-doc-parser/supervisor/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY --chown=${UID}:${GID} build-script/code-serving-doc-parser/scripts /app/scripts
COPY --chown=${UID}:${GID} build-script/code-serving-doc-parser/src /app/src
COPY --chown=${UID}:${GID} build-script/code-serving-doc-parser/gunicorn /app/gunicorn

# 샘플 service 제거(런타임에 /app/src/service 로 clone) + 스크립트 실행권한
# (root 가 3000 소유 파일을 chmod 하는 것은 문제없고 소유권도 유지된다)
RUN rm -rf /app/src/service && chmod +x /app/scripts/*.sh

# 위 COPY 들이 이미 ${UID}:${GID} 로 복사했으므로 여기서는 작은 경로만 정리한다.
#   - /app, /app/nltk_data : base 단계에서 root 로 미리 만들어진 디렉토리라
#     COPY --chown 이 디렉토리 자체의 소유권은 바꾸지 않는다 → 비재귀 chown 으로 보정.
#   - site-packages/hwp_sdk : 위 RUN 이 만든 심볼릭 링크 자체의 소유권(-h).
#   - /var/log/supervisor, /var/run/supervisor 는 /var/log, /run 에 포함된다.
RUN chown "${UID}:${GID}" /app /app/nltk_data && \
    chown -h "${UID}:${GID}" ${APP_VENV}/lib/python3.12/site-packages/hwp_sdk && \
    find /app -maxdepth 1 -user 0 -exec chown "${UID}:${GID}" {} + && \
    chown -R "${UID}:${GID}" /run /var/log /etc/supervisor /app/.cache /app/.config /app/.local /app/.fontconfig /app/tmp /tmp/xdg

USER $UID
CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
