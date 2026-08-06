"""HWP 표 구조가 최종 청크까지 살아남는지 회귀 검증 (이슈 #148).

sample_files/hwp_sample_table.hwp (한국투자공사 여비세칙, 표 9개 / 페이지 섹션 3개) 를
두 전처리기로 끝까지 돌린 뒤, 최종 청크 텍스트 안의 표 두 개를 구조적으로 검증한다.

  - 별표 제3호 숙박급지 (5행 x 4열, 세로 페이지)
    갑지/을지/병지 컬럼 경계가 무너지지 않는지 확인한다.
    (버그: 병지 셀의 '말레이시아' 가 을지 로 읽혀 AI 가 '을지' 라고 오답했음)

  - 별표 제4호 국외출장·연수 상세일정 (7행 x 7열, **가로(landscape) 페이지**)
    맨 오른쪽 '법인카드 사용여부' 컬럼이 잘려나가지 않는지 확인한다.
    (버그: 가로 페이지가 잘려 해당 컬럼 내역이 청크에 아예 없었음)

두 경로는 HWP 처리 방식과 표 출력 형식이 다르므로 각각 검증한다.

  - 첨부용(attachment) : 자체 개발 parser 로 직접 파싱(PDF 변환 없음) → **markdown** 표
  - 적재용(intelligent): rhwp 로 PDF 변환 후 dots.ocr 레이아웃 파싱 → **HTML** 표

golden-file 스냅샷이 아니라 의미 기반(semantic) assert 만 사용한다. 셀 텍스트의 사소한
변화(공백/줄바꿈/셀 병합 복제)에는 둔감하고, 컬럼 개수·컬럼 소속에는 민감하도록 설계했다.

무거운 의존성(docling / HWP SDK) 미설치 환경에서는 모듈 단위로 skip 된다(GitHub CI 에서는 실행).
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path

import pytest

# 무거운 의존성 미설치 환경(로컬 macOS 등)에서는 파일 전체 skip.
# monkeypatch 대상 모듈 객체가 필요하므로 fixture 가 아니라 모듈 단위로 import 한다.
attachment = pytest.importorskip("facade.attachment_processor")

from genon.preprocessor.converters.hwp_to_pdf import convert_hwp_to_pdf  # noqa: E402
from genon.preprocessor.converters.hwp_to_pdf.config import _AVAILABILITY  # noqa: E402

_PREPROC = Path(__file__).resolve().parents[2]
SAMPLE = _PREPROC / "sample_files" / "hwp_sample_table.hwp"
RESOURCE_DIR = _PREPROC / "resource"
RESOURCE_DEV_DIR = _PREPROC / "resource_dev"

pytestmark = [
    pytest.mark.regression,
    pytest.mark.skipif(not SAMPLE.exists(), reason="hwp_sample_table.hwp 없음"),
]


# ---------------------------------------------------------------------------
# 텍스트 정규화
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    """공백류(개행 포함)를 단일 스페이스로 축약."""
    return _WS.sub(" ", (text or "").replace("\u00a0", " ").replace("\u3000", " ")).strip()


def _squash(text: str) -> str:
    """비교용 정규화: 모든 공백 제거.

    HWP 셀은 BeautifulSoup get_text(strip=True) 로 만들어져 줄바꿈이 구분자 없이
    이어붙는 경우가 있고(예: '말레이시아브루나이태국, 필리핀'), markdown 직렬화
    단계에서는 '\\n' 이 공백으로 치환된다. 공백을 모두 제거해 두 경우를 동일하게 본다.
    """
    return _WS.sub("", (text or "").replace("\u00a0", " ").replace("\u3000", " "))


# ---------------------------------------------------------------------------
# markdown 표 파싱 (첨부용)
# ---------------------------------------------------------------------------

# 압축(-, :-, -:, :-:) / 패딩(------) 구분선 셀을 모두 인식
_SEP_CELL = re.compile(r"^:?-+:?$")


def _split_row(line: str) -> list[str]:
    """'| a | b |' -> ['a', 'b'] (docling _compact_table 분해의 역연산)."""
    return [c.strip() for c in line.split("|")[1:-1]]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL.match(c) for c in cells)


def _extract_md_tables(md: str) -> list[list[list[str]]]:
    """markdown 텍스트에서 파이프 표 블록들을 [표][행][셀] 로 추출.

    연속된 '|...|' 줄을 한 표로 묶고, 두 번째 줄이 구분선이면 제거한다.
    데이터 셀이 우연히 '-' 인 경우를 지우지 않도록 구분선 판정은 index 1 에만 적용.
    """
    blocks: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for raw in md.splitlines():
        line = raw.strip()
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 2:
            cur.append(_split_row(line))
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)

    out: list[list[list[str]]] = []
    for block in blocks:
        if len(block) >= 2 and _is_separator(block[1]):
            block = [block[0]] + block[2:]
        out.append(block)
    return out


# ---------------------------------------------------------------------------
# HTML 표 파싱 (적재용)
# ---------------------------------------------------------------------------

_TABLE_RE = re.compile(r"<table\b.*?</table>", re.S | re.I)


def _parse_html_grids(text: str) -> list[list[list[str]]]:
    """청크 텍스트 안의 모든 <table> 을 rowspan/colspan 전개된 grid 로 변환.

    docling HTML 출력은 <thead> 없이 <tbody><tr><th|td> 이고, 병합에 가려진 셀은
    아예 생략되므로 점유 맵으로 전개해야 컬럼 인덱스가 맞는다.
    """
    from bs4 import BeautifulSoup

    grids: list[list[list[str]]] = []
    for match in _TABLE_RE.finditer(text):
        soup = BeautifulSoup(match.group(0), "html.parser")
        rows = soup.find_all("tr")
        occupied: dict[tuple[int, int], str] = {}
        for r, tr in enumerate(rows):
            c = 0
            for cell in tr.find_all(["td", "th"]):
                while (r, c) in occupied:
                    c += 1
                try:
                    rowspan = max(int(cell.get("rowspan", 1) or 1), 1)
                    colspan = max(int(cell.get("colspan", 1) or 1), 1)
                except (TypeError, ValueError):
                    rowspan = colspan = 1
                value = _norm(cell.get_text(separator=" "))
                for dr in range(rowspan):
                    for dc in range(colspan):
                        occupied[(r + dr, c + dc)] = value
                c += colspan
        if not occupied:
            continue
        ncols = max(col for _, col in occupied) + 1
        grids.append(
            [[occupied.get((r, c), "") for c in range(ncols)] for r in range(len(rows))]
        )
    return grids


# ---------------------------------------------------------------------------
# 표 탐색 / assert 헬퍼 (두 경로 공용)
# ---------------------------------------------------------------------------


def _dump(table: list[list[str]]) -> str:
    return "\n".join("| " + " | ".join(row) + " |" for row in table)


def _headers_of(tables: list[list[list[str]]]) -> str:
    return "\n".join(f"  [{i}] {t[0] if t else []}" for i, t in enumerate(tables))


def _find_table(
    tables: list[list[list[str]]],
    *,
    header_kws: tuple[str, ...] = (),
    body_kws: tuple[str, ...] = (),
) -> list[list[str]]:
    """헤더/본문 키워드로 표 1개를 특정한다. 0개거나 2개 이상이면 진단과 함께 실패.

    검증 대상 컬럼명(예: '법인카드 사용여부')은 탐색 키워드로 쓰지 않는다 —
    버그가 재발하면 '표를 못 찾음' 이 아니라 '컬럼이 없음' 으로 실패해야 하기 때문.
    """

    def hit(table: list[list[str]]) -> bool:
        head = _squash(" ".join(table[0]))
        body = _squash(" ".join(c for row in table[1:] for c in row))
        return all(_squash(k) in head for k in header_kws) and all(
            _squash(k) in body for k in body_kws
        )

    matched = [t for t in tables if t and hit(t)]
    assert len(matched) == 1, (
        f"표 특정 실패: header={header_kws} body={body_kws} -> {len(matched)}개 매치.\n"
        f"문서 내 표 {len(tables)}개의 헤더 목록:\n{_headers_of(tables)}"
    )
    return matched[0]


def _header_texts(table: list[list[str]], n_header_rows: int) -> list[str]:
    """헤더가 여러 줄로 쪼개져도 견디도록 컬럼별로 헤더 행들을 이어붙인다.

    별표 제4호의 헤더 셀은 원본에서 '방문'/'기관', '접촉예정'/'인물',
    '법인카드'/'사용여부' 처럼 두 런(run)에 나뉘어 저장돼 있다.
    """
    ncols = len(table[0])
    return [
        _norm(" ".join(table[r][j] for r in range(n_header_rows) if j < len(table[r])))
        for j in range(ncols)
    ]


def _col_index(header: list[str], keyword: str) -> int:
    """헤더에서 keyword 에 해당하는 컬럼 인덱스를 1개로 특정. 인덱스 하드코딩 금지.

    정확일치 → 접두일치 → 부분일치 순으로 좁혀간다. 단순 부분일치만 쓰면
    '갑지' 가 '갑지' 와 '을지 (※ 갑지 제외)' 양쪽에 걸려 정상 표에서도 실패한다.
    """
    kw = _squash(keyword)
    cells = [_squash(c) for c in header]
    for hits in (
        [i for i, c in enumerate(cells) if c == kw],
        [i for i, c in enumerate(cells) if c.startswith(kw)],
        [i for i, c in enumerate(cells) if kw in c],
    ):
        if len(hits) == 1:
            return hits[0]
    raise AssertionError(
        f"헤더에서 '{keyword}' 컬럼을 1개로 특정하지 못함. header={header}"
    )


def _find_row(table: list[list[str]], keyword: str) -> list[str]:
    rows = [r for r in table[1:] if _squash(keyword) in _squash(r[0])]
    assert len(rows) == 1, (
        f"'{keyword}' 로 시작하는 행을 1개로 특정 실패({len(rows)})\n{_dump(table)}"
    )
    return rows[0]


def _assert_sukbak(table: list[list[str]], *, expected_rows: int = 5) -> None:
    """별표 제3호 숙박급지 — 갑지/을지/병지 컬럼 경계 보존 (이슈 #148 본체)."""
    assert len(table) == expected_rows, (
        f"행 수 {len(table)} != {expected_rows}\n{_dump(table)}"
    )
    widths = {len(r) for r in table}
    assert widths == {4}, f"행별 컬럼 수 {widths} != {{4}}\n{_dump(table)}"

    hdr = _header_texts(table, 1)
    j_gu = _col_index(hdr, "구분")
    j_gap = _col_index(hdr, "갑지")
    j_eul = _col_index(hdr, "을지")
    j_byeong = _col_index(hdr, "병지")
    assert [j_gu, j_gap, j_eul, j_byeong] == [0, 1, 2, 3], f"헤더 컬럼 순서 이상: {hdr}"

    row = _find_row(table, "아시아주")
    byeongji = _squash(row[j_byeong])
    eulji = _squash(row[j_eul])
    gapji = _squash(row[j_gap])

    # (1) 문제의 국가들이 병지 컬럼에 실제로 담겨 있어야 한다
    for country in ("말레이시아", "브루나이", "태국", "필리핀"):
        assert country in byeongji, (
            f"'{country}' 가 아시아주 행의 병지 컬럼에 없음. "
            f"병지={row[j_byeong]!r}\n{_dump(table)}"
        )

    # (2) 같은 국가가 을지/갑지로 새어 들어가면 안 된다 ← AI 가 '을지' 라고 답한 원인
    for country in ("말레이시아", "브루나이"):
        assert country not in eulji, (
            f"'{country}' 가 을지 컬럼으로 새어 들어감(컬럼 경계 붕괴). "
            f"을지={row[j_eul]!r}\n{_dump(table)}"
        )
        assert country not in gapji, (
            f"'{country}' 가 갑지 컬럼으로 새어 들어감(컬럼 경계 붕괴). "
            f"갑지={row[j_gap]!r}\n{_dump(table)}"
        )

    # (3) 양성 대조 — 빈 셀이라서 (2)가 통과하는 상황을 막는다.
    #     '싱가포르' 는 갑지에만, '대만' 은 을지에만 등장한다(실측 확인).
    #     일본/아제르바이잔/호주/터키 등은 갑지·을지 양쪽에 나와 판별자로 쓸 수 없다.
    assert "대만" in eulji, f"을지 컬럼에 '대만' 이 없음: {row[j_eul]!r}\n{_dump(table)}"
    assert "싱가포르" in gapji, (
        f"갑지 컬럼에 '싱가포르' 가 없음: {row[j_gap]!r}\n{_dump(table)}"
    )


_ILJUNG_HEADERS = (
    ("구분",),
    ("일시",),
    ("장소",),
    ("방문", "기관"),
    ("접촉예정", "인물"),
    ("업무수행",),
    ("법인카드", "사용여부"),
)


def _assert_iljung(table: list[list[str]]) -> None:
    """별표 제4호 상세일정 — 가로 페이지 표의 최우측 컬럼 유실 방지."""
    assert len(table) == 7, f"행 수 {len(table)} != 7\n{_dump(table)}"
    widths = {len(r) for r in table}
    assert widths == {7}, (
        f"행별 컬럼 수 {widths} != {{7}} — 가로 페이지 표의 컬럼이 잘렸을 수 있음\n"
        f"{_dump(table)}"
    )

    hdr = _header_texts(table, 1)
    for j, keywords in enumerate(_ILJUNG_HEADERS):
        for kw in keywords:
            assert _squash(kw) in _squash(hdr[j]), (
                f"{j}번 컬럼 헤더에 '{kw}' 없음: {hdr[j]!r} (전체: {hdr})"
            )

    # 이슈의 직접 증상: 최우측 컬럼 생존을 한 번 더 명시적으로 확인
    assert _squash("법인카드사용여부") in _squash(hdr[-1]), (
        f"마지막 컬럼이 '법인카드 사용여부' 가 아님: {hdr!r}\n{_dump(table)}"
    )


# ---------------------------------------------------------------------------
# 공용 호출 헬퍼
# ---------------------------------------------------------------------------


class _DummyRequest:
    """facade __call__ 이 요구하는 최소 request (await is_disconnected() 만 사용)."""

    async def is_disconnected(self) -> bool:
        return False


def _chunk_texts(vectors) -> list[str]:
    out: list[str] = []
    for v in vectors:
        if hasattr(v, "model_dump"):
            out.append(v.model_dump().get("text", ""))
        elif isinstance(v, dict):
            out.append(v.get("text", ""))
        else:
            out.append(getattr(v, "text", ""))
    return out


def _isolated_sample(tmp_dir: Path) -> Path:
    """샘플을 tmp 로 복사. 변환기/이미지 저장이 입력 파일 옆에 산출물을 남기기 때문."""
    dst = tmp_dir / SAMPLE.name
    shutil.copy2(SAMPLE, dst)
    return dst


# ---------------------------------------------------------------------------
# 첨부용(attachment) — markdown 표
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def attachment_chunks(tmp_path_factory) -> list[str]:
    """hwp_sample_table.hwp 를 첨부용 프로세서로 끝까지 처리한 최종 청크 텍스트.

    - 배포 config(resource/) 를 명시 지정한다. 인자 없이 만들면 resource_dev/ 가 잡힌다.
    - 프로덕션 기본 kwargs 그대로(chunker_type=recursive, chunk_size=1000000) 호출한다.
      실제 배포 설정에서 표가 살아남는지가 검증 대상이므로 테스트가 chunk_size 를 키우지 않는다.
    - convert_to_pdf 를 차단한다: HWP 네이티브 파싱이 실패하면 __call__ 이 조용히 PDF 변환
      폴백으로 새서 '그럴듯한 다른 결과' 가 통과해버린다.
    """
    work_dir = tmp_path_factory.mktemp("hwp_table_attachment")
    work = _isolated_sample(work_dir)

    dp = attachment.DocumentProcessor(
        config_path=str(RESOURCE_DIR / "attachment_processor_config.yaml")
    )

    def _no_pdf_fallback(*args, **kwargs):
        raise AssertionError(
            "HWP 네이티브 파싱이 실패해 PDF 변환 폴백으로 진입했습니다 "
            "(HWP SDK/convtext 환경 확인 필요). 이 테스트는 네이티브 파싱 결과만 검증합니다."
        )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(attachment, "convert_to_pdf", _no_pdf_fallback, raising=False)
        vectors = asyncio.run(dp(_DummyRequest(), str(work), save_images=False))

    assert isinstance(vectors, list) and vectors, "최종 벡터가 비어 있음"
    return _chunk_texts(vectors)


@pytest.fixture(scope="module")
def attachment_tables(attachment_chunks) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for text in attachment_chunks:
        tables.extend(_extract_md_tables(text))
    assert tables, "최종 청크에서 markdown 표를 하나도 찾지 못했습니다"
    return tables


class TestAttachmentHwpTableStructure:
    """첨부용 전처리기 — 최종 청크 markdown 표의 구조 검증."""

    def test_표가_청크경계로_분할되지_않음(self, attachment_chunks):
        """검증 대상 표가 청크 경계로 쪼개지지 않았는지 (chunk_size=1000000 전제).

        표가 쪼개지면 이후 구조 assert 가 '표를 못 찾음' 으로 실패해 원인 파악이
        어려워지므로 여기서 먼저 원인을 특정한다. RAG 품질 관점에서도 표 분할 자체가
        회귀 신호다.
        """
        for label, kw in (("별표 제3호", "말레이시아"), ("별표 제4호", "접촉예정")):
            holders = [
                i for i, t in enumerate(attachment_chunks) if _squash(kw) in _squash(t)
            ]
            assert len(holders) == 1, (
                f"{label} 표('{kw}')를 담은 청크가 {len(holders)}개 "
                f"(전체 청크 {len(attachment_chunks)}개, 길이={[len(t) for t in attachment_chunks]}). "
                f"0개면 표 유실, 2개 이상이면 청크 경계로 분할된 것."
            )

    def test_별표3_숙박급지_표구조(self, attachment_tables):
        table = _find_table(
            attachment_tables,
            header_kws=("구분", "갑지", "을지"),
            body_kws=("아시아주",),
        )
        _assert_sukbak(table)

    def test_별표4_상세일정_표구조(self, attachment_tables):
        table = _find_table(attachment_tables, header_kws=("일시", "방문", "접촉예정"))
        _assert_iljung(table)

    def test_문서_표가_모두_남아있음(self, attachment_tables):
        """원본 표가 최종 청크에 남아 있는지 (파서 폴백/대량 누락 조기 감지).

        원본은 표 9개지만 그중 하나는 1x1 안내문 표라 텍스트로 렌더링될 수 있어
        기준을 8개로 둔다. 정밀 검증은 별표3/별표4 전용 테스트가 담당한다.
        """
        assert len(attachment_tables) >= 8, (
            f"표 {len(attachment_tables)}개 (기대 8개 이상):\n"
            f"{_headers_of(attachment_tables)}"
        )


# ---------------------------------------------------------------------------
# 적재용(intelligent) — HTML 표
# ---------------------------------------------------------------------------


def _intelligent_config(tmp_dir: Path) -> str:
    """운영 경로(genos_layout)를 유지하되 표 추출과 무관한 외부 호출만 차단한 임시 config.

    주의: config_path 를 반드시 넘겨야 한다. 인자 없는 DocumentProcessor() 는
    resource_dev/ 를 집는데 거기에는 실제 엔드포인트와 API 키가 들어 있다.
    """
    import yaml

    base = RESOURCE_DEV_DIR / "intelligent_processor_config.yaml"
    if not base.exists():
        base = RESOURCE_DIR / "intelligent_processor_config.yaml"
    cfg = yaml.safe_load(base.read_text(encoding="utf-8")) or {}

    layout = cfg.setdefault("layout", {})
    layout["layout_model_type"] = "genos_layout"  # 운영과 동일한 표 추출기(dots.ocr)
    genos_layout = layout.setdefault("genos_layout", {})
    endpoint = os.environ.get("GENOS_LAYOUT_ENDPOINT")
    if endpoint:
        genos_layout["endpoint"] = endpoint
    api_key = os.environ.get("GENOS_LAYOUT_API_KEY")
    if api_key is not None:
        genos_layout["api_key"] = api_key

    # 표 추출과 무관한 LLM 호출(toc/metadata/summary/table_desc) 전면 차단
    cfg["enrichment"] = []
    cfg.setdefault("output", {})["table_format"] = "html"
    cfg["guardrail"] = {
        "url": "",
        "workflow_id": None,
        "api_key": "",
        "masking_enabled": False,
    }

    out = tmp_dir / "intelligent_processor_config.yaml"
    out.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return str(out)


@pytest.fixture(scope="module")
def intelligent_chunks(tmp_path_factory) -> list[str]:
    """hwp_sample_table.hwp 를 적재용 프로세서로 끝까지 처리한 최종 청크 텍스트.

    - rhwp 로 먼저 프로브해 변환 가능 여부를 확인한다. 그래야 libreoffice 로 조용히
      폴백된 결과를 rhwp 결과로 착각하지 않는다.
    - table_as_chunk=True + chunk_size=0 으로 '표 1개 = 청크 1개, 행 분할 없음' 을 보장한다.
    """
    intelligent = pytest.importorskip("facade.intelligent_processor")
    pytest.importorskip("bs4")

    work_dir = tmp_path_factory.mktemp("hwp_table_intelligent")

    probe = work_dir / ("probe_" + SAMPLE.name)
    shutil.copy2(SAMPLE, probe)
    if convert_hwp_to_pdf(str(probe), primary="rhwp", disable_fallback=True) is None:
        pytest.skip("rhwp 가 이 샘플을 변환하지 못함")

    src = _isolated_sample(work_dir)
    dp = intelligent.DocumentProcessor(config_path=_intelligent_config(work_dir))

    vectors = asyncio.run(
        dp(
            _DummyRequest(),
            str(src),
            use_pdf_sdk=False,      # HWP 체인을 rhwp → libreoffice 로 고정
            table_as_chunk=True,    # 표 1개 = 청크 1개
            chunk_size=0,           # max_tokens=0 → 표를 행 단위로 쪼개지 않음
            img_desc=0,
            chart_desc=0,
            doc_summary=0,
            table_desc=0,
            table_refine=0,
            toc=0,
        )
    )
    assert isinstance(vectors, list) and vectors, "최종 벡터가 비어 있음"
    texts = _chunk_texts(vectors)

    # 글리프 깨짐은 skip 하지 않고 명확히 실패시킨다(실제 회귀를 숨기지 않기 위해).
    broken = sum(1 for t in texts if "GLYPH<" in t)
    if broken > len(texts) // 2:
        pytest.fail(
            f"PDF 텍스트 추출이 글리프로 깨짐({broken}/{len(texts)} 청크) — "
            f"폰트/ToUnicode 문제로 표 내용 검증 불가"
        )
    return texts


@pytest.fixture(scope="module")
def intelligent_tables(intelligent_chunks) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for text in intelligent_chunks:
        tables.extend(_parse_html_grids(text))
    assert tables, "최종 청크에서 HTML 표를 하나도 찾지 못했습니다"
    return tables


class TestIntelligentHwpTableStructure:
    """적재용 전처리기 — 최종 청크 HTML 표의 구조 검증.

    운영과 동일한 경로(rhwp 변환 + genos_layout/dots.ocr)에서만 실행한다.
    """

    pytestmark = [
        pytest.mark.skipif(
            not _AVAILABILITY["rhwp"](),
            reason="rhwp backend 없음 (도커 이미지에서만 가용)",
        ),
        pytest.mark.skipif(
            not os.environ.get("GENOS_LAYOUT_AVAILABLE"),
            reason="GENOS_LAYOUT_AVAILABLE 미설정 — dots.ocr layout 엔드포인트 없음",
        ),
    ]

    def test_표가_HTML로_직렬화됨(self, intelligent_chunks, intelligent_tables):
        """배선 sanity — 이후 실패의 원인을 명확히 하기 위한 가드."""
        assert any("<table" in t for t in intelligent_chunks), (
            "청크 텍스트에 HTML 표가 없음 (output.table_format 회귀?)"
        )
        assert len(intelligent_tables) >= 5, (
            f"표 grid 수 부족: {len(intelligent_tables)}\n{_headers_of(intelligent_tables)}"
        )

    def test_별표3_숙박급지_표구조(self, intelligent_tables):
        table = _find_table(
            intelligent_tables,
            header_kws=("구분", "갑지", "을지"),
            body_kws=("아시아주",),
        )
        # rhwp PDF에서 별표 제3호는 두 페이지로 나뉜다. dots.ocr는 페이지별로
        # 표를 추출하므로 여기서는 첫 페이지의 헤더 + 본문 3행만 검증한다.
        _assert_sukbak(table, expected_rows=4)

    def test_별표4_상세일정_표구조(self, intelligent_tables):
        table = _find_table(intelligent_tables, header_kws=("일시", "방문", "접촉예정"))
        _assert_iljung(table)

    def test_별표4_행레이블_1_6(self, intelligent_tables):
        """별표 제4호는 빈 양식이라 1~6 행 레이블만 존재한다(구조 오인식 감지)."""
        table = _find_table(intelligent_tables, header_kws=("일시", "방문", "접촉예정"))
        labels = [_norm(row[0]) for row in table[1:]]
        assert labels == ["1", "2", "3", "4", "5", "6"], (
            f"행 레이블 회귀: {labels}\n{_dump(table)}"
        )


# ---------------------------------------------------------------------------
# 변환 단계 분리 검증 — '변환이 깨진 것' 과 '파싱/청킹이 깨진 것' 을 구분
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _AVAILABILITY["rhwp"](), reason="rhwp backend 없음 (도커 이미지에서만 가용)"
)
def test_rhwp_pdf_페이지_지오메트리(tmp_path):
    """rhwp 변환 결과가 가로 페이지를 보존하는지 (docling 없이 수 초).

    실측 기준: 18페이지 중 정확히 1페이지가 가로(841.88 x 595.28).
    별표 제4호가 그 가로 페이지에 있으므로, 이 검사가 깨지면 컬럼 잘림의 원인이
    파싱이 아니라 변환 단계임을 즉시 알 수 있다.
    """
    PdfReader = pytest.importorskip("pypdf").PdfReader

    work = _isolated_sample(tmp_path)
    out = convert_hwp_to_pdf(str(work), primary="rhwp", disable_fallback=True)
    assert out is not None, "rhwp 변환 실패"

    pages = PdfReader(out).pages
    assert len(pages) == 18, f"페이지 수 {len(pages)} != 18"

    landscape = [
        i for i, p in enumerate(pages) if p.mediabox.width > p.mediabox.height
    ]
    assert len(landscape) == 1, (
        f"가로 페이지가 1개가 아님: {landscape} (전체 {len(pages)}페이지) — "
        f"HWP 의 landscape 섹션이 PDF 변환에서 유실됨"
    )
