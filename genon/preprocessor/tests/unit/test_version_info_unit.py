"""/version 응답의 수기 갱신 시각(UPDATED_AT) 노출을 고정한다."""

import json

import pytest

from common import version_info

pytestmark = pytest.mark.unit


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'VERSION').write_text(json.dumps({
        'source_version': '2.2.5', 'source_commit': 'abc', 'source_commit_date': '2026-09-01T00:00:00+09:00',
    }), encoding='utf-8')
    version_info._resolve.cache_clear()
    yield tmp_path
    version_info._resolve.cache_clear()


def test_manual_absent_is_none(root):
    info = version_info.get_version_info(root)
    assert info['manual_updated_at'] is None
    assert info['version'] == '2.2.5'


def test_manual_first_line_trimmed(root):
    (root / 'UPDATED_AT').write_text('﻿  2026-09-16 14:30 핫픽스  \n둘째 줄\n', encoding='utf-8')
    assert version_info.get_version_info(root)['manual_updated_at'] == '2026-09-16 14:30 핫픽스'


def test_manual_empty_is_none(root):
    (root / 'UPDATED_AT').write_text('\n', encoding='utf-8')
    assert version_info.get_version_info(root)['manual_updated_at'] is None


def test_manual_is_read_per_request_and_capped(root):
    version_info.get_version_info(root)
    (root / 'UPDATED_AT').write_text('x' * 500, encoding='utf-8')
    got = version_info.get_version_info(root)
    assert got['manual_updated_at'] == 'x' * version_info._MANUAL_MAX_LEN
    # 수기 파일은 릴리스 스탬프와 독립이다.
    assert got['updated_at'] == '2026-09-01T00:00:00+09:00'
