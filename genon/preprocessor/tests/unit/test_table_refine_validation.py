"""
table_description refine 재구성 HTML 의 구조 유효성 검증 단위 테스트.

재구성 표가 구조적으로 유효하지 않으면(<table> 없음 / grid 빈 / 2행 미만 / 첫 행 빈)
`is_valid_refined_html` 이 False 를 반환해야 한다(→ 부착 안 함 → 원본 표 폴백).

의존성(bs4/docling 등) 미가용 환경에서는 importorskip 으로 자동 skip 된다(CI gate).
"""

import pytest

_MOD = "processing.enrichment.table_description"


def _mod():
    return pytest.importorskip(_MOD)


@pytest.mark.unit
def test_valid_table_is_accepted():
    m = _mod()
    html = (
        "<table><thead><tr><th>구분</th><th>값</th></tr></thead>"
        "<tbody><tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></tbody></table>"
    )
    assert m.is_valid_refined_html(html) is True
    assert m._parse_refined_table_data(html) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "html",
    [
        "",                       # 빈 문자열
        "   ",                    # 공백만
        "<div>표 아님</div>",       # <table> 없음
        "일반 텍스트",              # 태그 없음
        "<table><tr><th>A</th><th>B</th></tr></table>",  # 헤더만(1행) → 2행 미만
        # </table> 없음(잘린 출력) — bs4 자동보정 방지 태그쌍 검사로 걸러야 함
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr>",
        # 여는 태그 2 / 닫는 태그 1 (불균형)
        "<table><tr><td>x</td></tr><table><tr><td>1</td><td>2</td></tr></table>",
        # 첫 행(헤더) 셀이 전부 빈 값 → 유효한 표 아님
        "<table><tr><th></th><th></th></tr><tr><td>1</td><td>2</td></tr></table>",
    ],
)
def test_invalid_refined_html_is_rejected(html):
    m = _mod()
    assert m.is_valid_refined_html(html) is False
    assert m._parse_refined_table_data(html) is None


# ── resolve_runtime_table_options: table_refine 단독으로도 enrichment 활성 ──────
@pytest.mark.unit
def test_table_refine_alone_enables():
    m = _mod()
    base = m.TableDescriptionOptions()
    # table_refine=1 단독이면 enabled=True, refine_enabled=True
    r = m.resolve_runtime_table_options(base, table_desc=0, table_refine=1)
    assert r.enabled is True and r.refine_enabled is True
    # 둘 다 0이면 비활성
    off = m.resolve_runtime_table_options(base, table_desc=0, table_refine=0)
    assert off.enabled is False and off.refine_enabled is False
    # table_desc=1, refine=0 → enabled True, refine False
    d = m.resolve_runtime_table_options(base, table_desc=1, table_refine=0)
    assert d.enabled is True and d.refine_enabled is False


# ── _parse_refine_output: degenerate/잘린 응답 폐기 ────────────────────────────
@pytest.mark.unit
def test_parse_refine_output_valid_split():
    m = _mod()
    out = "[[[TABLE_HTML]]]\n<table><tr><td>1</td></tr></table>\n[[[TABLE_SUMMARY]]]\n표 요약입니다."
    summary, refined_html = m.TableDescriptionEnricher._parse_refine_output(out)
    assert summary == "표 요약입니다."
    assert "<table>" in refined_html and "[[[TABLE_HTML]]]" not in refined_html


@pytest.mark.unit
def test_parse_refine_output_truncated_marker_is_discarded():
    """[[[TABLE_HTML]]] 만 있고 [[[TABLE_SUMMARY]]] 누락(잘림/degeneration) → ("", "")."""
    m = _mod()
    out = "[[[TABLE_HTML]]]\n<table><tr><td>결정</td></tr>\n결정 결정 결정"  # </table>·SUMMARY 마커 없음
    assert m.TableDescriptionEnricher._parse_refine_output(out) == ("", "")


@pytest.mark.unit
def test_parse_refine_output_plain_prose_is_summary():
    m = _mod()
    out = "이 표는 부서별 권한을 나타낸다."
    summary, refined_html = m.TableDescriptionEnricher._parse_refine_output(out)
    assert summary == out and refined_html == ""


