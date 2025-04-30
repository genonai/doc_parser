import copy
import inspect
from typing import Awaitable, Callable, TypeVar, Union

import aiohttp
from fastapi import status

from commons.loggers import ErrorLogger, MainLogger
from schemas.params import (
    AdmBylRequestParams,
    AdmRuleRequestParams,
    APIEndpoints,
    LawItemRequestParams,
    LawSystemRequestParams,
    LicBylRequestParams,
    UpdatedLawRequestParams,
)

main_logger = MainLogger()
error_logger = ErrorLogger()

T = TypeVar('T')

class ClientError(Exception):
    def __init__(self, detail: str, status_code: int = status.HTTP_404_NOT_FOUND):
        self.detail = detail
        self.status_code = status_code

## API GET Request
async def fetch_api(url: str):
    async with aiohttp.ClientSession() as client:
        async with client.get(url) as response:
            content_type = response.headers.get("Content-Type", "")
            
            if response.status == 200:
                if "application/json" in content_type:
                    data = await response.json()
                    if "Law" in data.keys():
                        raise ClientError(detail="해당되는 법령/행정데이터를 찾을 수 없음 : {url}")
                else:
                    data = await response.text()
                    error_logger.law_error(f"[fetch_api] 예상치 못한 데이터 타입: {content_type} ({url})")
                    raise ClientError(detail=f"Unexpected content type: {url}: {content_type}", status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
            else:
                error_logger.law_error(f"[fetch_api] API 요청 실패: {url} (HTTP {response.status})")
                raise ClientError(detail=f"Request {url} failed with status {response.status}", status_code=status.HTTP_400_BAD_REQUEST)
            
            return data
        
async def fetch_api_amend(url: str):
    async with aiohttp.ClientSession() as client:
        async with client.get(url) as response:
            content_type = response.headers.get("Content-Type", "")
            if response.status == 200:
                if "application/json" in content_type:
                    data = await response.json()
                    if data and data["LawSearch"]["totalCount"]  == 0: 
                        raise ClientError(detail="해당일자에 개정된 법령을 찾을 수 없음 : {url}")
                else:
                    data = await response.text()
                    error_logger.law_error(f"[fetch_api] 예상치 못한 응답 데이터 타입: {content_type} ({url})")
                    raise ClientError(detail=f"Unexpected content type: {url}: {content_type}", status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
            else:
                error_logger.law_error(f"[fetch_api] API 요청 실패: {url} (HTTP {response.status})")
                raise ClientError(detail=f"Request {url} failed with status {response.status}", status_code=status.HTTP_400_BAD_REQUEST)
                    
# API 호출 
async def get_api_response(
    query: Union[
        LawItemRequestParams,
        LawSystemRequestParams,
        LicBylRequestParams,
        AdmBylRequestParams,
        AdmRuleRequestParams
    ],
):
    api_url = APIEndpoints().get_item_url(query.get_query_params())
    main_logger.debug(f"[get_api_response] API 요청 시작: {api_url}")
    response = await fetch_api(api_url)
    main_logger.debug(f"[get_api_response] API 요청 성공: {api_url}")
    return response

# API 호출 
async def get_all_api_responses(query, api_func: Union[Callable, Awaitable[dict]], merge_func: Callable):
    """모든 페이지의 API 응답을 가져오는 함수"""

    url  = APIEndpoints().get_list_url(query.get_query_params())

    # 함수가 비동기인지 확인
    is_async = inspect.iscoroutinefunction(api_func)
    
    # 첫 번째 응답 가져오기
    if is_async:
        first_response = await api_func(url)
    else:
        first_response = api_func(url)
    total_count = int(first_response.get("LawSearch", {}).get("totalCnt", 0))
    
    # 결과를 저장할 리스트
    all_results = [first_response]
    
    # 필요한 총 페이지 수 계산
    total_pages = (total_count + query.display - 1) // query.display
    
    main_logger.debug(f"총 {total_count}개 항목, {total_pages}페이지 조회 필요")
    
    # 2페이지부터 마지막 페이지까지 요청
    for page in range(2, total_pages + 1):
        query.page = page
        main_logger.debug(f"{page}/{total_pages} 페이지 요청 중...")

        if is_async:
            response = await api_func(query)
        else:
            response = api_func(query)
        
        all_results.append(response)
    
    # 결과 병합
    merged_result = merge_func(all_results)
    
    return merged_result

def merge_amend_responses(responses) -> dict[dict[list[dict]]]:
    """여러 API 응답을 하나로 병합하는 함수"""
    if not responses:
        return {}
    
    # 첫 번째 응답을 기본 템플릿으로 사용
    merged = copy.deepcopy(responses[0])
    
    # LawSearch 섹션이 없으면 첫 번째 응답 그대로 반환
    if "LawSearch" not in merged:
        return merged
    
    # 모든 항목을 담을 리스트
    all_items = []
    
    # 각 응답에서 항목 추출 및 병합
    for response in responses:
        if "LawSearch" in response and "law" in response["LawSearch"]:
            # 단일 항목인 경우와 리스트인 경우 모두 처리
            items = response["LawSearch"]["law"]
            if isinstance(items, list):
                all_items.extend(items)
            else:
                all_items.append(items)
    
    # 병합된 결과에 모든 항목 설정
    merged["LawSearch"]["law"] = all_items
    
    return merged


async def get_api_amend(
    query:UpdatedLawRequestParams
):
    main_logger.debug(f"[get_api_amend] API 요청 시작: {query.regDt}")
    response = await get_all_api_responses(query, fetch_api_amend, merge_amend_responses)
    main_logger.debug(f"[get_api_amend] API 요청 성공: {query.regDt}")
    return response