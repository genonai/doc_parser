# 08. 고객 개발자용 facade — 100줄 표면과 확장 훅

전제: [01](01-chunking-dead-code.md)~[06](06-new-doctype-drill.md) 완료.
**[07](07-customer-change-lifecycle.md) 의 A 결정을 이 문서가 A3 로 확정한다.**

## 이 단계가 기존 계획과 다른 점

README 의 원칙 2 는 "새 설정 키, 새 확장 메커니즘, 새 플러그인 지점을 만들지 않는다" 였고
04~06 은 그 전제 위에서 합격 판정을 받았다. **이 단계는 그 원칙을 의도적으로 깬다.**
고객 요구사항이 확장 메커니즘 신설 그 자체이기 때문이다.

그래서 04~06 의 연장이 아니라 별도 단계로 세우고, 골든 56케이스 차이 0 을 매 커밋 관문으로
다시 건다. 신설하는 메커니즘은 **훅 4개 + ClassVar 2개 + toolbox 1개**로 한정한다.
그 밖은 전부 이동이다.

## 고객 요구사항

1. 100줄 이내의 코드 — **초기 전달 템플릿 기준.** 고객이 추가한 코드는 무관
2. 내부 코어 코드를 보고 싶지 않음
3. 최종 호출 메서드와 관련된 최소한의 코드만
4. 신규 문서 추가 가능 (json, xlsx, md, html)
5. custom_fields 로 안 되는 처리를, **코어를 고치지 않고** facade 의 전처리·후처리로

## 확정된 결정

| # | 결정 | 근거 |
|---|---|---|
| D1 | 엑셀도 **데이터 형태**로 훅에 넘긴다 | `xlsx_processor.load_sheets` 가 이미 중립 표현을 만든다 |
| D2 | `__call__` 을 facade 에 노출한다 | 진입점과 단계 순서가 보여야 한다 |
| D3 | `GenOSVectorMeta` 24필드를 facade 에 남긴다 | 적재 DB 컬럼은 사이트마다 다르다 |
| D4 | 대상은 parser·chunker **2종만** | 5종을 한 번에 옮기면 골든 범위와 위험이 3배 |
| D5 | 훅 호출을 **가능한 한 `__call__` 에서** 한다 | 아래 "훅 호출 위치" |
| D6 | 엑셀 병합 정보는 **행·열 개수가 그대로일 때만** 유지 | 아래 "병합셀 규칙" |
| D7 | **07-A 는 A3** — 고객 소유 `preprocessor.py` 를 배포 범위에서 분리 | facade 가 얇아져 비로소 가능 |

### 훅 호출 위치 (D5)

4개 중 3개는 `__call__` 에서 부른다. `pre_source` 만 core 가 부르고 `__call__` 에는
배선으로 드러낸다.

**`pre_source` 를 올릴 수 없는 이유**

- 로드 시점이 확장자마다 다르다. `.json` 은 `route_json`, `.xlsx` 는 `route_tabular`,
  `.md`/`.html` 은 `route_docling` 안에서 읽힌다. `__call__` 시점에는 읽을 데이터가 없다.
- **미리 읽으면 동작이 바뀐다.** `_route_json` 은 custom_fields 가 매칭될 때만
  `_load_json_payload` 를 부르고, 미매칭이면 `return None` 으로 캐치올(TextLoader)에 넘긴다.
  eager 로드는 오늘 평문으로 잘 처리되는 깨진 JSON 을 예외로 죽인다.
  04 가 "이 작업의 유일한 위험"으로 꼽은 폴스루가 정확히 여기다.
- 올리려면 확장자별 로딩 분기가 facade 로 올라온다 — 요구사항 2 위반.

**대가:** 훅 호출이 고객이 지울 수 있는 코드가 된다. `__call__` 을 "웬만하면 손대지 않는
자리"로 문서에 명시하고 단계 번호 주석을 단다.

### 병합셀 규칙 (D6)

`load_tables` 는 멀티헤더 자동판정에 병합범위 **좌표**(`(r0,c0,r1,c1)`)를 쓴다. 고객이 행을
지우면 좌표가 어긋난다.

