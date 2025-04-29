import asyncio
import os

import aiohttp
from dotenv import load_dotenv

from commons.loggers import ErrorLogger, MainLogger
from schemas.vdb_schema import VectorAPIResponse

load_dotenv()

main_logger = MainLogger()
error_logger = ErrorLogger()

class VDBTokenManager:
    def __init__(self, login_url:str):
        self.login_url = login_url
        self.token = None
        self.lock = asyncio.Lock()
        self.user_id = os.getenv('GENOS_ADMIN_ID'), 
        self.password =  os.getenv('GENOS_ADMIN_PASSWORD')

    async def login(self) -> VectorAPIResponse:
        """로그인하여 토큰 발급"""
        login_header = {
            "Accept": "application/json"
        }
        login_request = {
            "user_id" : self.user_id,
            "password" : self.password
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.login_url, json=login_request, headers=login_header) as resp:
                    resp.raise_for_status()
                    resp_json = await resp.json()
                    response = VectorAPIResponse(**resp_json)
        except aiohttp.ClientResponseError as e:
            error_logger.law_error(f"[VDBTokenManager] Token 발급 실패 - HTTP Error: {e.status} {e.message}")
        except aiohttp.ClientError as e:
            error_logger.law_error(f"[VDBTokenManager] Token 발급 실패 - Client Error: {str(e)}")
        except Exception:
            error_logger.law_error("[VDBTokenManager] Token 발급 실패 - 알 수 없는 에러")
        else:
            self.token = response.data.access_token
            main_logger.info("[VDBTokenManager] Genos Cluster Token 발급 성공 {self.token}")

    async def get_token(self) -> str:
        async with self.lock:
            if self.token is None:
                await self.login()
            return self.token
        
    async def refresh_token(self):
        # TODO expired period 알아둬야 함.
        async with self.lock:
            await self.login()    
        

