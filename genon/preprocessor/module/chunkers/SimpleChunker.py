from docling_core.types import DoclingDocument
from docling_core.types.doc import DocItem, TextItem, SectionHeaderItem, CodeItem, TableItem, PictureItem
from docling_core.types.doc.document import LevelNumber, ContentLayer, ListItem
from docling_core.transforms.chunker import BaseChunker, BaseChunk, DocChunk, DocMeta
from docling_core.types.doc.labels import DocItemLabel


class SimpleChunker(BaseChunker):
    """
    청크 길이가 kwargs['chunk_size']에 절대로 넘지 않게 청킹
    """

    chunk_size: int = 1000

    def chunk(self, dl_doc: DoclingDocument, **kwargs: dict):
        if "chunk_size" not in kwargs:
            print(f"@@@@ 기본 chunk_size를 사용합니다: {self.chunk_size}")

        chunk_size = kwargs.get("chunk_size", self.chunk_size)

        # 모든 아이템 수집
        all_items: list[DocItem] = []
        for item, _ in dl_doc.iterate_items(included_content_layers={ContentLayer.BODY, ContentLayer.FURNITURE}):
            if isinstance(item, (TextItem, ListItem, CodeItem, SectionHeaderItem, TableItem, PictureItem)):
                all_items.append(item)

        if not all_items:
            return iter([])

        def get_text(item: DocItem) -> str:
            if isinstance(item, TableItem):
                try:
                    return item.export_to_markdown(dl_doc) or ""
                except Exception:
                    return getattr(item, "text", "") or ""
            return getattr(item, "text", "") or ""

        def make_chunk(items, text=None):
            return DocChunk(
                text=text if text is not None else "\n".join(get_text(it) for it in items),
                meta=DocMeta(
                    doc_items=items,
                    headings=None,
                    captions=None,
                    origin=dl_doc.origin,
                ),
            )

        chunks: list[DocChunk] = []
        current_items: list[DocItem] = []
        current_len = 0

        for item in all_items:
            item_text = get_text(item)
            item_len = len(item_text)

            # 현재 청크가 비어있지 않고 추가하면 chunk_size 초과 시 저장
            if current_items and current_len + item_len + 1 > chunk_size:
                chunks.append(make_chunk(current_items))
                current_items = []
                current_len = 0

            # 단일 아이템이 chunk_size 초과 시 텍스트 분할
            if item_len > chunk_size:
                for start in range(0, item_len, chunk_size):
                    chunks.append(make_chunk([item], item_text[start : start + chunk_size]))
                continue

            current_items.append(item)
            current_len += item_len + (1 if current_len else 0)

        # 마지막 청크 처리
        if current_items:
            chunks.append(
                DocChunk(
                    text="\n".join(get_text(it) for it in current_items),
                    meta=DocMeta(
                        doc_items=current_items,
                        headings=None,
                        captions=None,
                        origin=dl_doc.origin,
                    ),
                )
            )

        return iter(chunks)
