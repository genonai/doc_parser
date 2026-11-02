"""코드서빙에 시작 명령이 없을 때 뜨는 자리 — 기동 오류만 낸다(#577).

doc_parser 코드서빙은 전처리기 Temporal 워커 하나이고, 리비전 시작 명령(START_COMMAND)이 필수다:

    python -m genon.preprocessor.worker

시작 명령이 비어 있으면 base 이미지 기동 스크립트가 이 파일을 `uvicorn main:app` 으로 띄운다. 그때 엉뚱한
것이 뜨지 않게 여기서 멈춘다. 파일을 지우면 기동 스크립트가 base 이미지의 기본 앱으로 떨어진다.

진입점은 둘이다.
- 코드서빙(전처리기 워커): `genon/preprocessor/worker/` — 파싱·청킹을 Temporal 큐로 받는다. HTTP 는 `/health`·`/version`.
- GenOS 전처리기(기본 전처리기 서비스): `genon/preprocessor/src/main.py` — preprocess-api 가 `/run` 을 부른다.
"""
raise SystemExit(
    "[doc_parser] 코드서빙 리비전의 시작 명령(START_COMMAND)이 비어 있습니다. "
    "`python -m genon.preprocessor.worker` 를 넣으세요.")
