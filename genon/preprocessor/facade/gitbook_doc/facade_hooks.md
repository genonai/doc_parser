# 고객 확장 훅 — 설정으로 안 되는 원천 처리하기

`custom_field_*.yaml` 로 안 풀리는 원천을 만났을 때, **코어를 고치지 않고** 전처리기 파일
하나만 손봐서 해결하는 방법입니다.

> 새 `doc_type` 을 추가하는 것뿐이라면 코드가 아니라 `custom_field_*.yaml` 이 먼저입니다.
> 어디까지 설정으로 되는지는 [parser_processor.md](parser_processor.md) 의 지원 매트릭스를
> 보세요. 이 문서는 **설정으로 안 될 때** 읽습니다.

## 고칠 파일은 둘뿐입니다

| 파일 | 줄수 | 고칠 자리 |
|---|---:|---|
| `facade/parser_processor.py` | 293 | `ROUTES` · `CONFIG_BY_DOC_TYPE` · `edit_input` · `edit_document` · `edit_output` |
| `facade/chunking_processor.py` | 243 | `GenOSVectorMeta` · `GenosSmartChunker` · `ROW_CATEGORIES` · `CONFIG_BY_DOC_TYPE` · `edit_input` · `edit_chunk` · `edit_output` |

처리 본체는 `facade/core/` 에 있고 **열어 볼 일이 없습니다.** 열어야 했다면 그건 훅이
부족하다는 뜻이니 알려 주세요.

## 언제 무엇이 불리나

```
파싱   요청 → 확장자 판정 → doc_type 별 설정 → [edit_input] → ROUTES → 파싱 → [edit_document] → enrichment → [edit_output] → 응답
청킹   파서 결과 → 형태 판별 → doc_type 별 설정 → [edit_input] → 분할 → [edit_chunk] → vector_meta 조립 → [edit_output] → 응답
```

`__call__` 을 열어 보면 이 순서가 그대로 적혀 있습니다.

`.json` · `.md` · `.html` · 엑셀은 파일 경로가 아니라 **로드된 데이터**를 훅에 넘겨야 해서
`edit_input` 가 해당 라우트 안에서 불립니다. 순서상 위치는 같습니다.

## 모든 훅에 공통인 두 가지

### 요청 파라미터는 `**kwargs` 로 받습니다

훅 시그니처 끝에 `**kwargs` 를 붙이면 요청의 `params` 가 그대로 들어옵니다. 부서·언어·
원천시스템처럼 **요청마다 달라지는 값**은 이 통로로만 받으세요.

```python
    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
        if kwargs.get("tenant") == "CARD":
            ...
```

`self` 에 담아 두면 안 됩니다. 프로세서는 **인스턴스 하나가 모든 요청을 받습니다.**
`__call__` 에서 `self._tenant = ...` 로 담고 `edit_output` 에서 읽으면, 그 사이의 `await`
에서 다른 요청이 끼어들어 값이 섞입니다.

`**kwargs` 를 안 붙인 기존 훅은 인자가 늘지 않습니다 — 그대로 두어도 동작합니다.

### 훅은 `async def` 로 써도 됩니다

사내 API 조회처럼 외부 호출이 필요하면 `async def` 로 바꾸고 `await` 하세요. core 가
코루틴을 알아서 기다립니다.

```python
    async def edit_output(self, ext, doc_type, result, **kwargs):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MASTER_API}/dept/{kwargs.get('dept_cd')}")
        tb.set_chunk_metadata(result, {"DEPT_NM": resp.json()["name"]})
        return result
```

**동기 함수 안에서 외부 호출을 하지 마세요.** 서버가 요청 하나를 처리하는 동안 다른
문서의 요청까지 함께 멈춥니다(이벤트 루프가 막힙니다).

