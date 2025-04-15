import json
import re
from typing import Union
from pathlib import Path
from pydantic import BaseModel

from constants import ARTICLENUM, DATE, DATEKOR, APPENDIX_REF, APPENDIX_FORM_REF
import logging

logger = logging.getLogger(__name__)

def replace_strip(data: list[str]) -> list[str]:
    return [x.strip() for x in data if x.strip()]


def extract_addenda_id(
    rule_id: int, addenda_data: Union[list, dict]
) -> tuple[list[str], str]:
    """
    법령/행정규칙 메타데이터에 필요한 부칙 ID를 추출하는 함수
    """

    # If "부칙" data is available, process it
    def extractor(lst):
        res = []
        for item in lst:
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

    addenda_list = addenda_data if isinstance(addenda_data, list) else [addenda_data]
    addenda, enact_date = extractor(addenda_list)

    return addenda, enact_date


def extract_appendix_id(rule_id:str, appendix_data: dict) -> list[str]:
    """
    법령/행정규칙 별표 ID를 추출하는 함수
    """
    appendices = []

    if isinstance(appendix_data, dict):
        appendix_units = appendix_data.get("별표단위", [])
        if isinstance(appendix_units, dict):
            appendix_units = [appendix_units]
        for item in appendix_units:
            if not isinstance(item, dict):
                print(rule_id, item)
                return []
            appendix_key = item.get('별표키', "")
            if len(rule_id) > 10:
                type_sign = "E" if item.get('별표구분') == "별표" else "F"
                appendix_key += type_sign
            appendices.append(f'{rule_id}{appendix_key}')
    return appendices


def extract_date_to_yyyymmdd(text:str, date_korean:bool=False) -> list[str]:
    """ 문자열에서 YYYY.MM.DD 또는 YYYY년 MM월 DD일 형식의 날짜를 추출하여 YYYYMMDD로 변환 """

    date_list = re.findall(DATE, text)
    if not date_list and date_korean:  # DATE(Regex) 결과가 없고, date_korean = DATEKOR(Regex) 사용
        date_list = re.findall(DATEKOR, text)
    
    return [f"{year}{int(month):02d}{int(day):02d}" for year, month, day in date_list]


def get_latest_date(dates:list[str], enact_date:str) -> str:
    """ 날짜 리스트에서 가장 최신 날짜를 반환 (없으면 enact_date 반환) """
    return max(dates) if dates else enact_date


def extract_article_num(law_id:str, text: Union[str, list[str]], lst=False) -> Union[tuple[str,str,str], list[str]]:
    """
    텍스트에서 조문 번호를 추출합니다.
    "(제 xx조의 xx~~)" 또는 "(제 xx조)" 패턴을 찾아 조문 ID 리스트를 반환합니다.
    조문 번호가 없으면 []을 반환합니다.
    현재 행정규칙, 별표 데이터에서 사용
    """
    article_ids = []
    text = ''.join(text) if isinstance(text, list) else text
    match = re.search(ARTICLENUM, text)
    if match:
        main_num = int(match.group(1))  # 본조 번호
        sub_num = (
            int(match.group(2)) if match.group(2) else 1
        )  # '의' 조문 번호 (없으면 1)
        article_id = f"{law_id}{main_num:04d}{sub_num:03d}"
        if not lst:
            return article_id, main_num, sub_num
        else :
            article_ids.append(article_id)
    return article_ids



def extract_related_appendices(law_id: str, article_content: Union[str, list[str]]) -> list[str]:
    '''
    조문 내용에서 인용된 별표를 검색하고 별표 ID 리스트를 생성하는 함수
    '''
    content_text = " ".join(article_content) if isinstance(article_content, list) else article_content
    related_appendices = set()
    
    # 별표 검색
    appendix_pattern = re.compile(APPENDIX_REF)
    for match in appendix_pattern.findall(content_text):
        appendix_num = int(match[0])
        appendix_sub_num = int(match[1]) if match[1] else 0
        appendix_id = f"{law_id}{appendix_num:04d}{appendix_sub_num:02d}E"
        related_appendices.add(appendix_id)

    # 별지 검색
    appendix_form_pattern = re.compile(APPENDIX_FORM_REF)
    for match in appendix_form_pattern.findall(content_text):
        appendix_num = int(match[0])
        appendix_sub_num = int(match[1]) if match[1] else 0
        appendix_id = f"{law_id}{appendix_num:04d}{appendix_sub_num:02d}F"
        related_appendices.add(appendix_id)

    return list(related_appendices)



def export_json(data, KEY, id):
    output_file = f"result/{KEY}/{KEY}_data_{id}.json"

    logger.info(f"JSON 데이터 저장: KEY={id}, 파일 경로={output_file}\n")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_json_as_model(json_file: str, model: type[BaseModel]) -> BaseModel:
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return model.model_validate(data)


def load_json(key, file_name):
    base_path = Path(__file__).parent.parent / "data_files"  # legal_parser 기준으로 상위 폴더 접근
    file_path = base_path / f"{key}" / f"{file_name}"

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)
