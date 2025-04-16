<<<<<<< HEAD
from datetime import datetime

from commons.constants import LAWFIELD
from commons.type_converter import TypeConverter
from parsers.extractor import (
    extract_addenda_id,
    extract_appendix_id,
=======
from constants import LAWFIELD
from extractor import (
    extract_addenda_id,
    extract_appendix_id,
    extract_date_to_yyyymmdd,
    extract_related_appendices,
    get_latest_date,
    replace_strip,
>>>>>>> 1d622be (fix: improve legal data parser)
)
from schemas.law_schema import LawMetadata
from schemas.schema import ParserContent

type_converter = TypeConverter()

<<<<<<< HEAD
def _extract_department_info(office: dict) -> str:
    """소관부처 정보를 추출하는 함수"""
    if type_converter.validator(office, dict):
        return f"{office['소관부처명']} {office['부서명']}"
    elif type_converter.validator(office, list[dict]):
        return f"{office[0]['소관부처명']}"
    else :
        return ""

def _extract_law_field(law: dict) -> str:
    """법 분야명을 추출하는 함수"""
    return LAWFIELD.get(int(law.get("편장절관", "00")[:2]))

def _extract_addenda_info(law_id: str, law_data: dict) -> tuple[list[str], str]:
    """부칙 정보를 추출하는 함수"""
    addenda = []
    enact_date = "00000000"
    addenda_data = law_data.get("부칙")
=======

# 법령본문 조회 -> 법령
def parse_law_info(law_id: str, law_data: dict, hierarchy_laws, connected_laws) -> ParserContent:
    law:dict = law_data.get('기본정보')

    # 소관부처 : 소관부처명 + 연락부서 부서명
    office = law["연락부서"]["부서단위"]
    dept = (
        f"{office['소관부처명']} {office['부서명']}"
        if isinstance(office, dict)
        else f"{office[0]['소관부처명']}"
    )
    #법 분야명: 편 번호 -> 법 분야 dict에서 조회
    law_field = LAWFIELD.get(int(law.get("편장절관")[:2]))
>>>>>>> 1d622be (fix: improve legal data parser)
    
    if addenda_data and addenda_data.get("부칙단위"):
        addenda_units = type_converter.converter(addenda_data.get("부칙단위"), list[dict])
        addenda, enact_date = extract_addenda_id(law_id, addenda_units)
    
    return addenda, enact_date

<<<<<<< HEAD
def _extract_appendix_info(law_id: str, law_data: dict) -> list[str]:
    """별표 정보를 추출하는 함수"""
    appendices = []
    appendix_data = law_data.get("별표")
    
    if appendix_data and appendix_data.get("별표단위"):
        appendix_units = type_converter.converter(appendix_data.get("별표단위"), list[dict])
        appendices = extract_appendix_id(law_id, appendix_units)
    
    return appendices
=======
    ## 별표 ID 리스트
    appendix_data = law_data.get("별표", {})
    appendices = extract_appendix_id(law_id, appendix_data)
>>>>>>> 1d622be (fix: improve legal data parser)

def _extract_is_effective_info(enforce_date:str) -> int:
    "시행일자를 기준으로 현재 시행 예정인지(1) 혹은 현행(0)인지 추출하는 함수"
    today = datetime.now().strftime("%Y%m%d")
    return 1 if enforce_date > today else 0

def _create_law_metadata(
    law_id: str,
    law: dict,
    law_field: str,
    hierarchy_laws: list,
    connected_laws: list,
    addenda: list,
    appendices: list,
    dept: str,
    enact_date: str,
    is_effective: int
) -> LawMetadata:
    return LawMetadata(
        law_id=law_id,
        law_num=law.get("법령ID"),
        announce_num=law.get("공포번호"),
        announce_date=law.get("공포일자"),
        enforce_date=law.get("시행일자"),
        law_name=law.get("법령명_한글"),
        law_short_name=law.get("법령명약칭"),
        law_type=law.get("법종구분", {}).get("content", ""),
        law_field=law_field,
<<<<<<< HEAD
        is_effective=is_effective, 
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,  
        related_addenda_law=addenda,  
        related_appendices_law=appendices,  
        dept=dept if dept else None,
        enact_date=enact_date,
    )
    

# 법령본문 조회 -> 법령
def parse_law_info(law_id: str, law_data: dict, hierarchy_laws, connected_laws) -> ParserContent:
    law:dict = law_data.get('기본정보')

    # 소관부처 : 소관부처명 + 연락부서 부서명
    office = law.get("연락부서", {}).get("부서단위")
    dept = _extract_department_info(office)

    # 현행 여부
    is_effective = _extract_is_effective_info(law.get("시행일자"))

    ## 법 분야명
    law_field = _extract_law_field(law)
    
    ## 부칙 ID 리스트
    addenda, enact_date = _extract_addenda_info(law_id, law_data)

    ## 별표 ID 리스트
    appendices = _extract_appendix_info(law_id, law_data)

    metadata = _create_law_metadata(
        law_id=law_id,
        law=law,
        law_field=law_field,
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,
        addenda=addenda,
        appendices=appendices,
        dept=dept,
        enact_date=enact_date,
        is_effective=is_effective
    )

    return ParserContent(metadata=metadata, content=[])
=======
        is_effective=0, 
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,  
        related_addenda_law=addenda,  
        related_appendices_law=appendices,  
        dept=dept if dept else None,
        enact_date=enact_date,
    )

    return ParserContent(
        metadata=metadata,
        content=[]
    )

