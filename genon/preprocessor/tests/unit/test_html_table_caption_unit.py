"""HTML 표의 <caption> 이 docling 문서에 남는지 검증한다.

이 처리가 없으면 캡션 텍스트가 표에도 본문에도 남지 않고 통째로 사라져,
청크만 보고는 그 표가 무엇에 대한 것인지 알 수 없다.
"""

import pytest


def _convert(tmp_path, body: str):
    pytest.importorskip("docling.document_converter", exc_type=ImportError)
    from docling.document_converter import DocumentConverter

    path = tmp_path / "table.html"
    path.write_text(f"<!doctype html><html><body>{body}</body></html>", encoding="utf-8")
    return DocumentConverter().convert(path).document


@pytest.mark.unit
def test_table_caption_survives_conversion(tmp_path):
    doc = _convert(tmp_path, """
        <table>
          <caption>2026년 월별 이용 내역</caption>
          <thead><tr><th>월</th><th>금액</th></tr></thead>
          <tbody><tr><td>1월</td><td>1000</td></tr></tbody>
        </table>""")

    assert doc.tables[0].caption_text(doc).strip() == "2026년 월별 이용 내역"
    assert "2026년 월별 이용 내역" in doc.export_to_markdown()


@pytest.mark.unit
def test_caption_text_is_not_mistaken_for_a_table_cell(tmp_path):
    doc = _convert(tmp_path, """
        <table>
          <caption>안내</caption>
          <tbody><tr><td>값</td></tr></tbody>
        </table>""")

    cells = [c.text for row in doc.tables[0].data.grid for c in row]
    assert cells == ["값"]


@pytest.mark.unit
@pytest.mark.parametrize("body", [
    "<table><tbody><tr><td>a</td></tr></tbody></table>",              # 캡션 없음
    "<table><caption>   </caption><tbody><tr><td>a</td></tr></tbody></table>",  # 공백뿐
])
def test_missing_or_blank_caption_leaves_table_uncaptioned(tmp_path, body):
    doc = _convert(tmp_path, body)
    assert doc.tables[0].caption_text(doc) == ""


@pytest.mark.unit
def test_inline_markup_inside_caption_is_flattened(tmp_path):
    doc = _convert(tmp_path, """
        <table><caption>월별 <b>이용</b> 내역</caption>
        <tbody><tr><td>a</td></tr></tbody></table>""")
    assert doc.tables[0].caption_text(doc).strip() == "월별 이용 내역"


@pytest.mark.unit
def test_nested_table_keeps_its_own_caption(tmp_path):
    """직계 자식만 봐야 바깥 표가 안쪽 표의 캡션을 가져가지 않는다."""
    doc = _convert(tmp_path, """
        <table><caption>바깥표</caption><tbody><tr><td>
          <table><caption>안쪽표</caption><tbody><tr><td>x</td></tr></tbody></table>
        </td></tr></tbody></table>""")

    captions = {t.caption_text(doc).strip() for t in doc.tables}
    assert captions == {"바깥표", "안쪽표"}


