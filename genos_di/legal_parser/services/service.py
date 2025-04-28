import time

from api.law_client import get_api_amend
from commons.file_handler import export_json
from commons.loggers import ErrorLogger, MainLogger
from schemas.params import UpdatedLawRequestParams
from schemas.schema import ConnectedLaws, HierarchyLaws, ParserRequest, ParserResponse
from services.admrule_service import get_parsed_admrule
from services.law_service import get_parsed_law, get_parsed_law_system

error_logger = ErrorLogger()
main_logger = MainLogger()

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
        error_logger.log_error(id, e)

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

        # result.append(parse_result)

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
    
        # result.append(parse_result)

    law_ids = request.law_ids_input.law_ids
    admrule_ids = request.law_ids_input.admrule_ids

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
                error_logger.log_error(law_id, e)

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
        updated_law_ids.law_ids_input.law_ids = law_ids

        return await get_parse_result(updated_law_ids)
    else:
        main_logger.error("[get_updated_result] 개정된 법령이 존재하지 않습니다.")
        return ParserResponse()

