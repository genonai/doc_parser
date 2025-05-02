import logging
import os
import sys
import traceback
from datetime import datetime
from typing import Optional

import pytz

from commons.constants import DIR_PATH_ERROR_LOG


class NewlineTracebackFormatter(logging.Formatter):
    def formatException(self, exc_info):
        result = super().formatException(exc_info)
        return result + "\n"
    
    def format(self, record):
        # 기본 format 메서드 호출
        result = super().format(record)
        # 예외 정보가 있는 경우에만 처리
        if record.exc_info:
            pass
        return result

class ErrorLogger:
    _instance : Optional["ErrorLogger"] = None

    def __init__(self):

        if ErrorLogger._instance is not None:
            raise RuntimeWarning("Use ErrorLogger.instance() instead of direct instantiation.")

        # 로그 디렉토리가 없으면 생성
        os.makedirs(DIR_PATH_ERROR_LOG, exist_ok=True)

        seoul_tz = pytz.timezone('Asia/Seoul')
        created_at = datetime.now(seoul_tz).strftime('%Y%m%d')
        log_file = f"{DIR_PATH_ERROR_LOG}/error_log_{created_at}.txt"

        # 로거 설정
        self.logger = logging.getLogger('error_logger')
        self.logger.setLevel(logging.ERROR)
        
        # 파일 핸들러 설정
        self.file_handler = logging.FileHandler(log_file)
        self.file_handler.setLevel(logging.ERROR)
        
        # 포맷터 설정
        formatter = NewlineTracebackFormatter('%(message)s')
        self.file_handler.setFormatter(formatter)

        # 콘솔 핸들러 설정
        self.console_handler = logging.StreamHandler()
        self.console_handler.setLevel(logging.ERROR)
        self.console_handler.setFormatter(formatter)
        
        # 중복 핸들러 방지
        if not self.logger.handlers:
            self.logger.addHandler(self.file_handler)
            self.logger.addHandler(self.console_handler)
        
        # 상위 로거로 전파 방지
        self.logger.propagate = False

    @classmethod
    def instance(cls) -> "ErrorLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def vdb_error(self, text:str, e:Exception):
        error_summary = self.get_error_summary(e)
        self.logger.error(f"Exception occurred : {text} {error_summary}", exc_info=True)

    def law_error(self, id:str, e: Exception):
        """예외 정보를 로그 파일에 기록"""
        error_summary = self.get_error_summary(e)
        self.logger.error(f"Exception occurred on {id}: {error_summary}", exc_info=True)
    
    def get_error_summary(self, e: Exception) -> str:
        """예외 정보를 요약하여 반환"""
        exc_type, _, exc_tb = sys.exc_info()
        tb = traceback.extract_tb(exc_tb)

        seoul_tz = pytz.timezone('Asia/Seoul')
        timestamp = datetime.now(seoul_tz).strftime('%Y-%m-%d %H:%M')
        
        if tb:
            last = tb[-1]  # 마지막 프레임 (가장 최근 호출)
            filename = last.filename  
            lineno = last.lineno
            funcname = last.name
            err_type = exc_type.__name__
            err_msg = str(e)
            return f"[{timestamp}] {err_type} at {filename}:{lineno} in {funcname}() - {err_msg}"
        else:
            # traceback 정보가 없을 경우
            return f"[{timestamp}] {type(e).__name__} - {str(e)}"

class MainLogger:
    _instance: Optional["MainLogger"] = None

    def __init__(self, level=logging.INFO):

        if MainLogger._instance is not None:
            raise RuntimeWarning("Use MainLogger.instance() instead of direct instantiation.")
        
        # 로거 설정
        self.logger = logging.getLogger('main_logger')
        self.logger.setLevel(level)
        
        # 콘솔 핸들러 설정
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        
        # 포맷터 설정 - 파일명 포함
        formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(formatter)
        
        # 중복 핸들러 방지
        if not self.logger.handlers:
            self.logger.addHandler(console_handler)
        
        # 상위 로거로 전파 방지
        self.logger.propagate = False

    @classmethod
    def instance(cls) -> "MainLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def debug(self, message):
        self.logger.debug(message)
    
    def info(self, message):
        self.logger.info(message)
    
    def warning(self, message):
        self.logger.warning(message)
    
    def error(self, message):
        self.logger.error(message)
    
    def critical(self, message):
        self.logger.critical(message)