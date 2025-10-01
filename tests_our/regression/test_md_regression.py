"""
Markdown regression test - compare MD processing with baselines
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
def test_md_regression(basic_processor):
    """Test MD processing against baseline"""
    test_dir = Path(__file__).resolve().parents[2] / "sample_files" / "regression_test"
    baseline_dir = Path(__file__).parent / "baselines"

    # Find MD files
    md_files = list(test_dir.glob("*.md"))
    if not md_files:
        pytest.skip("No MD files in regression_test directory")

    for test_file in md_files:
        test_name = f"md_{test_file.stem}"
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
            "chunk_count": len(chunks),
            "text_length": len(text)
        }

        # Load baseline
        if not baseline_file.exists():
            pytest.fail(
                f"Baseline not found: {baseline_file}\n"
                f"Run: pytest -m update_baseline -k test_update_md_baseline"
            )

        with open(baseline_file, 'r', encoding='utf-8') as f:
            baseline = json.load(f)

        # Compare
        text_similarity = SequenceMatcher(None, baseline["text"], current_result["text"]).ratio()
        assert text_similarity >= 0.95, f"Text similarity {text_similarity:.2%} below 95%"

        chunk_diff = abs(baseline["chunk_count"] - current_result["chunk_count"])
        assert chunk_diff <= max(2, baseline["chunk_count"] * 0.1), \
            f"Chunk count difference too large: {chunk_diff}"


@pytest.mark.update_baseline
def test_update_md_baseline(basic_processor):
    """Update MD baselines"""
    test_dir = Path(__file__).resolve().parents[2] / "sample_files" / "regression_test"
    baseline_dir = Path(__file__).parent / "baselines"
    baseline_dir.mkdir(parents=True, exist_ok=True)

    md_files = list(test_dir.glob("*.md"))
    if not md_files:
        pytest.skip("No MD files to update baseline")

    for test_file in md_files:
        test_name = f"md_{test_file.stem}"
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
            "chunk_count": len(chunks),
            "text_length": len(text)
        }

        # Save baseline
        with open(baseline_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"✅ Updated baseline: {test_name}")
        print(f"   Chunks: {result['chunk_count']}, Text: {result['text_length']} chars")