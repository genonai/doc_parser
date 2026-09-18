"""출고 삼성카드 WCMS 상품 설정(custom_field_product_hpp.yaml)의 실제 매핑 회귀 테스트.

이 파일은 예전에 json_mapping(레코드 모드) 시절의 custom_field_product_hpp_json.yaml 을
검증했다. 그 설정은 삭제됐고(레코드 1건 = 상품 1건이라는 전제가, 성격이 다른 내용이 섞인
카드 WCMS JSON 에는 맞지 않아 json_semantic 으로 교체됐다 — json_semantic.py 모듈 docstring
참고) parser_processor_config*.yaml 의 product_hpp 항목도 extractor: json_semantic +
config_file: custom_field_product_hpp.yaml 을 가리키도록 바뀌었다. 이 파일은 그
출고 설정을 실제 두 샘플로 재검증한다.
"""
import json
from pathlib import Path

from genon.preprocessor.processing.enrichment.json_semantic import SemanticJsonMapper

PREPROCESSOR_DIR = Path(__file__).resolve().parents[2]
RESOURCE_DIR = PREPROCESSOR_DIR / "resource"
SAMPLE = PREPROCESSOR_DIR / "sample_files/monimo/monimo_product_hpp_wcms_sample.json"
MINIMAL_SAMPLE = PREPROCESSOR_DIR / "sample_files/monimo/monimo_product_hpp_sample.json"


CONFIG_NAME = "custom_field_product_hpp.yaml"


def _build_result(sample_path: Path):
    from shipped_config import sibling_presets

    # 설정의 `endpoint.model_preset` 은 기동 때 프로세서 설정의 `model_presets:` 로 펼쳐진다.
    # 여기서 주지 않으면 참조가 그대로 남아 "config_file 또는 url 이 필요하다" 로 갈린다.
    mapper = SemanticJsonMapper(
        config_file=CONFIG_NAME,
        resource_path=str(RESOURCE_DIR),
        model_presets=sibling_presets(RESOURCE_DIR / CONFIG_NAME),
        doc_type="product_hpp",
        extractor="json_semantic",
    )
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    fields_list = mapper.build_fields(payload, "product_hpp", table_format="markdown")
    return mapper.to_parse_format(fields_list, "product_hpp")