| 고객이 한 일 | 병합 정보 | 결과 |
|---|---|---|
| 값만 고침 (행·열 개수 그대로) | 유지 | 멀티헤더 자동판정 그대로 동작 |
| 행·열을 지우거나 더함 | 폐기 | 헤더 판정이 단순 모드. `formats.xlsx.header_row` 로 지정 |

"무조건 폐기"로 하면 값만 고친 사용자도 컬럼명이 나빠진다(`연락처_전화` → `전화`).
좌표가 여전히 유효한 경우까지 버릴 이유가 없다.

### A3 배포 (D7)

```
1회차   벤더: core/** + preprocessor_template.py
        고객: template → preprocessor.py 로 복사, 자기 코드 추가
2회차~  벤더: core/** 덮어씀 + preprocessor_template.py 덮어씀
        고객: preprocessor.py 그대로 — 아무것도 안 해도 된다
```

07 이 A3 를 "facade 는 파일 하나라 분리 불가"로 기각한 것은 벤더 로직과 고객 수정이 한
파일에 섞여 있었기 때문이다. 벤더 로직이 `core/` 로 빠지면 분리가 성립한다.

**A3 의 대가 두 가지**

1. **훅은 고정 API 가 된다.** `pre_source(ext, doc_type, data, work_dir)` 등의 시그니처를
   나중에 바꾸면 고객 파일이 깨진다.
2. 템플릿이 바뀌면 릴리스 노트에 **"템플릿 변경 있음/없음"** 을 표시한다.

## 산출 구조

```
facade/
  preprocessor_template.py       벤더 배포. 고객이 한 번 복사해 preprocessor.py 로
  core/
    parser.py                    parser_processor.py 에서 이동 (~1,569줄)
    chunker.py                   chunking_processor.py 에서 이동 (~1,140줄)
    errors.py                    GenosServiceException 1벌 (현재 활성 경로 2벌)
    toolbox.py                   기존 기능 재수출 (내용은 08-A 가 결정한다)
  common/ chunking/ enrichment/ guardrail/ serialize/    그대로
```

`sync-serving-repo.sh` 의 whitelist 가 `genon` 전체라 `core/` 도 자동으로 배포된다.
고객이 "안 본다"는 뜻이지 "안 받는다"가 아니다.

## 훅 계약

| 훅 | 호출 지점 | 받는 것 |
|---|---|---|
| `pre_source` | core 가 확장자별 로드 직후 | `.json` dict/list(깨졌으면 str) · `.md/.html` str · `.xlsx/.csv` dict[시트, 2차원 행] · 그 밖 파일 경로 |
| `post_parse` | `__call__`, 응답 확정 직전 | 응답 dict (`elements` / `document` / `metadata`) |
| `pre_chunk` | `__call__`, 분할 직전 | `("docling", DoclingDocument)` 또는 `("parse", list[dict])` |
| `post_chunk` | `__call__`, 응답 직전 | `list[GenOSVectorMeta]` |

**축을 문서에 명시한다 — 고객이 가장 헷갈릴 지점이다.**

```
pre_source   매핑 "전"  — 원천 키·원천 값
post_parse   매핑 "후"  — 목표 필드명·최종 값
```

`require` / `on_missing: skip` 을 훅으로 재현할 때 `pre_source` 에서 하면 판정 기준이
원천 키가 되어 의미가 달라진다. **`post_parse` 에서 elements 를 거르는 것이 맞다.**

### 산출 동일성 보장

| 훅 | 장치 |
|---|---|
| `post_parse` `pre_chunk` `post_chunk` | 항상 부른다. 기본 구현이 받은 값을 그대로 돌려주므로 산출 불변 |
| `pre_source` | core 가 `pre_source.__func__ is ParserCore.pre_source` 로 판정. 손대지 않은 기본형이면 `.md/.html` 원문 읽기·엑셀 격자 로드를 건너뛴다. 오버라이드했더라도 **받은 객체를 그대로 돌려주면**(`out is data`) 파생 입력을 만들지 않는다 |

## core 변경 목록

