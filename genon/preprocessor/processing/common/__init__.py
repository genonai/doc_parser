"""facade 공용 하위 모듈.

최상위 processor 파일(`*_processor.py`)은 서로 import 하지 않지만, 배포본에 함께
담기는 이 패키지는 공유할 수 있다. docling 타입에 의존하지 않고 duck typing 으로
처리해 배포본이 docling 버전에 묶이지 않게 한다.
"""
