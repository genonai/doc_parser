"""`activities doc-types list | validate | add` (#712) — 임시 activities 폴더에서 등록 블록과 규칙 파일을 다룬다.

전처리 Studio 가 코드스페이스에서 부르는 명령이다. 거부할 때 어떤 파일도 바뀌지 않는지, `add` 가 `config.yaml` 의
기존 줄과 주석을 그대로 두는지, 결과 줄(`@@RESULT@@`)의 `error.detail.reason` 이 계약대로인지를 본다.
"""
import importlib
import json
from pathlib import Path

import pytest
import yaml

from genon.preprocessor.activities import _doc_types, _folders
from genon.preprocessor.activities._doc_types import DocTypeError

pytestmark = pytest.mark.unit

# 기본 activity parse 의 설정을 줄인 것 — 주석·빈 줄·등록 블록 뒤의 다른 섹션을 그대로 둔다.
CONFIG = """\
# Studio 는 옵션 이름 옆 툴팁에 그 키의 주석을 보여 준다.
defaults:
  log_level: 4
  # warn(기본)=경고만 | fail=요청을 실패시킨다
  unknown_doc_type: fail

enrichment:
  # 목차 추출
  - toc:
      enable: false
  # ── 문서 유형별 custom_fields 추출 ──
  - custom_fields:
      enable: true                       # 이 문서유형 설정을 쓸지. true(기본) | false
      doc_type: card                     # 요청의 doc_type 이 이 값과 같을 때 적용한다(대소문자 무시)
      config_file: custom_field_card.yaml   # 이 문서유형의 필드 추출 규칙 파일(이 폴더 기준)
  # 뮤직 AI 플레이리스트 문구
  - custom_fields:
      enable: true
      doc_type: music_ai
      config_file: custom_field_music_ai.yaml      # 이 문서유형의 필드 추출 규칙 파일(이 폴더 기준)

  # 고객사이트 전용 문서유형은 여기가 아니라 사이트 폴더에 둔다.

output:
  format: "docling"
"""
LAST_BLOCK_END = "      config_file: custom_field_music_ai.yaml      # 이 문서유형의 필드 추출 규칙 파일(이 폴더 기준)\n"

# genon/sites/monimo/activities/parse/custom_field_music_ai.yaml 을 줄인 것(LLM 을 쓰지 않는 레코드 매핑).
RULE = """\
# 모니모 뮤직 AI 플레이리스트 문구 JSON 레코드
#
# playList 항목 하나가 chunk 하나다.

schema: v2
source:
  kind: records
  records_at: playList
  on_missing: error
fields:
  GROUP_C:
    const: IFP
  TXT:
    alias: [text]
    transform: to_text
require:
  fields: [TXT]
body:
  fields: [TXT]
  split: true
"""

# pipeline 시험 자료 tests/unit/fixtures/doc_type_api/tour/parse_config_blocks.yaml (genonai/data-ingestion-pipeline#216)
# 의 등록 블록. `add` 가 만드는 블록이 이것과 같아야 pipeline 쪽 시험 자료를 그대로 쓸 수 있다.
PIPELINE_BLOCKS = """\
enrichment:
  - custom_fields:
      enable: true
      doc_type: tour_blog
      config_file: custom_field_tour_blog.yaml
  - custom_fields:
      enable: true
      doc_type: tour_guide
      config_file: custom_field_tour_guide.yaml
"""


