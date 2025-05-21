import json
import logging
from pathlib import Path

import yaml

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.document_converter import (
    DocumentConverter,
    PdfFormatOption,
    WordFormatOption,
)
from docling.pipeline.simple_pipeline import SimplePipeline
from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline

_log = logging.getLogger(__name__)
from docling.document_converter import DocumentConverter

def main():
    input_paths = [
        # Path("README.md"),
        # Path("tests/data/html/wiki_duck.html"),
        # Path("/workspaces/doc_parser/tests/data/docx/word_sample.docx"),
        # Path("/workspaces/doc_parser/tests/data/docx/lorem_ipsum.docx"),
        # Path("/workspaces/doc_parser/tests/data/docx/word_tables.docx"),
        # Path("tests/data/pptx/powerpoint_sample.pptx"),
        # Path("tests/data/2305.03393v1-pg9-img.png"),
        # Path("tests/data/pdf/2206.01062.pdf"),
        # Path("tests/data/asciidoc/test_01.asciidoc"),
        # Path("/workspaces/hwpx/2021.01.29_주간 글로벌 펀드자금 유출입 동향(20210121-20210127)_210129_주간 글로벌 펀드자금 유출입 동향(210121~210127)_F.hwpx"),
        Path("/workspaces/hwpx/2016.11.21_주중 금융시장 동향_주중_금융시장동향(20161121)_MI.hwpx"),
        # Path("/workspaces/hwpx/주중금융시장자른버전.hwpx"),
        # Path("/workspaces/hwpx/금융동향시장자른버전.hwpx"),
        Path("/workspaces/hwpx/201810_금융시장동향_FF.hwpx"),
        # Path("/workspaces/hwpx/참고1_주중.hwpx"),
        # Path("/workspaces/hwpx/금융시장동향_최근외국인.hwpx"),
        # Path("/workspaces/hwpx/금융시장동향_테이블.hwpx"),
        Path("/workspaces/hwpx/(1810) 통화정책방향 여건점검 메모_F.hwpx"),
        # Path("/workspaces/hwpx/금시동_여백.hwpx"),
        # Path("/workspaces/myenv/hwp/(1810) 통화정책방향 여건점검 메모_F.md")
    ]

    ## for defaults use:
    # doc_converter = DocumentConverter()

    ## to customize use:

    doc_converter = (
        DocumentConverter(  # all of the below is optional, has internal defaults.
            allowed_formats=[
                InputFormat.PDF,
                InputFormat.IMAGE,
                InputFormat.DOCX,
                InputFormat.HTML,
                InputFormat.PPTX,
                InputFormat.ASCIIDOC,
                InputFormat.CSV,
                InputFormat.MD,
                InputFormat.XML_HWPX
            ],  # whitelist formats, non-matching files are ignored.
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_cls=StandardPdfPipeline, backend=PyPdfiumDocumentBackend
                ),
                InputFormat.DOCX: WordFormatOption(
                    pipeline_cls=SimplePipeline  # , backend=MsWordDocumentBackend
                ),
            },
        )
    )

    conv_results = doc_converter.convert_all(input_paths)

    for res in conv_results:
        out_path = Path("/workspaces/doc_parser/scratch")
        print(
            f"Document {res.input.file.name} converted."
            f"\nSaved markdown output to: {out_path!s}"
        )
        _log.debug(res.document._export_to_indented_text(max_text_len=16))

        # Markdown (변경 없음)
        with (out_path / f"{res.input.file.stem}.md").open("w", encoding="utf-8") as fp:
            fp.write(res.document.export_to_markdown())

        # JSON: ensure_ascii=False 로 한글 출력
        with (out_path / f"{res.input.file.stem}.json").open("w", encoding="utf-8") as fp:
            fp.write(
                json.dumps(
                    res.document.export_to_dict(),
                    ensure_ascii=False,
                    indent=2,
                )
            )

        # YAML: allow_unicode=True 로 한글 출력
        with (out_path / f"{res.input.file.stem}.yaml").open("w", encoding="utf-8") as fp:
            fp.write(
                yaml.safe_dump(
                    res.document.export_to_dict(),
                    allow_unicode=True,
                    sort_keys=False,
                    indent=2,
                )
            )
if __name__ == "__main__":
    main()