# 실물 발췌(#564 재현 입력): v5 web05(MNT 고유가 피해지원금 글)의 워드프레스 wp-block-table 과
# v5 web03(삼성카드 추천 비교 글)의 셀 안에 그림이 든 figure>table.
_WP_BLOCK_TABLE = """<figure class="wp-block-table"><table class="has-fixed-layout"><thead><tr><th>구분</th><th>수도권</th><th>비수도권</th><th>우대지원지역</th><th>특별지원지역</th></tr></thead><tbody><tr><td><strong>기초생활수급자</strong></td><td>55만 원</td><td>60만 원</td><td>60만 원</td><td>60만 원</td></tr><tr><td><strong>차상위·한부모</strong></td><td>45만 원</td><td>50만 원</td><td>50만 원</td><td>50만 원</td></tr><tr><td><strong>그 외 국민 70%</strong></td><td>10만 원</td><td>15만 원</td><td>20만 원</td><td>25만 원</td></tr></tbody></table></figure>"""
_FIGURE_TABLE_WITH_CELL_IMAGE = """<figure class="table" style="width:500px;"><table><thead><tr><th><p style="text-align:center;">카드명</p></th><th style="width:200px;"><p style="text-align:center;">삼성카드 &amp; MILEAGE PLATINUM (스카이패스)</p></th><th><p style="text-align:center;">혜택</p></th></tr></thead><tbody><tr><td style="background-color:#f2f2f2;"><p style="text-align:center;"><strong>디자인</strong></p></td><td><figure class="image"><img height="281" src="https://image.ajd.co.kr/EDITOR/CONTENT/01KF2P1T9QNM99AWS0XD8ZN9HQ" style="aspect-ratio:178/281;" width="178"/></figure></td><td rowspan="2"><p style="text-align:center;"><strong>1천 원당 스카이패스 1 항공 마일리지 적립</strong></p><p style="text-align:center;"> </p><p style="text-align:center;"><strong>해외형,국내형 선택</strong><br/><strong>1천 원당 스카이패스 1 항공 마일리지 추가 적립</strong><br/><span style="font-size:12px;">(월 2천 마일리지)</span></p></td></tr><tr><td style="background-color:#f2f2f2;"><p style="text-align:center;"><strong>조건</strong></p></td><td><p style="text-align:center;">연회비 49,000원<br/>전월 실적 없음</p></td></tr></tbody></table></figure>"""
# 실물 발췌: v5 web02(뱅크샐러드 삼성카드 추천 글)의 그림만 담은 figure(<picture> + <figcaption>).
_IMAGE_ONLY_FIGURE = """<figure class="my-20 grid gap-y-12 overflow-scroll"><picture class="relative block aspect-[3/2] w-full"><source sizes="(max-width: 480px) 400w, (max-width: 1200px) 750w, 33vw" srcset="https://financialshopping-cdn.banksalad.com/large-webp_and_MILEAGE_PLATINUM_1d49831c97.webp 1000w, https://financialshopping-cdn.banksalad.com/medium-webp_and_MILEAGE_PLATINUM_1d49831c97.webp 750w, https://financialshopping-cdn.banksalad.com/small-webp_and_MILEAGE_PLATINUM_1d49831c97.webp 400w" type="image/webp"/><source sizes="(max-width: 480px) 400w, (max-width: 1200px) 750w, 33vw" srcset="https://financialshopping-cdn.banksalad.com/large-png_and_MILEAGE_PLATINUM_1d49831c97.png 1000w, https://financialshopping-cdn.banksalad.com/medium-png_and_MILEAGE_PLATINUM_1d49831c97.png 750w, https://financialshopping-cdn.banksalad.com/small-png_and_MILEAGE_PLATINUM_1d49831c97.png 400w" type="image/png"/><img alt="마일리지 적립 앤마일리지 신용카드 추천 2024 " class="absolute h-full w-full object-cover align-middle rounded-[20px]" decoding="async" loading="lazy" src="https://financialshopping-cdn.banksalad.com/and_MILEAGE_PLATINUM_1d49831c97.png"/></picture><figcaption><p class="max-w-full text-[14px] leading-[130%] scroll-mt-[130px] md:scroll-mt-[155px] text-gray-400 text-center"><span style="background-color:#ffffff;color:inherit;font-family:Roboto,sans-serif;font:inherit"><span style="font-size:10.5pt;font-style:normal;font-variant:normal;font-weight:400;text-decoration:none;vertical-align:baseline;white-space:pre-wrap;color:inherit;font:inherit">삼성카드 &amp; MILEAGE PLATINUM(스카이패스)</span></span></p></figcaption></figure>"""


@pytest.mark.unit
@pytest.mark.parametrize("body, text", [
    (_WP_BLOCK_TABLE, "60만 원"),
    (_FIGURE_TABLE_WITH_CELL_IMAGE, "연회비 49,000원"),
], ids=["wp_block_table", "cell_image"])
def test_table_wrapped_in_figure_survives_conversion(tmp_path, body, text):
    """figure 분기가 첫 img 만 내보내면 figure 로 감싼 표가 본문에서 통째로 사라진다(#564)."""
    doc = _convert(tmp_path, body)

    assert len(doc.tables) == 1
    assert text in doc.export_to_markdown()


@pytest.mark.unit
def test_image_only_figure_keeps_picture_and_caption(tmp_path):
    doc = _convert(tmp_path, _IMAGE_ONLY_FIGURE)

    assert (len(doc.pictures), len(doc.tables)) == (1, 0)
    assert doc.pictures[0].caption_text(doc).strip() == "삼성카드 & MILEAGE PLATINUM(스카이패스)"


