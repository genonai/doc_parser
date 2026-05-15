# 실행 스크립트 모음

> 모든 명령어는 프로젝트 루트(`doc_parser/`)에서 실행

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

### 확장자별

```bash
# PDF
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_pdf_smoke.py -v

# DOCX
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_docx_smoke.py -v

# PPTX
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_pptx_smoke.py -v

# Markdown
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_md_smoke.py -v

# HWPX
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_hwpx_smoke.py -v

# HWP (제외 권장)
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_hwp_smoke.py -v
```

---

## 유닛 테스트

```bash
# 전체 유닛
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/unit/ -v

# intelligent_processor 유닛
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/unit/test_intelligent_processor_unit.py -v
```

---

## 리그레션 테스트

```bash
# 전체 리그레션
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/regression/ -v

# 베이스라인 업데이트 (변경 시에만)
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/regression/ -m update_baseline -v
```

---

## intelligent_processor EasyOCR 비교 테스트

> OLD(GenosBucketChunker) vs NEW(GenosSmartChunker) — EasyOCR CPU 모드, pdf_sample.pdf 기준  
> 결과: `genon/preprocessor/tests/intelligent_ocr_compare_result.md`

```bash
.venv-cu126/bin/python3 - << 'EOF'
import sys, warnings
from pathlib import Path
from collections import defaultdict
from datetime import datetime

_ROOT = Path(".").resolve()
_FACADE = _ROOT / "genon/preprocessor/facade"
sys.path.insert(0, str(_FACADE))
sys.path.insert(0, str(_ROOT))

SAMPLE_PDF = _ROOT / "genon/preprocessor/sample_files/pdf_sample.pdf"

def build_old_processor():
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from intelligent_processor import DocumentProcessor
    from docling.datamodel.pipeline_options import EasyOcrOptions
    dp = DocumentProcessor()
    dp.ocr_pipe_line_options.ocr_options = EasyOcrOptions(force_full_page_ocr=True, use_gpu=False)
    dp._create_converters()
    return dp

_CPU_CONFIG = {
    "format_options": {
        "pdf": {
            "pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": True,
            "do_ocr": {"Easy": {"force_full_page_ocr": True, "use_gpu": False}},
        },
        "docx": {"pipeline_options": "simple", "backend": "msword"},
        "md":   {"pipeline_options": "simple", "backend": "md"},
        "html": {"pipeline_options": "simple", "backend": "html"},
        "hwpx": {"pipeline_options": "simple", "backend": "hwpx"},
        "pptx": {"pipeline_options": "simple", "backend": "mspowerpoint"},
    },
    "chunker": {"name": "smart", "max_tokens": 1024},
    "return_level": "chunk", "log_level": 4,
}

def build_new_processor():
    from test_intelligent_processor import DocumentProcessor
    return DocumentProcessor(config=_CPU_CONFIG)

def chunk_counts_per_page(chunks):
    counts = defaultdict(int)
    for chunk in chunks:
        for item in chunk.meta.doc_items:
            if item.prov:
                counts[item.prov[0].page_no] += 1
                break
    return dict(sorted(counts.items()))

pdf_path = str(SAMPLE_PDF)
old_dp = build_old_processor()
new_dp = build_new_processor()

old_doc = old_dp.load_documents_with_docling_ocr(pdf_path)
old_chunks = old_dp.split_documents(old_doc)
new_doc = new_dp.load_documents(pdf_path)
new_chunks = new_dp.split_documents(new_doc, file_path=pdf_path)

old_pc = chunk_counts_per_page(old_chunks)
new_pc = chunk_counts_per_page(new_chunks)
all_pages = sorted(set(old_pc) | set(new_pc))

rows, passed = [], 0
for p in all_pages:
    o, n = old_pc.get(p, 0), new_pc.get(p, 0)
    match = o == n
    if match: passed += 1
    rows.append({"page": p, "old": o, "new": n, "match": match})

total = len(rows)
icon = "✅ 일치" if passed == total else "❌ 불일치"

lines = [
    "# EasyOCR 비교 테스트 결과 — intelligent_processor", "",
    f"**날짜**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
    f"**파일**: `{SAMPLE_PDF.name}`  ",
    f"**결과**: {passed}/{total} 페이지 청크 수 일치 — {icon}", "",
    "## 페이지별 청크 수 비교", "",
    "| 페이지 | OLD (GenosBucketChunker) | NEW (GenosSmartChunker) | 일치 |",
    "|:------:|:------------------------:|:-----------------------:|:----:|",
]
for r in rows:
    lines.append(f"| {r['page']} | {r['old']} | {r['new']} | {'✅' if r['match'] else '❌'} |")
lines += [
    "", "## 요약", "",
    "| 항목 | OLD | NEW |", "|------|-----|-----|",
    f"| 총 청크 수 | {len(old_chunks)} | {len(new_chunks)} |",
    f"| 페이지 수 | {old_doc.num_pages()} | {new_doc.num_pages()} |", "",
    f"- 페이지별 청크 수 일치: **{passed}/{total}**",
    f"- 종합: **{icon}**",
]
md = "\n".join(lines)
print(md)
out = _ROOT / "genon/preprocessor/tests/intelligent_ocr_compare_result.md"
out.write_text(md, encoding="utf-8")
print(f"\n결과 저장: {out}")
EOF
```

