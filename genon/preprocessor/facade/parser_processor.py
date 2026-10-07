# 옛 이름 — 파싱용 전처리기는 activities/parse.py 로 옮겼다(#474). 고칠 곳은 그 파일이다.
#
# 이 모듈은 옛 경로(genon.preprocessor.facade.parser_processor · facade.parser_processor)를 쓰는 import·
# 테스트·도구·실행 명령을 깨지 않으려고 남긴 별칭이다. 같은 모듈 객체를 가리키므로
# monkeypatch·mock 도 그대로 동작한다.
import sys

from genon.preprocessor.activities import parse as _module

if __name__ == "__main__":
    _module.DocumentProcessor.cli()
else:
    sys.modules[__name__] = _module
