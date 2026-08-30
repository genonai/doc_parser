"""청킹 processor들이 공유하는 구조 보존 유틸리티."""

from .table_shape import (
    TableShape,
    analyze_grid,
    flatten_header_rows,
    normalize_row_spans,
    resolve_table_format,
    serialize_rows,
)
from .table_splitter import (
    TableSplitResult,
    leading_header_row_count,
    split_entries_preserving_tables,
    split_table_rows,
)
from . import text_norm

__all__ = [
    "TableShape",
    "TableSplitResult",
    "analyze_grid",
    "flatten_header_rows",
    "leading_header_row_count",
    "normalize_row_spans",
    "resolve_table_format",
    "serialize_rows",
    "split_entries_preserving_tables",
    "split_table_rows",
    "text_norm",
]
