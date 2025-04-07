
from pathlib import Path
import json
from law_preprocess import DocumentProcessor  # 클래스 임포트

# DocumentProcessor 클래스 인스턴스 생성
processor = DocumentProcessor()

# 문서 처리
input_file = Path("/root/workspaces/metadata_268423_20250404_0213.json")
output_dir = Path("/root/workspaces/myenv/mynewtest/realreal_result")

# 법률 문서 처리 
documents = processor.process_law_document(input_file, output_dir)

print(f"처리된 문서가 {output_dir}에 저장되었습니다.")

