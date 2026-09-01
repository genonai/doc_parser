"""attachment_processor 의 output.compact_tables (markdown 표 패딩 제거) 단위 테스트.

attachment_processor 가 markdown 을 만드는 경로는 두 곳뿐이며 둘 다 검증한다:
  1) `_split_with_recursive_chunker` — 기본 `chunker_type: recursive`. 전체 문서.
  2) `HierarchicalChunker.chunk` — `chunker_type: hybrid` 전용. 구현은
     facade/chunking/hybrid_chunker.py 의 `HierarchicalDocChunker` 이고
     attachment_processor 는 별칭만 갖는다.
둘 다 `common/markdown_export.export_markdown` 을 거친다 - docling 의
`export_to_markdown()` 은 compact 도 링크 억제도 인자로 받지 않기 때문이다.
그리고 config(`output.compact_tables`) → `_default_kwargs` 배선을 확인한다.

내부 서버 요청 없음. attachment_processor import 가 불가한 환경에서는 모듈 단위로 skip 된다.
"""

import pytest

# 무거운 의존성(docling 등) 미설치 환경에서는 파일 전체 skip (GitHub CI 에서는 정상 import)
attachment = pytest.importorskip("facade.attachment_processor")

_split_with_recursive_chunker = attachment._split_with_recursive_chunker
HierarchicalChunker = attachment.HierarchicalChunker
DocumentProcessor = attachment.DocumentProcessor
_resolve_compact_tables = attachment._resolve_compact_tables


def _build_table_doc():
    """표 1개(3열 x 2행, 첫 행이 헤더)만 있는 최소 DoclingDocument."""
    from docling_core.types.doc import DoclingDocument, TableCell, TableData

    doc = DoclingDocument(name="compact_tables_test")
    rows = [["구분", "사장", "임원"], ["갑지", "$ 389", "$ 282"]]
    cells = []
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            cells.append(
                TableCell(
                    text=value,
                    start_row_offset_idx=r,
                    end_row_offset_idx=r + 1,
                    start_col_offset_idx=c,
                    end_col_offset_idx=c + 1,
                    column_header=(r == 0),
                )
            )
    doc.add_table(data=TableData(num_rows=len(rows), num_cols=3, table_cells=cells))
    return doc


def _separator_line(md: str) -> str:
    """markdown 표의 구분선(두 번째 줄) 반환."""
    lines = [ln for ln in md.splitlines() if ln.strip().startswith("|")]
    assert len(lines) >= 2, f"표 markdown 이 아님: {md!r}"
    return lines[1].strip()


@pytest.mark.unit
class TestRecursiveChunkerCompactTables:
    """기본 recursive 경로: DoclingDocument.export_to_markdown 에 인자가 전달되는지."""

    def test_default_is_compact(self):
        """인자 미지정 시 기본 compact(패딩 없음) — 구분선이 '| - |' 형태."""
        chunks = _split_with_recursive_chunker(_build_table_doc(), chunk_size=0)
        assert len(chunks) == 1
        assert _separator_line(chunks[0]["text"]) == "| - | - | - |"

    def test_compact_true_removes_padding(self):
        chunks = _split_with_recursive_chunker(
            _build_table_doc(), chunk_size=0, compact_tables=True
        )
        text = chunks[0]["text"]
        assert _separator_line(text) == "| - | - | - |"
        assert "|--------|" not in text

    def test_compact_false_keeps_padding(self):
        """off 스위치: 기존(패딩 있는) 형식이 그대로 나와야 한다."""
        chunks = _split_with_recursive_chunker(
            _build_table_doc(), chunk_size=0, compact_tables=False
        )
        text = chunks[0]["text"]
        assert "|--------|" in text
        assert _separator_line(text) != "| - | - | - |"

    def test_cell_contents_are_identical(self):
        """패딩만 사라지고 셀 내용은 동일해야 한다."""
        doc = _build_table_doc()
        compact = _split_with_recursive_chunker(doc, chunk_size=0, compact_tables=True)[0]["text"]
        padded = _split_with_recursive_chunker(doc, chunk_size=0, compact_tables=False)[0]["text"]

        def cells(md):
            out = []
            for i, line in enumerate([ln for ln in md.splitlines() if ln.strip().startswith("|")]):
                if i == 1:  # 구분선 제외
                    continue
                out.append([c.strip() for c in line.strip().strip("|").split("|")])
            return out

        assert cells(compact) == cells(padded)
        assert cells(compact) == [["구분", "사장", "임원"], ["갑지", "$ 389", "$ 282"]]

    def test_compact_is_shorter(self):
        doc = _build_table_doc()
        compact = _split_with_recursive_chunker(doc, chunk_size=0, compact_tables=True)[0]["text"]
        padded = _split_with_recursive_chunker(doc, chunk_size=0, compact_tables=False)[0]["text"]
        assert len(compact) < len(padded)


