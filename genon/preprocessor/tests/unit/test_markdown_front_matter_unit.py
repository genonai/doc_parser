"""Markdown front matter 선택/제외 규칙 단위 테스트."""
from __future__ import annotations

import asyncio
import textwrap
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
    # 내부 표현은 `alias` 와 같은 방향이다 — `{목표: [원천키…]}`, 먼저 찾은 것을 쓴다.
    # 옛 표기 `metadata_fields`(`{원천키: 목표}`)는 여기서 뒤집혀 흡수된다.
    assert specs[0].field_aliases == {
        "source_file": ["source_file"],
        "created_date": ["created_at"],
    }
    assert specs[0].exclude_text_fields == ("*",)

    config["markdown"] = {"front_matter": {"exclude_text_fields": ["conversion_note"]}}
    overridden = build_markdown_front_matter_specs([config])
    assert overridden[0].field_aliases == specs[0].field_aliases
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


@pytest.mark.unit
def test_one_front_matter_key_can_feed_several_targets():
    """`created_at` 하나가 날짜 벡터 필드와 사람이 읽는 표기 양쪽에 필요할 때가 있다.

    front matter 에는 그 키가 하나뿐이라 목표를 하나로 제한하면 둘 중 하나를 포기해야 했다.
    """
    spec = build_markdown_front_matter_specs([{
        "doc_type": "p", "extractor": "llm",
        "markdown": {"front_matter": {
            "metadata_fields": {"created_at": ["created_date", "PRODUCT_ATTRS"]},
        }},
    }])[0]
    # 목표를 키로 뒤집어도 관계는 그대로다 — 목표 둘이 같은 원천키를 가리킨다.
    assert spec.field_aliases == {
        "created_date": ["created_at"],
        "PRODUCT_ATTRS": ["created_at"],
    }


@pytest.mark.unit
def test_fields_alias_declares_the_front_matter_source(tmp_path: Path):
    """`fields.<목표>.alias` 가 front matter 키를 고르는 새 단일 선언 자리다.

    예전에는 같은 개념이 `front_matter.metadata_fields` 와 `fields` 두 자리로 흩어져
    한 필드의 규칙을 두 곳에서 봐야 했다.
    """
    child = tmp_path / "product.yaml"
    child.write_text(
        "schema: v2\n"
        "source:\n"
        "  kind: document\n"
        "  pre:\n"
        "    markdown:\n"
        "      front_matter:\n"
        "        exclude_text_fields: ['*']\n"
        "fields:\n"
        "  AUTHOR: {alias: [author, writer]}\n"
        "  created_date: {alias: [created_at]}\n",
        encoding="utf-8",
    )
    spec = build_markdown_front_matter_specs([{
        "doc_type": "p", "extractor": "llm",
        "config_file": child.name, "resource_path": str(tmp_path),
    }])[0]

    assert spec.field_aliases == {"AUTHOR": ["author", "writer"], "created_date": ["created_at"]}


@pytest.mark.unit
def test_fields_alias_uses_the_first_source_present(tmp_path: Path):
    """여러 원천키를 적으면 **먼저 찾은 것**을 쓴다(다른 kind 의 alias 와 같은 규칙)."""
    source = tmp_path / "sample.md"
    source.write_text("---\nwriter: 김도연\n---\n\n본문.\n", encoding="utf-8")
    spec = MarkdownFrontMatterSpec.from_config({
        "doc_type": "p", "extractor": "llm",
        "markdown": {"front_matter": {"exclude_text_fields": ["*"]}},
        "front_matter_map": {"AUTHOR": ["author", "writer"]},
    })

    assert spec.parse(source).metadata == {"AUTHOR": "김도연"}


