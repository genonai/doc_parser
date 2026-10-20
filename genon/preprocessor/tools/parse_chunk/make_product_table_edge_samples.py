#!/usr/bin/env python3
"""표 분할·병합 경계 사례(#550)의 재현용 Markdown 3종을 생성한다.

`make_product_slf_span_table_sample.py` 의 샘플 하나로는 덮지 못하는 경계를 사례별 구간으로
나눠 담는다. 셀 문구와 표 구조는 실물에서 가져오고, 경계를 만들기 위해 구조만 조합한다.
실제 상품 안내가 아니다. 출처 약칭은 다음과 같다.

  캡처      운영 상품설명서 HTML 표 md 캡처(2026-10-17). 11·12·13쪽 표와 6쪽 치과 표의 문구는
            span 샘플 생성기의 상수(WAIVER, DENTAL_HEADER, LONG_HEADER, LONG_BODY)와 아래 상수로 전사했다.
  fence     sample_files/monimo/monimo_product_slf_fence_sample.md 의 Q/A 문장
  ssf       sample_files/monimo/monimo_product_ssf_html_table_sample.md·_quality_sample.md
  링크      sample_files/json/monimo_card_sample.json, monimo/monimo_event_real_sample.json,
            monimo/monimo_cs_hpp_chunksize_sample.json 의 URL
  분류표    한국표준질병·사인분류의 악성신생물(C00~C97)·제자리신생물(D00~D09) 분류 항목명

slf 긴 셀(EDGE_SLF, 문서 쪽 번호 기준)
  3쪽  사례7  문서 선두 섹션 제목 바로 뒤의 큰 표. 앞 청크가 없다(캡처 11쪽 표 구조).
  4쪽  사례1  한 행에 긴 셀 두 개. 캡처 13쪽 thead 조항(LONG_HEADER)과 tbody 조항(LONG_BODY)을
             한 행의 보장 내용·유의사항 칸에 놓았다.
  5쪽  사례2  문장 경계 없는 긴 셀. 마침표 없는 분류표 항목 나열(공백 경계만 있음)과 공백도
             없이 쉼표로 이은 링크 목록(문자 위치에서만 자를 수 있음). 앞 칸에 긴 조항(링크 행은
             캡처 11쪽 면제 조항과 13쪽 tbody 조항을 이은 것)을 두어 자르는 위치가 이 칸 안에 오게 했다.
  6쪽  사례3  rowspan=3 으로 세 행에 복제되는 긴 유의사항 칸(LONG_BODY)과 캡처 11쪽 3대안과 행.
  7쪽  사례6  표 헤더만으로 한도를 넘는 표. 캡처 13쪽 thead 조항과 tbody 조항을 한 thead 셀로
             합쳤다. 의도된 초과이며 경고 로그로만 드러나야 한다.
slf 쪽 구조(EDGE_SLF_PAGES)
  2쪽  사례4  하위 헤더가 있는 계층 헤더(지급금액 colspan=2 > 1년이내·1년초과). 캡처 13쪽 tbody 의
             소제목 두 행을 thead 로 올렸다. 행 요약 라벨이 유지되는지 보는 대조군이다.
  3쪽  사례5  하위 헤더 없는 짧은 colspan 헤더(`구분` colspan=2). 캡처 6쪽 thead 안의 1년초과·
             1년이내 지급금액 목록을 rowspan 행으로 풀었다. 6쪽 thead 셀 전문을 지급사유 칸에 기간
             묶음마다 rowspan=3 으로 두고 면제 조항(캡처 11쪽)을 마지막 행으로 두어 분할되게 했다.
  4~5쪽 사례8  쪽 경계의 짧은 본문(fence 문장) → 쪽번호 펜스 → 다음 섹션 제목 → 큰 표(캡처 11쪽 구조).
  6~12쪽 사례9 쪽번호 펜스만 있어 제목만 남는 섹션 6개(캡처·fence 의 긴 쪽 제목)가 다음 청크로
             병합되어 HEADER 리프 상한(CHUNK_PATH_MAX_LEAVES=5)을 넘고, 이어서 H3·H4 깊은 경로 아래 큰 표.
  13~14쪽 사례10 tbody 중간의 소제목 행 두 줄(캡처 13쪽)과 빈 행(#537), 쪽번호 펜스 변형
             `-13-`(공백 없음)과 쪽번호+문구 꼬리말(ssf 품질 샘플의 `상품요약서 - 6 -` 형태).
ssf HTML 표(EDGE_SSF)
  2쪽  사례11 한도 안에 드는 평범한 span 표(ssf 담보 표, caption 포함). 출력 불변 대조군이다.
  3쪽  사례5  `구분` colspan=2 의 짧은 표(ssf 가입자격 값). 뒤에 펜스 없는 쪽 꼬리말 줄이 온다.
  4~5쪽 사례8  쪽 경계의 짧은 본문 → 쪽번호+문구 꼬리말 → 다음 섹션 제목 → 표.

재생성(저장소 루트):
  genon/preprocessor/.venv/bin/python genon/preprocessor/tools/parse_chunk/make_product_table_edge_samples.py
실행(하이브리드 PYTHONPATH는 CLAUDE.md 참조):
  .venv/bin/python genon/preprocessor/tools/parse_chunk/parse_chunk_test.py \
    --site monimo --doc_type product_slf <생성된 md> <저장소 밖 출력 폴더>
검증은 parse_chunk_verify.py 의 check_table_edge·check_table_edge_warning 이 한다. 샘플을 변경할 때는 이 생성기를
수정하고 재실행한다.
"""
from __future__ import annotations

