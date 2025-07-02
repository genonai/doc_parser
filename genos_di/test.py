from fastapi import Request

from preprocess import DocumentProcessor
# from origin_preprocess import DocumentProcessor

# 파일 경로 및 요청 설정
file_path = "/workspaces/hwpx/외환국제금융동향(2018.4.12)_최종(송부본).hwpx"

# DocumentProcessor 인스턴스 생성
doc_processor = DocumentProcessor()

# FastAPI 요청 예제
mock_request = Request(scope={"type": "http"})

# 비동기 메서드 실행
import asyncio


async def process_document():
    print(file_path)
    vectors = await doc_processor(mock_request, file_path)
    return vectors


# 메인 루프 실행
result = asyncio.run(process_document())

result_list_as_dict = [item.model_dump() for item in result]

import json
# 최종적으로 이 리스트를 JSON으로 저장
with open("result.json", "w", encoding="utf-8") as f:
    json.dump(result_list_as_dict, f, ensure_ascii=False, indent=4)