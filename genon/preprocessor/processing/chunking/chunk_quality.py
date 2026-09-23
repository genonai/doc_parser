"""이상 청크 판정(chunking.validation).

유효 내용이 없거나, 지나치게 짧거나, 반복·문자 손상이 심한 청크를 찾아 적재 대상에서
뺀다. 기준은 yaml `chunking.validation` 블록이 정하고, 모든 판정은 사람이 읽을 수 있는
근거(측정값·임계값)를 남긴다. 설계와 실측 근거는 genon_docs/chunk_validation_plan.md 다.

판정 순서(앞에 걸리면 그 사유가 대표 사유다)

  1 blank         판정용 텍스트가 공백뿐
  2 no_content    내용 문자(str.isalnum) 0자
  3 broken_chars  손상 문자 개수와 점유율이 모두 기준 이상
  4 repetition    반복 지표 하나가 횟수 기준 이상이고 중복 점유율도 기준 이상
  5 min_chars     내용 문자 수가 유형별 하한 미만

설계 원칙은 "애매하면 통과" 다. 정상 청크를 빼면 그 내용이 검색에서 영구히 사라지지만,
놓친 불량 청크는 다음 조정에서 잡으면 된다. 아래 예외 규칙은 실제 청크 1,727건 실측에서
오탐이 난 뒤 넣은 것이므로 근거 없이 빼지 않는다.

  - 반복 단위에 내용 문자가 없으면 반복으로 세지 않는다(구분선, 표 구분선)
  - 반복은 마크업을 걷어낸 판정용 텍스트에서 센다(HTML 표의 `</td><td>` 반복)
  - 표가 든 청크는 길이 하한을 적용하지 않는다(table_min_chars 로 켤 수 있다)
  - 코드·수식 청크는 길이 하한과 반복 판정을 적용하지 않는다
  - 반복·손상은 횟수와 점유율을 함께 넘어야 한다

검증 키는 요청 파라미터로 바꿀 수 없다. `config_parse.CONFIG_PATH_ALIASES` 에 넣으면 요청 한
줄로 검증을 우회할 수 있게 되므로 넣지 않는다. docling 타입은 import 하지 않는다.
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Optional

from genon.preprocessor.processing.core.errors import GenosServiceException

_log = logging.getLogger(__name__)

ATTR = "_chunk_validation"
ACTIONS = ("report", "drop")
REASONS = ("blank", "no_content", "broken_chars", "repetition", "min_chars")

# 오류 메시지 앞머리. 호출자가 메시지로 원인을 구분한다.
CHUNK_ALL_REJECTED = "CHUNK_ALL_REJECTED"
CHUNK_REJECTED = "CHUNK_REJECTED"
CHUNK_VALIDATION_ERROR = "CHUNK_VALIDATION_ERROR"
_STAGE = "chunk_validation"


@dataclass(frozen=True)
class Config:
    """검증 설정. 기본값은 실측(계획서 3.6)으로 정한 값이다."""
    action: str = "report"          # report(기록만) | drop(제외)
    fail_on_any: bool = False       # drop 모드에서 불량 청크가 하나라도 있으면 문서 실패
    min_chars: int = 4              # 문서·평문 청크 내용 문자 하한
    row_min_chars: int = 0          # 행 청크 하한(FAQ·메뉴 보호를 위해 기본 끔)
    repeat_min_count: int = 10
    repeat_max_share: float = 0.5
    broken_max_share: float = 0.3
    broken_min_count: int = 3
    table_min_chars: bool = False   # 표가 든 청크에도 길이 하한을 적용할지
    by_doc_type: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def drops(self) -> bool:
        return self.action == "drop"


_BOOL_KEYS = ("fail_on_any", "table_min_chars")
_COUNT_KEYS = ("min_chars", "row_min_chars", "repeat_min_count", "broken_min_count")
_SHARE_KEYS = ("repeat_max_share", "broken_max_share")
_VALUE_KEYS = ("action",) + _BOOL_KEYS + _COUNT_KEYS + _SHARE_KEYS


def _parse_value(key: str, value: Any, where: str) -> Any:
    path = f"{where}.{key}"
    if key == "action":
        if value not in ACTIONS:
            raise ValueError(f"{path}: {list(ACTIONS)} 중 하나여야 합니다(지금: {value!r}).")
        return value
    if key in _BOOL_KEYS:
        if not isinstance(value, bool):
            raise ValueError(f"{path}: true/false 여야 합니다(지금: {value!r}).")
        return value
    if key in _COUNT_KEYS:
        minimum = 2 if key == "repeat_min_count" else 0
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{path}: {minimum} 이상의 정수여야 합니다(지금: {value!r}).")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(f"{path}: 0~1 사이의 비율이어야 합니다(지금: {value!r}).")
    return float(value)


def _parse_values(raw: dict, allowed: tuple, where: str) -> dict:
    unknown = sorted(str(k) for k in raw if k not in allowed)
    if unknown:
        raise ValueError(
            f"{where}.{unknown[0]}: 쓸 수 없는 키입니다. 쓸 수 있는 것: {list(allowed)}")
    return {key: _parse_value(key, value, where) for key, value in raw.items()}


def _normalize_doc_type(value: Any) -> str:
    return str(value or "").strip().lower()


def config_from_cfg(chunking_cfg: dict) -> Optional[Config]:
    """yaml `chunking.validation` → Config. 블록이 없거나 enable 이 false 면 None.

    오기입은 요청 때가 아니라 **기동 시** 실패한다(ValueError). enable: false 인 블록도
    검사한다 — 켜는 순간 기동이 실패하는 설정을 미리 드러낸다.
    """
    where = "chunking.validation"
    raw = (chunking_cfg or {}).get("validation")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: object 여야 합니다(예: {{enable: true, action: report}}).")
    enable = raw.get("enable", False)
    if not isinstance(enable, bool):
        raise ValueError(f"{where}.enable: true/false 여야 합니다(지금: {enable!r}).")
    values = _parse_values(
        {k: v for k, v in raw.items() if k not in ("enable", "by_doc_type")},
        _VALUE_KEYS + ("enable", "by_doc_type"), where)
    by_doc_type_raw = raw.get("by_doc_type") or {}
    if not isinstance(by_doc_type_raw, dict):
        raise ValueError(f"{where}.by_doc_type: doc_type 별 object 여야 합니다.")
    by_doc_type: dict = {}
    for doc_type, overrides in by_doc_type_raw.items():
        path = f"{where}.by_doc_type.{doc_type}"
        if not isinstance(overrides, dict):
            raise ValueError(f"{path}: object 여야 합니다(예: {{min_chars: 0}}).")
        by_doc_type[_normalize_doc_type(doc_type)] = _parse_values(overrides, _VALUE_KEYS, path)
    if not enable:
        return None
    return Config(**values, by_doc_type=by_doc_type)


def config_for(owner, doc_type: Any = None) -> Optional[Config]:
    """processor 에 실린 설정에 doc_type 별 덮어쓰기를 적용한다. 꺼져 있으면 None.

    `object.__new__` 로 __init__ 을 우회한 인스턴스에는 속성이 없으므로 꺼진 것으로 본다.
    """
    cfg = getattr(owner, ATTR, None)
    if cfg is None:
        return None
    overrides = cfg.by_doc_type.get(_normalize_doc_type(doc_type))
    return replace(cfg, **overrides) if overrides else cfg


# ---------------------------------------------------------------------------
# 판정
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>\n]{1,200}>")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|", re.M)
_GLYPH_RE = re.compile(r"GLYPH<[^>]*>|GLYPH\w+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES_RE = re.compile(r"[ \t]+")
_WS_RE = re.compile(r"\s+")
_CODE_LABELS = frozenset({"code", "formula"})


@lru_cache(maxsize=8)
def _phrase_re(min_count: int):
    # 길이 4~50자 문구가 min_count 회 이상 연속.
    return re.compile(r"(.{4,50}?)\1{%d,}" % (min_count - 1), re.S)


@dataclass
class Verdict:
    """판정 결과 1건. 여러 사유에 걸려도 청크 1건으로 센다."""
    reason: str
    extra: tuple
    measures: dict
    limits: dict

    def describe(self) -> str:
        """고객에게 보여줄 근거 한 줄."""
        m, lim = self.measures, self.limits
        if self.reason == "blank":
            return "본문 없음"
        if self.reason == "no_content":
            return f"내용 문자 0자(공백 제외 {m['chars']}자)"
        if self.reason == "broken_chars":
            return f"깨진 문자 {m['broken']}자 / {m['raw_chars']}자({m['broken_share']:.0%})"
        if self.reason == "repetition":
            parts = []
            if m["run"]:
                parts.append(f"동일 문자 {m['run']}회 연속")
            if m["line"]:
                parts.append(f"동일 줄 {m['line']}회")
            if m["phrase"]:
                parts.append(f"동일 문구 {m['phrase']}회")
            return ", ".join(parts) + f", 중복 {m['dup_share']:.0%}"
        return f"내용 문자 {m['content']}자 < 하한 {lim['min_chars']}자"


def _has_alnum(text: str) -> bool:
    return any(c.isalnum() for c in text)


def _judged_text(body: str) -> str:
    """판정용 사본: 표 구분선 줄을 빼고 태그와 표 칸 경계를 공백으로 바꾼다."""
    lines = [line for line in body.split("\n") if not _TABLE_SEP_RE.match(line)]
    return _SPACES_RE.sub(" ", _TAG_RE.sub(" ", "\n".join(lines)).replace("|", " "))


def has_table(text: str) -> bool:
    return "<table" in text.lower() or _TABLE_ROW_RE.search(text) is not None


def code_like(chunk) -> bool:
    """docling 청크의 doc_items 라벨에 코드·수식이 있는지. 행·평문 청크는 라벨이 없다."""
    items = getattr(getattr(getattr(chunk, "source", None), "meta", None), "doc_items", None)
    for item in items or ():
        label = getattr(item, "label", "")
        if str(getattr(label, "value", label)).lower() in _CODE_LABELS:
            return True
    return False


def _repetition(judged: str, min_count: int) -> tuple:
    """(최장 연속 문자, 최다 반복 줄, 최다 연속 문구, 중복 구간 문자 수)."""
    dup = 0
    run, prev, best_run = 1, None, 0
    for c in judged:
        run = run + 1 if c == prev else 1
        prev = c
        if c.isalnum() and run >= min_count:
            best_run = max(best_run, run)
    if best_run:
        dup += best_run - 1

    lines = [_WS_RE.sub(" ", line).strip() for line in judged.split("\n") if line.strip()]
    best_line = 0
    for line, count in Counter(lines).items():
        if count >= min_count and _has_alnum(line):
            best_line = max(best_line, count)
            dup += (count - 1) * len(line.replace(" ", ""))

    best_phrase = 0
    for match in _phrase_re(min_count).finditer(judged):
        unit = match.group(1)
        if _has_alnum(unit):
            best_phrase = max(best_phrase, len(match.group(0)) // len(unit))
            dup += len(match.group(0)) - len(unit)
    return best_run, best_line, best_phrase, dup


def measure(text: Optional[str], *, prefix_len: int = 0, repeat_min_count: int = 10) -> dict:
    """판정에 쓰는 측정값. 실측 스크립트가 통과 청크의 분포를 잴 때도 쓴다.

    prefix_len 은 코어가 본문 앞에 붙인 접두(문서 접두·HEADER 경로) 길이다. 측정은 그 뒤
    본문으로 한다. 반복 지표(run/line/phrase)는 repeat_min_count 이상인 것만 센다.
    """
    body = (text or "")[prefix_len:]
    judged = _judged_text(body.strip())
    # 손상 판정은 정규화 전 본문으로 한다. 정규화가 손상 증거(GLYPH<…> 태그)를 지우지 않게.
    broken = (sum(len(m.group(0)) for m in _GLYPH_RE.finditer(body))
              + len(_CTRL_RE.findall(body)) + body.count("\ufffd"))
    raw_chars = sum(1 for c in body if not c.isspace())
    chars = sum(1 for c in judged if not c.isspace())
    run, line, phrase, dup = _repetition(judged, repeat_min_count)
    return {
        "blank": not body.strip(),
        "content": sum(1 for c in judged if c.isalnum()),
        "chars": chars,
        "raw_chars": raw_chars,
        "broken": broken,
        "broken_share": broken / (raw_chars or 1),
        "run": run, "line": line, "phrase": phrase,
        "dup_share": dup / (chars or 1),
        "has_table": has_table(body),
    }


def judge(text: Optional[str], *, kind: str = "docling", code_like: bool = False,
          prefix_len: int = 0, cfg: Config) -> Optional[Verdict]:
    """청크 본문 1건을 판정한다. 통과면 None. 출력 본문은 바꾸지 않는다."""
    m = measure(text, prefix_len=prefix_len, repeat_min_count=cfg.repeat_min_count)
    if m["blank"]:
        return Verdict("blank", (), {"chars": 0}, {})
    if m["content"] == 0:
        return Verdict("no_content", (), {"content": 0, "chars": m["chars"]}, {})

    if kind == "row":
        min_chars = cfg.row_min_chars
    elif code_like or (m["has_table"] and not cfg.table_min_chars):
        min_chars = 0
    else:
        min_chars = cfg.min_chars

    hits = [reason for reason, hit in (
        ("broken_chars", m["broken"] >= cfg.broken_min_count
         and m["broken_share"] >= cfg.broken_max_share),
        ("repetition", not code_like and bool(m["run"] or m["line"] or m["phrase"])
         and m["dup_share"] >= cfg.repeat_max_share),
        ("min_chars", m["content"] < min_chars),
    ) if hit]
    if not hits:
        return None
    measures = {k: (round(v, 3) if isinstance(v, float) else v)
                for k, v in m.items() if k not in ("blank", "has_table")}
    limits = {
        "min_chars": min_chars, "repeat_min_count": cfg.repeat_min_count,
        "repeat_max_share": cfg.repeat_max_share, "broken_min_count": cfg.broken_min_count,
        "broken_max_share": cfg.broken_max_share,
    }
    return Verdict(hits[0], tuple(hits[1:]), measures, limits)


# ---------------------------------------------------------------------------
# 문서 1건의 검증 진행(코어 청커가 쓴다)
# ---------------------------------------------------------------------------

def _error(code: str, message: str) -> GenosServiceException:
    # 재시도로 해결되지 않는 오류다.
    return GenosServiceException(
        "1", f"{code}: {message}", stage=_STAGE, error_type="permanent")


@dataclass
class _Origin:
    """vector_meta 1건이 어느 청크에서 왔는지. 최종 검사가 접두를 빼는 데 쓴다."""
    vector_meta: Any
    prefix: str
    kind: str
    code_like: bool
    index: int
    page: Any
    flagged: bool = False


class Session:
    """문서 1건의 검증. 판정·기록·로그를 맡고 코어는 호출만 한다.

    초기 검사(check_chunk)는 청크가 vector_meta 가 되기 전에, 최종 검사(finalize)는
    edit_output 훅이 끝난 뒤 돌려줄 목록에 건다. 훅이 본문을 바꾸거나 새 vector_meta 를
    만들어도 최종 검사를 거친다.
    """

    def __init__(self, cfg: Config, file_path: str = "", marker_vectors=()):
        self.cfg = cfg
        self.file_name = os.path.basename(file_path or "") or "-"
        self.rejected = 0            # drop 모드에서 제외한 건수
        self.records: list = []      # 판정에 걸린 청크(모드 무관)
        self.n_input = 0
        self._input_pages: Counter = Counter()
        self._origins: dict = {}
        self._markers = {id(v): v for v in marker_vectors}
        self._default_kind = "docling"
        self._flagged_initial = False

    # --- 판정 1건 ---
    def _check(self, text, *, kind, code_like, prefix_len, stage, index, page) -> Optional[Verdict]:
        try:
            verdict = judge(text, kind=kind, code_like=code_like, prefix_len=prefix_len,
                            cfg=self.cfg)
        except Exception as exc:
            if not self.cfg.drops:
                # report 모드는 청크를 빼지 않으므로 판정 실패가 적재 결과를 바꾸지 않는다.
                # 관측용 기능이 적재를 막지 않도록 기록만 하고 통과시킨다.
                _log.error("[chunk_validation] report file=%s index=%s 판정 오류로 이 청크를 "
                           "건너뜁니다: %s", self.file_name, index, exc, exc_info=True)
                return None
            # drop 모드는 검증하지 않은 결과를 돌려주지 않는다.
            raise _error(CHUNK_VALIDATION_ERROR,
                         f"청크 판정 중 오류가 났습니다({self.file_name} #{index}): {exc}") from exc
        if verdict is None:
            return None
        self.records.append({"stage": stage, "index": index, "page": page,
                             "reason": verdict.reason})
        _log.info(
            "[chunk_validation] %s file=%s index=%s page=%s stage=%s reason=%s extra=%s "
            "근거=%s measures=%s limits=%s",
            self.cfg.action, self.file_name, index, page, stage, verdict.reason,
            ",".join(verdict.extra) or "-", verdict.describe(), verdict.measures, verdict.limits)
        if self.cfg.drops:
            if self.cfg.fail_on_any:
                raise _error(CHUNK_REJECTED,
                             f"불량 청크가 있어 문서를 실패 처리합니다({self.file_name} "
                             f"#{index}, {verdict.reason}: {verdict.describe()}).")
            self.rejected += 1
        return verdict

    def check_chunk(self, chunk, index: int) -> bool:
        """초기 검사. 청크를 빼야 하면 True(report 모드는 항상 False)."""
        self.n_input += 1
        self._input_pages[chunk.page] += 1
        self._default_kind = chunk.kind
        verdict = self._check(chunk.text, kind=chunk.kind, code_like=code_like(chunk),
                              prefix_len=0, stage="initial", index=index, page=chunk.page)
        self._flagged_initial = verdict is not None
        return verdict is not None and self.cfg.drops

    def remember(self, vector_meta, chunk, prefix: str, index: int) -> None:
        """chunk_to_vector_meta 가 만든 vector_meta 의 출처를 기록한다.

        접두는 끝 공백을 빼고 기록한다. text_cleanup 의 표현 정리가 본문이 빈 청크에서
        접두 끝 줄바꿈까지 지우므로, 그대로 두면 접두만 남은 청크가 접두와 맞지 않아
        `HEADER:` 줄이 본문으로 판정된다.
        """
        self._origins[id(vector_meta)] = _Origin(
            vector_meta, (prefix or "").rstrip(), chunk.kind, code_like(chunk), index, chunk.page,
            flagged=self._flagged_initial)

    # --- 최종 검사 ---
    def finalize(self, vector_metas) -> list:
        """edit_output 뒤 최종 검사. drop 모드면 걸린 것을 뺀 목록을 돌려준다.

        개수가 줄었으면 호출자가 순번을 다시 맞춘다(vector_meta.refresh_stats). 그 모듈이
        docling 타입을 import 하므로 여기서 부르지 않는다.
        """
        items = list(vector_metas or [])
        kept: list = []
        for position, item in enumerate(items):
            if self._markers.get(id(item)) is item:
                kept.append(item)
                continue
            origin = self._origins.get(id(item))
            if origin is not None and origin.vector_meta is not item:
                origin = None
            if origin is not None and origin.flagged:
                # report 모드에서 초기 검사가 이미 기록한 청크다. 두 번 세지 않는다.
                kept.append(item)
                continue
            text = getattr(item, "text", None)
            text = text if isinstance(text, str) else ""
            # 훅이 본문을 바꿔 접두로 시작하지 않으면 전체로 판정한다(더 관대하다).
            prefix_len = (len(origin.prefix)
                          if origin is not None and origin.prefix
                          and text.startswith(origin.prefix) else 0)
            verdict = self._check(
                text,
                kind=origin.kind if origin else self._default_kind,
                code_like=origin.code_like if origin else False,
                prefix_len=prefix_len, stage="final",
                index=origin.index if origin else getattr(item, "i_chunk_on_doc", position),
                page=origin.page if origin else getattr(item, "i_page", None))
            if verdict is not None and self.cfg.drops:
                if position == 0 and origin is not None and origin.prefix:
                    _log.warning(
                        "[chunk_validation] file=%s 최종 검사에서 첫 청크를 제외했습니다. "
                        "첫 청크 전용 접두는 다시 붙이지 않습니다.", self.file_name)
                continue
            kept.append(item)

        if not kept and items:
            raise all_rejected_error(self)
        self.log_summary(kept)
        return kept

    # --- 기록 ---
    def reason_counts(self) -> dict:
        return dict(Counter(r["reason"] for r in self.records))

    def log_summary(self, survivors) -> None:
        flagged_pages = Counter(r["page"] for r in self.records if r["stage"] == "initial")
        empty_pages = sorted(
            (p for p, n in flagged_pages.items() if n >= self._input_pages.get(p, 0)),
            key=str)
        rate = len(self.records) / self.n_input if self.n_input else 0.0
        _log.info(
            "[chunk_validation] %s file=%s 입력=%d 판정=%d 제외=%d 생존=%d 사유별=%s "
            "판정률=%.1f%% 청크가_모두_걸린_페이지=%s",
            self.cfg.action, self.file_name, self.n_input, len(self.records), self.rejected,
            len(list(survivors)), self.reason_counts(), rate * 100, empty_pages or "-")


def all_rejected_error(session: Session) -> GenosServiceException:
    return _error(
        CHUNK_ALL_REJECTED,
        f"모든 청크가 검증 기준에 걸려 제외됐습니다({session.file_name}, "
        f"입력 {session.n_input}건, 사유별 {session.reason_counts()}).")


def start(owner, job, marker_vectors=()) -> Optional[Session]:
    """문서 1건의 검증을 시작한다. 검증이 꺼져 있으면 None."""
    cfg = config_for(owner, getattr(job, "doc_type", None))
    if cfg is None:
        return None
    return Session(cfg, getattr(job, "file_path", ""), marker_vectors)
