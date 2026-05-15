# `_merge_small_chunks` 재사용 불가 문제

## 상황

plan.md VT5 항목에 `enrichment/chunk_merger.py`로 `_merge_small_chunks`를 구현할 예정이다.
레거시 파일들에 이미 구현체가 있지만 그대로 가져다 쓰기 어렵다.

## 레거시 구현 위치

- `facade/legacy/적재용(외부)_ocr.py:597` (마지막 수정: 2025-11-18, 김성헌)
- `facade/legacy/적재용(내부).py`, `적재용(규정).py`, `다우기술.py`, `삼성증권리서치센터.py`, `OKDS_pdf.py`, `preprocess_ocr.py`

동일한 로직이 레거시 파일마다 **복붙** 형태로 존재한다.

## 문제: GenosSmartChunker 전용 헬퍼에 강결합

레거시 `_merge_small_chunks`가 아래 메서드들에 의존한다.

```python
self._generate_text_from_items_with_headers(merged_items, merged_header_infos, dl_doc)
self._extract_used_headers(merged_header_infos)
chunk._header_info_list  # DocChunk에 동적으로 붙이는 비공개 속성
```

이 메서드들은 `GenosSmartChunker` 내부에서만 존재하는 것들이라,
`enrichment/chunk_merger.py`로 독립 모듈로 분리하려면 아래처럼 대체해야 한다.

| 레거시 의존 | 대체 |
|------------|------|
| `_generate_text_from_items_with_headers` | `chunk.text` 직접 사용 |
| `_extract_used_headers` | `chunk.meta.headings` 직접 사용 |
| `chunk._header_info_list` | 제거 (DocChunk 공개 API로 충분) |

## 핵심 로직 (레거시에서 추출)

```
min_chunk_size = max_tokens // 3

청크를 순회하면서:
  - chunk_tokens < min_chunk_size → 병합 후보에 누적
  - 병합 후보 + 현재 청크 합산이 max_tokens 이하 → 병합
  - max_tokens 초과 → 후보 확정 후 새 후보 시작
  - 마지막 남은 후보 그대로 추가
```

## 결론

`enrichment/chunk_merger.py` 구현 시 레거시 로직은 참고만 하고,
`DocChunk` 공개 API(`chunk.text`, `chunk.meta.headings`, `chunk.meta.doc_items`)만 사용하도록 새로 작성해야 한다.
