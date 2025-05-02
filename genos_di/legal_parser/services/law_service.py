import asyncio
import time

from api.law_client import get_api_amend, get_api_response
from commons.file_handler import export_json
from commons.loggers import ErrorLogger, MainLogger
from parsers.addendum import parse_addendum_info
from parsers.admrule import parse_admrule_info
from parsers.admrule_article import parse_admrule_article_info
from parsers.appendix import parse_appendix_info
from parsers.law import parse_law_info
from parsers.law_article import parse_law_article_info
from parsers.law_system import parse_law_relationships
from parsers.mapper import processor_mapping
from schemas.params import (
    AdmRuleRequestParams,
    LawItemRequestParams,
    LawSystemRequestParams,
    UpdatedLawRequestParams,
)
from schemas.schema import (
    ConnectedLaws,
    HierarchyLaws,
    ParserContent,
    ParserRequest,
    ParserResponse,
    ParserResult,
    RuleInfo,
)

error_logger = ErrorLogger.instance()
main_logger = MainLogger.instance()

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



async def process_with_error_handling(
        id: str,
        process_func: callable,
        hierarchy_laws: list[HierarchyLaws],
        connected_laws: list[ConnectedLaws],
        response : ParserResponse,
    ) -> bool:
    try:
        response.increment_total()
        await process_func(id, hierarchy_laws, connected_laws)
    except Exception as e:
        main_logger.error(f"[get_parse_result] 파싱 실패: ID={id} 자세한 내용은 로그 파일을 확인하세요.")
        error_logger.law_error(id, e)

        response.increment_fail(id)
        return False
    else:
        response.increment_success()
        return True

async def get_parse_result(request: ParserRequest) -> ParserResponse:
    # result = []
    law_consecutive_fail = 0
    admrule_consecutive_fail = 0

    seen_law_id = set()
    seen_admrule_id = set()

    async def process_law(id: str, hierarchy_laws, connected_laws):
        if id in seen_law_id:
            return
        seen_law_id.add(id)
        
        main_logger.info(f"[get_parse_result] 법령 데이터 파싱 시작: ID={id}")
        start = time.perf_counter()         
        
        parse_result = await get_parsed_law(id, hierarchy_laws, connected_laws)
        export_json(parse_result.model_dump(), id, parse_result.law.metadata.law_num, False)
    
        end = time.perf_counter()
        duration = end - start
        main_logger.info(f"[get_parse_result] 법령 파싱 완료 - ID={id}, 소요 시간: {duration:.2f}초\n")

    async def process_admrule(id: str, hierarchy_laws, connected_laws):
        if id in seen_admrule_id:
            return
        seen_admrule_id.add(id)
        
        main_logger.info(f"[get_parse_result] 행정규칙 데이터 파싱 시작: ID={id}")
        start = time.perf_counter()

        parse_result = await get_parsed_admrule(id, hierarchy_laws, connected_laws)
        export_json(parse_result.model_dump(), id, parse_result.law.metadata.admrule_num)
    
        end = time.perf_counter()
        duration = end - start
        main_logger.info(f"[get_parse_result] 행정규칙 파싱 완료 - ID={id}, 소요 시간: {duration:.2f}초\n")
    
    law_ids = request.law_ids
    admrule_ids = request.admrule_ids

    response = ParserResponse()

    for law_id in law_ids:
        if law_consecutive_fail >= 10:
            main_logger.critical("법령 - 연속된 에러 10회 초과로 Parser 실행 중단")
            break
        if law_id not in seen_law_id:
            try:
                hierarchy_laws, connected_laws, related_law_ids = await get_parsed_law_system(law_id)
            except Exception as e:
                main_logger.error(f"[get_law_system] 법령 관계도 파싱 실패: ID={law_id} 자세한 내용은 로그 파일을 확인하세요.")
                error_logger.law_error(law_id, e)

                response.increment_fail(law_id)
                law_consecutive_fail += 1
                continue
            else:
                law_consecutive_fail = 0

            for related_id in related_law_ids.get("law", []):
                if law_consecutive_fail >= 10:
                    main_logger.critical("법령 - 연속된 에러 10회 초과로 Parser 실행 중단")
                    break
                success = await process_with_error_handling(related_id, process_law, hierarchy_laws, connected_laws, response)
                if success:
                    law_consecutive_fail = 0
                else :
                    law_consecutive_fail += 1
                
            for related_id in related_law_ids.get("admrule", []):
                if admrule_consecutive_fail >= 10:
                    main_logger.critical("행정규칙 - 연속된 에러 10회 초과로 Parser 실행 중단")
                    break
                success = await process_with_error_handling(related_id, process_admrule, hierarchy_laws, connected_laws, response)
                if success:
                    admrule_consecutive_fail = 0
                else:
                    admrule_consecutive_fail += 1
                


    for admrule_id in admrule_ids:
        if admrule_consecutive_fail >= 10:
            main_logger.critical("행정규칙 - 연속된 에러 10회 초과로 Parser 실행 중단")
            break
        if admrule_id not in seen_admrule_id:
            success = await process_with_error_handling(admrule_id, process_admrule, [], [], response)
            if success:
                admrule_consecutive_fail = 0
            else:
                admrule_consecutive_fail += 1

    seen_count = len(seen_law_id) + len(seen_admrule_id)
    response.seen_ids = {
        "law": seen_law_id,
        "admrule": seen_admrule_id,
    }
    response.unseen_count = response.total_count - seen_count
    response.seen_count = seen_count
    response.unseen_ids = {
        "law": set(law_ids) - seen_law_id,
        "admrule": set(admrule_ids) - seen_admrule_id,
    }

    main_logger.info(f"\n[get_parse_result] 데이터 파싱 완료: {response.model_dump()}")
    return response

async def get_amend_result() -> ParserResponse:
    """어제 개정된 법령 데이터를 파싱하는 함수"""
    query = UpdatedLawRequestParams()
    api_response: dict = await get_api_amend(query)

    laws = api_response.get("LawSearch", {}).get("law", [])
    total = api_response.get("LawSearch", {}).get("totalCnt", 0)

    
    if total:
        law_ids = [law.get("법령일련번호") for law in laws]
        main_logger.info(f"[get_amend_result]: 개정된 법령 ID - {law_ids}. 개정된 법령의 파싱을 시작합니다.")
        
        updated_law_ids = ParserRequest()
        updated_law_ids.law_ids = law_ids

        return await get_parse_result(updated_law_ids)
    else:
        main_logger.info("[get_updated_result] 개정된 법령이 존재하지 않습니다.")
        return ParserResponse()

