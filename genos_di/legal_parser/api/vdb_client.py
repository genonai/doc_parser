from typing import Any, Union

import aiofiles
import aiohttp
from fastapi import UploadFile

from api.vdb_token import VDBTokenManager
from commons.loggers import ErrorLogger, MainLogger
from schemas.params import VectorAPIEndpoints
from schemas.vdb_schema import (
    VDBRegisterResponse,
    VDBUploadResponse,
    VDBRegisterRequest,
)

main_logger = MainLogger()
error_logger = ErrorLogger()

def get_headers(token:str) -> dict[str, str]:
    """
    토큰을 받아서 API 호출에 필요한 headers를 만들어주는 함수.
    """
    header = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    return header

async def request_post(url: str, payload: Any = None, headers:dict[str, str]=None, is_json: bool = True):
    try:
        async with aiohttp.ClientSession() as session:
            request_kwargs = {"headers": headers}

            if is_json and payload:
                request_kwargs["json"] = payload
            elif payload:
                request_kwargs["data"] = payload

            main_logger.debug(f"[request_post] POST {url} payload={payload}")

            async with session.post(url, **request_kwargs) as response:
                response.raise_for_status()
                response_json = await response.json()

                main_logger.debug(f"[request_post] POST 성공 {url} status={response.status}")
                return response_json

    except aiohttp.ClientResponseError as e:
        error_logger.vdb_error(f"[request_post] HTTP Error {e.status} {e.message} for {url}", e)
    except aiohttp.ClientError as e:
        error_logger.vdb_error(f"[request_post] Client Error for {url}", e)
    except Exception as e:
        error_logger.vdb_error(f"[request_post] 알 수 없는 에러 for {url}", e)
    main_logger.error("[request_post] VDB POST API ")
    return None  

async def upload_file(request: list[UploadFile]) -> Union[VDBUploadResponse, None]:
    url = VectorAPIEndpoints().get_upload_route()

    manager = await VDBTokenManager.get_instance()
    token = manager.get_token()
    headers = get_headers(token)

    try:
        form = aiohttp.FormData()
        for file in request:
            async with aiofiles.open(file.file, 'rb') as f:
                file_content = await f.read()
                form.add_field(
                    name="file",
                    value=file_content,
                    content_type=file.content_type,
                    filename=file.filename
                )
 
        response_json = await request_post(
            url=url,
            payload=form,
            headers=headers,
            is_json=False
        )

        if response_json:
            return VDBUploadResponse(**response_json)
    
    except Exception as e:
        error_logger.vdb_error("[upload_file] 파일 업로드 실패", e)
    return None

async def register_vector(request: VDBRegisterRequest) -> Union[VDBRegisterResponse, None]:
    url = VectorAPIEndpoints().get_register_route()
    payload = request.model_dump()

    manager = await VDBTokenManager.get_instance()
    token = manager.get_token()
    headers = get_headers(token)

    try:
        response_json = await request(
            url=url,
            payload=payload,
            headers=headers,
            is_json=True
        )

        if response_json:
            return VDBRegisterResponse(**response_json)

    except Exception as e:
        error_logger.vdb_error("[register_vector] 벡터 등록 실패", e)

    return None