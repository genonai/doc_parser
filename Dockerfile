# 코드서빙 부팅 단계 도커 빌드용 Dockerfile.
#
# 이 파일은 배포본(doc_parser_code_serving) **루트**에 그대로 나간다 — main.py 와 같은 위치여야
# 코드서빙이 리비전 부팅 시 이 Dockerfile 로 이미지를 빌드한다. sync-serving-repo.sh 의 whitelist 에
# 들어 있으므로, 여기서 고친 내용은 커밋 후 배포본 재생성 시 반영된다.
#
# ⚠️ 이 저장소(개발 원본) 루트에서 `docker build .` 하지 말 것. 컨텍스트가 11GB(6만 파일)이고
#    COPY 가 그 전부를 이미지에 넣는다. 빌드는 배포본(또는 gitea 배포처) 루트에서만 한다.
#    (원래 이 자리에 있던 docling 업스트림 샘플 Dockerfile 은 공개 PyPI 의 `docling` 을 설치하는
#     CLI 데모로, 이 포크에서는 어떤 스크립트도 참조하지 않았다. 필요하면 git 이력에서 꺼낸다.)
#
# 구조와 사이트 값(레지스트리 경로·사내 PyPI 미러·NFS 루트)은 같은 사이트에서 동작 중인
# data-ingestion-pipeline 의 코드서빙 Dockerfile 에서 가져왔다. 배포 화면의 세 필드가
# 그대로 Dockerfile 로 옮겨온 형태다: 베이스 이미지 → FROM, 빌드 커맨드 → RUN pip install, 시작 커맨드 → CMD.
#
#   docker build -t doc-parser-serving:<태그> .      # 배포본 루트에서
#   docker run --rm -p 8080:8080 doc-parser-serving:<태그>
#
# `# syntax=docker/dockerfile:...` 지시자를 넣지 말 것. BuildKit 이 그 frontend 이미지를
# docker.io 에서 받아오려 하는데, 폐쇄망 사이트에서는 그 pull 이 실패해 부팅 빌드 전체가 죽는다.
# 여기서 쓰는 문법(전역 ARG, COPY --chown)은 모두 기본 frontend 로 동작한다.
#
# ── 이 파일이 하는 일과 하지 않는 일 ────────────────────────────────────────
# 하는 일: 사이트에 등록된 base 이미지 위에 서비스 코드와 docling wheel 만 얹는다.
#          빌드는 수십 초, 외부 인터넷 불필요.
# 하지 않는 일: 모델/HWP SDK/rhwp/LibreOffice 확장/폰트/NLTK/EasyOCR 준비.
#          이들은 base 이미지에 pre-bake 되어 있다 (사내 build-script/code-serving-doc-parser/Dockerfile).
#
# 부팅 단계에서 전체(fat) 빌드는 성립하지 않는다 — base 이미지를 반드시 재사용해야 한다:
#   1) HWP SDK 는 HF private repo 라 빌드 시 토큰(--secret)이 필요하다. 부팅 빌드에 주입 경로가 없다.
#   2) rhwp 는 github 에서 rust 소스를 clone 해 cargo build 한다. 폐쇄망에서 실패한다.
#   3) HCRBatang 폰트 tar(genon/preprocessor/resources) 와 의존성 정의(pyproject.toml/uv.lock) 는
#      배포본 whitelist 에서 제외되어 빌드 컨텍스트에 아예 없다.
#   4) 다 있다 해도 모델·torch 다운로드로 콜드 빌드가 수십 분이다. 리비전 부팅마다 감당할 수 없다.
#   → base 이미지 내용을 바꿔야 하면 사내 원본 저장소에서 빌드해 사이트 레지스트리에 push 하고
#     아래 BASE_IMAGE 태그를 올린다.

# ── 베이스 이미지 ───────────────────────────────────────────────────────────
# 사이트에 등록된 template-code-serving-doc-parser (사내 build-script/code-serving-doc-parser 산출물).
# 레지스트리 호스트·경로 접두어·태그는 코드서빙 배포 화면에 표시된 이미지 경로와 정확히 같아야 한다.
#   태그 확인:  curl http://<레지스트리>/v2/<경로>/template-code-serving-doc-parser/tags/list
#   GPU 사이트는 이미지명이 다르다 — template-code-serving-doc-parser-gpu (build.sh 가 -gpu 를 붙인다).
ARG BASE_IMAGE=scrpifpext-xichxjgz.scr.kr-west.scp-in.com/external/ifa/mnc/template-code-serving-doc-parser:2.1.4
FROM ${BASE_IMAGE}

