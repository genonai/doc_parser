import logging
from pathlib import Path
from typing import Any

from .base_loader import BaseLoader

_log = logging.getLogger(__name__)

try:
    import chardet
except ImportError:
    chardet = None

try:
    import pandas as pd
    from langchain_community.document_loaders import DataFrameLoader
except ImportError:
    pd = None
    DataFrameLoader = None

SUPPORTED_EXTENSIONS = {".csv", ".xlsx"}


class TabularLoader(BaseLoader):
    """
    CSV/XLSX 파일을 로드하여 data_dict 구조로 반환.

    반환 형식:
        {
            "data": [
                {
                    "sheet_name": str,
                    "data_rows": [{"col": val, ...}, ...],
                    "data_types": [[col_name, sql_dtype], ...]
                },
                ...  # XLSX 멀티시트 시 여러 항목
            ]
        }
    """

    def __init__(self, config: dict) -> None:
        self.config = config

    def load(self, file_path: str) -> dict:
        assert pd is not None, "TabularLoader requires pandas and langchain-community: pip install pandas langchain-community openpyxl"
        assert chardet is not None, "TabularLoader requires chardet: pip install chardet"

        ext = Path(file_path).suffix.lower()
        assert ext in SUPPORTED_EXTENSIONS, f"TabularLoader가 지원하지 않는 확장자: {ext}"

        if ext == ".csv":
            return self._load_csv(file_path)
        else:
            return self._load_xlsx(file_path)

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
            elif "date" in dtype:
                sql_dtype = "DATE"
                df[col] = df[col].astype(str)
            elif "datetime" in dtype:
                sql_dtype = "DATETIME"
                df[col] = df[col].astype(str)
            else:
                max_len_val = df[col].astype(str).str.len().max()
                max_len = int(0 if pd.isna(max_len_val) else max_len_val) + 10
                sql_dtype = f"VARCHAR({max_len})"
            res.append([col, sql_dtype])
        return df, res

    def _process_data_rows(self, sheet_name: str, col: str, col_type: str, documents: list, dtypes: list) -> dict:
        rows = []
        for doc in documents:
            row = {}
            if "int" in col_type:
                row[col] = int(doc.page_content)
            elif "float" in col_type:
                row[col] = float(doc.page_content)
            elif "bool" in col_type:
                if doc.page_content.lower() == "true":
                    row[col] = True
                elif doc.page_content.lower() == "false":
                    row[col] = False
                else:
                    raise ValueError(f"Invalid boolean string: {doc.page_content}")
            else:
                row[col] = doc.page_content
            row.update(doc.metadata)
            rows.append(row)
        return {"sheet_name": sheet_name, "data_rows": rows, "data_types": dtypes}

    def _load_csv(self, file_path: str) -> dict:
        with open(file_path, "rb") as f:
            raw = f.read(10000)
        enc = chardet.detect(raw)["encoding"] or "utf-8"

        df = pd.read_csv(file_path, encoding=enc, index_col=False)
        df = df.fillna("null")
        df, dtypes = self._check_sql_dtypes(df)

        col = df.columns[0]
        col_type = str(df[col].dtype)
        df = df.astype({col: "str"})

        documents = DataFrameLoader(df, page_content_column=col).load()
        sheet = self._process_data_rows("table_1", col, col_type, documents, dtypes)
        return {"data": [sheet]}

    def _load_xlsx(self, file_path: str) -> dict:
        dfs = pd.read_excel(file_path, sheet_name=None)
        sheets = []
        for sheet_name, df in dfs.items():
            df = df.fillna("null")
            df, dtypes = self._check_sql_dtypes(df)

            col = df.columns[0]
            col_type = str(type(col))
            df = df.astype({col: "str"})

            documents = DataFrameLoader(df, page_content_column=col).load()
            sheet = self._process_data_rows(sheet_name, col, col_type, documents, dtypes)
            sheets.append(sheet)

        return {"data": sheets}
