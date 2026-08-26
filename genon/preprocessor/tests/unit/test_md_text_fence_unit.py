"""레이아웃 보존용 ```text 펜스 → 마크다운 단락 복원 단위 테스트."""
from __future__ import annotations

import pytest

from genon.preprocessor.converters.md_text_fence import (
    MarkdownTextFenceSpec,
    transform,
)

# 모니모 상품요약서 원문 형태 — ```text 펜스 + 레이아웃 파이프 줄 + 하드랩된 Q/A 본문.
SAMPLE = """# 삼성 팩 건강보험(2607) 상품요약서

<!-- source_page: 2 -->
## 원문 2쪽 - 상품의 특이사항

```text
|    |    |    |    상품의 특이사항
Q    이 상품의 특이사항은 무엇인가요?
A    - 이 상품의 1종(건강고지형)은 건강고지 상품으로 표준체에 해당하는 계약 전 알릴의무사항 항
       목(이하, "일반고지"라 한다) 대비 추가된 항목을 활용합니다.
     - 이 상품은 사망을 보장하며 각종 특약 부가로 다양한 보장 설계를 목적으로 합니다.
Q    갱신형 특약에서 갱신은 어떻게 이루어 지나요?
A    - 갱신형특약은 만료일 15일전까지 갱신하지 않겠다는 의사표시를 하지 않으면 갱신됩니다.
     ※ 보험료가 갱신됨에 따라 고령시점 보험료가 큰 폭으로 인상될 수 있습니다.
```
"""


@pytest.mark.unit
def test_fence_becomes_logical_unit_paragraphs():
    out, converted = transform(SAMPLE)

    assert converted == 1
    # 펜스 마커가 남으면 docling 이 다시 CodeItem 하나로 만든다.
    assert "```" not in out
    # 논리 단위(파이프 헤더 1 + Q/A 4 + 불릿 1 + ※ 1)마다 빈 줄로 끊긴 단락이 된다.
    units = [b for b in out.split("\n\n") if b.strip()]
    assert [u.splitlines()[0][:2] for u in units[-7:]] == [
        "상품", "Q ", "A ", "- ", "Q ", "A ", "※ ",
    ]
    # 하드랩된 줄은 단위 안에서 줄바꿈으로 유지한다(재결합하지 않는다).
    assert "알릴의무사항 항\n목(이하," in out


@pytest.mark.unit
def test_layout_pipes_removed():
    """`|` 를 남기면 md 백엔드가 표 모드에 들어가 뒤따르는 본문을 삼킨다(실측 1586자 소실)."""
    out, converted = transform(SAMPLE)

    assert converted == 1
    assert "|" not in out
    assert "상품의 특이사항" in out


@pytest.mark.unit
def test_indentation_removed():
    """들여쓰기가 남으면 marko 가 indented code block 으로 파싱해 다시 CodeItem 이 된다."""
    out, _ = transform(SAMPLE)

    fenceless = out.split("## 원문 2쪽 - 상품의 특이사항", 1)[1]
    assert not any(line.startswith("    ") for line in fenceless.splitlines())


@pytest.mark.unit
@pytest.mark.parametrize("lang", ["python", "json", "yaml"])
def test_real_code_fence_untouched(lang: str):
    text = f"# 문서\n\n```{lang}\ndef load(self, path: str) -> Doc:\n    return self.convert(path)\n```\n"

    out, converted = transform(text)

    assert converted == 0
    assert out == text


@pytest.mark.unit
def test_ascii_heavy_fence_untouched():
    """정보문자열이 없어도 한글 비율이 낮으면(=코드/쿼리) 변환하지 않는다."""
    text = (
        "# 문서\n\n```\n"
        "SELECT product_c, product_nm FROM tb_product WHERE group_c = 'HPP';\n"
        "```\n"
    )

    out, converted = transform(text)

    assert converted == 0
    assert out == text


@pytest.mark.unit
def test_unclosed_fence_untouched():
    text = "# 문서\n\n```text\nQ    닫는 펜스가 없습니다.\n"

    out, converted = transform(text)

    assert converted == 0
    assert out == text


@pytest.mark.unit
def test_no_fence_is_identity():
    text = "# 제목\n\n본문 첫 줄\n본문 둘째 줄\n"

    out, converted = transform(text)

    assert converted == 0
    assert out == text


@pytest.mark.unit
def test_rule_lines_dropped_and_block_prefix_escaped():
    """`---` 는 thematic break/setext 헤딩이 되고, 줄머리 `#`/`>` 는 헤딩·인용이 된다."""
    text = "```text\n----------\nQ    질문입니다\n# 제목처럼 보이는 본문\n> 인용처럼 보이는 본문\n```\n"

    out, converted = transform(text)

    assert converted == 1
    assert "----------" not in out
    assert "\\# 제목처럼 보이는 본문" in out
    assert "\\> 인용처럼 보이는 본문" in out


@pytest.mark.unit
def test_spec_defaults_and_apply():
    spec = MarkdownTextFenceSpec.from_config(True, ("product_hpp",))

    assert spec.doc_types == ("product_hpp",)
    assert spec.langs == ("", "text")
    assert spec.apply(SAMPLE)[1] == 1


