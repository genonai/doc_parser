#!/usr/bin/env python3
"""monimo_stock_insight_split_sample.txt 재생성 — 2026-10-01 현장 캡처(split_sample.txt) 재현.

현장 원천 `SSS_S_FDA_GADACTBSSS001_GADACTBSSS001_20260915_00002.txt` 의 앞 36행을 캡처에서
옮겼다. 종목·종목코드·등록번호·게시물 라인번호·뉴스일자·기타 코드와 **행 순서**는 캡처와
같고, 세부내용은 캡처에 보이는 문장을 살려 JSON 으로 다시 구성했다(중간 부분은 캡처에
보이지 않으므로 같은 투로 채웠다).

이 샘플이 고정하는 원천의 특징은 다음과 같다.

1. 같은 종목·일자의 행이 떨어져 온다. 테슬라(4556)는 엔비디아를 사이에 두고 1~3행과
   4~7행으로 갈린다.
2. 같은 종목·일자가 등록번호를 달리해 두 번 온다. 마이크로클라우드 홀로그램은 4637 의
   1·2·3·5행과 4721 의 4·6행만 있고, 둘 사이에 다른 종목이 끼어 있다. 라인번호는 등록번호마다
   따로 매겨진다. 캡처의 검색 결과도 6건이라 빠진 행(4637 의 4행, 4721 의 1·2·3·5행)은
   원천에 실제로 없다. 그래서 두 문서 모두 JSON 으로 복원되지 않는다.

기존 종목 4건 샘플(make_stock_insight_sample.py)은 HTML·평문 판별을 고정하므로 따로 둔다.

실행:  genon/preprocessor/.venv/bin/python tools/parse_chunk/make_stock_insight_split_sample.py
"""
from __future__ import annotations

import json
from pathlib import Path

from make_stock_insight_sample import _KEY_RE, BR, SEPARATOR

OUT = (Path(__file__).resolve().parents[2] / "sample_files" / "monimo"
       / "monimo_stock_insight_split_sample.txt")

NEWS_DATE = "20260821"

