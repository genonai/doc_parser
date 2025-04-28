from commons.type_converter import TypeConverter
from typing import Union
import unicodedata

type_converter = TypeConverter()

def replace_strip(data: list[str]) -> list[str]:
    return [x.strip() for x in data if x.strip()]

def format_date(year:str, month:str, day:str) -> str:
    if type_converter.validator((year, month, day), tuple[str, str, str]):
        return f"{year}{int(month):02d}{int(day):02d}"        

def normalize_to_nfc(text: str) -> Union[str, None]:
	"""
	주어진 텍스트를 NFC(Normalization Form C)로 정규화합니다.

	맥 환경에서 자주 발생하는 한글 자모 분리 문제를 해결하는 데 사용될 수 있습니다.
	예를 들어, "가나다"와 같이 표기된 파일명이 맥에서 "ㄱㅏㄴㅏㄷㅏ"와 같이 분리되어 보이는 문제를 해결할 수 있습니다.

	:param text: 정규화할 텍스트
	:return: NFC로 정규화된 텍스트

	From https://github.com/mindsandcompany/GenOS/blob/develop/admin-api/src/common/utils.py#L71
	"""
	if not text: 
		return None

	return unicodedata.normalize('NFC', text)