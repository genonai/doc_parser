# activity 폴더 — 파일 하나가 아니라 폴더 하나가 activity 하나다. 코드는 processor.py, 설정은 config.yaml 이고,
# 설정이 파일 이름으로 부르는 프롬프트·custom_field 파일도 이 폴더 안에 사본으로 둔다. 폴더 이름 = slug(activity 를 가리키는 짧은 이름) = activity 이름.
# 이전 import 경로(`from genon.preprocessor.activities.parse import DocumentProcessor`)가 그대로 동작하도록 다시 내보낸다.
from .processor import DocumentProcessor  # noqa: F401
