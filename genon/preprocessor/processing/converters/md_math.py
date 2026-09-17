r"""마크다운 LaTeX 수식을 파싱 전에 감추고 파싱 후 되돌린다.

## 왜 필요한가

docling 의 Markdown 백엔드(marko)는 `$` 를 모른다. 그래서 수식이 평범한 본문으로 흘러가고
세 가지가 깨진다(모두 상품설명서 md 로 실측한 것이다).

1. 백엔드는 `"|" in snippet_text` 한 줄로 표 모드에 들어간다. 절댓값·행렬·cases 처럼
   세로줄을 쓰는 수식이 든 문단은 통째로 빈 표가 되고 본문이 사라진다.
   실측: `$\left| S_t - K \right|$` 한 줄이 1x1 표가 되면서 앞뒤 문장까지 소실됐다.
2. CommonMark 백슬래시 이스케이프가 수식 안에서도 적용된다. `\{` 가 `{` 로 해독되고
   그 자리에서 인라인 요소가 갈려 문단 하나가 TextItem 여러 개로 쪼개진다.
3. `$$` 블록은 여는 줄·본문·닫는 줄이 각각 TextItem 이 된다. 수식이 하나의 단위가
   아니게 되므로 청크 경계가 수식 한가운데를 자를 수 있다.

## 왜 백엔드가 아니라 여기인가

marko 에 수식 요소를 등록하면 위 세 가지가 원천적으로 사라지지만, 그것은 docling 포크
본체를 고치는 일이라 배포에 wheel 재빌드가 강제된다. 대신 파싱 **전에** 수식을 백엔드가
건드릴 수 없는 문자열로 바꿔 두고 파싱 **후에** 되돌린다. 백엔드 입장에서 수식은 그냥
평범한 낱말 하나이므로 표로 오인할 세로줄도, 해독할 백슬래시도, 쪼갤 경계도 없다.

감추고 되돌리는 자리는 `processing/common/docling_runtime.py` 의 로딩 지점 한 곳이다. 모든
facade 가 그리로 모이므로 `/run` 이든 `/parser` 든 같은 보호를 받는다. 다만 `md_text_fence`
의 레이아웃 파이프 제거는 그보다 앞에서 도므로, 그쪽은 이 모듈의 `INLINE_MATH_RE` 를
가져다 스스로 수식 구간을 비켜간다.

## 플레이스홀더가 지켜야 하는 것

파싱과 그 전후 전처리를 통과하는 동안 원형이 유지돼야 한다. 그래서 ASCII 영숫자만 쓴다.

- 세로줄이 없어야 한다(표 오인)
- 백슬래시·중괄호가 없어야 한다(이스케이프 해독)
- 한 줄이어야 한다(블록 분해). 여러 줄 수식도 한 줄 토큰이 된다
- 마크다운 특수문자로 시작하지 않아야 한다(마커 heading 승격·인용문 오인)
- 전각/제로폭 문자가 없어야 한다(`text_norm` 의 문자 위생)

원문에 토큰과 같은 문자열이 이미 있으면 접두를 바꿔 충돌을 피한다.

## 오탐을 막는 규칙

통화 표기(`$100 에서 $200`)를 수식으로 잡으면 본문이 왜곡된다. 미검출보다 나쁜 실수다.
그래서 여는 `$` 뒤와 닫는 `$` 앞에 공백을 금지한다. 위 예는 본문 후보가 `100 에서 ` 로
공백에서 끝나므로 매치되지 않는다. escape 한 `\$` 도 제외한다.

블록 수식은 닫는 `$$` 를 확인한 뒤에만 성립시킨다. 이 확인이 없으면 `$$` 오타 하나가
문서의 나머지 전부를 수식으로 삼켜, 지금 고치려는 것과 같은 종류의 본문 소실을 만든다.
"""

from __future__ import annotations

import logging
import re
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

# 블록 수식이 닫히지 않은 채 이 줄 수를 넘으면 수식으로 보지 않는다. 실제 수식은 수십
# 줄을 넘지 않으므로, 상한을 두는 쪽이 미닫힘 문서에서 본문을 지킨다.
MAX_BLOCK_LINES = 40

# 여는 `$` 뒤와 닫는 `$` 앞의 공백을 금지해 통화 표기를 걸러낸다. 본문에서 개행을 빼
# 문단 전체가 수식으로 삼켜지는 것을 막고, `\.` 로 escape 한 문자는 통과시킨다.
INLINE_MATH_RE = re.compile(r"(?<![$\\])\$(?![\s$])((?:\\.|[^$\n\\])+?)(?<!\s)\$(?!\$)")