from pathlib import Path

from make_product_slf_span_table_sample import DENTAL_HEADER, LONG_BODY, LONG_HEADER, WAIVER

MONIMO = Path(__file__).resolve().parents[2] / "sample_files/monimo"
EDGE_SLF = MONIMO / "monimo_product_slf_table_edge_sample.md"
EDGE_SLF_PAGES = MONIMO / "monimo_product_slf_table_edge_sample_pages.md"
EDGE_SSF = MONIMO / "monimo_product_ssf_table_edge_sample.md"

# 캡처 11쪽·12쪽·13쪽 셀 문구.
CORNEA = ('□ 각막이식수술보험금<br>피보험자가 이 특약의 보험기간 중 &quot;각막이식수술&quot;을 받<br>'
          '은 경우(단, 각막공여자에 대한 수술은 제외) [단, 좌안<br>(左眼), 우안(右眼), 양안(兩眼) 중 최초 1회에 한함]')
EYE_PLAN = '3대안과질환<br>수술보장특약<br>D5<br>(갱신형, 무배<br>당)'
EYE_ROWS = (
    ('□ 녹내장수술보험금<br>피보험자가 이 특약의 보험기간 중 “녹내장”으로 진단<br>이 확정되고, 이 특약의 보험기간 중 '
     '“녹내장”의 직접<br>적인 치료를 목적으로 수술을 받은 경우 [단, 좌안(左<br>眼), 우안(右眼), 양안(兩眼) 중 최초1회에 한함]',
     '50만원'),
    ('□ 황반변성질환수술보험금<br>피보험자가 이 특약의 보험기간 중 “황반변성질환”으<br>로 진단이 확정되고, 이 특약의 '
     '보험기간 중 “황반변성<br>질환”의 직접적인 치료를 목적으로 수술을 받은 경우<br>[단, 좌안(左眼), 우안(右眼), '
     '양안(兩眼) 중 최초1회에<br>한함]', '70만원'),
    ('□ 당뇨병성망막질환수술보험금<br>피보험자가 이 특약의 보험기간 중 “당뇨병성망막질<br>환”으로 진단이 확정되고, '
     '이 특약의 보험기간 중 “당<br>뇨병성망막질환”의 직접적인 치료를 목적으로 수술을<br>받은 경우 [단, 좌안(左眼), '
     '우안(右眼), 양안(兩眼) 중<br>최초1회에 한함]', '70만원'),
)
CANCER_PLAN = '암진단특약Ⅲ N15(갱<br>신형,무배당)'
CANCER = '□ 암진단보험금<br>- 피보험자가 이 특약의 보험기간 중 암보장개시일 이후<br>에 암으로 진단 확정 되었을 경우(최초 1회한)'
SMALL_PLAN = '소액질병보장특약VII<br>D(무배당, 무해지환<br>급금형)'
SMALL_REASON = ('□ 소액질병진단보험금<br>- 피보험자가 이 특약의 보험기간 중 “전립선암”으로<br>진단이 확정되었을 때(단, '
                '피보험자가 남성인 경우에 한<br>하며, 1회의 진단 확정에 한함)')
