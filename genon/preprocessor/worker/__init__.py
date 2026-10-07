"""전처리기 Temporal 워커 — `activities/` 의 파싱·청킹 파사드를 Temporal 액티비티로 실행한다.

같은 배포본(doc_parser_code_serving)으로 코드서빙을 만들고 시작 커맨드만 이것으로 준다.

    python -m genon.preprocessor.worker

    registry.py    activities/ 에서 parse·chunk·parse_…·chunk_… 파일을 slug(=파일명) 이름으로 등록
    runtime.py     액티비티 본문(ParserCore·ChunkerCore.run_activity 가 부른다) — 결과 JSON 을
                   ARTIFACT_ROOT 아래 ref 위치에 직접 쓴다
    paths.py       파드 사이에는 상대 참조만 오간다. 루트는 이 파드의 env 로 해석한다
    errors.py      예외 → 재시도 성격 판정
    runner.py      자식 프로세스 본체 — Temporal 워커 1개, 동시 1건
    __main__.py    프로세스 N개 기동·재기동 + /health(코드서빙 startupProbe)

동시 실행은 **프로세스 N개 × 프로세스당 1건**이다. 프로세서 객체가 요청마다 자기 설정을
덮어쓰고(processing/common/runtime_kwargs.py) 파싱 본체가 동기라, 한 프로세스 안에서 여러 건을
돌리면 문서끼리 옵션이 섞인다. HTTP 쪽이 uvicorn 워커(프로세스)를 늘려 동시성을 내는 것과 같다.
"""