## edit_input — 원천을 파싱 입력으로 바꾼다

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
    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
        # JSONL/NDJSON — json.loads 가 실패하면 원문 str 로 옵니다.
        if ext == ".json" and isinstance(data, str):
            return {"rows": [json.loads(ln) for ln in data.splitlines() if ln.strip()]}

        # 엑셀 상단 2행이 로고·안내문일 때
        if ext == ".xlsx" and doc_type == "branch_list":
            return {name: rows[2:] for name, rows in data.items()}

        return data
```

### 새 확장자를 받으려면 — ROUTES 한 줄

`route_*` 메서드를 새로 만들 필요는 없습니다. `edit_input` 가 원천을 **이미 처리할 수 있는
포맷으로 바꿔** 그 핸들러에 태우면 됩니다. `.md` `.html` `.json` 표 파일 말고 다른 확장자는
`edit_input` 가 **파일 경로**를 받고 `work_dir`(요청이 끝나면 정리되는 임시 디렉터리)을
함께 받으므로, 거기에 변환 결과를 쓰고 그 경로를 돌려주면 됩니다.

```python
    ROUTES = (((".xml",), "route_json"),        # 표 맨 앞에 두 줄
              ((".tsv",), "route_tabular")) + (... 기존 표 그대로 ...)

    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
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

### 그래도 안 되면 — 라우트를 직접 씁니다

로그·고정폭 텍스트·사내 전문 포맷처럼 **표준 포맷 어느 것으로도 못 바꾸는 원천**은
핸들러를 직접 만듭니다. 그 핸들러도 **이 파일에 둡니다** — core 는 여전히 안 고칩니다.

```python
    ROUTES = (((".log",), "route_log"),) + (... 기존 표 그대로 ...)

    async def route_log(self, job):
        lines = [l for l in tb.read_text_with_fallback(job.source).splitlines() if l.strip()]
        return {"elements": tb.make_elements(lines)}
```

계약은 셋뿐입니다.

| | |
|---|---|
| 시그니처 | `async def route_<이름>(self, job) -> dict \| None` |
| 응답 | `{"elements": [...]}` 만 채우면 됩니다. `content`·`usage` 는 core 가 채웁니다 |
| 폴스루 | `None` 을 돌려주면 `ROUTES` 의 다음 후보로 넘어갑니다 |

`job` 은 요청 한 건의 정보입니다. 자주 쓰는 것은 넷입니다.

| 필드 | 값 |
|---|---|
| `job.source` | 실제로 파싱할 경로. 확장자 별칭 사본이나 `edit_input` 파생 파일이면 원본과 다릅니다 |
| `job.file_path` | 요청이 넘긴 원본 경로 |
| `job.ext` / `job.doc_type` | 표준 확장자, 문서 유형 |
| `job.params` | 요청 파라미터. `job.notes` 는 단계 사이 값 전달용입니다 |

> 옛 시그니처 `async def route_<이름>(self, file_path, ext, ctx, **kwargs)` 로 쓴 라우트도
> 그대로 불립니다. 새로 쓰는 라우트만 `job` 형태로 쓰면 됩니다.

`tb.make_elements()` 가 `id`·`page`·`coordinates` 같은 배관 필드를 채웁니다. 원소는
문자열이거나 dict 이고, **행 1개 = 청크 1개**로 적재하려면 category 를 바꿉니다.

```python
        return {"elements": tb.make_elements(
            [{"content": row["본문"], "metadata": {"ORDER_NO": row["주문번호"]}} for row in rows],
            category="custom_fields_row",   # 청커의 행 기반 경로로 보냅니다
        )}
```

실측: 위 `.log` 예시 그대로 3줄 파일을 파싱하면 element 3개, 이어서 청킹하면 청크 3개가
나옵니다. 돌려 볼 수 있는 예시는 `examples/facade_hooks/hooks_custom_route.py` 입니다.

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

## edit_document — LLM enrichment 전에 문서 구조를 고친다

