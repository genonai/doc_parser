# 06. 신규 문서 실전 드릴 — 실행 결과 (2026-09-06)

[06-new-doctype-drill.md](06-new-doctype-drill.md) 의 절차대로 돌린 기록이다.
**이 결과가 01~05 리팩터링의 최종 판정이다** — 줄 수가 아니라 이것이 기준이다.

## 재현

```bash
# 실행 위치: genon/preprocessor
.venv/bin/python examples/parse_chunk/drill/make_drill_fixtures.py   # 픽스처 15개 생성
.venv/bin/python examples/parse_chunk/drill/run_drill.py --step raw     # 1단계
.venv/bin/python examples/parse_chunk/drill/run_drill.py --step config  # 2단계
```

드릴은 **배포 설정을 건드리지 않는다.** `resource_dev/` 를 임시 디렉터리로 복사한 뒤
드릴 설정만 덧붙여 등록하고 그 사본을 `--config` 로 넘긴다(그래서 골든이 흔들리지 않는다).

## 합격 판정

| 기준 | 목표 | 실측 | 판정 |
|---|---|---|---|
| 고쳐야 하는 파일 | facade **1개** | `parser_processor.py` 1개 | **통과** |
| 고치는 위치 | 04 가 정한 "고칠 자리" 안 | 전부 `_load_json_payload`(파일 머리 앵커 3번) | **통과** |
| 변경 줄 수 | 30줄 이하 | `git diff --numstat -- genon/preprocessor/facade` → **27 삽입 / 3 삭제** | **통과** |
| 기존 doc_type 산출 | 골든 차이 0 | 56케이스 차이 0 | **통과** |
| 청커 산출 | 새 doc_type 의 metadata 가 청크에 실림 | 15/15 `custom_fields_row` → 행 1개 = 청크 1개 | **통과** |
| 원인 파악 | 파일 전체를 읽지 않고 도달 | 파일 머리 주석 → `_load_json_payload` 한 번에 | **통과** |

