# import os
# import shutil
# import xml.etree.ElementTree as ET 
# import zipfile
# import pandas as pd

# # 한글 파일 불러오기
# hwpx_file = r"/workspaces/hwpx/외국금 차례.hwpx"
# os.chdir(os.path.dirname(hwpx_file))
# path = os.path.join(os.getcwd(), "hwp")

# with zipfile.ZipFile(hwpx_file, 'r') as zf:
#     zf.extractall(path=path)

import os
import zipfile

# 1. 원본 .hwpx 파일 경로
hwpx_file = r"/workspaces/hwpx/★(통화정책국)의결문(안) 및 참고자료(1810)_의결문제외.hwpx"

# 2. 파일명(확장자 제외) 추출
base_name = os.path.splitext(os.path.basename(hwpx_file))[0]  # -> "외국금 차례"

# 3. 저장할 디렉토리 경로 설정: <현재 작업 폴더>/hwpx/xml/{base_name}
output_dir = os.path.join(os.getcwd(), "hwpx", "xml", base_name)

# 4. 디렉토리 없으면 생성
os.makedirs(output_dir, exist_ok=True)

# 5. .hwpx는 zip이므로, 해당 디렉토리에 모두 추출
with zipfile.ZipFile(hwpx_file, 'r') as zf:
    zf.extractall(path=output_dir)

print(f"Extracted all files to: {output_dir}")