@pytest.mark.unit
def test_spec_langs_narrowing():
    """langs 를 좁히면 언어 표기 없는 펜스는 대상에서 빠진다."""
    spec = MarkdownTextFenceSpec.from_config({"langs": ["text"]})
    text = "```\nQ    한글 산문입니다\nA    - 예\n```\n"

    assert spec.apply(text) == (text, 0)
    assert spec.apply(SAMPLE)[1] == 1


@pytest.mark.unit
def test_product_fence_sample_parser_to_chunk_round_trip():
    """출고 설정/샘플로 파싱→청킹 왕복 검증 — 펜스가 아이템 1개로 뭉치지 않는지.

    전처리가 없으면 펜스 본문 전체가 CodeItem 하나가 되어 청크 경계가 아이템 경계와
    맞지 않는다. front matter 제거와 함께 걸리는지(두 전처리 합성)도 여기서 확인한다.
    """
    import asyncio
    from pathlib import Path
    from unittest.mock import AsyncMock

    from fastapi import Request

    from genon.preprocessor.facade.chunking_processor import (
        DocumentProcessor as ChunkProcessor,
    )
    from genon.preprocessor.facade.parser_processor import (
        DocumentProcessor as ParserProcessor,
    )

    async def _run():
        request = Request(scope={"type": "http"})
        parser = ParserProcessor()
        parser._output_format = "docling"
        for enricher in parser._intel.custom_fields_enrichers:
            if "product_slf" in enricher._doc_types:
                enricher._call_llm = AsyncMock(return_value='{"PRODUCT_C":"2607"}')

        source = (
            Path(__file__).resolve().parents[2]
            / "sample_files" / "monimo" / "monimo_product_slf_fence_sample.md"
        )
        payload = await parser(request, str(source), doc_type="product_slf", log_level=3)
        vectors = await ChunkProcessor()(
            request, str(source), document=payload,
            chunk_size=1000, chunk_mode="split_only",
        )
        return [v.model_dump() for v in vectors]

    rows = asyncio.run(_run())
    texts = [r["text"] for r in rows]

    # chunk_size 1000 은 코드 하한 1024 로 보정된다(_MIN_CHUNK_SIZE).
    assert len(rows) > 1
    assert max(len(t) for t in texts) <= 1024
    # 펜스 마커가 남으면 다시 CodeItem 하나가 된다. 파이프가 남으면 표로 오인된다.
    assert all("```" not in t and "|" not in t for t in texts)
    # 펜스 본문의 Q/A 가 청크 텍스트에 살아있다(빈 표로 삼켜지지 않았다).
    joined = "\n".join(texts)
    assert "이 상품의 특이사항은 무엇인가요?" in joined
    assert "가족결합할인" in joined
    # front matter 전처리도 같이 걸린다.
    assert all("conversion_note:" not in t for t in texts)


@pytest.mark.unit
def test_product_fence_sample_keeps_repeated_qa():
    """반복되는 Q/A 가 반복 횟수만큼 남는다 — 청커 본문 dedup 회귀 방지.

    상품요약서는 같은 특이사항 문단이 여러 쪽에 실린다. 예전에는 한 섹션이 통째로 한 그룹이
    되는 큰 chunk_size 에서 청커가 중복 본문을 버려, docling 아이템 65개 중 36개가 사라졌다.
    chunk_size 를 작게 주면 섹션이 쪼개져 증상이 감춰지므로 여기서는 넉넉히 준다.
    """
    import asyncio
    import re
    from pathlib import Path
    from unittest.mock import AsyncMock

    from fastapi import Request

    from genon.preprocessor.facade.chunking_processor import (
        DocumentProcessor as ChunkProcessor,
    )
    from genon.preprocessor.facade.parser_processor import (
        DocumentProcessor as ParserProcessor,
    )

    source = (
        Path(__file__).resolve().parents[2]
        / "sample_files" / "monimo" / "monimo_product_slf_fence_sample.md"
    )
    raw = source.read_text(encoding="utf-8")

    async def _run():
        request = Request(scope={"type": "http"})
        parser = ParserProcessor()
        parser._output_format = "docling"
        for enricher in parser._intel.custom_fields_enrichers:
            if "product_slf" in enricher._doc_types:
                enricher._call_llm = AsyncMock(return_value='{"PRODUCT_C":"2607"}')

        payload = await parser(request, str(source), doc_type="product_slf", log_level=3)
        vectors = await ChunkProcessor()(
            request, str(source), document=payload,
            chunk_size=10000, chunk_mode="split_only",
        )
        return "\n".join(v.model_dump()["text"] for v in vectors)

    joined = asyncio.run(_run())

    for question in ("이 상품의 특이사항은 무엇인가요?",
                     "갱신형 특약에서 갱신은 어떻게 이루어 지나요?"):
        expected = len(re.findall(re.escape(question), raw))
        assert expected > 1, "샘플이 반복 케이스를 담고 있어야 한다"
        assert joined.count(question) == expected, (
            f"{question!r}: 청크에 {joined.count(question)}회(원문 {expected}회)"
        )


@pytest.mark.unit
@pytest.mark.parametrize("cfg", [
    {"langs": []},
    {"langs": 3},
    {"min_hangul_ratio": "높게"},
    {"min_hangul_ratio": 1.5},
    "text",
])
def test_spec_rejects_invalid_config(cfg):
    with pytest.raises(ValueError):
        MarkdownTextFenceSpec.from_config(cfg)
