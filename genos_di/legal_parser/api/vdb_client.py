import aiohttp
from schemas.vdb_schema import VectorAPIResponse, VectorRegisterRequest
from schemas.params import VectorAPIEndpoints
from dotenv import load_dotenv
import os
from fastapi import UploadFile
from commons.loggers import MainLogger, ErrorLogger
import aiofiles

load_dotenv()

main_logger = MainLogger()
error_logger = ErrorLogger()


async def get_login() -> VectorAPIResponse:

    login_route = VectorAPIEndpoints().get_login_route()
    login_request = {
        "user_id" : os.getenv('KOMIPO_ADMIN_ID'), 
        "password" : os.getenv('KOMIPO_ADMIN_PASSWORD')
    }
    login_header = {
        "Accept": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(login_route, json=login_request, headers=login_header) as response:
            response.raise_for_status()
            response_json = await response.json()
            return VectorAPIResponse(**response_json)  


def get_headers(token: str) -> dict[str, str]:
    """
    토큰을 받아서 API 호출에 필요한 headers를 만들어주는 함수.
    """
    header = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}"
    }
    return header

async def upload_file(token:str, request: list[UploadFile]) -> VectorAPIResponse:
    url = VectorAPIEndpoints().get_upload_route()
    headers = get_headers(token)

    async with aiohttp.ClientSession() as session:
        form = aiohttp.FormData()

        for file in request:
            async with aiofiles.open(file.file, 'rb') as f:
                form.add_field(
                    name="file",
                    value=f,
                    content_type=file.content_type,
                    filename=file.filename
                )

        async with session.post(url, data=form, headers=headers) as response:
            response.raise_for_status()
            response_json = await response.json()
            return VectorAPIResponse(**response_json)

async def register_vector(vdb_id: str, token:str, request: VectorRegisterRequest) -> VectorAPIResponse:
    url = f"{VectorAPIEndpoints().get_register_route(vdb_id)}"
    headers = get_headers(token)
    payload = request.model_dump_json()

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=payload, headers=headers) as response:
            response.raise_for_status()
            response_json = await response.json()
            return VectorAPIResponse(**response_json)
