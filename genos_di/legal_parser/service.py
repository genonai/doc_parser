from typing import Union

import aiohttp
import logging

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
from mappers.mapper_data import (
    map_article_addenda,
    map_article_appendix,
    map_addendum_appendix
)
from schemas import ParserContent, ParserResult, RuleInfo
from extractor import export_json

logger = logging.getLogger(__name__)

## API GET Request
async def fetch_api(url: str):
    async with aiohttp.ClientSession() as client:
        async with client.get(url) as response:
            content_type = response.headers.get("Content-Type", "")
            
            if response.status == 200:
                if "application/json" in content_type:
                    data = await response.json()
                    logger.info(f"[fetch_api]API 요청 성공: {url}")
                else:
                    data = await response.text()
                    logger.warning(f"[fetch_api]예상치 못한 데이터 타입: {content_type} ({url})")
            else:
                logger.error(f"[fetch_api]API 요청 실패: {url} (HTTP {response.status})")
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
    return response

async def get_relate_laws(KEY: str) -> tuple:
    '''법령 체계도 내의 법령 정보 및 ID 리스트 조회 (상하위법, 관련법령)'''
    system_response: dict = await get_api_response(
        LawSystemRequestParams(MST=KEY)
    )

    law_system = system_response.get("법령체계도")
    return parse_law_relationships(law_system)


async def get_parsed_law(id, hierarchy_laws=[], connected_laws=[]) -> ParserResult:
    '''
        법률, 시행령, 시행규칙의 모든 정보 조회
        조문 - 별칙, 조문 - 부칙 연결 처리
    '''
    law_response: dict = await get_api_response(
        LawItemRequestParams(MST=id)
    )

    # 법령 데이터 처리
    logger.info(f"[parse_law_info] 법령 메타데이터 처리: id={id}")
    law_data: dict = law_response.get("법령")
    law_result: ParserContent = parse_law_info(id, law_data, hierarchy_laws, connected_laws)

    # 부칙 데이터 처리
    logger.info("[parse_addendum_law_info] 법령 부칙 메타데이터 처리")
    addendum_list: list[ParserContent] = parse_addendum_info(
        id, law_data.get("부칙"), False
    )

    law_info = RuleInfo(
        id, law_result.metadata.enforce_date, law_result.metadata.enact_date, law_result.metadata.is_effective
    )

    # 별표 데이터 처리
    logger.info("[parse_appendix_info] 법령 별표 메타데이터 처리")
    appendix_list: list[ParserContent] = parse_appendix_info(
        law_info, law_data.get("별표", {}), False
    )

    # 조문 데이터 처리
    logger.info("[parse_law_article_info] 법령 조문 메타데이터 처리")
    article_data: dict = law_data.get("조문")
    article_list: list[ParserContent] = parse_law_article_info(
        law_info, article_data
    )

    # 조문 - 부칙 연결
    logger.info("[get_parsed_law] 조문 - 부칙 연결 처리")
    mapped_articles, mapped_addendum = map_article_addenda(
        article_list, addendum_list
    )

    # 조문 - 별표 연결
    logger.info("[get_parsed_law] 조문 - 별표 연결 처리")
    article_result, mapped_appendices = map_article_appendix(mapped_articles, appendix_list)

    logger.info("[get_parsed_law] 부칙 - 별표 연결 처리")
    addendum_result, appendix_result = map_addendum_appendix(mapped_addendum, mapped_appendices)


    parse_result = ParserResult(
        law=law_result,
        addendum=addendum_result,
        appendix=appendix_result,
        article=article_result,
    )
    logger.info("[get_parsed_law] 법령 데이터 파싱 완료")
    return parse_result

async def get_parsed_admrule(id, hierarchy_laws=[], connected_laws=[]) -> ParserResult:
    '''
        행정규칙의 모든 정보 조회
        조문 - 별칙, 조문 - 부칙 연결 처리
    '''
    admrule_response: dict = await get_api_response(AdmRuleRequestParams(ID=id))
    admrule_data = admrule_response.get("AdmRulService")

    # 행정규칙
    logger.info(f"[parse_admrule_info] 행정규칙 메타데이터 처리: id={id}")
    admrule_result: ParserContent = parse_admrule_info(
        id, admrule_data, hierarchy_laws, connected_laws
    )

    # 부칙
    logger.info("[parse_addendum_admrule_info] 행정규칙 부칙 메타데이터 처리")
    addendum_list: list[ParserContent] = parse_addendum_info(
        id, admrule_data.get("부칙"), True
    )

    admrule_info = RuleInfo(
        id,
        admrule_result.metadata.enforce_date,
        admrule_result.metadata.enact_date,
        admrule_result.metadata.is_effective,
    )

    # 별표
    appendix_data = admrule_data.get("별표", {})
    logger.info("[parse_appendix_info] 행정규칙 별표 메타데이터 처리")
    appendix_list: list[ParserContent] = (
        parse_appendix_info(admrule_info, appendix_data, True)
        if appendix_data
        else []
    )

    # 행정규칙 조문
    article_data = admrule_data.get("조문내용", [])
    logger.info("[parse_admrule_info] 행정규칙 조문 메타데이터 처리")
    article_list: list[ParserContent] = parse_admrule_article_info(
        admrule_info, article_data
    )
    
    # 조문 - 부칙 연결
    logger.info("[get_parsed_law] 조문 - 부칙 연결 처리")
    mapped_articles, mapped_addendum = map_article_addenda(
        article_list, addendum_list
    )

    # 조문 - 별표 연결
    logger.info("[get_parsed_law] 조문 - 별표 연결 처리")
    article_result, mapped_appendices = map_article_appendix(mapped_articles, appendix_list)

    # 조문 - 부칙 연결
    logger.info("[get_parsed_law] 부칙 - 별표 연결 처리")
    addendum_result, appendix_result = map_addendum_appendix(mapped_addendum, mapped_appendices)

    parse_result = ParserResult(
        law=admrule_result,
        article=article_result,
        addendum=addendum_result,
        appendix=appendix_result,
    )
    
    logger.info("[get_parsed_admrule] 행정규칙 데이터 파싱 완료")
    return parse_result

async def get_parse_result(KEY: str):
    result = []
    logger.info(f"[get_parse_result] 데이터 파싱 시작: KEY={KEY}\n")

    ## 법령체계도의 상하위법, 관련법령 정보 조회
    hierarchy_laws, connected_laws, related_law_ids = await get_relate_laws(KEY)

    print(related_law_ids)
    # 법률, 시행령, 시행규칙 데이터 파싱
    related_laws =  related_law_ids.get("law")
    for id in related_laws:  ## 법률, 시행령, 시행규칙
        logger.info("Current Law: ", id)
        parse_result = await get_parsed_law(id, hierarchy_laws, connected_laws)
        export_json(parse_result.model_dump(), KEY, id)
        result.append(parse_result)

    # 행정규칙 데이터 파싱
    related_admrule = related_law_ids.get("admrule")
    for id in related_admrule:
        logger.info("Current Admin Rule: ", id)
        parse_result = await get_parsed_admrule(id, hierarchy_laws, connected_laws)
        export_json(parse_result.model_dump(), KEY, id)
        result.append(parse_result)

    logger.info(f"[get_parse_result] 모든 법령 데이터 파싱 완료: KEY={KEY}, 총 개수={len(related_laws) + (len(related_admrule))}\n")
    return result
