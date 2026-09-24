"""최상위 facade 끼리 서로 import 하지 않는지 정적으로 검사한다.

기본 전처리기 서비스는 facade 한 개만 `preprocessor.py` 로 이름을 바꿔 마운트하므로,
다른 `*_processor.py` 를 import 하는 facade 는 배포본에서 ImportError 로 기동하지 못한다.
로컬·CI 에서는 파일이 모두 있어 import 가 성공하므로 실행 테스트로는 드러나지 않는다.
그래서 소스를 AST 로 읽어 import 문을 직접 단정한다(함수 안의 지연 import 포함).

`processing/` 과 `core/` 는 배포본에 함께 들어가므로 import 해도 된다. 이름이 비슷한
`processing.converters.xlsx_processor` 는 facade 가 아니므로 대상이 아니다.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

FACADE_DIR = Path(__file__).resolve().parents[2] / "facade"
FACADES = sorted(FACADE_DIR.glob("*_processor.py"))
FACADE_STEMS = {p.stem for p in FACADES}


def _imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            found.append((node.lineno, base))
            # `from facade import parser_processor` 형태는 이름 쪽에 facade 가 온다.
            found += [(node.lineno, f"{base}.{a.name}".lstrip(".")) for a in node.names]
    return found


def test_top_level_facades_do_not_import_each_other():
    assert len(FACADES) >= 2, f"facade 를 찾지 못했다: {FACADE_DIR}"
    violations = [
        f"{path.name}:{lineno} imports {module}"
        for path in FACADES
        for lineno, module in _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        if module.rsplit(".", 1)[-1] in FACADE_STEMS
    ]
    assert violations == []
