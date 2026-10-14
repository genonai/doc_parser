"""전처리기 Temporal 워커 — `activities/` 의 파싱·청킹 파사드를 Temporal 액티비티로 실행한다.

같은 배포본(doc_parser_code_serving)으로 코드서빙을 만들고 시작 커맨드만 이것으로 준다.

    python -m genon.preprocessor.worker

    registry.py    activities/ 에서 parse·chunk·parse_…·chunk_… 파일을 slug(=파일명) 이름으로 등록
    runtime.py     액티비티 본문(ParserCore·ChunkerCore.run_activity 가 부른다) — 결과 JSON 을
                   ARTIFACT_ROOT 아래 ref 위치에 직접 쓴다
    paths.py       파드 사이에는 상대 참조만 오간다. 루트는 이 파드의 env 로 해석한다
    errors.py      예외 → 재시도 성격 판정
    runner.py      자식 프로세스 본체 — Temporal 워커 1개, 동시 WORKER_THREADS 건
    __main__.py    프로세스 N개 기동·재기동 + /health(코드서빙 startupProbe)

동시 실행은 **프로세스 N개(`WORKER_PROCESSES`) × 프로세스당 M건(`WORKER_THREADS`)**, 기본 1 × 1 이다.
프로세서 객체는 slug 마다 프로세스에 하나이고 M개 스레드가 함께 쓴다 — 요청 상태는 인스턴스가 아니라
job 에 산다(#481). 실제 워커 경로의 동시 실행은 tests/smoke/test_worker_concurrency_smoke.py 가 본다.
"""
