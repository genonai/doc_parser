# 전처리기 Temporal 워커 이미지 — 코드서빙 부팅 단계 도커 빌드용 Dockerfile.
#
# 코드서빙은 리비전 부팅 시 배포처(gitea) 루트의 이 Dockerfile 로 이미지를 빌드한다.
#
#   docker build -t doc-parser-worker:<태그> .        # 배포처 루트에서
#   docker run --rm -p 8080:8080 -v <NFS>:/nfs-root doc-parser-worker:<태그>
#
# 하는 일: 사이트에 등록된 base 이미지 위에 서비스 코드와 docling wheel 만 얹는다(수십 초, 외부 인터넷 불필요).
# 하지 않는 일: 모델·HWP SDK·rhwp·LibreOffice·폰트·NLTK·EasyOCR·temporalio 준비 — base 이미지에 포함되어 있다.
#   이들은 토큰·외부망·긴 빌드 시간이 필요해 부팅 빌드로 만들 수 없다. base 이미지 내용이 바뀌면 새 태그를
#   사이트 레지스트리에 올리고 BASE_IMAGE 태그를 바꾼다.
#
# `# syntax=docker/dockerfile:...` 지시자를 넣지 않는다. BuildKit 이 frontend 이미지를 docker.io 에서 받으려 하므로
# 폐쇄망에서 부팅 빌드가 실패한다. 여기서 쓰는 문법(전역 ARG, COPY --chown)은 기본 frontend 로 동작한다.
#
# 아래 ENV 는 이 사이트의 운영 값이다. 리비전 환경변수로 같은 이름을 주면 그 값이 우선한다.
# 사이트 환경이 바뀌면 확인·수정해야 하는 값에는 [사이트 설정] 표시를 붙였다.

# ── 베이스 이미지 ───────────────────────────────────────────────────────────
# [사이트 설정] 레지스트리 호스트·경로·태그는 코드서빙 배포 화면에 표시된 이미지 경로와 같아야 한다.
#   태그 확인: curl http://<레지스트리>/v2/<경로>/template-code-serving-doc-parser/tags/list
#   GPU 사이트는 이미지명이 template-code-serving-doc-parser-gpu 다.
ARG BASE_IMAGE=scrpifpext-xichxjgz.scr.kr-west.scp-in.com/external/ifa/mnc/template-code-serving-doc-parser:2.2.5.1
FROM ${BASE_IMAGE}

# 서비스 루트. 워커는 이 디렉터리에서 `python -m genon.preprocessor.worker` 로 기동해야 패키지 import 와
# activities/<slug>/ 탐색이 성립한다(base entrypoint 도 START_COMMAND 를 이 자리에서 실행한다).
WORKDIR /app/src/service

# 소유권은 숫자 UID 대신 base 이미지의 계정명(genos)으로 준다. UID 는 이미지마다 다르다.
COPY --chown=genos:genos . /app/src/service/

# ── docling(fork) wheel 설치 ────────────────────────────────────────────────
# requirements.txt 는 packages/ 의 wheel 경로 한 줄이고, 나머지 의존성은 base 이미지 venv(/app/.venv)에 있다.
# 로컬 wheel 만 설치하므로 실제로 네트워크를 쓰지 않는다.
#   --no-deps : 의존성 재해석을 막는다. 없으면 resolver 가 base 이미지에서 걷어낸 CUDA torch 를 다시 받을 수 있다.
# [사이트 설정] PIP_INDEX_URL·PIP_TRUSTED_HOST 는 requirements.txt 에 패키지명이 추가될 때만 쓰는 사이트 사내 미러다.
ARG PIP_INDEX_URL=https://p-nexus.samsungcard.biz/repository/sc-px-pypi/simple
ARG PIP_TRUSTED_HOST=p-nexus.samsungcard.biz
RUN pip install --no-cache-dir --no-deps \
      --index-url "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}" \
      --find-links=/app/src/service/packages \
      -r /app/src/service/requirements.txt \
 && python -c "import importlib.metadata as m, docling; print('[INFO] genon-docling', m.version('genon-docling'))"