# 실물 발췌: 서울시설공단 개인정보처리방침(https://sisul.or.kr/open_content/main/guidance/pim.jsp)
# 제5조 열람 양식 표(<ul> 의 직계 자식 <table>, 원문 그대로)와 제13조 영향평가 표(<li> 안 <table>).
_SISUL_UL_DIRECT_TABLE = """<ul>
                        <li class="ssblet">공단은 정보주체 권리에 따른 열람의 요구, 정정·삭제의 요구, 처리정지의 요구 시 열람 등 요구를 한 자가 본인이거나 정당한 대리인 인지를
                            확인합니다.</li>
                        <table class="data" summary="정보주체와 법정대리인의 권리,의무 행사 방법을 위한 양식을 문서명과 첨부파일로 나누어 정리한 표입니다." width="100%">
					        <caption>정보주체와 법정대리인의 권리,의무 행사 방법을 위한 양식을 문서명과 첨부파일로 나누어 정리한 표입니다.</caption>
					        <thead>
						        <tr>
                                    <th scope="col">문서명</th>
                                    <th scope="col">첨부파일</th>
						        </tr>
					        </thead>
					        <tbody>
                                <tr>
                                    <td>개인정보 열람 등 결정 이의신청서</td>
                                    <td><a aria-label="개인정보 열람 등 결정 이의신청서" class="btn" href="/open_content/main/bbs/bbsMsgFileDown.do?bcd=upfile&amp;msg_seq=87&amp;fileno=3"><span class="down">다운로드</span></a></td>
                                </tr>
					        </tbody>
				        </table>
</ul>"""
_SISUL_TABLE_IN_LI = """<ul>
<li class="ssblet">공단은 다음 개인정보파일에 대해 영향평가를 수행하였습니다.
                            <table class="data margin_t10" summary="" width="100%">
<caption>개인정보 영향평가 수행결과를 개인정보파일명, 영향평가수행연도, 이행여부로 나누어 정리한 표</caption>
<thead>
<tr>
<th scope="col">개인정보파일명</th>
<th scope="col">영향평가수행연도</th>
<th scope="col">이행여부</th>
</tr>
</thead>
<tbody>
<tr>
<td>주차료 미납과태료 관리</td>
<td>2015</td>
<td>이행</td>
</tr>
</tbody>
</table>
</li>
</ul>"""


@pytest.mark.unit
@pytest.mark.parametrize("body, cell", [
    (_SISUL_UL_DIRECT_TABLE, "개인정보 열람 등 결정 이의신청서"),
    (_SISUL_TABLE_IN_LI, "주차료 미납과태료 관리"),
], ids=["ul_direct_table", "table_in_li"])
def test_table_inside_list_is_kept_as_table(tmp_path, body, cell):
    """목록 안의 표가 표로 남고 목록 항목 텍스트에 섞이지 않는다(#567).

    <ul> 직계 <table> 은 목록 순회에서 빠져 통째로 사라졌고, <li> 안 <table> 은 셀 텍스트가
    목록 항목 한 줄로 이어 붙어 표 구조가 사라졌다.
    """
    doc = _convert(tmp_path, body)

    assert len(doc.tables) == 1
    assert cell in [c.text for c in doc.tables[0].data.table_cells]
    assert not any(cell in t.text for t in doc.texts)


# 실물 발췌: 서울 구로구청 생활민원 안내(https://guro.go.kr/www/contents.do?key=1963, 검증 문서군 v7
# d11) 국민기초생활보장 항목의 <li>. 표 행은 머리행과 첫 행만 남겼고 닫는 태그 오기(</td>)는 원문 그대로다.
_GURO_TABLE_IN_LI = """<ul class="bu">
<li>가구규모별·급여종류별 소득인정액 기준(2025.01.현재)(단위:원)
                    <table class="table responsive">
                        <caption>가구규모별·급여종류별 소득인정액 기준 - 가구규모, 1인, 2인, 3인, 4인, 5인, 6인, 7인가구 순으로 정보 제공</caption>
						<thead>
						  <tr>
							<th scope="col">가구규모</th>
							<th scope="col">1인가구</th>
							<th scope="col">2인가구</th>
						  </tr></thead>
						<tbody class="text_center">
						  <tr>
							<th scope="row">생계급여수급자<br>(기준중위소득 32%)</td>
							<td>       765,444 </td>
							<td>     1,258,451 </td>
						  </tr>
						</tbody>
					</table>
					<p>※ 생계급여 수급자 선정기준인 기준 중위소득 32%는 동시에 생계급여 지급기준에 해당</p>
					<p>※ 8인 이상 가구의 급여별 선정기준 : 1인 증가시마다 295,559원씩 증가(8인가구 생계급여 : 3,171,856원)</p>
                </li>
</ul>"""


@pytest.mark.unit
def test_text_after_table_in_li_stays_after_table(tmp_path):
    """<li> 안 표 뒤 문장이 표 앞 항목 텍스트로 옮겨지지 않고 표는 그 항목 아래에 붙는다(#567)."""
    doc = _convert(tmp_path, _GURO_TABLE_IN_LI)

    order = [getattr(item, "text", "") or item.label for item, _ in doc.iterate_items()]
    table_at = order.index("table")
    after_at = next(i for i, t in enumerate(order) if t.startswith("※ 생계급여"))
    item = next(t for t in doc.texts if t.text.startswith("가구규모별"))
    assert table_at < after_at
    assert "※" not in item.text
    assert doc.tables[0].parent.cref == item.self_ref


