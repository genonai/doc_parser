
import asyncio

from api.law_client import get_api_response
from commons.loggers import ErrorLogger, MainLogger
from parsers.addendum import parse_addendum_info
from parsers.admrule import parse_admrule_info
from parsers.admrule_article import parse_admrule_article_info
from parsers.appendix import parse_appendix_info
from parsers.mapper import processor_mapping
from schemas.params import (
    AdmRuleRequestParams,
)
from schemas.schema import (
    ConnectedLaws,
    HierarchyLaws,
    ParserContent,
    ParserResult,
    RuleInfo,
)

error_logger = ErrorLogger.instance()
main_logger = MainLogger.instance()

async def get_parsed_admrule(id, hierarchy_laws:list[HierarchyLaws], connected_laws:list[ConnectedLaws]) -> ParserResult:
    """행정규칙의 모든 정보 처리"""
    hierarchy_laws = hierarchy_laws if isinstance(hierarchy_laws, list) else []
    connected_laws = connected_laws if isinstance(connected_laws, list) else []
        
    admrule_response: dict = await get_api_response(AdmRuleRequestParams(ID=id))
    admrule_data = admrule_response.get("AdmRulService")

    # 행정규칙
    main_logger.info(f"[parse_admrule_info] 행정규칙 메타데이터 처리: id={id}")
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

    main_logger.info("[get_parsed_admrule] 행정규칙 부칙, 별표, 조문 데이터 병렬 처리")
    addendum_data = admrule_data.get("부칙")
    appendix_data = admrule_data.get("별표", {})
    article_data = admrule_data.get("조문내용", [])

    addendum_task = asyncio.to_thread(parse_addendum_info, id, addendum_data, is_admrule)
    appendix_task = asyncio.to_thread(parse_appendix_info, admrule_info, appendix_data, is_admrule)
    article_task = asyncio.to_thread(parse_admrule_article_info, admrule_info, article_data)

    addendum_list, appendix_list, article_list = await asyncio.gather(
        addendum_task, appendix_task, article_task
    )
    
    main_logger.info("[processor_mapping] 행정규칙 조문 - 부칙 - 별표 연결 처리")
    article_result, addendum_result, appendix_result = processor_mapping(article_list, addendum_list, appendix_list)
   
    parse_result = ParserResult(
        law=admrule_result,
        article=article_result,
        addendum=addendum_result,
        appendix=appendix_result,
    )
    
    main_logger.info("[get_parsed_admrule] 행정규칙 데이터 파싱 완료")
    return parse_result