**공용 모듈은 한 줄도 건드리지 않았다.** 계획이 정한 실패 조건("공용 모듈을 건드려야 하면
04 의 실패")에 해당하지 않는다.

## 픽스처별 결과

`sample_files/drill/` (생성 스크립트: `examples/parse_chunk/drill/make_drill_fixtures.py`)

| # | 변형 | 계획의 예상 | 1단계(그대로) | 2단계(설정만) | 3단계(코드) |
|---|---|---|---|---|---|
| B1 | 동명 키 충돌 — 필요한 것은 `meta.title` | 설정 불가 → 코드 | 캐치올 텍스트 1청크 | **틀린 값이 조용히 실린다** | ✔ 3줄 |
| B2 | 동적 키(상품코드가 key) | json_semantic 이면 설정 0줄 | — | **✔ 설정만으로 3청크** | 불필요 |
| B3 | 2단 중첩 `groups[].items[]` | 이름 검색으로 될 수도 | — | **3건 중 2건만**(첫 배열만) | ✔ 2줄 |
| B4 | 형제 `type` 값에 따라 본문이 갈림 | 배타적이면 template 로 우회 | — | **✔ `template` 결합으로 됨** | 불필요 |
| B5 | 문자열/숫자/배열/null 혼재 | 값 파이프라인으로 흡수 | — | **✔ regex_sub + to_int** | 불필요 |
| B6 | 표가 HTML 문자열 | `transform: html_text` 로 됨 | — | **✔ 표가 markdown 표로 보존** | 불필요 |
| B7 | 문서형 메타 + 레코드 배열 혼합 | 레코드 밖 공통 필드가 문제 | — | 레코드는 됨, **공통 필드는 null** | 미수행(경계 ④) |
| B8 | depth 8 | 이름 검색이 버티는지 | — | **✔ sections 자동 순회** | 불필요 |
| B11 | UTF-8 BOM / CP949 | 코드 필요(1줄) | **하드 실패** | 하드 실패 | ✔ 9줄 |
| B12 | JSONL/NDJSON | 코드 필요 | 러너가 확장자에서 컷 | 확장자는 별칭으로, **내용은 하드 실패** | ✔ 6줄 |
| B13 | null 값과 결측 키 | default/require 상호작용 | — | **✔ 4건 중 3건**(이름 없는 건만 skip) | 불필요 |
| B14 | 이스케이프된 중첩 JSON | `transform: text` 가 판별하는지 | — | **✔ 불릿 목록으로 평문화** | 불필요 |
| B15 | `records` 는 있는데 요소 0 | 에러 경로가 갈림 | — | **청커가 예외로 죽는다** | 별도 이슈(아래) |
| B16 | doc_type 충돌 | 즉시 예외, 전 케이스 기동 실패 | — | **요청 시점 예외, 그 doc_type 만** | — |
| B17 | 레코드 5,000건 | 비용·지연 축 | — | **✔ 5,000청크**(LLM 없음) | 불필요 |

**설정만으로 된 것 8개 / 코드가 필요한 것 4개 / 경계 확인 3개.**

## 3단계에서 실제로 고친 것

전부 `DocumentProcessor._load_json_payload` 안이다. 이 메서드가 `.json` 두 경로
(레코드 모드 `_parse_json_records`, 문서 모드 `_parse_json`)의 **유일한 입구**라서,
원천 구조 문제가 여기 한 곳으로 모인다.

```python
    def _load_json_payload(self, file_path, doc_type=None):
        raw = Path(file_path).read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp949"):        # B11 BOM / CP949
            try:
                text = raw.decode(enc); break
            except UnicodeDecodeError:
                continue
        try:
            payload = json.loads(text)
        except ValueError:                                  # B12 JSONL/NDJSON
            payload = {"rows": [json.loads(ln) for ln in text.splitlines() if ln.strip()]}
        dt = normalize_doc_type(doc_type)
        if dt == "drill_b1":                                # B1 동명 키 충돌
            for item in payload.get("items", []):
                item["title"] = (item.get("meta") or {}).get("title") or item.get("title")
        elif dt == "drill_b3":                              # B3 2단 중첩 레코드
            payload = {"items": [i for g in payload.get("groups", []) for i in g.get("items", [])]}
        return payload
```

**doc_type 게이팅이 핵심이다.** B1·B3 정규화가 게이팅 없이 들어갔다면 모든 JSON
doc_type 의 산출이 바뀌어 골든이 즉시 깨졌을 것이다. 게이팅이 있어서 골든 56케이스가
차이 0 으로 통과했다 — 이것이 04 가 `_load_json_payload` 시그니처에 `doc_type` 을 넣은 이유다.

B11·B12 는 게이팅이 없다(모든 JSON 에 적용). 인코딩 판별과 JSONL 폴백은 **기존에
하드 실패하던 입력만** 살리고 정상 UTF-8 JSON 의 경로를 바꾸지 않기 때문이다.
골든이 그 사실을 확인했다.

**이 수정은 커밋하지 않는다.** 드릴은 "가능한가" 를 검증하는 것이지 신규 doc_type 을
추가하는 것이 아니다. 남기는 것은 픽스처·설정·절차·이 기록이다.

## 계획과 달랐던 것 4가지

### 1. B16 의 폭발 반경이 계획보다 작다

계획은 "설정 로드 시점 예외 → 전 케이스 기동 실패" 로 봤다. **실측은 요청 시점이고
그 doc_type 만 죽는다.**

```
GenosServiceException: 동일 doc_type 에 json_mapping 설정이 여러 개인데 records 키가
겹칩니다(drill_b1, records=['items', 'items']). 배열마다 다른 records 키를 주거나
설정 하나를 지우세요.
```

같은 설정 상태에서 `drill_b5` 는 정상 처리됐다. 즉 고객이 설정을 잘못 추가해도
**남의 doc_type 은 죽지 않는다.** 계획이 걱정한 "기존 doc_type 을 죽이는 유일한 경로" 는
생각보다 안전하고, 메시지가 원인과 조치를 그대로 알려 준다.

### 2. B1 은 실패하지 않고 **틀린 값을 조용히 싣는다**

설정만으로 돌리면 청크가 2건 나오고 `TITLE` 도 채워진다 — 다만 값이
`목록 항목 1`(얕은 쪽)이지 `청년 우대 적금`(필요한 쪽)이 아니다. 에러도 경고도 없다.

**"청크가 나왔다" 는 검증 기준이 될 수 없다.** 이 드릴 러너도 처음엔 "필드가 비지
않았는가" 만 봐서 B1 을 OK 로 오판했다. 고객 문서의 "조용히 실패하는 자리" 절에
이 사례를 넣어야 한다.

### 3. B3 은 되다 말았다 — `records_at` 은 최초 매칭 1개다

`groups[].items[]` 에서 `records_at: items` 는 **첫 그룹의 items 만** 잡는다.
3건 중 2건이 나오고 나머지는 말없이 빠진다. 이것도 에러가 아니다.

설정 우회(매퍼 2개 등록)는 `records_at` 이 서로 달라야 한다는 규칙에 막힌다 —
둘 다 `items` 이므로 등록 자체가 거부된다. **코드가 필요한 케이스가 맞다.**

### 4. B15 는 "에러 경로가 갈리는" 정도가 아니라 청커가 죽는다

`{"noticeList": []}` 는 정상 입력인데(레코드가 없는 날의 피드) `_chunk_text_elements`
가 `GenosServiceException("chunk length is 0")` 을 던진다. 파서는 element 0개를
정상 반환하고 청커에서 터진다.

**별도 이슈 후보다.** 이번 드릴 범위(파서 facade 수정)가 아니고, 고치려면 청커
facade 를 손대야 한다. 운영에서는 "어제는 되던 피드가 오늘 0건이라 500" 으로 나타난다.

## 그 밖에 드러난 것

- **B5**: 배열 값이 파이썬 repr 문자열로 실린다(`"['대출', '신용']"`). JSON 배열을
  metadata 로 그대로 싣는 경로의 표기가 확정돼 있지 않다. 별도 확인 필요.
- **B7**: 레코드 밖 공통 필드(`documentTitle`/`owner`)는 `alias` 로 못 읽는다(경계 ④).
  `const` 로 박거나 문서 모드(`json:`)로 가야 한다.
- **B12 는 두 겹이다.** 확장자(`.jsonl`)는 `formats.extension_aliases` 로 **설정 한 줄**로
  받을 수 있고, 그 다음 내용(줄 단위 JSON)에서 비로소 코드가 필요하다. "설정으로 어디까지" 의
  경계가 파일 하나 안에서도 갈린다는 사례다.
- **러너도 원천을 읽는다.** `parse_chunk_test.py` 가 `.json` 입력을 먼저 `json.load` 해서
  파서 출력물인지 판별하는데, 여기서 utf-8 을 강제하면 BOM 원천이 **파서에 닿기도 전에**
  죽는다. 이번에 러너도 관대한 디코딩으로 바꿨다(제품 코드가 아니라 검증 도구다).

## 트랙 A — 기존 보유 문서

골든 56케이스가 매 단계 차이 0 으로 통과하고 있으므로 별도 실행을 하지 않았다.
그 안에 계획이 꼽은 복잡도 상위가 이미 들어 있다:
`monimo_product_hpp_wcms_sample.json`(depth 5 / 키 28), `monimo_event_real_sample.json`(키 35),
`monimo_product_hpp_rich_table_sample.json`, `monimo_cs_hpp_rich_table_sample.html`,
`.INC_235488_02_20260626103138.html.parsed`, `monimo_stock_insight_sample.xlsx`,
`html_tables.html`, `tablecell.docx`, `docx_sample.docx`, `pdf_sample.pdf`.

빠진 것은 `hwp_sample_table.hwp`(이 머신에 `convtext` 없음)와
`monimo_cs_hpp_large_table_sample.html` · `monimo_cs_hpp_marker_real_dom_sample.html`
(저장소에 없음)이다.

## 후속

| 항목 | 성격 |
|---|---|
| B15 — 빈 레코드 배열에서 청커가 예외 | **결함**, 별도 이슈 |
| B5 — 배열 metadata 의 repr 표기 | 확인 필요 |
| 05 경계 ⑤ — sections/document 의 표 평문화(렌더러 주입 한 줄) | 별도 이슈 1순위 |
| B1·B3 처럼 **조용히 틀린 값**이 되는 자리 | 고객 문서 "조용히 실패하는 자리" 에 반영 완료 |
