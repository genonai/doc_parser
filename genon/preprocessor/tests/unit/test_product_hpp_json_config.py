"""캡처 기반 삼성카드 WCMS JSON 설정의 실제 매핑 회귀 테스트."""
import json
from pathlib import Path

from genon.preprocessor.facade.enrichment.json_records import JsonRecordsMapper


PREPROCESSOR_DIR = Path(__file__).resolve().parents[2]
RESOURCE_DIR = PREPROCESSOR_DIR / "resource"
SAMPLE = PREPROCESSOR_DIR / "sample_files/monimo/monimo_product_hpp_wcms_sample.json"


def test_product_hpp_wcms_sample_maps_one_product_and_search_text():
    mapper = JsonRecordsMapper(
        config_file="custom_field_product_hpp_json.yaml",
        resource_path=str(RESOURCE_DIR),
        doc_type="product_hpp",
        extractor="json_mapping",
    )
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))

    rows = mapper.build_fields(payload, "product_hpp", table_format="markdown")
    assert len(rows) == 1

    row = rows[0]
    assert row["BIZ_ID"] == "1202631"
    assert row["PRODUCT_C"] == "AAP1344"
    assert row["PRODUCT_NM"] == "새마을금고 삼성카드 7"
    assert row["GROUP_C"] == "HPP"
    assert row["SALE_STATUS"] is None
    assert row["PRODUCT_ATTRS"] == [
        "새마을금고 현금카드 기능",
        "0.5%~3% 빅포인트 적립",
        "주말 2·3개월 무이자할부",
    ]

    # 반복 serviceUrl은 bubble[]과 내부 mpo[].ksp[]에서 모두 수집한다.
    assert len(row["BENEFIT_DETAIL_HTMLS"]) == 3
    assert "국내외 가맹점" in row["BENEFIT_DETAIL_TEXT"]
    assert "S-OIL" in row["BENEFIT_DETAIL_TEXT"]
    assert "자동으로 찾아 할인" in row["BENEFIT_DETAIL_TEXT"]
    # Docling 버전이 맞는 환경에서는 표 구조가 유지되고, 불일치 환경의 안전 폴백에서도
    # 셀 텍스트 자체는 보존되어 검색 내용이 유실되지 않는다.
    assert "구분" in row["FEE_TEXT"]
    assert "연회비" in row["FEE_TEXT"]

    result = mapper.to_parse_format(rows, "product_hpp")
    assert result["usage"] == {"pages": 1}
    element = result["elements"][0]
    assert element["splittable"] is True
    assert "새마을금고 삼성카드 7" in element["content"]
    assert "주말 2·3개월 무이자할부" in element["content"]
    assert "18,000원" in element["content"]
    assert "발급 중단" in element["content"]