@pytest.mark.unit
class TestHierarchicalChunkerCompactTables:
    """hybrid 경로: TableItem 은 serializer 를 직접 구성하므로 분기 동작을 확인."""

    def test_default_is_compact(self):
        doc = _build_table_doc()
        chunks = list(HierarchicalChunker().chunk(dl_doc=doc))
        assert len(chunks) == 1
        assert _separator_line(chunks[0].text) == "| - | - | - |"

    def test_compact_false_keeps_padding(self):
        doc = _build_table_doc()
        chunks = list(HierarchicalChunker().chunk(dl_doc=doc, compact_tables=False))
        assert "|--------|" in chunks[0].text

    def test_never_calls_table_item_export_to_markdown(self, monkeypatch):
        """두 모드 모두 공용 관문(`common/markdown_export`)을 거친다.

        `TableItem.export_to_markdown()` 은 params 를 받지 않아 링크 URL 을 억제할 수 없다.
        어느 분기에서도 그 경로로 새지 않아야 한다.
        """
        from docling_core.types.doc.document import TableItem

        calls = []
        original = TableItem.export_to_markdown
        monkeypatch.setattr(
            TableItem, "export_to_markdown",
            lambda self, *a, **kw: (calls.append(1), original(self, *a, **kw))[1],
        )

        for compact in (True, False):
            chunks = list(HierarchicalChunker().chunk(
                dl_doc=_build_table_doc(), compact_tables=compact))
            assert len(chunks) == 1
            assert "구분" in chunks[0].text
        assert calls == []


@pytest.mark.unit
class TestConfigWiring:
    """output.compact_tables → _default_kwargs 배선."""

    @staticmethod
    def _processor_with(tmp_path, body: str):
        cfg = tmp_path / "attachment_processor_config.yaml"
        cfg.write_text(body, encoding="utf-8")
        return DocumentProcessor(config_path=str(cfg))

    def test_default_true_when_output_section_absent(self, tmp_path):
        """output: 섹션이 아예 없는 구버전 config 도 기본 True."""
        dp = self._processor_with(tmp_path, "defaults:\n  log_level: 4\n")
        assert dp._default_kwargs["compact_tables"] is True

    def test_explicit_true(self, tmp_path):
        dp = self._processor_with(tmp_path, "output:\n  compact_tables: true\n")
        assert dp._default_kwargs["compact_tables"] is True

    def test_explicit_false(self, tmp_path):
        dp = self._processor_with(tmp_path, "output:\n  compact_tables: false\n")
        assert dp._default_kwargs["compact_tables"] is False

    def test_invalid_value_falls_back_to_true(self, tmp_path):
        dp = self._processor_with(tmp_path, "output:\n  compact_tables: bogus\n")
        assert dp._default_kwargs["compact_tables"] is True

    def test_runtime_kwarg_overrides_config(self, tmp_path):
        """_merge_runtime_kwargs 는 None 이 아닌 런타임 값만 덮어쓴다 (False 포함)."""
        dp = self._processor_with(tmp_path, "output:\n  compact_tables: true\n")
        assert dp._merge_runtime_kwargs({"compact_tables": False})["compact_tables"] is False
        assert dp._merge_runtime_kwargs({"compact_tables": None})["compact_tables"] is True
        assert dp._merge_runtime_kwargs({})["compact_tables"] is True

    def test_merge_passes_runtime_value_through_unvalidated(self, tmp_path):
        """_merge_runtime_kwargs 는 타입 검증을 하지 않는다 — 검증은 소비 지점(_resolve_compact_tables) 책임.

        이 성질 때문에 소비 지점에서 bool() 을 쓰면 문자열 "false" 가 True 가 된다.
        """
        dp = self._processor_with(tmp_path, "output:\n  compact_tables: true\n")
        assert dp._merge_runtime_kwargs({"compact_tables": "false"})["compact_tables"] == "false"


