"""구분자 텍스트 원천(`source.pre.delimited`) 단위 테스트.

고정하는 것은 둘이다.

1. 설정 정규화(`parse_spec`) — 잘못된 값이 기동 때 걸린다.
2. 레코드 경계 — 인용 안 개행으로 여러 물리 줄에 걸친 본문이 **글자 하나까지** 복원된다.

2번은 건수만 세면 통과해 버리는 회귀가 있었다. 인용 열림 판정의 부호가 뒤집혔을 때
레코드 건수는 그대로였고(구분자가 없는 이어지는 줄은 조용히 건너뛰어졌다) 본문만
1/40 로 잘렸다. 그래서 아래 단정은 **본문 내용 자체**를 원본과 대조한다.
"""

import pytest

from genon.preprocessor.processing.converters.delimited_text import (
    DelimitedSpec,
    parse_spec,
    read_records,
)

pytestmark = pytest.mark.unit

COLUMNS = ("대분류", "중분류", "소분류", "제목", "내용")
SPEC = DelimitedSpec(separator="|@|", columns=COLUMNS)

# 캡쳐 원천과 같은 모양: 인용된 마지막 필드에 개행과 이스케이프된 따옴표가 들어 있다.
HTML = '<table style="width: 100%">\n<tr>\n<td>구 분</td>\n</tr>\n</table>'


def _write(tmp_path, text):
    path = tmp_path / "sample.dtms"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _encode(fields):
    head, body = fields[:-1], fields[-1]
    return "|@|".join(head) + '|@|"' + body.replace('"', '""') + '"'


# ── 설정 정규화 ──────────────────────────────────────────────────────────────

def test_parse_spec_없으면_None():
    assert parse_spec(None) is None


def test_parse_spec_기본값():
    spec = parse_spec({"separator": "|@|", "columns": list(COLUMNS)})
    assert spec.separator == "|@|"
    assert spec.columns == COLUMNS
    assert spec.quote == '"'
    assert spec.encoding is None


@pytest.mark.parametrize("cfg", [
    {"columns": ["a"]},                                   # separator 누락
    {"separator": "|@|"},                                 # columns 누락
    {"separator": "|@|", "columns": "abc"},               # 문자열은 목록이 아니다
    {"separator": "|@|", "columns": ["a", ""]},           # 빈 이름
    {"separator": "|@|", "columns": ["a", "a"]},          # 중복 이름
    {"separator": "|@|", "columns": ["a"], "quote": "``"},  # 인용부호는 1글자
])
def test_parse_spec_잘못된_값은_기동때_실패(cfg):
    with pytest.raises(ValueError):
        parse_spec(cfg)


# ── 레코드 경계 ──────────────────────────────────────────────────────────────

def test_인용_안_개행은_레코드를_끊지_않는다(tmp_path):
    """여러 물리 줄에 걸친 본문이 한 레코드로 복원되고 내용이 원본과 같다."""
    path = _write(tmp_path, _encode(["자동차", "담보", "", "제목", HTML]) + "\n")
    records = read_records(path, SPEC)

    assert len(records) == 1
    # 건수가 아니라 내용을 본다 — 이 단정이 본문 소실 회귀를 잡는다.
    assert records[0]["내용"] == HTML
    assert records[0]["소분류"] == ""          # 빈 필드가 사라지지 않는다


def test_여러_레코드가_각각_복원된다(tmp_path):
    rows = [
        ["자동차", "담보", "", "제목1", HTML],
        ["화재", "보상", "누수", "제목2", "<p>한 줄 본문</p>"],
        ["일반", "계약", "해지", "제목3", HTML],
    ]
    path = _write(tmp_path, "\n".join(_encode(r) for r in rows) + "\n")
    records = read_records(path, SPEC)

    assert len(records) == 3
    assert [r["대분류"] for r in records] == ["자동차", "화재", "일반"]
    assert [r["내용"] for r in records] == [HTML, "<p>한 줄 본문</p>", HTML]


def test_본문_안의_구분자는_필드를_더_쪼개지_않는다(tmp_path):
    body = "<p>표기 |@| 는 본문에도 나온다</p>"
    path = _write(tmp_path, _encode(["A", "B", "C", "제목", body]) + "\n")
    records = read_records(path, SPEC)

    assert len(records) == 1
    assert records[0]["내용"] == body
    assert records[0]["제목"] == "제목"


def test_이스케이프된_따옴표가_원래대로_돌아온다(tmp_path):
    body = '<div class="box">인용 "강조" 부분</div>'
    path = _write(tmp_path, _encode(["A", "B", "C", "제목", body]) + "\n")
    assert read_records(path, SPEC)[0]["내용"] == body


def test_인용되지_않은_본문도_받는다(tmp_path):
    path = _write(tmp_path, "A|@|B|@|C|@|제목|@|평문 본문\n")
    records = read_records(path, SPEC)

    assert len(records) == 1
    assert records[0]["내용"] == "평문 본문"


def test_헤더_행은_설정으로만_건너뛴다(tmp_path):
    text = "대분류|@|중분류|@|소분류|@|제목|@|내용\n" + _encode(["A", "B", "C", "T", "본문"]) + "\n"
    path = _write(tmp_path, text)

    assert len(read_records(path, SPEC)) == 2            # 기본은 데이터로 본다
    skipping = DelimitedSpec(separator="|@|", columns=COLUMNS, skip_header=True)
    records = read_records(path, skipping)
    assert len(records) == 1
    assert records[0]["내용"] == "본문"


def test_cp949_원천도_읽는다(tmp_path):
    path = tmp_path / "cp949.dtms"
    path.write_text(_encode(["자동차", "담보", "", "제목", "<p>한글 본문</p>"]) + "\n",
                    encoding="cp949")
    records = read_records(str(path), SPEC)

    assert len(records) == 1
    assert records[0]["내용"] == "<p>한글 본문</p>"