@pytest.fixture
def acts(tmp_path, monkeypatch):
    """기본 activity parse·chunk 와, parse 를 복제한 parse_tour(복제 때 card·music_ai 등록이 따라온다)."""
    from genon.preprocessor.activities.__main__ import new_activity

    root = tmp_path / "activities"
    for slug in ("parse", "chunk"):
        (root / slug).mkdir(parents=True)
        (root / slug / "processor.py").write_text(f"# {slug}\nclass DocumentProcessor: ...\n", encoding="utf-8")
        (root / slug / "config.yaml").write_text(CONFIG if slug == "parse" else "output: {}\n", encoding="utf-8")
    for doc_type in ("card", "music_ai"):
        (root / "parse" / f"custom_field_{doc_type}.yaml").write_text(RULE, encoding="utf-8")
    monkeypatch.delenv("GENOS_RESOURCE_DIR", raising=False)
    new_activity("parse", "parse_tour", activities_dir=root)
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _edit_config(root: Path, activity: str, old: str, new: str) -> None:
    path = root / activity / "config.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_list_reads_registered_blocks_and_their_variants(acts):
    """등록 블록마다 한 항목. doc_type 목록·doc_type 없음·config_file 없는 블록·꺼진 블록·없는 규칙 파일도 읽는다."""
    (acts / "parse" / "config.yaml").write_text("""\
defaults:
  unknown_doc_type: warn
enrichment:
  - custom_fields:
      doc_type: [Card, card_new]
      config_file: custom_field_card.yaml
  - custom_fields:
      enable: false
      doc_type: gone
      config_file: custom_field_gone.yaml
  - custom_fields:
      doc_type: inline
      model_preset: default
      output_fields: [A]
  - custom_fields:
      config_file: custom_field_music_ai.yaml
""", encoding="utf-8")
    (acts / "parse" / "custom_field_music_ai.yaml").write_text("schema: v2\n# 첫 줄이 주석이 아님\n", encoding="utf-8")
    described = "모니모 뮤직 AI 플레이리스트 문구 JSON 레코드"

    assert _doc_types.list_doc_types("parse", acts) == {"activity": "parse", "unknown_doc_type": "warn", "doc_types": [
        {"doc_type": "card", "config_file": "custom_field_card.yaml", "enable": True, "description": described},
        {"doc_type": "card_new", "config_file": "custom_field_card.yaml", "enable": True, "description": described},
        {"doc_type": "gone", "config_file": "custom_field_gone.yaml", "enable": False, "description": None},
        {"doc_type": "inline", "config_file": None, "enable": True, "description": None},
        {"doc_type": None, "config_file": "custom_field_music_ai.yaml", "enable": True, "description": None},
    ]}
    # 복제한 activity 는 복제 시점의 등록을 그대로 가진다
    assert [d["doc_type"] for d in _doc_types.list_doc_types("parse_tour", acts)["doc_types"]] == ["card", "music_ai"]


def _twice(root):
    _edit_config(root, "parse", "doc_type: card ", "doc_type: MUSIC_AI ")


def _disabled(root):
    _edit_config(root, "parse", "      enable: true\n      doc_type: music_ai", "      enable: false\n      doc_type: music_ai")


def _renamed(root):
    (root / "parse" / "custom_field_music_ai.yaml").rename(root / "parse" / "custom_field_music.yaml")
    _edit_config(root, "parse", "config_file: custom_field_music_ai.yaml", "config_file: custom_field_music.yaml")


def _missing(root):
    (root / "parse" / "custom_field_music_ai.yaml").unlink()


def _unknown_key(root):
    (root / "parse" / "custom_field_music_ai.yaml").write_text(RULE + "no_such_key: 1\n", encoding="utf-8")


def _not_yaml(root):
    (root / "parse" / "custom_field_music_ai.yaml").write_text("fields: [a\n", encoding="utf-8")


@pytest.mark.parametrize("name, break_it, valid, expected", [
    ("Music_AI", None, True, [("registered", "pass", None), ("block", "pass", None), ("config_file", "pass", None)]),
    ("tour_blog", None, False,
     [("registered", "fail", "not_registered"), ("block", "skip", None), ("config_file", "skip", None)]),
    ("music_ai", _twice, False,
     [("registered", "fail", "registered_twice"), ("block", "skip", None), ("config_file", "skip", None)]),
    ("music_ai", _disabled, False,
     [("registered", "pass", None), ("block", "fail", "disabled"), ("config_file", "skip", None)]),
    ("music_ai", _renamed, True,
     [("registered", "pass", None), ("block", "warn", "config_file_name_differs"), ("config_file", "pass", None)]),
    ("music_ai", _missing, False,
     [("registered", "pass", None), ("block", "pass", None), ("config_file", "fail", "config_file_missing")]),
    ("music_ai", _unknown_key, False,
     [("registered", "pass", None), ("block", "pass", None), ("config_file", "fail", "config_invalid")]),
    ("music_ai", _not_yaml, False,
     [("registered", "pass", None), ("block", "pass", None), ("config_file", "fail", "config_invalid")]),
])
def test_validate_reports_each_step_without_raising_or_writing(acts, name, break_it, valid, expected):
    if break_it:
        break_it(acts)
    before = _snapshot(acts)

    result = _doc_types.validate_doc_type(name, "parse", acts)

    assert (result["valid"], result["doc_type"]) == (valid, name.lower())
    assert [(s["step"], s["status"], s.get("reason")) for s in result["steps"]] == expected
    assert all(bool(s.get("message")) == (s["status"] in ("fail", "warn")) for s in result["steps"])
    assert _snapshot(acts) == before


