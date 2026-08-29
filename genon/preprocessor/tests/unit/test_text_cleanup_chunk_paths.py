"""text_cleanup=safe 가 chunking_processor 의 parse-format 출력 경로 전부에 적용되는지 검증.

parse-format 경로는 3가지다(모두 벡터를 직접 만든다).
  - recursive : 일반 텍스트 → RecursiveCharacterTextSplitter
  - row       : tabular_row/custom_fields_row → 행마다 벡터 1개(metadata 가 청크 property)
  - marker    : audio([AUDIO]) / legacy tabular([DA]) → 단일 벡터

docling 경로는 실제 문서 변환이 필요하므로 여기서 다루지 않는다.
"""

import shutil
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_CONFIG_NAME = "chunking_processor_config.yaml"

# 자모 분리(NFD) + 제로폭 + NBSP + 전각 + 줄 끝 공백 + 빈 줄 3개
_NOISY = "﻿한​글 Ａ   \n\n\n\n본문"


def _make_processor(tmp_path: Path, text_cleanup):
    mod = pytest.importorskip("facade.chunking_processor")
    resource_dir = Path(__file__).resolve().parents[2] / "resource"
    shutil.copytree(resource_dir, tmp_path, dirs_exist_ok=True)
    cfg = yaml.safe_load((resource_dir / _CONFIG_NAME).read_text(encoding="utf-8"))
    cfg.setdefault("chunking", {})["text_cleanup"] = text_cleanup
    cfg["chunking"]["chunk_size"] = 1000
    out = tmp_path / _CONFIG_NAME
    out.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    try:
        return mod.DocumentProcessor(config_path=str(out))
    except Exception as e:  # noqa: BLE001 - 모델/네트워크 등 환경 의존
        pytest.skip(f"DocumentProcessor init unavailable: {e}")


def _assert_clean(text: str):
    assert "​" not in text and "﻿" not in text
    assert " " not in text
    assert "Ａ" not in text and "A" in text
    assert "한글" in text          # NFD → NFC
    assert "\n\n\n" not in text    # 연속 빈 줄 최대 1개
    assert not text.endswith(" ")


def _assert_stats_match(vector):
    text = vector.text
    assert vector.n_char == len(text)
    assert vector.n_word == len(text.split())
    assert vector.n_line == len(text.splitlines())


# ---------------------------------------------------------------------------
# recursive 경로
# ---------------------------------------------------------------------------

def test_recursive_path_normalized(tmp_path):
    proc = _make_processor(tmp_path, "safe")
    vectors = proc._chunk_parse_format([{"content": _NOISY, "page": 1, "category": "text"}])
    assert len(vectors) == 1
    _assert_clean(vectors[0].text)
    _assert_stats_match(vectors[0])


def test_recursive_path_off_keeps_original(tmp_path):
    """기본 off 에서는 기존 산출물이 그대로여야 한다."""
    proc = _make_processor(tmp_path, "off")
    vectors = proc._chunk_parse_format([{"content": _NOISY, "page": 1, "category": "text"}])
    assert "​" in vectors[0].text


def test_recursive_path_kwargs_override(tmp_path):
    """yaml 이 off 여도 요청 kwargs 로 켤 수 있다."""
    proc = _make_processor(tmp_path, "off")
    vectors = proc._chunk_parse_format(
        [{"content": _NOISY, "page": 1, "category": "text"}], text_cleanup="safe"
    )
    _assert_clean(vectors[0].text)


def test_recursive_path_drops_blank_chunks(tmp_path):
    """공백만 남는 element 는 벡터가 되지 않고, 남은 청크 인덱스도 연속이다."""
    proc = _make_processor(tmp_path, "safe")
    elements = [
        {"content": "첫 청크", "page": 1, "category": "text"},
        {"content": "​    ", "page": 2, "category": "text"},
        {"content": "셋째 청크", "page": 3, "category": "text"},
    ]
    vectors = proc._chunk_parse_format(elements)
    assert len(vectors) == 2
    assert [v.i_chunk_on_doc for v in vectors] == [0, 1]
    assert all(v.n_chunk_of_doc == 2 for v in vectors)


# ---------------------------------------------------------------------------
# row 경로
# ---------------------------------------------------------------------------

def test_row_path_normalizes_text_and_property(tmp_path):
    """행 metadata 는 청크 property 로 나가므로 text 와 같은 표현이어야 한다."""
    proc = _make_processor(tmp_path, "safe")
    vectors = proc._chunk_parse_format([{
        "content": _NOISY,
        "page": 1,
        "category": "custom_fields_row",
        "metadata": {"question": "﻿질​문Ａ"},
    }])
    assert len(vectors) == 1
    _assert_clean(vectors[0].text)
    _assert_stats_match(vectors[0])
    assert getattr(vectors[0], "question") == "질문A"


# ---------------------------------------------------------------------------
# marker 경로
# ---------------------------------------------------------------------------

def test_marker_path_audio_normalized_and_stats_fixed(tmp_path):
    """legacy 는 n_char/n_word/n_line 을 1 로 고정했다 — 실제 값이어야 한다."""
    proc = _make_processor(tmp_path, "safe")
    vectors = proc._chunk_parse_format([{"content": "[AUDIO] " + _NOISY, "page": 1}])
    assert len(vectors) == 1
    _assert_clean(vectors[0].text)
    _assert_stats_match(vectors[0])
    assert vectors[0].n_char > 1


def test_marker_path_tabular_normalized(tmp_path):
    proc = _make_processor(tmp_path, "safe")
    vectors = proc._chunk_parse_format([{"content": _NOISY, "page": 1, "category": "table"}])
    assert len(vectors) == 1
    assert vectors[0].text.startswith("[DA]")
    _assert_clean(vectors[0].text)
    _assert_stats_match(vectors[0])
