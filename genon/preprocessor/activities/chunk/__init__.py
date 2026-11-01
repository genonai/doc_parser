# 액티비티 폴더 — 파일 하나가 아니라 폴더 하나가 액티비티다(#495). 코드는 processor.py, 설정은 config.yaml.
# 폴더 이름 = slug = 액티비티 이름.
# 옛 import(`from genon.preprocessor.activities.chunk import DocumentProcessor, GenosServiceException`)가 그대로 돌게
# 재노출한다 — tools/parse_chunk/parse_chunk_test.py 가 둘 다 가져온다(폴더 이관 #495 뒤 예외가 빠져 러너가 깨져 있었다).
from .processor import DocumentProcessor, GenosServiceException  # noqa: F401