| 대상 | 작업 |
|---|---|
| `facade/core/parser.py` | 신설. 이동 + `run()` `cli()` `resolve_ext()` `resolve_doc_type()` + 훅 호출부 4곳 |
| `facade/core/chunker.py` | 신설. 이동 + `run()` `cli()` `load_input()` |
| `facade/core/errors.py` | 신설 |
| `facade/core/toolbox.py` | 신설. **내용은 08-A 가 결정한다** |
| `converters/xlsx_processor.py` | `normalize_sheets()` `sheets_to_xlsx()` 추가, `load_tables(sheets=)` 주입 인자 |
| `common/` | JSON 인코딩 폴백 `read_text_with_fallback()` (utf-8-sig → utf-8 → cp949) |
| `pyproject.toml` | `openpyxl` `pandas` `beautifulsoup4` 명시 선언 — 셋 다 미선언인데 코드가 직접 import 한다 |
| `facade/test.py` | parser·chunker 는 `cli()` 로 대체. 나머지 3종용으로 존치 |
| 테스트 | 모듈 레벨 참조 재타겟 10~20곳(`_classify_payload` `_build_header_line` `enrich_document` 등). 인스턴스 메서드 호출 24종은 상속으로 그대로 동작 |

## 단독 실행

facade 에는 2줄만 들어간다.

```python
if __name__ == "__main__":
    DocumentProcessor.cli()
```

```bash
python preprocessor.py 지점현황.xlsx --doc-type branch_list -o parsed.json   # 파서 배포본
python preprocessor.py parsed.json -o chunks.json                            # 청커 배포본
```

`--doc-type` 이 필수다. 훅이 전부 doc_type 게이팅이라 없으면 훅을 테스트할 수 없다.

## 검증 — 설정 최소화 ablation (08-A / 08-B)

**이 검증이 이 단계의 성공을 판정한다.** 줄 수가 아니다.

### 무엇을 재는가

출고 doc_type 의 custom_fields yaml 을 **초기 개발 단계 수준으로 깎았을 때**, 리팩터링된
facade 와 toolbox 만으로(+ 고객 코드 약간) 같은 산출이 나오는가.

### 2회 실행 — 순서가 중요하다

| | 시점 | 대상 코드 | 산출 |
|---|---|---|---|
| **08-A** | **08-1 착수 전** | **현행 `parser_processor.py` 그대로** | toolbox 요구 명세 · 기본 기능 집합 확정 |
| **08-B** | 08-3 완료 후 | 리팩터링된 facade + toolbox | 최종 판정 |

08-A 는 리팩터링이 필요 없다. yaml 만 깎고 "차이를 파이썬으로 메우면 몇 줄이고 어떤 함수가
필요한가"를 재면 된다. **그 목록이 곧 toolbox 설계서다.** 08-A 없이 toolbox 를 만들면
추측으로 짓게 된다.

### 기본 기능 집합 — 실측 근거

자의적으로 정하면 결론도 자의적이 된다. 두 실측으로 정했다.

**기준 A — 최초 구현 시점** (`git log -S'<키>' -- facade/enrichment/`)

```
2026-05-29  alias  fields  default  transform  values      ← 최초 커밋일
2026-06-08  template          2026-06-16  split
2026-07-09  body
2026-08-04  const  require
2026-08-26  on_missing        2026-08-27  ignore_keys  sections
2026-08-30  order_by  labels
2026-09-03  records_at  merge_rows  mirror_to
```

**기준 B — 사용 폭** (출고 17개 파일 / 2,160줄)

```
alias 85  fields 38  default 37  source 17  const 16  transform 15  body 15
require 11  labels 10  split 8  values 7  on_missing 6  pre 4  once 4
records_at 3  merge_rows 1  order_by 1  mirror_to 1  ignore_keys 1  sections 1
```

**3계층**

| 계층 | 키 | 판정 |
|---|---|---|
| **기본** (첫날 + 광범위) | `schema` `kind` `source` `fields` `alias` `default` `transform` `values` | 남긴다 |
| **경계** (중기 + 사용 많음) | `const` `body` `require` `labels` `split` `template` | **08-A 가 결정한다** |
| **고급** (후발 + 희소) | `on_missing` `ignore_keys` `sections` `order_by` `mirror_to` `records_at` `merge_rows` `llm.pre/once/repeat` `markdown.*` `marker_headings` `exclude_text_fields` | 제거 대상 |

`transform` 과 `values` 가 첫날부터 있던 기능이라는 것이 조사의 정정 사항이다. 초판 분류는
둘을 고급으로 잘못 넣었다.

