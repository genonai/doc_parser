"""비표준 확장자 별칭(formats.extension_aliases) 단위 테스트.

원천이 표준 확장자를 쓰지 않는 경우(예: 마크다운+HTML 혼합 산출물이 `*.parsed` 로 옴)를
설정 한 줄로 받기 위한 장치다. 검증 대상은 두 가지다.

1. 설정 정규화(`parse_extension_aliases`) — 점 보정/소문자화/이상값 제거/연쇄 미추종.
2. parser 라우팅 — `.parsed` 입력이 md 분기를 타고, docling 에는 `.md` 이름의 사본이
   넘어가며(그래야 docling `_guess_format` 이 포맷을 판정한다), artifacts 경로 기준은
   원본 경로로 유지되는가.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from genon.preprocessor.facade.common import format_alias as fa


# 캡처 원천과 같은 형태 — 마크다운 본문에 HTML 표가 섞여 있다.
MIXED_MD_HTML = (
    "# [AI 에이전트용]\n\n"
    "- **2015년 6월 25일**\n\n"
    "<table><tbody><tr><td>문서ID</td><td>CS-HPP-0231</td></tr></tbody></table>\n"
)


# ---------------------------------------------------------------------------
# 1. 설정 정규화
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_parse_extension_aliases_normalizes_dot_and_case():
    aliases = fa.parse_extension_aliases({"extension_aliases": {"PARSED": "MD"}})
    assert aliases == {".parsed": ".md"}


@pytest.mark.unit
@pytest.mark.parametrize("raw", [
    {},                                   # 키 자체가 없음
    {"extension_aliases": None},          # 값이 비어 있음
    {"extension_aliases": [".parsed"]},   # 매핑이 아님
])
def test_parse_extension_aliases_missing_or_malformed_is_empty(raw):
    assert fa.parse_extension_aliases(raw) == {}


@pytest.mark.unit
def test_parse_extension_aliases_drops_invalid_entries():
    aliases = fa.parse_extension_aliases({"extension_aliases": {
        ".parsed": ".md",
        ".md": ".md",          # 자기 자신 → 제거
        ".a/b": ".md",         # 경로 구분자 → 제거
        ".tar.gz": ".md",      # 다중 확장자 → 제거
        ".x": "",              # 빈 값 → 제거
    }})
    assert aliases == {".parsed": ".md"}


@pytest.mark.unit
def test_parse_extension_aliases_does_not_follow_chain():
    """a→b, b→c 를 연쇄로 따르지 않는다(한 번만 치환)."""
    aliases = fa.parse_extension_aliases({"extension_aliases": {
        ".parsed": ".mdx", ".mdx": ".md",
    }})
    assert fa.resolve_ext(".parsed", aliases) == ".mdx"


@pytest.mark.unit
def test_resolve_ext_passthrough_when_no_alias():
    assert fa.resolve_ext(".md", {}) == ".md"
    assert fa.resolve_ext(".md", {".parsed": ".md"}) == ".md"


@pytest.mark.unit
def test_materialize_alias_copy_renames_suffix_and_keeps_content(tmp_path: Path):
    src = tmp_path / "INC_235488_02_20260626103138.html.parsed"
    src.write_text(MIXED_MD_HTML, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()

    out = Path(fa.materialize_alias_copy(str(src), ".md", str(work)))

    assert out.name == "INC_235488_02_20260626103138.html.md"
    assert out.parent == work
    assert out.read_text(encoding="utf-8") == MIXED_MD_HTML
    assert src.exists()  # 원본은 건드리지 않는다


# ---------------------------------------------------------------------------
# 2. parser 라우팅
# ---------------------------------------------------------------------------

def _stub_processor(cls, aliases: dict[str, str]):
    """__init__ 을 우회한 최소 인스턴스. md 분기 호출부만 스텁으로 채운다."""
    dp = object.__new__(cls)
    dp._ext_aliases = aliases
    dp._md_cfg = {"processing_mode": "docling"}
    dp._log_level = 4
    dp.setup_logging = MagicMock()
    dp._intel = MagicMock()
    dp._intel._normalize_runtime_kwargs.side_effect = lambda kwargs: kwargs
    dp._markdown_front_matter_spec_for = MagicMock(return_value=None)
    dp._markdown_text_fence_spec_for = MagicMock(return_value=None)
    dp._apply_docling_post_enrichment = AsyncMock(side_effect=lambda doc, **kw: doc)
    dp._build_docling_response = MagicMock(return_value={"elements": []})
    dp._normalize_response = MagicMock(side_effect=lambda result: result)
    return dp


def _record_parse_docling(dp):
    """_parse_docling 호출 시점의 경로/내용/artifacts_from 을 기록한다.

    별칭 사본은 요청이 끝나면 지워지므로 호출 시점에 읽어 둬야 한다.
    """
    seen: dict = {}

    def _fake(file_path, artifacts_from=None, **kwargs):
        seen["path"] = file_path
        seen["content"] = Path(file_path).read_text(encoding="utf-8")
        seen["artifacts_from"] = artifacts_from
        return MagicMock(name="DoclingDocument")

    dp._parse_docling = MagicMock(side_effect=_fake)
    return seen


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parsed_extension_routes_to_markdown_branch(parser_processor, tmp_path: Path):
    src = tmp_path / "INC_235488_02_20260626103138.html.parsed"
    src.write_text(MIXED_MD_HTML, encoding="utf-8")

    dp = _stub_processor(parser_processor, {".parsed": ".md"})
    seen = _record_parse_docling(dp)

    await dp(MagicMock(), str(src))

    dp._parse_docling.assert_called_once()
    # docling 입력은 .md 이름의 사본이어야 한다(확장자로 포맷을 판정하므로).
    assert Path(seen["path"]).suffix == ".md"
    assert seen["path"] != str(src)
    assert seen["content"] == MIXED_MD_HTML
    # artifacts(이미지) 경로 기준은 임시 사본이 아니라 원본이어야 한다.
    assert seen["artifacts_from"] == str(src)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_alias_temp_copy_is_cleaned_up(parser_processor, tmp_path: Path):
    src = tmp_path / "sample.parsed"
    src.write_text(MIXED_MD_HTML, encoding="utf-8")

    dp = _stub_processor(parser_processor, {".parsed": ".md"})
    seen = _record_parse_docling(dp)

    await dp(MagicMock(), str(src))

    assert not Path(seen["path"]).exists()
    assert src.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plain_md_is_unchanged_by_alias_support(parser_processor, tmp_path: Path):
    """별칭이 걸리지 않는 입력은 사본 없이 원본 경로 그대로 파싱한다(기존 동작 보존)."""
    src = tmp_path / "sample.md"
    src.write_text(MIXED_MD_HTML, encoding="utf-8")

    dp = _stub_processor(parser_processor, {".parsed": ".md"})
    seen = _record_parse_docling(dp)

    await dp(MagicMock(), str(src))

    assert seen["path"] == str(src)
    assert seen["artifacts_from"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unaliased_unknown_extension_falls_back_to_catchall(parser_processor, tmp_path: Path):
    """별칭 설정이 없으면 기존 캐치올 경로 그대로다(회귀 방지)."""
    src = tmp_path / "sample.parsed"
    src.write_text(MIXED_MD_HTML, encoding="utf-8")

    dp = _stub_processor(parser_processor, {})
    dp._parse_docling = MagicMock()
    dp._parse_other = MagicMock(return_value=[])
    dp._langchain_to_parse_format = MagicMock(return_value={"elements": []})

    await dp(MagicMock(), str(src))

    dp._parse_docling.assert_not_called()
    dp._parse_other.assert_called_once()


# ---------------------------------------------------------------------------
# 3. 별칭이 없는 미지의 확장자 — 오프라인(unstructured 미설치) 내성
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_unknown_text_extension_uses_textloader_not_unstructured(tmp_path: Path):
    """설정에 별칭이 없어도 내용이 텍스트면 TextLoader 로 읽는다.

    unstructured 는 무거운 선택 의존이라 오프라인 배포본에는 없을 수 있다. 예전에는
    이 경로가 UnstructuredFileLoader 를 만들다 ImportError 로 죽었다.
    """
    from facade.parser_processor import GenericDocumentLoader, TextLoader

    src = tmp_path / "sample.parsed"
    src.write_text(MIXED_MD_HTML, encoding="utf-8")

    loader = GenericDocumentLoader().get_loader(str(src))

    assert isinstance(loader, TextLoader)


@pytest.mark.unit
def test_unknown_binary_extension_still_goes_to_unstructured(tmp_path: Path):
    """텍스트가 아니면 기존대로 Unstructured 로 보낸다(동작 보존)."""
    from facade.parser_processor import GenericDocumentLoader, TextLoader

    src = tmp_path / "sample.bin"
    src.write_bytes(b"\x00\x01\x02\x03" * 64)

    loader = GenericDocumentLoader().get_loader(str(src))

    assert not isinstance(loader, TextLoader)


@pytest.mark.unit
def test_missing_unstructured_becomes_actionable_error(tmp_path: Path, monkeypatch):
    """unstructured 미설치 ImportError 는 조치가 적힌 서비스 예외로 바뀐다."""
    from facade import parser_processor as pp

    src = tmp_path / "sample.bin"
    src.write_bytes(b"\x00\x01\x02\x03" * 64)

    generic = pp.GenericDocumentLoader()
    monkeypatch.setattr(
        generic, "get_loader",
        MagicMock(side_effect=ImportError("unstructured package not found")),
    )

    with pytest.raises(pp.GenosServiceException) as excinfo:
        generic.load_documents(str(src))

    assert "unstructured" in excinfo.value.error_msg
    assert "extension_aliases" in excinfo.value.error_msg
