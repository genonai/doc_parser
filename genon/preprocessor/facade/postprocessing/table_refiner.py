"""
TableRefiner: PDF 표 영역 이미지 크롭 → VLM → HTML 복원 → chunk.text 치환.

VT5.3.py 의 TableRefiner 를 DocChunk 기반으로 포팅.
핵심 흐름: refine_chunks() → refine_table() → cropping() + _refine_table()
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections import Counter
from typing import Any

import fitz
import httpx
from docling_core.transforms.chunker import DocChunk
from docling_core.types import DoclingDocument
from docling_core.types.doc import TableItem

_log = logging.getLogger(__name__)


class TableRefiner:
    """
    config 예시 (parsing_config.yaml):
        table_refiner:
          serving_id: "729"
          bearer_token: ""     # 없으면 GENOS_BEARER_TOKEN 환경변수 사용
          batch_size: 5
    """

    def __init__(self, serving_id: str = "", genos_url: str = "", bearer_token: str = "", batch_size: int = 5):
        self._url = f"{genos_url}/api/gateway/rep/serving/{serving_id}/v1/chat/completions"
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if bearer_token:
            self._headers["Authorization"] = f"Bearer {bearer_token}"
        self._batch_size = batch_size
        self._pdf: fitz.Document | None = None

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------

    def _debug(self, message: str, *args, level: str = "info"):
        rendered = message % args if args else message
        print(f"[table_refine] {rendered}", flush=True)
        getattr(_log, level, _log.info)("[table_refine] " + message, *args)

    def load_pdf(self, file_path: str):
        self._pdf = fitz.open(file_path)
        self._debug("loaded pdf path=%s pages=%s", file_path, getattr(self._pdf, "page_count", "?"))

    # ------------------------------------------------------------------
    # 크롭 (VT5 cropping 그대로)
    # ------------------------------------------------------------------

    def cropping(self, bbox: dict, i_page: int, clip_margin: float = 0.05, zoom: float = 2.0) -> str:
        """정규화된 bbox dict (l/t/r/b 0~1, coord_origin) → PNG base64 반환."""
        page = self._pdf.load_page(i_page)
        page_rect = page.rect

        l, t, r, b = float(bbox["l"]), float(bbox["t"]), float(bbox["r"]), float(bbox["b"])
        coord_origin = bbox.get("coord_origin", "BOTTOMLEFT")

        if coord_origin.upper() == "BOTTOMLEFT":
            t, b = 1.0 - t, 1.0 - b

        top, bottom = min(t, b), max(t, b)
        left, right = min(l, r), max(l, r)

        if clip_margin > 0:
            left  -= (right - left)   * clip_margin
            right += (right - left)   * clip_margin
            top   -= (bottom - top)   * clip_margin
            bottom += (bottom - top)  * clip_margin

        left, right = max(0.0, min(1.0, left)), max(0.0, min(1.0, right))
        top, bottom = max(0.0, min(1.0, top)),  max(0.0, min(1.0, bottom))

        abs_x0 = page_rect.x0 + left   * page_rect.width
        abs_y0 = page_rect.y0 + top    * page_rect.height
        abs_x1 = page_rect.x0 + right  * page_rect.width
        abs_y1 = page_rect.y0 + bottom * page_rect.height

        rect = fitz.Rect(abs_x0, abs_y0, abs_x1, abs_y1) & page_rect
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
        return base64.b64encode(pix.tobytes(output="png")).decode("ascii")

    # ------------------------------------------------------------------
    # VLM 호출 (VT5 _refine_table 그대로)
    # ------------------------------------------------------------------

    async def _refine_table(self, b64: str, original_table_text: str = None) -> str | None:
        system_prompt = """
당신은 'HTML 테이블 복원 전문가(Table Reconstruction Expert)'입니다.
입력으로 제공되는 테이블 이미지를 검색/RAG에 적합한 **읽기 쉬운 HTML fragment**로 변환하는 것이 역할입니다.