pdf·hwp·docx·html·md 처럼 문서를 만드는 경로에서, 파싱이 끝나고 **LLM enrichment(표 설명,
이미지 설명, 문서 요약, custom_fields 추출) 전**에 불립니다. `doc` 은 DoclingDocument 객체입니다.
`edit_output` 의 `result["document"]` 는 enrichment 가 끝난 뒤 JSON 으로 바꾼 dict 라 형태가 다릅니다.

```python
    def edit_document(self, job, doc):
        for item in doc.texts:
            if item.text.startswith("부칙"):
                item.label = "section_header"   # 헤딩 레벨 보정
        return doc
```

돌려준 문서가 enrichment 로 넘어갑니다. `None` 을 돌려주면 받은 문서를 그대로 씁니다.
enrichment 가 끝난 뒤(`edit_output`)에 표를 빼면 LLM 호출 비용은 이미 치른 뒤이므로,
설명 대상에서 뺄 표는 여기서 손봅니다. `**kwargs` 와 `async def` 는 다른 훅과 같이 쓸 수 있습니다.

## edit_output — 산출을 손본다

```python
    def edit_output(self, ext, doc_type, result, **kwargs):
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

## doc_type 마다 설정을 다르게 — `CONFIG_BY_DOC_TYPE`

설정 파일은 모든 문서에 똑같이 적용됩니다. **문서 종류마다 다르게 하고 싶으면** 훅을 쓰지
말고 이 표에 적으세요. 두 파일 모두 같은 자리에 있습니다.

```python
    CONFIG_BY_DOC_TYPE = {
        "press":    {"enrichment.table_description.enable": False},  # 표가 없어 불필요한 LLM 호출
        "contract": {"ocr.ocr_mode": "force"},                       # 스캔본이 많다
    }
```

키는 **설정 파일 경로(점 표기)** 로 씁니다. 설정 파일에 적던 이름 그대로입니다.
같은 뜻의 요청 파라미터 이름(괄호 안)으로 써도 같게 동작합니다.

| 파서 | | 청커 | |
|---|---|---|---|
| `enrichment.table_description.enable` (`table_desc`) | 표 설명 | `chunking.chunk_size` (`chunk_size`) | 청크 최대 크기 |
| `enrichment.image_description.enable` (`img_desc`) | 이미지 설명 | `chunking.recursive.chunk_overlap` (`chunk_overlap`) | 청크 간 겹침 |
| `enrichment.doc_summary.enable` (`doc_summary`) | 문서 요약 | `chunking.chunk_mode` (`chunk_mode`) | `split_only` / `resize_all` |
| `enrichment.toc.enable` (`toc`) | 목차 보강 | | |
| `ocr.ocr_mode` | `auto` / `force` / `disable` | | |
| `pdf_output.keep` (`keep_pdf`) | 변환 PDF 보존 | | |

**모든 설정을 요청마다 바꿀 수 있는 것은 아닙니다.** 엔드포인트 주소, 프롬프트, 토크나이저
경로처럼 기동 시 한 번 읽혀 굳는 설정은 이 표에 적어도 건너뛰고 로그에 경고가 남습니다.
그런 값은 설정 파일에서 바꿉니다. 청킹 설정은 파서가 아니라 청커의 같은 표에 적습니다.

표로 안 되는 조건은 `config_by_condition()` 에서 정합니다. 같은 형식의 dict 를 돌려주고,
빈 dict 면 아무것도 바뀌지 않습니다.

```python
    def config_by_condition(self, job):
        if job.params.get("dept") == "IR":
            return {"enrichment.doc_summary.enable": True}
        return {}
