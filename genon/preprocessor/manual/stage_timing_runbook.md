# 구간별 처리 시간 계측 런북

전처리가 느릴 때 시간이 어디에 쓰였는지를 구간(단계)별로 보는 절차다. 코드서빙의 두 실행 모드(HTTP, Temporal 워커)에서
같은 환경변수 하나로 켜고, 같은 도구로 읽는다.

- 무엇이 느린가: 단계마다 벽시계 시간과 CPU 시간
- 왜 느린가: CPU 를 쓰는 일인지, 외부 모델(레이아웃 VLM, 보강 LLM 등) 응답을 기다리는 일인지
- 외부 모델이 감당하는가: 모델(엔드포인트)마다 동시 호출 수에 따른 응답 시간

## 1. 켜고 끄기

| 실행 모드 | 켜는 법 | 끄는 법 |
|---|---|---|
| HTTP(루트 `main.py`) | 리비전 환경변수에 `GENOS_PROFILE=1` 을 넣고 배포한다 | 환경변수를 빼고 다시 배포한다 |
| Temporal 워커(`python -m genon.preprocessor.worker`) | 같다 | 같다 |

- 값은 `1` 일 때만 켜진다. 다른 값이나 미설정이면 계측 코드는 아무것도 하지 않는다.
- 비용: 외부 호출 한 건과 단계 하나마다 로그 한 줄이 늘어난다. 처리 시간 차이는 측정 편차 범위였다(문서 46건 순차 310초 대 328초).
- 기본 전처리기 서비스(`genon/preprocessor/src/main.py`)는 이 계측을 지원하지 않는다.

## 2. 켜졌는지 확인하기

기동 로그에 프로세스마다 한 줄이 찍힌다. HTTP 는 uvicorn 워커 수만큼, 워커 모드는 자식 프로세스 수만큼 나온다.

```text
[prof] enabled pid=<PID>
```

문서 한 건을 보낸 뒤 `[prof] stage` 줄과 `[prof] call` 줄이 찍히는지 본다.

```bash
kubectl -n <NAMESPACE> logs <POD> --since=10m | grep -c "\[prof\]"
```

## 3. 로그 형식

```text
[prof] stage name=<단계> doc=<문서> wall=<초> thread_cpu=<초> proc_cpu=<초>
[prof] call ep=<호스트+경로> status=<HTTP 코드> wall=<초> inflight=<같은 엔드포인트 동시 호출 수> total=<전체 동시 호출 수>
```

| 단계 이름 | 구간 |
|---|---|
| `parser_total` · `chunker_total` 등 `<엔드포인트>_total` | HTTP 요청 하나 전체 |
| `parse_total` · `chunk_total` | 워커 액티비티 하나 전체 |
| `to_pdf` | PPT 등을 PDF 로 바꾸는 LibreOffice 변환 |
| `convert` · `convert_ocr` | docling 변환(PDF 는 레이아웃 VLM 호출 포함), OCR 재변환 |
| `page_desc` | PPT 페이지 이미지 설명 |
| `doc_summary` · `image_desc` · `table_desc` · `metadata` · `custom_fields` | 보강 단계 |

읽을 때 주의할 점:

- `proc_cpu` 는 프로세스 전체 CPU 증분이다. 한 프로세스가 한 번에 한 문서만 처리할 때만 그 단계의 몫으로 읽는다. 여러 문서를 동시에 처리하는 구간에서는 `wall` 만 본다.
- `thread_cpu` 는 그 단계를 부른 스레드의 CPU 다. 단계 안에서 띄운 스레드(쪽 묶음, 보강 병렬 호출)의 CPU 는 빠진다.
- LibreOffice 는 자식 프로세스라 `to_pdf` 의 CPU 가 `proc_cpu` 에 잡히지 않는다. 파드 CPU 는 cgroup(`/sys/fs/cgroup/cpu.stat` 의 `usage_usec`)으로 본다.
- `inflight`·`total` 은 프로세스 안의 수다. HTTP 의 uvicorn 워커가 여럿이면 프로세스마다 따로 센다.

## 4. 표로 읽기

`genon/preprocessor/tools/code_serving/prof_report.py`(표준 라이브러리만 사용)가 로그를 표 세 개로 묶는다.
시각 범위는 로그의 `HH:MM:SS` 로 자른다. 순차 구간과 동시 구간은 나눠서 본다.

```bash
kubectl -n <NAMESPACE> logs <POD> --since=2h > worker.log
python3 genon/preprocessor/tools/code_serving/prof_report.py worker.log --from <시작> --to <끝>
```

| 표 | 보는 법 |
|---|---|
| 단계별(문서 × 단계) | 문서마다 어느 단계가 길었는가 |
| 단계 합계 | 단계마다 `wall` 합, `proc_cpu` 합, `wait`(= wall - proc_cpu). 순차 구간에서 CPU 일과 외부 대기의 비율 |
| 외부 호출 | 엔드포인트마다 건수·실패·p50·p95·최대, 그리고 동시 호출 수 구간(1 · 2~3 · 4~7 · 8~15 · 16 이상)별 p50 |

판단 기준:

- 단계 합계에서 `wait` 가 크면 그 단계는 외부 모델을 기다린다. 외부 호출 표에서 해당 엔드포인트를 본다.
- 외부 호출 표에서 동시 호출 수가 늘수록 p50 이 비례해 늘면, 그 모델 서빙의 처리 용량이 상한에 닿은 것이다. 문서를 더 겹쳐도 처리량이 늘지 않는다.
- 동시 호출 수가 늘어도 p50 이 그대로면 그 모델은 여유가 있다. 그때 처리량은 동시 처리 문서 수(HTTP 워커 수, 워커 모드의 프로세스 × 스레드)나 CPU 가 정한다.
- `wait` 가 작고 `proc_cpu` 가 크면 CPU 일이다. 파드 CPU 가 한도에 붙어 있는지 cgroup 으로 함께 본다.

## 5. 워커 모드: 액티비티마다 하위 단계 보기

워커 모드에서 계측을 켜면 parse·chunk 액티비티의 결과에 `stages` 가 실린다. 로그를 모으지 않아도 Temporal 화면의
워크플로 이력에서 액티비티 결과를 열면 그 문서의 하위 단계 시간을 볼 수 있다.

```json
{"stages": [{"stage": "convert", "wall": 30.7, "cpu": 21.4},
            {"stage": "page_desc", "wall": 29.1, "cpu": 0.8},
            {"stage": "parse_total", "wall": 84.3, "cpu": 24.0}],
 "stage": "parse", "activity": "parse", "ref": "…", "duration_ms": 84301}
```

측정 클라이언트(`tools/code_serving/worker_concurrency_test.py`)는 이 값을 문서마다 `results.json` 의 `parse_stages`·`chunk_stages` 로 남긴다.
측정 절차 전체는 `genon/docs/code_serving_verification/04_worker_mode.md` 에 있다.
