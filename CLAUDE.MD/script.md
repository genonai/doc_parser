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

def build_new_processor():
    from test_intelligent_processor import DocumentProcessor
    return DocumentProcessor()

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
old_chunks = old_dp.split_documents(old_doc, max_chunk_size=1024)  # NEW(max_tokens=1024)와 공정 비교
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

---

## intelligent_processor 이미지 디스크립션 비교 테스트

> OLD(intelligent_processor + PictureDescriptionApiOptions) vs NEW(test_intelligent_processor + ImageDescriptionEnricher)  
> 동일 조건: OpenRouter, google/gemini-2.0-flash-001, picture_area_threshold=0.001  
> 결과: `genon/preprocessor/tests/image_description_compare_result.md`

```bash
.venv-cu126/bin/python3 - << 'EOF'
import sys, warnings, asyncio
from pathlib import Path
from datetime import datetime
from docling_core.types.doc import PictureItem

_ROOT = Path(".").resolve()
_FACADE = _ROOT / "genon/preprocessor/facade"
sys.path.insert(0, str(_FACADE))
sys.path.insert(0, str(_ROOT))
warnings.filterwarnings("ignore")

SAMPLE_PDF = _ROOT / "genon/preprocessor/sample_files/pdf_sample.pdf"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY = "YOUR_OPENROUTER_API_KEY"
MODEL = "google/gemini-2.0-flash-001"

def build_old():
    from intelligent_processor import DocumentProcessor
    from docling.datamodel.pipeline_options import PictureDescriptionApiOptions
    from pydantic import AnyUrl
    dp = DocumentProcessor()
    dp.pipe_line_options.do_picture_description = True
    dp.pipe_line_options.enable_remote_services = True
    dp.pipe_line_options.generate_picture_images = True
    dp.pipe_line_options.picture_description_options = PictureDescriptionApiOptions(
        url=AnyUrl(OPENROUTER_URL),
        headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
        params={"model": MODEL, "max_tokens": 1000, "temperature": 0.1},
        prompt=(_FACADE / "configs/enrich/image_description/gemini-2.0-flash.yaml")
               .read_text(encoding="utf-8").split("prompt:")[1].strip().strip("|").strip(),
        timeout=90,
        picture_area_threshold=0.001,
    )
    dp._create_converters()
    return dp

_NEW_CFG = {
    "format_options": {
        "pdf": {"pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": True},
    },
    "chunker": {"name": "smart", "max_tokens": 1024},
    "enrichers": [{"name": "image_description", "api_key": OPENROUTER_KEY, "config_file": "gemini-2.0-flash"}],
    "return_level": "document",
    "log_level": 4,
}

def build_new():
    from test_intelligent_processor import DocumentProcessor
    return DocumentProcessor(config=_NEW_CFG)

def get_descriptions(doc):
    results = []
    for item, _ in doc.iterate_items():
        if isinstance(item, PictureItem):
            descs = [a.text for a in item.annotations if hasattr(a, "text") and a.text]
            results.append({"ref": str(item.self_ref), "descriptions": descs})
    return results

old_dp = build_old()
new_dp = build_new()

old_doc = old_dp.load_documents(str(SAMPLE_PDF))
new_doc = new_dp.load_documents(str(SAMPLE_PDF))
async def run_enrichers(doc):
    for enricher in new_dp.enrichers:
        doc = await enricher.enrich(doc)
    return doc
new_doc = asyncio.run(run_enrichers(new_doc))

old_descs = get_descriptions(old_doc)
new_descs = get_descriptions(new_doc)

lines = [
    "# 이미지 디스크립션 비교 테스트 결과", "",
    f"**날짜**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
    f"**파일**: `{SAMPLE_PDF.name}`  ",
    f"**모델**: {MODEL} (OpenRouter)", "",
    "## 요약",
    "| 항목 | OLD (PictureDescriptionApiOptions) | NEW (ImageDescriptionEnricher) |",
    "|------|:---:|:---:|",
    f"| PictureItem 수 | {len(old_descs)} | {len(new_descs)} |",
    f"| 설명 생성 수 | {sum(1 for d in old_descs if d['descriptions'])} | {sum(1 for d in new_descs if d['descriptions'])} |",
    "", "## OLD 설명", "",
]
for i, d in enumerate(old_descs):
    desc = d["descriptions"][0] if d["descriptions"] else "(없음)"
    lines.append(f"**[{i}]** `{d['ref']}`  \n{desc}\n")
lines += ["", "## NEW 설명", ""]
for i, d in enumerate(new_descs):
    desc = d["descriptions"][0] if d["descriptions"] else "(없음)"
    lines.append(f"**[{i}]** `{d['ref']}`  \n{desc}\n")

md = "\n".join(lines)
print(md)
out = _ROOT / "genon/preprocessor/tests/image_description_compare_result.md"
out.write_text(md, encoding="utf-8")
print(f"\n결과 저장: {out}")
EOF
```

