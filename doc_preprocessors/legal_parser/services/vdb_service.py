import json
from typing import List, Union

from api.vdb_client import register_vector, upload_file
from commons.constants import DIR_PATH_LAW_RESULT
from commons.file_handler import extract_law_infos, extract_local_files
from commons.loggers import ErrorLogger, MainLogger
from commons.settings import settings
from schemas.vdb_schema import (
    LawFileInfo,
    LawVectorResult,
    VDBRegisterRequest,
    VDBRegisterResponse,
    VDBResponse,
    VDBUploadFile,
    VDBUploadResponse,
)

main_logger = MainLogger.instance()
error_logger = ErrorLogger.instance()


def set_register_request(upload_response:VDBUploadResponse, description:str, batch_size:int, chunk_size:int, chunk_overlap:int):
    files = upload_response.data.files
    register_params = json.dumps({"chunk_size": chunk_size, "chunk_overlap": chunk_overlap})

    register_request = VDBRegisterRequest(
        vdb_id=settings.genos_test_vdb_id,
        description=description,
        serving_id=settings.genos_test_serving_id,
        serving_rev_id=settings.genos_test_serving_rev_id,
        preprocessor_id=settings.genos_test_preprocessor_id,
        batch_size=batch_size,
        params=register_params,
        files=files
    )

    return register_request

async def vectorize_document(
    files: list[VDBUploadFile],
    batch_size: int,
    chunk_size: int,
    chunk_overlap: int,
    description: str
) -> Union[VDBRegisterResponse, None]:
    """
    단일 문서 업로드 및 벡터 등록 프로세스를 수행합니다.
    Args:
        upload_files (bytes): 업로드할 파일 리스트 (1개 파일만 처리)
        batch_size (int): 등록 요청의 배치 크기
        chunk_size (int): 문서 청크 크기
        chunk_overlap (int): 청크 겹침 크기
        description (str): 등록 요청 설명

    Returns:
        Optional[VDBRegisterResponse]: 벡터 등록 결과 (성공 시)
    """
    # 업로드 처리
    upload_response: VDBUploadResponse = await upload_file(files)

    if not upload_response or not upload_response.data:
        main_logger.error("[process_single_document] VDB Upload 실패")
        return None
    
    # Register 요청 생성
    register_request = set_register_request(upload_response, description, batch_size, chunk_size, chunk_overlap)

    # 벡터 등록
    register_response: VDBRegisterResponse = await register_vector(request=register_request)

    if not register_response or not register_response.data:
        main_logger.error("[process_single_document] VDB Register 실패")
        return None

    return register_response

async def process_law_vectorization(
    batch_size: int = 64,
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
    description: str = "법령 벡터 등록",
) -> tuple[VDBResponse, list[LawVectorResult]]:
    
    vdb_response = VDBResponse()
    mappings: List[LawVectorResult] = []
    
    law_info_list: List[LawFileInfo] = await extract_law_infos(DIR_PATH_LAW_RESULT)
    data_files: List[VDBUploadFile] = await extract_local_files(DIR_PATH_LAW_RESULT)
    vdb_id = settings.genos_test_vdb_id

    try:
        if len(law_info_list) != len(data_files):
            raise ValueError("파일 수와 법령 정보 수가 일치하지 않습니다")
    except ValueError as e:
        error_logger.vdb_error(
            f"[run_vdb_service] 파일 수 불일치 - 파일 수: {len(data_files)}, 법령 정보 수: {len(law_info_list)}",
            e
        )
        raise

    for idx, file in enumerate(data_files[:2]):
        try:
            response = await vectorize_document(
                files=[file],
                batch_size = batch_size,
                chunk_size = chunk_size,
                chunk_overlap = chunk_overlap,
                description = description
            )
            if response is None:
                main_logger.error(f"VDB 업로드 및 등록 실패: {file.file_name}")
                raise RuntimeError("VDB 응답이 None입니다.")

        
            law_info = law_info_list[idx]
            doc_ids = response.data.doc_ids
            upsert_ids = response.data.upsert_ids

            if len(doc_ids) != len(upsert_ids):
                raise ValueError("doc_ids와 upsert_ids의 길이가 일치하지 않습니다")
                
            for i in range(len(doc_ids)):
                mapping = LawVectorResult(
                    law_id=law_info.law_id,
                    law_num=law_info.law_num,
                    law_type=law_info.law_type,
                    doc_id=doc_ids[i],
                    vdb_id=vdb_id,
                    upsert_id=upsert_ids[i]
                )
                mappings.append(mapping)
            
            vdb_response.increment_success()

        except Exception as e:
            main_logger.error(f"[process_law_vectorization] 벡터화 실패: {file.file_name}")
            error_logger.vdb_error(f"[process_law_vectorization] 벡터화 중 오류 발생: {file.file_name}", e)
            vdb_response.increment_fail(file.file_name)

    main_logger.info(f"[run_vdb_service] 총 등록된 문서 수: {len(mappings)}")

    return vdb_response, mappings