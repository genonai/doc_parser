"""변환 PDF(뷰어용 아티팩트) 정책 — 위치와 보존 여부를 고정.

HWP/PPT/DOC/DOCX/이미지는 PDF 변환이 파싱에 필요하고, 그 산출물을 GenOS 문서 뷰어가
원본 옆에서 참조한다. 뷰잉을 쓰지 않는 현장에서는 원천 디렉터리에 계속 쌓이므로
남길지를 고르게 한다. 기본은 남기지 않는다.

실제 변환기(LibreOffice)는 부르지 않는다 — backend 가 `입력경로.with_suffix('.pdf')`
에 쓴다는 계약만 흉내 낸 가짜 변환기로 정책 자체를 검증한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from genon.preprocessor.processing.common import pdf_artifact as pa

pytestmark = pytest.mark.unit


def _fake_convert(input_path: str) -> str:
    """backend 3종과 같은 규칙: 입력 파일 옆에 같은 stem 의 .pdf 를 쓴다."""
    out = Path(input_path).with_suffix(".pdf")
    out.write_bytes(b"%PDF-1.7 fake")
    return str(out)


# ── 설정 해석 ───────────────────────────────────────────────────────────────

def test_defaults_do_not_keep_and_use_source_dir():
    """기본은 남기지 않고, 위치는 원본 파일과 같은 폴더다."""
    opts = pa.PdfArtifactOptions.from_config({})

    assert opts.keep is False
    assert opts.dir == ""


def test_kwargs_override_yaml():
    """요청 kwargs 가 yaml 을 덮어쓴다. None 이면 yaml 값을 그대로 쓴다."""
    opts = pa.PdfArtifactOptions.from_config({"keep": True, "dir": "/from/yaml"})

    assert opts.for_request(keep=None, dir=None).keep is True
    assert opts.for_request(keep=None, dir=None).dir == "/from/yaml"
    assert opts.for_request(keep=False, dir=None).keep is False
    assert opts.for_request(keep=None, dir="/from/kwargs").dir == "/from/kwargs"


# ── 보존 여부 ───────────────────────────────────────────────────────────────

def test_keep_false_removes_produced_pdf(tmp_path: Path):
    """기본(keep=False): 변환은 원본 옆에서 하되 요청이 끝나면 지운다."""
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK\x03\x04fake")
    policy = pa.PdfArtifactPolicy(keep=False)

    produced = policy.convert(str(src), _fake_convert)

    assert produced == str(tmp_path / "deck.pdf")
    assert Path(produced).exists()          # 파싱 중에는 존재해야 한다
    policy.cleanup()
    assert not Path(produced).exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["deck.pptx"]


def test_keep_true_leaves_pdf_for_viewer(tmp_path: Path):
    """keep=True: 뷰어가 참조하도록 원본 옆에 남긴다(현행 동작)."""
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK\x03\x04fake")
    policy = pa.PdfArtifactPolicy(keep=True)

    produced = policy.convert(str(src), _fake_convert)
    policy.cleanup()

    assert Path(produced).exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["deck.pdf", "deck.pptx"]


def test_preexisting_pdf_is_never_deleted(tmp_path: Path):
    """이미 있던 PDF 는 이 요청의 산출물이 아니므로 지우지 않는다.

    뷰어가 보던 파일을 요청 하나가 없애 버리는 사고를 막는 가드다.
    """
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK\x03\x04fake")
    sibling = tmp_path / "deck.pdf"
    sibling.write_bytes(b"%PDF-1.7 original")
    policy = pa.PdfArtifactPolicy(keep=False)

    policy.convert(str(src), _fake_convert)
    policy.cleanup()

    assert sibling.exists()


# ── 위치 ────────────────────────────────────────────────────────────────────

def test_dir_redirects_output_and_leaves_source_untouched(tmp_path: Path):
    """dir 을 주면 그 폴더에 만든다. 원본 폴더에는 아무것도 남지 않는다.

    backend 가 입력 옆에만 쓸 수 있으므로 입력 사본을 목적지에 두고 변환한다.
    그 사본은 아티팩트가 아니므로 변환 후 사라져야 한다.
    """
    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    out_dir = tmp_path / "artifacts"
    src = source_dir / "deck.pptx"
    src.write_bytes(b"PK\x03\x04fake")
    policy = pa.PdfArtifactPolicy(keep=True, dir=str(out_dir))

    produced = policy.convert(str(src), _fake_convert)

    assert produced == str(out_dir / "deck.pdf")
    assert sorted(p.name for p in source_dir.iterdir()) == ["deck.pptx"]
    assert sorted(p.name for p in out_dir.iterdir()) == ["deck.pdf"]


def test_dir_with_keep_false_cleans_up(tmp_path: Path):
    """지정한 폴더를 임시 저장소로 쓰는 조합 — 요청이 끝나면 비워진다."""
    src = tmp_path / "doc.hwp"
    src.write_bytes(b"\xd0\xcf\x11\xe0fake")
    out_dir = tmp_path / "scratch"
    policy = pa.PdfArtifactPolicy(keep=False, dir=str(out_dir))

    produced = policy.convert(str(src), _fake_convert)
    assert Path(produced).exists()

    policy.cleanup()
    assert list(out_dir.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.hwp", "scratch"]


def test_expected_pdf_path_follows_dir(tmp_path: Path):
    """변환 실패 시 기존 산출을 찾을 자리도 정책을 따라야 한다."""
    src = str(tmp_path / "raw" / "deck.pptx")

    assert pa.PdfArtifactPolicy().expected_pdf_path(src) == str(tmp_path / "raw" / "deck.pdf")
    assert pa.PdfArtifactPolicy(dir="/out").expected_pdf_path(src) == "/out/deck.pdf"


def test_failed_conversion_records_nothing(tmp_path: Path):
    """변환기가 None 을 돌려주면 정리 대상도 없다."""
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK\x03\x04fake")
    policy = pa.PdfArtifactPolicy(keep=False, dir=str(tmp_path / "out"))

    assert policy.convert(str(src), lambda _p: None) is None
    assert policy.produced == []
    # 입력 사본은 남기지 않는다.
    assert list((tmp_path / "out").iterdir()) == []


# ── parser 배선 ─────────────────────────────────────────────────────────────
#
# 위 테스트는 정책 객체만 본다. 여기서는 run() 이 요청 스코프 정책을 만들어
# 변환 지점(get_loader)까지 내려보내고 finally 에서 정리하는지를 본다.
# 실제 LibreOffice 는 부르지 않는다 — backend 계약만 흉내 낸다.

@pytest.fixture
def stub_parser(monkeypatch):
    """__init__ 을 우회한 최소 parser 인스턴스 + 가짜 변환기."""
    from unittest.mock import MagicMock

    import genon.preprocessor.processing.core.parser as core
    from facade.parser_processor import DocumentProcessor

    monkeypatch.setattr(core.pc, "convert_to_pdf",
                        lambda path, **_kw: _fake_convert(path))
    monkeypatch.setattr(core, "UnstructuredPowerPointLoader",
                        lambda path: MagicMock(load=MagicMock(return_value=[])),
                        raising=False)

    def _make(yaml_cfg: dict | None = None):
        dp = object.__new__(DocumentProcessor)
        dp._ext_aliases = {}
        dp._log_level = 4
        dp.setup_logging = MagicMock()
        dp._intel = MagicMock()
        dp._intel._normalize_runtime_kwargs.side_effect = lambda kw: kw
        dp._pdf_output = pa.PdfArtifactOptions.from_config(yaml_cfg or {})
        dp._generic = core.GenericDocumentLoader()
        dp._normalize_response = MagicMock(side_effect=lambda result: result)
        dp._langchain_to_parse_format = MagicMock(return_value={"elements": []})
        # PPT 는 docling 경로가 먼저다. 그걸 막아 미리보기 변환이 있는 langchain 폴백을 태운다.
        dp._parse_ppt_docling = MagicMock(return_value=None)
        dp._parse_other = lambda fp, **kw: dp._generic.load_documents(fp, **kw)
        return dp

    return _make


async def _run_pptx(dp, tmp_path: Path, **kwargs) -> list[str]:
    from unittest.mock import MagicMock

    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK\x03\x04fake")
    await dp.run(MagicMock(), str(src), **kwargs)
    return sorted(p.name for p in tmp_path.iterdir())


@pytest.mark.asyncio
async def test_run_default_leaves_no_pdf(stub_parser, tmp_path: Path):
    assert await _run_pptx(stub_parser(), tmp_path) == ["deck.pptx"]


@pytest.mark.asyncio
async def test_run_yaml_keep_leaves_pdf(stub_parser, tmp_path: Path):
    dp = stub_parser({"keep": True})
    assert await _run_pptx(dp, tmp_path) == ["deck.pdf", "deck.pptx"]


@pytest.mark.asyncio
async def test_run_kwargs_keep_pdf_overrides_yaml(stub_parser, tmp_path: Path):
    """kwargs 가 yaml 을 이긴다 — 양방향 모두."""
    assert await _run_pptx(stub_parser(), tmp_path, keep_pdf=1) == ["deck.pdf", "deck.pptx"]

    other = tmp_path / "off"
    other.mkdir()
    dp = stub_parser({"keep": True})
    assert await _run_pptx(dp, other, keep_pdf=0) == ["deck.pptx"]


@pytest.mark.asyncio
async def test_run_pdf_dir_keeps_source_dir_clean(stub_parser, tmp_path: Path):
    from unittest.mock import MagicMock

    source_dir = tmp_path / "raw"
    source_dir.mkdir()
    out_dir = tmp_path / "artifacts"
    src = source_dir / "deck.pptx"
    src.write_bytes(b"PK\x03\x04fake")

    await stub_parser().run(MagicMock(), str(src), keep_pdf=1, pdf_dir=str(out_dir))

    assert sorted(p.name for p in source_dir.iterdir()) == ["deck.pptx"]
    assert sorted(p.name for p in out_dir.iterdir()) == ["deck.pdf"]