---

## intelligent_processor ToC 비교 테스트

> OLD(intelligent_processor + enrich) vs NEW(test_intelligent_processor + TOCEnricher)  
> 동일 조건: OpenRouter, google/gemini-2.0-flash-001, doc_type=law  
> 결과: `genon/preprocessor/tests/intelligent_toc_compare_result.md`

```bash
.venv-cu126/bin/python3 - << 'EOF'
import sys, warnings, asyncio
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from docling_core.types.doc import SectionHeaderItem

_ROOT = Path(".").resolve()
_FACADE = _ROOT / "genon/preprocessor/facade"
sys.path.insert(0, str(_FACADE))
sys.path.insert(0, str(_ROOT))

SAMPLE_PDF = _ROOT / "genon/preprocessor/sample_files/pdf_sample.pdf"

_TOC_CONFIG = {
    "format_options": {
        "pdf": {"pipeline_options": "pdf", "backend": "pypdf", "generate_picture_images": False},
        "docx": {"pipeline_options": "simple", "backend": "msword"},
        "md":   {"pipeline_options": "simple", "backend": "md"},
        "html": {"pipeline_options": "simple", "backend": "html"},
        "hwpx": {"pipeline_options": "simple", "backend": "hwpx"},
        "pptx": {"pipeline_options": "simple", "backend": "mspowerpoint"},
    },
    "chunker": {"name": "smart", "max_tokens": 1024},
    "enrichers": [
        {
            "name": "toc",
            "doc_type": "law",
            "api_provider": "openrouter",
            "api_base_url": "https://openrouter.ai/api/v1/chat/completions",
            "api_key": "YOUR_OPENROUTER_API_KEY",
            "model": "google/gemini-2.0-flash-001",
            "temperature": 0.0, "top_p": 0.00001, "seed": 33, "max_tokens": 10000,
            "extract_metadata": True,
        }
    ],
    "return_level": "chunk", "log_level": 4,
}

def build_old():
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    from intelligent_processor import DocumentProcessor
    return DocumentProcessor()

def build_new():
    from test_intelligent_processor import DocumentProcessor
    return DocumentProcessor(config=_TOC_CONFIG)

def get_section_headers(doc):
    headers = []
    for item, _ in doc.iterate_items():
        if isinstance(item, SectionHeaderItem):
            headers.append({"level": item.level, "text": item.text.strip()})
    return headers

# OLD: load → enrich(내부) → split
def run_old(dp, pdf_path):
    doc = dp.load_documents(pdf_path)
    doc = dp.enrichment(doc)
    chunks = dp.split_documents(doc)
    return doc, chunks

# NEW: load → TOCEnricher(내부) → split
async def run_new(dp, pdf_path):
    doc = dp.load_documents(pdf_path)
    for enricher in dp.enrichers:
        doc = await enricher.enrich(doc)
    chunks = dp.split_documents(doc, file_path=pdf_path)
    return doc, chunks

pdf_path = str(SAMPLE_PDF)
assert SAMPLE_PDF.exists(), f"샘플 PDF 없음: {pdf_path}"

print("[OLD] intelligent_processor (enrich 주석 해제) 초기화 중...")
old_dp = build_old()
print("[NEW] test_intelligent_processor (TOCEnricher) 초기화 중...")
new_dp = build_new()

print("[OLD] 처리 중...")
old_doc, old_chunks = run_old(old_dp, pdf_path)
old_headers = get_section_headers(old_doc)

print("[NEW] 처리 중...")
new_doc, new_chunks = asyncio.run(run_new(new_dp, pdf_path))
new_headers = get_section_headers(new_doc)

# 비교
old_texts = {h["text"] for h in old_headers}
new_texts = {h["text"] for h in new_headers}
both = old_texts & new_texts
only_old = old_texts - new_texts
only_new = new_texts - old_texts

lines = [
    "# ToC 비교 테스트 결과 — intelligent_processor", "",
    f"**날짜**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
    f"**파일**: `{SAMPLE_PDF.name}`  ",
    f"**모델**: google/gemini-2.0-flash-001 (OpenRouter)", "",
    "## 섹션 헤더 수",
    "",
    "| 항목 | OLD | NEW |",
    "|------|-----|-----|",
    f"| 총 청크 수 | {len(old_chunks)} | {len(new_chunks)} |",
    f"| 섹션 헤더 수 | {len(old_headers)} | {len(new_headers)} |",
    f"| 공통 헤더 수 | {len(both)} | {len(both)} |",
    "",
    "## OLD 섹션 헤더",
    "",
]
for h in old_headers:
    lines.append(f"- (lv{h['level']}) {h['text']}")
lines += ["", "## NEW 섹션 헤더", ""]
for h in new_headers:
    lines.append(f"- (lv{h['level']}) {h['text']}")
lines += ["", "## OLD에만 있는 헤더", ""]
for t in sorted(only_old): lines.append(f"- {t}")
lines += ["", "## NEW에만 있는 헤더", ""]
for t in sorted(only_new): lines.append(f"- {t}")

md = "\n".join(lines)
print("\n" + md)
out = _ROOT / "genon/preprocessor/tests/intelligent_toc_compare_result.md"
out.write_text(md, encoding="utf-8")
print(f"\n결과 저장: {out}")
EOF
```

