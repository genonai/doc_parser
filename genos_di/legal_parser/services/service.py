from commons.file_handler import export_mapping_json
from commons.loggers import ErrorLogger, MainLogger
from schemas.schema import (
    ParserRequest,
    ParserResponse,
    PipelineResponse,
)
from schemas.vdb_schema import VDBResponse
from services.law_service import get_amend_result, get_parse_result
from services.vdb_service import process_law_vectorization

error_logger = ErrorLogger.instance()
main_logger = MainLogger.instance()

async def process_all_pipeline(request: ParserRequest) -> PipelineResponse:
    vdb_response = VDBResponse()
    mappings = []

    try:
        main_logger.info("[process_all_pipeline] 전체 파싱 시작")
        parser_response: ParserResponse = await get_parse_result(request)

        if parser_response.success_count == 0:
            main_logger.warning("[process_all_pipeline] 파싱된 데이터가 없습니다. 벡터화 중단.")
            return parser_response

        main_logger.info("[process_all_pipeline] 파싱 완료. VDB 업로드 시작")

        vdb_response = await process_law_vectorization()

        mappings_dump = [m.model_dump() for m in vdb_response.mappings]
        export_mapping_json(mappings_dump)

        main_logger.info("[process_all_pipeline] VDB 업로드 완료. 파이프라인 종료")

    except Exception as e:
        error_logger.vdb_error("[run_vdb_vectorization_pipeline] 전체 파이프라인 실패", e)

    return PipelineResponse(parser=parser_response, vdb=vdb_response, mappings=mappings)

async def process_updated_pipeline() -> PipelineResponse:
    vdb_response = VDBResponse()

    try:
        
        main_logger.info("[process_updated_pipeline] 개정 법령 파싱 시작")
        parser_response: ParserResponse = await get_amend_result()

        if parser_response.success_count == 0:
            main_logger.warning("[process_updated_pipeline] 파싱된 데이터가 없습니다. 벡터화 중단.")
            return PipelineResponse(parser=parser_response)
            
        main_logger.info("[process_updated_pipeline] 파싱 완료. VDB 업로드 시작")
        vdb_response = await process_law_vectorization()

        mappings_dump = [m.model_dump() for m in vdb_response.mappings]
        export_mapping_json(mappings_dump)

        main_logger.info("[process_all_pipeline] VDB 업로드 완료. 파이프라인 종료")


    except Exception as e:
        error_logger.vdb_error("[process_updated_pipeline] 개정 법령 처리 파이프라인 실패", e)

    return PipelineResponse(parser=parser_response, vdb=vdb_response)