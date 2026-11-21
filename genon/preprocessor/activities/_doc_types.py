"""doc_type 등록 명령 — `python -m genon.preprocessor.activities doc-types list | validate | add | remove`.

parse activity 폴더(`activities/<이름>/`)의 doc_type 등록을 읽고 더하고 뺀다. 등록은 두 곳에 있다(#748).

    규칙 파일      `custom_field_<doc_type>.yaml` 최상위의 `doc_type:` — `add` 가 만드는 등록이다
    등록 블록      `config.yaml` 의 `enrichment` 목록에 있는 `- custom_fields:` 항목 — #748 전부터 있던 등록이다

`add` 는 규칙 파일 하나만 만들고 `config.yaml` 을 고치지 않는다. 기본 activity `parse` 의 `config.yaml` 은 서빙본
빌드가 다시 만드는 파일이라, 배포처에서 고치면 새 빌드를 merge 할 때 충돌하기 때문이다. 그래서 기본 activity 에도
`add` 할 수 있다. `remove` 는 규칙 파일로 등록한 doc_type 이면 그 파일을 지우고, 등록 블록이면 블록을 뺀다(기본
activity 의 등록 블록은 같은 이유로 빼지 않는다). 전처리 Studio 가 코드스페이스의 서빙본 체크아웃에서 부른다 —
worker 는 이 모듈을 쓰지 않는다.

판정은 worker 가 쓰는 함수를 그대로 부른다. 이름 비교는 `normalize_doc_type(s)`, 규칙 파일 읽기는
`load_custom_fields_config`, 규칙 파일 검사는 `tools/config_precheck/precheck_custom_fields.py` 의 `check_block`.
규칙 파일 등록 찾기는 worker 와 같은 `config_v2.rule_file_registrations` 다.
이 모듈이 새로 하는 일은 등록 찾기, 단계별 결과 모으기, 규칙 파일·`config.yaml` 의 텍스트 수정이다.

두 파일 모두 주석이 문서 역할을 하므로(Studio 툴팁) YAML 로 다시 쓰지 않고 필요한 줄만 텍스트로 고친다.
고친 결과는 다시 읽어 의도한 값만 달라졌는지 확인한 뒤에만 쓴다.

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
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")          # add --name 에만 적용한다. pipeline 의 doc_type 이름 규칙과 같다
# 등록 블록의 첫 줄(`  - custom_fields:`) — 목록 항목의 들여쓰기를 여기서 읽는다.
_BLOCK_HEAD_RE = re.compile(r"^(?P<indent>[ ]*)-[ ]+custom_fields[ ]*:[ ]*(#.*)?$")
# 등록 블록이 등록 자체에 쓰는 키. `--from` 블록에 그 밖의 키가 있으면 규칙 파일로 옮길 수 없어 `add` 를 거부한다.
_OWN_KEYS = ("enable", "doc_type", "config_file")
DOC_TYPE_KEY = "doc_type"
# 규칙 파일 최상위의 `doc_type:` 줄과 `schema:` 줄 — `add` 가 복사본에 doc_type 을 적을 자리를 찾는다.
_DOC_TYPE_LINE_RE = re.compile(r"^doc_type[ ]*:")
_SCHEMA_LINE_RE = re.compile(r"^schema[ ]*:")


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
    file_blocks: list[dict]             # 규칙 파일에 적은 등록 — 등록 블록과 같은 모양 `{doc_type, config_file}`
    presets: dict                       # 등록 블록·규칙 파일의 `model_preset:` 을 펼 때 쓴다

    def label(self, name: str = _folders.CONFIG_FILE) -> str:
        return f"activities/{self.name}/{name}"

    @property
    def registrations(self) -> list[dict]:
        """모든 등록 — 등록 블록, 그 뒤에 규칙 파일 등록."""
        return [*self.blocks, *self.file_blocks]

    def from_file(self, block: dict) -> bool:
        return any(block is item for item in self.file_blocks)


def rule_file_name(doc_type: str) -> str:
    return f"custom_field_{doc_type}.yaml"


def _load(activity: str, activities_dir: Path | None) -> _Activity:
    from genon.preprocessor.processing.common import config_parse as cp
    from genon.preprocessor.processing.enrichment import config_v2

    root = activities_dir or _folders.ACTIVITIES_DIR
    known = _folders.slugs(root)
    if activity not in known:
        raise DocTypeError("activity_not_found",
                           f"activity 없음: {activity!r} (있는 것: {', '.join(known) or '<없음>'})")
    if _folders.stage_of(activity) != PARSE_STAGE:
        raise DocTypeError("not_parse_activity",
                           f"activity {activity!r} 는 parse 단계가 아님: doc_type 은 parse activity 에만 등록함")
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
    try:
        file_blocks = config_v2.rule_file_registrations(folder)
    except ValueError as exc:
        raise DocTypeError("config_invalid", f"activities/{activity} 의 규칙 파일 읽기 실패: {exc}") from exc
    return _Activity(activity, folder, config_path, config, blocks, file_blocks, presets)


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
    """모든 등록 → `{activity, unknown_doc_type, doc_types: [{doc_type, config_file, enable, description,
    registered_in, removable, copyable}]}`.

    등록 블록이 먼저, 규칙 파일 등록이 그 뒤다. 규칙 파일 등록은 끄는 키가 없으므로 늘 `enable: true` 다.
    블록의 `doc_type` 이 목록이면 이름마다 한 항목, 없으면(모든 doc_type 대상) `doc_type: null` 한 항목이다.
    `config_file` 없이 설정을 블록에 직접 적었으면 `config_file`·`description` 이 null 이다.

    `registered_in` 은 등록 위치다 — `rule_file`(규칙 파일의 `doc_type:` 줄) | `config`(`config.yaml` 의 등록 블록).
    `removable` 은 `remove --name <doc_type>` 이, `copyable` 은 `add --from <doc_type>` 이 거부하지 않는지다.
    두 값은 그 명령의 거부 검사(`_removal`·`_copy_check`)를 그대로 불러 정하므로 명령 결과와 어긋나지 않는다.
    `doc_type` 이 null 인 항목은 이름으로 부를 수 없어 둘 다 false 다.
    """
    from genon.preprocessor.processing.common.parser_config import resolve_unknown_doc_type_policy

    act = _load(activity, activities_dir)
    defaults = act.config.get("defaults")
    try:
        policy = resolve_unknown_doc_type_policy((defaults if isinstance(defaults, dict) else {}).get("unknown_doc_type"))
    except ValueError as exc:
        raise DocTypeError("config_invalid", f"{act.label()} 읽기 실패: {exc}") from exc
    entries = []
    for block in act.registrations:
        config_file = _config_file_of(block)
        for doc_type in _doc_types_of(block) or (None,):
            entries.append({"doc_type": doc_type, "config_file": config_file, "enable": _enabled(block),
                            "description": _description(act.folder, config_file),
                            "registered_in": "rule_file" if act.from_file(block) else "config",
                            "removable": doc_type is not None and _passes(_removal, act, doc_type),
                            "copyable": doc_type is not None and _passes(_copy_check, act, doc_type)})
    return {"activity": act.name, "unknown_doc_type": policy, "doc_types": entries}


def _passes(check, act: _Activity, doc_type: str) -> bool:
    """거부 검사 `check(act, doc_type)` 가 `DocTypeError` 없이 끝나는지."""
    try:
        check(act, doc_type)
    except DocTypeError:
        return False
    return True


def _step(step: str, status: str, reason: str | None = None, message: str | None = None) -> dict:
    return {"step": step, "status": status, **({"reason": reason, "message": message} if reason else {})}


def _check_rule_file(act: _Activity, block: dict, doc_type: str) -> dict:
    """등록(등록 블록 또는 규칙 파일 등록)이 가리키는 규칙 파일이 있고, worker 기동 때와 같은 검사를 통과하는지."""
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
    matches = [b for b in act.registrations if doc_type in _doc_types_of(b)]
    rest = [_step("block", "skip"), _step("config_file", "skip")]
    if not matches:
        return [_step("registered", "fail", "not_registered",
                      f"doc_type={doc_type} 등록 없음: activities/{act.name} 에 doc_type 을 적은 규칙 파일도, "
                      f"{_folders.CONFIG_FILE} 의 custom_fields 등록 블록도 없음"),
                *rest]
    if len(matches) > 1:
        where = ", ".join(_config_file_of(b) or _folders.CONFIG_FILE if act.from_file(b) else _folders.CONFIG_FILE
                          for b in matches)
        return [_step("registered", "fail", "registered_twice",
                      f"doc_type={doc_type} 중복 등록: activities/{act.name} 에 등록 {len(matches)}개 ({where}), "
                      "1개만 남겨야 함"),
                *rest]
    block = matches[0]
    steps = [_step("registered", "pass")]
    if act.from_file(block):                         # 규칙 파일 등록에는 꺼짐·파일 이름 불일치가 없다
        return [*steps, _step("block", "pass"), _check_rule_file(act, block, doc_type)]
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
    act = _load(activity, activities_dir)
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


def _with_doc_type(text: str, doc_type: str, label: str) -> str:
    """규칙 파일 본문의 최상위 `doc_type:` 을 `doc_type` 으로 맞춘 본문. 그 밖의 줄은 한 글자도 바꾸지 않는다.

    `doc_type:` 줄이 있으면 그 줄을 바꾸고, 없으면 `schema:` 줄 바로 아래에 넣는다.
    """
    lines = text.splitlines(keepends=True)
    cannot = DocTypeError("config_invalid",
                          f"{label} 에 doc_type 기록 실패: 최상위 `schema:` 줄이나 한 줄짜리 `doc_type:` 줄을 찾지 못함\n"
                          "규칙 파일 최상위에 `schema: v2` 를 한 줄로 적은 형식으로 수정 필요")
    try:
        before = yaml.safe_load(text)
    except yaml.YAMLError:
        raise cannot from None
    at = next((i for i, line in enumerate(lines) if _DOC_TYPE_LINE_RE.match(line)), None)
    schema = next((i for i, line in enumerate(lines) if _SCHEMA_LINE_RE.match(line)), None)
    if not isinstance(before, dict) or (at is None and schema is None):
        raise cannot
    anchor = lines[at if at is not None else schema]
    newline = "\r\n" if anchor.endswith("\r\n") else "\n"
    if not anchor.endswith(("\n", "\r")):
        lines[lines.index(anchor)] = anchor + newline
    written = f"doc_type: {doc_type}{newline}"
    if at is not None:
        lines[at] = written
    else:
        lines.insert(schema + 1, written)
    new_text = "".join(lines)
    # 고친 결과를 다시 읽어 확인한다 — doc_type 값 하나만 달라야 한다.
    try:
        after = yaml.safe_load(new_text)
    except yaml.YAMLError:
        raise cannot from None
    if after != {**before, DOC_TYPE_KEY: doc_type}:
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


def _copy_source(act: _Activity, origin: str) -> Path:
    """`add --from <origin>` 이 복사할 규칙 파일. 복사할 수 없으면 `DocTypeError`."""
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import resolve_custom_fields_config_path

    sources = [b for b in act.registrations if _normalize(origin) in _doc_types_of(b)]
    if not sources:
        known = sorted({d for b in act.registrations for d in _doc_types_of(b)})
        raise DocTypeError("source_not_found",
                           f"--from doc_type={origin} 등록 없음: activities/{act.name} "
                           f"(등록된 것: {', '.join(known) or '<없음>'})")
    source = sources[0]
    source_file = _config_file_of(source)
    try:
        source_path = resolve_custom_fields_config_path(source_file, str(act.folder)) if source_file else None
    except ValueError:
        source_path = None
    if source_path is None or not source_path.is_file():
        raise DocTypeError("config_file_missing",
                           f"--from doc_type={origin} 의 규칙 파일 없음: {act.label(source_file or '<config_file 없음>')}")
    extra = sorted(k for k in source if k not in _OWN_KEYS)
    if extra and not act.from_file(source):
        # 블록의 그 밖의 키는 규칙 파일에 둘 자리가 없다. 조용히 빼면 복사본의 동작이 원본과 달라진다.
        raise DocTypeError("config_invalid",
                           f"--from doc_type={origin} 복사 불가: {act.label()} 의 등록 블록에 "
                           f"enable·doc_type·config_file 외의 키가 있음 ({', '.join(extra)})\n"
                           "그 키가 없는 doc_type 을 --from 으로 지정 필요")
    return source_path


def _copy_check(act: _Activity, origin: str) -> None:
    """`list` 의 `copyable` — `add --from <origin>` 이 하는 거부 검사 전부(원본 찾기, 복사본에 doc_type 줄 적기)."""
    source_path = _copy_source(act, origin)
    _with_doc_type(_read_text(source_path), "copy_check", act.label(source_path.name))


def add_doc_type(name: str, origin: str, activity: str, title: str | None = None,
                 activities_dir: Path | None = None) -> tuple[dict, list[Path]]:
    """parse activity 에 doc_type 을 등록한다 → (`{doc_type, activity}`, 만든 파일 [규칙 파일]).

    `origin` doc_type 의 규칙 파일을 `custom_field_<name>.yaml` 로 복사하고 그 파일 최상위에 `doc_type: <name>` 을
    적는다. 이 한 줄이 등록이다 — `config.yaml` 은 고치지 않으므로 기본 activity `parse` 에도 쓸 수 있다.
    거부할 때는 `DocTypeError` 를 올리고 어떤 파일도 만들지 않는다.
    """
    act = _load(activity, activities_dir)
    if not NAME_RE.fullmatch(name):
        raise DocTypeError("invalid_name", f"doc_type 이름 형식 오류: {name!r} (소문자로 시작, 소문자·숫자·_ 만 허용)")
    registered = [b for b in act.registrations if name in _doc_types_of(b)]
    if registered:
        steps = _validate(act, name)
        problem = next((s["message"] for s in steps if s["status"] == "fail"), None)
        state = "등록 상태 정상" if problem is None else "등록 상태 확인 필요"
        raise DocTypeError(
            "already_registered",
            f"doc_type={name} 이미 등록됨: config_file={_config_file_of(registered[0]) or '<없음>'}, {state}"
            + (f"\n{problem}" if problem else ""),
            steps=steps)
    source_path = _copy_source(act, origin)
    target = act.folder / rule_file_name(name)
    if target.exists():
        raise DocTypeError("config_file_exists",
                           f"doc_type={name} 등록 중단: 규칙 파일이 이미 있음 ({act.label(target.name)})\n"
                           "그 파일을 지우거나 다른 이름 지정 필요")
    rule_text = _with_doc_type(_titled(_read_text(source_path), name, title), name, act.label(source_path.name))
    with target.open("xb") as f:
        f.write(rule_text.encode("utf-8"))
    return {"doc_type": name, "activity": act.name}, [target]


def _block_span(lines: list[str], head: int) -> tuple[int, int]:
    """등록 블록 하나가 차지하는 줄 범위 [head, last]. 블록의 끝 = 목록 항목보다 깊이 들여 쓴 마지막 내용 줄."""
    indent = len(_BLOCK_HEAD_RE.match(lines[head].rstrip("\r\n")).group("indent"))  # type: ignore[union-attr]
    last = head
    for i in range(head + 1, len(lines)):
        body = lines[i].strip()
        if not body or body.startswith("#"):
            continue
        if len(lines[i]) - len(lines[i].lstrip(" ")) <= indent:
            break
        last = i
    return head, last


def _remove_block(act: _Activity, index: int) -> str:
    """`config.yaml` 본문에서 index 번째 등록 블록의 줄만 뺀 본문. 그 밖의 줄은 한 글자도 바꾸지 않는다."""
    text = _read_text(act.config_path)
    lines = text.splitlines(keepends=True)
    heads = [i for i, line in enumerate(lines) if _BLOCK_HEAD_RE.match(line.rstrip("\r\n"))]
    cannot = DocTypeError("config_invalid",
                          f"{act.label()} 에서 등록 블록 삭제 실패: 등록 블록의 범위를 찾지 못함\n"
                          "등록 블록을 `  - custom_fields:` 아래에 키를 한 줄씩 적은 형식으로 수정 필요")
    if len(heads) != len(act.blocks):
        raise cannot
    first, last = _block_span(lines, heads[index])
    kept = lines[:first] + lines[last + 1:]
    before_items = act.config.get("enrichment") or []
    registered = [i for i, item in enumerate(before_items) if isinstance(item, dict) and isinstance(item.get(BLOCK_KEY), dict)]
    at = registered[index]
    expected = [*before_items[:at], *before_items[at + 1:]]
    if not expected:                     # 마지막 항목을 빼면 `enrichment:` 가 null 이 되어 기본값이 달라진다 — 빈 목록으로 둔다
        key = next((i for i, line in enumerate(kept) if re.match(r"^enrichment[ ]*:[ ]*(#.*)?$", line.rstrip("\r\n"))), None)
        if key is None:
            raise cannot
        newline = "\r\n" if kept[key].endswith("\r\n") else "\n"
        kept[key] = re.sub(r"^enrichment[ ]*:", "enrichment: []", kept[key].rstrip("\r\n"), count=1) + newline
    new_text = "".join(kept)
    # 뺀 결과를 다시 읽어 확인한다 — enrichment 에서 그 항목 하나만 빠지고 그 밖의 내용은 같아야 한다.
    try:
        after = yaml.safe_load(new_text)
    except yaml.YAMLError:
        raise cannot from None
    if (not isinstance(after, dict) or list(after) != list(act.config)
            or any(after[k] != act.config[k] for k in act.config if k != "enrichment")
            or after.get("enrichment") != expected):
        raise cannot
    return new_text


@dataclass(frozen=True)
class _Removal:
    """`remove` 가 지울 등록 하나와 그 규칙 파일."""

    doc_type: str
    block: dict
    from_file: bool
    path: Path | None                   # 등록의 규칙 파일(로더와 같은 함수로 푼 실제 경로)
    others: set                         # 다른 등록이 쓰는 규칙 파일 경로
    new_config: str | None = None       # 등록 블록이면 그 블록을 뺀 `config.yaml` 텍스트


def _removal(act: _Activity, name: str) -> _Removal:
    """`remove --name <name>` 의 거부 검사 — 파일을 바꾸지 않는다. `list` 의 `removable` 도 이것으로 정한다."""
    from genon.preprocessor.processing.enrichment.custom_fields_enricher import resolve_custom_fields_config_path

    want = _normalize(name)
    hits = [b for b in act.registrations if want in _doc_types_of(b)]
    if not hits:
        known = sorted({d for b in act.registrations for d in _doc_types_of(b)})
        raise DocTypeError("not_registered",
                           f"doc_type={name} 삭제 불가: activities/{act.name} 에 등록 없음 "
                           f"(등록된 것: {', '.join(known) or '<없음>'})")
    if len(hits) > 1:
        raise DocTypeError("registered_twice",
                           f"doc_type={name} 삭제 불가: activities/{act.name} 에 등록이 {len(hits)}개\n"
                           "config.yaml 의 등록 블록과 규칙 파일의 doc_type 을 직접 정리 필요")
    block = hits[0]
    from_file = act.from_file(block)
    if len(_doc_types_of(block)) > 1:
        raise DocTypeError("shared_block",
                           f"doc_type={name} 삭제 불가: 등록 하나에 여러 doc_type 이 있음 "
                           f"({', '.join(_doc_types_of(block))})\n"
                           f"{_config_file_of(block) if from_file else 'config.yaml'} 에서 doc_type 목록 직접 수정 필요")

    def resolved(config_file: str | None) -> Path | None:
        if not config_file:
            return None
        try:
            return resolve_custom_fields_config_path(config_file, str(act.folder)).resolve()
        except (ValueError, OSError):
            return None

    path = resolved(_config_file_of(block))
    # 다른 등록이 같은 파일을 쓰는지는 로더와 같은 함수로 푼 실제 경로로 본다(`./x.yaml`·확장자 생략도 같은 파일)
    others = {resolved(_config_file_of(b)) for b in act.registrations if b is not block}
    if from_file:
        if path in others:
            raise DocTypeError("shared_block",
                               f"doc_type={name} 삭제 불가: 규칙 파일 {_config_file_of(block)} 을 config.yaml 의 "
                               "다른 등록 블록도 사용함\nconfig.yaml 에서 그 블록을 먼저 정리 필요")
        return _Removal(want, block, True, path, others)
    if act.name == PARSE_STAGE:
        raise DocTypeError("base_activity_not_allowed",
                           f"doc_type={name} 삭제 불가: 기본 activity parse 의 config.yaml 에 등록된 doc_type 임\n"
                           "복제한 activity 에서 삭제하거나, 적재를 막으려면 pipeline 의 doc-type-mappings disable 사용")
    index = next(i for i, b in enumerate(act.blocks) if b is block)
    return _Removal(want, block, False, path, others, _remove_block(act, index))


def remove_doc_type(name: str, activity: str, activities_dir: Path | None = None) -> tuple[dict, list[Path]]:
    """parse activity 에서 doc_type 등록을 뺀다 → (`{doc_type, activity}`, 바뀐·지운 파일).

    `add` 의 반대다. 규칙 파일로 등록한 doc_type 이면 그 파일을 지운다(`config.yaml` 은 그대로다).
    등록 블록으로 등록한 doc_type 이면 `config.yaml` 에서 그 블록을 빼고, 그 블록의 규칙 파일이 이 폴더 안에 있고
    다른 등록이 쓰지 않으면 지운다 — 기본 activity `parse` 의 등록 블록은 빼지 않는다(`config.yaml` 을 고치게 된다).
    적재된 문서가 있는지는 확인하지 않는다(pipeline `doc-type-mappings remove` 몫).
    거부할 때는 `DocTypeError` 를 올리고 어떤 파일도 바꾸지 않는다.
    """
    act = _load(activity, activities_dir)
    plan = _removal(act, name)
    want, path, others = plan.doc_type, plan.path, plan.others
    if plan.from_file:
        path.unlink()                                 # type: ignore[union-attr]  # 규칙 파일 등록은 늘 이 폴더의 파일이다
        return {"doc_type": want, "activity": act.name}, [path]  # type: ignore[list-item]
    new_text = plan.new_config
    files: list[Path] = [act.config_path]
    rule = None
    if path is not None and path not in others and path.is_file() and path.parent == act.folder.resolve():
        rule = path
    old_text = _read_text(act.config_path)
    _write_atomic(act.config_path, new_text)  # type: ignore[arg-type]
    if rule is not None:
        try:
            rule.unlink()
        except BaseException:            # 규칙 파일을 지우지 못하면 등록 블록도 되돌린다 — 반쯤 지운 상태를 남기지 않는다
            _write_atomic(act.config_path, old_text)
            raise
        files.append(rule)
    return {"doc_type": want, "activity": act.name}, files
