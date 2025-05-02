from typing import Any, Union
import aiohttp

from commons.loggers import ErrorLogger, MainLogger
from commons.settings import settings
from schemas.params import VectorAPIEndpoints
from schemas.vdb_schema import (
    VDBRegisterRequest,
    VDBRegisterResponse,
    VDBUploadResponse,
)

main_logger = MainLogger.instance()
error_logger = ErrorLogger.instance()

def get_headers() -> dict[str, str]:
    """
    토큰을 받아서 API 호출에 필요한 headers를 만들어주는 함수.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {settings.genos_admin_token}"
    }
    return headers

async def request_post(url: str, data: Any = None, is_json: bool = True):
   
    headers = get_headers()

    try:
        async with aiohttp.ClientSession() as session:
            request_kwargs = {}
            if data:
                if is_json:
                    request_kwargs["json"] = data.model_dump()
                else:
                    request_kwargs["data"] = data

            main_logger.debug(f"[request_post] POST {url} data={data}")

            async with session.post(url, headers=headers, **request_kwargs) as response:
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
    return None  

async def upload_file(request: list[tuple[str, bytes]]) -> Union[VDBUploadResponse, None]:
    url = VectorAPIEndpoints().get_upload_route()

    try:
        form = aiohttp.FormData()
        for filename, file_content in request:
            form.add_field(
                name="files",
                value=file_content,
                filename=filename,
                content_type="application/json"
            )
 
        response_json = await request_post(
            url=url,
            data=form,
            is_json=False
        )
        main_logger.critical(f"upload !!! {response_json}")

        if response_json:
            response = VDBUploadResponse(**response_json)
            main_logger.debug(f"[upload_file] VDB 파일 업로드 성공: {response.data.files}")
            return response
    
    except Exception as e:
        error_logger.vdb_error(f"[upload_file] 파일 업로드 실패 {request[0]}", e)
    return None

async def register_vector(request: VDBRegisterRequest) -> Union[VDBRegisterResponse, None]:
    url = VectorAPIEndpoints().get_register_route()
    print(request.model_dump())
    try:
        response_json = await request_post(
            url=url,
            data=request,
            is_json=True
        )
        main_logger.critical(f"register !!! {response_json}")
        if response_json:
            response = VDBRegisterResponse(**response_json)
            main_logger.debug(f"[upload_file] VDB 파일 업로드 성공: {response.data.doc_ids[0]}, {response.data.upsert_ids[0]}")
            return response

    except Exception as e:
        error_logger.vdb_error(f"[register_vector] 벡터 등록 실패, {request.files}", e)

    return None