```

우선순위는 뒤가 셉니다 — **설정 파일 → `CONFIG_BY_DOC_TYPE` → `config_by_condition()` →
요청 파라미터.** 요청이 보낸 값은 절대 덮이지 않습니다. 실제로 적용된 값은 `job.config` 에
남아 "이 문서가 어떤 설정으로 처리됐는지" 를 되짚을 수 있습니다.

## 코드가 아니라 값으로 바꾸는 것들 — 청킹

훅을 쓰기 전에 이쪽부터 보세요. 한 줄이면 끝나는 것들입니다.

| 하고 싶은 것 | 바꿀 것 | 자리 |
|---|---|---|
| 청크 앞 `HEADER:` 라벨을 다른 말로 / 없애기 | `GenosSmartChunker.CHUNK_HEADER_PREFIX` (빈 문자열이면 경로만) | `chunking_processor.py` |
| 섹션 경로 구분자 | `CHUNK_HEADER_SEP` · `CHUNK_PATH_SEP` · `CHUNK_PATH_MAX_LEAVES` | 〃 |
| 파서가 만든 **새 category** 를 행 1개 = 청크 1개로 처리 | `ROW_CATEGORIES` 에 이름 추가 | 〃 |
| 1024 보다 작은 청크 만들기 | `chunking.min_chunk_size` (0 이면 보정 안 함) | `chunking_processor_config.yaml` |

`CHUNK_HEADER_PREFIX` 는 청크 크기 산정과 실제 부착이 같은 값을 보므로 여기만 바꾸면
됩니다. `min_chunk_size` 는 docling 경로의 하한이라, 임베딩 모델의 입력 길이가 짧아
더 잘게 나눠야 하는 사이트에서 낮춥니다. **둘 다 청크 본문·경계가 바뀌므로 재색인이
필요합니다.**

## edit_input / edit_output — 청킹 쪽

```python
    def edit_input(self, kind, data, **kwargs):
        # kind=="parse" 면 data 는 list[dict]
        # kind=="docling" 이면 DoclingDocument 를 **직렬화한 dict** 입니다.
        #   본문은 data["texts"][i]["text"] 로 닿습니다. data.texts 는 없습니다.
        return data

    def edit_output(self, vectors, **kwargs):
        kept = [v for v in vectors if v.n_char > 20]   # 너무 짧은 청크 버리기
        return tb.refresh_stats(kept)                  # 아래 주의사항
```

## edit_chunk — 청크 한 건씩 손보거나 버립니다

**본문을 고치거나 청크를 버리는 일은 `edit_output` 가 아니라 여기서 하세요.** 통계와 순번이
붙기 전이라 코어가 알아서 맞춰 줍니다 — `refresh_stats` 를 부를 필요가 없습니다.

```python
    def edit_chunk(self, text, info, **kwargs):
        if "상담직원용" in text:
            return tb.DROP                 # 이 청크를 버립니다
        return text.replace("■", "")       # 고친 본문을 돌려줍니다
```

돌려주는 값의 뜻은 셋입니다.

| 값 | 뜻 |
|---|---|
| 문자열 | 그 문자열이 청크 본문이 됩니다 |
| `None` | 손대지 않습니다. `return` 을 빠뜨려도 청크가 사라지지 않습니다 |
| `tb.DROP` | 이 청크를 버립니다. 순번·개수는 코어가 다시 맞춥니다 |

빈 문자열이나 공백만 돌려줘도 버린 것으로 봅니다(빈 청크는 적재 의미가 없습니다).

`info` 는 **경로가 달라도 모양이 같습니다.** 문서·레코드·평문 어느 원천이든 훅 한 벌로
처리할 수 있습니다.

| 키 | 값 |
|---|---|
| `kind` | `"docling"`(문서) · `"row"`(레코드/표 행) · `"text"`(그 밖) |
| `page` | 1-based 페이지 |
| `index` | 현재 순번. 버리면 다시 매겨지므로 참고용입니다 |
| `headings` | 섹션 경로 목록. `docling` 경로만 채워집니다 |
| `metadata` | `row` 는 레코드 metadata, `docling` 은 문서 메타. 복사본이라 고쳐도 저장되지 않습니다 |
| `fields` | 청크별 값을 넣는 dict. 넣은 값은 `GenOSVectorMeta` 필드로 실립니다 |

청크마다 다른 값을 실으려면 `info["fields"]` 에 넣습니다. `GenOSVectorMeta` 에 필드를 선언해
두면 타입도 검사됩니다.

```python
    def edit_chunk(self, text, info, **kwargs):
        if "손실" in text:
            info["fields"]["RISK"] = "high"
        return None
