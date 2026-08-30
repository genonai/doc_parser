from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
def test_parse_created_date():
    pass


@pytest.mark.unit
def test_safe_join():
    pass


# ─── _get_pdf_path ────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("ext", [".hwp", ".txt", ".json", ".md", ".ppt", ".pptx", ".docx"])
def test_get_pdf_path_returns_pdf_for_convertible_ext(ext):
    from facade.parser_processor import _get_pdf_path
    assert _get_pdf_path(f"/path/to/file{ext}") == "/path/to/file.pdf"


@pytest.mark.unit
def test_get_pdf_path_preserves_directory_structure():
    from facade.parser_processor import _get_pdf_path
    assert _get_pdf_path("/some/deep/dir/document.docx") == "/some/deep/dir/document.pdf"


@pytest.mark.unit
def test_get_pdf_path_pdf_input_is_unchanged():
    from facade.parser_processor import _get_pdf_path
    assert _get_pdf_path("/path/to/file.pdf") == "/path/to/file.pdf"


# ─── convert_to_pdf (subprocess argument verification) ───────────────────────
# soffice 를 실제로 부르는 곳은 backend 모듈이다. facade 의 convert_to_pdf 는
# facade/common/pdf_convert.py 를 거쳐 그 backend 로 위임하므로, subprocess 는
# 호출이 일어나는 모듈(converters.hwp_to_pdf.libreoffice)에서 가로채야 한다.
# 예전에는 facade.parser_processor.subprocess 를 patch 했고, 그 mock 을 유지하려고
# parser 만 backend 를 안 쓰고 soffice 를 직접 부르는 사본을 들고 있었다(#199).
_SOFFICE_RUN = "genon.preprocessor.converters.hwp_to_pdf.libreoffice.subprocess.run"

@pytest.mark.unit
@pytest.mark.parametrize("ext,expected_arg", [
    (".pptx", "pdf:impress_pdf_Export"),
    (".ppt",  "pdf:impress_pdf_Export"),
    (".docx", "pdf:writer_pdf_Export"),
    (".doc",  "pdf:writer_pdf_Export"),
    (".xlsx", "pdf:calc_pdf_Export"),
    (".xls",  "pdf:calc_pdf_Export"),
    (".csv",  "pdf:calc_pdf_Export"),
    (".txt",  "pdf"),
])
def test_convert_to_pdf_passes_correct_convert_arg(ext, expected_arg, tmp_path):
    from facade.parser_processor import convert_to_pdf

    in_file = tmp_path / f"test{ext}"
    in_file.write_bytes(b"fake content")
    (tmp_path / "test.pdf").write_bytes(b"fake pdf")  # pre-create so exists() is True

    with patch(_SOFFICE_RUN) as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        result = convert_to_pdf(str(in_file))

    assert result is not None
    called_cmd = mock_run.call_args[0][0]
    assert expected_arg in called_cmd


@pytest.mark.unit
def test_convert_to_pdf_returns_none_when_soffice_fails(tmp_path):
    from facade.parser_processor import convert_to_pdf

    in_file = tmp_path / "test.docx"
    in_file.write_bytes(b"fake content")

    with patch(_SOFFICE_RUN) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="conversion failed")
        result = convert_to_pdf(str(in_file))

    assert result is None
