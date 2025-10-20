from fastapi import Request
import logging

# OCR 로깅 활성화
logging.basicConfig(level=logging.INFO)
logging.getLogger("docling").setLevel(logging.DEBUG)

# from basic_processor import DocumentProcessor
from 이미지판독 import DocumentProcessor
# from intelligent_processor import DocumentProcessor
# from 삼성증권.문서생성_범용에이전트 import DocumentProcessor

# 파일 경로 및 요청 설정
import os
file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_files", "/workspaces/docx_attach/doc_parser/sample_files/png2_sample.png")

# 파일 존재 여부 확인
if not os.path.exists(file_path):
    print(f"Sample file not found: {file_path}")
    print("Please add a file to the sample_files folder.")
    exit(1)

# DocumentProcessor 인스턴스 생성
doc_processor = DocumentProcessor()

# FastAPI 요청 예제
mock_request = Request(scope={"type": "http"})

# 비동기 메서드 실행
import asyncio


async def process_document():
    # print(f"Processing file: {file_path}")
    # print("OCR 설정 확인:")
    # print(f"- do_ocr: {doc_processor.pipe_line_options.do_ocr}")
    # print(f"- ocr_options type: {type(doc_processor.pipe_line_options.ocr_options)}")
    # if hasattr(doc_processor.pipe_line_options.ocr_options, 'lang'):
    #     print(f"- OCR languages: {doc_processor.pipe_line_options.ocr_options.lang}")
    
    vectors = await doc_processor(mock_request, file_path)
    # WMF 변환 여부는 include_wmf 파라미터 전달: 현재 한글만 지원
    # vectors = await doc_processor(mock_request, file_path, save_images=True, include_wmf=False)
    return vectors


# 메인 루프 실행
result = asyncio.run(process_document())

result_list_as_dict = [item.model_dump() for item in result]

import json
# 최종적으로 이 리스트를 JSON으로 저장
with open("result.json", "w", encoding="utf-8") as f:
    json.dump(result_list_as_dict, f, ensure_ascii=False, indent=4)