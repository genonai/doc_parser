from typing import Union

import aiohttp
from fastapi import status

from params import (
    AdmBylRequestParams,
    AdmRuleRequestParams,
    APIEndpoints,
    LawItemRequestParams,
    LawSystemRequestParams,
    LicBylRequestParams,
)
from utils.loggers import MainLogger

main_logger = MainLogger()

class ClientError(Exception):
    def __init__(self, id:str, detail: str, status_code: int = status.HTTP_404_NOT_FOUND):
        self.id = id 
        self.detail = detail
        self.status_code = status_code

## API GET Request
async def fetch_api(id:str, url: str):
    async with aiohttp.ClientSession() as client:
        async with client.get(url) as response:
            content_type = response.headers.get("Content-Type", "")
            
            if response.status == 200:
                if "application/json" in content_type:
                    data = await response.json()
                    if "Law" in data.keys():
                        raise ClientError(id=id, detail="해당되는 법령/행정데이터를 찾을 수 없음 : {url}")
                else:
                    data = await response.text()
                    main_logger.warning(f"[fetch_api] 예상치 못한 데이터 타입: {content_type} ({url})")
                    raise ClientError(id=id, detail=f"Unexpected content type: {url}: {content_type}", status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
            else:
                main_logger.error(f"[fetch_api] API 요청 실패: {url} (HTTP {response.status})")
                raise ClientError(id=id, detail=f"Request {url} failed with status {response.status}", status_code=status.HTTP_400_BAD_REQUEST)
            
            return data
        
# API 호출 
async def get_api_response(
    id:str,
    query: Union[
        LawItemRequestParams,
        LawSystemRequestParams,
        LicBylRequestParams,
        AdmBylRequestParams,
        AdmRuleRequestParams,
    ],
):
    api_url = APIEndpoints().get_full_url(query.get_query_params())
    main_logger.info(f"[get_api_response] API 요청 시작: {api_url}")
    response = await fetch_api(id, api_url)
    main_logger.info(f"[get_api_response] API 요청 성공: {api_url}")
    return response