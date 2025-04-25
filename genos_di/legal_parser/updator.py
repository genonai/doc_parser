from utils.fetcher import get_api_response_updated
from params import UpdatedLawRequestParams
from schemas import ParserRequest
from utils.loggers import MainLogger
from typing import Optional

main_logger = MainLogger()

async def get_updated_law() -> Optional[ParserRequest]:
    """오늘 개정된 법령의 ID를 가져오는 함수"""
    query = UpdatedLawRequestParams()
    api_response: dict = await get_api_response_updated(query)

    laws = api_response.get("LawSearch", {}).get("law", [])
    

    if laws:
        law_ids = [law.get("법령일련번호") for law in laws]
        main_logger.info(f"[get_updated_law]: 개정된 법령 ID : {law_ids}")
        updated_law_ids = ParserRequest()
        updated_law_ids.law_ids_input.law_ids = law_ids
        return updated_law_ids
    
    return None

   


    
     




