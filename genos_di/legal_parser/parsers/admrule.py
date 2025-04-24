<<<<<<< HEAD
from commons.regex_handler import RegexProcessor
from commons.type_converter import TypeConverter
from parsers.extractor import (
    extract_addenda_id,
    extract_appendix_id,
=======
import re

from extractor import (
    extract_addenda_id,
    extract_appendix_id,
)
from schemas import (
    AdmRuleMetadata,
    FileAttached,
    ParserContent,
)
from  extractor import (
    extract_addenda_id,
    extract_appendix_id,
    extract_article_num,
    extract_date_to_yyyymmdd,
    get_latest_date,
    replace_strip,
    extract_related_appendices,
    load_json
>>>>>>> 1d622be (fix: improve legal data parser)
)
from schemas.law_schema import AdmRuleMetadata, FileAttached
from schemas.schema import ParserContent

type_converter = TypeConverter()
regex_processor = RegexProcessor()


def parse_admrule_info(admrule_id: str, admrule_data: dict, hierarchy_laws, connected_laws) -> ParserContent:
    """
    행정규칙 정보를 파싱하여 ParserContent 객체로 반환하는 함수

    Args:
        admrule_id (str): 행정규칙 ID
        admrule_data (dict): 행정규칙 데이터 딕셔너리
        hierarchy_laws: 상위법 정보
        connected_laws: 연계법 정보

    Returns:
        ParserContent: 메타데이터와 내용이 포함된 ParserContent 객체
    """
    # 행정규칙 기본 정보 추출
    basic_info = _extract_basic_info(admrule_data)
    
<<<<<<< HEAD
    # 부칙 정보 및 시행일자 추출
    addenda, enact_date = _extract_addenda_info(admrule_id, admrule_data)
    
    # 별표(부속서류) 정보 추출
    appendices = _extract_appendix_info(admrule_id, admrule_data)
    
    # 첨부파일 정보 추출
    file_attached = _extract_file_attachments(admrule_data)
    
    # 메타데이터 객체 생성
    metadata = _create_admrule_metadata(
=======
    # 기본정보
    basic_info = admrule_data["행정규칙기본정보"]

    admrule_num = basic_info.get("행정규칙ID", "")
    announce_num = basic_info.get("발령번호", "")
    announce_date = basic_info.get("발령일자", "")
    enforce_date = basic_info.get("시행일자", "")
    rule_name = basic_info.get("행정규칙명", "")
    rule_type = basic_info.get("행정규칙종류", "")
    article_form = True if basic_info.get("조문형식여부") == "Y" else False
    is_effective = 0 if basic_info.get("현행여부") == "Y" else -1
    dept = basic_info.get("담당부서기관명", "")

    ## 부칙, 별표 ID 리스트
    appendices = extract_appendix_id(admrule_id, admrule_data.get("별표"))
    addenda, enact_date = extract_addenda_id(admrule_id, admrule_data.get("부칙"))

    ## 첨부파일 리스트
    attachments = admrule_data.get("첨부파일", {})
    if attachments:
        file_attached = [
            FileAttached(
                id=link.split("flSeq=")[-1],
                filename=name,
                filelink=link
            )
            for link, name in zip(attachments["첨부파일링크"], attachments["첨부파일명"])
        ]
    else:
        file_attached = []


    metadata = AdmRuleMetadata(
>>>>>>> 1d622be (fix: improve legal data parser)
        admrule_id=admrule_id,
        basic_info=basic_info,
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,
<<<<<<< HEAD
        addenda=addenda,
        appendices=appendices,
        enact_date=enact_date,
        file_attached=file_attached
    )
    
    # ParserContent 객체 반환 (content는 비어 있음)
    return ParserContent(metadata=metadata, content=[])

def _extract_basic_info(admrule_data: dict) -> dict:
    """
    행정규칙 기본 정보를 admrule_data에서 추출하는 함수

    Args:
        admrule_data (dict): 행정규칙 데이터 딕셔너리

    Returns:
        dict: 메타데이터 기본 정보가 담긴 딕셔너리
    """
    # admrule_data에서 '행정규칙기본정보' 키의 값을 가져옴, 없으면 빈 dict
    basic_info = admrule_data.get("행정규칙기본정보", {})
    
    # 각 필드별로 값 추출, 없는 경우 기본값 적용
    return {
        "admrule_num": basic_info.get("행정규칙ID", ""),
        "announce_num": basic_info.get("발령번호", ""),
        "announce_date": basic_info.get("발령일자", ""),
        "enforce_date": basic_info.get("시행일자", ""),
        "rule_name": basic_info.get("행정규칙명", ""),
        "rule_type": basic_info.get("행정규칙종류", ""),
        "article_form": True if basic_info.get("조문형식여부") == "Y" else False,
        "is_effective": 0 if basic_info.get("현행여부") == "Y" else -1,
        "dept": basic_info.get("담당부서기관명", "")
    }

def _extract_addenda_info(admrule_id: str, admrule_data: dict) -> tuple[list, str]:
    """
    부칙 정보를 admrule_data에서 추출하는 함수

    Args:
        admrule_id (str): 행정규칙 ID
        admrule_data (dict): 행정규칙 데이터 딕셔너리

    Returns:
        tuple[list, str]: (부칙 목록, 시행일자)
    """
    addenda = []
    enact_date = "00000000"  # 기본 시행일자

    # admrule_data에 '부칙' 정보가 있을 경우 처리
    if admrule_data.get("부칙"):
        try:
            # 부칙 데이터를 list[dict]로 변환
            addenda_data = type_converter.converter(admrule_data.get("부칙"), list[dict])
            # 부칙 ID 및 시행일자 추출
            addenda, enact_date = extract_addenda_id(admrule_id, addenda_data)
        except Exception:
            # 변환 실패 시 기본값 유지
            pass
    
    return addenda, enact_date

def _extract_appendix_info(admrule_id: str, admrule_data: dict) -> list:
    """
    별표(부속서류) 정보를 admrule_data에서 추출하는 함수

    Args:
        admrule_id (str): 행정규칙 ID
        admrule_data (dict): 행정규칙 데이터 딕셔너리

    Returns:
        list: 별표 정보 목록
    """
    appendices = []
    appendix_data = admrule_data.get("별표")
    
    # '별표' 정보가 있고, 그 안에 '별표단위'가 있을 경우 처리
    if appendix_data and appendix_data.get("별표단위"):
        try:
            # 별표단위 데이터를 list[dict]로 변환
            appendix_units = type_converter.converter(appendix_data.get("별표단위"), list[dict])
            # 별표 ID 추출
            appendices = extract_appendix_id(admrule_id, appendix_units)
        except Exception:
            # 변환 실패 시 기본값 유지
            pass
    
    return appendices

def _extract_file_attachments(admrule_data: dict) -> list[FileAttached]:
    """
    첨부파일 정보를 admrule_data에서 추출하는 함수

    Args:
        admrule_data (dict): 행정규칙 데이터 딕셔너리

    Returns:
        list[FileAttached]: 첨부파일 객체 리스트
    """
    file_attached = []
    attachments = admrule_data.get("첨부파일")
    
    # 첨부파일 정보가 dict 형태로 유효할 경우 처리
    if type_converter.validator(attachments, dict):
        # 첨부파일 링크와 이름을 쌍으로 묶어서 FileAttached 객체 생성
        file_attached = [
            FileAttached(
                id=link.split("flSeq=")[-1],  # 링크에서 파일 ID 추출
                filename=name,
                filelink=link
            )
            for link, name in zip(attachments.get("첨부파일링크", []), attachments.get("첨부파일명", []))
        ]
    
    return file_attached

def _create_admrule_metadata(
    admrule_id: str,
    basic_info: dict,
    hierarchy_laws: list,
    connected_laws: list,
    addenda: list,
    appendices: list,
    enact_date: str,
    file_attached: list
) -> AdmRuleMetadata:
    """
    행정규칙 메타데이터 객체를 생성하는 함수

    Args:
        admrule_id (str): 행정규칙 ID
        basic_info (dict): 기본 정보 딕셔너리
        hierarchy_laws (list): 상위법 목록
        connected_laws (list): 연계법 목록
        addenda (list): 부칙 목록
        appendices (list): 별표 목록
        enact_date (str): 시행일자
        file_attached (list): 첨부파일 목록

    Returns:
        AdmRuleMetadata: 행정규칙 메타데이터 객체
    """
    # AdmRuleMetadata 객체 생성 및 반환
    return AdmRuleMetadata(
        admrule_id=admrule_id,
        admrule_num=basic_info["admrule_num"],
        announce_num=basic_info["announce_num"],
        announce_date=basic_info["announce_date"],
        enforce_date=basic_info["enforce_date"],
        rule_name=basic_info["rule_name"],
        rule_type=basic_info["rule_type"],
        article_form=basic_info["article_form"],
        is_effective=basic_info["is_effective"],
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,
        related_addenda_admrule=addenda,
        related_appendices_admrule=appendices,
        dept=basic_info["dept"],
        enact_date=enact_date,
        file_attached=file_attached,
    )
=======
        related_addenda_admrule=addenda,
        related_appendices_admrule=appendices,
        dept=basic_info["dept"],
        enact_date=enact_date,
        file_attached=file_attached,
    )

    return ParserContent(
        metadata=metadata,
        content=[]
    )


# 행정규칙 조회 -> 행정규칙 조문
def parse_admrule_article_info(admrule_info: RuleInfo, article_list:list[str]) -> list[ParserContent]:
    """행정규칙 조문 처리
    """
    if not article_list:
        return []
    
    admrule_articles = []
    
    admrule_id = admrule_info.rule_id
    enfoce_date = admrule_info.enforce_date
    is_effective = admrule_info.is_effective
    enact_date = admrule_info.enact_date

    article_chapter = ArticleChapter()
    current_chapter = None

    article_list = article_list if isinstance(article_list, list) else [article_list]

    for article in article_list:
        
        article_chapter.extract_text(article)
        is_preamble = bool(regex_processor.search("IS_PREAMBLE", article))
        if is_preamble:
            article_num = article_chapter.chapter_num
            article_sub_num = 0
            article_id = f"{admrule_id}{article_num:04d}{article_sub_num:03d}"
            current_chapter = ArticleChapter(
                chapter_num=article_chapter.chapter_num,
                chapter_title=article_chapter.chapter_title,
                section_num=article_chapter.section_num,
                section_title=article_chapter.section_title,
            )
            title = article_chapter.chapter_title
        else :
            article_id, article_num, article_sub_num = extract_article_num(admrule_id, article)

        
        # 3. 조문 제목 추출: () 안의 첫 번째 문자열
        title_match = regex_processor.search("BLANKET", article)
        title = title_match.group(1) if title_match else ""

        # 4. 개정일자 추출: "(개정 yyyy. m. d.,  yyyy. m. d.)" 형식에 맞는 날짜 찾기
        matches = regex_processor.findall("BLANKET_DATE", article)
        date_matches = []
        for match in matches:
            # 쉼표로 구분된 모든 날짜를 찾기
            date_matches.extend(extract_date_to_yyyymmdd(match))    
        # 최신 날짜 선택
        announce_date = get_latest_date(date_matches, enact_date)

        # 5. 삭제된 조문 처리
        if "삭제" in article:
            announce_date_match = regex_processor.search("CHEVRON_DATE", article)
            if announce_date_match:
                year, month, day = announce_date_match.groups()
                announce_date = format_date(year, month, day)
            title = "삭제"
        
        # 조문 내용 처리
        content = replace_strip(article.split("\n"))

        related_appendices = extract_related_appendices(admrule_id, content)


        # 메타데이터 생성
        metadata = AdmRuleArticleMetadata(
            article_id=article_id,
            article_num=article_num,
            article_sub_num=article_sub_num,
            article_title=title,
            article_chapter=current_chapter or article_chapter,
            enforce_date=enfoce_date,
            announce_date=announce_date,
            admrule_id=admrule_id,
            is_effective=is_effective,
            is_preamble=is_preamble,
            related_addenda=[],
            related_appendices=related_appendices,
        )
        parsed_article = ParserContent(
            metadata=metadata,
            content=content
        )
        admrule_articles.append(parsed_article)

    return admrule_articles



if __name__ == '__main__':
    data = load_json("264627","response_1743555531884" )
    admrule_info = RuleInfo("2100000237816", "20240517", "20000517", 0)
    article_list = data.get("AdmRulService").get("조문내용")
    res = parse_admrule_article_info(admrule_info, article_list)
    print(res[6].metadata)
>>>>>>> 1d622be (fix: improve legal data parser)
