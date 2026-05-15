import logging
from pathlib import Path

from docling_core.types import DoclingDocument
from docling_core.types.doc import DocItemLabel
from docling_core.types.doc.document import MiscAnnotation, TableCell, TableData

from .base_loader import BaseLoader

_log = logging.getLogger(__name__)

try:
    import chardet
except ImportError:
    chardet = None

try:
    import pandas as pd
except ImportError:
    pd = None

SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


class TabularLoader(BaseLoader):
    """
    CSV/XLSX 파일을 로드하여 DoclingDocument로 반환.

    시트당 TableItem 하나 생성.
    - caption: 시트명
    - annotation(MiscAnnotation): SQL dtype 정보 JSON
    """

    def __init__(self, config: dict) -> None:
        self.config = config

    def load(self, file_path: str) -> DoclingDocument:
        assert pd is not None, "TabularLoader requires pandas: pip install pandas openpyxl"
        assert chardet is not None, "TabularLoader requires chardet: pip install chardet"

        ext = Path(file_path).suffix.lower()
        assert ext in SUPPORTED_EXTENSIONS, f"TabularLoader가 지원하지 않는 확장자: {ext}"

        doc = DoclingDocument(name=Path(file_path).name)
        doc.add_text(label=DocItemLabel.PARAGRAPH, text="[DA]")

        if ext == ".csv":
            self._add_csv(doc, file_path)
        else:
            self._add_xlsx(doc, file_path)

        return doc

    def _check_sql_dtypes(self, df) -> tuple:
        df = df.convert_dtypes()
        res = []
        for col in df.columns:
            dtype = str(df.dtypes[col]).lower()
            if "int" in dtype:
                sql_dtype = "BIGINT" if "64" in dtype else "INT"
            elif "float" in dtype:
                sql_dtype = "FLOAT"
            elif "bool" in dtype:
                sql_dtype = "BOOLEAN"
            elif "datetime" in dtype:
                sql_dtype = "DATETIME"
                df[col] = df[col].astype(str)
            elif "date" in dtype:
                sql_dtype = "DATE"
                df[col] = df[col].astype(str)
            else:
                max_len_val = df[col].astype(str).str.len().max()
                max_len = int(0 if pd.isna(max_len_val) else max_len_val) + 10
                sql_dtype = f"VARCHAR({max_len})"
            res.append([col, sql_dtype])
        return df, res

    def _df_to_table_data(self, df) -> TableData:
        cols = list(df.columns)
        num_cols = len(cols)
        cells = []

        for c, col in enumerate(cols):
            cells.append(TableCell(
                row_span=1, col_span=1,
                start_row_offset_idx=0, end_row_offset_idx=1,
                start_col_offset_idx=c, end_col_offset_idx=c + 1,
                text=str(col), column_header=True,
            ))

        for r, (_, row) in enumerate(df.iterrows(), start=1):
            for c, col in enumerate(cols):
                cells.append(TableCell(
                    row_span=1, col_span=1,
                    start_row_offset_idx=r, end_row_offset_idx=r + 1,
                    start_col_offset_idx=c, end_col_offset_idx=c + 1,
                    text=str(row[col]),
                ))

        return TableData(table_cells=cells, num_rows=len(df) + 1, num_cols=num_cols)

    def _add_sheet(self, doc: DoclingDocument, sheet_name: str, df, dtypes: list) -> None:
        table_data = self._df_to_table_data(df)
        annotations = [
            MiscAnnotation(content={"sheet_name": sheet_name, "data_types": dtypes})
        ]
        doc.add_table(data=table_data, annotations=annotations)

    def _add_csv(self, doc: DoclingDocument, file_path: str) -> None:
        with open(file_path, "rb") as f:
            raw = f.read(10000)
        enc = chardet.detect(raw)["encoding"] or "utf-8"

        df = pd.read_csv(file_path, encoding=enc, index_col=False)
        df = df.fillna("null")
        df, dtypes = self._check_sql_dtypes(df)
        self._add_sheet(doc, "table_1", df, dtypes)

    def _add_xlsx(self, doc: DoclingDocument, file_path: str) -> None:
        dfs = pd.read_excel(file_path, sheet_name=None)
        for sheet_name, df in dfs.items():
            df = df.fillna("null")
            df, dtypes = self._check_sql_dtypes(df)
            self._add_sheet(doc, sheet_name, df, dtypes)