SMALL_REASON2 = ('- 피보험자가 이 특약의 보험기간 중 “경계성종양”,<br>“갑상선암”, “양성뇌종양”, “제자리암”, “기타피부<br>'
                 '암”, “비침습 방광암” 또는 “대장점막내암”으로 진<br>단이 확정되었을 때(단, 각 1회의 진단확정에 한함)')

# 분류표 항목명. 항목 사이에 마침표가 없어 문장 경계로는 자를 수 없다.
MALIGNANT = (
    '입술, 구강 및 인두의 악성신생물(C00~C14), 소화기관의 악성신생물(C15~C26), 호흡기 및 흉곽내기관의 '
    '악성신생물(C30~C39), 뼈 및 관절연골의 악성신생물(C40~C41), 피부의 흑색종 및 기타 악성신생물(C43~C44), '
    '중피성 및 연조직의 악성신생물(C45~C49), 유방의 악성신생물(C50), 여성생식기관의 악성신생물(C51~C58), '
    '남성생식기관의 악성신생물(C60~C63), 요로의 악성신생물(C64~C68), 눈, 뇌 및 중추신경계통의 기타 부분의 '
    '악성신생물(C69~C72), 갑상선 및 기타 내분비선의 악성신생물(C73~C75), 불명확한, 이차성 및 상세불명 부위의 '
    '악성신생물(C76~C80), 림프, 조혈 및 관련조직의 악성신생물(C81~C96), 독립된 여러 부위의 원발성 악성신생물(C97)'
)
IN_SITU = (
    '구강, 식도 및 위의 제자리암종(D00), 기타 및 상세불명의 소화기관의 제자리암종(D01), 중이 및 호흡계통의 '
    '제자리암종(D02), 제자리흑색종(D03), 피부의 제자리암종(D04), 유방의 제자리암종(D05), 자궁경부의 '
    '제자리암종(D06), 기타 및 상세불명의 생식기관의 제자리암종(D07), 기타 및 상세불명 부위의 제자리암종(D09)'
)
# 공백 없이 쉼표로만 이은 링크 목록. 문단·문장·공백 경계가 하나도 없다.
LINKS = ','.join((
    'https://static11.samsungcard.com/wcms/home/scard/personal/__icsFiles/afieldfile/2026/05/28/AAP1913_bg.png',
    'https://www.samsungcard.com/home/card/cardinfo/PGHPPCCCardCardinfoDetails001?code=AAP1913'
    '&click=UHPPCO0101M0_card_PGHPPCCCardCardinfoDetails001_PRD_AAP1913',
    'https://static11.samsungcard.com/wcms/home/scard/image/personal/DN_AAP1913.gif',
    'https://inc-kms2.example.invalid/okmsView/dictionary/31/237219/10',
    'https://inc-kms2.example.invalid/okmsView/dictionary/31/237220/10',
    'https://s.go-mpulse.net/boomerang/Z7ZNG-4Y6ZW-B943N-XMU95-YXETG',
))

# fence 샘플의 짧은 답변 문장.
FENCE_LINE = '최종 갱신계약의 보험기간 만료일은 주계약의 보험기간 만료일로 합니다.'
# 캡처와 fence 의 긴 쪽 제목. 변환기가 쪽 첫 줄을 잘라 제목으로 쓴 형태다.
LONG_TITLES = (
    '㈜ 1. 보험료 납입기간 중 주보험의 보험료 납입 면제 사유가 발생하였을 경우에는 이 특약의',
    '피보험자가 보험기간 중 「"치과치료보장개시일 이후에 치아에 대하여 \'치아우식증',
    '㈜ 1.보험료 납입기간 중 주계약의 보험료 납입면제 사유가 발생하였을 경우(다만, 주계약이 유효한',
    '갱신형 특약에서 갱신은 어떻게 이루어 지나요?',
    "이 상품의 '가족결합할인'이란 무엇인가요?",
    '이 상품의 특이사항은 무엇인가요?',
)
# ssf 품질 샘플의 쪽 꼬리말(상품명 + 쪽번호).
SSF_FOOTER = '삼성화재 다이렉트 해외여행보험(2506) 상품요약서 - {page} -'

