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
| `TEMPORAL_HOST`·`TEMPORAL_PORT`·`TEMPORAL_NAMESPACE` | 파이프라인 워커와 같은 값 | 코드 기본값(`ns-ifa-app`)이 사이트 값과 다를 수 있다. 실행 중인 파이프라인 워커 파드에서 `printenv TEMPORAL_NAMESPACE` 로 확인한다 |
| `WORKER_TASK_QUEUE` | `<MEASURE_QUEUE>` | **측정 전용 이름.** 파이프라인이 쓰는 큐 이름을 주면 운영 작업이 이 리비전으로 온다 |
| `WORKER_PROCESSES` | `1` | 1 코어 파드 |
| `WORKER_THREADS` | `5` | HTTP 의 워커 5개와 동시 처리 수를 맞춘다 |
| `NFS_ROOT` | 문서가 있는 NFS 루트 | 액티비티는 이 루트 기준 상대 경로로 파일을 연다 |
| `ARTIFACT_ROOT` | `<NFS 의 측정 전용 폴더>` | parse·chunk 결과 JSON 을 여기에 쓴다. 파이프라인 산출물 폴더와 겹치지 않게 한다 |
| `GENOS_RESOURCE_DIR` | `<SITE_DIR>/resource` | 아래 "사이트 설정 폴더 구성" 참고 |
| `OMP_NUM_THREADS` | 넣지 않는다 | 워커가 "파드 CPU ÷ 프로세스 수" 의 내림으로 정한다(1 코어면 1) |
| `GENOS_PROFILE` | `1`(병목 분해 측정 때만) | 단계별 시간·CPU 와 모든 외부 HTTP 호출의 지연·동시 호출 수를 `[prof]` 로그로 남긴다. 아래 "병목 분해" 참고 |

### 사이트 설정 폴더 구성

