"""
지능형 전처리기 FastAPI 서버

intelligent_processor.py의 DocumentProcessor를 HTTP endpoint로 노출하는 독립형 서버.
- /healthcheck: 상태 확인
- /run: 파일 경로 기반 문서 처리
- /upload/run: multipart 파일 업로드 후 처리

실행: uvicorn intelligent_main:app --host 0.0.0.0 --port 7085
또는: python intelligent_main.py
"""

import os
import sys
import traceback
import time
import tempfile
import json
from pathlib import Path

# sys.path 설정 (먼저 설정해야 함)
_src_dir = os.path.dirname(os.path.abspath(__file__))
_facade_dir = os.path.join(_src_dir, '..', 'facade')
_root_dir = os.path.join(_src_dir, '../../..')

# sys.path.insert(0, _root_dir)      # /app (docling 패키지 찾기 위해) - use installed docling from pip
sys.path.insert(0, _facade_dir)    # /app/facade
sys.path.insert(0, _src_dir)       # /app/src

from fastapi import FastAPI, Request, Body, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from logger import Logger
from utils import make_failure_response, make_success_response
from config import cors_config
from common.exception import GenosServiceException

logger = Logger.getLogger(__name__)

app: FastAPI = FastAPI(
    title="Intelligent Document Preprocessor API",
    description="AI 기반 지능형 문서 전처리기 서빙",
    version="1.0.0"
)
cors_config(app)


# ============================================================
# 예외 핸들러
# ============================================================