# (종목명, 종목코드, 등록번호, 조각 수, md_stck_itm_c, kosc_stck_itm_c, 세부내용)
DOCS = {
    "4556": ("테슬라", "NQQUSATSLA", 7, "US88160R1014", "TSLA.O", {
        "trading_strategy": (
            "현재 종가 346.58은 5일선(343.23)과 10일선(339.87)을 상회하며 단기 반등을 시도하고 "
            "있습니다. 8월 12일부터 14일까지 3거래일 연속 양봉 또는 도지가 지속되었고, 이후 "
            "20일선(338.98) 수준에서의 반등 가능성도 고려할 수 있습니다." + BR
            + "현재 캔들은 하단(저가 338.98)이 지지선으로 작용할 경우, 이후 상승 전환 가능성이 "
            "있습니다."),
        "volume_change": (
            "8월 10일 24,708,543으로 36.32% 감소했습니다." + BR
            + "8월 11일에는 다시 23,206,596으로 감소세를 유지했으나, 8월 12일 27,986,512로 "
            "증가했습니다."),
        "technical_indicator_analysis": {
            "RSI": ("RSI는 8월 19일 53.43으로 중립 구간 상단에 위치했습니다." + BR
                    + "최근 8월 20일에는 51.1로 소폭 하락했으나, 여전히 30~70 과매도과매수 구간 "
                    "밖에 머무르고 있습니다."),
            "MACD": ("MACD는 구간에서 안정적으로 유지되고 있으며, 과매수 또는 과매도 신호는 "
                     "나타나지 않았습니다." + BR + "MACD는 여전히 Signal선 위에서 상승 흐름의 "
                     "지속 가능성이 시사되고 있습니다."),
        },
        "overall_diagnosis": (
            "<strong>MACD는 골든크로스를 유지하고</strong> 있으나 거래량이 뒷받침되지 않아 "
            "기술적 조정 국면으로의 전환 가능성이 있습니다."),
    }),
    "4558": ("엔비디아", "NQQUSANVDA", 6, "US67066G1040", "NVDA.O", {
        "trading_strategy": (
            "현재 종가 217.05는 5일선(220.90) 및 10일선(219.44) 아래에 위치합니다." + BR
            + "8월 12일에는 주가등락률 +3.03%의 강한 양봉이 나타났으나 추가 상승은 제한적이었습니다. "
            "추가 하락 시 20일선(212.79) 및 볼린저밴드 중심선 근처에서 지지 여부를 확인해야 합니다."),
        "volume_change": (
            "8월 13일부터 14일까지 거래량은 98,072,310에서 75,311,152로 감소했습니다." + BR
            + "8월 17일에는 105,927,877로 전일 대비 감소했으나, 여전히 높은 수준을 유지했습니다."),
        "technical_indicator_analysis": {
            "MACD": ("MACD 값은 8월 7일 2.46에서 8월 10일 3.13까지 상승했으나, 이후 8월 11일 "
                     "2.86으로 하락했습니다."),
        },
        "overall_diagnosis": (
            "최근 주가는 이동평균선 아래로 내려서 하락 후 중심선 근처 접근 중이며, 기술적 조정 "
            "국면으로 해석될 수 있습니다."),
    }),
    "4637": ("마이크로클라우드 홀로그램", "NQQUSAHOLO", 5, "KYG550322088", "HOLO.O", {
        "trading_strategy": (
            "현재 종가 1.75는 5일선(1.71)을 상회하고 있습니다. 8월 18일까지 저점이 낮아졌으나, "
            "8월 19일 장대양봉으로 강한 반등이 나타났습니다."),
        "price_pattern_desc": (
            "8월 14일부터 17일까지 3거래일 연속 음봉이 이어지며 하락 압력이 나타났습니다." + BR
            + "8월 18일에는 1.66까지 내려갔으나, 8월 19일 다시 1.7선을 회복했습니다."),
        "technical_indicator_analysis": {
            "RSI": ("RSI는 8월 7일 53.1에서 상승하여 8월 13일 62.16까지 올랐으나, 이후 8월 14일 "
                    "60.1, 8월 17일 53.45로 하락하며 조정 흐름을 보였습니다."),
            "MACD": "MACD는 시그널선과 교차 상태로, 향후 상향 돌파 시 매수 신호로 해석됩니다.",
        },
        "overall_diagnosis": "단기 반등 이후 방향성 확인이 필요한 구간입니다.",
    }),
    "4560": ("팔란티어 테크놀로지스", "NQQUSAPLTR", 5, "US69608A1088", "PLTR.O", {
        "trading_strategy": (
            "8월 7일 종가 172.01에서 출발해 8월 10일 175.23(+1.87%), 8월 14일 68.14로 하락한 "
            "이후 8월 17일과 18일 연속 음봉 또는 도지로 전환되었습니다."),
        "price_pattern_desc": (
            "8월 7일 종가 172.01에서 시작해 8월 10일 175.23(+1.87%)까지 오른 뒤 하락했습니다." + BR
            + "8월 14일 음봉(-2.78%) 이후 하락 흐름이 이어졌으나, 8월 14일부터 음봉이 "
            "시작되었습니다."),
        "technical_indicator_analysis": {
            "RSI": ("RSI는 8월 7일 72.83에서 8월 10일 73.91까지 상승했으나, 이후 8월 13일 72.89, "
                    "8월 14일 68.14로 하락했습니다." + BR + "8월 17일 66.73, 18일 65.74로 "
                    "떨어졌습니다."),
            "MA": ("이동평균선은 5일선(173.47)과 10일선(173.96)이 수렴 중이며, 주가가 이 구간에서 "
                   "등락하고 있습니다." + BR + "볼린저밴드 중심선(154.43)은 지지선 역할을 "
                   "합니다."),
        },
    }),
    "4566": ("리게티 컴퓨팅", "NQQUSARGTI", 6, "US76655K1034", "RGTI.O", {
        "trading_strategy": (
            "현재 주가는 20일선(16.66)과 60일선(15.92) 사이에서 거래되고 있습니다. 최근 "
            "3거래일은 모두 음봉으로 마감했으며, 종가가 점차 낮아지는 흐름입니다." + BR
            + "8월 13일과 18일에는 슈팅스타 패턴이 나타났습니다."),
        "volume_change": (
            "이후 8월 10일 -1.62% 하락하며 17.65에서 마감했고, 종가는 17.94까지 올랐습니다." + BR
            + "8월 17일 거래량은 10,353,681로 -38.75% 급감했고, 이후 8월 18일과 19일 각각 "
            "31.88%, 40.27% 증가하며 거래량 폭이 확대됐습니다."),
        "technical_indicator_analysis": {
            "RSI": ("8월 20일에는 전일 대비 3.58% 증가한 0.53까지 상승했습니다." + BR
                    + "그러나 Signal선은 0.53까지 따라오며 갭이 좁혀졌고, 8월 18일 MACD가 0.59, "
                    "Signal선이 0.64로 MACD가 증가했다가 최근 소폭 감소했습니다."),
        },
        "overall_diagnosis": (
            "기술적 지표 전반에서 상승 모멘텀 약화와 조정 국면으로 해석됩니다."),
    }),
    "4721": ("마이크로클라우드 홀로그램", "NQQUSAHOLO", 6, "KYG550322088", "HOLO.O", {
        "trading_strategy": (
            "현재 종가 1.75는 5일선(1.71)과 10일선(1.69)을 상회하며 단기 반등 흐름입니다."),
        "price_pattern_desc": (
            "8월 19일 장대양봉 이후 8월 20일 거래량이 줄며 관망세로 전환했습니다."),
        "technical_indicator_analysis": {
            "RSI": ("RSI는 8월 7일 53.1에서 8월 13일 62.16까지 올랐으나, 이후 8월 17일 53.45로 "
                    "내려서 강한 상승 모멘텀은 나타나지 않았습니다."),
            "SUBtotal": ("RSI는 과매수과매도 경계 아래에 있고, MACD는 시그널선 아래에서 음의 "
                         "영역이 축소되는 흐름입니다."),
        },
        "overall_diagnosis": (
            "8월 19일 고가 돌파 여부에 따라 반전 신호의 신뢰도가 결정될 것으로 보입니다."),
    }),
    "4567": ("애플", "NQQUSAAAPL", 6, "US0378331005", "AAPL.O", {
        "trading_strategy": (
            "현재 주가는 20일선(315.56) 아래에서 거래되며 단기 약세 흐름입니다." + BR
            + "8월 10일 이후 연속 음봉이 발생했습니다."),
        "price_pattern_desc": (
            "8월 13일 도지 이후 8월 14일과 17일 연속 음봉이 나타났습니다." + BR
            + "8월 10일 308.26, 8월 11일 304.91, 8월 12일 302.25로 하락 흐름이 이어졌습니다." + BR
            + "8월 13일부터 이후 8월 10일 도지로 상승 힘의 약화가 나타났으며, 8월 11일과 12일 "
            "하락하고 거래량이 감소하는 모습입니다."),
        "technical_indicator_analysis": {
            "SUBtotal": ("RSI는 중립권에서 움직이며 과매도 후 회복된 상태이고, MACD는 신호선을 "
                         "상회합니다. 반등에도 불구하고 신호선 대비 상승 폭은 제한적입니다."),
        },
        "overall_diagnosis": (
            "연속 도지와 음봉 재진입, 그리고 볼린저 밴드 중심선의 저항 돌파 실패로 캔들 패턴 "
            "상 약세가 우세합니다."),
    }),
}

