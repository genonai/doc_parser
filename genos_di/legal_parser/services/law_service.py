import asyncio

from api.law_client import get_api_response
from commons.loggers import ErrorLogger, MainLogger
from parsers.addendum import parse_addendum_info
from parsers.appendix import parse_appendix_info
from parsers.law import parse_law_info
from parsers.law_article import parse_law_article_info
from parsers.law_system import parse_law_relationships
from parsers.mapper import processor_mapping
from schemas.params import (
    LawItemRequestParams,
    LawSystemRequestParams,
)
from schemas.schema import (
    ConnectedLaws,
    HierarchyLaws,
    ParserContent,
    ParserResult,
    RuleInfo,
)

error_logger = ErrorLogger()
main_logger = MainLogger()
async def get_parsed_law_system(id: str) -> tuple[list[HierarchyLaws], list[ConnectedLaws], dict[str, list[str]]]:
    """법령 체계도 내의 법령 정보 및 ID 리스트 조회 (상하위법, 관련법령)"""
    main_logger.info("[get_law_system] 상하위법 및 관련법령 데이터 처리")
    system_response: dict = await get_api_response(LawSystemRequestParams(MST=id))
    law_system = system_response.get("법령체계도")
    return parse_law_relationships(law_system)


async def get_parsed_law(id, hierarchy_laws:list[HierarchyLaws], connected_laws:list[ConnectedLaws]) -> ParserResult:
    """법률, 시행령, 시행규칙의 데이터 처리"""
    hierarchy_laws = hierarchy_laws if isinstance(hierarchy_laws, list) else []
    connected_laws = connected_laws if isinstance(connected_laws, list) else []

    law_response: dict = await get_api_response(LawItemRequestParams(MST=id))

    # 법령 데이터 처리
    main_logger.info(f"[parse_law_info] 법령 메타데이터 처리: id={id}")
    law_data: dict = law_response.get("법령")
    law_result: ParserContent = parse_law_info(id, law_data, hierarchy_laws, connected_laws)

    is_admrule = False 

    law_info = RuleInfo(
        id, law_result.metadata.enforce_date, law_result.metadata.enact_date, law_result.metadata.is_effective
    )

    main_logger.info("[get_parsed_law] 법령 부칙, 별표, 조문 데이터 병렬 처리")
    addendum_data = law_data.get("부칙")
    appendix_data = law_data.get("별표")
    article_data = law_data.get("조문")

    addendum_task = asyncio.to_thread(parse_addendum_info, id, addendum_data, is_admrule)
    appendix_task = asyncio.to_thread(parse_appendix_info, law_info, appendix_data, is_admrule)
    article_task = asyncio.to_thread(parse_law_article_info, law_info, article_data)

    addendum_list, appendix_list, article_list = await asyncio.gather(
        addendum_task, appendix_task, article_task
    )

    main_logger.info("[processor_mapping] 법령 조문 - 부칙 - 별표 연결 처리")
    article_result, addendum_result, appendix_result = processor_mapping(article_list, addendum_list, appendix_list)
    
    parse_result = ParserResult(
        law=law_result,
        addendum=addendum_result,
        appendix=appendix_result,
        article=article_result,
    )
    return parse_result