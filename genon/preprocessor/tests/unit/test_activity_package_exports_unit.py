"""액티비티 패키지의 재노출 — 검증 러너(`tools/parse_chunk/parse_chunk_test.py`)가 가져오는 이름이 import 되는지 본다.

폴더 이관(#495) 뒤 `activities/chunk/__init__.py` 가 `DocumentProcessor` 만 재노출해, 러너가 함께 가져오는
`GenosServiceException` 에서 ImportError 가 나 `parse_chunk_verify.py` 가 전 케이스 실패했다(#611).
"""


def test_chunk_package_reexports_what_the_runner_imports():
    from genon.preprocessor.activities.chunk import DocumentProcessor, GenosServiceException
    from genon.preprocessor.processing.core.errors import GenosServiceException as core_error

    assert DocumentProcessor is not None
    assert GenosServiceException is core_error      # 같은 클래스여야 러너의 except 가 처리기 예외를 잡는다


def test_parse_package_reexports_what_the_runner_imports():
    from genon.preprocessor.activities.parse import DocumentProcessor

    assert DocumentProcessor is not None