# ── is_valid_table_summary ────────────────────────────────────────────────────
@pytest.mark.unit
def test_valid_table_summary():
    m = _mod()
    assert m.is_valid_table_summary("이 표는 부서별 직무권한을 정리한 표이다.") is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "summary",
    [
        "",
        "   ",
        "[[[TABLE_HTML]]]<table><tr><td>결정</td></tr>",  # 마커 잔재
        "요약 <table border='1'><tr><td>x</td></tr></table>",  # 원문 table 마크업
    ],
)
def test_invalid_table_summary(summary):
    m = _mod()
    assert m.is_valid_table_summary(summary) is False


# ── params passthrough: 블록 최상위 temperature/top_p 승격 ────────────────────
# table_description/image_description/page_description 은 서로 컨벤션을 공유하는
# sibling 모듈이다(각 모듈 docstring 참조). 세 곳 모두 이전에는 params dict passthrough 만
# 읽어 블록 최상위 temperature/top_p 를 조용히 무시했다.

def _image_mod():
    return pytest.importorskip("processing.enrichment.image_description")


def _page_mod():
    return pytest.importorskip("processing.enrichment.page_description")


@pytest.mark.unit
def test_table_description_promotes_top_level_temperature():
    m = _mod()
    opt = m.TableDescriptionOptions.from_config(
        table_desc_cfg={"temperature": 0.1},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["temperature"] == 0.1


@pytest.mark.unit
def test_table_description_existing_params_temperature_wins():
    m = _mod()
    opt = m.TableDescriptionOptions.from_config(
        table_desc_cfg={"temperature": 0.9, "params": {"temperature": 0.2}},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["temperature"] == 0.2


@pytest.mark.unit
def test_table_description_no_temperature_no_params_key():
    m = _mod()
    opt = m.TableDescriptionOptions.from_config(
        table_desc_cfg={},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert "temperature" not in opt.params


@pytest.mark.unit
def test_image_description_promotes_top_level_temperature():
    m = _image_mod()
    opt = m.ImageDescriptionOptions.from_config(
        image_desc_cfg={"temperature": 0.1},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["temperature"] == 0.1


@pytest.mark.unit
def test_image_description_existing_params_temperature_wins():
    m = _image_mod()
    opt = m.ImageDescriptionOptions.from_config(
        image_desc_cfg={"temperature": 0.9, "params": {"temperature": 0.2}},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["temperature"] == 0.2


@pytest.mark.unit
def test_image_description_no_temperature_no_params_key():
    m = _image_mod()
    opt = m.ImageDescriptionOptions.from_config(
        image_desc_cfg={},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert "temperature" not in opt.params


@pytest.mark.unit
def test_page_description_promotes_top_level_temperature():
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config({"temperature": 0.1}, config_dir=None)
    assert opt.params["temperature"] == 0.1


@pytest.mark.unit
def test_page_description_existing_params_temperature_wins():
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config(
        {"temperature": 0.9, "params": {"temperature": 0.2}}, config_dir=None
    )
    assert opt.params["temperature"] == 0.2


@pytest.mark.unit
def test_page_description_no_temperature_no_params_key():
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config({}, config_dir=None)
    assert "temperature" not in opt.params


# ── collect_generation_params 일반화: seed/repetition_penalty/max_tokens 도 흡수 ──
# temperature/top_p 만 승격하던 인라인 코드를 model_params.collect_generation_params
# 호출로 바꾼 뒤, 나머지 생성 파라미터도 같은 규칙으로 흡수되는지 고정한다.


@pytest.mark.unit
def test_table_description_absorbs_seed_repetition_penalty_max_tokens():
    m = _mod()
    opt = m.TableDescriptionOptions.from_config(
        table_desc_cfg={"seed": 7, "repetition_penalty": 1.1, "max_tokens": 512},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["seed"] == 7
    assert opt.params["repetition_penalty"] == 1.1
    assert opt.params["max_tokens"] == 512


@pytest.mark.unit
def test_table_description_params_dict_wins_over_top_level():
    m = _mod()
    opt = m.TableDescriptionOptions.from_config(
        table_desc_cfg={"seed": 7, "params": {"seed": 99}},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["seed"] == 99


@pytest.mark.unit
def test_table_description_unset_generation_keys_absent():
    m = _mod()
    opt = m.TableDescriptionOptions.from_config(
        table_desc_cfg={},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert "seed" not in opt.params
    assert "repetition_penalty" not in opt.params
    assert "max_tokens" not in opt.params


@pytest.mark.unit
def test_image_description_absorbs_seed_repetition_penalty_max_tokens():
    m = _image_mod()
    opt = m.ImageDescriptionOptions.from_config(
        image_desc_cfg={"seed": 7, "repetition_penalty": 1.1, "max_tokens": 512},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["seed"] == 7
    assert opt.params["repetition_penalty"] == 1.1
    assert opt.params["max_tokens"] == 512


@pytest.mark.unit
def test_image_description_params_dict_wins_over_top_level():
    m = _image_mod()
    opt = m.ImageDescriptionOptions.from_config(
        image_desc_cfg={"seed": 7, "params": {"seed": 99}},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert opt.params["seed"] == 99


@pytest.mark.unit
def test_image_description_unset_generation_keys_absent():
    m = _image_mod()
    opt = m.ImageDescriptionOptions.from_config(
        image_desc_cfg={},
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    assert "seed" not in opt.params
    assert "repetition_penalty" not in opt.params
    assert "max_tokens" not in opt.params


@pytest.mark.unit
def test_page_description_absorbs_seed_and_repetition_penalty():
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config(
        {"seed": 7, "repetition_penalty": 1.1}, config_dir=None
    )
    assert opt.params["seed"] == 7
    assert opt.params["repetition_penalty"] == 1.1


@pytest.mark.unit
def test_page_description_params_dict_wins_over_top_level():
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config(
        {"seed": 7, "params": {"seed": 99}}, config_dir=None
    )
    assert opt.params["seed"] == 99


# ── page_description 의 max_tokens: typed 필드가 '0=상한 없음(키 미전송)' 을 계속 지킨다 ──
# collect_generation_params 는 설정에 적힌 값을 그대로 담는 일반 규칙이라 max_tokens=0 도
# 그대로 담아버리면 기존 '0=상한 없음' 의미가 깨진다. 그래서 top-level max_tokens 는
# typed 필드(+조건부 주입)가 계속 전담하고, collect_generation_params 입력에서는 뺀다.


@pytest.mark.unit
def test_page_description_max_tokens_unset_not_sent():
    """설정에 max_tokens 를 안 적으면(기존 현장) req_params 에 키 자체가 없다."""
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config({}, config_dir=None)
    assert opt.max_tokens == 0
    assert "max_tokens" not in opt.params


@pytest.mark.unit
def test_page_description_max_tokens_zero_means_unlimited_not_sent():
    """max_tokens: 0 을 명시해도(상한 없음) params 에 실리지 않는다."""
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config({"max_tokens": 0}, config_dir=None)
    assert opt.max_tokens == 0
    assert "max_tokens" not in opt.params


@pytest.mark.unit
def test_page_description_max_tokens_positive_is_typed_field():
    """양수 max_tokens 는 typed 필드에 실린다(요청 조립은 describe_page_images 가 담당)."""
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config({"max_tokens": 300}, config_dir=None)
    assert opt.max_tokens == 300


@pytest.mark.unit
def test_page_description_max_tokens_via_params_escape_hatch():
    """params.max_tokens 는 탈출구라 0 을 포함해 그대로 전송 대상에 실린다."""
    m = _page_mod()
    opt = m.PageDescriptionOptions.from_config(
        {"params": {"max_tokens": 0}}, config_dir=None
    )
    assert opt.params["max_tokens"] == 0


# ── describe_page_images: 실제 요청 파라미터 조립(하위 호환 고정) ────────────────


@pytest.mark.unit
def test_describe_page_images_max_tokens_default_not_sent(monkeypatch):
    """기존 현장 무변경: max_tokens 미설정이면 req_params 에 키가 없다."""
    m = _page_mod()
    from PIL import Image

    captured = {}

    def _fake_api_image_request(*, image, prompt, url, timeout, headers, **params):
        captured.update(params)
        captured["_headers"] = headers
        return "설명"

    monkeypatch.setattr(m, "api_image_request", _fake_api_image_request)

    opt = m.PageDescriptionOptions.from_config(
        {"enable": True, "url": "http://example.invalid/vlm"}, config_dir=None
    )
    images = {1: Image.new("RGB", (2, 2))}
    result = m.describe_page_images(images, opt)
    assert result == {1: "설명"}
    assert "max_tokens" not in captured


@pytest.mark.unit
def test_describe_page_images_max_tokens_configured_is_sent(monkeypatch):
    m = _page_mod()
    from PIL import Image

    captured = {}

    def _fake_api_image_request(*, image, prompt, url, timeout, headers, **params):
        captured.update(params)
        return "설명"

    monkeypatch.setattr(m, "api_image_request", _fake_api_image_request)

    opt = m.PageDescriptionOptions.from_config(
        {"enable": True, "url": "http://example.invalid/vlm", "max_tokens": 256},
        config_dir=None,
    )
    images = {1: Image.new("RGB", (2, 2))}
    m.describe_page_images(images, opt)
    assert captured["max_tokens"] == 256


@pytest.mark.unit
def test_describe_page_images_seed_and_headers_configured(monkeypatch):
    m = _page_mod()
    from PIL import Image

    captured = {}

    def _fake_api_image_request(*, image, prompt, url, timeout, headers, **params):
        captured.update(params)
        captured["_headers"] = headers
        return "설명"

    monkeypatch.setattr(m, "api_image_request", _fake_api_image_request)

    opt = m.PageDescriptionOptions.from_config(
        {
            "enable": True,
            "url": "http://example.invalid/vlm",
            "api_key": "secret",
            "seed": 7,
            "headers": {"X-Tenant": "kb"},
        },
        config_dir=None,
    )
    images = {1: Image.new("RGB", (2, 2))}
    m.describe_page_images(images, opt)
    assert captured["seed"] == 7
    assert captured["_headers"]["X-Tenant"] == "kb"
    assert captured["_headers"]["Authorization"] == "Bearer secret"


@pytest.mark.unit
def test_describe_page_images_config_authorization_not_overwritten(monkeypatch):
    m = _page_mod()
    from PIL import Image

    captured = {}

    def _fake_api_image_request(*, image, prompt, url, timeout, headers, **params):
        captured["_headers"] = headers
        return "설명"

    monkeypatch.setattr(m, "api_image_request", _fake_api_image_request)

    opt = m.PageDescriptionOptions.from_config(
        {
            "enable": True,
            "url": "http://example.invalid/vlm",
            "api_key": "secret",
            "headers": {"Authorization": "Basic zzz"},
        },
        config_dir=None,
    )
    images = {1: Image.new("RGB", (2, 2))}
    m.describe_page_images(images, opt)
    assert captured["_headers"]["Authorization"] == "Basic zzz"


# ── image/table description: headers 가 api_image_request 호출에 실제로 실린다 ──


class _FakeVlmItem:
    """picture_item.get_image(document, prov_index=0) 만 흉내내는 최소 더블.

    pydantic 모델(PictureItem/TableItem)은 임의 속성 할당을 막으므로, 실제 docling
    문서를 짓는 대신 헤더 배선만 검증하는 이 더블을 쓴다.
    """

    def get_image(self, document, prov_index=0):
        from PIL import Image as PILImage
        return PILImage.new("RGB", (2, 2))


@pytest.mark.unit
def test_image_description_headers_reach_request(monkeypatch):
    m = _image_mod()

    captured = {}

    def _fake_api_image_request(*, image, prompt, url, timeout, headers, **params):
        captured["_headers"] = headers
        return "설명"

    monkeypatch.setattr(m, "api_image_request", _fake_api_image_request)

    opt = m.ImageDescriptionOptions.from_config(
        image_desc_cfg={
            "enabled": True,
            "api_url": "http://example.invalid/vlm",
            "api_key": "secret",
            "headers": {"X-Tenant": "kb"},
        },
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    enricher = m.ImageDescriptionEnricher(opt)

    result = enricher._annotate_single_picture(None, _FakeVlmItem(), "설명해줘")
    assert result is not None
    assert captured["_headers"]["X-Tenant"] == "kb"
    assert captured["_headers"]["Authorization"] == "Bearer secret"


@pytest.mark.unit
def test_table_description_headers_reach_request(monkeypatch):
    m = _mod()

    captured = {}

    def _fake_api_image_request(*, image, prompt, url, timeout, headers, **params):
        captured["_headers"] = headers
        return "설명"

    monkeypatch.setattr(m, "api_image_request", _fake_api_image_request)

    opt = m.TableDescriptionOptions.from_config(
        table_desc_cfg={
            "enabled": True,
            "api_url": "http://example.invalid/vlm",
            "api_key": "secret",
            "headers": {"X-Tenant": "kb"},
        },
        fallback_api_url="", fallback_api_key="", fallback_model="model",
    )
    enricher = m.TableDescriptionEnricher(opt)

    result = enricher._annotate_single_table(None, _FakeVlmItem(), "설명해줘", False)
    assert result is not None
    assert captured["_headers"]["X-Tenant"] == "kb"
    assert captured["_headers"]["Authorization"] == "Bearer secret"
