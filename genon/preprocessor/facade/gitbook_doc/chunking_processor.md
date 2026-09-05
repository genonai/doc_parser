# 청킹용 전처리기 매뉴얼

`chunking_processor.py` (`/chunker`) 레퍼런스입니다. **파싱 결과를 입력받아 청킹만** 합니다.

- 파싱은 하지 않습니다. OCR·레이아웃·enrichment 는 앞 단계(파서)에서 이미 끝났습니다.
- 그래서 이 프로세서의 설정에는 `ocr` / `layout` / `pdf_pipeline` / `enrichment` 섹션이 **없습니다.**
  적어도 읽지 않습니다.

## 목차

- [개요](#개요)
- [API](#api)
- [입력 — 두 가지 형태](#입력--두-가지-형태)
- [chunking_processor_config.yaml 설정](#chunking_processor_configyaml-설정)
- [청킹 동작 — split_only 와 resize_all](#청킹-동작--split_only-와-resize_all)
- [표 처리](#표-처리)
- [청크 텍스트 정제](#청크-텍스트-정제)
- [출력 스키마](#출력-스키마)
- [파서 → 청커 element 계약](#파서--청커-element-계약)
- [코드에서 고칠 자리](#코드에서-고칠-자리)
- [자주 겪는 증상](#자주-겪는-증상)

---

## 개요

| | |
|---|---|
| 파일 | `genon/preprocessor/facade/chunking_processor.py` |
| 마커 | `IS_CHUNKER = True` (이게 없으면 `/chunker` 요청이 거부됩니다) |
| 설정 | `resource/chunking_processor_config.yaml` (개발 시 `resource_dev/` 가 우선) |
| 청킹 엔진 본체 | `facade/chunking/smart_chunker.py` — **공용 모듈** |

> **엔진은 이 파일 안에 없습니다.** facade 의 `GenosSmartChunker` 는 동작 옵션(ClassVar)만
> 지정하는 얇은 서브클래스이고, 실제 분할·병합 로직은 `facade/chunking/smart_chunker.py`
> 한 벌에 있습니다. 거기를 고치면 `/chunker` 와 `/preprocess*`(적재)가 **함께** 바뀝니다.

### 배포 단위가 파서와 다릅니다

외부 사이트형 배포는 facade **한 개**만 `preprocessor.py` 로 마운트합니다. `IS_PARSER` 는
파서에만, `IS_CHUNKER` 는 청커에만 있으므로 **한 서빙이 파싱과 청킹을 둘 다 할 수 없습니다.**
파싱 서빙과 청킹 서빙 **두 개**를 운용하고, 설정도 두 벌을 각각 갱신합니다.

특히 아래 둘은 **청커 쪽 yaml 에 적어야** 효과가 있습니다.

- `chunking.*` 전부 (`chunk_size`·`chunk_mode`·`include_chunk_header`·`text_cleanup` …)
- `output.*` — 청크 텍스트 안의 표 표기형태

---

## API

`POST /preprocessor/{id}/run` (통합 실행 시 `POST /chunker`)

| 파라미터 | 위치 | 설명 |
|---|---|---|
| `file_path` | body | 파서 결과 JSON 파일 경로. `document` 를 인라인으로 주면 생략 가능 |
| `params.document` | body | 파서 응답의 `data` 를 그대로. **인라인이 우선** |
| `params.chunk_size` | body | 청크 상한. yaml 보다 우선 |
| `params.chunk_mode` | body | `split_only` / `resize_all` |
| `params.include_chunk_header` | body | `HEADER:` 라인 on/off (0/1 또는 on/off) |
| `params.table_as_chunk` | body | 표를 독자 청크로 |
| `params.text_cleanup` | body | `off` / `safe` |
| `params.doc_type` | body | 문서유형 스탬프(파서가 이미 실어 보냈으면 불필요) |

반환은 청크(=적재 벡터) **리스트**입니다.

---

## 입력 — 두 가지 형태

청커는 **`file_path` 의 확장자가 아니라 payload 의 모양**으로 경로를 정합니다.

| payload | 판별 | 경로 |
|---|---|---|
| `{"document": {...}}` 또는 DoclingDocument dict | `document` 를 **먼저** 검사 | **docling 경로** — 구조 인식 청킹 |
| `{"elements": [...]}` | 위가 아닐 때 | **parse-format 경로** — 문자 기반 분할 |

> 파서 응답은 빈 `"elements": []` 를 함께 담기 때문에 검사 순서가 중요합니다. 순서가 바뀌면
> docling 문서가 문자 기반 splitter 로 흘러가 청킹 품질이 크게 떨어집니다.

두 경로의 차이:

| | docling 경로 | parse-format 경로 |
|---|---|---|
| 청커 | `GenosSmartChunker` (섹션·표 구조 인식) | 문자 기반 splitter |
| 크기 단위 | `char` / `huggingface` 선택 | **항상 문자 수** |
| overlap | 없음 | `chunking.recursive.chunk_overlap` (기본 100) |
| 좌표·미디어 | 실제 값 | `"."` 고정값 |
| 문서 metadata | 부착됨 | 없음 (행 단위 element 는 예외 — 아래 계약 참조) |

**파서가 `output.format: docling` 으로 내보내야 docling 경로를 탑니다.** 파서 설정이
`json` 이면 청커는 parse-format 경로로 갑니다.

---

## chunking_processor_config.yaml 설정

섹션은 5개뿐입니다: `defaults` / `chunking` / `table_image` / `output` / `guardrail`.

### `chunking`

| 키 | 기본 | 의미 |
|---|---|---|
| `chunk_size` | `1000` | 청크 상한. **0 = 분할 안 함.** docling 경로는 0 초과 시 **최소 1024 로 보정**됩니다 |
| `chunk_mode` | `split_only` | 아래 절 참조 |
| `table_as_chunk` | `true` | 표를 본문과 섞지 않고 독자 청크로 |
| `include_chunk_header` | `true` | 청크 선두 `HEADER: <섹션 경로>` 라인 |
| `text_cleanup` | `off` | `safe` 로 켜면 문자 노이즈 제거 + 사이트 규칙 |
| `tokenizer_type` | `char` | `char`=문자 수 / `huggingface`=토큰 수 |
| `tokenizer_path` · `tokenizer_id` | — | `huggingface` 모드에서만 사용. 경로가 없으면 HF ID 로 폴백 |
| `recursive.chunk_overlap` | `100` | parse-format 경로 전용 overlap |

> ⚠️ **`chunk_size` 의 유효 하한이 경로마다 다릅니다.** docling 경로만 1024 로 보정되고
> parse-format(행/텍스트) 경로는 보정이 없습니다. `chunk_size: 300` 을 주면 docling 문서는
> 1024, 행 element 는 300 으로 잘립니다.

> ⚠️ **`tokenizer_type` 을 바꾸면 `chunk_size` 의 단위가 바뀝니다.** `1000` 은 `char` 에서
> 1,000자, `huggingface` 에서 1,000토큰(대략 2~3천 자)입니다. 함께 조정하세요.

### `output` — 청크 텍스트 안의 표 표기형태

| 키 | 기본 | 의미 |
|---|---|---|
| `table_format` | `html` | `html` / `markdown` / `auto`(표마다 구조를 보고 고름) |
| `compact_tables` | `true` | markdown 표 정렬 패딩 제거(대형 표 축소). html 엔 무관 |
| `table_row_serialization` | `false` | 병합 셀 표 뒤에 `컬럼=값 | 컬럼=값` 행 문장 추가 |
| `table_text_formats` | `[]` | 같은 청크를 표만 다른 형식으로 렌더한 텍스트를 **추가 필드**로. 켜면 본문이 형식 수만큼 복제됩니다 |

> **파서 설정의 같은 이름 키와 맞출 필요가 없습니다.** 파서 쪽은 자기 출력(`format: json/html/markdown`)과
> custom_fields 의 `html_text`/`text` 변환에만 씁니다. **청크 텍스트의 표 모양은 이 섹션이 최종 결정**합니다.

### `table_image` · `guardrail`

- `table_image.enable` — 표를 이미지로 잘라 `media_files` 에 `type='table_image'` 로 기록
- `guardrail.masking_enabled` — 파서가 넘긴 `sensitive_infos` 로 **치환**할지. 라벨(`guardrail_categories`)
  부착은 항상 수행합니다. 분류 호출 자체는 파서가 합니다

---

## 청킹 동작 — split_only 와 resize_all

```
표 단위 조기 반환   ← table_as_chunk 또는 xlsx 유래 문서
   표마다 독립 청크로 만들고 즉시 반환 (아래 단계 전부 우회)
      │ (해당 없으면)
      ▼
1단계    섹션 헤더 기준 분할
2단계    각 섹션 텍스트에 heading 붙이기
2.5단계  긴 섹션 균등 분할              ← resize_all 전용
3단계    단독 타이틀을 다음 섹션에 병합
4단계    섹션들을 그룹으로 묶기          ← split_only 는 여기서 아무 병합도 하지 않는다
5단계    인접 그룹 greedy 병합           ← resize_all 전용
5.5단계  chunk_size 초과 그룹만 분할     ← split_only 전용
6단계    최종 청크 객체 생성
```

| | `split_only` (기본) | `resize_all` |
|---|---|---|
| 하는 일 | 파서가 인식한 섹션 경계(조항 등)를 그대로 유지하고 **큰 섹션만** 쪼갬 | 전부 `chunk_size` 에 맞춰 재조립 |
| 결과 | 작은 청크가 많이 나옴. 섹션 = 검색 단위 | 균일한 크기 |
| 언제 | 조문·약관처럼 경계가 의미를 갖는 문서 | 길이가 들쭉날쭉한 일반 문서 |

> `merge_peers` 는 **읽지 않는 잔재 필드**입니다. 병합 동작은 `chunk_mode` 로 조절하세요.

**섹션 인식은 정규식이 아닙니다.** docling 이 붙인 라벨(`SECTION_HEADER`/`TITLE`)로 판정합니다.
"제N조" 같은 텍스트 패턴으로 자르고 싶다면 코드 수정이 필요합니다(아래 "코드에서 고칠 자리").

### `HEADER:` 라인

청크 선두에 `HEADER: 상품 안내 > 우대금리 조건` 같은 줄이 붙습니다.

- 경로 내부 구분자는 ` > `(부모→자식), 형제 경로 사이는 ` | ` 입니다.
- 한 청크가 여러 섹션에 걸치면 공통 조상을 앞으로 빼고 리프는 **5개까지만** 나열합니다
  (`… 외 N개`). 수십 개를 다 적으면 헤더가 노이즈가 됩니다.
- **섹션 제목은 이 줄에만 붙고 본문에서 반복되지 않습니다.**
- `include_chunk_header: false` 로 끄면 순수 본문만 나옵니다(검색 시 섹션 문맥 소실).
  parse-format 경로는 애초에 헤더가 없어 no-op 입니다.

---

## 표 처리

| 원하는 것 | 방법 |
|---|---|
| 표를 독자 청크로 | `chunking.table_as_chunk: true` (기본) — 표 청크에 섹션 제목·캡션·표 설명이 함께 실림 |
| 표를 앞뒤 본문과 한 청크로 | `table_as_chunk: false` |
| 표 표기형태 | `output.table_format` = `html` / `markdown` / `auto` |
| 큰 표 | `chunk_size` 초과 시 **행 단위로** 나뉘고 조각마다 표 헤더 행이 반복됩니다 |
| 병합 셀 표를 검색에 잘 걸리게 | `output.table_row_serialization: true` (청크가 커집니다) |

> `markdown` 은 병합 셀을 표현하지 못해 헤더 계층이 사라집니다. 병합 셀이 있는 표가 섞여 있다면
> `auto` 가 안전합니다 — 표마다 구조를 보고 고릅니다.

> ⚠️ **알려진 한계**: 표 분할 경로는 아직 `HEADER:` 라인 몫을 예약하지 않습니다. 그 경로에서는
> 청크가 헤더 길이만큼 `chunk_size` 를 넘을 수 있습니다.

---

## 청크 텍스트 정제

`chunking.text_cleanup: safe` 로 켜면 검색 매칭을 방해하는 문자 노이즈만 결정적으로 제거합니다.
한글 자모 분리(NFD) 복원, BOM·제로폭·제어문자 제거, 특수 공백 통일, 전각→반각, 줄 끝 공백 제거 등.
**문장 재작성이나 공백 병합은 하지 않고, 표/코드 블록 안은 건드리지 않습니다.**

사이트 원천에만 있는 노이즈는 규칙으로 지웁니다.

```yaml
chunking:
  text_cleanup:
    mode: safe
    rules:                                        # 적은 순서대로 적용
      - {line: "^\\s*목차\\s*$"}                   # 매칭되는 줄을 지운다
      - {find: "\\[이미지[^\\]]*\\]", replace: ""}  # 부분 치환(replace 생략 시 삭제)
      - {chunk: "^본 문서는 참고용"}                # 청크를 통째로 버린다
```

- `line`/`find` 는 **청킹 입력**에 적용됩니다 — 그래야 삭제가 청크 경계와 `n_char` 에 반영됩니다.
  `chunk` 만 출력 단계에서 판정합니다.
- 코드 블록(``` 펜스·인라인 백틱)과 마크다운 표 행은 규칙을 **타지 않습니다.**
- 잘못된 정규식·모르는 액션 키는 요청 때가 아니라 **기동 시** 실패합니다.
- **규칙을 켜면 청크 본문이 바뀌므로 재색인이 필요합니다.**

---

## 출력 스키마

청크 하나가 아래 필드를 가집니다. 스키마는 **추가 필드를 허용**합니다.

| 필드 | 의미 |
|---|---|
| `text` | 청크 본문 (선두에 `HEADER:` 줄) |
| `n_char` · `n_word` · `n_line` | 본문에서 자동 계산 |
| `i_page` · `e_page` · `n_page` | 시작/끝/전체 페이지 |
| `i_chunk_on_page` · `n_chunk_of_page` | 페이지 내 순번/총수 |
| `i_chunk_on_doc` · `n_chunk_of_doc` | 문서 내 순번/총수 |
| `chunk_bboxes` | 좌표 정보 (**JSON 문자열**) |
| `media_files` | 이미지·표 이미지 참조 (**JSON 문자열**) |
| `title` · `reg_date` · `created_date` | 문서 수준 메타 |
| `appendix` | 부록 여부 |
| `file_path` | 원본 파일 경로 |
| `doc_type` | 문서유형 스탬프 |
| `guardrail_categories` | 개인정보 분류 라벨 |
| (그 외) | 문서·행 metadata 가 그대로 전달될 수 있음 |

> - **추가는 안전, 삭제·타입 변경은 위험**합니다. 이 스키마는 벡터 DB 적재 형식과 같습니다.
> - `chunk_bboxes` · `media_files` 는 dict 가 아니라 **JSON 문자열**입니다.
> - parse-format 경로는 이 두 필드에 `"."` 를 넣습니다.

---

## 파서 → 청커 element 계약

parse-format 입력은 element 의 `category` 로 경로가 갈립니다. **여기가 파서와 청커 사이의
유일한 계약이고, 문자열 하나가 어긋나면 조용히 결과가 달라집니다.**

| 판별 | 경로 | 결과 |
|---|---|---|
| `category` 가 `tabular_row` / `custom_fields_row` / `faq_row` 인 element 가 있음 | 행 기반 | **행 1개 = 청크 1개.** element `metadata` 를 청크 property 로 승격. `"splittable": true` 면 `chunk_size` 초과분만 나누고 metadata 는 조각마다 동일 |
| `content` 이 `[AUDIO]` 로 시작 | 단일 벡터 | 전사 전체가 청크 1개 |
| 비어있지 않은 element 가 전부 `category=="table"` | 단일 벡터 | `[DA]` 청크 1개 (예전 csv/xlsx 하위호환) |
| 그 외 | 텍스트 분할 | 문자 단위 분할 |

> ⚠️ 행 기반 경로는 **행 element 만 청킹하고 섞여 온 다른 element 는 경고 한 줄 남기고 버립니다.**
> 위 세 문자열이 아니면 **조용히** 일반 텍스트 분할로 빠져 metadata 가 청크에 실리지 않습니다.
> 새 JSON 경로를 파서에 추가할 때 가장 자주 발이 걸리는 자리입니다.

---

## 코드에서 고칠 자리

**대부분은 코드 수정이 필요 없습니다.** 아래 표에서 "코드 수정 불필요" 를 먼저 확인하세요.

| 목표 | 어디 |
|---|---|
| 청크 크기·모드·헤더·표 청크·정제 | **코드 수정 불필요** — yaml `chunking.*` 또는 요청 `params` |
| 표 표기형태 | **코드 수정 불필요** — yaml `output.table_format` |
| 헤더 구분자(` > ` · ` | `)·리프 상한·최소 청크 크기·토크나이저 기본 경로 | `chunking_processor.py` **파일 머리의 사이트 조정 지점 블록** |
| 그림 annotation 을 청크에 실을지, 표 설명 반영 범위 | `chunking_processor.py::GenosSmartChunker` 의 ClassVar |
| 청크 metadata 필드 추가 | 가장 안전한 것은 문서 metadata 경유(스키마가 `extra` 허용). 정식 필드로 올리려면 스키마·빌더·조립부 + parse-format 경로를 함께 |
| 섹션 인식 규칙("제N조" 등) | `facade/chunking/smart_chunker.py` 의 `_is_section_header` + `preprocess` 안의 같은 판정 + `_get_section_header_level` — **세 곳이 같은 판정을 중복 구현합니다** |
| 병합·분할 기준 | `facade/chunking/smart_chunker.py` 4·5·5.5단계 |
| 표 직렬화 | `facade/chunking/smart_chunker.py::_extract_table_text` · `_table_item_to_texts`, HTML 은 `facade/chunking/table_html.py` |

> `facade/chunking/` 는 **공용 모듈**입니다. 고치면 `/chunker` 와 `/preprocess*`(적재)가 함께
> 바뀝니다. 한쪽만 바꾸려던 것이면 다른 방법을 찾으세요.

### 고치기 전에 내 기준선을 만드세요

```bash
# 실행 위치: genon/preprocessor/examples/parse_chunk
cat > my_cases.yaml <<'EOF'
- {doc_type: my_type, path: /data/samples/a.pdf}
EOF
./parse_chunk_golden.py --record --cases my_cases.yaml --golden ~/my_golden   # 고치기 전
# ... 수정 ...
./parse_chunk_golden.py --check  --cases my_cases.yaml --golden ~/my_golden   # 차이 0 이어야 통과
```

파서 산출(`.docling.json` / `.parse.json`)과 청커 산출(`.chunks.json`)을 **둘 다** 고정합니다.
청크만 보면 파서 단계의 회귀가 청킹에서 상쇄돼 보일 수 있습니다.

---

## 자주 겪는 증상

| 증상 | 원인 | 조치 |
|---|---|---|
| 청크가 하나로 뭉쳐 나온다 | `chunk_size: 0` 이거나 문서에 섹션 헤더가 없음 | `chunk_size` 확인. 헤더가 없으면 `resize_all` |
| 청크가 너무 잘게 쪼개진다 | `split_only` + 섹션이 많은 문서 | `resize_all` 로 바꾸거나 `chunk_size` 를 키움 |
| `chunk_size` 를 줬는데 1024 미만이 안 나온다 | docling 경로의 하한 보정 | 의도된 동작. 행 element 는 보정이 없습니다 |
| 표가 본문에 섞인다 | `table_as_chunk: false` | `true` 로 |
| 표 계층이 사라진다 | `table_format: markdown` + 병합 셀 | `html` 또는 `auto` |
| 행 metadata 가 청크에 없다 | element `category` 가 계약 3종이 아님 | 파서 쪽 element 생성부 확인 |
| 청크가 0건인데 요청은 성공 | custom_fields `require` 가 전건을 걸러냄 | 기동/요청 로그의 WARNING 확인 |
| 개인정보가 치환되지 않는다 | `guardrail.masking_enabled: false` | 청커 yaml 에서 켬. 분류 호출은 파서가 함 |
| yaml 을 고쳤는데 반영이 안 된다 | 파서 쪽 yaml 을 고침 | `chunking.*` · `output.*` 는 **청커 쪽 yaml** |