```

본문(`text`)과 통계·순번 필드(`n_char`, `i_chunk_on_doc` 등)는 `fields` 로 바꿀 수 없습니다.
본문은 반환값으로 바꾸고, 통계와 순번은 코어가 계산합니다.

`text` 는 접두와 `HEADER:` 라인까지 **붙은 뒤**의 본문입니다. 훅이 돌려준 값에 마스킹·정제·
표기형태 변형이 뒤이어 적용됩니다.

> 첫 청크 전용 접두(`body.once`)는 **살아남은 첫 청크**가 받습니다. 0번을 버려도 문서
> 식별 정보가 사라지지 않습니다.
>
> 두 경로는 이 훅을 타지 않습니다 — 음성 전사(`[AUDIO]`)와 legacy tabular(`[DA]`)는
> 파일 하나가 청크 하나라 `edit_output` 로 충분합니다.

### edit_output 에서 본문을 고치면 refresh_stats 를 부르세요

(본문 수정·청크 버리기는 위 `edit_chunk` 가 낫습니다. 이 절은 그 밖의 손질에 해당합니다.)

`n_char`·`n_word`·`n_line` 과 청크 순번(`i_chunk_on_doc` 등)은 청킹이 끝날 때 계산됩니다.
`edit_output` 는 그 뒤라서, 본문을 고치거나 청크를 버려도 이 값들이 **옛 값으로 남습니다**
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
| `edit_chunk` | 이 파일 | 특정 doc_type 만 (통계가 자동으로 맞습니다) |
| 둘 다 | 공통은 yaml, 예외만 훅 | 대부분의 실제 사이트 |

설정 규칙은 **청킹 입력**에 걸리므로 삭제가 청크 경계와 `n_char` 에 반영되고, LLM 보강이
보는 텍스트까지 같이 깨끗해집니다. 훅은 이미 잘린 청크를 손보므로 경계는 되돌리지 못합니다
(`edit_chunk` 는 `n_char` 까지는 맞춰 줍니다). 그래서 `text_cleanup` 이 doc_type 을 가릴 수
없을 때만 훅을 씁니다.

**전부 지우면 안 됩니다.** 실측(상담 HTML 1건, 청크 11건 / 2,582자)에서 특수문자 225개 중
지워서 이득인 것은 장식 마커(`■ ◈ ※ ☎`) 13개와 미해독 엔티티(`&gt;`) 3개뿐이었습니다.
`|` 66개는 마크다운 표의 칸 경계이고 `[` `]` 12개는 파이프라인이 붙인 `[표 검색 설명]`
라벨입니다 — 지우면 표와 라벨이 같이 죽습니다.

원천에는 `<table>` 마크업이 261쌍 있었지만 **청크에는 남지 않습니다**(docling 이 표로
바꿔 줍니다). 규칙은 원문이 아니라 **청크 산출을 보고** 정하세요.

돌려 볼 수 있는 예시 3종이 `examples/text_cleanup/` 에 있습니다 — yaml 만 / `edit_output` 만 /
둘 다. 세 산출을 나란히 재고, 정제 후 마커가 0 인지와 `n_char` 가 어긋나지 않는지 단정합니다.

어느 방식이든 청크 본문이 바뀌므로 **재색인이 필요합니다.**

## 파이썬을 꽂을 수 있는 자리 3곳

훅 말고도 **설정에서 이름을 부르면 실행되는** 자리가 셋 있습니다. 훅보다 좁고 정확해서,
해당되면 이쪽이 먼저입니다.

| 자리 | 무엇을 꽂나 | 어떻게 |
|---|---|---|
| **값 추출 자체** | 정규식 추출, 사내 마스터 조회처럼 **LLM 이 아닌 방법** | custom_fields 의 `extractor: python` |
| 값 변환기 | 금액 파싱, 사번 → 부서명처럼 **사이트 전용 값 변환** | `tb.register_transform()` 후 yaml `transforms:` 에서 이름으로 |
| LLM 출력 파서 | 표준 JSON 이 아닌 응답 해석 | custom_fields yaml 의 `parser: {type: python, file, callable}` |
| 라우트 | 표준 포맷으로 못 바꾸는 원천 | 위 [ROUTES 절](#그래도-안-되면--라우트를-직접-씁니다) |

세 자리 모두 **파일은 config yaml 과 같은 폴더 아래**에 둡니다(경로 탈출은 거부됩니다).

### 값 추출을 파이썬으로 — `extractor: python`

문서에서 값을 뽑는 방법이 LLM·엑셀 열 매핑·JSON 키 매핑 셋뿐이었습니다. 계약번호처럼
**규칙이 분명한 값**은 LLM 에 물을 이유가 없고, **사내 시스템에 조회해야 채워지는 값**은
LLM 이 아예 알 수 없습니다. 그럴 때 씁니다.

```yaml
# custom_field_contract.yaml
schema: v2
source: {kind: document}
python:
  file: contract_extract.py     # 이 yaml 과 같은 폴더
  callable: extract             # 기본값 extract
  out: [CONTRACT_NO, AMOUNT]
