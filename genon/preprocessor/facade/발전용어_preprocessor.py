# 용어집(약어/원어/한글풀이) JSON 전용 전처리기
from __future__ import annotations

import json
from datetime import datetime

from fastapi import Request
from pydantic import BaseModel

_FIELDS = ("약 어", "원 어", "한 글 풀 이")


class GenOSVectorMeta(BaseModel):
    class Config:
        extra = 'allow'

    text: str | None = None
    n_char: int | None = None
    n_word: int | None = None
    n_line: int | None = None
    i_page: int | None = None
    e_page: int | None = None
    i_chunk_on_page: int | None = None
    n_chunk_of_page: int | None = None
    i_chunk_on_doc: int | None = None
    n_chunk_of_doc: int | None = None
    n_page: int | None = None
    reg_date: str | None = None
    chunk_bboxes: str | None = None
    media_files: str | None = None


def _load_entries(file_path: str) -> list[dict]:
    """{"약 어":..,"원 어":..,"한 글 풀 이":..} 단일 객체 또는 그 리스트를 읽는다."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise ValueError(f"지원하지 않는 JSON 최상위 타입: {type(data).__name__}")


def _entry_to_text(entry: dict) -> str:
    return "\n".join(f"{k}: {entry.get(k) or ''}" for k in _FIELDS)


class DocumentProcessor:
    def __init__(self, config_path: str | None = None):
        pass

    async def __call__(self, request: Request, file_path: str, **kwargs: dict) -> list[GenOSVectorMeta]:
        entries = _load_entries(file_path)
        n_chunk = len(entries)
        reg_date = datetime.now().isoformat(timespec='seconds') + 'Z'

        vectors: list[GenOSVectorMeta] = []
        for i, entry in enumerate(entries):
            text = _entry_to_text(entry)
            vectors.append(GenOSVectorMeta.model_validate({
                'text': text,
                'n_char': len(text),
                'n_word': len(text.split()),
                'n_line': len(text.splitlines()),
                'i_page': 1,
                'e_page': 1,
                'n_page': 1,
                'i_chunk_on_page': i,
                'n_chunk_of_page': n_chunk,
                'i_chunk_on_doc': i,
                'n_chunk_of_doc': n_chunk,
                'reg_date': reg_date,
                'chunk_bboxes': "[]",
                'media_files': "[]",
                **{k: entry.get(k) for k in _FIELDS},
            }))
        return vectors
