# 청킹용 전처리기 매뉴얼

`activities/chunk/processor.py` (청킹 액티비티 `chunk`) 레퍼런스입니다. **파싱 결과를 입력받아 청킹만** 합니다.

- 파싱은 하지 않습니다. OCR·레이아웃·enrichment 는 앞 단계(파서)에서 이미 끝났습니다.
- 그래서 이 프로세서의 설정에는 `ocr` / `layout` / `pdf_pipeline` / `enrichment` 섹션이 **없습니다.**
  적어도 읽지 않습니다.

> **코드를 고쳐야 할 때는** [facade_hooks.md](facade_hooks.md) 를 보세요.
> 설정으로 안 되는 원천은 전처리기 파일의 훅 메소드(`edit_input` / `edit_output` /
> `edit_input` / `edit_output`)에서 처리합니다. 처리 본체(`processing/core/`)는 열지 않습니다.

## 목차

- [개요](#개요)
- [API](#api)
- [입력 — 두 가지 형태](#입력--두-가지-형태)
- [activities/chunk/config.yaml 설정](#activitieschunkconfigyaml-설정)
- [청킹 동작 — split_only 와 resize_all](#청킹-동작--split_only-와-resize_all)
- [표 처리](#표-처리)
- [청크 텍스트 정제](#청크-텍스트-정제)
- [이상 청크 검증](#이상-청크-검증)
- [출력 스키마](#출력-스키마)
- [파서 → 청커 element 계약](#파서--청커-element-계약)
- [코드에서 고칠 자리](#코드에서-고칠-자리)
- [자주 겪는 증상](#자주-겪는-증상)

---

## 개요

| | |
|---|---|
| 파일 | `genon/preprocessor/activities/chunk/processor.py` |
| 클래스 | `DocumentProcessor(ChunkerCore)` — 클래스명과 부모를 바꾸면 워커가 호출마다 오류로 거부합니다 |
| 설정 | `activities/chunk/config.yaml` |
| 청킹 엔진 본체 | `processing/chunking/smart_chunker.py` — **공용 모듈** |

> **엔진은 이 파일 안에 없습니다.** 액티비티 처리기의 `GenosSmartChunker` 는 동작 옵션(ClassVar)만
> 지정하는 얇은 서브클래스이고, 실제 분할·병합 로직은 `processing/chunking/smart_chunker.py`
> 한 벌에 있습니다. 거기를 고치면 청킹 액티비티와 파사드(intelligent·convert 의 적재 청킹)가 **함께** 바뀝니다.

### 배포 형태에 따라 서빙 수가 다릅니다

**코드서빙(전처리기 Temporal 워커)** 은 한 서빙이 `parse` 와 `chunk` 액티비티를 **둘 다** 등록합니다.
워커가 `activities/` 의 폴더마다 액티비티를 하나씩 만들기 때문입니다. 설정은 `activities/parse/config.yaml` 과
`activities/chunk/config.yaml` 두 벌을 읽습니다.

**GenOS 전처리기(기본 전처리기 서비스)** 는 처리기 한 개만 `preprocessor.py` 로 올려 `/run` 으로 부르므로
**한 서빙이 둘 다 할 수 없습니다.** 그때는 파싱 서빙과 청킹 서빙 두 개를 운용하고 설정도 두 벌을 각각 갱신합니다.

특히 아래 둘은 **청커 쪽 yaml 에 적어야** 효과가 있습니다.

- `chunking.*` 전부 (`chunk_size`·`chunk_mode`·`include_chunk_header`·`text_cleanup` …)
- `output.*` — 청크 텍스트 안의 표 표기형태

---

## API

- 코드서빙: Temporal 액티비티 `chunk`(또는 사본 slug) — 인자 `{parsed_ref, output_ref, source_name?, params?}`.
  워커가 `ARTIFACT_ROOT/<parsed_ref>.json` 을 읽어 `params.document` 로 넘기고, 청크 리스트를
  `ARTIFACT_ROOT/<output_ref>.json` 에 씁니다(인자·반환은 [code_serving.md](code_serving.md)).
- GenOS 전처리기: `POST /preprocessor/{id}/run` — 아래 body.

| 파라미터 | 위치 | 설명 |
|---|---|---|
| `file_path` | body | 파서 결과 JSON 파일 경로. `document` 를 인라인으로 주면 생략 가능 |
| `params.document` | body | 파서 응답의 `data` 를 그대로. **인라인이 우선**(코드서빙은 워커가 `parsed_ref` 로 채웁니다) |
| `params.chunk_size` | body | 청크 상한. yaml 보다 우선 |
| `params.chunk_mode` | body | `split_only` / `resize_all` |
| `params.short_chunk_size` | body | 짧은 청크 병합 기준. yaml 보다 우선 |
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

## activities/chunk/config.yaml 설정

기본 전처리기 서비스용으로는 이 키 중 자주 고치는 것만 남긴 간이 설정 `resource/chunk_config_simple.yaml` 을 제공합니다.

섹션은 5개뿐입니다: `defaults` / `chunking` / `table_image` / `output` / `guardrail`.

### `chunking`

| 키 | 기본 | 의미 |
|---|---|---|
| `chunk_size` | `1500` | 청크 상한. **0 = 분할 안 함.** docling 경로는 0 초과 시 **최소 1024 로 보정**됩니다 |
| `chunk_mode` | `split_only` | 아래 절 참조 |
| `short_chunk_size` | `0`(끔) | 본문이 이보다 짧은 청크를 다음 청크에 병합. [짧은 청크 병합](#짧은-청크-병합) 참조 |
| `table_as_chunk` | `true` | 표를 본문과 섞지 않고 독자 청크로 |
| `include_chunk_header` | `true` | 청크 선두 `HEADER: <섹션 경로>` 라인 |
| `text_cleanup` | `off` | `safe` 로 켜면 문자 노이즈 제거 + 사이트 규칙 |
| `validation` | 블록이 없으면 꺼짐. 표준·monimo 설정은 켜짐(`action: drop`) | 이상 청크 검증. [이상 청크 검증](#이상-청크-검증) 참조 |
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
| `table_format` | `markdown` | `html` / `markdown` / `auto`(표마다 구조를 보고 고름). 키를 생략하면 `html` |
| `compact_tables` | `true` | markdown 표 정렬 패딩 제거(대형 표 축소). html 엔 무관 |
| `table_row_serialization` | `true` | 병합 셀 표 뒤에 `컬럼=값 | 컬럼=값` 행 문장 추가. 키를 생략하면 `false` |
| `table_text_formats` | `[]` | 같은 청크를 표만 다른 형식으로 렌더한 텍스트를 **추가 필드**로. 켜면 본문이 형식 수만큼 복제됩니다 |

> **`table_format` 은 파서 설정의 같은 이름 키와 같은 값으로 맞추세요.** 이 섹션은 일반 문서(PDF·DOCX·HTML 등)의
> 표 모양을 정합니다. custom_fields 레코드형 문서는 파서가 청크 본문을 완성해 넘기므로, 그 표 모양은
> **파서 설정의 `output.table_format`** 이 정합니다(청커는 다시 렌더링하지 않습니다). 두 값이 다르면
> `precheck_custom_fields.sh` 가 경고합니다.

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

### 짧은 청크 병합

`split_only` 는 섹션 하나가 청크 하나이므로, 섹션 제목만 있거나 본문이 1~2줄인 섹션은 짧은 청크가
됩니다. 이런 청크는 `HEADER:` 경로가 내용의 대부분이라 제목 검색어로 상위에 오르지만 답변 근거는
주지 못합니다.

- 기준값이 0(기본)이면 **제목만 있는 청크**만 다음 청크로 병합합니다(문서 끝이면 앞 청크로). 붙이면
  `chunk_size` 를 넘거나 제목이 산출물에 남지 않는 경우에는 붙이지 않고 그대로 둡니다.
- 기준값을 0 보다 크게 두면 본문(`HEADER:` 라인 제외)이 그보다 짧은 청크도 병합하고, **짧은 청크를
  남기지 않습니다.** `chunk_size` 는 아래 마지막 경우에만 넘깁니다. 제목만 있는 청크도 같은 규칙을 따릅니다.
  - 짧은 청크가 연달아 나오면 먼저 `chunk_size` 안에서 서로 묶습니다. 묶음이 기준보다 길어지면
    독립 청크로 둡니다. 여러 섹션이 큰 청크 하나에 쌓이지 않게 하기 위해서입니다.
  - 묶음이 여전히 짧으면 `chunk_size` 안에서 **다음 청크 본문 앞에**, 안 되면 앞 청크 뒤에 붙습니다.
    상위 섹션(장)이 바뀌어도 붙이고, `HEADER:` 에는 경로가 함께 남습니다.
  - 양쪽 모두 `chunk_size` 를 넘으면, 이웃 청크 본문을 줄 경계에서 잘라 기준을 채울 만큼만 짧은
    청크 쪽으로 옮깁니다. 표가 든 청크는 행이 표 머리와 떨어지므로 자르지 않습니다.
  - 붙인 청크의 텍스트(제목 포함)는 본문에 그대로 이어붙습니다. 경로가 5개를 넘으면 `HEADER:` 가
    `… 외 N개` 로 줄어들어, 경로만으로는 제목이 남지 않기 때문입니다.
  - 표 청크(`table_as_chunk: true`)도 `chunk_size` 안이면 붙일 곳이 됩니다. 표 청크 자체는 짧아도 병합하지 않습니다.
  - 이상 청크 검증(`validation.action: drop`)에서 버려질 청크는 건너뛰고 그 다음 청크에 붙습니다.
    붙이면 정상 본문이 불량 청크와 함께 버려지기 때문입니다.
  - 묶음 끝에 다음 섹션의 제목만 있는 청크가 있으면, 묶음째 다음 청크에 들어가지 못할 때 제목만
    다음 청크 앞에 붙여 자기 섹션과 함께 둡니다.
  - 붙일 이웃이 모두 `chunk_size` 에 가깝고 나눌 수도 없으면(표가 들었거나, 나누면 어느 한쪽이
    기준에 못 미칠 때) `chunk_size` 를 넘겨 다음(없으면 앞) 청크에 붙입니다. 넘는 양은 붙인 짧은
    청크의 길이(기준 미만)와 `HEADER:` 경로가 늘어난 만큼입니다.
  - 짧은 청크가 남는 경우는 다음뿐입니다: 문서 전체가 기준보다 짧을 때, 합친 결과가 검증에서
    버려질 청크가 될 때(예: 같은 안내 문구가 여러 번 이어져 반복으로 판정).
- 단위는 `chunk_size` 와 같습니다(`char` 모드면 문자 수). 켜면 청크 경계가 바뀌므로 재색인이 필요합니다.

권장값은 **200** 입니다. 본문 200자 미만이면 `HEADER:` 경로(보통 50~90자)가 청크의 30% 이상을
차지하고, 그 정도 본문은 1~2문장이라 독립된 검색 단위로서 정보가 부족합니다.

문서유형마다 다르게 두려면 그 문서유형의 custom_fields yaml 에 `body.merge_short` 로 적습니다
(문서형 `source.kind: document` 전용). 값은 파서 결과에 실려 청커로 전달되므로, 바꾸면 **파싱부터**
다시 해야 반영됩니다. 0 이상의 정수가 아니면 기동 시 실패합니다(청커 설정 `chunking.short_chunk_size` 도
같습니다). 요청 파라미터의 잘못된 값은 경고를 남기고 다음 순위 값을 씁니다.

```yaml
# custom_field_product_slf.yaml
body:
  once: [created_date]
  merge_short: 200
```

우선순위는 앞이 셉니다 — 요청 파라미터 `short_chunk_size`(facade `CONFIG_BY_DOC_TYPE` 포함) →
custom_fields `body.merge_short` → 청커 설정 `chunking.short_chunk_size` → 0.

### `HEADER:` 라인

청크 선두에 `HEADER: 상품 안내 > 우대금리 조건` 같은 줄이 붙습니다.

- 경로 내부 구분자는 ` > `(부모→자식), 형제 경로 사이는 ` | ` 입니다.
- 한 청크가 여러 섹션에 걸치면 공통 조상을 앞으로 빼고 리프는 **5개까지만** 나열합니다
  (`… 외 N개`). 수십 개를 다 적으면 헤더가 노이즈가 됩니다.
- **섹션 경로는 이 줄에만 붙고 본문에서 반복되지 않습니다.** 섹션 제목 원문 한 줄은 대개 그 섹션의 첫 청크 본문에 남습니다(제목만 있는 청크나 짧은 청크를 다음 청크에 합치면서 한도를 지키려고, 이 줄에 이미 나오는 제목 줄을 본문에서 빼는 경우는 제외). 이 줄을 끄거나 리프 상한에 걸려도 제목이 본문에 남게 하기 위한 것입니다.
- 문서 접두가 있으면 이 줄 다음에 `body.repeat` 값, 첫 청크에는 이어서 `body.once` 값이 붙습니다. 문서형에서 `body.repeat` 를 쓸 때는 `body.labels` 로 항목명을 함께 지정합니다. 항목명이 없으면 값만 있는 줄이 본문 소제목처럼 보입니다. 이 줄의 첫 요소가 이미 문서 제목이므로, 문서 제목과 같은 값(상품명 등)은 `body.repeat` 로 다시 싣지 말고 분류·종목명처럼 제목에 없는 식별 값을 고릅니다.
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

## 이상 청크 검증

`chunking.validation` 을 켜면 **유효 내용이 없거나, 지나치게 짧거나, 반복·문자 손상이 심한 청크**를
적재 대상에서 뺍니다. 판정마다 사유와 근거 수치가 로그에 남습니다.

```yaml
chunking:
  validation:
    enable: true
    action: drop              # report(기록만) | drop(제외). 표준·monimo 설정은 drop 입니다
    fail_on_any: false        # drop 모드에서 불량 청크가 하나라도 있으면 문서를 실패 처리
    min_chars: 4              # 문서·평문 청크의 내용 문자 하한
    row_min_chars: 0          # 행 청크 하한. FAQ·메뉴 보호를 위해 기본은 끔
    repeat_min_count: 10      # 반복 횟수 하한
    repeat_max_share: 0.5     # 중복 점유율 하한
    broken_max_share: 0.3     # 손상 점유율 하한
    broken_min_count: 3       # 손상 문자 최소 개수
    table_min_chars: false    # 표가 든 청크에도 길이 하한을 적용할지
    max_chars: 0              # 출력 청크 글자 수 상한. 0 이면 검사하지 않음. 넘어도 버리지 않고 보고만 함
    by_doc_type:              # doc_type 별로 위 값을 덮어씁니다
      faq:  {min_chars: 0}
      menu: {min_chars: 0}
```

위 수치가 기본값이며, 근거는 아래 [판정 기준의 근거](#판정-기준의-근거)에 있습니다. 블록이 없거나 `enable: false` 면
기존과 완전히 같게 동작합니다.

`faq`·`menu` 예시처럼 짧은 답이 정상인 문서 유형은 `min_chars: 0` 으로 완화합니다. 행 청크는 원래
`row_min_chars: 0` 이라 이 완화는 문서·평문 청크(예: 단독 청크로 나온 `없음`)에 적용됩니다. `by_doc_type` 은
청커 요청의 `doc_type` 파라미터로 고르므로, 요청에 `doc_type` 이 없거나 이름이 다르면 공통 기준을 씁니다.

### 판정 사유

앞의 사유에 걸리면 그 사유가 대표 사유입니다.

| 순서 | 사유 | 판정 | 근거 예 |
|---|---|---|---|
| 1 | `blank` | 본문이 공백뿐 | 본문 없음 |
| 2 | `no_content` | 내용 문자(글자·숫자, 언어 무관) 0자 | 내용 문자 0자 |
| 3 | `broken_chars` | 깨진 문자(U+FFFD, 제어 문자, `GLYPH<…>`, 잘못된 인코딩으로 바뀐 한글 `占쏙옙`·`ë³´í—˜`) 개수 ≥ `broken_min_count` **그리고** 점유율 ≥ `broken_max_share` | 깨진 문자 100자 / 101자(99%) |
| 4 | `repetition` | 동일 문자 연속·동일 줄·동일 문구 연속 중 하나가 `repeat_min_count` 이상 **그리고** 중복 점유율 ≥ `repeat_max_share` | 동일 문구 29회, 중복 93% |
| 5 | `min_chars` | 내용 문자 수 < 하한 | 내용 문자 1자 < 하한 4자 |

정상 청크를 빼지 않는 것이 우선이라, 아래는 판정에서 보호합니다.

- 구분선(`-----`, `| --- |`)처럼 글자가 없는 반복은 반복으로 세지 않습니다.
- 동일 문자 연속은 글자만 셉니다. `10000000000원` 같은 금액·계좌 번호의 숫자 연속은 반복이 아닙니다.
- 중복 점유율은 반복된 사본의 문자를 한 번씩만 셉니다(원본 한 벌 제외, 공백 제외). 같은 구간을 여러 지표로
  겹쳐 세지 않으므로 100% 를 넘지 않습니다.
- 내용 문자를 셀 때 걷어내는 것은 HTML 태그(`<p>`, `</td>`, `<!-- … -->`)뿐입니다. `0 < 금리 < 5%` 같은
  비교식이나 `<가입대상>` 같은 꺾쇠 표기는 본문으로 셉니다.
- 표가 든 청크는 길이 하한을 적용하지 않습니다(`table_min_chars: true` 로 켤 수 있습니다).
- 코드·수식 청크(docling 라벨 `code`·`formula`)는 길이 하한과 반복 판정을 적용하지 않습니다.
  행·평문 청크에는 라벨이 없어 일반 기준을 씁니다.
- 반복·손상은 횟수와 점유율을 **함께** 넘어야 합니다. 본문 중간의 구분선 한 줄이나 깨진 글자
  하나로는 청크가 빠지지 않습니다.
- `HEADER:` 경로와 문서 접두는 판정에서 뺍니다. 청크 본문(출력)은 바꾸지 않습니다.

### 검사 시점

1. **초기 검사** — 청크가 만들어진 직후, `edit_chunk` 훅보다 앞입니다. 첫 청크가 빠지면 첫 청크
   전용 접두는 살아남은 다음 청크가 받습니다.
2. **최종 검사** — `edit_output` 훅이 끝난 뒤 응답 직전입니다. `edit_chunk`·`edit_output` 이
   본문을 바꾸거나 새 청크를 만들어도 이 검사를 거칩니다. 훅이 `HEADER:` 줄을 지웠으면 본문 전체로
   판정합니다. 여기서 청크가 빠지면 순번·개수(`i_chunk_on_doc`, `n_chunk_of_doc` 등)를 다시 맞춥니다.
   문서 전체 쪽수(`n_page`)는 청크가 빠져도 줄어들지 않습니다.

### 응답 요약

판정에 걸린 청크, 청킹 중 빠진 메타 필드(`missing_fields`), 글자 수 상한을 넘은 청크(`max_length`) 중 하나라도
있으면 성공 응답 최상위에 `chunk_validation` 이 붙습니다. `drop` 으로 뺀 청크를 로그를 보지 않고도 알 수 있게
하기 위한 것입니다. `data`(청크 목록)의 구조는 바뀌지 않으며, 셋 다 없거나 검증이 꺼져 있으면 키 자체가 없습니다. 문서 실패(`CHUNK_ALL_REJECTED` 등)는 지금처럼 실패 응답입니다.
청킹 처리기를 배포한 기본 전처리기 서비스의 `/run` 응답에 붙습니다. 코드서빙의 `chunk` 액티비티는 반환 요약에
이 값을 싣지 않으므로 로그의 `[chunk_validation]` 줄로 봅니다.

```json
{"code": 0, "errMsg": "success", "data": [ ... ],
 "chunk_validation": {"action": "drop", "count": 3, "reasons": {"min_chars": 2, "repetition": 1},
  "items": [{"index": 7, "page": 4, "reason": "min_chars",
             "message": "본문이 지나치게 짧음(글자·숫자 1자, 최소 4자)", "preview": "1."}]}}
```

| 필드 | 설명 |
|---|---|
| `action` | `report` 면 검증 때문에 빠진 청크는 없습니다(훅이 고치거나 버렸을 수는 있습니다). `drop` 이면 `items` 의 청크가 `data` 에서 빠졌습니다 |
| `count` | 판정에 걸린 청크 전체 건수 |
| `reasons` | 사유 코드별 건수. `items` 가 20건에서 잘려도 전체 분포를 알 수 있습니다. 합계는 `count` 와 같습니다 |
| `items` | 판정 순서로 최대 20건 |
| `index` | 원래 청크 순번(0부터). 로그 `[chunk_validation]` 줄의 `index` 와 같아 같은 페이지의 청크도 구분됩니다. 훅이 새로 만든 청크는 `edit_output` 결과 목록에서의 위치입니다 |
| `page` | 청크의 대표 페이지. 1 이상일 때만 실립니다. 행·평문 청크의 1은 기본값일 수 있습니다 |
| `section` | 청크가 걸친 헤딩 경로(`부모 > 자식`)의 목록. 출력 본문에 그대로 들어 있는 경로만 최대 5개, 경로당 200자까지 싣습니다. 없으면 생략됩니다 |
| `reason` | [판정 사유](#판정-사유)의 사유 코드 |
| `message` | 사유와 측정값을 설명한 문구. 예: `깨진 문자가 많음(깨진 문자 3자 / 전체 5자, 60%)`, `같은 내용이 반복됨(같은 문구 29회)` |
| `preview` | 본문의 앞 200자. 제어 문자는 `␁` 같은 기호로 바꿔 보여 줍니다. 본문을 알 수 없으면 빈 문자열입니다(아래 참조) |
| `missing_fields` | 파서가 실어 보낸 메타 중 청크에 실리지 않은 필드와 건수(예: `{"i_page": 2}`). 있을 때만 실립니다. 아래 참조 |
| `max_length` | `max_chars` 를 넘은 출력 청크. `{"limit": 상한, "count": 건수, "items": [{"index", "page", "n_char"}]}`(최대 20건). 있을 때만 실립니다 |

`missing_fields` 는 청크를 버리지 않는 보고 항목이며 `action` 과 무관합니다. 훅(`edit_output` 포함)이 끝난 뒤
최종 출력 청크의 값을 파서가 실어 보낸 메타와 대조합니다.

- 행 청크: 행 메타(목표 필드)가 그 청크에 같은 값으로 실리지 않은 경우(예약 값에 덮어써짐, 훅이 지움 등). 청크마다 셉니다.
  `edit_chunk` 훅이 `info["fields"]` 로 넘긴 값은 의도된 변경이라 대조하지 않습니다.
- 문서 청크: 문서 메타가 청크에 같은 값으로 실리지 않은 경우. 문서당 1건입니다.
- 버려진 청크와 훅이 새로 만든 청크(출처를 알 수 없음)는 대조하지 않습니다. 목록·객체 값과 그 JSON 문자열, 숫자와
  숫자 문자열처럼 표기만 다른 값은 같은 값으로 봅니다.

`max_length` 도 청크를 버리지 않는 보고 항목이며 `action` 과 무관합니다. 접두(`HEADER:` 등)를 포함한 출력 본문의
글자 수로 잽니다. 행 청크, 분할할 수 없는 표 행, `[AUDIO]`·`[DA]` 단일 청크처럼 `chunk_size` 를 넘을 수 있는 청크를
임베딩 전에 찾기 위한 것입니다. 상한은 임베딩 모델의 입력 한도(토큰)를 글자 수로 환산해 정합니다. 표준·사이트
설정은 이 값을 넣지 않았습니다(0).

목표 필드명이 예약 이름과 겹치는 설정은 기동 시 막히므로, 이 항목은 기동 검증을 거치지 않은 메타(외부에서
만든 파서 결과, 훅이 넣은 값 등)에서 나타납니다. LLM 이 값을 만들지 못해 null 로 남은 필드는 세지 않습니다.

- `preview`·`section` 은 민감정보 마스킹과 표현 정리를 거친 본문에서 만듭니다. 출력 본문과 같은 마스킹이 적용됩니다.
  - 초기 검사에서 `drop` 으로 뺀 청크는 `edit_chunk` 훅을 거치기 전 본문에 마스킹·표현 정리를 적용해 만듭니다.
    훅이 지우거나 바꿀 내용이 그대로 남아 있을 수 있습니다.
  - 그 밖의 청크(`report`, 최종 검사에서 뺀 청크)는 `data` 에 실리는 출력 본문에서 만듭니다.
  - `report` 에서 걸린 청크가 훅에서 버려지거나 `edit_output` 에서 빠졌으면 출력 본문이 없어 비어 있습니다.
- `preview` 는 원문에서 해당 청크를 찾기 위한 단서입니다. 결함이 앞 200자 밖에 있을 수 있고, 섹션 제목 줄이나
  `HEADER:` 줄이 앞에 포함될 수 있습니다.
- 마스킹은 출력 본문과 같은 설정(`guardrail.masking_enabled`)을 따릅니다. **꺼져 있으면(표준 기본값 `false`)
  원문이 그대로 실립니다.**
- 한 요청에서 청킹을 여러 번 하는 확장 코드라면 마지막 청킹의 요약만 남습니다.

### 모드 전환 절차

표준·monimo 설정은 `action: drop` 입니다. 새 현장에서 기준을 먼저 확인하려면 아래 순서를 따릅니다.

1. `action: report` 로 배포합니다. 청크는 그대로 적재되고 판정은 로그와 [응답 요약](#응답-요약)에 남습니다.
2. 로그에서 `[chunk_validation]` 줄을 모아 걸린 청크와 근거를 검토합니다. 정상 청크가 걸렸으면
   임계값을 조정하거나 `by_doc_type` 으로 그 문서 유형만 완화합니다.
3. 기준에 합의한 뒤 `action: drop` 으로 바꿉니다.

로그는 청크마다 한 줄(파일명, 원래 순번, 페이지, 검사 시점, 사유, 측정값, 임계값, 모드)과 문서마다
한 줄(입력·판정·제외·생존 건수, 사유별 건수, 판정률, 청크가 모두 걸린 페이지)입니다. 로그에는 본문을
남기지 않습니다. 출력 본문의 앞부분은 응답 요약의 `preview` 에만 실립니다.

### 실패 처리

| 상황 | `report` | `drop` | `drop` + `fail_on_any` |
|---|---|---|---|
| 일부 청크 불량 | 전부 반환, 로그·응답 요약 기록 | 해당 청크 제외 후 반환, 로그·응답 요약 기록 | 문서 실패 `CHUNK_REJECTED` |
| 전부 불량 | 전부 반환, 로그·응답 요약 기록 | 문서 실패 `CHUNK_ALL_REJECTED` | 문서 실패 `CHUNK_REJECTED` |
| 판정기 오류 | 전부 반환, 오류 로그 기록 | 문서 실패 `CHUNK_VALIDATION_ERROR` | 같음 |

세 경우 모두 실패(`stage: chunk_validation`, 성격 `permanent`)이며 재시도로 해결되지 않습니다. 오류 코드는
메시지에 실리지만 배포 형태에 따라 앞부분이 다르므로, 시작 문자열이 아니라 포함 여부로 구분합니다.

| 배포 형태 | 메시지 예 | 실패 표시 |
|---|---|---|
| 기본 전처리기 서비스(`/run`) | `error_msg`: `CHUNK_ALL_REJECTED: 모든 청크가 …` | HTTP 200 + `code: 1`, `error_kind: permanent` |
| 코드서빙(`chunk` 액티비티) | `chunk <원본 이름>: ChunkValidationError: CHUNK_ALL_REJECTED: 모든 청크가 …` | 액티비티 오류 `type: permanent`, `non_retryable: true` |

- 검증 키는 **요청 파라미터로 바꿀 수 없습니다.** `text_cleanup=off` 요청에서도 검증은 그대로입니다.
  `CONFIG_BY_DOC_TYPE` 에 적어도 건너뜁니다 — doc_type 별 차등은 `by_doc_type` 으로 합니다.
- 오기입(음수, 정수 자리의 true/false, 0~1 밖의 비율, 모르는 키, 모르는 `action`)은 **기동 시** 실패합니다.
- 오디오(`[AUDIO]`)·legacy 표(`[DA]`) 단일 청크는 검증하지 않습니다.

### 판정 기준의 근거

기본값은 두 자료로 정했고, 두 자료 모두 `tools/chunk_validation/measure.py` 로 다시 측정할 수 있습니다.

1. **판정 샘플** `tests/fixtures/chunk_quality/cases.yaml` — 꼭 제외해야 할 오류(E), 꼭 보존해야 할 정상(N),
   임계값 바로 아래·같음·위의 경계(B) 사례입니다. 정상 사례에는 실측에서 오탐이 났던 형태(병합 셀 통계표, 체크
   목록이 붙은 약관 문단, 금액·계좌 번호, 비교식)가 들어 있습니다. 단위 테스트가 모든 사례의 판정을 고정합니다.
2. **실제 청킹 결과** — 저장소의 샘플 문서(`sample_files/`)를 실제로 파싱·청킹한 결과(`*.chunks.json`)에
   판정기를 돌려, 정상 청크에서 지표가 어디까지 올라가는지와 임계값 후보별 제외 건수를 봅니다. 청킹 결과 파일은
   커밋하지 않으므로 아래처럼 만들어 측정합니다(파싱·청킹 단계에서 LLM 을 호출합니다).

```bash
cd genon/preprocessor
tools/parse_chunk/parse_chunk_verify.sh --keep --out /tmp/cv_corpus       # 샘플 문서 청킹 결과 생성
.venv/bin/python tools/chunk_validation/measure.py --extra /tmp/cv_corpus --out report.md
```

2026-10-07 측정(샘플 문서를 설정 두 벌로 청킹한 결과) 결과는 청크 794건(본문 중복 제거 211건, 금융 상품·FAQ·메뉴·약관
등 doc_type 14종), 판정 샘플
52건 전부 기대 일치, 제외 판정 0건입니다. 실물 문서를 포함한 청킹 결과 52건(일반 형식 21건, custom_fields 31건)의 청크
1,755건에서는 `min_chars` 17건, `no_content` 3건이 걸렸고 `repetition`·`broken_chars` 는 0건입니다. 기본값을 처음 정할 때는 더 큰 코퍼스(청크 2,545건, 본문 중복 제거
1,422건)로 측정했으나 그 원자료는 보존되어 있지 않아 다시 측정할 수 없습니다.

이 결과는 **판정기가 저장소의 정상 자료를 빼지 않는다**는 근거이며, 고객 문서에서의 오탐률을 보증하지 않습니다.
도입 현장에서는 [모드 전환 절차](#모드-전환-절차)대로 고객 문서를 `report` 로 관측하고, 같은 도구에
`--extra <청킹 결과 폴더>` 를 주어 현장 자료로 다시 측정합니다. 이 도구는 고객 YAML 이 아니라 모듈 기본값으로
판정하고 청크 종류를 메타에서 추정하므로, 운영 판정과 다를 수 있는 근사치입니다.

### 보장하지 않는 것

원문 일부 누락, 읽기 순서 오류, 표 값·열 연결 오류, 출처 매핑 오류는 그럴듯한 문장으로 남으므로
청크 단위 판정으로는 찾을 수 없습니다. 원문과 대조하는 문서 단위 평가가 필요합니다.

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
| 헤더 구분자(` > ` · ` | `)·리프 상한·최소 청크 크기·토크나이저 기본 경로 | `activities/chunk/processor.py` **파일 머리의 사이트 조정 지점 블록** |
| 그림 annotation 을 청크에 실을지, 표 설명 반영 범위 | `activities/chunk/processor.py::GenosSmartChunker` 의 ClassVar |
| 청크 metadata 필드 추가 | 가장 안전한 것은 문서 metadata 경유(스키마가 `extra` 허용). 정식 필드로 올리려면 스키마·빌더·조립부 + parse-format 경로를 함께 |
| 섹션 인식 규칙("제N조" 등) | `processing/chunking/smart_chunker.py` 의 `_is_section_header` + `preprocess` 안의 같은 판정 + `_get_section_header_level` — **세 곳이 같은 판정을 중복 구현합니다** |
| 병합·분할 기준 | `processing/chunking/smart_chunker.py` 4·5·5.5단계 |
| 표 직렬화 | `processing/chunking/smart_chunker.py::_extract_table_text` · `_table_item_to_texts`, HTML 은 `processing/chunking/table_html.py` |

> `processing/chunking/` 는 **공용 모듈**입니다. 고치면 청킹 액티비티와 파사드(적재)가 함께
> 바뀝니다. 한쪽만 바꾸려던 것이면 다른 방법을 찾으세요.

### 고치기 전에 내 기준선을 만드세요

```bash
# 실행 위치: genon/preprocessor/tools/parse_chunk
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
