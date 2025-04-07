import json
import re
from typing import Union
from pydantic import BaseModel

from constants import ARTICLENUM, DATE, DATEKOR
from datetime import datetime
import logging



def replace_empty_with_none(data: dict) -> dict:
    """
    빈 문자열을 None으로 변경하는 함수
    """
    for key, value in data.items():
        if value == "":
            data[key] = None
    return data


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
    별표 ID를 추출하는 함수
    """
    appendices = []

    if appendix_data:
        appendix_units = appendix_data.get("별표단위", [])
        appendices = [
            f"{rule_id}{item.get('별표번호', '')}{item.get('별표가지번호', '00')}"
            for item in appendix_units
            if "별표번호" in item
        ]

    return appendices



def extract_latest_announce(data: dict, enact_date:str) -> str:
    """
    조문 내용, 조문 참고자료, 항 내용, 호 내용에서 가장 최신의 개정 날짜를 추출하여 내용과 함께 반환합니다.
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
                # matches = re.findall(SQUAREBLANCKET, reference_data)  # 대괄호 안의 내용 찾기
                # for match in matches:
                    # dates.extend(extract_date_to_yyyymmdd(match))
                dates.extend(extract_date_to_yyyymmdd(reference_data))

            # 조문참고자료가 2차원 리스트인 경우
            elif isinstance(reference_data, list) and reference_data:
                for item in reference_data[0]:
                    # matches = re.findall(SQUAREBLANCKET, item)
                    # for match in matches:
                        # dates.extend(extract_date_to_yyyymmdd(match))
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


def extract_date_to_yyyymmdd(text:str, date_korean:bool=False) -> list[str]:
    """ 문자열에서 YYYY.MM.DD 또는 YYYY년 MM월 DD일 형식의 날짜를 추출하여 YYYYMMDD로 변환 """

    date_list = re.findall(DATE, text)
    if not date_list and date_korean:  # DATE(Regex) 결과가 없고, date_korean = DATEKOR(Regex) 사용
        date_list = re.findall(DATEKOR, text)
    
    return [f"{year}{int(month):02d}{int(day):02d}" for year, month, day in date_list]


def get_latest_date(dates:list[str], enact_date:str) -> str:
    """ 날짜 리스트에서 가장 최신 날짜를 반환 (없으면 enact_date 반환) """
    return max(dates) if dates else enact_date


def extract_article_num(text: str, lst=False) -> Union[str, list[str]]:
    """
    텍스트에서 조문 번호를 추출합니다.
    "(제 xx조의 xx~~)" 또는 "(제 xx조)" 패턴을 찾아 조문 ID 리스트를 반환합니다.
    조문 번호가 없으면 []을 반환합니다.
    """
    article_nums = []
    match = re.search(ARTICLENUM, text)
    if match:
        main_num = int(match.group(1))  # 본조 번호
        sub_num = (
            int(match.group(2)) if match.group(2) else 1
        )  # '의' 조문 번호 (없으면 1)
        article_num = f"{main_num:04d}{sub_num:03d}"
        if lst:
            article_nums.append(article_num)
        else :
            return article_num
    return article_nums


def export_json(data, id):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    output_file = f"result/matadata_{id}_{timestamp}.json"

    logger.info(f"📂 [export_json] JSON 데이터 저장: KEY={id}, 파일 경로={output_file}")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_json_as_model(json_file: str, model: type[BaseModel]) -> BaseModel:
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return model.model_validate(data)  # Pydantic v2 (v1은 model.parse_obj(data))


# 색상 코드 설정
COLORS = {
    "INFO": "\033[92m",    # 초록색 ✅
    "WARNING": "\033[93m", # 노란색 ⚠️
    "ERROR": "\033[91m",   # 빨간색 ❌
    "RESET": "\033[0m"     # 초기화 (흰색)
}

# FastAPI의 기본 로거 가져오기
logger = logging.getLogger("uvicorn")

# 기존 핸들러 제거 (중복 로그 방지)
logger.handlers.clear()

# 컬러 포맷터 설정
class ColoredFormatter(logging.Formatter):
    def format(self, record):
        levelname_color = COLORS.get(record.levelname, COLORS["RESET"])
        colored_levelname = f"{levelname_color}{record.levelname}{COLORS['RESET']}"  # 레벨명만 색상 적용
        log_time = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return f"{colored_levelname} {log_time} : {record.getMessage()}"

# 새로운 핸들러 추가
console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter("%(levelname)s %(asctime)s : %(message)s"))
logger.addHandler(console_handler)

# 로그 레벨 설정
logger.setLevel(logging.INFO)



