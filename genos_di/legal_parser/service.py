import asyncio
import logging
import time
from typing import Union

import aiohttp

from file_utils import export_json
from mappers.mapper_data import processor_mapping
from params import (
    AdmBylRequestParams,
    AdmRuleRequestParams,
    APIEndpoints,
    LawItemRequestParams,
    LawSystemRequestParams,
    LicBylRequestParams,
)
from parsers.addendum import parse_addendum_info
from parsers.admrule import (
    parse_admrule_article_info,
    parse_admrule_info,
)
from parsers.appendix import parse_appendix_info
from parsers.law import (
    parse_law_article_info,
    parse_law_info,
)
from parsers.law_system import parse_law_relationships
from schemas import ConnectedLaws, HierarchyLaws, ParserContent, ParserResult, RuleInfo

logger = logging.getLogger(__name__)

## API GET Request
async def fetch_api(url: str):
    async with aiohttp.ClientSession() as client:
        async with client.get(url) as response:
            content_type = response.headers.get("Content-Type", "")
            
            if response.status == 200:
                if "application/json" in content_type:
                    data = await response.json()
                else:
                    data = await response.text()
                    logger.warning(f"[fetch_api] 예상치 못한 데이터 타입: {content_type} ({url})")
            else:
                logger.error(f"[fetch_api] API 요청 실패: {url} (HTTP {response.status})")
                return {"error": f"Request failed with status {response.status}"}
        
            return data
        
# API 호출 
async def get_api_response(
    query: Union[
        LawItemRequestParams,
        LawSystemRequestParams,
        LicBylRequestParams,
        AdmBylRequestParams,
        AdmRuleRequestParams,
    ],
):
    api_url = APIEndpoints().get_full_url(query.get_query_params())
    logger.info(f"[get_api_response] API 요청 시작: {api_url}")
    response = await fetch_api(api_url)
    logger.info(f"[get_api_response] API 요청 성공: {api_url}")
    return response

async def get_law_system(KEY: str) -> tuple:
    """법령 체계도 내의 법령 정보 및 ID 리스트 조회 (상하위법, 관련법령)"""
    logger.info("[get_law_system] 상하위법 및 관련법령 데이터 처리")
    system_response: dict = await get_api_response(
        LawSystemRequestParams(MST=KEY)
    )
    law_system = system_response.get("법령체계도")
    return parse_law_relationships(law_system)

async def get_parsed_law(id, hierarchy_laws:list[HierarchyLaws], connected_laws:list[ConnectedLaws]) -> ParserResult:
    """법률, 시행령, 시행규칙의 데이터 처리"""
    hierarchy_laws = hierarchy_laws if isinstance(hierarchy_laws, list) else []
    connected_laws = connected_laws if isinstance(connected_laws, list) else []

    law_response: dict = await get_api_response(LawItemRequestParams(MST=id))

    # 법령 데이터 처리
    logger.info(f"[parse_law_info] 법령 메타데이터 처리: id={id}")
    law_data: dict = law_response.get("법령")
    law_result: ParserContent = parse_law_info(id, law_data, hierarchy_laws, connected_laws)

    is_admrule = False 

    law_info = RuleInfo(
        id, law_result.metadata.enforce_date, law_result.metadata.enact_date, law_result.metadata.is_effective
    )

    logger.info("[get_parsed_law] 법령 부칙, 별표, 조문 데이터 병렬 처리")
    addendum_data = law_data.get("부칙")
    appendix_data = law_data.get("별표", {})
    article_data = law_data.get("조문")

    addendum_task = asyncio.to_thread(parse_addendum_info, id, addendum_data, is_admrule)
    appendix_task = asyncio.to_thread(parse_appendix_info, law_info, appendix_data, is_admrule)
    article_task = asyncio.to_thread(parse_law_article_info, law_info, article_data)

    addendum_list, appendix_list, article_list = await asyncio.gather(
        addendum_task, appendix_task, article_task
    )

    logger.info("[processor_mapping] 법령 조문 - 부칙 - 별표 연결 처리")
    article_result, addendum_result, appendix_result = processor_mapping(article_list, addendum_list, appendix_list)
    
    parse_result = ParserResult(
        law=law_result,
        addendum=addendum_result,
        appendix=appendix_result,
        article=article_result,
    )
    return parse_result

