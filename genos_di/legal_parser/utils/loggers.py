import logging
import traceback
import sys
import os
from datetime import datetime

class ErrorLogger:
    def __init__(self):
        # 로그 디렉토리가 없으면 생성
        log_dir = 'resources/errors'
        os.makedirs(log_dir, exist_ok=True)

        created_at = datetime.now().strftime('%Y%m%d')
        log_file = f"{log_dir}/error_log_{created_at}.txt"

        # 로거 설정
        self.logger = logging.getLogger('error_logger')
        self.logger.setLevel(logging.ERROR)
        
        # 파일 핸들러 설정
        self.file_handler = logging.FileHandler(log_file)
        self.file_handler.setLevel(logging.ERROR)
        
        # 포맷터 설정
        formatter = logging.Formatter('%(levelname)s at %(filename)s:%(lineno)d in %(funcName)s() - %(message)s')
        self.file_handler.setFormatter(formatter)
        
        # 중복 핸들러 방지
        if not self.logger.handlers:
            self.logger.addHandler(self.file_handler)
        
        # 상위 로거로 전파 방지
        self.logger.propagate = False
    
    def log_error(self, e: Exception):
        """예외 정보를 로그 파일에 기록"""
        error_summary = self.get_error_summary(e)
        self.logger.error(f"Exception occurred: {error_summary}", exc_info=True)
    
    def get_error_summary(self, e: Exception) -> str:
        """예외 정보를 요약하여 반환"""
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

class MainLogger:
    def __init__(self, level=logging.INFO):
        # 로거 설정
        self.logger = logging.getLogger('main_logger')
        self.logger.setLevel(level)
        
        # 콘솔 핸들러 설정
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        
        # 포맷터 설정 - 파일명 포함
        formatter = logging.Formatter('[%(name)s]-%(levelname)s: %(message)s')
        console_handler.setFormatter(formatter)
        
        # 중복 핸들러 방지
        if not self.logger.handlers:
            self.logger.addHandler(console_handler)
        
        # 상위 로거로 전파 방지
        self.logger.propagate = False
    
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