@pytest.mark.unit
def test_fields_alias_wins_over_legacy_metadata_fields(tmp_path: Path):
    """같은 목표를 두 자리에 적었으면 새 표기가 이긴다 — 조용히 합치지 않는다."""
    source = tmp_path / "sample.md"
    source.write_text("---\nauthor: 옛값\nwriter: 새값\n---\n\n본문.\n", encoding="utf-8")
    spec = MarkdownFrontMatterSpec.from_config({
        "doc_type": "p", "extractor": "llm",
        "markdown": {"front_matter": {
            "metadata_fields": {"author": "AUTHOR"},
            "exclude_text_fields": ["*"],
        }},
        "front_matter_map": {"AUTHOR": ["writer"]},
    })

    assert spec.parse(source).metadata == {"AUTHOR": "새값"}


@pytest.mark.unit
def test_duplicate_targets_are_still_rejected():
    """목표가 겹치면 어느 원천이 이겼는지 알 수 없어진다 — 종전대로 막는다."""
    with pytest.raises(ValueError, match="중복"):
        build_markdown_front_matter_specs([{
            "doc_type": "p", "extractor": "llm",
            "markdown": {"front_matter": {
                "metadata_fields": {"created_at": "X", "author": ["X"]},
            }},
        }])


# ── document 의 공통 필드 값 파이프라인(values / transform / template) ────────
#
# 실제 원천 파일로 검증한다 — front matter 값의 형태(코드값·비표준 날짜·브랜드 분리)를
# 흉내낸 sample_files/monimo/monimo_product_slf_fields_sample.md 를 쓴다. 생성 스크립트는
# examples/parse_chunk/make_product_slf_fields_sample.py 다.

FIELDS_SAMPLE_MD = (
    Path(__file__).resolve().parents[2]
    / "sample_files" / "monimo" / "monimo_product_slf_fields_sample.md"
)

PIPELINE_V2 = """
schema: v2
source:
  kind: document
  pre:
    markdown:
      front_matter:
        exclude_text_fields: ['*']
fields:
  BRAND_NM:    {alias: [brand]}
  PRODUCT_NM:  {alias: [product_name]}
  PRODUCT_C:   {alias: [product_code]}
  SALE_STATUS: {alias: [sale_state], values: {SALE: ["1"], STOP: ["0"]}}
  created_date: {alias: [created_at], transform: date_int_flex}
  DISPLAY_NM:  {template: "{{BRAND_NM}} {{PRODUCT_NM}}"}
  GROUP_C:     {const: SLF}
llm:
- out: [SUMMARY_TEXT]
  endpoint: {url: "http://example/v1/chat/completions", model: model}
  prompt: {user: "{{raw_text}}"}
"""


def _pipeline_enricher(tmp_path, config_text=PIPELINE_V2):
    """v2 설정 파일을 임시로 쓰고 enricher 와 front matter spec 을 함께 만든다."""
    path = tmp_path / "custom_field_doc.yaml"
    path.write_text(textwrap.dedent(config_text).lstrip(), encoding="utf-8")
    block = {
        "doc_type": "p", "extractor": "llm",
        "config_file": path.name, "resource_path": str(tmp_path),
    }
    enricher = CustomFieldsEnricher(config_file=path.name, resource_path=str(tmp_path))
    spec = build_markdown_front_matter_specs([block])[0]
    return enricher, spec


def _pipeline_fields(tmp_path, config_text=PIPELINE_V2, llm_result=None):
    enricher, spec = _pipeline_enricher(tmp_path, config_text)
    front_matter = spec.parse(FIELDS_SAMPLE_MD).metadata
    return enricher._normalize_output_fields(llm_result or {}, front_matter)


@pytest.mark.unit
def test_document_alias_reads_front_matter(tmp_path: Path):
    """`fields.<목표>.alias` 로 front matter 값을 가져온다."""
    fields = _pipeline_fields(tmp_path)

    assert fields["PRODUCT_C"] == "90001"
    assert fields["BRAND_NM"] == "삼성생명"


@pytest.mark.unit
def test_document_values_folds_a_code_value(tmp_path: Path):
    """`values` — front matter 의 코드값(`"1"`)이 적재 표준값으로 접힌다."""
    assert _pipeline_fields(tmp_path)["SALE_STATUS"] == "SALE"


