import re

from constants import AMENDDATE, BLANCKETDATE
from schemas import AppendixMetadata, ParserContent, RuleInfo
from extractor import (
    extract_article_num,
    extract_date_to_yyyymmdd,
    get_latest_date,
    replace_strip,
)



def parse_appendix_info(rule_info: RuleInfo, appendix_data: dict, is_admrule: bool = False) -> list[ParserContent]:
    """
    법령 또는 행정규칙의 별표 정보를 파싱하여 구조화된 콘텐츠 리스트를 반환합니다.

    이 함수는 입력된 별표 데이터에서 주요 메타데이터(별표번호, 제목, 링크 등)와 본문 내용을 추출하고,
    관련 조문 및 개정일, 시행일 등의 정보를 포함한 ParserContent 객체로 반환합니다.

    Args:
        rule_info (RuleInfo): 법령 또는 행정규칙에 대한 기본 정보 (rule_id, is_effective, enact_date 등 포함).
        appendix_data (dict): 별표(raw JSON 형식) 데이터.
        is_admrule (bool, optional): 행정규칙 여부를 나타내는 플래그. True이면 행정규칙 형식으로 파싱함. 
                                     False이면 법령 형식으로 처리. 기본값은 False.

    Returns:
        list[ParserContent]: 파싱된 별표 콘텐츠와 메타데이터가 포함된 ParserContent 객체의 리스트.
    """
   
    appendix_list = []
    appendix_units = appendix_data.get("별표단위", [])

    if isinstance(appendix_units, dict):
        appendix_units = [appendix_units]

    rule_id = rule_info.rule_id
    is_effective = rule_info.is_effective
    enact_date = rule_info.enact_date
    
    for item in appendix_units:
        appendix_key = item.get("별표키")
        appendix_type = item.get("별표구분", "")  # 별표 구분 (별표, 별지, 서식 등)

        if is_admrule:
            type_sign = "E" if appendix_type == "별표" else "F"
            appendix_key = type_sign

        appendix_id = f"{rule_id}{appendix_key}"
        appendix_num = int(item.get("별표번호"))
        appendix_sub_num = int(item.get("별표가지번호", 0))
        file_link = item.get("별표서식파일링크")
        appendix_seq_num = file_link.split("flSeq=")[-1]
        appendix_link = f"https://www.law.go.kr{file_link}"

        appendix_title = item.get("별표제목", "")
        appendix_content = replace_strip(item.get("별표내용")[0])

        article_text = appendix_content[0] if is_admrule else appendix_title
        articles = extract_article_num(rule_id, article_text, lst=True)

        # 별표 내용에서 최신 개정일자를 추출
        matched_date = None
        if is_admrule:
            # 행정규칙의 경우 BLANCKETDATE 패턴도 함께 고려 (group(1) 있을 수 있음)
            amend_date_matches = re.search(rf"{AMENDDATE}|{BLANCKETDATE}", appendix_content[0])
            if amend_date_matches:
                matched_date = amend_date_matches.group(1) or amend_date_matches.group(0)
        else:
            amend_date_matches = re.search(AMENDDATE, appendix_content[0])
            matched_date = amend_date_matches.group(0) if amend_date_matches else None

        if matched_date:
            format_dates = extract_date_to_yyyymmdd(matched_date)
            announce_date = get_latest_date(format_dates, enact_date)
            enforce_date = announce_date  # NOTE: 시행일 = 개정일 또는 법령 시행일
        else:
            announce_date = enact_date
            enforce_date = rule_info.enforce_date

        appendix_metadata = AppendixMetadata(
            appendix_id=appendix_id,
            appendix_num=appendix_num,
            appendix_sub_num=appendix_sub_num,
            appendix_seq_num=appendix_seq_num,
            appendix_type=appendix_type,
            appendix_title=appendix_title,
            appendix_link=appendix_link,
            announce_date=announce_date,
            enforce_date=enforce_date,
            is_effective=is_effective,
            law_id=rule_id,
            related_articles=articles
        )

        appendix_result = ParserContent(
            content=appendix_content,
            metadata=appendix_metadata
        )
    
        appendix_list.append(appendix_result)
    
    return appendix_list