# 저장소 루트가 곧 서비스 루트다. 코드서빙이 clone 하던 자리(/app/src/service)와 같게 두어야
# main.py 의 BASE_DIR 기준 경로(genon/preprocessor/resource, genon/preprocessor/src)가 성립한다.
WORKDIR /app/src/service

# 소유권은 숫자 UID 대신 base 이미지의 계정명으로 준다.
# 숫자는 이미지마다 다르지만(사내 build.config 는 3000, 다른 서비스 base 는 1000) 계정명은 genos 로 같다.
COPY --chown=genos:genos . /app/src/service/

# ── docling(fork) wheel 설치 ────────────────────────────────────────────────
# requirements.txt 는 packages/ 의 wheel 경로 한 줄이고, 나머지 deps 는 base 이미지 venv(/app/.venv)에
# 이미 들어 있다. 즉 이 단계는 로컬 wheel 만 설치하므로 실제로 네트워크를 쓰지 않는다.
# index 는 requirements.txt 에 패키지명이 추가될 때를 위한 사이트 사내 미러다(폐쇄망이라 공개 PyPI 불가).
#   --no-deps : wheel 의 deps 재해석을 막는다. 이게 없으면 resolver 가 torch 를 다시 잡아
#               base 이미지가 일부러 걷어낸 CUDA 휠(약 2.7GB)을 되살릴 수 있다.
# pip 은 PATH 상 /app/.venv/bin/pip 으로 해석되므로 base deps 와 같은 venv 에 설치된다.
ARG PIP_INDEX_URL=https://p-nexus.samsungcard.biz/repository/sc-px-pypi/simple
ARG PIP_TRUSTED_HOST=p-nexus.samsungcard.biz
RUN pip install --no-cache-dir --no-deps \
      --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
      --find-links=/app/src/service/packages \
      -r /app/src/service/requirements.txt \
 && python -c "import importlib.metadata as m, docling; print('[INFO] genon-docling', m.version('genon-docling'))"

# ── NFS (LLM 파일 캐시) ─────────────────────────────────────────────────────
# llm_cache 는 캐시를 <NFS_ROOT_DIR>/interim/<workflow_id>/<run_id>/llm_cache 에 쓴다
# (docling/utils/llm_cache.py). 코드 기본값도 /nfs-root 지만, 파이프라인 워커와 같은 NFS 를 봐야
# 캐시가 공유되므로 사이트 값을 명시해 둔다.
# 이 ENV 만으로는 부족하다 — 리비전에 같은 NFS 를 /nfs-root 로 마운트해야 한다. 마운트가 없으면
#    llm_cache 요청이 조용히 무효가 되고, /parser 에 넘기는 NFS 파일 경로도 열리지 않는다.
ENV NFS_ROOT_DIR=/nfs-root

# 안전망: 런타임이 command 를 base 의 supervisord 로 덮어쓰는 배포 형태 대비.
# 코드가 이미 이미지에 있으므로 base 의 scripts/init.sh(REPOSITORY_URL clone + pip install)를
# 그대로 두면 비어있지 않은 디렉토리에 clone 을 시도해 실패 경고만 남긴다.
# 이 no-op 로 바꿔두면 그 경로에서도 곧바로 main.py 자동 감지 → uvicorn 기동으로 이어진다.
RUN printf '%s\n' \
      '#!/bin/sh' \
      '# 부팅 빌드 방식 — 코드와 deps 가 이미 이미지에 포함되어 있어 clone/pip 이 필요없다.' \
      'echo "[init.sh] image-baked code — clone/pip skipped"' \
      > /app/scripts/init.sh

EXPOSE 8080

# 진입점은 배포본 루트의 main.py 하나다(FastAPI). 업무 API 7개와 /health 를 모두 제공한다.
# PORT 는 코드서빙이 8080 으로 주입한다. exec 로 감싸 uvicorn 이 PID 1 이 되게 한다(종료 시그널 전달).
# 워커는 1개다 — 모델 로딩·문서 변환이 동기 블로킹이라 늘리려면 메모리(워커당 수 GB)를 먼저 확인한다.
# 노드 코어가 많고 리비전 CPU 가 1코어인 인스턴스에서 OpenMP 스레드 경합이 보이면
#    ENV OMP_NUM_THREADS=4 를 추가한다. 현재 base 이미지는 이 값을 설정하지 않는다.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