@pytest.mark.unit
def test_document_transform_runs_on_a_front_matter_value(tmp_path: Path):
    """`transform` — 구분자가 다른 날짜 문자열(`2026.01.12`)이 YYYYMMDD 정수가 된다.

    보고된 요구("프론트메타에서 추출한 항목에 transform 이 필요")의 본체다.
    """
    assert _pipeline_fields(tmp_path)["created_date"] == 20260112


@pytest.mark.unit
def test_document_template_joins_other_fields(tmp_path: Path):
    """`template` — 브랜드와 상품명을 합쳐 표시용 이름을 만든다."""
    fields = _pipeline_fields(tmp_path)

    assert fields["DISPLAY_NM"] == "삼성생명 든든한 여행상해보험(2601)(무배당)"


@pytest.mark.unit
def test_document_pipeline_runs_after_the_merge_priority(tmp_path: Path):
    """적용 순서 `default < LLM < front matter < const` **뒤에** 파이프라인이 돈다.

    다른 kind 와 순서가 어긋나는 것이 이 기능에서 가장 나기 쉬운 결함이다.
    LLM 이 내놓은 값도, const 로 못 박은 값도 파이프라인을 지나야 한다.
    """
    config = """
schema: v2
source:
  kind: document
  pre:
    markdown:
      front_matter:
        exclude_text_fields: ['*']
fields:
  SALE_STATUS: {alias: [sale_state], values: {SALE: ["1"], STOP: ["0"]}}
  LLM_STATUS:  {values: {GOLD: ["1"], SILVER: ["2"]}}
  GROUP_C:     {const: "1", values: {SLF: ["1"]}}
  DISPLAY:     {template: "{{GROUP_C}}/{{SALE_STATUS}}"}
llm:
- out: [LLM_STATUS]
  endpoint: {url: "http://example/v1/chat/completions", model: model}
  prompt: {user: "{{raw_text}}"}
"""
    fields = _pipeline_fields(tmp_path, config, llm_result={"LLM_STATUS": "2"})

    assert fields["SALE_STATUS"] == "SALE"   # front matter 값이 values 를 지난다
    assert fields["LLM_STATUS"] == "SILVER"  # LLM 값도 values 를 지난다
    assert fields["GROUP_C"] == "SLF"        # const 가 덮은 값도 values 를 지난다
    assert fields["DISPLAY"] == "SLF/SALE"   # template 은 파이프라인 마지막이다


@pytest.mark.unit
def test_document_rejects_a_bad_transform_at_startup(tmp_path: Path):
    """잘못된 변환기 이름은 요청 때가 아니라 기동 시에 잡는다(다른 kind 와 동일)."""
    config = """
schema: v2
source:
  kind: document
fields:
  X: {const: "1", transform: no_such_transform}
llm:
- out: [Y]
  endpoint: {url: "http://example/v1/chat/completions", model: model}
  prompt: {user: "{{raw_text}}"}
"""
    with pytest.raises(ValueError, match="no_such_transform"):
        _pipeline_enricher(tmp_path, config)


@pytest.mark.unit
def test_document_without_front_matter_still_works(tmp_path: Path):
    """front matter 가 없는 순수 LLM 경로가 깨지지 않는다.

    `fields.<목표>.alias` 선언이 있어도 원천에 front matter 가 없으면 그 필드는 조용히
    빈 값으로 지나가야 한다 — cs_hpp/card 처럼 md 가 아닌 원천이 이 경로다.
    """
    enricher, _spec = _pipeline_enricher(tmp_path)
    fields = enricher._normalize_output_fields({"SUMMARY_TEXT": "요약"})

    assert fields["SUMMARY_TEXT"] == "요약"
    assert fields["GROUP_C"] == "SLF"
    assert fields.get("PRODUCT_C") is None