def test_product_hpp_wcms_sample_maps_to_sections():
    result = _build_result(SAMPLE)
    elements = result["elements"]

    # 개요 1 + 상품 문서(연회비/추가 서비스와 발급 기준/이용 유의사항/상품 안내) 4
    # + 혜택 상세(bubble) 1 + 주요 혜택(benefit) 1 = 7.
    #
    # 접히는 섹션은 4건이다(식별자/빈 본문뿐). ksp 3건은 title 을 빼면 code 만 남는다.
    # bubble 은 원천에 2건인데 1건만 남는다 — 본문에 해당하는 serviceUrl 을 사이트 운영
    # 설정이 ignore_keys 에 두었고(커밋 263f53ea), 그러면 tabName 을 가진 쪽만 본문이
    # 생긴다. tabName 이 없는 bubble(S-OIL 적립)은 제목만 남아 접힌다. 즉 이 7 은
    # "설정이 혜택 상세 본문을 검색에서 뺀 상태" 를 고정한 값이다.
    assert len(elements) == 7

    contents = [e["content"] for e in elements]

    # 전 섹션 본문에 상품명이 상속돼 있다(공통 정보 상속 — 어떤 청크가 검색돼도 어느
    # 카드 이야기인지 알 수 있어야 한다).
    for content in contents:
        assert "상품명: 새마을금고 삼성카드 7" in content

    # htmlList 의 각 HTML 필드가 스스로 들고 있는 <h3> 제목으로 섹션 제목이 승격된다.
    headers = [content.splitlines()[0] for content in contents]
    for expected_title in ("연회비", "추가 서비스와 발급 기준", "이용 유의사항", "상품 안내"):
        assert any(expected_title in header for header in headers), (
            f"'{expected_title}' 이 어느 섹션 제목에도 없음: {headers}"
        )

    # 연회비 섹션에는 표 셀 텍스트가 살아있어야 한다. 표 구조(파이프 개수 등)는 docling
    # 버전에 따라 갈릴 수 있어 단정하지 않고, 셀 텍스트 보존만 확인한다(html_to_text 의
    # 안전 폴백에서도 텍스트 자체는 보존된다 — json_records.html_to_text 참고).
    # 셀 값이 인라인 태그로 쪼개져 있어(`<span>18,000</span>원`) 공백 삽입 여부는
    # 백엔드 사정이다. 숫자 보존만 본다.
    fee_content = next(c for c in contents if "18,000" in c)
    assert "구분" in fee_content
    assert "연회비" in fee_content
    # 행 라벨이 `<th scope="row">` 인 실제 WCMS 표다. 행 라벨이 헤더 행으로 오인되면
    # 표가 html 로 새고 데이터 행이 사라진다(table_shape.leading_header_row_count 회귀).
    assert "총 연회비" in fee_content and "제휴 연회비" in fee_content

    # 추천 상품(mpo)의 혜택 문구는 이 상품 것이 아니므로 어느 섹션에도 없어야 한다.
    all_content = "\n".join(contents)
    assert "많이 쓰는 영역 5% 자동 맞춤 할인" not in all_content

    # 원문 JSON 키 이름(사람이 안 붙인 것)이 본문에 새어 나오지 않는다.
    for raw_key in (
        "imgInfo", "pcImg1", "is3d", "pCApplyNoYn", "serviceCode", "cardSlogan", "mpoNum",
    ):
        assert raw_key not in all_content, f"원문 키 '{raw_key}' 가 본문에 유출됨"

    # 섹션마다 SECTION_NM(None 아님) + SOURCE_JSON_PATH + 공통 식별 필드가 채워진다.
    # PRODUCT_C 는 ksp[].code 같은 하위 객체의 동명 키에 덮이지 않고 루트 값(AAP1344)으로
    # 전 섹션에 고정된다(리뷰 지적 1 회귀 방지).
    for element in elements:
        metadata = element["metadata"]
        assert metadata.get("SECTION_NM")
        assert metadata.get("SOURCE_JSON_PATH")
        assert metadata.get("PRODUCT_NM") == "새마을금고 삼성카드 7"
        assert metadata.get("PRODUCT_C") == "AAP1344"
        assert metadata.get("BIZ_ID") == "1202631"
        assert metadata.get("GROUP_C") == "HPP"
        # SALE_STATUS 는 원천에 값이 없을 때 채우는 기본값이다. 사이트 설정이 적재 코드값
        # `SALE` 로 바꿨다(custom_field_product_hpp.yaml 의 `defaults`).
        assert metadata.get("SALE_STATUS") == "SALE"
        # PRODUCT_ATTRS 는 llm 이 만든 FEE 를 `pack` 으로 묶은 값이다. 원천에서 채우지
        # 않으므로(설정의 shared_fields 에 없다) LLM 을 부르지 않는 이 경로에서는 키만
        # 있고 값이 null 인 JSON 이 실린다 — pack 은 적재 스키마를 고정하는 자리라
        # 값이 없다고 키를 빼지 않는다. 원천 benefit 배열로 되채우면 "원천 값이 먼저
        # 채워졌다가 llm 결과에 덮이는" 순서 의존이 되살아난다.
        assert metadata.get("PRODUCT_ATTRS") == '{"FEE": null}'


def test_product_hpp_minimal_sample_yields_sections():
    """mpo 래핑 없이 상품 필드가 루트에 바로 오는 최소 케이스도 섹션 0건이 아니다."""
    result = _build_result(MINIMAL_SAMPLE)
    assert len(result["elements"]) > 0
    for element in result["elements"]:
        assert "상품명: 새마을금고 삼성카드 7" in element["content"]
