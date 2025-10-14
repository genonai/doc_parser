import json
import logging
from pathlib import Path

import yaml

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.backend.genos_msword_backend import GenosMsWordDocumentBackend
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
    # /workspaces/삼증리서치_리포트반출_일부 아래의 모든 DOCX 파일을 입력으로 사용
    # input_paths = sorted(Path("/workspaces/삼증리서치_리포트반출_일부").rglob("*.docx"))
    input_paths = [Path("/workspaces/jayoo/test_doc/docx/롯데손해보험 데이터경영팀 MLOps 운영자 매뉴얼.docx")]

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
                InputFormat.HWP,
                InputFormat.XML_HWPX
            ],  # whitelist formats, non-matching files are ignored.
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_cls=StandardPdfPipeline, backend=PyPdfiumDocumentBackend
                ),
                InputFormat.DOCX: WordFormatOption(
                    pipeline_cls=SimplePipeline, backend=GenosMsWordDocumentBackend  # , backend=MsWordDocumentBackend 
                ),
            },
        )
    )

    conv_results = doc_converter.convert_all(input_paths)

    for res in conv_results:
        out_path = Path("/workspaces/docx_header/doc_parser/scratch")
        out_path.mkdir(parents=True, exist_ok=True)
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