# 캡처의 행 순서 — (등록번호, 라인번호). 캡처에 없는 행은 원천에도 없다(모듈 docstring 2번).
LAYOUT = [
    *[("4556", n) for n in (1, 2, 3)],
    *[("4558", n) for n in range(1, 7)],
    *[("4556", n) for n in (4, 5, 6, 7)],
    *[("4637", n) for n in (1, 2, 3, 5)],
    *[("4560", n) for n in range(1, 6)],
    *[("4566", n) for n in range(1, 7)],
    *[("4721", n) for n in (4, 6)],
    *[("4567", n) for n in range(1, 7)],
]


def split_pieces(text: str, count: int) -> list[str]:
    """count 조각으로 균등 절단하되, 다음 경계 전에 키가 있으면 키 이름 한가운데에서 자른다.

    make_stock_insight_sample.split_at_key_names 는 키가 조각 수보다 적으면 같은 키에서 두 번
    잘라 조각이 어긋난다. 이 샘플의 문서는 키가 적어 경계를 넘는 키는 쓰지 않는다.
    """
    step = len(text) // count
    edges = [0]
    for i in range(1, count):
        boundary = i * step
        match = _KEY_RE.search(text, boundary, boundary + step // 2)
        if match:
            boundary = match.start() + 1 + len(match.group(1)) // 2
        edges.append(boundary)
    edges.append(len(text))
    return [text[a:b] for a, b in zip(edges, edges[1:])]


def build_rows() -> list[list]:
    """문서마다 detail 을 절단한 뒤 LAYOUT 순서로 원천 행을 뽑는다."""
    pieces: dict[str, list[str]] = {}
    for regt_no, (_, _, count, _, _, detail) in DOCS.items():
        cut = split_pieces(json.dumps(detail, ensure_ascii=False), count)
        assert len(cut) == count and json.loads("".join(cut)) == detail, regt_no
        pieces[regt_no] = cut
    rows = []
    for regt_no, line_no in LAYOUT:
        name, code, _, md_code, kosc_code, _ = DOCS[regt_no]
        rows.append([name, code, regt_no, line_no, pieces[regt_no][line_no - 1],
                     NEWS_DATE, md_code, kosc_code, "US"])
    return rows


def main() -> None:
    rows = build_rows()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(SEPARATOR.join(str(v) for v in row) + "\n")
    print(f"{OUT}  rows={len(rows)}")


if __name__ == "__main__":
    main()