def test_add_copies_rule_file_and_inserts_block_after_last_registration(acts):
    before = _snapshot(acts)

    data, files = _doc_types.add_doc_type("tour_blog", "music_ai", "parse_tour", title="여행 블로그 글",
                                          activities_dir=acts)

    folder = acts / "parse_tour"
    assert data == {"doc_type": "tour_blog", "activity": "parse_tour"}
    assert files == [folder / "config.yaml", folder / "custom_field_tour_blog.yaml"]
    # config.yaml: 마지막 등록 블록 바로 뒤에 블록 하나만 늘고, 기존 줄·주석·빈 줄은 그대로다
    inserted = ("  - custom_fields:\n      enable: true\n      doc_type: tour_blog\n"
                "      config_file: custom_field_tour_blog.yaml\n")
    assert (folder / "config.yaml").read_text(encoding="utf-8") == CONFIG.replace(LAST_BLOCK_END, LAST_BLOCK_END + inserted)
    # 만든 블록은 pipeline 시험 자료의 블록과 같다
    added = yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8"))["enrichment"][-1]
    assert added == yaml.safe_load(PIPELINE_BLOCKS)["enrichment"][0]
    # 규칙 파일: 첫 줄 주석만 --title 로 바뀌고 나머지는 원본과 같다
    first, _, rest = (folder / "custom_field_tour_blog.yaml").read_text(encoding="utf-8").partition("\n")
    assert first == "# 문서 유형 tour_blog(여행 블로그 글)의 custom_fields 설정"
    assert rest == RULE.partition("\n")[2]
    # 그 밖의 파일(기본 activity 포함)은 그대로이고, 새 등록은 검사를 통과한다
    after = _snapshot(acts)
    assert {k: v for k, v in after.items() if k not in
            ("parse_tour/config.yaml", "parse_tour/custom_field_tour_blog.yaml")} == \
        {k: v for k, v in before.items() if k != "parse_tour/config.yaml"}
    assert _doc_types.validate_doc_type("tour_blog", "parse_tour", acts)["valid"] is True
    assert _doc_types.list_doc_types("parse_tour", acts)["doc_types"][-1] == {
        "doc_type": "tour_blog", "config_file": "custom_field_tour_blog.yaml", "enable": True,
        "description": "문서 유형 tour_blog(여행 블로그 글)의 custom_fields 설정"}


def test_add_copies_other_keys_of_source_block_and_keeps_first_line_without_title(acts):
    """`--from` 블록의 그 밖의 키(extractor·markdown 등)는 새 블록에 함께 간다. `--title` 이 없으면 규칙 파일은 원본 그대로."""
    _edit_config(acts, "parse_tour", "      doc_type: music_ai\n",
                 "      doc_type: [music_ai, music]\n      extractor: json_mapping\n"
                 "      markdown:\n        front_matter: {on_missing: error}\n")

    _doc_types.add_doc_type("tour_blog", "MUSIC", "parse_tour", activities_dir=acts)

    folder = acts / "parse_tour"
    assert yaml.safe_load((folder / "config.yaml").read_text(encoding="utf-8"))["enrichment"][-1] == {"custom_fields": {
        "enable": True, "doc_type": "tour_blog", "config_file": "custom_field_tour_blog.yaml",
        "extractor": "json_mapping", "markdown": {"front_matter": {"on_missing": "error"}}}}
    assert (folder / "custom_field_tour_blog.yaml").read_text(encoding="utf-8") == RULE


def _broken_config(root):
    (root / "parse_tour" / "config.yaml").write_text("enrichment: [\n", encoding="utf-8")


def _flow_style_blocks(root):
    (root / "parse_tour" / "config.yaml").write_text(
        "enrichment: [{custom_fields: {doc_type: music_ai, config_file: custom_field_music_ai.yaml}}]\n",
        encoding="utf-8")


def _no_source_rule(root):
    (root / "parse_tour" / "custom_field_music_ai.yaml").unlink()


def _target_exists(root):
    (root / "parse_tour" / "custom_field_tour_blog.yaml").write_text(RULE, encoding="utf-8")


@pytest.mark.parametrize("name, origin, activity, break_it, reason", [
    ("tour_blog", "music_ai", "parse_nope", None, "activity_not_found"),
    ("tour_blog", "music_ai", "../parse", None, "activity_not_found"),
    ("tour_blog", "music_ai", "chunk", None, "not_parse_activity"),
    ("tour_blog", "music_ai", "parse", None, "base_activity_not_allowed"),
    ("Tour-Blog", "music_ai", "parse_tour", None, "invalid_name"),
    ("tour_blog", "faq", "parse_tour", None, "source_not_found"),
    ("tour_blog", "music_ai", "parse_tour", _no_source_rule, "config_file_missing"),
    ("tour_blog", "music_ai", "parse_tour", _target_exists, "config_file_exists"),
    ("tour_blog", "music_ai", "parse_tour", _broken_config, "config_invalid"),
    ("tour_blog", "music_ai", "parse_tour", _flow_style_blocks, "config_invalid"),
])
def test_add_rejects_with_reason_and_changes_no_file(acts, name, origin, activity, break_it, reason):
    if break_it:
        break_it(acts)
    before = _snapshot(acts)

    with pytest.raises(DocTypeError) as info:
        _doc_types.add_doc_type(name, origin, activity, title="설명", activities_dir=acts)

    assert info.value.detail == {"reason": reason}
    assert _snapshot(acts) == before


