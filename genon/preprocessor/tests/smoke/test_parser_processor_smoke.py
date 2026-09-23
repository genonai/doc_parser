"""
Smoke tests for facade/parser_processor.py DocumentProcessor.

Calls DocumentProcessor.__call__ with real sample files and validates
the output schema. Each parametrized case is skipped when no matching
sample files are found in sample_files/.
"""
import os
import shutil
from pathlib import Path

import pytest
import yaml

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "sample_files"
REQUIRED_KEYS = {"elements", "usage"}
ELEMENT_KEYS = {"id", "page", "category", "content", "coordinates"}


def _samples(ext: str) -> list[Path]:
    if not SAMPLE_DIR.exists():
        return []
    return sorted(SAMPLE_DIR.rglob(f"*{ext}"))


def _validate_result(result: dict) -> None:
    assert isinstance(result, dict)
    for key in REQUIRED_KEYS:
        assert key in result, f"result missing key: {key!r}"
    assert isinstance(result["elements"], list), "elements must be a list"
    assert isinstance(result["usage"]["pages"], int), "usage.pages must be int"
    assert result["usage"]["pages"] >= 1
    for element in result["elements"]:
        for key in ELEMENT_KEYS:
            assert key in element, f"element missing key: {key!r}"


@pytest.fixture(scope="module")
def dp(parser_processor, tmp_path_factory):
    """기본 설정에서 표 설명 LLM 호출(table_text_description)만 끈 파서.

    스모크는 출력 스키마만 본다. 기본 설정 그대로면 표가 있는 샘플마다 실제 LLM 게이트웨이를
    호출해 CI 시간의 대부분이 응답 대기가 된다(표 71개 샘플 한 건이 약 100초). LLM 경로는
    examples/parse_chunk/parse_chunk_verify.sh 가 검증한다. 설정이 프롬프트·custom_fields
    파일을 상대 경로로 읽으므로 설정 디렉터리째 복사한 뒤 사본만 고친다.
    """
    from genon.preprocessor.processing.core.parser import _resolve_default_parser_config_path

    source = Path(_resolve_default_parser_config_path())
    config_dir = tmp_path_factory.mktemp("parser_config")
    shutil.copytree(source.parent, config_dir, dirs_exist_ok=True)
    cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
    for block in cfg.get("enrichment") or []:
        if isinstance(block, dict) and isinstance(block.get("table_text_description"), dict):
            block["table_text_description"]["enable"] = False
    config_path = config_dir / source.name
    config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return parser_processor(config_path=str(config_path))


# ─── DOCX ─────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".docx"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_docx_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)
    for element in result["elements"]:
        assert element["coordinates"] == [], "docx elements must have empty coordinates"


# ─── HWP ──────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".hwp"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_hwp_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)


# ─── HWPX ─────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".hwpx"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_hwpx_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)


# ─── CSV ──────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".csv"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_csv_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)
    assert all(e["category"] == "tabular_row" for e in result["elements"])


# ─── XLSX ─────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".xlsx"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_xlsx_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)
    assert all(e["category"] == "tabular_row" for e in result["elements"])


# ─── PDF ──────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.skipif(
    not os.environ.get("GENOS_LAYOUT_AVAILABLE"),
    reason="GENOS_LAYOUT_AVAILABLE not set; skipping PDF smoke test requiring internal layout endpoint",
)
@pytest.mark.parametrize("sample", _samples(".pdf"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_pdf_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)


# ─── Markdown ─────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".md"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_md_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)


# ─── HTML ─────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".html"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_html_smoke(dp, sample):
    """docling HTML 경로. md 가 raw HTML 블록 때문에 이 경로로 위임됐을 때 usage.pages 가
    0 으로 나가던 버그를 여기서도 잡는다(HTML 백엔드는 doc.pages 를 채우지 않는다)."""
    result = await dp(None, str(sample))
    _validate_result(result)


# ─── PPTX ─────────────────────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.parametrize("sample", _samples(".pptx"), ids=lambda p: p.name)
@pytest.mark.asyncio
async def test_pptx_smoke(dp, sample):
    result = await dp(None, str(sample))
    _validate_result(result)
