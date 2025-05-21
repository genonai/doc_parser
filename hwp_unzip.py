import os
import shutil
import xml.etree.ElementTree as ET 
import zipfile
import pandas as pd

# 한글 파일 불러오기
hwpx_file = r"/workspaces/hwpx/주중금융시장자른버전.hwpx"
os.chdir(os.path.dirname(hwpx_file))
path = os.path.join(os.getcwd(), "hwp")

with zipfile.ZipFile(hwpx_file, 'r') as zf:
    zf.extractall(path=path)