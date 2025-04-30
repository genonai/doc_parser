import os
from asyncio import Lock
from typing import Optional

import aiohttp
from dotenv import load_dotenv

from commons.loggers import ErrorLogger, MainLogger
from schemas.vdb_schema import VDBLoginResponse

load_dotenv()

main_logger = MainLogger()
error_logger = ErrorLogger()

class VDBTokenManager:
    _instance = Optional["VDBTokenManager"] = None
    _lock : Lock = Lock() 

    def __init__(self, login_url:str):
        self.login_url = login_url,
        self.token : Optional[str] = None,
        self.refresh_token : Optional[str] = None,
        self.user_id = os.getenv('GENOS_ADMIN_ID'), 
        self.password =  os.getenv('GENOS_ADMIN_PASSWORD')

    @classmethod
    async def get_instance(cls) -> "VDBTokenManager":
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    async def login(self) -> VDBLoginResponse:
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
                    response = VDBLoginResponse(**resp_json)
        except aiohttp.ClientResponseError as e:
            error_logger.vdb_error(f"[VDBTokenManager] Token 발급 실패 - HTTP Error: {e.status} {e.message}", e)
        except aiohttp.ClientError as e:
            error_logger.vdb_error("[VDBTokenManager] Token 발급 실패 - Client Error", e)
        except Exception as e:
            error_logger.vdb_error("[VDBTokenManager] Token 발급 실패 - 알 수 없는 에러", e)
        else:
            self.token = response.data.access_token
            self.refresh_token = response.data.refresh_token
            main_logger.info("[VDBTokenManager] Genos Cluster Token 발급 성공 {self.token}")

    async def get_token(self) -> str:
        if self.token is None:
            await self.login()
        return self.token
        
    async def refresh_tokens(self):
        # TODO expired 되기 전 자동 갱신
        await self.login()    
        

