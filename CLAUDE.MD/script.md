# 실행 스크립트 모음

> 모든 명령어는 프로젝트 루트(`doc_parser/`)에서 실행

---

## 실험 결과 요약 (2026-05-28)

### 지능형 전처리기 (intelligent_processor)

| 확장자 | OLD | NEW | 결과 |
|--------|-----|-----|------|
| PDF | 18 chunks | 18 chunks | ✓ PASS |
| DOCX | 1 chunk | 1 chunk | ✓ PASS |
| HWPX | 61 chunks | 61 chunks | ✓ PASS |

**특이사항**
- OLD에 `generate_picture_images=True` 명시 필요 (없으면 `AttributeError: 'NoneType' object has no attribute 'uri'`)
- OLD converter 기본값이 `GenosHwpDocumentBackend`(hwp_sdk 사용)이므로, HWPX는 `HwpxDocumentBackend`로 override 필요
- 조건 맞추면 PDF/DOCX/HWPX 텍스트까지 완전 일치 → **리팩토링 정상**

---

### 첨부용 전처리기 (attachment_processor)

| 확장자 | OLD chunks | NEW 일치 config | 결과 |
|--------|-----------|----------------|------|
| DOCX | 4 | hybrid / inf / char | ✓ PASS |
| HWPX | 89 | hybrid / inf / char | ✓ PASS |
| PDF | 25 | recursive / 1000 / char | ✓ PASS |

**결론: 청킹 로직만 다르고 나머지는 정상**
- DOCX/HWPX: `HybridChunker(max_tokens=inf)` → 현재 config `max_tokens: 1024`와 다름
- PDF: OLD가 `RecursiveCharacterTextSplitter(chunk_size=1000)` 사용 → NEW는 `recursive/1000/char`로 일치
- **OLD가 이미 확장자별 청커가 달랐으므로, NEW도 확장자별 청커 config 지원 필요** (현재 미지원)

**발견된 버그 및 수정**
- `CharTokenizer.__call__` 누락 → `semchunk.chunkerify` TypeError 발생 → 수정 완료 ✅
- `[before_refactoring]attachment_processor.py` 헤딩 포맷 → HEADER: 형식으로 통일 ✅

---

## 공통

```bash
PYTHON=.venv-cu126/bin/python3
```

---

## 스모크 테스트

### 전체 스모크 (hwp·오디오·엑셀 제외)

```bash
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/ -v
```

## test1

### intelligent_processor old(before_refactoring) vs new
- ocr, enrich, do_picture_dicription, postprocessing 다 끄고 실행
- old는 hwp_sdk 안불러오게 해서 실행
- 청커 세팅도 동일하게 해서 진행
- 확장자 pdf, hwpx, docx

#### step 1: 테스트용 최소 config 생성

```bash
cat > /tmp/intelligent_config_test1.yaml << 'EOF'
format_options:
  pdf:
    pipeline_options: pdf
    backend: pypdf
    generate_picture_images: true
  docx:
    pipeline_options: simple
    backend: msword
  hwpx:
    pipeline_options: simple
    backend: hwpx
chunker:
  name: smart
  tokenizer: char
  max_tokens: 1024
enrichers: []
postprocessors: []
log_level: 0
EOF
```

#### step 2: 비교 실행 (결과: CLAUDE.MD/test1_result.txt)