# ── Temporal 워커 ───────────────────────────────────────────────────────────
# 워커는 전처리기 큐 하나만 long-poll 하고, task 의 액티비티 이름(slug = activities/<slug>/ 폴더명)으로 처리기를
# 찾아 실행한다. 워크플로·embed·load 는 파이프라인 큐를 받는 파이프라인 워커가 맡는다. 워크플로가 늘어도 큐는 둘 그대로다.

# ── 1. 필수 항목 — 기본값이 없고, 없으면 워커가 기동을 거부한다 ──────────────────────
# ("워커 필수 env 가 없습니다: <이름>" 로그를 남기고 종료한다)
# [사이트 설정] TEMPORAL_HOST — 파이프라인과 같은 Temporal frontend 주소
# [사이트 설정] WORKER_TASK_QUEUE — 파이프라인 DOC_PARSER_TASK_QUEUE 와 같은 이름(파이프라인 자기 큐와는 다른 이름).
#   파이프라인 DOC_PARSER_CHUNK_QUEUE_SPLIT 은 꺼 둔다. 켜면 청킹이 <큐>-chunk 로 가는데 이 워커는 큐 하나만 받는다.
# [사이트 설정] NFS_ROOT·ARTIFACT_ROOT — 리비전에 파이프라인과 같은 NFS 를 /nfs-root 로 마운트해야 한다.
#   NFS_ROOT       원본 문서(read_ref root="nfs")를 여는 루트. 파이프라인 NFS_ROOT 와 같은 경로
#   ARTIFACT_ROOT  parse·chunk 결과 JSON 을 쓰고 읽는 루트. 파이프라인 INTERIM_ROOT(기본 <NFS_ROOT>/temporal-artifacts)와 같은 자리
ENV TEMPORAL_HOST=llmops-temporal-frontend.ns-ifa-app.svc.cluster.local \
    WORKER_TASK_QUEUE=preproc-q \
    NFS_ROOT=/nfs-root \
    ARTIFACT_ROOT=/nfs-root/temporal-artifacts

# ── 2. 이 사이트에서 지정하는 항목 — 기본값이 없거나, 기본값과 다른 값을 쓴다 ───────────────
#   START_COMMAND     (기본값 없음) base entrypoint.sh 는 이 값이 있으면 그것을 실행하고, 없으면 main.py 를 찾아
#                     HTTP 서버를 띄운다. 런타임이 CMD 를 supervisord → entrypoint.sh 로 덮어쓰는 배포 형태에서도 워커가 뜨게 한다
#   WORKER_PROCESSES  (기본값 1) 워커 프로세스 수(N). Temporal 표준 옵션이 아니라 이 워커의 옵션이다 — 부모 프로세스가
#                     자식을 N 개 띄우고, 자식마다 Temporal 워커 1개가 같은 큐를 long-poll 한다. 프로세스끼리는 실제로 병렬 처리된다.
#                     pod 의 동시 처리 문서 수 = N × WORKER_THREADS(기본 1)
#                     [사이트 설정] 프로세스마다 모델이 메모리(수 GB)를 따로 쓰므로 늘리기 전에 리비전 메모리를 확인한다
#   TZ                (기본값 UTC — base 이미지) 로그 시각을 파이프라인 로그(KST)와 맞춘다
ENV START_COMMAND="python -m genon.preprocessor.worker" \
    WORKER_PROCESSES=5 \
    TZ=Asia/Seoul

# 기본값이 없지만 이 사이트에서는 쓰지 않는 항목(필요할 때 주석을 푼다)
#   LLM_CACHE_ROOT            재시도 때 이미 성공한 LLM 호출을 건너뛰는 캐시 루트(같은 NFS 아래). 없으면 캐시를 쓰지 않는다
#   GENOS_MODEL_PRESETS_FILE  모델 접속 실값 파일. 없으면 배포처의 genon/preprocessor/resource/model_presets.yaml 만 읽는다.
#                             그 파일에 실값(서빙 ID·OCR·레이아웃 주소 등)을 직접 채웠으면 필요 없다.
#                             주면 그 파일이 resource/model_presets.yaml 위에 프리셋 이름별·키 단위로 덮인다(형식은 같다).
#                             파일이 없거나 형식이 틀리면 기동이 실패한다
# ENV LLM_CACHE_ROOT=/nfs-root/interim
# ENV GENOS_MODEL_PRESETS_FILE=<model_presets.yaml 경로>