# 검증이 조각을 이어 붙여 원문 복원을 확인할 긴 셀: (쪽 제목 일부, md 표의 열 번호, 셀 원문).
LONG_CELLS = (
    ('원문 4쪽', 1, LONG_HEADER), ('원문 4쪽', 2, LONG_BODY),
    ('원문 5쪽', 2, f'{MALIGNANT}, {IN_SITU}'), ('원문 5쪽', 2, LINKS),
    ('원문 6쪽', 3, LONG_BODY),
)
# 헤더만으로 한도를 넘는 사례6 의 thead 셀.
OVERSIZED_HEAD = f'{LONG_HEADER}<br>{LONG_BODY}'


def table(page: int, index: int, heads: list[str], rows: list[str], caption: str = '') -> str:
    cap = f'  <caption>{caption}</caption>\n' if caption else ''
    return (f'<table data-source-page="{page}" data-table-index="{index}">\n{cap}  <thead>\n'
            + '\n'.join(f'    <tr>{head}</tr>' for head in heads)
            + '\n  </thead>\n  <tbody>\n'
            + '\n'.join(f'    <tr>{row}</tr>' for row in rows)
            + '\n  </tbody>\n</table>')


def page(number: int, title: str, *blocks: str, level: int = 2) -> str:
    return '\n\n'.join([f'<!-- source_page: {number} -->\n{"#" * level} 원문 {number}쪽 - {title}', *blocks])


def fence(text: str) -> str:
    return f'```text\n{text}\n```'


def front_matter(title: str, source_file: str, pages: int, note: str) -> str:
    return (f'---\nfile: "{title}"\ntitle: "{title}"\ndocument_type: "보험 상품요약서"\n'
            f'source_file: "{source_file}"\nsource_pages: {pages}\nauthor: "재현용 합성 문서"\n'
            f'created_at: "2026-10-18"\nconversion_note: "{note}"\n---')


def eye_table(page_no: int, index: int) -> str:
    """캡처 11쪽 표. 각막이식 행, 전체 폭 면제 조항, rowspan=3 특약, 면제 조항."""
    return table(page_no, index, ['<th>특약명</th><th>지급사유</th><th>지급금액</th>'], [
        f'<td>각막이식수술<br>특약D5(갱신<br>형, 무배당)</td><td>{CORNEA}</td><td>1,000만원</td>',
        f'<td colspan="3">{WAIVER}</td>',
        f'<td rowspan="3">{EYE_PLAN}</td><td>{EYE_ROWS[0][0]}</td><td>{EYE_ROWS[0][1]}</td>',
        *(f'<td>{reason}</td><td>{amount}</td>' for reason, amount in EYE_ROWS[1:]),
        f'<td colspan="3">{WAIVER}</td>',
    ])


def build_slf() -> str:
    note = '운영 캡처의 표 구조와 문구로 긴 셀 경계를 조합한 재현용 문서. 실제 상품 안내가 아님.'
    title = '표 분할 경계 재현 상품요약서(긴 셀)'
    two_long = table(4, 1, ['<th>특약명</th><th>보장 내용</th><th>유의사항</th><th>지급금액</th>'], [
        f'<td>{CANCER_PLAN}</td><td>{CANCER}</td><td>{WAIVER}</td><td>500만원</td>',
        f'<td>{SMALL_PLAN}</td><td>{LONG_HEADER}</td><td>{LONG_BODY}</td><td>150만원</td>',
    ])
    no_boundary = table(5, 1, ['<th>구분</th><th>유의사항</th><th>대상이 되는 질병 및 분류번호</th>'], [
        f'<td>암·제자리암</td><td>{LONG_HEADER}</td><td>{MALIGNANT}, {IN_SITU}</td>',
        f'<td>관련 링크</td><td>{WAIVER}<br>{LONG_BODY}</td><td>{LINKS}</td>',
    ])
    rowspan_long = table(6, 1, ['<th>특약명</th><th>지급사유</th><th>지급금액</th><th>유의사항</th>'], [
        f'<td rowspan="3">{EYE_PLAN}</td><td>{EYE_ROWS[0][0]}</td><td>{EYE_ROWS[0][1]}</td>'
        f'<td rowspan="3">{LONG_BODY}</td>',
        *(f'<td>{reason}</td><td>{amount}</td>' for reason, amount in EYE_ROWS[1:]),
    ])
    oversized = table(7, 1, [f'<th colspan="4">{OVERSIZED_HEAD}</th>'], [
        '<td rowspan="2">특약명</td><td rowspan="2">지급사유</td><td colspan="2">지급금액</td>',
        '<td>1년이내</td><td>1년초과</td>',
        f'<td rowspan="2">{SMALL_PLAN}</td><td>{SMALL_REASON}</td><td>150만원</td><td>300만원</td>',
        f'<td>{SMALL_REASON2}</td><td>50만원</td><td>100만원</td>',
    ])
    return '\n\n'.join([
        front_matter(title, 'table_edge_long_cells.pdf', 7, note), f'# {title}', '## 원문 전사',
        page(3, '각막이식수술보험금', eye_table(3, 1), fence('- 3 -')),
        page(4, '소액질병보장특약VII D', two_long, fence('- 4 -')),
        page(5, '암 분류표', no_boundary, fence('- 5 -')),
        page(6, '3대안과질환수술보장특약D5', rowspan_long, fence('- 6 -')),
        page(7, LONG_TITLES[2], oversized, fence('- 7 -')),
    ]) + '\n'