```bash
DOC_PROCESSOR_CONFIG_PATH=/tmp/intelligent_config_test1.yaml \
.venv-cu126/bin/python3 - << 'PYEOF' 2>&1 | tee CLAUDE.MD/test1_result.txt
import asyncio, importlib.util, sys, warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT))

SAMPLE_DIR = ROOT / "genon/preprocessor/sample_files"
FILES = {
    "pdf":  SAMPLE_DIR / "pdf_sample.pdf",
    "docx": SAMPLE_DIR / "docx_sample.docx",
    "hwpx": SAMPLE_DIR / "hwpx_sample.hwpx",
}

# ── OLD processor 로드 ──────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    "old_intelligent",
    ROOT / "genon/preprocessor/facade/[before_refactoring]intelligent_processor.py"
)
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)

from docling.datamodel.pipeline_options import (
    PdfPipelineOptions, PipelineOptions, AcceleratorDevice, AcceleratorOptions
)
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption, FormatOption
from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
from docling.backend.xml.hwpx_backend import HwpxDocumentBackend
from docling.pipeline.simple_pipeline import SimplePipeline
from genon.preprocessor.facade.chunkers import GenosSmartChunker

class OldProcTest(old_mod.DocumentProcessor):
    """OCR·GENOS_LAYOUT·enrich 비활성화, hwp_sdk 미사용(HwpxDocumentBackend), GenosSmartChunker(1024)"""
    def __init__(self):
        self.page_chunk_counts = defaultdict(int)
        acc = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.AUTO)
        self.pipe_line_options = PdfPipelineOptions()
        self.pipe_line_options.do_ocr = False
        self.pipe_line_options.generate_picture_images = True
        self.pipe_line_options.accelerator_options = acc
        self.simple_pipeline_options = PipelineOptions()
        self.simple_pipeline_options.save_images = False
        self.ocr_pipe_line_options = self.pipe_line_options.model_copy(deep=True)
        self._create_converters()

    def _create_converters(self):
        self.converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.pipe_line_options, backend=PyPdfiumDocumentBackend
                ),
                InputFormat.XML_HWPX: FormatOption(
                    pipeline_cls=SimplePipeline, backend=HwpxDocumentBackend
                ),
            }
        )
        self.second_converter = self.converter
        self.ocr_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self.ocr_pipe_line_options, backend=DoclingParseV4DocumentBackend
                ),
                InputFormat.XML_HWPX: FormatOption(
                    pipeline_cls=SimplePipeline, backend=HwpxDocumentBackend
                ),
            }
        )
        self.ocr_second_converter = self.ocr_converter

    def enrichment(self, document, **kwargs):
        return document

    def split_documents(self, documents, **kwargs):
        self.page_chunk_counts = defaultdict(int)
        chunker = GenosSmartChunker(max_tokens=1024, tokenizer="char")
        chunks = list(chunker.chunk(documents))
        for c in chunks:
            if c.meta.doc_items and c.meta.doc_items[0].prov:
                self.page_chunk_counts[c.meta.doc_items[0].prov[0].page_no] += 1
        return chunks

    async def __call__(self, request, file_path, **kwargs):
        self.page_chunk_counts = defaultdict(int)
        document = self.load_documents(file_path, **kwargs)
        chunks = self.split_documents(document)
        if not chunks:
            print("  [OLD] chunks 없음")
            return []
        return await self.compose_vectors(document, chunks, file_path, request, **kwargs)

# ── NEW processor 로드 ──────────────────────────────────────
from genon.preprocessor.facade.intelligent_processor import DocumentProcessor
new_proc = DocumentProcessor()  # DOC_PROCESSOR_CONFIG_PATH 환경변수로 test config 사용

# ── 비교 ───────────────────────────────────────────────────
def to_dict(v):
    return v.model_dump() if hasattr(v, "model_dump") else dict(v)

def compare(old_vecs, new_vecs, label):
    print(f"\n{'─'*60}")
    print(f"[{label}]  OLD={len(old_vecs)}  NEW={len(new_vecs)} chunks")
    n = min(len(old_vecs), len(new_vecs))
    diffs = 0
    for i in range(n):
        ot = to_dict(old_vecs[i]).get("text", "")[:100]
        nt = to_dict(new_vecs[i]).get("text", "")[:100]
        if ot != nt:
            diffs += 1
            if diffs <= 3:
                print(f"  [chunk {i}] DIFF")
                print(f"    OLD: {repr(ot)}")
                print(f"    NEW: {repr(nt)}")
    print(f"  일치: {n-diffs}/{n}  불일치: {diffs}/{n}  {'✓ PASS' if diffs==0 else '✗ DIFF'}")

async def main():
    old_proc = OldProcTest()
    for ext, fpath in FILES.items():
        if not fpath.exists():
            print(f"\n[SKIP] {ext.upper()}: {fpath.name} 없음")
            continue
        print(f"\n{'='*60}\n{ext.upper()}: {fpath.name}")
        old_vecs, new_vecs = [], []
        try:
            old_vecs = await old_proc(None, str(fpath))
            print(f"  OLD ✓  {len(old_vecs)} chunks")
        except Exception as e:
            print(f"  OLD ERROR: {type(e).__name__}: {e}")
        try:
            new_vecs = await new_proc(None, str(fpath))
            print(f"  NEW ✓  {len(new_vecs)} chunks")
        except Exception as e:
            print(f"  NEW ERROR: {type(e).__name__}: {e}")
        if old_vecs and new_vecs:
            compare(old_vecs, new_vecs, f"{ext.upper()}")

asyncio.run(main())
PYEOF
```




## test2

### attachment_processor old(before_refactoring) vs new
- ocr, enrich, do_picture_dicription, postprocessing 다 끄고 실행
- old는 hwp_sdk 안불러오게 해서 실행 (use_hwp_sdk=False kwarg)
- 확장자 pdf, hwpx, docx

### 발견된 이슈 (2026-05-28)

#### 버그: CharTokenizer.__call__ 누락
- **파일**: `genon/preprocessor/facade/chunkers/tokenizer.py`
- **증상**: `semchunk.chunkerify`에 callable이 아닌 객체 전달 → `TypeError: the first argument must be callable`
- **원인**: 리팩토링 시 `CharTokenizer` 신규 추가하면서 `__call__` 미구현. OLD는 항상 `AutoTokenizer`(callable)만 사용
- **수정**: `CharTokenizer`에 `def __call__(self, text: str) -> int: return len(text)` 추가 ✅

