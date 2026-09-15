"""doc_type 으로 표 설명(table_text_description)을 끄는 예시.

아래 상수 한 줄과 `__call__` 의 두 줄을 facade/parser_processor.py 에 붙인다.
나머지(ROUTES·pre_parse·post_parse)는 원본 그대로 둔다.

yaml 의 `enrichment.table_text_description` 은 프로세서 전역 스위치라 문서유형을 가려서
끌 수 없다. 표가 아주 많은 문서는 한 번의 LLM 호출이 감당할 표 수를 넘겨 응답 JSON 이
잘리고, 그 배치의 표 전부가 설명 없이 지나간다. 그런 문서유형만 여기서 끈다.

`table_text_desc` 는 독립 실행기와 custom_fields 융합 경로가 함께 보는 키라,
0 으로 두면 두 경로 모두 표 설명을 만들지 않는다.
"""
from __future__ import annotations

from fastapi import Request

# 표 설명을 쓰지 않을 doc_type. 소문자로 쓴다(런타임 doc_type 이 소문자로 정규화된다).
SKIP_TABLE_DESC_DOC_TYPES = ("cs_hpp",)


class Hooks:
    async def __call__(self, request: Request, file_path: str, **kwargs) -> dict:
        ext = self.resolve_ext(file_path)
        doc_type = self.resolve_doc_type(**kwargs)
        if doc_type in SKIP_TABLE_DESC_DOC_TYPES:   # 추가 1
            kwargs["table_text_desc"] = 0           # 추가 2
        result = await self.run(request, file_path, **kwargs)
        return self.post_parse(ext, doc_type, result)
