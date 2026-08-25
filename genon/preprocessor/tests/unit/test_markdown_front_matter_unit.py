"""Markdown front matter 선택/제외 규칙 단위 테스트."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from genon.preprocessor.facade.enrichment import custom_fields_enricher as cfe
from genon.preprocessor.facade.enrichment.custom_fields_enricher import (
    CustomFieldsEnricher,
)
from genon.preprocessor.facade.enrichment.markdown_front_matter import (
    MarkdownFrontMatterSpec,
    build_markdown_front_matter_specs,
)


def _spec(**front_matter) -> MarkdownFrontMatterSpec:
    return MarkdownFrontMatterSpec.from_config({
        "doc_type": "product_slf",
        "extractor": "llm",
        "markdown": {"front_matter": front_matter},
    })


@pytest.mark.unit
def test_extract_selected_metadata_and_exclude_all_text(tmp_path: Path):
    source = tmp_path / "sample.md"
    source.write_text(
        "\ufeff---\r\n"
        "title: 상품 설명서\r\n"
        "source_pages: 9\r\n"
        "created_at: 2026-01-12\r\n"
        "tags: [보험, 교통]\r\n"
        "---\r\n\r\n# 실제 제목\r\n\r\n본문입니다.\r\n",
        encoding="utf-8",
    )
    spec = _spec(
        metadata_fields={
            "source_pages": "source_pages",
            "created_at": "created_date",
            "tags": "tags",
        },
        exclude_text_fields=["*"],
    )

    result = spec.parse(source)

    assert result.found is True
    assert result.metadata == {
        "source_pages": 9,
        "created_date": "2026-01-12",
        "tags": ["보험", "교통"],
    }
    assert result.filtered_text.startswith("# 실제 제목")
    assert "source_pages" not in result.filtered_text
    assert "title:" not in result.filtered_text
    assert "source_pages: 9" in result.prompt_prefix


@pytest.mark.unit
def test_metadata_and_text_selection_are_independent(tmp_path: Path):
    source = tmp_path / "sample.md"
    source.write_text(
        "---\nkeep: 검색어\nsecret: 숨김\nmetadata_only: 값\n---\n# 제목\n본문\n",
        encoding="utf-8",
    )
    spec = _spec(
        metadata_fields=["metadata_only"],
        exclude_text_fields=["secret", "metadata_only"],
    )

    result = spec.parse(source)

    assert result.metadata == {"metadata_only": "값"}
    assert "keep: 검색어" in result.filtered_text
    assert "secret:" not in result.filtered_text
    assert "metadata_only:" not in result.filtered_text
    assert "secret: 숨김" in result.prompt_prefix
    assert "keep:" not in result.prompt_prefix


@pytest.mark.unit
def test_missing_front_matter_is_noop(tmp_path: Path):
    source = tmp_path / "plain.md"
    source.write_text("# 제목\n본문\n", encoding="utf-8")
    result = _spec(metadata_fields=["source_file"], exclude_text_fields=["*"]).parse(source)
    assert result.found is False
    assert result.metadata == {}
    assert result.filtered_text is None


@pytest.mark.unit
def test_missing_and_invalid_policies_default_to_ignore(tmp_path: Path):
    spec = _spec(metadata_fields=["source_file"], exclude_text_fields=["*"])
    assert spec.on_missing == "ignore"
    assert spec.on_invalid == "ignore"

    malformed = tmp_path / "malformed.md"
    malformed.write_text("---\nsource_file: [broken\n---\n# 제목\n", encoding="utf-8")
    result = spec.parse(malformed)
    assert result.found is False
    assert result.metadata == {}
    assert result.filtered_text is None


@pytest.mark.unit
def test_invalid_duplicate_key_and_reserved_target_fail(tmp_path: Path):
    source = tmp_path / "duplicate.md"
    source.write_text("---\na: 1\na: 2\n---\n본문\n", encoding="utf-8")
    with pytest.raises(ValueError, match="중복 키"):
        _spec(
            metadata_fields=["a"], exclude_text_fields=["*"], on_invalid="error"
        ).parse(source)

    with pytest.raises(ValueError, match="예약 필드"):
        _spec(metadata_fields={"source": "file_path"})


@pytest.mark.unit
def test_builder_routes_by_document_extractor_only():
    configs = [{
        "doc_type": "product_slf",
        "extractor": "llm",
        "markdown": {"front_matter": {
            "metadata_fields": ["source_file"],
            "exclude_text_fields": ["*"],
        }},
    }]
    specs = build_markdown_front_matter_specs(configs)
    assert len(specs) == 1
    assert specs[0].matches(" PRODUCT_SLF ")
    assert not specs[0].matches("product_ssf")

    configs[0]["extractor"] = "json_mapping"
    with pytest.raises(ValueError, match="문서 단위 extractor"):
        build_markdown_front_matter_specs(configs)


@pytest.mark.unit
def test_builder_loads_child_markdown_config_and_applies_inline_override(tmp_path: Path):
    child = tmp_path / "product.yaml"
    child.write_text(
        "markdown:\n"
        "  front_matter:\n"
        "    metadata_fields:\n"
        "      source_file: source_file\n"
        "      created_at: created_date\n"
        "    exclude_text_fields: ['*']\n",
        encoding="utf-8",
    )
    config = {
        "doc_type": "product_slf",
        "extractor": "llm",
        "config_file": child.name,
        "resource_path": str(tmp_path),
    }

    specs = build_markdown_front_matter_specs([config])
    assert len(specs) == 1
    assert specs[0].metadata_fields == {
        "source_file": "source_file",
        "created_at": "created_date",
    }
    assert specs[0].exclude_text_fields == ("*",)

    config["markdown"] = {"front_matter": {"exclude_text_fields": ["conversion_note"]}}
    overridden = build_markdown_front_matter_specs([config])
    assert overridden[0].metadata_fields == specs[0].metadata_fields
    assert overridden[0].exclude_text_fields == ("conversion_note",)


@pytest.mark.unit
def test_inline_markdown_false_disables_child_config(tmp_path: Path):
    child = tmp_path / "product.yaml"
    child.write_text(
        "markdown:\n"
        "  front_matter:\n"
        "    metadata_fields: [source_file]\n",
        encoding="utf-8",
    )
    config = {
        "doc_type": "product_slf",
        "extractor": "llm",
        "config_file": child.name,
        "resource_path": str(tmp_path),
        "markdown": False,
    }

    assert build_markdown_front_matter_specs([config]) == []

    config["markdown"] = {"front_matter": False}
    assert build_markdown_front_matter_specs([config]) == []


@pytest.mark.unit
def test_front_matter_and_constants_survive_without_llm(monkeypatch):
    stored = []
    monkeypatch.setattr(
        cfe,
        "store_metadata_in_document",
        lambda document, metadata, **kwargs: stored.append(metadata),
    )
    enricher = CustomFieldsEnricher(
        doc_type="product_slf",
        output_fields=["PRODUCT_C", "PRODUCT_NM"],
        constants={"GROUP_C": "SLF"},
    )
    document = AsyncMock()
    enricher._extract_raw_text = lambda _document: "본문"
    context = {}

    asyncio.run(enricher.enrich(
        document,
        doc_type="product_slf",
        _enrichment_context=context,
        _markdown_front_matter={
            "metadata": {"source_pages": 9, "PRODUCT_NM": "구조화 상품명"},
            "prompt_prefix": "[Markdown front matter]\nsource_pages: 9",
        },
    ))

    assert stored == [{
        "PRODUCT_C": None,
        "PRODUCT_NM": "구조화 상품명",
        "source_pages": 9,
        "GROUP_C": "SLF",
    }]
    assert context["metadata"] == stored[0]


@pytest.mark.unit
def test_no_llm_and_no_structured_source_stores_nothing(monkeypatch):
    """LLM 도 없고 front matter/constants 도 없으면 예전처럼 아무것도 저장하지 않는다.

    이 가드가 없으면 url 이 빈 설정에서 output_fields 가 전부 null 로 저장돼 모든 청크에
    빈 property 가 생긴다.
    """
    stored = []
    monkeypatch.setattr(
        cfe,
        "store_metadata_in_document",
        lambda document, metadata, **kwargs: stored.append(metadata),
    )
    enricher = CustomFieldsEnricher(
        doc_type="product_slf",
        output_fields=["PRODUCT_C", "PRODUCT_NM"],
    )
    enricher._extract_raw_text = lambda _document: "본문"
    context = {}

    asyncio.run(enricher.enrich(
        AsyncMock(), doc_type="product_slf", _enrichment_context=context
    ))

    assert stored == []
    assert context == {}


@pytest.mark.unit
def test_front_matter_keys_are_the_only_typed_keys(monkeypatch):
    """타입 보존(JSON 표식)은 front matter 유래 키에만 적용된다(#360)."""
    calls = []
    monkeypatch.setattr(
        cfe,
        "store_metadata_in_document",
        lambda document, metadata, **kwargs: calls.append(kwargs),
    )
    enricher = CustomFieldsEnricher(
        doc_type="product_slf",
        output_fields=["PRODUCT_NM"],
        constants={"GROUP_C": "SLF"},
    )
    enricher._extract_raw_text = lambda _document: "본문"

    asyncio.run(enricher.enrich(
        AsyncMock(),
        doc_type="product_slf",
        _markdown_front_matter={"metadata": {"source_pages": 9}, "prompt_prefix": ""},
    ))

    assert calls[0]["typed_keys"] == {"source_pages"}
    assert calls[0]["preserve_nulls"] is True


@pytest.mark.unit
def test_front_matter_wins_over_llm_and_is_added_to_prompt(monkeypatch):
    stored = []
    monkeypatch.setattr(
        cfe,
        "store_metadata_in_document",
        lambda document, metadata, **kwargs: stored.append(metadata),
    )
    enricher = CustomFieldsEnricher(
        doc_type="product_slf",
        url="http://example",
        model="model",
        output_fields=["PRODUCT_NM"],
        user_prompt="{{raw_text}}",
    )
    enricher._extract_raw_text = lambda _document: "본문"
    enricher._call_llm = AsyncMock(return_value='{"PRODUCT_NM":"LLM 상품명"}')

    asyncio.run(enricher.enrich(
        object(),
        doc_type="product_slf",
        _markdown_front_matter={
            "metadata": {"PRODUCT_NM": "구조화 상품명"},
            "prompt_prefix": "[Markdown front matter]\nfile: 구조화 상품명",
        },
    ))

    assert stored[0]["PRODUCT_NM"] == "구조화 상품명"
    raw_text_arg = enricher._call_llm.await_args.args[0]
    assert raw_text_arg.startswith("[Markdown front matter]")
    assert raw_text_arg.endswith("본문")


@pytest.mark.unit
def test_product_markdown_parser_to_chunk_round_trip():
    """출고 설정/샘플로 front matter 제거와 Docling JSON metadata 왕복을 검증."""
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
                enricher._call_llm = AsyncMock(return_value=(
                    '{"PRODUCT_C":"30387",'
                    '"PRODUCT_NM":"삼성 s교통상해보험(2501)(무배당)"}'
                ))

        source = (
            Path(__file__).resolve().parents[2]
            / "sample_files" / "monimo" / "monimo_product_slf_sample.md"
        )
        payload = await parser(request, str(source), doc_type="product_slf", log_level=3)
        vectors = await ChunkProcessor()(
            request,
            str(source),
            document=payload,
            chunk_size=10000,
            chunk_mode="split_only",
            include_chunk_header=0,
        )
        return [vector.model_dump() for vector in vectors]

    rows = asyncio.run(_run())
    forbidden = ("source_file:", "source_pages:", "created_at:", "conversion_note:")
    assert len(rows) == 7
    assert all(not any(token in row["text"] for token in forbidden) for row in rows)
    assert all(row["source_file"] == "1768198211902.pdf" for row in rows)
    assert all(row["source_pages"] == 9 for row in rows)
    assert all(row["created_date"] == 20260112 for row in rows)
    assert all(row["author"] == "김도연" for row in rows)
    assert all(row["PRODUCT_C"] == "30387" for row in rows)
    assert all(row["GROUP_C"] == "SLF" for row in rows)
    assert all(row["doc_type"] == "product_slf" for row in rows)