def build_slf_pages() -> str:
    note = '운영 캡처의 표 구조와 문구로 헤더·쪽 경계를 조합한 재현용 문서. 실제 상품 안내가 아님.'
    title = '표 분할 경계 재현 상품요약서(쪽 구조)'
    hierarchical = table(2, 1, [
        '<th rowspan="2">특약명</th><th rowspan="2">지급사유</th><th colspan="2">지급금액</th>',
        '<th>1년이내</th><th>1년초과</th>',
    ], [
        f'<td rowspan="2">{SMALL_PLAN}</td><td>{SMALL_REASON}</td><td>150만원</td><td>300만원</td>',
        f'<td>{SMALL_REASON2}</td><td>50만원</td><td>100만원</td>',
        f'<td>{CANCER_PLAN}</td><td>{CANCER}</td><td>500만원</td><td>1,000만원</td>',
        f'<td colspan="4">{WAIVER}</td>',
        f'<td colspan="4">{LONG_BODY}</td>',
    ])
    reason = DENTAL_HEADER
    short_colspan = table(3, 1, ['<th colspan="2">구분</th><th>지급금액</th><th>지급사유</th>'], [
        f'<td rowspan="3">계약일로부터 1년초과</td><td>인레이, 온레이</td><td>15만원</td><td rowspan="3">{reason}</td>',
        '<td>복합레진</td><td>5만원</td>', '<td>아말감, 글래스아이노머</td><td>1만원</td>',
        f'<td rowspan="3">계약일로부터 1년이내</td><td>인레이, 온레이</td><td>7.5만원</td><td rowspan="3">{reason}</td>',
        '<td>복합레진</td><td>2.5만원</td>', '<td>아말감, 글래스아이노머</td><td>0.5만원</td>',
        f'<td colspan="4">{WAIVER}</td>',
    ])
    short_pages = [page(6 + i, LONG_TITLES[i], fence(f'- {6 + i} -')) for i in range(6)]
    deep = '\n\n'.join([
        page(12, '각막이식수술보험금', ''), '### □ 3대안과질환수술보장특약D5(갱신형, 무배당)',
        '#### □ 녹내장수술보험금, 황반변성질환수술보험금, 당뇨병성망막질환수술보험금', eye_table(12, 1)])
    blank = '<td></td><td></td><td></td><td></td>'
    subhead = ('<td rowspan="2">특약명</td><td rowspan="2">지급사유</td><td colspan="2">지급금액</td>',
               '<td>1년이내</td><td>1년초과</td>')
    subhead_rows = table(13, 1, ['<th>특약명</th><th>지급사유</th><th>지급금액(1년이내)</th><th>지급금액(1년초과)</th>'], [
        f'<td>{CANCER_PLAN}</td><td>{CANCER}</td><td>500만원</td><td>1,000만원</td>', blank,
        f'<td colspan="4">{WAIVER}</td>', *subhead,
        f'<td rowspan="2">{SMALL_PLAN}</td><td>{SMALL_REASON}</td><td>150만원</td><td>300만원</td>',
        f'<td>{SMALL_REASON2}</td><td>50만원</td><td>100만원</td>', blank, blank,
        f'<td colspan="4">{LONG_BODY}</td>', *subhead,
        f'<td>소액질병보장특약VII</td><td>{SMALL_REASON}</td><td>150만원</td><td>300만원</td>',
    ])
    footer = table(14, 1, ['<th>구분</th><th>지급금액㈜</th><th>보장한도</th>'], [
        '<td>계약일로부터 1년초과</td><td>25만원</td><td rowspan="2">연간 『유치 3개 및 영구치 3<br>개』</td>',
        '<td>계약일로부터 1년이내</td><td>12.5만원</td>',
    ])
    return '\n\n'.join([
        front_matter(title, 'table_edge_pages.pdf', 14, note), f'# {title}', '## 원문 전사',
        page(2, '소액질병보장특약VII D', hierarchical, fence('- 2 -')),
        page(3, '보존치료보험금', short_colspan, fence('- 3 -')),
        page(4, LONG_TITLES[3], FENCE_LINE, fence('- 4 -')),
        page(5, '각막이식수술보험금', eye_table(5, 1), fence('- 5 -')),
        *short_pages, deep, fence('- 12 -'),
        page(13, '소액질병보장특약VII D', subhead_rows, fence('-13-')),
        page(14, '2. 보험금 지급사유', footer,
             fence('삼성 1540 청춘대표·4180 인생대표·팩 건강보험(2607) 상품요약서 - 14 -')),
    ]) + '\n'