fields:
  AMOUNT: {transform: [to_int]} # 값 파이프라인은 llm 과 완전히 같습니다
  SRC: {const: REGEX}
```

```yaml
# 프로세서 설정의 등록 블록
  - custom_fields:
      enable: true
      doc_type: contract
      extractor: python
      config_file: custom_field_contract.yaml
```

```python
# contract_extract.py
import re

CONTRACT = re.compile(r"계약번호[:\s]*([A-Z0-9-]+)")

def extract(text, document=None, output_fields=None, **kwargs):
    m = CONTRACT.search(text or "")
    return {"CONTRACT_NO": m.group(1) if m else None, "AMOUNT": ...}
```

- **돌려주는 것은 `dict` 하나**입니다. 그 뒤는 `extractor: llm` 과 **완전히 같은 경로**를
  탑니다 — `out` 으로 필드를 거르고, `default → const → value_map → transform → derive`
  순서로 값을 다듬어 문서에 싣습니다. 설정으로 하던 변환과 어긋나지 않습니다.
- `async def` 로 써도 됩니다(사내 API 조회). 인자가 부담스러우면 `def extract(text)` 만
  선언해도 불립니다.
- 파일이 없거나 함수 이름이 틀리면 **기동에서 실패**합니다. 첫 요청까지 미루지 않습니다.
- `url`·`system_prompt` 같은 LLM 전용 키는 이 extractor 에서 **쓸 수 없습니다**(기동 실패).
  표 설명(`table_text_description`) 융합도 LLM 경로 전용이라 함께 돌지 않습니다.
- v1 표기로는 `python:` 블록 대신 `file`·`callable`·`output_fields` 를 최상위에 씁니다.

돌려 볼 수 있는 예시는 `examples/facade_hooks/custom_field_regex_demo.yaml` 과
같은 이름의 `.py` 입니다.

### 값 변환기 등록

`custom_field_*.yaml` 의 `transforms:` 는 등록된 이름만 받습니다. 사이트 전용 변환은
**core 를 고치지 말고** 전처리기 파일 최상위에서 등록하세요 — core 를 고치면 릴리스
통째 갱신에서 사라집니다.

```python
tb.register_transform("won_to_int", lambda v: int(str(v).replace(",", "").replace("원", "")))
```

```yaml
transforms:
  AMT: [won_to_int]        # '1,200원' -> 1200
