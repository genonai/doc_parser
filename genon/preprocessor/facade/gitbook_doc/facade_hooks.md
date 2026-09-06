# 고객 확장 훅 — 설정으로 안 되는 원천 처리하기

`custom_field_*.yaml` 로 안 풀리는 원천을 만났을 때, **코어를 고치지 않고** 전처리기 파일
하나만 손봐서 해결하는 방법입니다.

> 새 `doc_type` 을 추가하는 것뿐이라면 코드가 아니라 `custom_field_*.yaml` 이 먼저입니다.
> 어디까지 설정으로 되는지는 [parser_processor.md](parser_processor.md) 의 지원 매트릭스를
> 보세요. 이 문서는 **설정으로 안 될 때** 읽습니다.

## 고칠 파일은 둘뿐입니다

| 파일 | 줄수 | 고칠 자리 |
|---|---:|---|
| `facade/parser_processor.py` | 92 | `ROUTES` · `pre_source` · `post_parse` |
| `facade/chunking_processor.py` | 100 | `GenOSVectorMeta` · `GenosSmartChunker` · `pre_chunk` · `post_chunk` |

처리 본체는 `facade/core/` 에 있고 **열어 볼 일이 없습니다.** 열어야 했다면 그건 훅이
부족하다는 뜻이니 알려 주세요.

## 언제 무엇이 불리나

```
파싱   요청 → 확장자 판정 → ROUTES → [pre_source] → 파싱 → [post_parse] → 응답
청킹   파서 결과 → 형태 판별 → [pre_chunk] → 분할·벡터 조합 → [post_chunk] → 응답
```

`__call__` 을 열어 보면 이 순서가 그대로 적혀 있습니다.

## pre_source — 원천을 파싱 입력으로 바꾼다

`data` 의 형은 확장자가 정하고, **같은 형으로 돌려줍니다.**

| 확장자 | data | 시점 |
|---|---|---|
| `.json` | `dict` / `list` (깨진 JSON 이면 `str`) | 매핑 전 |
| `.md` `.html` | `str` | 내장 전처리(flatten·front matter) 전 |
| `.xlsx` `.csv` | `dict[시트명, 2차원 행]` | 병합셀이 이미 펴진 상태 |
| 그 밖 | `str`(파일 경로) | 파생 파일은 `work_dir` 에 |

**건드릴 것이 없으면 받은 값을 그대로 돌려주세요.** 그래야 원본 경로가 유지됩니다.
`doc_type` 은 소문자로 정규화되어 옵니다 — `"MyType"` 으로 비교하면 영영 안 맞습니다.

```python
    def pre_source(self, ext, doc_type, data, work_dir=None):
        # JSONL/NDJSON — json.loads 가 실패하면 원문 str 로 옵니다.
        if ext == ".json" and isinstance(data, str):
            return {"rows": [json.loads(ln) for ln in data.splitlines() if ln.strip()]}

        # 엑셀 상단 2행이 로고·안내문일 때
        if ext == ".xlsx" and doc_type == "branch_list":
            return {name: rows[2:] for name, rows in data.items()}

        return data
```

### 새 확장자를 받으려면 — ROUTES 한 줄

`route_*` 메서드를 새로 만들 필요는 없습니다. `pre_source` 가 원천을 **이미 처리할 수 있는
포맷으로 바꿔** 그 핸들러에 태우면 됩니다. `.md` `.html` `.json` 표 파일 말고 다른 확장자는
`pre_source` 가 **파일 경로**를 받고 `work_dir`(요청이 끝나면 정리되는 임시 디렉터리)을
함께 받으므로, 거기에 변환 결과를 쓰고 그 경로를 돌려주면 됩니다.

```python
    ROUTES = (((".xml",), "route_json"),        # 표 맨 앞에 두 줄
              ((".tsv",), "route_tabular")) + (... 기존 표 그대로 ...)

    def pre_source(self, ext, doc_type, data, work_dir=None):
        if ext == ".xml" and doc_type == "monimo_event":
            events = [{c.tag: c.text for c in ev}
                      for ev in ET.parse(data).getroot().find("eventList")]
            out = os.path.join(work_dir, "converted.json")
            json.dump({"eventList": events}, open(out, "w", encoding="utf-8"),
                      ensure_ascii=False)
            return out                          # 새 경로를 돌려주면 그것으로 파싱합니다
        return data
```