def test_add_of_registered_name_fails_with_registration_state(acts):
    """복제 때 따라온 music_ai 를 다시 add 하면 거부하고, validate 와 같은 검사 결과를 메시지 첫 줄과 detail.steps 에 싣는다."""
    before = _snapshot(acts)
    with pytest.raises(DocTypeError) as info:
        _doc_types.add_doc_type("music_ai", "card", "parse_tour", activities_dir=acts)
    assert str(info.value) == "doc_type=music_ai 이미 등록됨: config_file=custom_field_music_ai.yaml, 등록 상태 정상"
    assert info.value.detail == {"reason": "already_registered",
                                 "steps": _doc_types.validate_doc_type("music_ai", "parse_tour", acts)["steps"]}
    assert [s["status"] for s in info.value.detail["steps"]] == ["pass", "pass", "pass"]
    assert _snapshot(acts) == before

    (acts / "parse_tour" / "custom_field_music_ai.yaml").unlink()
    with pytest.raises(DocTypeError) as info:
        _doc_types.add_doc_type("music_ai", "card", "parse_tour", activities_dir=acts)
    first, second = str(info.value).split("\n")                    # 검사에 실패한 등록은 첫 줄이 알리고 둘째 줄이 사유다
    assert first.endswith("등록 상태 확인 필요")
    assert second == info.value.detail["steps"][-1]["message"]


def _run_cli(capsys, *argv) -> tuple[int, dict]:
    cli = importlib.import_module("genon.preprocessor.activities.__main__")
    code = cli.main(["doc-types", *argv])
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith(cli.RESULT_PREFIX)
    return code, json.loads(last[len(cli.RESULT_PREFIX):])


def test_cli_result_line_carries_reason_in_error_detail(acts, monkeypatch, capsys):
    """결과 줄 계약: 거부는 exit 1 · `error.detail.reason`, 검사 실패는 exit 0 · `data.valid: false`."""
    monkeypatch.setattr(_folders, "ACTIVITIES_DIR", acts)
    before = _snapshot(acts)

    code, result = _run_cli(capsys, "add", "--name", "tour_blog", "--from", "music_ai", "--activity", "parse")
    assert code == 1
    assert {k: result[k] for k in ("ok", "stage", "activity", "command")} == {
        "ok": False, "stage": None, "activity": "parse", "command": "doc-types"}
    assert {k: result["error"][k] for k in ("type", "non_retryable", "detail")} == {
        "type": "DocTypeError", "non_retryable": True, "detail": {"reason": "base_activity_not_allowed"}}
    assert "reason_code" not in result["error"]

    code, result = _run_cli(capsys, "validate", "--name", "tour_blog")
    assert (code, result["ok"], result["command"], result["data"]["valid"]) == (0, True, "doc-types", False)

    code, result = _run_cli(capsys, "add", "--name", "tour_blog", "--from", "music_ai", "--activity", "parse_tour",
                            "--title", "두\n줄")
    assert (code, result["error"]["type"]) == (1, "_UsageError")
    assert _snapshot(acts) == before

    code, result = _run_cli(capsys, "add", "--name", "tour_blog", "--from", "music_ai", "--activity", "parse_tour")
    assert (code, result["stage"], result["activity"]) == (0, None, "parse_tour")
    assert result["data"]["doc_type"] == "tour_blog"
    assert [Path(f).name for f in result["data"]["files"]] == ["config.yaml", "custom_field_tour_blog.yaml"]


@pytest.mark.parametrize("argv", [
    ("add", "--name", "tour_blog", "--from", "music_ai"),                                    # 필수 인자 누락(argparse)
    ("rename", "--name", "tour_blog"),                                                       # 모르는 action(argparse)
    ("add", "--name", "tour_blog", "--from", "music_ai", "--activity", "parse_tour", "--title", "두\n줄"),
])
def test_cli_usage_error_carries_command_and_reason(acts, monkeypatch, capsys, argv):
    """인자 오류도 결과 줄에 `command` 와 `error.detail.reason = usage_error` 를 싣는다. 다른 명령은 그대로다."""
    monkeypatch.setattr(_folders, "ACTIVITIES_DIR", acts)
    code, result = _run_cli(capsys, *argv)
    assert (code, result["ok"], result["stage"], result["command"]) == (1, False, None, "doc-types")
    assert {k: result["error"][k] for k in ("type", "non_retryable", "detail")} == {
        "type": "_UsageError", "non_retryable": False, "detail": {"reason": "usage_error"}}

    cli = importlib.import_module("genon.preprocessor.activities.__main__")
    assert cli.main(["remove"]) == 1                                                         # 다른 명령의 인자 오류
    other = json.loads(capsys.readouterr().out.strip().splitlines()[-1][len(cli.RESULT_PREFIX):])
    assert "detail" not in other["error"] and "command" not in other
