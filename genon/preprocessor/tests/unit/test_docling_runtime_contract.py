"""DoclingRuntimeBase 가 facade 에 약속한 속성/메서드 계약.

이 파일이 존재하는 이유: parser 는 docling 런타임을 **합성**으로 쓴다
(self._intel = DoclingRuntimeBase(...)). 그래서 base 에서 속성이나 메서드가 하나
사라지면 parser 는 import 시점이 아니라 **요청 처리 중에** AttributeError 로 죽는다.
실제로 그런 사고가 있었다 — parser 의 임베드 사본이 원본을 따라가지 못해
check_empty_text 가 빠진 채로 남았고 크래시로 드러났다.

여기서 단정하는 것은 "값이 맞는가" 가 아니라 "이름이 있는가" 다.
"""

import pytest

from genon.preprocessor.facade.common.docling_runtime import DoclingRuntimeBase


@pytest.fixture(scope="module")
def runtime() -> DoclingRuntimeBase:
    """빈 설정으로 만든 런타임. 외부 호출은 없다(컨버터 생성은 지연 초기화)."""
    return DoclingRuntimeBase({}, config_path=None)


# parser 의 DocumentProcessor.__init__ 과 파싱 라우팅이 self._intel 에서 직접 읽는 속성.
# 하나라도 사라지면 parser 가 기동 또는 요청 처리 중에 죽는다.
PARSER_REQUIRED_ATTRS = (
    "_ext_aliases",
    "_md_cfg",
    "_xlsx_cfg",
    "_config_dir",
    "custom_fields_cfgs",
    "custom_fields_enrichers",
    "enrichment_options",
    "ocr_endpoint",
    "ocr_mode",
)

PARSER_REQUIRED_METHODS = (
    "load_documents",
    "load_documents_with_docling",
    "load_documents_with_docling_ocr",
    "check_glyphs",
    "check_empty_text",
    "ocr_all_table_cells",
    "enrich_doc_summary",
    "enrich_image_descriptions",
    "enrich_table_descriptions",
    "enrich_metadata",
    "enrich_custom_fields",
    "_normalize_runtime_kwargs",
    "_configure_runtime_image_mode",
    "_save_table_images",
)


@pytest.mark.unit
@pytest.mark.parametrize("name", PARSER_REQUIRED_ATTRS)
def test_base_exposes_attribute(runtime, name):
    assert hasattr(runtime, name), f"DoclingRuntimeBase 에 '{name}' 이 없다 — parser 가 읽는다"


@pytest.mark.unit
@pytest.mark.parametrize("name", PARSER_REQUIRED_METHODS)
def test_base_exposes_method(runtime, name):
    assert callable(getattr(runtime, name, None)), \
        f"DoclingRuntimeBase 에 '{name}()' 이 없다 — parser 가 호출한다"


@pytest.mark.unit
def test_pipeline_objects_built(runtime):
    """컨버터 4개와 파이프라인 옵션이 __init__ 에서 만들어진다."""
    for name in ("pipe_line_options", "ocr_pipe_line_options", "simple_pipeline_options",
                 "converter", "second_converter", "ocr_converter", "ocr_second_converter"):
        assert getattr(runtime, name, None) is not None, f"'{name}' 이 세워지지 않았다"


@pytest.mark.unit
def test_xlsx_default_mode_is_class_level():
    """xlsx 기본 모드가 서브클래스마다 다르다(parser=tabular, intelligent/convert=docling).

    ClassVar 로 갈리므로 base 를 고칠 때 이 축을 무심코 통일하지 않도록 고정한다.
    """
    assert DoclingRuntimeBase._xlsx_default_mode == "tabular"
    assert DoclingRuntimeBase({}, config_path=None)._xlsx_cfg["processing_mode"] == "tabular"

    class _DoclingDefault(DoclingRuntimeBase):
        _xlsx_default_mode = "docling"

    assert _DoclingDefault({}, config_path=None)._xlsx_cfg["processing_mode"] == "docling"


@pytest.mark.unit
def test_hooks_are_called_in_order():
    """_pre_pipeline_setup 은 파이프라인 옵션 생성 전에, _post_runtime_setup 은 컨버터 뒤에.

    순서가 뒤집히면 generate_page_images 강제가 OCR 컨버터 옵션에 반영되지 않는다.
    """
    seen = []

    class _Probe(DoclingRuntimeBase):
        def _pre_pipeline_setup(self, cfg):
            seen.append(("pre", hasattr(self, "pipe_line_options")))

        def _force_page_images(self):
            seen.append(("force", getattr(self, "converter", None) is not None))
            return True

        def _post_runtime_setup(self, cfg, ec):
            seen.append(("post", getattr(self, "converter", None) is not None))

    proc = _Probe({}, config_path=None)
    assert seen == [("pre", False), ("force", False), ("post", True)]
    assert proc.pipe_line_options.generate_page_images is True
    assert proc.ocr_pipe_line_options.generate_page_images is True
