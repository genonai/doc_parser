#!/usr/bin/env python3
# PostToolUse(Edit|Write) 훅 — 편집 직후 CLAUDE.md 규칙 위반을 경고한다. 차단하지 않는다.
#
# 왜: 아래 두 규칙은 CLAUDE.md 에 글로만 있어 강제력이 없다. 편집이 끝난 뒤 알려 주면
# 같은 세션 안에서 바로 되돌릴 수 있다. PostToolUse 는 이미 적용된 변경을 보는 지점이라
# 막을 수는 없고, Claude 의 편집만 본다. 사람의 편집까지 막아야 하는 facade 간 import 금지는
# tests/unit/test_facade_isolation_unit.py 가 CI 에서 맡는다.
#
# 검사:
#   1. 파싱·청킹 파사드(parser_processor.py, chunking_processor.py)에 HEAD 에 없던 메소드가
#      생기면, 처리 로직은 processing/ 에 두고 파사드에는 호출부만 두라고 알린다.
#   2. 활성 경로 .py 의 주석·docstring 에 이모지가 있으면 알린다. 문자열 값은 대상이 아니다.
#
# 입력 파싱이나 검사 중 오류가 나면 조용히 통과한다(경고 훅이 편집을 방해하지 않게).

import ast
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

CUSTOMER_FACADES = {"parser_processor.py", "chunking_processor.py"}
ACTIVE_PREFIXES = (
    "main.py",
    "genon/preprocessor/processing/",
    "genon/preprocessor/facade/",
    "genon/preprocessor/src/",
)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⭕️]")


def _def_names(source: str) -> set[str]:
    return {
        n.name
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _new_facade_methods(root: Path, rel: str, source: str) -> list[str]:
    head = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{rel}"],
        capture_output=True, text=True,
    )
    if head.returncode != 0:
        return []
    return sorted(_def_names(source) - _def_names(head.stdout))


def _emoji_lines(source: str) -> list[int]:
    lines = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT and EMOJI.search(tok.string):
            lines.add(tok.start[0])
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = node.body[0] if node.body else None
            if (
                isinstance(doc, ast.Expr)
                and isinstance(doc.value, ast.Constant)
                and isinstance(doc.value.value, str)
                and EMOJI.search(doc.value.value)
            ):
                lines.add(doc.lineno)
    return sorted(lines)


def main() -> None:
    data = json.load(sys.stdin)
    path = Path(data.get("tool_input", {}).get("file_path", ""))
    root = Path(data.get("cwd") or ".")
    top = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if top.returncode == 0:
        root = Path(top.stdout.strip())
    if path.suffix != ".py" or not path.is_file():
        return
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return
    if not rel.startswith(ACTIVE_PREFIXES) or "/legacy/" in rel:
        return

    source = path.read_text(encoding="utf-8")
    warnings = []
    if path.name in CUSTOMER_FACADES and rel.startswith("genon/preprocessor/facade/"):
        added = _new_facade_methods(root, rel, source)
        if added:
            warnings.append(
                f"{rel} 에 새 메소드 {', '.join(added)} 가 생겼다. 이 파일은 고객이 여는 파사드이고 "
                "릴리스가 통째로 덮어쓴다. 처리 로직이면 processing/ 공용 모듈에 구현하고 여기에는 "
                "호출부만 둔다. 새 훅이면 facade/gitbook_doc/facade_hooks.md 와 "
                "tests/unit/test_facade_hooks_unit.py 도 함께 고친다."
            )
    lines = _emoji_lines(source)
    if lines:
        warnings.append(
            f"{rel} 의 주석·docstring 에 이모지가 있다(줄 {', '.join(map(str, lines))}). "
            "CLAUDE.md 작업 방식 절에 따라 강조는 문장으로 한다."
        )
    if warnings:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(warnings),
            }
        }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
