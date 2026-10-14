# 묶음 4 — 워커 모드 측정(HTTP 대비)

묶음 3 은 HTTP 모드(uvicorn 워커 5개)에 요청을 직접 보냈다. 이 묶음은 같은 배포본을 **워커 모드**(Temporal 워커,
프로세스 1개 × 스레드 M)로 띄우고, 같은 문서·같은 인자를 parse → chunk 액티비티로 보내 HTTP 측정과 비교한다(#514).

## 구성

| 구성 | 위치 | 하는 일 |
|---|---|---|
| Temporal 서버 | 사이트에 이미 있는 것(파이프라인이 쓰는 것) | 큐를 중계한다 |
| 측정 클라이언트와 측정 전용 워크플로 | 코드스페이스(`worker_concurrency_test.py`) | 문서마다 워크플로 하나를 시작한다. 워크플로는 액티비티를 걸고 기다리기만 한다 |
| parse·chunk 처리 | 코드서빙 리비전(워커 모드) | 자기 큐에서 액티비티를 받아 처리한다 |

파이프라인 배치를 쓰지 않는 이유: HTTP 측정과 같은 조건(문서마다 parse 한 번, chunk 한 번)을 맞추기 위해서다.

## 무엇을 보나

| 지표 | 뜻 | HTTP 측정의 무엇과 비교하나 |
|---|---|---|
| wall | 단계 전체의 벽시계 | 순차·동시 벽시계 |
| `*_wait` | 예약 → 시작. 큐·스레드가 비기를 기다린 시간 | chunk 대기(긴 parse 뒤 chunk) |
| `*_run` | 시작 → 완료. 파드 안에서 처리한 시간 | — |
| 동시 처리 최대 | 단계별로 파드 안에서 동시에 처리 중이던 최대 건수 | `WORKER_THREADS` 까지 올라가야 실제로 동시에 처리한 것이다 |
| 간섭 징후 | R1·R2·청크 수·필드 집합(묶음 3 과 같은 검사) | 같음. `--artifact-root` 를 줄 때만 한다 |

시각은 모두 워크플로 이력에 Temporal 서버가 기록한 이벤트 시각이다. slug 마다 첫 호출의 `run` 에는 프로세서 생성
시간이 들어간다(순차 단계의 첫 건).

## 0. 배포본 빌드

묶음 1 과 같다. 원본 브랜치만 `feature/514-preproc-mode-worker-threads` 로, 배포본 테스트 브랜치를 `test/514-worker` 로 한다.

## 1. 리비전 생성(워커 모드)

묶음 2 와 같은 코드서빙, 같은 베이스 이미지, 같은 자원(CPU 1, 메모리 16Gi)으로 리비전을 만든다. 브랜치·커밋은 0 에서
push 한 것이다. 환경변수만 다르다.

| 환경변수 | 값 | 비고 |
|---|---|---|
| `START_COMMAND` | `python -m genon.preprocessor.worker` | 베이스 이미지 2.2.5 의 시작 스크립트에는 `PREPROC_MODE` 가 없다. 다음 베이스 이미지부터는 `PREPROC_MODE=worker` 로 대신한다 |
| `TEMPORAL_HOST`·`TEMPORAL_PORT`·`TEMPORAL_NAMESPACE` | 파이프라인 워커와 같은 값 | |
| `WORKER_TASK_QUEUE` | `<MEASURE_QUEUE>` | **측정 전용 이름.** 파이프라인이 쓰는 큐 이름을 주면 운영 작업이 이 리비전으로 온다 |
| `WORKER_PROCESSES` | `1` | 1 코어 파드 |
| `WORKER_THREADS` | `5` | HTTP 의 워커 5개와 동시 처리 수를 맞춘다 |
| `NFS_ROOT` | 문서가 있는 NFS 루트 | 액티비티는 이 루트 기준 상대 경로로 파일을 연다 |
| `ARTIFACT_ROOT` | `<NFS 의 측정 전용 폴더>` | parse·chunk 결과 JSON 을 여기에 쓴다. 파이프라인 산출물 폴더와 겹치지 않게 한다 |
| `GENOS_RESOURCE_DIR` | 묶음 2 와 같은 리소스 폴더 | |
| `OMP_NUM_THREADS` | 넣지 않는다 | 워커가 "파드 CPU ÷ 프로세스 수" 의 내림으로 정한다(1 코어면 1) |

기동 로그에서 아래 한 줄을 확인한다. `OMP_NUM_THREADS=1` 이고 `액티비티=chunk, parse` 여야 한다.

```text
[worker] INFO task_queue=<MEASURE_QUEUE> processes=1 threads=5 OMP_NUM_THREADS=1 ... 액티비티=chunk, parse
```

## 2. 클라이언트 실행(코드스페이스)

클라이언트는 `temporalio` 가 필요하다. 코드스페이스에 없으면 베이스 이미지와 같은 버전을 설치한다.

```bash
pip install temporalio==1.34.0
```

`--docdir` 는 리비전의 `NFS_ROOT` 기준 **상대 경로**다. 묶음 3 의 `<DOC_DIR>` 가 `<NFS_ROOT>/a/b` 였다면 `a/b` 다.
코드스페이스에 같은 NFS 가 마운트되어 있으면 `--artifact-root` 에 그 마운트 경로의 `ARTIFACT_ROOT` 를 주어 간섭 검사도 한다.

```bash
cd genon/preprocessor/tools/code_serving
export TEMPORAL_HOST=<TEMPORAL_HOST> TEMPORAL_PORT=<TEMPORAL_PORT> TEMPORAL_NAMESPACE=<TEMPORAL_NAMESPACE>
python3 worker_concurrency_test.py --activity-queue <MEASURE_QUEUE> --docdir <DOC_DIR_REL> --out ./worker_t5 --phases solo,concurrent --workers 15 --manifest monimo_manifest.json --param log_level=4 --artifact-root <ARTIFACT_ROOT_MOUNT> 2>&1 | tee ./worker_t5.log
```

단계마다 묶음 3 과 같은 표, 단계별 대기·처리 표, 요약 한 줄(wall, 동시 처리 최대, 대기 최대)이 나온다.
결과는 `./worker_t5/results.json` 에 남는다. 산출물은 `ARTIFACT_ROOT/measure/<run>/` 에 남으므로 끝나면 지운다.

## 3. 판정

| 질문 | 판정 |
|---|---|
| 실제로 동시에 처리했나 | concurrent 단계의 동시 처리 최대가 parse·chunk 모두 `WORKER_THREADS` 에 닿는다 |
| HTTP 보다 빠른가 | concurrent wall 을 묶음 3 의 "수정본 + `OMP_NUM_THREADS=1`"(동시 15: 191초)과 비교한다 |
| chunk 대기는 줄었나 | `chunk_wait` 최대를 HTTP 의 chunk 최대 대기(96초)와 비교한다. parse·chunk 가 한 큐를 쓰는 동안은 크게 줄지 않는 것이 정상이다. 줄이려면 단계별 큐를 나눈다(#514 다음 단계) |
| 간섭이 없나 | 간섭 징후 0건 |

1 코어에서 CPU 를 쓰는 일의 총량은 모드와 무관하다. 워커 모드의 이득은 VLM·LLM 응답을 기다리는 동안 다른 문서를
처리하는 데서 나오고, 모델을 프로세스 하나에만 올리므로 메모리가 HTTP 의 워커 5개보다 적다. 메모리는 측정 중
`kubectl top pod` 로 함께 본다.
