"""검사가 실행되지 않았는데 성공으로 보고하는 회귀를 방지한다."""
import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[2] / "tools/parse_chunk/parse_chunk_verify.py"
    spec = importlib.util.spec_from_file_location("review_parse_chunk_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("selected", [["unknown"], ["known", "unknown"]])
def test_unknown_type_is_rejected_before_loading_config(verifier, monkeypatch, tmp_path, selected):
    monkeypatch.setattr(verifier, "CASES", [("known", tmp_path / "input.json", "test")])
    monkeypatch.setattr(sys, "argv", ["verify", "--only", *selected])
    with pytest.raises(SystemExit) as error:
        verifier.main()
    assert error.value.code == 2


@pytest.mark.parametrize("sample_exists", [False, True])
def test_all_skipped_is_failure(verifier, monkeypatch, tmp_path, sample_exists):
    sample = tmp_path / "input.json"
    if sample_exists:
        sample.write_text("{}")
    monkeypatch.setattr(verifier, "CASES", [("known", sample, "test")])
    monkeypatch.setattr(verifier, "load_custom_field_blocks", lambda: [])
    monkeypatch.setattr(verifier, "pick_block", lambda *a: None)
    monkeypatch.setattr(sys, "argv", ["verify", "--out", str(tmp_path / "out")])
    assert verifier.main() == 1


def test_executed_case_keeps_result(verifier, monkeypatch, tmp_path):
    sample = tmp_path / "input.json"
    sample.write_text("{}")
    monkeypatch.setattr(verifier, "CASES", [("known", sample, "test")])
    monkeypatch.setattr(verifier, "load_custom_field_blocks", lambda: [])
    monkeypatch.setattr(verifier, "pick_block", lambda *a: {})
    monkeypatch.setattr(verifier, "run_case", lambda *a: (True, "", ""))
    monkeypatch.setattr(verifier, "verify", lambda *a: [])
    monkeypatch.setattr(verifier, "expected_from_yaml", lambda *a: ([], {}, []))
    monkeypatch.setattr(verifier, "llm_null_rate", lambda *a: "-")
    monkeypatch.setattr(sys, "argv", ["verify", "--out", str(tmp_path / "out")])
    assert verifier.main() == 0


def test_table_text_formats_unescapes_html_entities(verifier):
    """HTML 표기의 `&amp;` 를 내용 소실로 판정하지 않는다(#557).

    셀 값은 sample_files/json/monimo_card_sample.json 의 카드 상세 링크다.
    """
    url = ("https://www.samsungcard.com/home/card/cardinfo/PGHPPCCCardCardinfoDetails001"
           "?code=AAP1913&click=UHPPCO0101M0_card_PGHPPCCCardCardinfoDetails001_PRD_AAP1913")
    md = f"| 링크 |\n| --- |\n| {url} |"
    chunk = {
        "text": md,
        "text_table_md": md,
        "text_table_html": f"<table><tr><th>링크</th></tr><tr><td>{url.replace('&', '&amp;')}</td></tr></table>",
    }
    assert verifier.check_table_text_formats([chunk]) == []


def test_expected_fields_are_read_from_v2_config(verifier, monkeypatch, tmp_path):
    """`schema: v2` 표기에서도 필수·상수 필드 기대값을 읽는다(#615).

    원문 키(`require.fields`, `const`)를 내부 이름(`required`, `constants`)으로 찾으면
    기대값이 비어 공통 단정이 동작하지 않는다.
    """
    (tmp_path / "custom_field_x.yaml").write_text(
        "schema: v2\n"
        "source:\n  kind: records\n  records_at: items\n"
        "fields:\n  GROUP_C:\n    const: SLF\n  TITLE:\n    alias: [title]\n"
        "require:\n  fields: [TITLE]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "RESOURCE_DIR", tmp_path)
    required, constants, _llm = verifier.expected_from_yaml({"config_file": "custom_field_x.yaml"})
    assert (required, constants) == (["TITLE"], {"GROUP_C": "SLF"})
