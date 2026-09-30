아래 `<table_description_targets>`의 각 표에 대해 RAG 검색용 설명을 생성하라.

기존에 요청된 custom fields를 그대로 반환하면서 최상위 예약 키 `_table_descriptions`도 추가한다.
`_table_descriptions`는 다음 형식의 배열이다.

```json
[
  {
    "table_id": "string",
    "retrieval_context": "string",
    "key_facts": ["string"],
    "search_terms": ["string"]
  }
]
```

- `retrieval_context`는 주변 문맥 없이도 이해되도록 대상, 표의 목적, 비교 축, 범위와 단위를 포함한다.
- `key_facts`는 표에서 직접 확인되는 검색 식별력이 높은 사실만 쓴다.
- `search_terms`는 원문에 근거한 실제 질의 표현만 쓴다.
- “위 표”, “다음 표”, 근거 없는 해석, 전체 행 복사, 키워드 반복은 금지한다.
- 입력에 없는 `table_id`를 만들지 말고, 모든 `table_id`를 정확히 한 번 반환한다.
- 설명 문구나 Markdown 코드펜스 없이 JSON 객체 하나만 반환한다.