async def get_parsed_admrule(id, hierarchy_laws:list[HierarchyLaws], connected_laws:list[ConnectedLaws]) -> ParserResult:
    """행정규칙의 모든 정보 처리"""
    hierarchy_laws = hierarchy_laws if isinstance(hierarchy_laws, list) else []
    connected_laws = connected_laws if isinstance(connected_laws, list) else []
        
    admrule_response: dict = await get_api_response(AdmRuleRequestParams(ID=id))
    admrule_data = admrule_response.get("AdmRulService")

    # 행정규칙
    logger.info(f"[parse_admrule_info] 행정규칙 메타데이터 처리: id={id}")
    admrule_result: ParserContent = parse_admrule_info(
        id, admrule_data, hierarchy_laws, connected_laws
    )

    admrule_info = RuleInfo(
        id,
        admrule_result.metadata.enforce_date,
        admrule_result.metadata.enact_date,
        admrule_result.metadata.is_effective,
    )

    is_admrule = True 

    logger.info("[get_parsed_admrule] 행정규칙 부칙, 별표, 조문 데이터 병렬 처리")
    addendum_data = admrule_data.get("부칙")
    appendix_data = admrule_data.get("별표", {})
    article_data = admrule_data.get("조문내용", [])

    addendum_task = asyncio.to_thread(parse_addendum_info, id, addendum_data, is_admrule)
    appendix_task = asyncio.to_thread(parse_appendix_info, admrule_info, appendix_data, is_admrule)
    article_task = asyncio.to_thread(parse_admrule_article_info, admrule_info, article_data)

    addendum_list, appendix_list, article_list = await asyncio.gather(
        addendum_task, appendix_task, article_task
    )
    
    logger.info("[processor_mapping] 행정규칙 조문 - 부칙 - 별표 연결 처리")
    article_result, addendum_result, appendix_result = processor_mapping(article_list, addendum_list, appendix_list)
   
    parse_result = ParserResult(
        law=admrule_result,
        article=article_result,
        addendum=addendum_result,
        appendix=appendix_result,
    )
    
    logger.info("[get_parsed_admrule] 행정규칙 데이터 파싱 완료")
    return parse_result

async def get_parse_result(law_ids_dict: dict[str, list[str]]) -> int:
    # result = []
    count = 0
    seen_law_id = set()
    seen_admrule_id = set()

    async def process_law(id: str, hierarchy_laws, connected_laws):
        if id in seen_law_id:
            return
        seen_law_id.add(id)

        logger.info(f"[get_parse_result] 법령 데이터 파싱 시작: ID={id}")
        start = time.perf_counter()
        
        parse_result = await get_parsed_law(id, hierarchy_laws, connected_laws)
        export_json(parse_result.model_dump(), parse_result.law.metadata.law_num)
        
        end = time.perf_counter()
        duration = end - start
        logger.info(f"[get_parse_result] 법령 파싱 완료 - ID={id}, 소요 시간: {duration:.2f}초\n")
        
        nonlocal count
        count += 1
        # result.append(parse_result)

    async def process_admrule(id: str, hierarchy_laws, connected_laws):
        if id in seen_admrule_id:
            return
        seen_admrule_id.add(id)

        logger.info(f"[get_parse_result] 행정규칙 데이터 파싱 시작: ID={id}")
        start = time.perf_counter()
        
        parse_result = await get_parsed_admrule(id, hierarchy_laws, connected_laws)
        export_json(parse_result.model_dump(), parse_result.law.metadata.admrule_num)
        
        end = time.perf_counter()
        duration = end - start
        logger.info(f"[get_parse_result] 행정규칙 파싱 완료 - ID={id}, 소요 시간: {duration:.2f}초\n")
        
        nonlocal count
        count += 1
        # result.append(parse_result)

    law_ids = law_ids_dict.get("law_ids", [])
    admrule_ids = law_ids_dict.get("admrule_ids", [])

    for law_id in law_ids:
        if law_id not in seen_law_id:
            hierarchy_laws, connected_laws, related_law_ids = await get_law_system(law_id)
            await process_law(law_id, hierarchy_laws, connected_laws)

            for related_id in related_law_ids.get("law", []):
                await process_law(related_id, hierarchy_laws, connected_laws)

            for related_id in related_law_ids.get("admrule", []):
                await process_admrule(related_id, hierarchy_laws, connected_laws)

    for admrule_id in admrule_ids:
        if admrule_id not in seen_admrule_id:
            await process_admrule(admrule_id, [], [])

    logger.info(f"\n[get_parse_result] 모든 법령 데이터 파싱 완료: 총 개수={count}")
    return count


async def download_data(query: Union[LawItemRequestParams, AdmRuleRequestParams]):
    api_url = APIEndpoints().get_full_url(query.get_query_params())
    result =  await fetch_api(api_url)
    export_json(result, query.ID, is_result=False)