실측(#363)으로 `.xml` → `monimo_event`(3청크, `GROUP_C` 정상), `.tsv` → `faq`(3청크)가
위 방식으로 통했습니다. 등록 전에는 둘 다 1청크에 목표필드가 비어 있었습니다.

**먼저 ROUTES 에 등록한 다음 시험하세요.** 등록 전에 넣으면 캐치올(`route_other`)이 받아
결과가 달라집니다.

### 엑셀은 원하는 라이브러리로 다뤄도 됩니다

2차원 행 목록, pandas·polars `DataFrame`, `list[dict]` 중 무엇으로 돌려줘도 받습니다.

```python
        if ext == ".xlsx" and doc_type == "branch_list":
            import pandas as pd
            rows = data["지점현황"]
            df = pd.DataFrame(rows[3:], columns=rows[2])
            df["전화"] = df["전화"].map(lambda v: tb.regex_sub(v, pattern=r"\D", repl=""))
            return {"지점현황": df}
```

> **병합셀 주의.** 병합 정보는 (행,열) 좌표입니다. **행·열 개수를 그대로 두면** 유지되어
> `연락처_전화` 같은 멀티헤더 자동판정이 계속 동작하고, **행을 지우거나 더하면** 버려집니다.
> 그때는 `formats.xlsx.header_row` 로 헤더 위치를 알려 주세요.

## post_parse — 산출을 손본다

```python
    def post_parse(self, ext, doc_type, result):
        result["elements"]   # 레코드/표 경로 산출 (list[dict])
        result["document"]   # docling 경로 산출   (dict)
        return result
```

**청크에 실을 메타는 `result["metadata"]` 에 직접 쓰면 안 됩니다.** 파서와 청커는 별도
API 라 그 값은 호출자용 정보로 끝납니다. `tb.set_chunk_metadata()` 를 쓰세요.

```python
        tb.set_chunk_metadata(result, {
            "SOURCE_SYSTEM": "CRM",
            tb.FIRST_CHUNK_FIELDS_KEY: ["PRODUCT_NM"],   # 첫 청크에만 붙일 필드
        })
```

## pre_chunk / post_chunk — 청킹 쪽

```python
    def pre_chunk(self, kind, data, **kwargs):
        # kind=="parse" 면 data 는 list[dict]
        # kind=="docling" 이면 DoclingDocument 를 **직렬화한 dict** 입니다.
        #   본문은 data["texts"][i]["text"] 로 닿습니다. data.texts 는 없습니다.
        return data

    def post_chunk(self, vectors, **kwargs):
        kept = [v for v in vectors if v.n_char > 20]   # 너무 짧은 청크 버리기
        return tb.refresh_stats(kept)                  # 아래 주의사항
```

### post_chunk 에서 본문을 고치면 refresh_stats 를 부르세요

`n_char`·`n_word`·`n_line` 과 청크 순번(`i_chunk_on_doc` 등)은 청킹이 끝날 때 계산됩니다.
`post_chunk` 는 그 뒤라서, 본문을 고치거나 청크를 버려도 이 값들이 **옛 값으로 남습니다**
(실측: 마커만 지운 훅에서 11건 중 9건의 `n_char` 가 실제 길이와 달랐습니다).

```python
tb.refresh_stats(vectors)                  # 청크를 버렸을 때 — 순번까지 다시 맞춥니다
tb.refresh_stats(vectors, reindex=False)   # 본문만 고쳤을 때 — 통계만 고칩니다
```

## 청크 본문에서 특수문자 걷어내기

RAG 검색용 정제는 **설정으로 하는 것이 기본**입니다. `chunking_processor_config.yaml` 의
`chunking.text_cleanup` 에 규칙을 적으면 됩니다 — 코드를 고치지 않습니다.

| 방식 | 정제하는 자리 | 쓰는 때 |
|---|---|---|
| yaml | `chunking.text_cleanup` | 전 문서 공통 |
| `post_chunk` | 이 파일 | 특정 doc_type 만 |
| 둘 다 | 공통은 yaml, 예외만 훅 | 대부분의 실제 사이트 |

설정 규칙은 **청킹 입력**에 걸리므로 삭제가 청크 경계와 `n_char` 에 반영되고, LLM 보강이
보는 텍스트까지 같이 깨끗해집니다. `post_chunk` 는 완성된 청크를 손보므로 그 두 이점이
없습니다. 그래서 `text_cleanup` 이 doc_type 을 가릴 수 없을 때만 훅을 씁니다.

**전부 지우면 안 됩니다.** 실측(상담 HTML 1건, 청크 11건 / 2,582자)에서 특수문자 225개 중
지워서 이득인 것은 장식 마커(`■ ◈ ※ ☎`) 13개와 미해독 엔티티(`&gt;`) 3개뿐이었습니다.
`|` 66개는 마크다운 표의 칸 경계이고 `[` `]` 12개는 파이프라인이 붙인 `[표 검색 설명]`
라벨입니다 — 지우면 표와 라벨이 같이 죽습니다.

원천에는 `<table>` 마크업이 261쌍 있었지만 **청크에는 남지 않습니다**(docling 이 표로
바꿔 줍니다). 규칙은 원문이 아니라 **청크 산출을 보고** 정하세요.

돌려 볼 수 있는 예시 3종이 `examples/text_cleanup/` 에 있습니다 — yaml 만 / `post_chunk` 만 /
둘 다. 세 산출을 나란히 재고, 정제 후 마커가 0 인지와 `n_char` 가 어긋나지 않는지 단정합니다.

어느 방식이든 청크 본문이 바뀌므로 **재색인이 필요합니다.**

## toolbox — 이미 있는 기능을 씁니다

```python
from genon.preprocessor.facade.core import toolbox as tb
```

**직접 구현하기 전에 여기부터 보세요.** 값 변환기는 yaml 의 `transform:` 이 부르는 것과
**같은 함수**라, 설정으로 하던 변환과 코드로 하는 변환이 어긋나지 않습니다.

| 갈래 | 항목 |
|---|---|
| 값 변환 | `regex_sub` `regex_extract` `to_int` `truncate` `html_text` `text` `date_int` `date_int_flex` `text_norm` `json_to_markdown` |
| 엑셀 | `load_sheets` `load_tables` |
| JSON | `collect_text_fields` `detect_format` |
| 표 | `render_table` `render_plain_text` `sanitize_table_html` |
| 텍스트 | `sanitize` `tidy` `read_text_with_fallback` |
| md·html | `promote_markdown_marker_headings` `unfence_text` `precheck_html` `marker_heading_match` |
| 청크 메타 | `set_chunk_metadata` + 예약 키 4개 |
| 청크 통계 | `refresh_stats` (post_chunk 로 본문을 고쳤을 때) |

## 고쳤으면 확인합니다

전처리기 파일은 **그 자체로 실행됩니다.** 서버를 띄우지 않고 한 건만 돌려 볼 수 있습니다.

```bash
python preprocessor.py 지점현황.xlsx --doc-type branch_list -o parsed.json   # 파서
python preprocessor.py parsed.json -o chunks.json                            # 청커
```

`--doc-type` 을 꼭 주세요. 훅이 전부 `doc_type` 게이팅이라 없으면 훅이 안 탑니다.

**기존 문서가 안 깨졌는지**는 자기 골든으로 확인합니다.

```bash
# 고치기 전에 한 번
examples/parse_chunk/parse_chunk_golden.py --record
# 고친 뒤
examples/parse_chunk/parse_chunk_golden.py --check
```

## 릴리스 갱신 때 내 수정분 지키기

**전처리기 갱신은 릴리스 단위 통째 갱신입니다** — `facade/parser_processor.py` 와
`facade/chunking_processor.py` 도 함께 덮어써집니다. 그래서 갱신 전에 보관하고 뒤에 다시
붙입니다.

```bash
# 실행 위치: gitea_repo
git diff -- genon/preprocessor/facade/parser_processor.py \
             genon/preprocessor/facade/chunking_processor.py > my_change.patch
# … 릴리스 통째 갱신 …
git apply my_change.patch          # 충돌하면 patch 를 보고 손으로 반영
```

훅 시그니처(`pre_source` / `post_parse` / `pre_chunk` / `post_chunk`)와 `ROUTES` 형태는
**고정 API** 로 유지합니다. 그것이 안 바뀐 릴리스에서는 `git apply` 가 그대로 통합니다.
릴리스 노트의 **"템플릿 변경 있음 / 없음"** 표시를 먼저 확인하세요.

## 훅으로 안 되는 것

아래는 설정이 값 파이프라인 안쪽이나 순회 자체를 바꾸는 것들이라 훅으로 재현되지
않습니다. **`custom_field_*.yaml` 에서 푸세요.**

| 기능 | 이유 |
|---|---|
| `source.merge_rows` | 값 파이프라인 **이전**에 값을 이어붙여 렌더까지 바꿉니다 |
| `source.sections` `ignore_keys` | `json_semantic` 순회 자체를 좌우합니다 |
| `markdown.front_matter` 승격 | 본문 제외는 되지만 metadata 승격은 안 됩니다 |