**08-A 는 가장 공격적인 구성(기본만)으로 한 번만 돌린다.** 기본만으로 통과하면 경계 포함은
자동으로 통과한다. 실패 지점이 곧 경계 계층의 확정이다.

### extractor 분포 (대상 선정 근거)

| extractor | 개수 | doc_type |
|---|---:|---|
| `tabular_mapping` | 6 | cs_slf cs_ssf faq menu stock_insight term |
| `llm` | 5 | card cs_hpp product_hpp product_slf product_ssf |
| `json_mapping` | 4 | cs_sss faq_json monimo_event monimo_news |
| `json_semantic` | 1 | product_hpp_semantic |
| 문서모드(`json.text_fields`) | 1 | research_report |

### 난이도 군 — 실행 순서

| 군 | 고급키 | doc_type | 기대 |
|---|---:|---|---|
| **1군** | **0줄** | card, cs_slf, cs_ssf | **훅 0줄로 차이 0 이어야 정상.** 어긋나면 하네스나 리팩터링이 틀린 것 — 조기 경보 |
| 2군 | 1~4줄 | cs_hpp cs_sss faq faq_json menu product_hpp product_hpp_semantic product_slf product_ssf term | |
| 3군 | 6~8줄 | monimo_event(8) stock_insight(7) monimo_news(6) | |

### 합격 기준 4개

| 기준 | 목표 |
|---|---|
| 산출 동일성 | 파싱 **및 청킹**까지 차이 0 |
| 고객 코드량 | doc_type 당 30줄 이하 |
| **toolbox 밖 import 0** | `facade.enrichment.*` 를 직접 import 했다면 **toolbox 결손** |
| core 수정 0 | 고쳐야 했다면 그 기능은 yaml 에 되돌린다 |

세 번째가 toolbox 완성도를 재는 유일한 지표다.

### 결과 해석 규칙

이 검증의 산출은 합격/불합격이 아니라 **기본 기능 집합의 확정**이다.

| 결과 | 해석 |
|---|---|
| 훅 20줄로 됐다 | 그 기능은 빼도 된다 |
| 훅 200줄로 됐다 | 됐지만 기본 집합으로 되돌린다 |
| toolbox 밖 import 필요 | toolbox 에 추가 |
| core 수정 필요 | 그 기능은 yaml 에 남긴다 |

### LLM 산출을 대조할 때의 함정

5종이 `extractor: llm` 이다. 계획 검증 이력이 이미 지적했다 —
**"LLM soft-fail 이 빈 값으로 일치를 만든다."** 양쪽이 다 실패해 둘 다 비면 차이 0 으로
통과한다. 세 가지를 건다.

1. `llm_cache` 스코프를 record/check 와 **동일하게** (00 결정 2). 스코프를 나누면 2회차가
   LLM 을 다시 불러 그 흔들림까지 노이즈로 잡힌다 — 실측 58개 산출물이 어긋난 전례
2. doc_type 마다 **"비면 실패" 필드 목록**을 명시한다. 빈 값 일치는 통과로 세지 않는다
3. 노이즈 기준선 제외 — 같은 버전 2회 실행에서 흔들리는 것은 `reg_date` 하나(실측)

### 배포 설정 오염 금지

06 드릴과 같은 방식. `resource_dev/` 를 임시 디렉터리로 복사한 뒤 최소 yaml 을 그 사본에만
등록하고 `--config` 로 넘긴다. 골든이 흔들리지 않는다.

### 커버리지 실측

`parse_chunk_verify.py` CASES 대비 출고 17종.

| doc_type | 상태 |
|---|---|
| `faq_json` | `doc_type: faq` 로 선언 — faq 케이스가 json 샘플과 함께 덮는다 |
| `product_hpp_semantic` | `doc_type: product_hpp` — json_semantic 케이스 **4건** 존재(풀 캡처/최소/rich_table/fields) |
| **`research_report`** | **샘플 없음.** `enable: true`, extractor `llm` + `json.text_fields` 문서 모드 |

**미커버는 `research_report` 하나이고, 대상에서 제외한다 — 고객이 더 이상 쓰지 않는
doc_type 임을 확인했다(2026-09-06).** 조사 과정에서 함께 드러난 사실:

- `resource_dev/` 에 등록도 yaml 파일도 없다. dev 등록 14종과 `parse_chunk_verify.py`
  CASES 14종이 정확히 일치하며 `research_report` 는 양쪽에 없다. 검증 경로에서 한 번도
  실행된 적 없는 출고 전용 설정이었다.
- `json.text_fields` 문서 모드는 `card`(1군)·`product_hpp`(2군)·`research_report` 3곳에
  있고 앞 둘이 이미 검증 대상이다. **빼도 문서 모드 커버리지 공백은 없다.**
- **`resource/parser_processor_config.yaml:361` 이 아직 `enable: true` 다.** 폐기된
  doc_type 이 출고 설정에 살아 있다 — 이번 작업 범위 밖이므로 별도 이슈로 분리한다.

**08-A 는 16종으로 진행한다.**

## 단계

| 단계 | 내용 | 관문 |
|---|---|---|
| 08-0 | **완료** — 기본 집합 3계층 확정. `research_report` 는 dev 미등록으로 대상 제외(16종) | — |
| **08-A** | **완료** — 1차 ablation ([결과](08-A-ablation-results.md)). 하네스 결함 2건 수정, 메울 차이 8건 특정 | toolbox 요구 명세 산출 |
| 08-1 | `core/errors.py` + `core/parser.py` 이동 (훅 없이 순수 이동) | 골든 56 차이 0 |
| 08-2 | `core/chunker.py` 이동 + 빌더 제거 | 골든 56 차이 0 |
| 08-3 | 훅 4개 + `cli()` + toolbox + xlsx 데이터 훅 | 훅 미정의 시 골든 차이 0 |
| **08-B** | **2차 ablation (리팩터링본)** | 합격 기준 4개 |
| 08-4 | 신규 문서 드릴 — json(06 재실행) · xlsx · md · html | 포맷별 facade 1파일 · 30줄 이하 |
| 08-5 | `preprocessor_template.py` · gitbook 갱신 · 릴리스 노트 규칙 | — |

08-1/08-2 는 순수 이동이라 위험이 낮다. **실질 판정은 08-B 와 08-4 다.**

## 알려진 결손 (08-A 가 확정한다)

| 결손 | 내용 | 잠정 대응 |
|---|---|---|
| LLM 호출기 | `_call_llm` 이 `custom_fields_enricher.py:557` 과 `metadata_enricher.py:225` 에 각각 private 으로 복제. 공용 진입점 없음 | 08-A 가 요구하면 `tb.ask_llm()` 신설 |
| front_matter / text_fence | 모듈이 Spec 기반이라 훅에서 부를 함수가 없다 | `tb.split_front_matter()` `tb.unfence_text()` 래퍼 |
| `json_semantic` | 1,014줄. 훅으로 재현 불가 | **기본 집합에 남기는 것이 정답일 가능성이 높다** |
| 경계 ⑤ (05) | sections/document 표 평문화. `transform_html_text` 는 `html_renderer` 를 받는데 설정 호출부가 안 넘긴다 | 훅에서 고객이 직접 렌더러를 넘기면 **우회 가능**. 근본 해법(core 호출부 주입)은 별도 이슈 |

## 범위 밖

- `intelligent_processor.py` `convert_processor.py` `attachment_processor.py` — 09 로
- `facade/legacy/` — 별도 배포 단위
- `docling/` — wheel 재빌드 강제
- 동작 변경 · 성능 개선 · 조사 중 발견되는 곁가지 결함
- 05 경계 ⑤의 근본 수정 — 별도 이슈

## 위험

- **훅 호출이 `__call__` 에 있어 고객이 지울 수 있다** — 명시성과 맞바꾼 대가. 문서로 완화
- **훅이 고정 API 가 된다** — A3 의 대가. 시그니처를 신중히 확정할 것
- **docling 모드 엑셀 라운드트립은 서식·이미지·원본 병합을 잃는다** — 훅을 건드릴 때만 발생
- **골든이 3c 의 절반을 실행하지 않는다**(검증 이력) — `--output-format` 축은 00 에서 해결됨.
  xlsx/md/html 신규 훅 지점이 56케이스에 있는지 08-3 착수 전 확인
- 청커 템플릿이 97줄로 여유 3줄 — 100줄은 초기 템플릿 기준이므로 초과해도 무방