# 생성 데이터: 실물(서울시설공단 개인정보처리방침, v7 d07)의 목록 안 표 형식을 따랐다. 표만 든 <li> 와
# 셀 안 이미지, <ol start> 는 실물 사례가 없어 넣은 것이다.
_GENERATED_TABLE_ONLY_ITEM = """<ol start="3">
<li class="ssblet"><table class="data margin_t10" summary="" width="100%">
<caption>개인정보 영향평가 수행결과를 개인정보파일명, 영향평가수행연도, 이행여부로 나누어 정리한 표</caption>
<thead><tr><th scope="col">개인정보파일명</th><th scope="col">영향평가수행연도</th><th scope="col">이행여부</th></tr></thead>
<tbody><tr><td>주차료 미납과태료 관리</td><td>2015</td><td><img src="/images/common/icon_check.png" alt="이행"></td></tr></tbody>
</table></li>
<li class="ssblet">공단은 영향평가 결과에 따른 개선사항을 이행하고 있습니다.</li>
</ol>"""


@pytest.mark.unit
def test_table_only_li_keeps_list_item_and_number(tmp_path):
    """표만 든 <li> 도 번호 붙은 항목으로 남고, 표가 번호 계산과 셀 이미지 수에 끼지 않는다(#567)."""
    doc = _convert(tmp_path, _GENERATED_TABLE_ONLY_ITEM)

    items = [t for t in doc.texts if t.label == "list_item"]
    assert [t.marker for t in items] == ["3.", "4."]
    assert doc.tables[0].parent.cref == items[0].self_ref
    assert len(doc.pictures) == 1


# 생성 데이터: 실물(서울시설공단 개인정보처리방침, v7 d07) 형식을 따랐다. 행이 모두 rowspan>1 <th> 인
# 표는 행 수가 0 으로 계산되어 표 파서(parse_table_data)가 IndexError 를 낸다. 실물 사례는 없다.
_ROWSPAN_ONLY = (
    '<table class="data"><tr><th rowspan="2" scope="col">개인정보파일명'
    '<img src="/images/common/icon_file.png" alt="파일"></th></tr></table>'
)


@pytest.mark.unit
@pytest.mark.parametrize("body, text, pictures", [
    (f'<ul><li class="ssblet">공단은 다음 개인정보파일에 대해 영향평가를 수행하였습니다.{_ROWSPAN_ONLY}</li></ul>',
     "개인정보파일명", 1),
    (f'<ul><li class="ssblet">공단은 다음 개인정보파일에 대해 영향평가를 수행하였습니다.</li>{_ROWSPAN_ONLY}</ul>',
     "영향평가를 수행하였습니다", 0),
], ids=["table_in_li", "ul_direct_table"])
def test_list_table_without_countable_rows_does_not_fail(tmp_path, body, text, pictures):
    """목록 안에서 행으로 셀 수 없는 표는 표로 만들지 않아 문서 변환이 실패하지 않는다(#567).

    <li> 안 표는 이전처럼 항목 텍스트로, <ul> 직계 표는 이전처럼 건너뛴다.
    """
    doc = _convert(tmp_path, body)

    assert (len(doc.tables), len(doc.pictures)) == (0, pictures)
    assert text in doc.export_to_markdown()


# 실물 발췌: 검증 문서군 v5 web13(내부 샘플 sample_11.html, 증권사 리포트를 변환한 HTML) 첫 표.
# 자기 행 없는 <table> 이 표 하나를 감싸고 있다. 행은 앞 4행과 출처 행만 남겼다.
_REPORT_WRAPPED_TABLE = (
    "<table id = 1_5 > <table border='1'><tr><td colspan=3 rowspan=1></td></tr>"
    "<tr><td colspan=3 rowspan=1>▶ 주요 지표 일일 등락률</td></tr>"
    "<tr><td colspan=1 rowspan=1>S&P 500 6,040.53pt</td><td colspan=1 rowspan=1>-0.50%</td><td colspan=1 rowspan=1></td></tr>"
    "<tr><td colspan=1 rowspan=1>DOW 44,544.66pt</td><td colspan=1 rowspan=1>-0.75%</td><td colspan=1 rowspan=1></td></tr>"
    "<tr><td colspan=3 rowspan=1>자료: Bloomberg, 삼성증권</td></tr></table> </table>"
)


@pytest.mark.unit
def test_table_wrapping_only_a_table_keeps_inner_table(tmp_path):
    """행 없이 표 하나만 감싼 바깥 표가 빈 표로 남고 안쪽 표가 사라지던 결함(#567)."""
    doc = _convert(tmp_path, _REPORT_WRAPPED_TABLE)

    assert len(doc.tables) == 1
    assert "DOW 44,544.66pt" in [c.text for c in doc.tables[0].data.table_cells]