@pytest.mark.unit
class TestRuntimeValueParsing:
    """런타임 kwarg 는 검증 없이 전달되므로 문자열/정수도 올바르게 해석돼야 한다.

    문서(gitbook)가 `compact_tables=false` 를 off 스위치로 안내하는데,
    bool("false") 는 True 라서 파싱 없이는 off 가 조용히 무시된다.
    """

    @pytest.mark.parametrize("value", [False, "false", "False", " off ", "0", "no", "n", 0])
    def test_falsy_runtime_values_disable_compact(self, value):
        assert _resolve_compact_tables({"compact_tables": value}) is False

    @pytest.mark.parametrize("value", [True, "true", "TRUE", "on", "1", "yes", "y", 1])
    def test_truthy_runtime_values_enable_compact(self, value):
        assert _resolve_compact_tables({"compact_tables": value}) is True

    @pytest.mark.parametrize("value", ["bogus", "", "  ", [], {}, object()])
    def test_invalid_runtime_values_fall_back_to_true(self, value):
        assert _resolve_compact_tables({"compact_tables": value}) is True

    def test_missing_and_none_fall_back_to_true(self):
        assert _resolve_compact_tables({}) is True
        assert _resolve_compact_tables({"compact_tables": None}) is True

    def test_invalid_value_warns_once_per_call(self, caplog):
        """표 개수와 무관하게 경고는 해석 시점에 한 번만 찍힌다."""
        import logging

        with caplog.at_level(logging.WARNING):
            _resolve_compact_tables({"compact_tables": "bogus"})
        assert sum(1 for r in caplog.records if "compact_tables" in r.getMessage()) == 1


@pytest.mark.unit
class TestStringRuntimeValueReachesOutput:
    """소비 지점 검증: 문자열 런타임 값이 실제 markdown 출력까지 반영되는지."""

    def test_string_false_keeps_padding_in_hybrid_path(self):
        """HierarchicalChunker.chunk 는 런타임 kwargs 를 그대로 받는다(L1475/1681 경로)."""
        chunks = list(HierarchicalChunker().chunk(dl_doc=_build_table_doc(), compact_tables="false"))
        assert "|--------|" in chunks[0].text
        assert _separator_line(chunks[0].text) != "| - | - | - |"

    def test_string_true_removes_padding_in_hybrid_path(self):
        chunks = list(HierarchicalChunker().chunk(dl_doc=_build_table_doc(), compact_tables="true"))
        assert _separator_line(chunks[0].text) == "| - | - | - |"

    def test_invalid_string_stays_compact_in_hybrid_path(self):
        chunks = list(HierarchicalChunker().chunk(dl_doc=_build_table_doc(), compact_tables="bogus"))
        assert _separator_line(chunks[0].text) == "| - | - | - |"

    def test_recursive_path_receives_parsed_bool(self):
        """DocxProcessor/HwpProcessor 가 넘기는 값과 동일하게 파싱된 bool 로 분기되는지."""
        doc = _build_table_doc()
        off = _split_with_recursive_chunker(
            doc, chunk_size=0, compact_tables=_resolve_compact_tables({"compact_tables": "false"})
        )[0]["text"]
        on = _split_with_recursive_chunker(
            doc, chunk_size=0, compact_tables=_resolve_compact_tables({"compact_tables": "true"})
        )[0]["text"]
        assert "|--------|" in off
        assert _separator_line(on) == "| - | - | - |"


@pytest.mark.unit
class TestShippedConfigs:
    def test_shipped_configs_enable_compact(self):
        """배포 config 3종(resource/resource_dev/resource_product) 모두 compact 가 켜져 있다."""
        from pathlib import Path
        import yaml

        preproc = Path(attachment.__file__).resolve().parents[1]
        for name in ("resource", "resource_dev", "resource_product"):
            path = preproc / name / "attachment_processor_config.yaml"
            if not path.exists():
                continue
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            assert cfg.get("output", {}).get("compact_tables") is True, f"{path} 에 output.compact_tables: true 필요"
