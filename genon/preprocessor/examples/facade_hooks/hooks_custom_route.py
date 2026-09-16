"""ROUTES 에 자기 라우트를 추가하는 예시.

`edit_input` 로 원천을 이미 처리 가능한 포맷으로 바꾸는 것이 1순위다. 그것으로 안 되는
원천(로그·고정폭 텍스트·사내 전문 포맷처럼 표준 포맷 어느 것으로도 안 맞는 것)만
라우트를 직접 쓴다. **핸들러는 이 파일에 두면 된다** — core 를 고치지 않는다.

라우트 계약은 셋뿐이다.
    async def route_<이름>(self, file_path, ext, ctx, **kwargs) -> dict | None
  · 응답 dict 를 돌려주면 거기서 끝난다. `content`/`usage` 같은 나머지 키는 core 가 채운다.
  · None 을 돌려주면 ROUTES 의 다음 후보로 넘어간다(폴스루).
  · ctx 는 분기 사이 공유 상태다. 직접 만든 라우트는 보통 쓰지 않는다.

**ROUTES 에 먼저 등록하고 시험한다.** 등록 전에는 캐치올(route_other)이 받아 결과가 다르다.
실측: 아래 그대로 `.log` 3줄을 파싱하면 element 3개, 이어서 청킹하면 청크 3개다.
"""
from __future__ import annotations

from genon.preprocessor.facade.core import toolbox as tb


class Hooks:
    # 새 확장자는 표 맨 앞에 둔다. 기존 표는 그대로 뒤에 붙인다.
    ROUTES = (((".log",), "route_log"),) + (
        # ... 기존 ROUTES 표 그대로 ...
    )

    async def route_log(self, file_path: str, ext: str, ctx: dict, **kwargs) -> dict:
        """한 줄 = element 1개. 인코딩은 toolbox 가 흡수한다(utf-8-sig/utf-8/cp949)."""
        lines = [line for line in tb.read_text_with_fallback(file_path).splitlines()
                 if line.strip()]
        return {"elements": tb.make_elements(lines)}

    async def route_orders(self, file_path: str, ext: str, ctx: dict, **kwargs) -> dict:
        """행 1개 = 청크 1개로 적재하고 싶을 때.

        category 를 `custom_fields_row` 로 두면 청커의 행 기반 경로가 받아 metadata 를
        청크 property 로 올린다. 청커는 doc_type 을 보지 않고 category 만 본다.
        """
        rows = _read_orders(file_path)          # 사이트 코드
        return {"elements": tb.make_elements(
            [{"content": row["본문"], "metadata": {"ORDER_NO": row["주문번호"]}}
             for row in rows],
            category="custom_fields_row",
        )}


def _read_orders(file_path: str) -> list[dict]:
    raise NotImplementedError("사이트 원천에 맞춰 구현합니다.")
