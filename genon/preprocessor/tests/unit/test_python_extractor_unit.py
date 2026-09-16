"""extractor: python — 값을 만드는 주체가 LLM 이 아니라 고객 함수인 경우 (#363 09 B군 ⑧).

지금까지 값을 뽑는 방법은 LLM·엑셀 열 매핑·JSON 키 매핑 3종 고정이었다. 정규식 추출이나
사내 마스터 조회는 코어를 고치지 않고는 넣을 수 없었다.

핵심 계약은 하나다 — **값을 만드는 주체만 다르고 그 뒤는 llm 과 완전히 같은 경로**다.
출력 필드 정리, defaults/constants, value_map/transforms/derive, 문서 저장까지 같은
코드가 돌아야 설정으로 하던 것과 코드로 하는 것이 어긋나지 않는다.

네트워크·LLM 을 부르지 않는다.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

pytestmark = pytest.mark.unit

cfe = pytest.importorskip("genon.preprocessor.processing.enrichment.custom_fields_enricher")
cs = pytest.importorskip("genon.preprocessor.processing.enrichment.config_schema")
cv2 = pytest.importorskip("genon.preprocessor.processing.enrichment.config_v2")
pl = pytest.importorskip("genon.preprocessor.processing.enrichment.plugin_loader")

_EXTRACTOR_PY = '''
def extract(text, document=None, output_fields=None, **kwargs):
    return {"CODE": text.strip().upper(), "SEEN_DOC_TYPE": kwargs.get("doc_type")}


async def extract_async(text, **kwargs):
    return {"CODE": "ASYNC"}


def extract_narrow(text):
    """넓은 시그니처를 못 받는 함수도 (text) 하나로 불린다."""
    return {"CODE": "NARROW"}


def not_a_dict(text, **kwargs):
    return ["nope"]
'''


def _site(tmp_path: Path, config: dict, py: str = _EXTRACTOR_PY) -> tuple[str, str]:
    """설정 yaml 과 고객 파이썬 파일을 같은 폴더에 둔다(경로 해석 기준)."""
    (tmp_path / "site_extract.py").write_text(py, encoding="utf-8")
    name = "custom_field_site.yaml"
    (tmp_path / name).write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return name, str(tmp_path)


def _enricher(tmp_path: Path, *, callable_name: str = "extract", **config_extra):
    config = {"schema": "v2", "source": {"kind": "document"},
              "python": {"file": "site_extract.py", "callable": callable_name,
                         "out": ["CODE"]},
              **config_extra}
    config_file, resource_path = _site(tmp_path, config)
    return cfe.CustomFieldsEnricher(
        doc_type="site", extractor="python",
        config_file=config_file, resource_path=resource_path,
    )


def _run(enricher, text="abc", **kwargs):
    """enrich 를 돌리고 문서에 저장된 metadata 를 돌려준다."""
    doc = MagicMock()
    enricher._extract_raw_text = MagicMock(return_value=text)
    stored: list = []
    original = cfe.store_metadata_in_document
    cfe.store_metadata_in_document = lambda document, metadata, **kw: stored.append(metadata)
    try:
        asyncio.run(enricher.enrich(doc, doc_type="site", **kwargs))
    finally:
        cfe.store_metadata_in_document = original
    return stored[0] if stored else {}


# ---------------------------------------------------------------------------
# 등록 — 문서 단위 extractor 로 인정된다
# ---------------------------------------------------------------------------

def test_python_is_a_document_scope_extractor():
    assert "python" in cfe.DOCUMENT_CUSTOM_FIELD_EXTRACTORS
    assert "python" in cfe.SUPPORTED_CUSTOM_FIELD_EXTRACTORS


def test_builder_creates_an_enricher_for_python(tmp_path: Path):
    config_file, resource_path = _site(tmp_path, {
        "schema": "v2", "source": {"kind": "document"},
        "python": {"file": "site_extract.py"}})
    enrichers = cfe.build_document_custom_fields_enrichers([
        {"extractor": "python", "config_file": config_file, "resource_path": resource_path},
    ])
    assert len(enrichers) == 1 and enrichers[0].is_configured is True


def test_missing_file_fails_at_startup(tmp_path: Path):
    """오설정은 첫 요청이 아니라 기동에서 드러나야 한다."""
    config_file, resource_path = _site(tmp_path, {
        "schema": "v2", "source": {"kind": "document"},
        "python": {"file": "없는파일.py"}})
    with pytest.raises(FileNotFoundError):
        cfe.CustomFieldsEnricher(
            extractor="python", config_file=config_file, resource_path=resource_path)


def test_path_escape_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="허용 범위"):
        pl.load_callable(tmp_path, "../밖.py", "extract", label="t")


# ---------------------------------------------------------------------------
# 호출 계약
# ---------------------------------------------------------------------------

def test_callable_receives_text_and_request_params(tmp_path: Path):
    stored = _run(_enricher(tmp_path), text=" abc ")
    assert stored["CODE"] == "ABC"


def test_narrow_signature_is_supported(tmp_path: Path):
    stored = _run(_enricher(tmp_path, callable_name="extract_narrow"))
    assert stored["CODE"] == "NARROW"


def test_async_callable_is_awaited(tmp_path: Path):
    """사내 API 조회처럼 외부 호출이 필요한 추출기를 쓸 수 있어야 한다."""
    stored = _run(_enricher(tmp_path, callable_name="extract_async"))
    assert stored["CODE"] == "ASYNC"


def test_non_dict_result_is_reported_not_stored(tmp_path: Path):
    """계약 위반은 추출 실패로 흡수한다 — 문서 전체가 죽지는 않는다."""
    stored = _run(_enricher(tmp_path, callable_name="not_a_dict"))
    assert stored == {"CODE": None}


# ---------------------------------------------------------------------------
# llm 과 같은 값 파이프라인을 탄다 — 이 항목이 이번 작업의 핵심이다
# ---------------------------------------------------------------------------

def test_value_pipeline_is_shared_with_llm(tmp_path: Path):
    stored = _run(_enricher(
        tmp_path,
        fields={"SRC": {"const": "REGEX"},
                "MISSING": {"default": "기본값"},
                "CODE": {"transform": ["text_norm"]}},
    ))
    assert stored["SRC"] == "REGEX"        # const
    assert stored["MISSING"] == "기본값"    # default
    # 추출기는 "ABC" 를 돌려줬는데 text_norm(대소문자·공백 정규화)이 걸려 "abc" 가 된다.
    assert stored["CODE"] == "abc"


def test_pack_bundles_fields_into_json(tmp_path: Path):
    """`pack` — 문서형도 rows/records 와 같은 자리에서 같은 결과를 낸다.

    묶은 값은 문서 metadata 에 **문자열**로 실린다. dict 로 두면 청크 출력 경로마다
    모양이 갈린다(문서형은 문자열로 낮추고, 행 경로는 객체를 그대로 싣는다).
    """
    stored = _run(_enricher(
        tmp_path,
        fields={"SRC": {"const": "REGEX"},
                "MISSING": {"default": None},
                "DETAIL_JSON": {"pack": ["CODE", "SRC", "MISSING"]}},
    ))
    assert isinstance(stored["DETAIL_JSON"], str)
    assert json.loads(stored["DETAIL_JSON"]) == {
        "CODE": "ABC", "SRC": "REGEX", "MISSING": None,
    }


def test_to_json_applies_on_the_document_path(tmp_path: Path):
    """`to_json` — 문서형(llm·python)도 rows/records/sections 와 같은 자리에서 돈다.

    `pack` 은 묶을 원천이 여럿일 때의 기능이라 출처가 하나뿐인 필드에는 쓸 수 없다.
    적재 DB 의 JSON 컬럼이 스칼라를 받으면 `JSON_VALUE(col, '$.키')` 가 NULL 이 되므로,
    스칼라가 오면 키를 씌워 객체로 만든다.
    """
    stored = _run(_enricher(
        tmp_path,
        fields={
            "ATTRS": {"const": '{"annual_fee":  18000}', "transform": ["to_json"]},
            "FEE_TEXT": {"const": "국내전용 18,000원",
                         "transform": [{"name": "to_json", "key": "fee_text"}]},
        },
    ))
    assert json.loads(stored["ATTRS"]) == {"annual_fee": 18000}
    assert json.loads(stored["FEE_TEXT"]) == {"fee_text": "국내전용 18,000원"}


def test_to_json_on_a_body_field_fails_at_startup(tmp_path: Path):
    """변환은 필드를 제자리에서 덮으므로, 본문에도 쓰이는 필드에 걸면 본문에 JSON 이 실린다."""
    with pytest.raises(ValueError, match="쓸 수 없습니다"):
        _enricher(tmp_path, fields={"CODE": {"transform": ["to_json"]}},
                  body={"repeat": ["CODE"]})


def test_pack_referencing_an_unknown_field_fails_at_startup(tmp_path: Path):
    with pytest.raises(ValueError, match="NO_SUCH_FIELD"):
        _enricher(tmp_path, fields={"DETAIL_JSON": {"pack": ["NO_SUCH_FIELD"]}})


def test_output_fields_filter_applies(tmp_path: Path):
    """선언한 필드만 남긴다(추출기가 더 많이 돌려줘도)."""
    stored = _run(_enricher(tmp_path))
    assert set(stored) == {"CODE"}          # SEEN_DOC_TYPE 은 output_fields 밖


def test_table_descriptions_are_not_fused_for_python(tmp_path: Path):
    """표 설명은 LLM 이 만든다 — python 추출기는 그 호출을 하지 않는다."""
    enricher = _enricher(tmp_path)
    assert enricher.wants_table_descriptions(doc_type="site", table_text_desc=1) is False


# ---------------------------------------------------------------------------
# 설정 표기 — 기동 검증과 v2
# ---------------------------------------------------------------------------

def test_llm_only_keys_are_rejected_for_python():
    """다른 extractor 의 키를 쓰면 읽히지 않으므로 기동에서 막는다."""
    with pytest.raises(ValueError, match="쓸 수 없는 키"):
        cs.validate_known_keys({"url": "http://x"}, label="t", extractor="python")


def test_python_keys_are_declared():
    keys = cs.EXTRACTOR_KEYS["python"]
    assert {"file", "callable", "output_fields", "transforms"} <= keys
    assert "url" not in keys and "system_prompt" not in keys


def test_v2_notation_normalizes():
    """`python` 블록과 필드 상수가 내부 형태로 풀리는 자리를 고정한다."""
    v2 = {"schema": "v2", "source": {"kind": "document"},
          "python": {"file": "site_extract.py", "callable": "extract", "out": ["CODE"]},
          "fields": {"SRC": {"const": "REGEX"}}}
    back, _ = cv2.load(dict(v2), label="t")
    assert back == {"file": "site_extract.py", "callable": "extract",
                    "output_fields": ["CODE"], "constants": {"SRC": "REGEX"}}


def test_v2_rejects_llm_and_python_together():
    bad = {"schema": "v2", "source": {"kind": "document"},
           "python": {"file": "a.py"},
           "llm": [{"endpoint": {"url": "http://x", "model": "m"}}]}
    with pytest.raises(cv2.ConfigV2Error, match="함께 쓸 수 없습니다"):
        cv2.load(bad, label="t")


def test_v2_python_is_document_only():
    bad = {"schema": "v2", "source": {"kind": "rows"}, "python": {"file": "a.py"}}
    with pytest.raises(cv2.ConfigV2Error, match="document 전용"):
        cv2.load(bad, label="t")
