# Engineering context (Claude · GPT 리뷰어 공용)

GPT 독립 리뷰(`.claude/review/gpt-review.sh`)와 구현 에이전트가 같이 읽는다. 규칙의 정본은 루트 `CLAUDE.md`.
이 저장소는 **public** 이다 — 리뷰 결과에 내부 주소·계정·비밀값을 쓰지 않는다.

## 정본 문서
- 저장소 규칙: `CLAUDE.md`(「전처리 Studio 작업」 절 포함)
- 고객용 계약·훅: `genon/preprocessor/manual/`(facade_hooks.md 등)
- 이번 작업 요구사항: task brief(`GPT_REVIEW_BRIEF`, 없으면 `genon/docs/REVIEW_CONTEXT.md`)
- 문서끼리 어긋나면 어긋난다고 보고한다 — 우선순위를 지어내지 않는다

## 런타임
- 전처리기: `genon/preprocessor/.venv`(Python 3.13, uv) · 코드서빙 이미지는 별도 base 이미지
- temporalio 1.34.0(전처리기 워커) · docling(이 저장소의 포크)

## 검증 명령
- `cd genon/preprocessor && .venv/bin/python -m pytest -m unit -q`
- 단건: `python -m genon.preprocessor.activities run --activity parse <문서> -o parse.json`(#477 머지 후)
- 워커 큐 왕복은 짝 저장소(data-ingestion-pipeline)의 로컬 스모크로 본다

## 지켜야 할 불변식
- 파사드 파일(`activities/*.py`)에 처리 로직을 넣지 않는다 — 공용 하위 모듈에 둔다
- 액티비티 이름 = 파일명, 인자 dict 하나(상대 참조), 반환 요약 dict
- 프로세스당 1건(공유 인스턴스 설정 경쟁) · 구버전(HTTP) 호환은 요구사항이 아니다

## 에이전트 환경 계약
- 두 에이전트는 같은 저장소 루트·HEAD·작업 트리를 본다. 리뷰 중에는 구현을 멈춘다
- Claude 가 코드 수정·테스트를 맡고, GPT 는 `codex exec --sandbox read-only` 로 정적 리뷰만 한다
- 리뷰어는 읽지 못한 자료와 돌리지 않은 테스트를 그렇다고 적는다
