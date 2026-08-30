"""parser / attachment 가 공유하는 입력 로더.

두 facade 가 같은 구현을 복제해 두었던 TextLoader / TabularLoader / AudioLoader 의
단일 사본이다. 정본은 attachment 사본으로, 두 사본이 갈린 지점에서 더 안전한 쪽이다
(본문 HTML 이스케이프, 인코딩 감지 샘플 크기 설정화, 음수 overlap 방어).

벡터 스키마에 의존하는 return_vectormeta_format 은 여기 두지 않는다. GenOSVectorMeta
가 facade 마다 다르므로 파생 클래스가 갖는다.
"""

from __future__ import annotations

import html
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
import uuid
from glob import glob

import pandas as pd
import pydub
import requests
from langchain_community.document_loaders import DataFrameLoader, PyMuPDFLoader
from langchain_core.documents import Document

from genon.preprocessor.facade.common.file_probe import get_pdf_path

try:
    import chardet
except ImportError:  # 로더를 쓰지 않는 배포본도 있으므로 import 자체는 막지 않는다
    chardet = None

try:
    from weasyprint import HTML
except (ImportError, OSError):
    # OSError: weasyprint 는 설치돼 있으나 네이티브 라이브러리(libgobject-2.0 등)가 없는 환경.
    HTML = None

_log = logging.getLogger(__name__)

# PDF 로 변환해 처리하는 입력 확장자. 로더가 출력 PDF 경로를 만들 때 쓴다.
CONVERTIBLE_EXTENSIONS = ['.hwp', '.txt', '.json', '.md', '.ppt', '.pptx', '.docx']


def install_packages(packages):
    """import 되지 않는 패키지를 런타임에 설치 시도한다(기존 동작 유지)."""
    for package in packages:
        try:
            __import__(package)
        except ImportError:
            _log.warning(f"{package} 패키지가 없습니다. 설치를 시도합니다.")
            subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)


class TextLoaderBase:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.output_dir = os.path.join('/tmp', str(uuid.uuid4()))
        os.makedirs(self.output_dir, exist_ok=True)

    def load(self):
        try:
            with open(self.file_path, 'rb') as f:
                raw = f.read()
            enc = chardet.detect(raw).get('encoding') or ''
            encodings = [enc] if enc and enc.lower() not in ('ascii', 'unknown') else []
            encodings += ['utf-8', 'cp949', 'euc-kr', 'iso-8859-1', 'latin-1']

            content = None
            for e in encodings:
                try:
                    content = raw.decode(e)  # 전체 파일로 디코딩
                    break
                except UnicodeDecodeError:
                    continue
            if content is None:
                content = raw.decode('utf-8', errors='replace')

            # 4) PDF 변환 유지
            # <pre> 기본값(white-space: pre)은 자동 줄바꿈을 하지 않아, A4 폭을 넘는 긴 줄이
            # weasyprint 렌더 단계에서 잘려(discard) PDF·청킹에서 누락됨(이슈 #333).
            #  - white-space: pre-wrap  → 원문 줄바꿈/공백 유지 + 폭 초과 시 자동 줄바꿈
            #  - overflow-wrap: anywhere → 공백 없는 초장문(URL 등)도 강제 개행
            #  - html.escape           → <, & 등이 태그로 해석돼 뒤 텍스트가 유실되는 것 방지
            html_doc = (
                "<html><meta charset='utf-8'><body>"
                "<pre style='white-space: pre-wrap; overflow-wrap: anywhere;'>"
                f"{html.escape(content)}</pre></body></html>"
            )
            html_path = os.path.join(self.output_dir, 'temp.html')
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(html_doc)
            # pdf_path = (self.file_path
            #             .replace('.txt', '.pdf')
            #             .replace('.json', '.pdf'))
            pdf_path = get_pdf_path(self.file_path, CONVERTIBLE_EXTENSIONS)
            if HTML:
                HTML(html_path).write_pdf(pdf_path)
                loader = PyMuPDFLoader(pdf_path)
                return loader.load()
            # PDF가 불가하면 Document 직접 반환 (원형 스키마 유지)
            return [Document(page_content=content, metadata={'source': self.file_path, 'page': 0})]

        except Exception:
            # 실패 시에도 스키마는 그대로 유지해 반환
            for e in ['utf-8', 'cp949', 'euc-kr', 'iso-8859-1']:
                try:
                    with open(self.file_path, 'r', encoding=e) as f:
                        content = f.read()
                    return [Document(page_content=content, metadata={'source': self.file_path, 'page': 0})]
                except UnicodeDecodeError:
                    continue
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            return [Document(page_content=content, metadata={'source': self.file_path, 'page': 0})]
        finally:
            if os.path.exists(self.output_dir):
                shutil.rmtree(self.output_dir)