# ── 3. 기본값이 있는 항목 — 지정하지 않는다(아래 값이 기본값) ─────────────────────────
# 바꿀 때만 해당 줄의 주석을 풀거나 리비전 환경변수로 준다.
#   TEMPORAL_PORT                   Temporal frontend 포트
#   TEMPORAL_NAMESPACE              [사이트 설정] 파이프라인 TEMPORAL_NAMESPACE 와 같아야 한다
#   WORKER_THREADS                  프로세스마다 동시에 처리할 문서 수(M). 각 프로세스의 Temporal 워커가 M 건까지 받는다
#                                   (스레드 풀 크기 = max_concurrent_activities = M).
#                                   같은 프로세스의 스레드는 모델·처리기를 함께 쓰므로 메모리는 거의 늘지 않지만,
#                                   파이썬 GIL 때문에 파싱처럼 CPU 가 무거운 처리는 동시에 빨라지지 않는다.
#                                   LLM 호출 대기가 긴 문서에서만 효과가 있고, LibreOffice 변환은 프로세스 안에서 한 번에 하나씩 돈다.
#                                   한 건이 멈춰 프로세스를 다시 띄워야 하면 같은 프로세스의 다른 건이 끝난 뒤 재시작한다.
#                                   처리량은 WORKER_PROCESSES 로 늘리고, 메모리가 부족할 때만 이 값을 2 정도로 올려 본다
#   HEARTBEAT_INTERVAL              액티비티 하트비트 간격(초)
#   PREVIEW_CHARS                   액티비티 반환 preview 길이(Temporal 페이로드 2MB 한계 안)
#   PORT                            /health 포트(코드서빙도 8080 을 주입한다)
#   OMP_NUM_THREADS                 연산 스레드 수. 기본은 기동 때 "CPU 한도 ÷ WORKER_PROCESSES" 의 내림(최소 1)으로 자동 설정
#   GENOS_PROFILE                   1 이면 단계별 처리 시간·외부 호출 계측 로그를 남긴다(그 밖의 값은 꺼짐)
#   GENOS_CUSTOM_FIELDS_VALIDATION  custom_fields 설정 오기입 처리. error = 기동 실패, warn = 경고 후 그 설정 무시
# ENV TEMPORAL_PORT=7233
# ENV TEMPORAL_NAMESPACE=ns-ifa-app
# ENV WORKER_THREADS=1
# ENV HEARTBEAT_INTERVAL=10
# ENV PREVIEW_CHARS=2000
# ENV PORT=8080
# ENV OMP_NUM_THREADS=<자동>
# ENV GENOS_PROFILE=0
# ENV GENOS_CUSTOM_FIELDS_VALIDATION=error

# 이 이미지에서 쓰지 않는 값
#   GENOS_RESOURCE_DIR  주지 않는다. 사이트 배포본은 사이트 설정이 activities/<slug>/ 에 이미 들어 있다.
#                       주면 그 폴더 옆 activities/ 만 읽으므로 설정을 찾지 못한다
#   NFS_ROOT_DIR        HTTP 서빙의 LLM 캐시 경로다. 워커는 LLM_CACHE_ROOT 를 쓴다

# ── 기동 ─────────────────────────────────────────────────────────────────────
# base 의 init.sh 는 저장소를 clone 하고 pip install 한다. 코드가 이미 이미지에 있으므로 no-op 으로 바꾼다
# (그대로 두면 비어 있지 않은 디렉터리에 clone 을 시도해 실패 경고만 남긴다).
RUN printf '%s\n' \
      '#!/bin/sh' \
      '# 부팅 빌드 방식 — 코드와 deps 가 이미 이미지에 포함되어 있어 clone/pip 이 필요없다.' \
      'echo "[init.sh] image-baked code — clone/pip skipped"' \
      > /app/scripts/init.sh

# /health(PORT) — 코드서빙 startupProbe. 워커 프로세스가 모두 살아 있으면 200, 아니면 503.
EXPOSE 8080

# exec 로 감싸 워커가 PID 1 이 되게 한다(종료 시그널 전달).
CMD ["sh", "-c", "exec python -m genon.preprocessor.worker"]
