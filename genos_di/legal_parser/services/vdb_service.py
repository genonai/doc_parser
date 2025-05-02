"""
upload api response.data > class FileData(BaseModel):
    filename: str
    fullpath: str
    temporary_name: str

register api response.data > class RegisterData(BaseModel):
    doc_ids: List[int]
    upsert_ids: List[int]

register api request > class VectorRegisterRequest(BaseModel):    
    vdb_id: int
    description: Optional[str] = None
    serving_id: int
    serving_rev_id: int
    preprocessor_id: int
    batch_size: int
    params: str         # ex) "{\"chunk_size\":1000,\"chunk_overlap\":100}",
    files: List[DocumentFile]

    class DocumentFile(BaseModel):
        name: str = Field(..., description="파일 경로. 사용자 선택한 위치의 상대 경로로 보내주세요")
        path: str

vdb_client으로 부터 import
1-1. upload > request: UploadFile 임. 이 프로젝트 폴더 /resources/result에 있는 .json 파일을 list[UploadFile]로 변환
    법령/행정규칙 id, num 저장.
1-2. upload > response로 부터 각 파일의 fullpath를 받음
2-1. register > request 구성(VectorRegisterRequest) 1-2의 upload로부터 받은 fullpath를 request(files[].path에 추가)

2-2. register > reseponse를 받아 vdb_id(env), doc_ids, upsert_id와 법령_id 법령 numm, 법령 type(law/admrule) mapping
    


"""
import json
from typing import List, Union

from fastapi import UploadFile

from api.vdb_client import register_vector, upload_file
from commons.constants import DIR_PATH_LAW_RESULT
from commons.file_handler import extract_law_infos, extract_local_files
from commons.loggers import ErrorLogger, MainLogger
from commons.settings import settings
from schemas.vdb_schema import (
    LawVectorResult,
    LawInfo,
    VDBRegisterRequest,
    VDBRegisterResponse,
    VDBUploadResponse,
    VDBResponse
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
    upload_files: List[UploadFile],
    batch_size: int,
    chunk_size: int,
    chunk_overlap: int,
    description: str
) -> Union[VDBRegisterResponse, None]:
    """
    단일 문서 업로드 및 벡터 등록 프로세스를 수행합니다.
    Args:
        upload_files (List[UploadFile]): 업로드할 파일 리스트 (1개 파일만 처리)
        batch_size (int): 등록 요청의 배치 크기
        chunk_size (int): 문서 청크 크기
        chunk_overlap (int): 청크 겹침 크기
        description (str): 등록 요청 설명

    Returns:
        Optional[VDBRegisterResponse]: 벡터 등록 결과 (성공 시)
    """
    # 업로드 처리
    upload_response: VDBUploadResponse = await upload_file(upload_files)

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
    
    law_info_list: List[LawInfo] = await extract_law_infos(DIR_PATH_LAW_RESULT)
    data_files: List[tuple[str, bytes]] = await extract_local_files(DIR_PATH_LAW_RESULT)
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
        filename, _ = file
        try:
            response = await vectorize_document(
                upload_files=[file],
                batch_size = batch_size,
                chunk_size = chunk_size,
                chunk_overlap = chunk_overlap,
                description = description
            )
            if response is None:
                main_logger.error(f"VDB 업로드 및 등록 실패: {filename}")
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
            main_logger.error(f"[process_law_vectorization] 벡터화 실패: {filename}")
            error_logger.vdb_error(f"[process_law_vectorization] 벡터화 중 오류 발생: {filename}", e)
            vdb_response.increment_fail(filename)

    main_logger.info(f"[run_vdb_service] 총 등록된 문서 수: {len(mappings)}")

    return vdb_response, mappings