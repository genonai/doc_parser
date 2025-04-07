import re

from constants import AMENDDATE
from schemas import AppendixMetadata, ParserContent, RuleInfo
from utils import (
    extract_article_num,
    extract_date_to_yyyymmdd,
    get_latest_date,
    replace_strip,
)


# 법령본문 조회 -> 별표
def parse_appendix_info(rule_info: RuleInfo, appendix_data: dict) -> list[ParserContent]:
            
    appendix_list = []
    appendix_units = appendix_data.get("별표단위", [])

    rule_id = rule_info.id
    is_effective = rule_info.is_effective
    enforce_date = rule_info.enforce_date
    enact_date = rule_info.enact_date
    
    for item in appendix_units:
        appendix_id = f"{rule_id}{item.get('별표번호', '')}{item.get('별표가지번호', '')}"
        file_link = item.get("별표서식파일링크")
        appendix_num = file_link.split("flSeq=")[-1]
        appendix_link = f"https://www.law.go.kr{file_link}"

        appendix_type = item.get("별표구분", "")  # 별표 구분 (별지, 서식 등)
        appendix_title = item.get("별표제목", "")
        appendix_content = replace_strip(item.get("별표내용")[0])
        articles = [f"{rule_id}{num}" for num in extract_article_num(appendix_title, lst=True)] 

        # 별표 내용에서 최신 개정일자를 추출
        matches = re.findall(AMENDDATE, appendix_content[0])
        if matches:
            format_dates = extract_date_to_yyyymmdd(matches[0])
            announce_date = get_latest_date(format_dates, enact_date)
        else :
            announce_date = enact_date

        appendix_metadata = AppendixMetadata(
            appendix_id=appendix_id,
            appendix_num=appendix_num,
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