---

## attachment_processor CSV/XLSX 비교 테스트

> OLD(attachment_processor.TabularLoader) vs NEW(loaders.tabular_loader → DoclingDocument)  
> 결과: `genon/preprocessor/tests/attachment_tabular_compare_result.md`

```bash
.venv-cu126/bin/python3 - << 'EOF'
import sys, json
from pathlib import Path
from datetime import datetime

_ROOT = Path(".").resolve()
_FACADE = _ROOT / "genon/preprocessor/facade"
sys.path.insert(0, str(_FACADE))
sys.path.insert(0, str(_ROOT))

CSV_PATH  = str(_ROOT / "genon/preprocessor/sample_files/csv_sample.csv")
XLSX_PATH = str(_ROOT / "genon/preprocessor/sample_files/xlsx_sample.xlsx")

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
from attachment_processor import TabularLoader as OldTabularLoader

def run_old(file_path: str):
    ext = Path(file_path).suffix.lower()
    loader = OldTabularLoader(file_path, ext)
    vectors = loader.return_vectormeta_format()
    return loader.data_dict, vectors

from loaders.tabular_loader import TabularLoader as NewTabularLoader

def run_new(file_path: str):
    loader = NewTabularLoader(config={})
    return loader.load(file_path)

def doc_to_md_tables(doc):
    lines = []
    for table in doc.tables:
        caption = ""
        if table.captions:
            caption = table.captions[0].text if hasattr(table.captions[0], 'text') else str(table.captions[0])
        ann_info = ""
        if table.annotations:
            ann = table.annotations[0]
            if hasattr(ann, 'content'):
                ann_info = json.dumps(ann.content, ensure_ascii=False)
        lines += [f"**시트**: {caption}", f"**어노테이션**: `{ann_info}`",
                  f"**행 수(헤더 포함)**: {table.data.num_rows}, **열 수**: {table.data.num_cols}", ""]
        if table.data.table_cells:
            header_cells = sorted([c for c in table.data.table_cells if c.column_header], key=lambda c: c.start_col_offset_idx)
            lines.append("| " + " | ".join(c.text for c in header_cells) + " |")
            lines.append("| " + " | ".join("---" for _ in header_cells) + " |")
            data_cells = [c for c in table.data.table_cells if not c.column_header]
            rows_dict = {}
            for c in data_cells:
                rows_dict.setdefault(c.start_row_offset_idx, {})[c.start_col_offset_idx] = c.text
            for r_idx in sorted(rows_dict)[:5]:
                row = rows_dict[r_idx]
                lines.append("| " + " | ".join(row.get(ci, "") for ci in range(table.data.num_cols)) + " |")
            if len(rows_dict) > 5:
                lines.append(f"_(+{len(rows_dict)-5}행 생략)_")
        lines.append("")
    return "\n".join(lines)

lines = ["# CSV/XLSX 비교 테스트 결과 — attachment_processor", "",
         f"**날짜**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ", ""]

for label, file_path in [("CSV", CSV_PATH), ("XLSX", XLSX_PATH)]:
    lines += [f"## {label} — `{Path(file_path).name}`", ""]
    old_data, old_vectors = run_old(file_path)
    old_text = old_vectors[0].text if old_vectors else "(없음)"
    lines += ["### OLD (attachment_processor.TabularLoader)", "",
              f"- 벡터 수: {len(old_vectors)}",
              f"- 시트 수: {len(old_data.get('data', []))}",
              f"- text 미리보기 (400자):", "", f"```", old_text[:400], "```", ""]
    new_doc = run_new(file_path)
    lines += ["### NEW (loaders.tabular_loader → DoclingDocument)", "",
              f"- TableItem 수: {len(new_doc.tables)}", "",
              doc_to_md_tables(new_doc), "---", ""]

md = "\n".join(lines)
print(md)
out = _ROOT / "genon/preprocessor/tests/attachment_tabular_compare_result.md"
out.write_text(md, encoding="utf-8")
print(f"\n결과 저장: {out}")
EOF
```