```

설정으로 하던 변환과 **같은 파이프라인**을 타므로 `value_map` · `derive` 와 순서가
어긋나지 않습니다. 인자가 필요한 변환은 클로저로 감싸 인자 없는 함수로 만드세요.

### LLM 출력 파서

`extractor: llm` 의 응답이 표준 JSON 이 아니면 설정 디렉터리 안의 파이썬으로 해석합니다.
파일은 config yaml 과 **같은 폴더 아래**에 두어야 합니다(경로 탈출은 거부됩니다).

```yaml
parser:
  type: python
  file: my_parser.py       # config yaml 과 같은 폴더 기준
  callable: parse          # 기본값 parse
```

```python
def parse(llm_output, output_fields=None, **kwargs) -> dict:
    return {...}           # dict 를 돌려주지 않으면 기동에 실패합니다
```

## toolbox — 이미 있는 기능을 씁니다

```python
from genon.preprocessor.facade.core import toolbox as tb
```

**직접 구현하기 전에 여기부터 보세요.** 값 변환기는 yaml 의 `transform:` 이 부르는 것과
**같은 함수**라, 설정으로 하던 변환과 코드로 하는 변환이 어긋나지 않습니다.

| 갈래 | 항목 |
|---|---|
| 값 변환 | `regex_sub` `regex_extract` `to_int` `to_json` `truncate` `html_text` `text` `date_int` `date_int_flex` `text_norm` `json_to_markdown` |
| 엑셀 | `load_sheets` `load_tables` |
| JSON | `collect_text_fields` `detect_format` |
| 표 | `render_table` `render_plain_text` `sanitize_table_html` |
| 텍스트 | `sanitize` `tidy` `read_text_with_fallback` |
| md·html | `promote_markdown_marker_headings` `unfence_text` `precheck_html` `marker_heading_match` |
| 청크 메타 | `set_chunk_metadata` + 예약 키 4개 |
| 청크 통계 | `refresh_stats` (edit_output 로 본문을 고쳤을 때) |
| 확장 등록 | `register_transform` (사이트 전용 값 변환기) · `make_elements` (커스텀 라우트 산출) |
| edit_chunk 반환 | `DROP` (이 청크를 버린다) |
| 단독 실행 | `mock_request` (서버 없이 클래스를 직접 부를 때 첫 인자) |

## 고쳤으면 확인합니다

전처리기 파일은 **그 자체로 실행됩니다.** 서버를 띄우지 않고 한 건만 돌려 볼 수 있습니다.

```bash
python preprocessor.py 지점현황.xlsx --doc-type branch_list -o parsed.json   # 파서
python preprocessor.py parsed.json -o chunks.json                            # 청커
```

`--doc-type` 을 꼭 주세요. 훅이 전부 `doc_type` 게이팅이라 없으면 훅이 안 탑니다.

산출을 코드로 바로 들여다보려면 클래스를 직접 불러도 됩니다.

```python
import asyncio, json
from genon.preprocessor.facade.core import toolbox as tb
from preprocessor import DocumentProcessor

# 파서 — 산출은 dict 입니다
result = asyncio.run(DocumentProcessor()(tb.mock_request(), "지점현황.xlsx",
                                         doc_type="branch_list"))
with open("parsed.json", "w", encoding="utf-8") as fp:
    json.dump(result, fp, ensure_ascii=False, indent=2)

# 청커 — 산출은 vector_meta 객체 목록이라 model_dump() 를 거칩니다
metas = asyncio.run(DocumentProcessor()(tb.mock_request(), "parsed.json",
                                        doc_type="branch_list"))
with open("chunks.json", "w", encoding="utf-8") as fp:
    json.dump([m.model_dump() for m in metas], fp, ensure_ascii=False, indent=2)
```

첫 인자는 FastAPI 의 `Request` 자리입니다. 청커가 미디어 업로드에 쓰므로 `None` 을 주면
거기서 에러가 납니다. `tb.mock_request()` 를 넘기세요. 설정 yaml 을 고르려면
`DocumentProcessor(config_path="...yaml")` 로 만듭니다.

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

훅 시그니처(`edit_input` / `edit_document` / `edit_chunk` / `edit_output`)와 `ROUTES` 형태는
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