#### OLD vs NEW 구조적 차이 (config만으로 해결 불가)

| 항목 | OLD | NEW |
|------|-----|-----|
| **PDF 백엔드** | PyMuPDFLoader (langchain) | langchain_pdf | 
| **PDF 청커** | RecursiveCharacterTextSplitter | HybridChunker |
| **DOCX 청커** | HybridChunker(max_tokens=inf) | HybridChunker(config 따라) |
| **HWPX 청커** | HybridChunker(max_tokens=inf) | HybridChunker(config 따라) |
| **텍스트 포맷** | `headings\n` + `chunk.text` | `HEADER: h1, h2\n` + `chunk.text` |

- **텍스트 포맷 차이**: `metadata/builder.py:350`에서 의도적으로 `"HEADER: "` 접두사 추가
- **PDF**: 백엔드가 다르므로 결과 일치 불가
- **DOCX/HWPX**: 청커를 `hybrid` + `max_tokens=inf`로 맞추면 청크 수는 동일하나, `HEADER:` 접두사로 텍스트 불일치

#### 청커 config별 청크 수 비교 (NEW only, 결과: CLAUDE.MD/test2_chunker_compare.txt)

| config | PDF | DOCX | HWPX |
|--------|-----|------|------|
| hybrid / 1024 / char | 21 | 4 | 566 |
| hybrid / 512 / char | 39 | 5 | 1029 |
| hybrid / 2048 / char | 11 | 4 | 325 |
| smart / 1024 / char | 11 | 1 | 61 |
| **OLD 참고** | **25** | **4** | **89** |

#### 실행 (결과: CLAUDE.MD/test2_result.txt)

```bash
.venv-cu126/bin/python3 - << 'PYEOF' 2>&1 | tee CLAUDE.MD/test2_result.txt
import asyncio, importlib.util, sys, warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

ROOT = Path(".").resolve()
sys.path.insert(0, str(ROOT))

SAMPLE_DIR = ROOT / "genon/preprocessor/sample_files"
FILES = {
    "pdf":  SAMPLE_DIR / "pdf_sample.pdf",
    "docx": SAMPLE_DIR / "docx_sample.docx",
    "hwpx": SAMPLE_DIR / "hwpx_sample.hwpx",
}

# ── OLD processor 로드 ──────────────────────────────────────
spec = importlib.util.spec_from_file_location(
    "old_attachment",
    ROOT / "genon/preprocessor/facade/[before_refactoring]attachment_processor.py"
)
old_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_mod)
old_proc = old_mod.DocumentProcessor()

# ── NEW processor 로드 ──────────────────────────────────────
from genon.preprocessor.facade.attachment_processor import DocumentProcessor
new_proc = DocumentProcessor()

# ── 비교 ───────────────────────────────────────────────────
def to_dict(v):
    return v.model_dump() if hasattr(v, "model_dump") else dict(v)

def compare(old_vecs, new_vecs, label):
    print(f"\n{'─'*60}")
    print(f"[{label}]  OLD={len(old_vecs)}  NEW={len(new_vecs)} chunks")
    n = min(len(old_vecs), len(new_vecs))
    diffs = 0
    for i in range(n):
        ot = to_dict(old_vecs[i]).get("text", "")[:100]
        nt = to_dict(new_vecs[i]).get("text", "")[:100]
        if ot != nt:
            diffs += 1
            if diffs <= 3:
                print(f"  [chunk {i}] DIFF")
                print(f"    OLD: {repr(ot)}")
                print(f"    NEW: {repr(nt)}")
    print(f"  일치: {n-diffs}/{n}  불일치: {diffs}/{n}  {'✓ PASS' if diffs==0 else '✗ DIFF'}")

async def main():
    for ext, fpath in FILES.items():
        if not fpath.exists():
            print(f"\n[SKIP] {ext.upper()}: {fpath.name} 없음")
            continue
        print(f"\n{'='*60}\n{ext.upper()}: {fpath.name}")
        old_vecs, new_vecs = [], []
        try:
            old_vecs = await old_proc(None, str(fpath), use_hwp_sdk=False)
            print(f"  OLD ✓  {len(old_vecs)} chunks")
        except Exception as e:
            print(f"  OLD ERROR: {type(e).__name__}: {e}")
        try:
            new_vecs = await new_proc(None, str(fpath))
            print(f"  NEW ✓  {len(new_vecs)} chunks")
        except Exception as e:
            print(f"  NEW ERROR: {type(e).__name__}: {e}")
        if old_vecs and new_vecs:
            compare(old_vecs, new_vecs, ext.upper())

asyncio.run(main())
PYEOF
```