# 파싱용 전처리기 v.2.2.0 (2026-06-02 Release)
#
# ── 이 파일에서 고칠 자리 ────────────────────────────────────────────────────
# 처리 본체는 facade/core/parser.py 에 있고 열어 볼 일이 없다. 새 문서 유형을
# 만났을 때 손대는 지점은 아래 셋뿐이며, 셋 다 core 의 이름을 그대로 덮어쓴다.
#
#   1) ROUTES              새 **확장자**를 받을 때. 표에 한 줄 + _route_* 하나.
#   2) _route_*            그 확장자를 어떻게 파싱할지.
#   3) _load_json_payload  .json 원천의 **구조**가 설정으로 안 풀릴 때.
#                          두 JSON 경로의 유일한 입구다. doc_type 게이팅 필수.
#
# 새 **doc_type** 을 추가하는 것뿐이라면 코드가 아니라 custom_field_*.yaml 이 먼저다.
# 어디까지 설정으로 되는지는 gitbook_doc/parser_processor.md 의 지원 매트릭스를 본다.
from __future__ import annotations

# main.py 의 예외 핸들러가 이 이름으로 잡는다. core 가 던지는 것과 같은 클래스다.
from genon.preprocessor.facade.core.errors import GenosServiceException  # noqa: F401
from genon.preprocessor.facade.core.parser import ParserCore


class DocumentProcessor(ParserCore):
    """파싱 단계만 수행하고 결과를 JSON으로 반환하는 파사드.

    청킹/벡터 조합은 수행하지 않는다.
    IS_PARSER: main.py 가 이 프로세서가 /parser API 전용임을 식별하는 데 사용.
    """

    IS_PARSER: bool = True