@app.exception_handler(GenosServiceException)
async def genos_exception_handler(request, exc: GenosServiceException):
    logger.error(f"[GenosServiceException]: {exc.error_msg}")
    return JSONResponse(
        {
            'code': exc.error_code,
            'errMsg': exc.error_msg,
            'data': None,
            'error_code': exc.error_code
        },
        status_code=200
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    logger.error(f'[RequestValidationError]: {exc.errors()}')
    return make_failure_response(str(exc))


@app.exception_handler(Exception)
async def exception_handler(request, exc: Exception):
    logger.error(f'[Exception]: {exc}')
    logger.error(traceback.format_exc())
    return make_failure_response(str(exc))


# ============================================================
# DocumentProcessor 초기화 (lazy loading)
# ============================================================

_processor = None

def get_processor():
    """parser_processor.DocumentProcessor를 지연 로드"""
    global _processor
    if _processor is None:
        # from intelligent_processor import DocumentProcessor
        from parser_processor import DocumentProcessor # --- IGNORE -
        _processor = DocumentProcessor()
        logger.info("DocumentProcessor (parser) initialized successfully")
    return _processor


# ============================================================
# API Endpoints
# ============================================================

@app.get('/healthcheck', tags=["Health"])
async def healthcheck():
    """서버 상태 확인"""
    return {'status': 'ok'}


@app.post('/run', tags=["Document Processing"])
async def run(
        request: Request,
        file_path: str = Body(..., embed=True, description="처리할 문서의 파일 경로"),
        params: dict = Body(default_factory=dict, description="처리 옵션 (save_images, appendix, log_level 등)")
):
    """
    파일 경로 기반 문서 처리

    ### 요청 예시
    ```json
    {
      "file_path": "/data/document.pdf",
      "params": {
        "save_images": true,
        "include_wmf": false,
        "appendix": "[]",
        "log_level": 4,
        "max_chunk_size": 0,
        "auto_convert_to_pdf": true,
        "use_pdf_sdk": true
      }
    }
    ```

    ### 응답 예시
    ```json
    {
      "code": 0,
      "errMsg": "success",
      "data": [
        {
          "text": "HEADER: 제1장 총칙\\n제1조(목적)...",
          "title": "개인정보 보호법",
          "created_date": 20240115,
          "appendix": "별지 제1호.pdf",
          "i_page": 1,
          "e_page": 1,
          "chunk_bboxes": "[...]",
          "media_files": "[...]"
        }
      ]
    }
    ```
    """
    pt = time.time()
    try:
        logger.info(f'[intelligent] Start: "{file_path}"')
        data = await get_processor()(request, file_path, **params)
        logger.info(f'[intelligent] Success: "{file_path}"')
        
        result = data
        # result = [item.model_dump() if hasattr(item, 'model_dump') else item for item in data]
        

    except GenosServiceException as e:
        logger.error(f'[intelligent] Error: "{file_path}"\n{traceback.format_exc()}\n')
        return JSONResponse(
            {
                'code': 1,
                'errMsg': e.error_msg,
                'data': None,
                'error_code': e.error_code,
                'error_msg': e.error_msg
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f'[intelligent] Error: "{file_path}"\n{traceback.format_exc()}\n')
        return make_failure_response(str(e))
    finally:
        logger.info(f'[intelligent] End: "{file_path}" ({time.time() - pt:.2f} seconds)')

    return make_success_response(data=result)



@app.post('/parser', tags=["Document Processing"])
async def parse(
        request: Request,
        file_path: str = Body(..., embed=True, description="처리할 문서의 파일 경로"),
        params: dict = Body(default_factory=dict, description="처리 옵션 (save_images, appendix, log_level 등)")
):
    """파일 경로 기반 문서 파싱 (/run과 동일하며 파서 호환 엔드포인트로 제공)"""
    pt = time.time()
    try:
        logger.info(f'[parser] Start: "{file_path}"')
        data = await get_processor()(request, file_path, **params)
        logger.info(f'[parser] Success: "{file_path}"')

        # result = [item.model_dump() if hasattr(item, 'model_dump') else item for item in data]
        result = data
    except GenosServiceException as e:
        logger.error(f'[parser] Error: "{file_path}"\n{traceback.format_exc()}\n')
        return JSONResponse(
            {'code': 1, 'errMsg': e.error_msg, 'data': None,
             'error_code': e.error_code, 'error_msg': e.error_msg},
            status_code=200
        )
    except Exception as e:
        logger.error(f'[parser] Error: "{file_path}"\n{traceback.format_exc()}\n')
        return make_failure_response(str(e))
    finally:
        logger.info(f'[parser] End: "{file_path}" ({time.time() - pt:.2f} seconds)')

    return make_success_response(data=result)


@app.post('/upload/run', tags=["Document Processing"])
async def upload_and_run(
        request: Request,
        file: UploadFile = File(..., description="업로드할 문서 파일"),
        save_images: bool = Form(default=True),
        include_wmf: bool = Form(default=False),
        appendix: str = Form(default="[]", description="부록 목록 (JSON 문자열)"),
        log_level: int = Form(default=4),
        max_chunk_size: int = Form(default=0),
        auto_convert_to_pdf: bool = Form(default=True),
        use_pdf_sdk: bool = Form(default=True),
):
    """
    multipart 파일 업로드 및 처리

    ### 요청 예시
    ```bash
    curl -X POST http://localhost:7085/upload/run \\
      -F "file=@document.pdf" \\
      -F "save_images=true" \\
      -F "appendix=[]" \\
      -F "log_level=4"
    ```

    ### Form 파라미터
    - **file**: 업로드할 문서 파일 (PDF, HWP, DOCX, PPTX 등)
    - **save_images**: 이미지 저장 여부 (기본값: true)
    - **include_wmf**: WMF 이미지 포함 여부 (기본값: false)
    - **appendix**: 부록 파일명 JSON 리스트 (기본값: "[]")
    - **log_level**: 로그 레벨 0~5 (기본값: 4)
    - **max_chunk_size**: 청크 토큰 제한 0=무제한 (기본값: 0)
    - **auto_convert_to_pdf**: 비-PDF 자동 변환 (기본값: true)
    - **use_pdf_sdk**: PDF 변환 시 SDK 사용 여부 (기본값: true)
    """
    pt = time.time()
    tmp_file_path = None

    try:
        # 원본 파일명에서 확장자 추출
        original_filename = file.filename if file.filename else "document"
        file_ext = Path(original_filename).suffix or ".bin"

        # 임시 파일 생성 (확장자 유지)
        with tempfile.NamedTemporaryFile(
            suffix=file_ext,
            delete=False,
            prefix="intelligent_"
        ) as tmp_file:
            tmp_file_path = tmp_file.name

            # 업로드된 파일을 임시 파일로 저장
            contents = await file.read()
            tmp_file.write(contents)

        logger.info(f'[intelligent] Upload start: "{original_filename}" (tmp: "{tmp_file_path}")')

        # 요청 params 조성
        params = {
            "save_images": save_images,
            "include_wmf": include_wmf,
            "appendix": appendix,
            "log_level": log_level,
            "max_chunk_size": max_chunk_size,
            "auto_convert_to_pdf": auto_convert_to_pdf,
            "use_pdf_sdk": use_pdf_sdk,
            "file_name": original_filename,  # 원본 파일명 저장
        }

        # DocumentProcessor 호출
        data = await get_processor()(request, tmp_file_path, **params)
        logger.info(f'[intelligent] Upload success: "{original_filename}"')
        result = data
        # result = [item.model_dump() if hasattr(item, 'model_dump') else item for item in data]

    except GenosServiceException as e:
        logger.error(f'[intelligent] Upload error: "{file.filename}"\n{traceback.format_exc()}\n')
        return JSONResponse(
            {
                'code': 1,
                'errMsg': e.error_msg,
                'data': None,
                'error_code': e.error_code,
                'error_msg': e.error_msg
            },
            status_code=200
        )
    except Exception as e:
        logger.error(f'[intelligent] Upload error: "{file.filename}"\n{traceback.format_exc()}\n')
        return make_failure_response(str(e))
    finally:
        # 임시 파일 정리
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
                logger.info(f'[intelligent] Cleaned up temp file: "{tmp_file_path}"')
            except Exception as e:
                logger.warning(f'[intelligent] Failed to cleanup temp file: {e}')

        logger.info(f'[intelligent] Upload end: "{file.filename}" ({time.time() - pt:.2f} seconds)')

    return make_success_response(data=result)


# ============================================================
# Main
# ============================================================

if __name__ == '__main__':
    import uvicorn

    uvicorn.run('intelligent_main:app', host='0.0.0.0', port=7085, reload=True)
