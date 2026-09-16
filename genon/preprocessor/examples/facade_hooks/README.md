# 전처리기 파일 커스터마이징 — 보관과 재적용

`facade/parser_processor.py` 와 `facade/chunking_processor.py` 는 **고객이 채우는 파일**이고,
저장소에는 **빈 원본 상태로 유지한다.** 사이트별 업무 로직은 저장소에 직접 넣지 않고 이
디렉터리에 예시로 남긴 뒤, 현장에서 원본에 붙여 쓴다.

**릴리스 갱신은 통째 갱신이라 이 두 파일도 덮어써진다.** 그래서 보관이 필요하다.

```bash
# 갱신 전 — 내 수정분을 뽑아 둔다
git diff -- genon/preprocessor/facade/parser_processor.py \
             genon/preprocessor/facade/chunking_processor.py > my_change.patch
# … 릴리스 통째 갱신 …
git apply my_change.patch          # 충돌하면 patch 를 보고 손으로 반영
```

훅 시그니처(`edit_input` / `edit_document` / `edit_chunk` / `edit_output`)와 `ROUTES` 형태는
고정 API 라, 그것이 안 바뀐 릴리스에서는 `git apply` 가 그대로 통한다.

## 예시

| 파일 | 붙일 자리 | 하는 일 |
|---|---|---|
| `hooks_skip_table_desc.py` | `parser_processor.py` 의 `__call__` | 지정한 doc_type 에서만 표 설명을 끈다 |
| `hooks_custom_route.py` | `parser_processor.py` 의 `ROUTES` + 새 메서드 | 표준 포맷으로 못 바꾸는 원천을 자기 라우트로 받는다 |
| `hooks_edit_chunk.py` | `chunking_processor.py` 의 `edit_chunk` | 청크 한 건씩 정제하고 내부용 청크를 버린다 |
| `custom_field_regex_demo.yaml` + `.py` | 설정 폴더 (코드 수정 없음) | `extractor: python` — 정규식으로 값을 뽑는다 |

청킹 쪽 정제 예시 비교(설정 vs 훅)는 `../text_cleanup/` 에 있다.
훅 전반의 사용법은 [facade_hooks.md](../../facade/gitbook_doc/facade_hooks.md) 를 본다.

마지막 항목은 훅이 아니다 — **전처리기 파일을 고치지 않고 설정만으로** 파이썬을 꽂는
자리다(`extractor: python`). 값 추출이 목적이라면 훅보다 이쪽이 먼저다.

## hooks_skip_table_desc.py — doc_type 으로 표 설명 끄기

yaml 의 `enrichment.table_text_description` 은 **프로세서 전역 스위치**다. `enable: false`
로 내리면 모든 문서에서 꺼지고, 문서유형을 가릴 수단이 설정에는 없다. 특정 doc_type 만
빼려면 이 훅을 쓴다.

`parser_processor.py` 의 `__call__` 에 **두 줄**을 넣는다(상수 한 줄은 파일 위쪽에).

```python
SKIP_TABLE_DESC_DOC_TYPES = ("cs_hpp",)

    async def __call__(self, request: Request, file_path: str, **kwargs) -> dict:
        ext = self.resolve_ext(file_path)
        doc_type = self.resolve_doc_type(**kwargs)
        if doc_type in SKIP_TABLE_DESC_DOC_TYPES:   # 추가 1
            kwargs["table_text_desc"] = 0           # 추가 2
        result = await self.run(request, file_path, **kwargs)
        return self.edit_output(ext, doc_type, result)
```

`edit_input` / `edit_output` 가 아니라 `__call__` 인 이유는 그 둘이 `kwargs` 를 받지 않기
때문이다. 런타임 플래그를 심을 수 있는 자리는 `self.run()` 앞뿐이다.

이 예시는 요청이 `table_text_desc` 를 직접 보내도 덮어쓴다. 요청 쪽 지정을 살리려면
`if doc_type in ... and kwargs.get("table_text_desc") is None:` 으로 조건을 늘린다.

### 왜 끄는가

`table_text_desc` 를 켜면 문서의 표를 모아 LLM 한 번에 여러 개씩 설명하게 한다. 그런데
한 호출에 표를 몇 개 담을지는 `max_context_tokens`(입력 예산)가 정하고, 그 응답을 받아 줄
`max_tokens`(출력 상한)는 배치 계획에 반영되지 않는다. 표가 아주 많은 문서에서는 배치가
요구하는 응답이 상한을 넘겨 JSON 이 중간에서 잘리고, 잘린 JSON 은 파싱에 실패해
**그 배치의 표 전부**가 설명 없이 지나간다. 로그에 아래가 반복되면 이 상황이다.

```
[WARNING] ... 표 설명 응답에 _table_descriptions 배열이 없습니다.
```

표가 수백~수천 개인 문서유형은 표 설명을 끄는 편이 낫다. 켠 채로 쓰려면 yaml 에서
`max_context_tokens` 를 낮춰 배치당 표 수를 줄이고 `max_tokens` 를 함께 올린다.

### `table_text_desc` 한 키로 두 경로가 함께 꺼진다

표 설명은 두 경로가 만든다. 둘 다 같은 런타임 키를 보므로 0 하나로 모두 멎는다.

| 경로 | 판정 지점 |
|---|---|
| 독립 실행기 | `enrichment/table_text_description.py` 의 `wants()` |
| custom_fields 융합 | `enrichment/custom_fields_enricher.py` 의 `wants_table_descriptions()` |

### 확인

```bash
python preprocessor.py <원천파일> --doc-type cs_hpp -o parsed.json
```

`--doc-type` 을 꼭 준다. 훅이 doc_type 게이팅이라 없으면 안 탄다. 산출 청크에
`[표 검색 설명]` 블록이 없으면 꺼진 것이다. 목록에 없는 doc_type 으로 한 번 더 돌려
그때는 블록이 붙는지도 함께 본다.

요청 단위로 임시로 끄고 싶을 뿐이라면 코드를 고치지 않고 API params 로도 된다.

```bash
curl -X POST http://localhost:8000/parser -H 'Content-Type: application/json' \
  -d '{"file_path": "...", "params": {"table_text_desc": 0}}'
```
