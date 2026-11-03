"""검사가 실행되지 않았는데 성공으로 보고하는 회귀를 방지한다."""
import importlib.util
import os
import shutil
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


@pytest.mark.parametrize(
    "has_site, expected",
    [(True, "sites/monimo/resource"), (False, "preprocessor/resource")],
)
def test_default_resource_dir_falls_back_without_sites(verifier, tmp_path, has_site, expected):
    """genon/sites 가 없는 배포본에서는 표준 resource/ 를 기본 설정 폴더로 쓴다(#617)."""
    genon = tmp_path / "genon"
    if has_site:
        (genon / "sites" / "monimo" / "resource").mkdir(parents=True)
    assert verifier.default_resource_dir(genon / "preprocessor") == genon / expected


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
    parser_dir = tmp_path / "activities" / "parse"          # custom_field 는 파서 설정 폴더에 있다(워커와 같은 자리)
    parser_dir.mkdir(parents=True)
    (parser_dir / "config.yaml").write_text("enrichment: []\n", encoding="utf-8")
    (parser_dir / "custom_field_x.yaml").write_text(
        "schema: v2\n"
        "source:\n  kind: records\n  records_at: items\n"
        "fields:\n  GROUP_C:\n    const: SLF\n  TITLE:\n    alias: [title]\n"
        "require:\n  fields: [TITLE]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(verifier, "RESOURCE_DIR", tmp_path / "resource")
    required, constants, _llm = verifier.expected_from_yaml({"config_file": "custom_field_x.yaml"})
    assert (required, constants) == (["TITLE"], {"GROUP_C": "SLF"})


# ── 배포본(genon/sites 없음)에서도 같은 값 — develop #617 을 studio/dev 구조로 옮긴 자리(#622) ──
#
# develop 은 설정이 resource/ 한 자리라 "사이트 없으면 표준 resource/" 로 충분했다. studio/dev 는 파서·청커 설정과
# custom_field 가 activities/<slug>/ 에 있고, 배포본 resource/ 는 표준판이라 모니모 custom_field 가 없다.
# 아래는 sync-serving-repo.sh SITE=monimo 가 만드는 모양(표준 + 사이트 activities/ 덮어쓰기, genon/sites 없음)을
# 임시 폴더에 세워, 저장소(사이트 완성본)에서 읽은 값과 같은지 본다.

PREPROCESSOR = Path(__file__).resolve().parents[2]
SITE = PREPROCESSOR.parent / "sites" / "monimo"


def _deploy_tree(root: Path) -> Path:
    """배포본 모형 — genon/preprocessor/{resource(표준), activities/<slug>(표준 + 사이트 덮어쓰기)}."""
    pre = root / "genon" / "preprocessor"
    shutil.copytree(PREPROCESSOR / "resource", pre / "resource")
    for slug in ("parse", "chunk"):
        shutil.copytree(PREPROCESSOR / "activities" / slug, pre / "activities" / slug)
        shutil.copytree(SITE / "activities" / slug, pre / "activities" / slug, dirs_exist_ok=True)
    return pre


def _expectations(verifier) -> dict:
    return {(b["doc_type"], b["config_file"]): verifier.expected_from_yaml(b)
            for b in verifier.load_custom_field_blocks()}


def test_deploy_tree_reads_the_same_expectations_as_the_site(verifier, monkeypatch, tmp_path):
    site = _expectations(verifier)                      # 저장소 기본 = 사이트 완성본
    # monimo_news 처럼 아무 필드도 단정하지 않는 설정도 있다 — 옛 자리(resource/)를 읽으면 전부 비어 아래 비교에서 걸린다
    assert sum(1 for required, constants, llm in site.values() if required or constants or llm) >= len(site) - 1 > 0

    pre = _deploy_tree(tmp_path)
    assert not list((pre / "resource").glob("custom_field_faq*"))   # 전제: 배포본 resource/ 에는 모니모 사본이 없다
    monkeypatch.setattr(verifier, "PREPROCESSOR_DIR", pre)
    monkeypatch.setattr(verifier, "RESOURCE_DIR", verifier.default_resource_dir(pre))
    assert verifier.RESOURCE_DIR == pre / "resource"
    assert verifier.parser_config_path() == pre / "activities" / "parse" / "config.yaml"
    assert verifier.chunker_config_path() == pre / "activities" / "chunk" / "config.yaml"
    assert _expectations(verifier) == site


def test_custom_field_is_read_from_the_parser_folder_not_resource(verifier, monkeypatch, tmp_path):
    """resource/ 에 같은 이름의 다른 파일이 있어도 워커가 읽는 파서 설정 폴더 쪽을 쓴다."""
    pre = _deploy_tree(tmp_path)
    (pre / "resource" / "custom_field_faq.yaml").write_text(
        "schema: v2\nsource:\n  kind: records\n  records_at: items\nfields:\n  X:\n    const: WRONG\n",
        encoding="utf-8")
    monkeypatch.setattr(verifier, "PREPROCESSOR_DIR", pre)
    monkeypatch.setattr(verifier, "RESOURCE_DIR", pre / "resource")
    block = next(b for b in verifier.load_custom_field_blocks() if b["doc_type"] == "faq")
    _required, constants, _llm = verifier.expected_from_yaml(block)
    assert "X" not in constants


@pytest.mark.parametrize("has_dev_presets", [False, True])
def test_dev_model_presets_are_set_only_when_the_file_exists(monkeypatch, tmp_path, has_dev_presets):
    """genon/sites 가 없는 배포본에서 없는 프리셋 파일을 가리키면 기동이 실패한다 — 있을 때만 넣는다(#617)."""
    tools = tmp_path / "genon" / "preprocessor" / "tools" / "parse_chunk"
    tools.mkdir(parents=True)
    shutil.copy(PREPROCESSOR / "tools" / "parse_chunk" / "parse_chunk_verify.py", tools)
    presets = tmp_path / "genon" / "sites" / "dev" / "model_presets.yaml"
    if has_dev_presets:
        presets.parent.mkdir(parents=True)
        presets.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("GENOS_MODEL_PRESETS_FILE", "")   # 되돌림을 기록해 둔 뒤 지운다
    monkeypatch.delenv("GENOS_MODEL_PRESETS_FILE")
    spec = importlib.util.spec_from_file_location("deploy_parse_chunk_verify", tools / "parse_chunk_verify.py")
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    assert os.environ.get("GENOS_MODEL_PRESETS_FILE") == (str(presets) if has_dev_presets else None)


def test_drill_copies_only_the_config_folders(monkeypatch, tmp_path):
    """드릴은 설정 자리(resource/ + activities/parse/)만 복사한다 — 배포본에서 상위 폴더를 통째로 복사하면
    genon/preprocessor 패키지 전체가 따라온다."""
    pre = _deploy_tree(tmp_path / "deploy")
    (pre / "sample_files").mkdir()
    (pre / "sample_files" / "big.bin").write_bytes(b"x")
    spec = importlib.util.spec_from_file_location(
        "review_run_drill", PREPROCESSOR / "tools" / "parse_chunk" / "drill" / "run_drill.py")
    drill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drill)
    monkeypatch.setattr(drill.verify, "PREPROCESSOR_DIR", pre)
    monkeypatch.setattr(drill.verify, "RESOURCE_DIR", drill.verify.default_resource_dir(pre))

    work = tmp_path / "work"
    config = drill.build_drill_config(work, [])
    assert sorted(p.name for p in work.iterdir()) == ["activities", "resource"]
    assert sorted(p.name for p in (work / "activities").iterdir()) == ["parse"]
    assert config == work / "activities" / "parse" / "config.yaml"
    assert (work / "activities" / "parse" / "custom_field_faq.yaml").exists()