# 여는 `$$`. 마크다운 규격대로 들여쓰기 3칸까지 허용한다.
_BLOCK_OPEN_RE = re.compile(r"^[ \t]{0,3}\$\$")

# 플레이스홀더. 대문자 영숫자만 쓴다 — 위 "지켜야 하는 것" 참조.
_TOKEN_PREFIX = "GENOSMATH"
_TOKEN_RE_TEMPLATE = r"{prefix}[0-9]{{4}}X"


@dataclass
class MathVault:
    """감춰 둔 수식들. 파싱이 끝난 문서를 되돌릴 때 쓴다."""

    prefix: str = _TOKEN_PREFIX
    #: 토큰 -> (수식 본문, 블록 여부)
    entries: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.entries

    def token_re(self) -> re.Pattern:
        return re.compile(_TOKEN_RE_TEMPLATE.format(prefix=re.escape(self.prefix)))

    def restore_text(self, text: Optional[str]) -> Optional[str]:
        """문자열 안의 토큰을 수식 원문으로 되돌린다."""
        if not text or self.empty or self.prefix not in text:
            return text

        def _sub(m: re.Match) -> str:
            body, is_block = self.entries.get(m.group(0), (None, False))
            if body is None:
                return m.group(0)
            return f"$${body}$$" if is_block else f"${body}$"

        return self.token_re().sub(_sub, text)

    def sole_block_formula(self, text: Optional[str]) -> Optional[str]:
        """이 문자열이 블록 수식 토큰 하나뿐이면 그 수식 본문을 돌려준다."""
        if not text or self.empty:
            return None
        stripped = text.strip()
        entry = self.entries.get(stripped)
        if entry is None:
            return None
        body, is_block = entry
        return body if is_block else None


def _make_prefix(text: str) -> str:
    """원문과 충돌하지 않는 토큰 접두를 고른다."""
    prefix = _TOKEN_PREFIX
    suffix = 0
    while prefix in text:
        suffix += 1
        prefix = f"{_TOKEN_PREFIX}{suffix}"
    return prefix


def _scan_block(lines: list[str], start: int) -> Optional[tuple[str, int]]:
    """`lines[start]` 에서 시작하는 블록 수식을 읽는다.

    Returns:
        (수식 본문, 마지막 줄 인덱스). 상한 안에서 닫히지 않으면 None.
    """
    head = lines[start].strip()[2:]
    if head.rstrip().endswith("$$") and len(lines[start].strip()) >= 4:
        return head.rstrip()[:-2].strip(), start

    body: list[str] = [head.strip()] if head.strip() else []
    for idx in range(start + 1, min(len(lines), start + 1 + MAX_BLOCK_LINES)):
        line = lines[idx].strip()
        if line.endswith("$$"):
            tail = line[:-2].strip()
            if tail:
                body.append(tail)
            return "\n".join(body).strip(), idx
        body.append(line)
    return None


def protect(text: str) -> tuple[str, MathVault]:
    """수식을 플레이스홀더로 바꾼 텍스트와 되돌리기용 금고를 돌려준다.

    수식이 없으면 원문을 그대로 돌려준다(줄바꿈 문자까지 손대지 않기 위해 조기 반환한다).
    """
    if not text or "$" not in text:
        return text, MathVault()

    vault = MathVault(prefix=_make_prefix(text))
    counter = 0

    def _token() -> str:
        nonlocal counter
        counter += 1
        return f"{vault.prefix}{counter:04d}X"

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    idx = 0
    while idx < len(lines):
        raw = lines[idx]
        if _BLOCK_OPEN_RE.match(raw):
            found = _scan_block([line.rstrip("\n") for line in lines], idx)
            if found is not None:
                body, last = found
                if body:
                    token = _token()
                    vault.entries[token] = (body, True)
                    # 블록 수식은 앞뒤로 빈 줄을 둬 독립 문단으로 남긴다. 문장에 붙으면
                    # 되돌린 뒤에도 문단 하나에 섞여 수식 아이템이 되지 못한다.
                    out.append(f"\n{token}\n\n")
                idx = last + 1
                continue
        out.append(INLINE_MATH_RE.sub(
            lambda m: _register_inline(m, vault, _token), raw
        ))
        idx += 1

    return "".join(out), vault


def _register_inline(match: re.Match, vault: MathVault, make_token) -> str:
    token = make_token()
    vault.entries[token] = (match.group(1), False)
    return token


