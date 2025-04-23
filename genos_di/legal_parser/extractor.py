from typing import Union

from utils.regex_handler import regex_processor
from utils.helpers import format_date

def extract_addenda_id(
    rule_id: int, addenda_list: list[dict]
) -> tuple[list[str], str]:
    """법령/행정규칙 메타데이터에 필요한 부칙 ID를 추출하는 함수
        # TODO input : list여야 함 원래 dict도 받았음
    """
    if not addenda_list:
        return [], "00000000"
    
    # If "부칙" data is available, process it
    def extractor(addenda_list: list[dict]):
        res = []
        for item in addenda_list:
            # Handle single 부칙
            announce_date = item.get("부칙공포일자")
            if isinstance(announce_date, str):
                res.append(f"{rule_id}{announce_date}")
            # Handle multiple 부칙
            elif isinstance(announce_date, list):
                for date in announce_date:
                    res.append(f"{rule_id}{date}")
            enact_date = res[0][-8:]
        return res, enact_date
    
    addenda, enact_date = extractor(addenda_list)

    return addenda, enact_date


def extract_appendix_id(rule_id:str, appendix_units: list[dict]) -> list[str]:
    """법령/행정규칙 별표 ID를 추출하는 함수
    """
    if not appendix_units:
        return []
    
    appendices = []
    for item in appendix_units:
        appendix_key = item.get('별표키', "")
        if len(rule_id) > 10:
            type_sign = "E" if item.get('별표구분') == "별표" else "F"
            appendix_key += type_sign
        appendices.append(f'{rule_id}{appendix_key}')
    return appendices


def extract_date_to_yyyymmdd(text:str, date_korean:bool=False) -> list[str]:
    """문자열에서 YYYY.MM.DD 또는 YYYY년 MM월 DD일 형식의 날짜를 추출하여 YYYYMMDD로 변환"""
    date_list = regex_processor.findall("DATE", text)
    if not date_list and date_korean:  # DATE(Regex) 결과가 없고, date_korean = DATEKOR(Regex) 사용
        date_list = regex_processor.findall("DATE_KOR", text)
    
    return [format_date(year, month, day) for year, month, day in date_list]


def get_latest_date(dates:list[str], enact_date:str) -> str:
    """날짜 리스트에서 가장 최신 날짜를 반환 (없으면 enact_date 반환)"""
    return max(dates) if dates else enact_date


def extract_article_num(law_id:str, text: str, lst=False) -> Union[tuple[str,str,str], list[str]]:
    """텍스트에서 조문 번호를 추출합니다.
    "(제 xx조의 xx~~)" 또는 "(제 xx조)" 패턴을 찾아 조문 ID 리스트를 반환합니다.
    조문 번호가 없으면 []을 반환합니다.
    현재 행정규칙, 별표 데이터에서 사용

    # TODO 호출하는 함수에서 LIST[STR] -> STR로 변환
    """
    article_ids = []
    main_num = 1
    sub_num = 1

    match = regex_processor.search("ARTICLE_NUM", text)
    if match :
        main_num = int(match.group(1))  # 본조 번호
        sub_num = (
            int(match.group(2)) if match.group(2) else sub_num
        )  # '의' 조문 번호 (없으면 1)
    article_id = f"{law_id}{main_num:04d}{sub_num:03d}"
    if not lst:
        return article_id, main_num, sub_num
    else :
        article_ids.append(article_id)
        return article_ids



def extract_related_appendices(law_id: str, article_content: Union[str, list[str]]) -> list[str]:
    """조문 내용에서 인용된 별표를 검색하고 별표 ID 리스트를 생성하는 함수
    """
    content_text = " ".join(article_content) if isinstance(article_content, list) else article_content
    related_appendices = set()
    
    # 별표 검색
    for match in regex_processor.findall("APPENDIX_REF", content_text):
        appendix_num = int(match[0])
        appendix_sub_num = int(match[1]) if match[1] else 0
        appendix_id = f"{law_id}{appendix_num:04d}{appendix_sub_num:02d}E"
        related_appendices.add(appendix_id)

    # 별지 검색
    for match in regex_processor.findall("APPENDIX_FORM_REF", content_text):
        appendix_num = int(match[0])
        appendix_sub_num = int(match[1]) if match[1] else 0
        appendix_id = f"{law_id}{appendix_num:04d}{appendix_sub_num:02d}F"
        related_appendices.add(appendix_id)

    return list(related_appendices)