워커는 액티비티(slug)마다 설정 폴더 하나를 쓴다(#517). 사이트 배포에서는 `GENOS_RESOURCE_DIR` 이 사이트의 `resource/` 를
가리키고, 그 **옆의** `activities/<slug>/` 가 그 slug 의 설정 폴더가 된다. 사이트에 `activities/` 가 없으면 처리가 실패한다(#523).

```text
<SITE_DIR>/
  resource/              model_presets.yaml 등. GENOS_RESOURCE_DIR 은 여기를 가리킨다
  activities/
    parse/config.yaml    + 설정이 파일명으로 부르는 custom_field_*.yaml·prompt_*.md
    chunk/config.yaml
```

develop 기준 리소스 폴더(`parser_processor_config.yaml`·`chunking_processor_config.yaml`)로 HTTP 측정과 같은 설정을 만들려면
아래처럼 옮긴다. `model_presets_file:` 줄은 뺀다 — 빼면 `resource/model_presets.yaml` 을 읽고, 두면 액티비티 폴더 기준으로 찾는다.

```bash
SRC=<NFS_RESOURCE_DIR>; D=<SITE_DIR>
mkdir -p $D/resource $D/activities/parse $D/activities/chunk && cp -a $SRC/. $D/resource/
grep -v "^model_presets_file:" $SRC/parser_processor_config.yaml > $D/activities/parse/config.yaml
cp $SRC/custom_field_*.yaml $SRC/prompt_*.md $D/activities/parse/
grep -v "^model_presets_file:" $SRC/chunking_processor_config.yaml > $D/activities/chunk/config.yaml
```

기동 로그에 slug 마다 설정 경로와 출처(사이트 완성본 | 저장소 표준)가 한 줄씩 찍힌다(#520). 두 slug 모두 `<SITE_DIR>/activities/…` 여야 한다.

기동 로그에서 아래 한 줄을 확인한다. `OMP_NUM_THREADS=1` 이고 `액티비티=chunk, parse` 여야 한다.

```text
[worker] INFO task_queue=<MEASURE_QUEUE> processes=1 threads=5 OMP_NUM_THREADS=1 ... 액티비티=chunk, parse
```

## 2. 클라이언트 실행

클라이언트는 `temporalio` 가 필요하다. 배포본의 `requirements-dev.txt` 에 들어 있다. 실행 위치는 둘 중 하나다.

| 위치 | 조건 | 비고 |
|---|---|---|
| 코드스페이스 | `temporalio` 를 설치할 수 있다(PyPI 미러에 접근 가능) | 측정 대상 파드의 CPU 를 쓰지 않는다 |
| 워커 파드 안 | 항상 가능. 베이스 이미지에 `temporalio` 가 있다 | 클라이언트가 같은 1 코어를 나눠 쓴다. 클라이언트는 워크플로를 걸고 기다리기만 해 영향이 작지만, 결과에 적어 둔다 |

코드스페이스에서는 배포본 클론 루트에서 가상환경을 만든다. 폐쇄망이라 미러에 접근할 수 없으면 워커 파드 안에서 실행한다.

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

`--docdir` 는 리비전의 `NFS_ROOT` 기준 **상대 경로**다. 묶음 3 의 `<DOC_DIR>` 가 `<NFS_ROOT>/a/b` 였다면 `a/b` 다.
`--artifact-root` 에 실행 위치에서 보이는 `ARTIFACT_ROOT` 를 주면 간섭 검사도 한다(주지 않으면 시간만 잰다).

코드스페이스에서:

```bash
cd genon/preprocessor/tools/code_serving
export TEMPORAL_HOST=<TEMPORAL_HOST> TEMPORAL_PORT=<TEMPORAL_PORT> TEMPORAL_NAMESPACE=<TEMPORAL_NAMESPACE>
python3 worker_concurrency_test.py --activity-queue <MEASURE_QUEUE> --docdir <DOC_DIR_REL> --out ./worker_t5 --phases solo,concurrent --workers 15 --manifest monimo_manifest.json --param log_level=4 --artifact-root <ARTIFACT_ROOT_MOUNT> 2>&1 | tee ./worker_t5.log
```

워커 파드 안에서(배스천에서 실행한다. `TEMPORAL_*`·`ARTIFACT_ROOT` 는 리비전 환경변수로 이미 들어 있다):

```bash
kubectl -n <NAMESPACE> exec <POD> -- sh -c 'mkdir -p <ARTIFACT_ROOT> && cd /app/src/service/genon/preprocessor/tools/code_serving && nohup python worker_concurrency_test.py --activity-queue <MEASURE_QUEUE> --docdir <DOC_DIR_REL> --out <ARTIFACT_ROOT>/client --phases solo,concurrent --workers 15 --manifest monimo_manifest.json --param log_level=4 --artifact-root <ARTIFACT_ROOT> > <ARTIFACT_ROOT>/client.log 2>&1 &'
```

단계마다 묶음 3 과 같은 표, 단계별 대기·처리 표, 요약 한 줄(wall, 동시 처리 최대, 대기 최대)이 나온다.
결과는 `--out` 의 `results.json` 에 남는다. 산출물은 `ARTIFACT_ROOT/measure/<run>/` 에 남으므로 끝나면 지운다.

## 3. 병목 분해(`GENOS_PROFILE=1`)

리비전에 `GENOS_PROFILE=1` 을 넣고 같은 클라이언트를 돌린 뒤, 워커 로그를 받아 표로 묶는다. 순차 구간과 동시 구간은 클라이언트
로그의 단계 시작 시각으로 나눈다.

```bash
kubectl -n <NAMESPACE> logs <POD> --since=2h > worker.log
python3 genon/preprocessor/tools/code_serving/prof_report.py worker.log --from <순차 시작> --to <동시 시작>
python3 genon/preprocessor/tools/code_serving/prof_report.py worker.log --from <동시 시작> --to <끝>
```

| 표 | 보는 법 |
|---|---|
| 단계별·단계 합계 | 순차 구간에서 단계마다 `proc_cpu`(CPU 일)와 `wait`(= wall - proc_cpu, 외부 응답 대기)의 비율. `convert` 의 wait 는 레이아웃 VLM, 보강 단계의 wait 는 LLM 이다 |
| 외부 호출 | 엔드포인트마다 지연 p50·p95 와, 같은 엔드포인트 동시 호출 수 구간별 p50. 동시 호출이 늘 때 p50 이 따라 늘면 그 서빙의 용량이 병목이다 |

`proc_cpu` 는 프로세스 전체 CPU 증분이라 동시 구간에서는 문서 몫으로 읽지 않는다.

## 4. 판정

| 질문 | 판정 |
|---|---|
| 실제로 동시에 처리했나 | concurrent 단계의 동시 처리 최대가 parse·chunk 모두 `WORKER_THREADS` 에 닿는다 |
| HTTP 보다 빠른가 | concurrent wall 을 묶음 3 의 "수정본 + `OMP_NUM_THREADS=1`"(동시 15: 191초)과 비교한다 |
| chunk 대기는 줄었나 | `chunk_wait` 최대를 HTTP 의 chunk 최대 대기(96초)와 비교한다. parse·chunk 가 한 큐를 쓰는 동안은 크게 줄지 않는 것이 정상이다. 줄이려면 단계별 큐를 나눈다(#514 다음 단계) |
| 간섭이 없나 | 간섭 징후 0건 |

1 코어에서 CPU 를 쓰는 일의 총량은 모드와 무관하다. 워커 모드의 이득은 VLM·LLM 응답을 기다리는 동안 다른 문서를
처리하는 데서 나오고, 모델을 프로세스 하나에만 올리므로 메모리가 HTTP 의 워커 5개보다 적다. 메모리는 측정 중
`kubectl top pod` 로 함께 본다.

## 막혔을 때

| 증상 | 원인과 조치 |
|---|---|
| 기동 로그에 `Namespace … is not found` 가 1초마다 반복 | `TEMPORAL_NAMESPACE` 가 사이트 값과 다르다. 파이프라인 워커 파드의 값으로 바꾼다 |
| 요청이 레이아웃 모델 500(주소에 `<…_SERVING_ID>` 자리표시자), doc_type 미등록으로 실패 | 워커가 표준 설정으로 돌고 있다(#517 전 빌드). 사이트 폴더를 위 구성으로 맞춘다 |
| 처리가 "사이트 activities/ 가 없다" 로 바로 실패 | `GENOS_RESOURCE_DIR` 옆에 `activities/` 가 없다(#523). 위 구성으로 만든다 |
| 코드스페이스에서 `pip install` 이 `Name or service not known` | PyPI 미러에 접근할 수 없다. 워커 파드 안에서 실행한다 |

## 이 검증의 결과

조건: 묶음 3 과 같은 코드서빙·베이스 이미지 2.2.5·CPU 1·메모리 16Gi·리소스 폴더(`ocr_mode: disable`)·46건·동시 15, `OMP_NUM_THREADS=1`.
워커는 `feature/514-preproc-mode-worker-threads`(c47b44dd), 프로세스 1 × 스레드 5. 클라이언트는 워커 파드 안에서 실행했다.

| 모드 | 순차 | 동시 15 | 순차/동시 | 동시 처리 최대 | chunk 최대 대기 | 간섭 징후 | 실패 |
|---|---|---|---|---|---|---|---|
| HTTP(uvicorn 워커 5, 묶음 3 의 "수정본 + `OMP_NUM_THREADS=1`") | 355초 | 191초 | 1.86배 | — | 96초 | 0 | stock_insight 2(설정 불일치) |
| 워커(1 × 5) | 310초 | 159초 | 1.95배 | parse 5 · chunk 2 | 61초 | 0 | 같은 2 |

- 동시 처리: parse 가 스레드 수(5)만큼 실제로 겹쳐 돌았다. 결과는 문서마다 자기 원본에서 나왔다(원본 파일명·그림 설명 수 일치).
- 속도: 동시 15 가 17% 빨랐으나 순차도 13% 빨라 대부분은 외부 모델 응답 편차다. 1 코어에서 CPU 작업 총량은 모드와 무관하다는 예상과 맞는다.
- chunk 대기: 줄었지만 남는다. 스레드가 모두 parse 를 처리하는 동안 chunk 가 기다린다. 없애려면 단계별 큐를 나눈다(#514 다음 단계).
- 메모리: 파드 전체 최대 약 5.0GiB(모델을 프로세스 하나에만 올린다). HTTP 는 워커마다 1.0–2.3GB 였다.
- 청크 수가 순차와 다른 3건(`table__on`, `pptx_sample__on`·`__off`)은 간섭이 아니다. `table__on` 은 VLM 레이아웃 응답이 달라 표·그림 수가 바뀌었고,
  pptx 는 쪽마다 붙는 페이지 이미지 설명(LLM 생성) 길이가 달랐다. 이 설명 일부가 여러 언어가 섞인 깨진 문장으로 나오는데, 순차에서도 같은 빈도(63건 중 5건)로
  나와 동시성과 무관한 모델 출력 문제다.