### 출력 형식 (절대 규칙)
1. 결과는 반드시 HTML fragment여야 합니다. Markdown table, 코드블록, ```html fence, 설명 문장은 절대 출력하지 않습니다.
2. 사용할 수 있는 태그는 `<p>`, `<h4>`, `<table>`, `<thead>`, `<tbody>`, `<tr>`, `<th>`, `<td>`, `<br>`입니다. 필요한 경우 `<th>`/`<td>`에는 `rowspan`, `colspan` 속성을 사용할 수 있습니다.
3. 표 데이터는 반드시 `<table>`로 출력합니다.
4. 셀 안 줄바꿈은 `<br>`로 표현합니다.
5. 셀 텍스트에 원본 파이프 기호(`|`)가 있으면 HTML에서는 그대로 두지 말고 가운뎃점(`·`)으로 치환합니다.

### 구조 복원 규칙
1. 표가 단순하면 하나의 HTML `<table>`로 출력합니다.
2. 표 안에 표가 들어간 중첩 표, 다단 헤더, 큰 rowspan/colspan, 페이지 이어짐 표라면 **하나의 큰 격자로 억지 변환하지 말고**, 문맥 `<p>` + 여러 개의 작은 `<table>`로 분해합니다.
3. 왼쪽/상단의 큰 병합셀은 반복 컬럼으로 만들지 말고 문맥 `<p>`로 분리합니다.
4. 내부 표의 실제 컬럼명이 보이면 그 컬럼명을 사용합니다.
5. `[1] 기본형`, `[2] 확장형`처럼 표 내부의 섹션 구분자는 `<h4>`로 분리합니다.
6. 셀의 텍스트는 생략하거나 요약하지 말고 그대로 재현합니다.
7. 내용이 비어 있는 셀이 있다면 빈 `<td></td>` 또는 `<td>-</td>`로 유지하세요.
8. 결과에는 원본에 없는 `하위1`, `하위2`, `구분 1`, `구분 2` 같은 임시 컬럼명을 만들지 않습니다.
""".strip()

        reference_text = ""
        if original_table_text:
            reference_text = f"""

기존 파서가 추출한 표 텍스트가 아래에 있습니다. 이미지가 최우선이지만, 글자 판독이 애매할 때만 참고하세요.

<extracted_table_text>
{original_table_text[:12000]}
</extracted_table_text>
"""
        body = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "이 이미지를 위 규칙에 맞춰 검색용 HTML table fragment로 변환해줘." + reference_text,
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"},
                        },
                    ],
                },
            ],
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self._url, json=body, headers=self._headers)
            resp.raise_for_status()
            result = resp.json()

        if result and result.get("choices"):
            html = result["choices"][0]["message"]["content"]
            html = re.sub(r"^```(?:html)?\s*|\s*```$", "", html.strip(), flags=re.IGNORECASE | re.MULTILINE)
            html = self._normalize_sparse_form_table(html)
            return html
        return None

    # ------------------------------------------------------------------
    # 단일 테이블 refine (chunk_idx 반환)
    # ------------------------------------------------------------------

    async def refine_table(
        self,
        i_page: int,
        bbox: dict,
        chunk_idx: int,
        original_table_text: str = None,
        clip_margin: float = 0.0,
        zoom: float = 2.0,
    ) -> tuple[int, str | None]:
        self._debug(
            "refine_table start chunk=%s page=%s original_chars=%s",
            chunk_idx, i_page + 1, len(original_table_text or ""),
        )
        b64 = self.cropping(bbox=bbox, i_page=i_page, clip_margin=clip_margin, zoom=zoom)
        html = await self._refine_table(b64, original_table_text=original_table_text)
        self._debug(
            "refine_table done chunk=%s page=%s html_chars=%s",
            chunk_idx, i_page + 1, len(html or ""),
        )
        return chunk_idx, html

    # ------------------------------------------------------------------
    # 배치 처리 (VT5 run_in_batches 그대로)
    # ------------------------------------------------------------------

    async def run_in_batches(self, tasks: list, batch_size: int = 5) -> list:
        results = []
        for i in range(0, len(tasks), batch_size):
            batch_results = await asyncio.gather(*tasks[i : i + batch_size], return_exceptions=True)
            results.extend(batch_results)
        return results

    # ------------------------------------------------------------------
    # 메인: DocChunk 리스트 처리
    # ------------------------------------------------------------------

    async def refine_chunks(self, chunks: list[DocChunk], documents: DoclingDocument, file_path: str) -> list[DocChunk]:
        """
        TableItem 을 포함한 chunk 를 찾아 VLM으로 표 HTML 재생성 후 chunk.text 치환.
        단일 TableItem 청크만 처리 (복수 TableItem 청크는 skip).
        """
        self.load_pdf(file_path)
        tasks = []
        task_chunk_idx: list[int] = []

        for chunk_idx, chunk in enumerate(chunks):
            doc_items = getattr(chunk.meta, "doc_items", None) or []
            table_items = [item for item in doc_items if isinstance(item, TableItem)]

            if len(table_items) != 1:
                continue

            table_item = table_items[0]
            if not table_item.prov:
                continue

            prov = table_item.prov[0]
            page_no = prov.page_no  # 1-indexed
            page_info = documents.pages.get(page_no)
            if not page_info or not page_info.size:
                continue

            pw, ph = page_info.size.width, page_info.size.height
            bbox = {
                "l": prov.bbox.l / pw,
                "t": prov.bbox.t / ph,
                "r": prov.bbox.r / pw,
                "b": prov.bbox.b / ph,
                "coord_origin": prov.bbox.coord_origin.value,
            }

            tasks.append(self.refine_table(
                i_page=page_no - 1,
                bbox=bbox,
                chunk_idx=chunk_idx,
                original_table_text=chunk.text,
            ))
            task_chunk_idx.append(chunk_idx)

        self._debug("scheduled_tables=%s total_chunks=%s", len(tasks), len(chunks))

        results = await self.run_in_batches(tasks, batch_size=self._batch_size)

        for item in results:
            if isinstance(item, Exception):
                self._debug("refine task raised exception=%r", item, level="warning")
                continue
            chunk_idx, html = item
            if not html:
                continue
            is_poor, reason = self._looks_like_poor_refined_table(html)
            if is_poor:
                self._debug("chunk=%s poor result reason=%s, keeping original", chunk_idx, reason, level="warning")
                continue
            chunks[chunk_idx].text = html
            self._debug("chunk=%s replaced html_chars=%s", chunk_idx, len(html))

        return chunks

    # ------------------------------------------------------------------
    # 품질 검사 (VT5 그대로)
    # ------------------------------------------------------------------

    def _looks_like_poor_refined_table(self, refined_table: str) -> tuple[bool, str]:
        if not refined_table:
            return True, "empty"
        if "```" in refined_table:
            return True, "code_fence"
        if not re.search(r"<table\b", refined_table, flags=re.IGNORECASE):
            if "|" in refined_table:
                return True, "markdown_output"
            return True, "no_html_table"

        poor_markers = ("하위1", "하위2", "하위3", "구분 1", "구분 2", "구분 3", "구분1", "구분2", "구분3")
        for marker in poor_markers:
            if marker in refined_table:
                return True, f"placeholder_column:{marker}"

        rows = self._parse_html_rows(refined_table)
        data_rows = [row for row in rows if not all(cell.strip() in {"", "-"} for cell in row)]
        if len(data_rows) < 2:
            return True, "too_few_rows"

        nonempty_header = [c for c in data_rows[0] if c]
        if nonempty_header:
            most_common, count = Counter(nonempty_header).most_common(1)[0]
            if count >= 3 and count / max(len(nonempty_header), 1) >= 0.4:
                return True, f"repeated_header:{most_common}"

        first_col = [row[0] for row in data_rows[1:] if row and row[0]]
        if len(first_col) >= 6:
            most_common, count = Counter(first_col).most_common(1)[0]
            if count >= 5 and count / len(first_col) >= 0.7:
                return True, f"repeated_first_column:{most_common}"

        return False, ""

    # ------------------------------------------------------------------
    # HTML 파싱 헬퍼 (VT5 그대로)
    # ------------------------------------------------------------------

    def _strip_html(self, text: str) -> str:
        text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _parse_html_rows(self, html: str) -> list[list[str]]:
        rows = []
        for tr in re.findall(r"<tr\b[^>]*>.*?</tr>", html or "", flags=re.IGNORECASE | re.DOTALL):
            cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)
            if cells:
                rows.append([self._strip_html(c) for c in cells])
        return rows

    def _parse_html_rows_with_attrs(self, html: str) -> list[list[dict[str, Any]]]:
        rows = []
        for tr in re.findall(r"<tr\b[^>]*>.*?</tr>", html or "", flags=re.IGNORECASE | re.DOTALL):
            row = []
            for match in re.finditer(r"<(t[dh])\b([^>]*)>(.*?)</\1>", tr, flags=re.IGNORECASE | re.DOTALL):
                attrs = match.group(2) or ""

                def read_span(name: str) -> int:
                    m = re.search(rf"{name}\s*=\s*[\"']?(\d+)", attrs, flags=re.IGNORECASE)
                    return max(int(m.group(1)), 1) if m else 1

                row.append({
                    "tag": match.group(1).lower(),
                    "text": self._strip_html(match.group(3)),
                    "rowspan": read_span("rowspan"),
                    "colspan": read_span("colspan"),
                })
            if row:
                rows.append(row)
        return rows

    def _escape_html_text(self, text: str) -> str:
        return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _normalize_sparse_form_table(self, html: str) -> str:
        """빈칸이 많은 체크리스트/점검표 → 직사각형 HTML 그리드로 정규화 (VT5 그대로)."""
        if not html or not re.search(r"<table\b", html, flags=re.IGNORECASE):
            return html

        total_cells = len(re.findall(r"<t[dh]\b", html, flags=re.IGNORECASE))
        empty_cells = len(re.findall(r"<t[dh]\b[^>]*>\s*(?:&nbsp;|-)?\s*</t[dh]>", html, flags=re.IGNORECASE))
        if total_cells < 20 or empty_cells < 10:
            return html

        plain = self._strip_html(html)
        if not any(m in plain for m in ("점검", "체크", "확인", "서명", "날짜", "총계", "○", "X")):
            return html
        if empty_cells / max(total_cells, 1) < 0.35:
            return html

        rows = self._parse_html_rows_with_attrs(html)
        if len(rows) < 3:
            return html

        date_values = [
            cell["text"] for cell in rows[0]
            if cell["text"] and not any(l in cell["text"] for l in ("점검항목", "날짜", "합계"))
            and re.search(r"\d|총계", cell["text"])
        ]
        if len(date_values) < 3:
            return html

        items = []
        current_category = ""
        for row in rows[1:]:
            row_texts = [(c.get("text", "") or "").strip() for c in row]
            nonempty = [t for t in row_texts if t and t != "-"]
            if not nonempty:
                continue
            item_text = next((t for t in row_texts if re.match(r"^\d+\s*[.)]", t)), "")
            if item_text:
                idx = row_texts.index(item_text)
                for t in row_texts[:idx]:
                    if t and not re.match(r"^\d+\s*[.)]", t) and len(t) <= 30:
                        current_category = t
                items.append((current_category, item_text, [""] * len(date_values)))
            elif any(m in " ".join(nonempty) for m in ("합", "총계", "확인", "서명")):
                items.append(("기입란", " ".join(nonempty), [""] * len(date_values)))

        if not items:
            return html

        header_cells = ["구분", "점검항목", *date_values]
        lines = ["<table>", "  <thead>",
                 "    <tr>" + "".join(f"<th>{self._escape_html_text(c)}</th>" for c in header_cells) + "</tr>",
                 "  </thead>", "  <tbody>"]
        for cat, item_text, vals in items:
            lines.append(
                "    <tr>"
                f"<td>{self._escape_html_text(cat)}</td>"
                f"<td>{self._escape_html_text(item_text)}</td>"
                + "".join(f"<td>{self._escape_html_text(v)}</td>" for v in vals)
                + "</tr>"
            )
        lines += ["  </tbody>", "</table>"]
        return "\n".join(lines)

    async def process(self, chunks: list[DocChunk], documents: DoclingDocument, **kwargs) -> list[DocChunk]:
        file_path = kwargs.get("file_path", "")
        if not file_path.lower().endswith(".pdf"):
            return chunks
        return await self.refine_chunks(chunks, documents, file_path)
