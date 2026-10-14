# 액티비티 폴더 — 파일 하나가 아니라 폴더 하나가 액티비티다(#495). 코드는 processor.py, 설정은 config.yaml.
# 폴더 이름 = slug = 액티비티 이름.
# 옛 import(`from genon.preprocessor.activities.chunk import DocumentProcessor`)가 그대로 돌게 재노출한다.
from .processor import DocumentProcessor  # noqa: F401