def build_ssf() -> str:
    note = 'ssf HTML 표 판 샘플의 표 구조와 문구로 쪽 경계를 조합한 재현용 문서. 실제 상품 안내가 아님.'
    title = '표 분할 경계 재현 상품요약서(손해보험 HTML 표)'
    coverage = table(2, 1, [
        '<th rowspan="2">담보</th><th colspan="2">보장 내용</th><th rowspan="2">지급금액</th>',
        '<th>지급사유</th><th>자기부담금</th>',
    ], [
        '<td>상해</td><td>상해 사망·후유장해</td><td>없음</td><td>최대 1억원</td>',
        '<td>의료비</td><td>해외 실손의료비</td><td>1만원</td><td>최대 3천만원</td>',
        '<td>휴대품</td><td>휴대품 손해(1품목당 20만원)</td><td>1만원</td><td>최대 100만원</td>',
        '<td>지연</td><td>항공기 4시간 이상 지연</td><td>없음</td><td>20만원</td>',
    ], caption='담보별 보험금 지급사유와 지급금액')
    eligibility = table(3, 1, ['<th colspan="2">구분</th><th>내용</th>'], [
        '<td rowspan="3">가입조건</td><td>가입나이</td><td>만 0세 ~ 79세</td>',
        '<td>보험기간</td><td>여행 일정과 동일 (최대 90일)</td>', '<td>납입기간</td><td>일시납</td>',
    ], caption='보험가입자격요건 요약')
    claim = table(5, 1, ['<th>지급사유</th><th>지급금액</th>'], [
        '<td>상해 사망·후유장해</td><td>최대 1억원</td>', '<td>해외 실손의료비</td><td>최대 3천만원</td>',
        '<td>휴대품 손해</td><td>최대 100만원 (1품목당 20만원)</td>', '<td>항공기 지연 비용</td><td>20만원</td>',
    ])
    return '\n\n'.join([
        front_matter(title, 'table_edge_ssf.pdf', 5, note), f'# {title}', '## 원문 전사',
        page(1, '상품요약서', '여행 중 상해나 질병으로 현지 의료기관에서 치료받은 경우 실손의료비를 보장합니다. '
             '휴대품이 파손·도난된 경우 자기부담금을 차감하고 보상하며, 항공기가 4시간 이상 지연되면 지연 비용을 '
             '정액 지급합니다.', fence('-1-')),
        page(2, '보험금 지급사유', coverage, fence('- 2 -')),
        page(3, '보험가입자격요건', eligibility, SSF_FOOTER.format(page=3)),
        page(4, '상품의 특이사항', '전쟁·테러 지역 여행은 인수가 제한됩니다.', fence(SSF_FOOTER.format(page=4))),
        page(5, '보험금 지급사유', claim, fence('- 5 -')),
    ]) + '\n'


def main() -> None:
    for path, build in ((EDGE_SLF, build_slf), (EDGE_SLF_PAGES, build_slf_pages), (EDGE_SSF, build_ssf)):
        path.write_text(build(), encoding='utf-8')
        print(f'wrote {path}')


if __name__ == '__main__':
    main()