---

## attachment_processor CSV/XLSX 풀 파이프라인 비교

> OLD(attachment_processor 전체 파이프라인) vs NEW(test_attachment_processor 전체 파이프라인)  
> OLD: load → data_dict → [DA] 단일 벡터  
> NEW: load(TabularLoader) → split(hybrid) → 청크 텍스트  
> 결과: `genon/preprocessor/tests/attachment_tabular_pipeline_compare_result.md`

```bash
.venv-cu126/bin/python3 - << 'EOF'
import sys, copy
from pathlib import Path
from datetime import datetime

_ROOT = Path(".").resolve()
_FACADE = _ROOT / "genon/preprocessor/facade"
sys.path.insert(0, str(_FACADE))
sys.path.insert(0, str(_ROOT))

CSV_PATH  = str(_ROOT / "genon/preprocessor/sample_files/csv_sample.csv")
XLSX_PATH = str(_ROOT / "genon/preprocessor/sample_files/xlsx_sample.xlsx")

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── OLD ──────────────────────────────────────────────────────────────────────
from attachment_processor import TabularLoader as OldTabularLoader

def run_old(file_path):
    ext = Path(file_path).suffix.lower()
    loader = OldTabularLoader(file_path, ext)
    vectors = loader.return_vectormeta_format()
    return vectors

# ── NEW ──────────────────────────────────────────────────────────────────────
from test_attachment_processor import DocumentProcessor, config as _BASE_CFG

_cfg = copy.deepcopy(_BASE_CFG)
_cfg["return_level"] = "chunk"
dp = DocumentProcessor(config=_cfg)

def run_new(file_path):
    doc = dp.load_documents(file_path)
    return dp.split_documents(doc, file_path=file_path)

# ── 리포트 ───────────────────────────────────────────────────────────────────
lines = [
    "# CSV/XLSX 풀 파이프라인 비교 — attachment_processor", "",
    f"**날짜**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ", "",
]

for label, file_path in [("CSV", CSV_PATH), ("XLSX", XLSX_PATH)]:
    lines += [f"## {label} — `{Path(file_path).name}`", ""]

    old_vecs = run_old(file_path)
    old_text = old_vecs[0].text if old_vecs else ""
    lines += [
        "### OLD (attachment_processor)",
        f"- 벡터 수: **{len(old_vecs)}**",
        f"- [DA] 접두사: {'있음' if old_text.startswith('[DA]') else '없음'}",
        f"- 전체 텍스트 길이: {len(old_text)}자",
        "", "```", old_text[:600], "```", "",
    ]

    new_chunks = run_new(file_path)
    lines += [
        "### NEW (test_attachment_processor)",
        f"- 청크 수: **{len(new_chunks)}**",
    ]
    for i, c in enumerate(new_chunks):
        lines += [
            f"- chunk[{i}] 텍스트 길이: {len(c.text)}자",
            "", "```", c.text[:600], "```", "",
        ]

    lines += ["---", ""]

md = "\n".join(lines)
print(md)
out = _ROOT / "genon/preprocessor/tests/attachment_tabular_pipeline_compare_result.md"
out.write_text(md, encoding="utf-8")
print(f"\n결과 저장: {out}")
EOF
```