def extract_latest_announce(data: dict, enact_date:str) -> str:
    """조문 내용, 조문 참고자료, 항 내용, 호 내용에서 가장 최신의 개정 날짜를 추출하여 내용과 함께 반환합니다.
    """
    def extract_amendment_dates(data:dict) -> list[str] :
        dates = []

        # 조문내용에서 개정일 추출
        if "조문내용" in data and data["조문내용"]:
            dates.extend(extract_date_to_yyyymmdd(data["조문내용"]))

        # 조문참고자료에서 개정일 추출
        if "조문참고자료" in data and data["조문참고자료"]:
            reference_data = data["조문참고자료"]

            # 조문참고자료가 문자열인 경우
            if isinstance(reference_data, str):
                dates.extend(extract_date_to_yyyymmdd(reference_data))

            # 조문참고자료가 2차원 리스트인 경우
            elif isinstance(reference_data, list) and reference_data:
                for item in reference_data[0]:
                    dates.extend(extract_date_to_yyyymmdd(item))

        # 항 내용에서 개정일 추출
        if "항" in data and data["항"]:
            paragraph = data["항"]

            if isinstance(paragraph, list):
                for item in paragraph:
                    if "항제개정일자문자열" in item:
                        dates.extend(extract_date_to_yyyymmdd(item["항제개정일자문자열"]))
                        return dates
                    
                    if "항내용" in item:
                        text = (
                            item["항내용"][0][0]
                            if isinstance(item["항내용"], list)
                            else item["항내용"]
                        )
                        dates.extend(extract_date_to_yyyymmdd(text))

            # 항이 dict일 경우, 호 내용을 검사
            elif isinstance(paragraph, dict) and "호" in paragraph:
                for item in paragraph["호"]:
                    if "호내용" in item:
                        text = (
                            item["호내용"][0][0]
                            if isinstance(item["호내용"], list)
                            else item["호내용"]
                        )
                        dates.extend(extract_date_to_yyyymmdd(text, True))

        # 가장 최신 날짜 반환
        return dates
    
    amendment_dates = extract_amendment_dates(data)
    return get_latest_date(amendment_dates, enact_date)

# 법령 조문 내용 처리
def stringify_article_content(data: dict) -> list[str]:
    """법령 조문 데이터를 문자열 리스트로 변환하는 함수
    """
    content = []

    # 조문 내용 추가
    if "조문내용" in data and data["조문내용"]:
        content.append(data["조문내용"].strip())

    # 항 내용 처리 함수
    def process_paragraphs(paragraphs):
        for paragraph in paragraphs:
            if "항내용" in paragraph:
                text = paragraph["항내용"]
                if isinstance(text, list):
                    text = text[0][0] if text and isinstance(text[0], list) else text[0]
                content.append(text.strip())

            # 호(조항) 처리
            if "호" in paragraph:
                process_subparagraphs(paragraph["호"])

    # 호 내용 처리 함수
    def process_subparagraphs(subparagraphs):
        for subparagraph in subparagraphs:
            if "호내용" in subparagraph:
                text = subparagraph["호내용"]
                if isinstance(text, list):
                    text = text[0][0] if text and isinstance(text[0], list) else text[0]
                content.append(text.strip())

            # 목(세부 조항) 처리
            if "목" in subparagraph:
                process_items(subparagraph["목"])

    # 목 내용 처리 함수
    def process_items(items):
        for item in items:
            if isinstance(item["목내용"], list):
                text = replace_strip(item["목내용"][0])
                content.extend(text)
            else:
                text = item["목내용"]
                content.append(text.strip())

    # 항이 리스트 또는 딕셔너리인 경우 모두 처리
    paragraphs = data.get("항", [])
    if isinstance(paragraphs, dict):
        paragraphs = [paragraphs]

    if paragraphs:
        process_paragraphs(paragraphs)

    return content

# 법령본문 조회 -> 조문
def parse_law_article_info(law_info:RuleInfo, article_data:dict) -> list[ParserContent]:
    
    article_list = []
    
    law_id = law_info.rule_id
    enact_date = law_info.enact_date
    is_effective = law_info.is_effective

    article_units = article_data.get("조문단위", [])

    article_chapter = ArticleChapter()
    current_chapter = None

    for item in article_units:

        article_num = item.get("조문번호")
        article_sub_num = item.get("조문가지번호") or 1
        article_id = f"{law_id}{int(article_num):04d}{int(article_sub_num):03d}"
    
        article_title = item.get("조문제목", "")
        enforce_date = item.get("조문시행일자")

        is_preamble = True if item.get("조문여부") == "전문" else False

        # 전문인 경우 장 번호로 article_num 대체
        if is_preamble:
            content = item.get("조문내용", "")
            article_chapter.extract_text(content)
            current_chapter = ArticleChapter(
                chapter_num=article_chapter.chapter_num,
                chapter_title=article_chapter.chapter_title,
                section_num=article_chapter.section_num,
                section_title=article_chapter.section_title,
            )
            article_num = f"{article_chapter.chapter_num}"
            article_sub_num = 0
        
        announce_date = extract_latest_announce(item, enact_date)

        article_content = stringify_article_content(item)

        # 인용된 별표 ID 추출
        related_appendices = extract_related_appendices(law_id, article_content)    

        artice_meta = LawArticleMetadata(
            article_id=article_id,
            article_num=article_num,
            article_sub_num=article_sub_num,
            is_preamble=is_preamble,
            article_title=article_title,
            article_chapter=current_chapter or article_chapter,
            enforce_date=enforce_date,
            announce_date=announce_date,
            law_id=law_id,
            is_effective=is_effective,
            related_laws=[],
            related_appendices=related_appendices,
            related_addenda=[],
            related_articles=[],
        )

        article_result = ParserContent(
            metadata=artice_meta,
            content=article_content
        )

        article_list.append(article_result)    
    return article_list
>>>>>>> 5d45219 (feat: Add legal data parser (fastapi))