class TabularLoaderBase:
    def __init__(self, file_path: str, ext: str, encoding_detect_sample_bytes: int = 10000):
        packages = ['openpyxl', 'chardet']
        install_packages(packages)

        self.file_path = file_path
        self.encoding_detect_sample_bytes = max(int(encoding_detect_sample_bytes), 1)
        if ext == ".csv":
            # convert_to_pdf(file_path) csv는 Pdf 변환 안 함
            self.data_dict = self.load_csv_documents(file_path)
        elif ext == ".xlsx":
            # convert_to_pdf(file_path) xlsx는 Pdf 변환 안 함
            self.data_dict = self.load_xlsx_documents(file_path)
        else:
            _log.warning(f"Inadequate extension for TabularLoader: {ext}")
            return

    def check_sql_dtypes(self, df):
        df = df.convert_dtypes()
        res = []
        for col in df.columns:
            # col_name = col.strip().replace(' ', '_')
            dtype = str(df.dtypes[col]).lower()

            if 'int' in dtype:
                if '64' in dtype:
                    sql_dtype = 'BIGINT'
                else:
                    sql_dtype = 'INT'
            elif 'float' in dtype:
                sql_dtype = 'FLOAT'
            elif 'bool' in dtype:
                sql_dtype = 'BOOLEAN'
            elif 'date' in dtype:
                sql_dtype = 'DATE'
                df[col] = df[col].astype(str)
            elif 'datetime' in dtype:
                sql_dtype = 'DATETIME'
                df[col] = df[col].astype(str)
            # else:
            #     max_len = df[col].str.len().max().item() + 10
            #     sql_dtype = f'VARCHAR({max_len})'
            else:
                lens = df[col].astype(str).str.len()
                max_len_val = lens.max()
                max_len = int(0 if pd.isna(max_len_val) else max_len_val) + 10
                sql_dtype = f'VARCHAR({max_len})'

            res.append([col, sql_dtype])

        return df, res

    def process_data_rows(self, data: dict):
        """Arg: data (keys: 'sheet_name', 'page_column', 'page_column_type', 'documents')"""

        rows = []
        for doc in data["documents"]:
            row = {}
            if 'int' in data["page_column_type"]:
                row[data["page_column"]] = int(doc.page_content)
            elif 'float' in data["page_column_type"]:
                row[data["page_column"]] = float(doc.page_content)
            elif 'bool' in data["page_column_type"]:
                if doc.page_content.lower() == 'true':
                    row[data["page_column"]] = True
                elif doc.page_content.lower() == 'false':
                    row[data["page_column"]] = False
                else:
                    raise ValueError(f"Invalid boolean string: {doc.page_content}")
            else:
                row[data["page_column"]] = doc.page_content

            row.update(doc.metadata)
            rows.append(row)

        processed_data = {"sheet_name": data["sheet_name"], "data_rows": rows, "data_types": data["dtypes"]}
        return processed_data

    def load_csv_documents(self, file_path: str, **kwargs: dict):
        import chardet

        with open(file_path, "rb") as f:
            raw_file = f.read(self.encoding_detect_sample_bytes)
        enc_type = chardet.detect(raw_file)['encoding']
        df = pd.read_csv(file_path, encoding=enc_type, index_col=False)
        df = df.fillna('null')  # csv 파일에서도 xlsx 파일과 동일하게 null로 채움
        df, dtypes_str = self.check_sql_dtypes(df)

        for i in range(len(df.columns)):
            try:
                col = df.columns[0]
                # col_type = str(type(col))
                col_type = str(df[col].dtype)
                df = df.astype({col: 'str'})
                break
            except:
                raise ValueError(
                    f"Any columns cannot be converted into the string type so that can't load LangChain Documents: {dtypes_str}")

        loader = DataFrameLoader(df, page_content_column=col)
        documents = loader.load()

        data = {
            "sheet_name": "table_1",
            "page_column": col,
            "page_column_type": col_type,
            "documents": documents,
            "dtypes": dtypes_str
        }
        data = self.process_data_rows(data)  # including only one sheet as it's a csv file
        data_dict = {"data": [data]}
        return data_dict

    def load_xlsx_documents(self, file_path: str, **kwargs: dict):
        dfs = pd.read_excel(file_path, sheet_name=None)
        sheets = []
        for sheet_name, df in dfs.items():
            df = df.fillna('null')
            df, dtypes_str = self.check_sql_dtypes(df)

            for i in range(len(df.columns)):
                try:
                    col = df.columns[0]
                    col_type = str(type(col))
                    df = df.astype({col: 'str'})
                    break
                except:
                    raise ValueError(
                        f"Any columns cannot be converted into string type so that can't load LangChain Documents: {dtypes_str}")

            loader = DataFrameLoader(df, page_content_column=col)
            documents = loader.load()

            sheet = {
                "sheet_name": sheet_name,
                "page_column": col,
                "page_column_type": col_type,
                "documents": documents,
                "dtypes": dtypes_str
            }
            sheets.append(sheet)

        data_dict = {"data": []}
        for sheet in sheets:
            data = self.process_data_rows(sheet)
            data_dict["data"].append(data)

        return data_dict


