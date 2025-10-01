"""
PDF regression test - compare PDF processing with baselines
"""

import json
import pytest
from pathlib import Path
from difflib import SequenceMatcher


@pytest.mark.regression
@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "sample_files" / "regression_test").exists(),
    reason="regression_test directory not found",
)
def test_pdf_regression(basic_processor):
    """Test PDF processing against baseline"""
    test_dir = Path(__file__).resolve().parents[2] / "sample_files" / "regression_test"
    baseline_dir = Path(__file__).parent / "baselines"

    # Find PDF files
    pdf_files = list(test_dir.glob("*.pdf"))
    if not pdf_files:
        pytest.skip("No PDF files in regression_test directory")

    for test_file in pdf_files:
        test_name = f"pdf_{test_file.stem}"
        baseline_file = baseline_dir / f"{test_name}.json"

        # Process document
        processor = basic_processor()
        doc = processor.load_documents(str(test_file))
        chunks = processor.split_documents(doc)

        # Extract data
        text = ""
        if hasattr(doc, 'export_to_markdown'):
            text = doc.export_to_markdown()
        elif chunks:
            text = "\n".join(chunk.text if hasattr(chunk, 'text') else str(chunk) for chunk in chunks)

        current_result = {
            "text": text[:5000],  # Limit text size for storage
            "page_count": doc.num_pages() if hasattr(doc, 'num_pages') else 0,
            "chunk_count": len(chunks),
            "text_length": len(text)
        }

        # Load baseline
        if not baseline_file.exists():
            pytest.fail(
                f"Baseline not found: {baseline_file}\n"
                f"Run: pytest -m update_baseline -k test_update_pdf_baseline"
            )

        with open(baseline_file, 'r', encoding='utf-8') as f:
            baseline = json.load(f)

        # Compare
        text_similarity = SequenceMatcher(None, baseline["text"], current_result["text"]).ratio()
        assert text_similarity >= 0.95, f"Text similarity {text_similarity:.2%} below 95%"

        assert baseline["page_count"] == current_result["page_count"], \
            f"Page count changed: {baseline['page_count']} → {current_result['page_count']}"

        chunk_diff = abs(baseline["chunk_count"] - current_result["chunk_count"])
        assert chunk_diff <= max(2, baseline["chunk_count"] * 0.1), \
            f"Chunk count difference too large: {chunk_diff}"


@pytest.mark.update_baseline
def test_update_pdf_baseline(basic_processor):
    """Update PDF baselines"""
    test_dir = Path(__file__).resolve().parents[2] / "sample_files" / "regression_test"
    baseline_dir = Path(__file__).parent / "baselines"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = list(test_dir.glob("*.pdf"))
    if not pdf_files:
        pytest.skip("No PDF files to update baseline")

    for test_file in pdf_files:
        test_name = f"pdf_{test_file.stem}"
        baseline_file = baseline_dir / f"{test_name}.json"

        # Process document
        processor = basic_processor()
        doc = processor.load_documents(str(test_file))
        chunks = processor.split_documents(doc)

        # Extract data
        text = ""
        if hasattr(doc, 'export_to_markdown'):
            text = doc.export_to_markdown()
        elif chunks:
            text = "\n".join(chunk.text if hasattr(chunk, 'text') else str(chunk) for chunk in chunks)

        result = {
            "text": text[:5000],  # Limit text size
            "page_count": doc.num_pages() if hasattr(doc, 'num_pages') else 0,
            "chunk_count": len(chunks),
            "text_length": len(text)
        }

        # Save baseline
        with open(baseline_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"✅ Updated baseline: {test_name}")
        print(f"   Pages: {result['page_count']}, Chunks: {result['chunk_count']}, Text: {result['text_length']} chars")