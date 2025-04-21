import sys
import traceback

from fastapi import status


class ClientError(Exception):
    def __init__(self, id:str, detail: str, status_code: int = status.HTTP_404_NOT_FOUND):
        self.id = id 
        self.detail = detail
        self.status_code = status_code
    
def get_error_summary(e: Exception) -> str:
    exc_type, _, exc_tb = sys.exc_info()
    tb = traceback.extract_tb(exc_tb)

    if tb:
        last = tb[-1]  # 마지막 프레임 (가장 최근 호출)
        filename = last.filename  
        lineno = last.lineno
        funcname = last.name
        err_type = exc_type.__name__
        err_msg = str(e)
        return f"{err_type} at {filename}:{lineno} in {funcname}() - {err_msg}"
    else:
        # traceback 정보가 없을 경우
        return f"{type(e).__name__} - {str(e)}"