class AudioLoaderBase:
    def __init__(self,
                 file_path: str,
                 req_url: str,
                 req_data: dict,
                 chunk_sec: int = 29,
                 chunk_overlap_ms: int = 300,
                 tmp_path: str = '.',
                 ):
        self.file_path = file_path
        self.tmp_path = tmp_path
        self.chunk_sec = chunk_sec
        self.chunk_overlap_ms = max(int(chunk_overlap_ms), 0)
        self.req_url = req_url
        self.req_data = req_data

    def split_file_as_chunks(self) -> list:
        audio = pydub.AudioSegment.from_file(self.file_path)
        chunk_len = self.chunk_sec * 1000
        n_chunks = math.ceil(len(audio) / chunk_len)

        for i in range(n_chunks):
            start_ms = i * chunk_len
            overlap_start_ms = start_ms - self.chunk_overlap_ms if start_ms > 0 else start_ms
            end_ms = start_ms + chunk_len
            audio_chunk = audio[overlap_start_ms:end_ms]
            audio_chunk.export(os.path.join(self.tmp_path, "tmp_{}.wav".format(str(i))), format="wav")
        tmp_files = glob(os.path.join(self.tmp_path, "*.wav"))
        return tmp_files

    def transcribe_audio(self, file_path_lst: list):
        transcribed_text_chunks = []

        def _send_request(filepath: str):
            """Send a request to 'whisper' model served"""
            files = {
                'file': (filepath, open(filepath, 'rb'), 'audio/mp3'),
            }

            response = requests.post(self.req_url, data=self.req_data, files=files)
            text = response.json().get('text', ', ')
            transcribed_text_chunks.append({
                'file_name': os.path.basename(filepath),
                'text': text
            })

        # Send parallel requests
        threads = [threading.Thread(target=_send_request, args=(f,)) for f in file_path_lst]
        for t in threads: t.start()
        for t in threads: t.join()

        # Merge transcribed text snippets in order
        transcribed_text_chunks.sort(key=lambda x: x['file_name'])
        transcribed_text = "[AUDIO]" + ' '.join([t['text'] for t in transcribed_text_chunks])
        return transcribed_text