def restore_document(document: Any, vault: MathVault) -> int:
    """파싱이 끝난 문서에서 토큰을 수식으로 되돌린다.

    블록 수식 하나만 담긴 텍스트 아이템은 `formula` 라벨로 올린다. 그래야 청커가 그것을
    수식으로 알아보고 `$$` 구분자를 붙인다. 라벨만 바꾸는 이유는 docling 타입을 import
    하지 않기 위해서다 — 배포본이 docling 버전에 묶이지 않게 한다.

    Returns:
        되돌린 아이템 수.
    """
    if vault is None or vault.empty or document is None:
        return 0

    restored = 0
    for item in _iter_text_carriers(document):
        text = getattr(item, "text", None)
        if not isinstance(text, str) or vault.prefix not in text:
            continue

        body = vault.sole_block_formula(text)
        if body is not None:
            item.text = body
            _set_formula_label(item)
        else:
            item.text = vault.restore_text(text)
        orig = getattr(item, "orig", None)
        if isinstance(orig, str) and vault.prefix in orig:
            item.orig = vault.restore_text(orig)
        restored += 1

    restored += _restore_tables(document, vault)
    if restored:
        _log.info(f"[md_math] 수식 {restored}건 복원")
    return restored


def _iter_text_carriers(document: Any):
    """텍스트를 들고 있는 아이템들. docling 타입을 모른 채 순회한다."""
    for attr in ("texts", "groups"):
        for item in getattr(document, attr, None) or []:
            yield item


def _restore_tables(document: Any, vault: MathVault) -> int:
    """표 셀 안의 수식도 되돌린다. 셀은 별도 객체라 텍스트 순회에 안 걸린다."""
    count = 0
    for table in getattr(document, "tables", None) or []:
        data = getattr(table, "data", None)
        for cell in getattr(data, "table_cells", None) or []:
            text = getattr(cell, "text", None)
            if isinstance(text, str) and vault.prefix in text:
                cell.text = vault.restore_text(text)
                count += 1
    return count


def _set_formula_label(item: Any) -> None:
    """텍스트 아이템을 수식으로 표시한다. 라벨 타입이 막으면 조용히 건너뛴다."""
    try:
        item.label = "formula"
    except Exception:  # pragma: no cover - 라벨이 Literal 로 고정된 타입
        _log.debug("[md_math] formula 라벨 승격 실패 — 텍스트로 남긴다.")


# ── 로딩 지점에서 쓰는 가드 ───────────────────────────────────────────────────

#: 이 확장자만 감춘다. 다른 포맷은 백엔드가 `$` 를 특별히 다루지 않는다.
MARKDOWN_EXTS = (".md", ".markdown", ".mdown", ".mkd")


class _Guard:
    """`with` 블록 안에서 쓸 파싱 경로와, 블록 뒤에 쓸 복원 함수."""

    def __init__(self, path: str, vault: Optional[MathVault] = None):
        self.path = path
        self.vault = vault

    def restore(self, document: Any) -> int:
        return restore_document(document, self.vault)


@contextmanager
def guard_markdown(file_path: str):
    """markdown 이면 수식을 감춘 임시 파일 경로를, 아니면 원본 경로를 준다.

    임시 파일은 원본과 같은 basename 을 쓴다. docling 은 확장자로 백엔드를 고르고
    `origin.filename` 을 파일명에서 가져오므로, 이름이 바뀌면 그 둘이 함께 어긋난다.

    수식이 없거나 읽을 수 없으면 원본 경로를 그대로 준다 — 파싱 입력이 종전과 같아진다.
    """
    if os.path.splitext(file_path)[1].lower() not in MARKDOWN_EXTS:
        yield _Guard(file_path)
        return

    try:
        source = Path(file_path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        _log.debug(f"[md_math] 입력을 읽지 못해 수식 보호를 건너뛴다: {exc}")
        yield _Guard(file_path)
        return

    protected, vault = protect(source)
    if vault.empty:
        yield _Guard(file_path)
        return

    with tempfile.TemporaryDirectory(prefix="md_math_") as work_dir:
        out_path = Path(work_dir) / Path(file_path).name
        try:
            out_path.write_text(protected, encoding="utf-8")
        except OSError as exc:
            # 파생 파일을 만들지 못하면 보호 없이 원본으로 간다(기존 동작).
            _log.warning(f"[md_math] 임시 파일 생성 실패 — 수식 보호를 건너뛴다: {exc}")
            yield _Guard(file_path)
            return
        _log.info(f"[md_math] 수식 {len(vault.entries)}건 보호 ({Path(file_path).name})")
        yield _Guard(str(out_path), vault)
