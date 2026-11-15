"""doc_type 등록 명령 — `python -m genon.preprocessor.activities doc-types list | validate | add`.

parse activity 폴더(`activities/<이름>/`)의 `config.yaml` 에 있는 등록 블록(`enrichment` 목록의
`- custom_fields:` 항목)과 규칙 파일(`custom_field_<doc_type>.yaml`)을 읽고, `add` 는 그 폴더에 블록 하나와
규칙 파일 하나를 더한다. 전처리 Studio 가 코드스페이스의 서빙본 체크아웃에서 부른다 — worker 는 이 모듈을 쓰지 않는다.

판정은 worker 가 쓰는 함수를 그대로 부른다. 이름 비교는 `normalize_doc_type(s)`, 규칙 파일 읽기는
`load_custom_fields_config`, 규칙 파일 검사는 `tools/config_precheck/precheck_custom_fields.py` 의 `check_block`.
이 모듈이 새로 하는 일은 등록 블록 찾기, 단계별 결과 모으기, `config.yaml` 텍스트 삽입이다.

`config.yaml` 은 주석이 문서 역할을 하므로(Studio 툴팁) YAML 로 다시 쓰지 않고 마지막 등록 블록 뒤에 텍스트로
끼운다. 끼운 결과는 다시 읽어 "enrichment 끝에 항목 하나가 늘었고 나머지는 같다"를 확인한 뒤에만 쓴다.

실패 사유(`error.detail.reason`)는 이 파일의 `DocTypeError` 호출부가 전부다. 문서 처리 실패의 사유 코드
(`processing/common/reason_codes.py`)와는 별개다.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from genon.preprocessor.activities import _folders

PARSE_STAGE = "parse"
BLOCK_KEY = "custom_fields"
NAME_RE = re.compile(r"^[a-z0-9_]+$")
# 등록 블록의 첫 줄(`  - custom_fields:`) — 목록 항목의 들여쓰기를 여기서 읽는다.
_BLOCK_HEAD_RE = re.compile(r"^(?P<indent>[ ]*)-[ ]+custom_fields[ ]*:[ ]*(#.*)?$")
# `add` 가 새 블록에 직접 적는 키. `--from` 블록의 그 밖의 키는 함께 복사한다.
_OWN_KEYS = ("enable", "doc_type", "config_file")


class DocTypeError(ValueError):
    """`doc-types` 명령이 거부한 요청. 같은 입력이면 결과가 같으므로 다시 시도하지 않는다."""

    non_retryable = True

    def __init__(self, reason: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.detail: dict[str, Any] = {"reason": reason, **detail}


@dataclass(frozen=True)
class _Activity:
    """activity 하나의 설정 폴더와 읽은 `config.yaml`."""

    name: str
    folder: Path
    config_path: Path
    config: dict
    blocks: list[dict]                  # enrichment 의 custom_fields 등록 블록(적힌 순서)
    presets: dict                       # 등록 블록·규칙 파일의 `model_preset:` 을 펼 때 쓴다

    def label(self, name: str = _folders.CONFIG_FILE) -> str:
        return f"activities/{self.name}/{name}"


def rule_file_name(doc_type: str) -> str:
    return f"custom_field_{doc_type}.yaml"


def _load(activity: str, activities_dir: Path | None, *, allow_base: bool) -> _Activity:
    from genon.preprocessor.processing.common import config_parse as cp

    root = activities_dir or _folders.ACTIVITIES_DIR
    known = _folders.slugs(root)
    if activity not in known:
        raise DocTypeError("activity_not_found",
                           f"activity 없음: {activity!r} (있는 것: {', '.join(known) or '<없음>'})")
    if _folders.stage_of(activity) != PARSE_STAGE:
        raise DocTypeError("not_parse_activity",
                           f"activity {activity!r} 는 parse 단계가 아님: doc_type 은 parse activity 에만 등록함")
    if activity == PARSE_STAGE and not allow_base:
        raise DocTypeError("base_activity_not_allowed",
                           "기본 activity parse 에는 doc_type 을 추가할 수 없음\n"
                           "복제한 activity 지정 필요 (없으면 먼저 생성: activities new --from parse --slug parse_<이름>)")
    folder = root / activity
    config_path = folder / _folders.CONFIG_FILE
    where = f"activities/{activity}/{_folders.CONFIG_FILE}"
    if not config_path.is_file():
        raise DocTypeError("config_invalid", f"{where} 없음")
    try:
        config = cp.read_yaml_file(config_path)
    except ValueError as exc:
        raise DocTypeError("config_invalid", f"{where} 읽기 실패: {exc}") from exc
    if not isinstance(config, dict):
        raise DocTypeError("config_invalid", f"{where} 읽기 실패: 최상위가 mapping 이 아님")
    enrichment = config.get("enrichment") or []
    if not isinstance(enrichment, list):
        raise DocTypeError("config_invalid", f"{where} 읽기 실패: enrichment 가 목록이 아님")
    blocks = [item[BLOCK_KEY] for item in enrichment
              if isinstance(item, dict) and isinstance(item.get(BLOCK_KEY), dict)]
    presets = cp.collect_model_presets(config, str(config_path), strict=False)
    return _Activity(activity, folder, config_path, config, blocks, presets)


def _doc_types_of(block: dict) -> tuple[str, ...]:
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import normalize_doc_types

    return normalize_doc_types(block.get("doc_type"))


def _normalize(name: str) -> str:
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import normalize_doc_type

    return normalize_doc_type(name)


def _config_file_of(block: dict) -> str | None:
    return str(block.get("config_file") or "").strip() or None


def _enabled(block: dict) -> bool:
    return bool(block.get("enable", True))          # worker(enrichment_config)와 같다 — 없으면 켠 것


def _description(folder: Path, config_file: str | None) -> str | None:
    """규칙 파일 첫 줄의 주석(`#` 뒤 그대로). 주석이 아니거나 파일이 없으면 None."""
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import resolve_custom_fields_config_path

    if not config_file:
        return None
    try:
        with resolve_custom_fields_config_path(config_file, str(folder)).open(encoding="utf-8") as f:
            first = f.readline()
    except (OSError, ValueError):
        return None
    return first.lstrip("#").strip() or None if first.startswith("#") else None


def list_doc_types(activity: str = PARSE_STAGE, activities_dir: Path | None = None) -> dict:
    """등록 블록 → `{activity, unknown_doc_type, doc_types: [{doc_type, config_file, enable, description}]}`.

    블록의 `doc_type` 이 목록이면 이름마다 한 항목, 없으면(모든 doc_type 대상) `doc_type: null` 한 항목이다.
    `config_file` 없이 설정을 블록에 직접 적었으면 `config_file`·`description` 이 null 이다.
    """
    from genon.preprocessor.processing.common.parser_config import resolve_unknown_doc_type_policy

    act = _load(activity, activities_dir, allow_base=True)
    defaults = act.config.get("defaults")
    try:
        policy = resolve_unknown_doc_type_policy((defaults if isinstance(defaults, dict) else {}).get("unknown_doc_type"))
    except ValueError as exc:
        raise DocTypeError("config_invalid", f"{act.label()} 읽기 실패: {exc}") from exc
    entries = []
    for block in act.blocks:
        config_file = _config_file_of(block)
        for doc_type in _doc_types_of(block) or (None,):
            entries.append({"doc_type": doc_type, "config_file": config_file, "enable": _enabled(block),
                            "description": _description(act.folder, config_file)})
    return {"activity": act.name, "unknown_doc_type": policy, "doc_types": entries}


def _step(step: str, status: str, reason: str | None = None, message: str | None = None) -> dict:
    return {"step": step, "status": status, **({"reason": reason, "message": message} if reason else {})}


def _check_rule_file(act: _Activity, block: dict, doc_type: str) -> dict:
    """등록 블록이 가리키는 규칙 파일이 있고, worker 기동 때와 같은 검사를 통과하는지."""
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import load_custom_fields_config
    from genon.preprocessor.tools.config_precheck import precheck_custom_fields as precheck

    config_file = _config_file_of(block)
    if config_file:
        try:
            load_custom_fields_config(config_file, str(act.folder))
        except FileNotFoundError:
            return _step("config_file", "fail", "config_file_missing",
                         f"doc_type={doc_type} 규칙 파일 없음: {act.label(config_file)}")
        except ValueError as exc:
            return _step("config_file", "fail", "config_invalid", f"doc_type={doc_type} 규칙 파일 오류: {exc}")
    try:
        problems = precheck.check_block(_folders.CONFIG_FILE, block, act.folder, set(), act.presets)
    except SystemExit as exc:                        # precheck 의 load_yaml 은 읽지 못한 파일을 SystemExit 로 알린다
        problems = [f"[기동실패] {exc}"]
    blocking = [p.removeprefix("[기동실패]").strip() for p in problems if p.startswith("[기동실패]")]
    if blocking:
        more = f" (외 {len(blocking) - 1}건)" if len(blocking) > 1 else ""
        return _step("config_file", "fail", "config_invalid",
                     f"doc_type={doc_type} 규칙 파일 오류{more}: {blocking[0]}")
    return _step("config_file", "pass")


def _validate(act: _Activity, doc_type: str) -> list[dict]:
    matches = [b for b in act.blocks if doc_type in _doc_types_of(b)]
    rest = [_step("block", "skip"), _step("config_file", "skip")]
    if not matches:
        return [_step("registered", "fail", "not_registered",
                      f"doc_type={doc_type} 등록 없음: {act.label()} 의 enrichment 에 custom_fields 등록 블록 없음"),
                *rest]
    if len(matches) > 1:
        return [_step("registered", "fail", "registered_twice",
                      f"doc_type={doc_type} 중복 등록: {act.label()} 에 등록 블록 {len(matches)}개, 1개만 남겨야 함"),
                *rest]
    block = matches[0]
    steps = [_step("registered", "pass")]
    if not _enabled(block):
        return [*steps,
                _step("block", "fail", "disabled",
                      f"doc_type={doc_type} 등록 꺼짐: {act.label()} 의 등록 블록이 enable: false"),
                _step("config_file", "skip")]
    expected, written = rule_file_name(doc_type), _config_file_of(block)
    if written != expected:
        steps.append(_step("block", "warn", "config_file_name_differs",
                           f"doc_type={doc_type} 규칙 파일 이름 다름: 받은 값={written or '<없음>'}, 기대 값={expected}"))
    else:
        steps.append(_step("block", "pass"))
    return [*steps, _check_rule_file(act, block, doc_type)]


def _is_valid(steps: list[dict]) -> bool:
    return all(step["status"] != "fail" for step in steps)


def validate_doc_type(name: str, activity: str = PARSE_STAGE, activities_dir: Path | None = None) -> dict:
    """등록 상태 검사 → `{valid, doc_type, steps}`. 검사 결과가 실패여도 예외가 아니다(`valid: false`)."""
    act = _load(activity, activities_dir, allow_base=True)
    doc_type = _normalize(name)
    steps = _validate(act, doc_type)
    return {"valid": _is_valid(steps), "doc_type": doc_type, "steps": steps}


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")          # 줄바꿈(CRLF 포함)을 그대로 둔다


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _block_text(doc_type: str, source: dict, indent: str, key_indent: str, newline: str) -> str:
    extra = {k: v for k, v in source.items() if k not in _OWN_KEYS}
    lines = [f"{indent}- {BLOCK_KEY}:",
             f"{key_indent}enable: true",
             f"{key_indent}doc_type: {doc_type}",
             f"{key_indent}config_file: {rule_file_name(doc_type)}"]
    if extra:
        dumped = yaml.safe_dump(extra, allow_unicode=True, default_flow_style=False, sort_keys=False)
        lines += [key_indent + line if line else line for line in dumped.splitlines()]
    return newline.join(lines) + newline


def _insert_block(act: _Activity, doc_type: str, source: dict) -> str:
    """`config.yaml` 본문의 마지막 등록 블록 뒤에 새 블록을 끼운 본문. 기존 줄은 한 글자도 바꾸지 않는다."""
    text = _read_text(act.config_path)
    lines = text.splitlines(keepends=True)
    heads = [i for i, line in enumerate(lines) if _BLOCK_HEAD_RE.match(line.rstrip("\r\n"))]
    cannot = DocTypeError("config_invalid",
                          f"{act.label()} 에 등록 블록 추가 실패: 마지막 custom_fields 등록 블록의 끝을 찾지 못함\n"
                          "등록 블록을 `  - custom_fields:` 아래에 키를 한 줄씩 적은 형식으로 수정 필요")
    if not heads:
        raise cannot
    head = heads[-1]
    indent = _BLOCK_HEAD_RE.match(lines[head].rstrip("\r\n")).group("indent")  # type: ignore[union-attr]
    last, key_indent = head, None
    for i in range(head + 1, len(lines)):                 # 블록의 끝 = 목록 항목보다 깊이 들여 쓴 마지막 내용 줄
        body = lines[i].strip()
        if not body or body.startswith("#"):
            continue                                       # 빈 줄·주석은 다음 내용 줄이 블록 안일 때만 블록에 든다
        depth = len(lines[i]) - len(lines[i].lstrip(" "))
        if depth <= len(indent):
            break
        key_indent = key_indent or lines[i][:depth]
        last = i
    newline = "\r\n" if lines[head].endswith("\r\n") else "\n"
    if not lines[last].endswith(("\n", "\r")):
        lines[last] += newline
    lines.insert(last + 1, _block_text(doc_type, source, indent, key_indent or indent + "    ", newline))
    new_text = "".join(lines)
    # 끼운 결과를 다시 읽어 확인한다 — enrichment 끝에 항목 하나가 늘고 그 밖의 내용은 같아야 한다.
    try:
        after = yaml.safe_load(new_text)
    except yaml.YAMLError:
        raise cannot from None
    expected_block = {"enable": True, "doc_type": doc_type, "config_file": rule_file_name(doc_type),
                      **{k: v for k, v in source.items() if k not in _OWN_KEYS}}
    before_items = act.config.get("enrichment") or []
    if not isinstance(after, dict) or list(after) != list(act.config):
        raise cannot
    after_items = after.get("enrichment")
    same_rest = all(after[k] == act.config[k] for k in act.config if k != "enrichment")
    registered = [i for i, item in enumerate(before_items) if isinstance(item, dict) and BLOCK_KEY in item]
    at = registered[-1] + 1
    if not same_rest or after_items != [*before_items[:at], {BLOCK_KEY: expected_block}, *before_items[at:]]:
        raise cannot
    return new_text


def _titled(text: str, doc_type: str, title: str | None) -> str:
    """규칙 파일 본문의 첫 줄 주석을 `--title` 로 바꾼다. 첫 줄이 주석이 아니면 그 위에 넣는다."""
    if not title:
        return text
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    lines[0:1 if lines and lines[0].startswith("#") else 0] = [
        f"# 문서 유형 {doc_type}({title})의 custom_fields 설정{newline}"]
    return "".join(lines)


def add_doc_type(name: str, origin: str, activity: str, title: str | None = None,
                 activities_dir: Path | None = None) -> tuple[dict, list[Path]]:
    """복제한 parse activity 에 doc_type 을 등록한다 → (`{doc_type, activity}`, 바뀐 파일 [config.yaml, 규칙 파일]).

    `origin` doc_type 의 규칙 파일을 `custom_field_<name>.yaml` 로 복사하고 `config.yaml` 에 등록 블록을 더한다.
    거부할 때는 `DocTypeError` 를 올리고 어떤 파일도 바꾸지 않는다.
    """
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import resolve_custom_fields_config_path

    act = _load(activity, activities_dir, allow_base=False)
    if not NAME_RE.fullmatch(name):
        raise DocTypeError("invalid_name", f"doc_type 이름 형식 오류: {name!r} (소문자·숫자·_ 만 허용)")
    registered = [b for b in act.blocks if name in _doc_types_of(b)]
    if registered:
        steps = _validate(act, name)
        problem = next((s["message"] for s in steps if s["status"] == "fail"), None)
        state = "등록 상태 정상" if problem is None else "등록 상태 확인 필요"
        raise DocTypeError(
            "already_registered",
            f"doc_type={name} 이미 등록됨: config_file={_config_file_of(registered[0]) or '<없음>'}, {state}"
            + (f"\n{problem}" if problem else ""),
            steps=steps)
    sources = [b for b in act.blocks if _normalize(origin) in _doc_types_of(b)]
    if not sources:
        known = sorted({d for b in act.blocks for d in _doc_types_of(b)})
        raise DocTypeError("source_not_found",
                           f"--from doc_type={origin} 등록 없음: {act.label()} (등록된 것: {', '.join(known) or '<없음>'})")
    source = sources[0]
    source_file = _config_file_of(source)
    try:
        source_path = resolve_custom_fields_config_path(source_file, str(act.folder)) if source_file else None
    except ValueError:
        source_path = None
    if source_path is None or not source_path.is_file():
        raise DocTypeError("config_file_missing",
                           f"--from doc_type={origin} 의 규칙 파일 없음: {act.label(source_file or '<config_file 없음>')}")
    target = act.folder / rule_file_name(name)
    if target.exists():
        raise DocTypeError("config_file_exists",
                           f"doc_type={name} 등록 중단: 규칙 파일이 이미 있음 ({act.label(target.name)})\n"
                           "그 파일을 지우거나 다른 이름 지정 필요")
    rule_text = _titled(_read_text(source_path), name, title)
    config_text = _insert_block(act, name, source)
    with target.open("xb") as f:                          # 여기부터 파일을 쓴다 — 실패하면 만든 것을 되돌린다
        f.write(rule_text.encode("utf-8"))
    try:
        _write_atomic(act.config_path, config_text)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"doc_type": name, "activity": act.name}, [act.config_path, target]
