# 모니모 전처리기 개발 매뉴얼

모니모 환경에 배포된 **문서 전처리기(doc parser)** 의 설정과 코드를 수정하고 반영하는 방법을 다룹니다.

전처리기 코드서빙은 **이미 배포되어 동작하고 있습니다.** 이 문서는 실행 중인 전처리기를 우리
요구에 맞게 고치는 방법을 다룹니다. 새 코드서빙을 만들거나 도커 이미지를 빌드하는 일은 없습니다.

## 목차

- [0. 개요](#0-개요) — 제공 항목 · 작업 흐름 · 작업 범위
- [1. 새 문서 유형 추가하기](#1-새-문서-유형-추가하기) — `custom_field_*.yaml` 6단계
- [2. 코드 수정](#2-코드-수정) — facade 2개의 훅 메소드와 확장 지점
- [3. 개발 환경](#3-개발-환경) — 코드스페이스 사용법 · 검증 절차
- [4. 배포](#4-배포) — Bitbucket 브랜치 → PR → CI/CD → `/version` 확인
- [5. 구성 YAML 옵션](#5-구성-yaml-옵션) — 주요 구성 옵션
- [6. 주요 동작 방식](#6-주요-동작-방식) — 파싱 결과 2종 · 청킹 · 출력 스키마
- 부록 [A 용어집](#부록-a-용어집) · [B 지원 요청이 필요한 경우](#부록-b-지원-요청이-필요한-경우) · [C custom_fields 설정 레퍼런스](#부록-c-custom_fields-설정-레퍼런스)

---

## 0. 개요

### 0.1 제공 항목

다음 항목은 솔루션 개발자가 제공합니다.

| 전달물 | 내용 | 확인 방법 |
|---|---|---|
| 전처리기 소스 | Bitbucket 저장소에 이미 반영되어 있습니다 | 코드스페이스에 저장소가 클론되어 있는지 |
| **개발 환경이 구성된 코드스페이스** | 파이썬과 의존성 설치가 완료되어 바로 실행할 수 있습니다 | 3.3의 facade 단독 실행이 가능한지 |
| 코드서빙 접속 정보 | 코드서빙 `ID`와 리비전 번호 | 4.4의 `/version`이 응답하는지 |
| 채워진 `resource/*.yaml` | 모델 서빙 주소와 인증키가 들어 있습니다 | 5.1 |
| 설정 템플릿 5종 | `resource/templates/custom_field_TEMPLATE_*.yaml` | 1.3 |
| 매뉴얼 | 이 문서 (설정 키 전체 목록은 [부록 C](#부록-c-custom_fields-설정-레퍼런스)) | — |

### 0.2 작업 흐름

```
 [1장 또는 2장]  코드스페이스에서 설정 또는 코드 수정
        │
 [3장]   facade 단독 실행으로 결과 확인                    ← 수 초. 대부분의 확인은 이 단계에서 완료됩니다
        │
 [4장]   UPDATED_AT 갱신 → commit/push → PR → 담당자 merge
        │
 [4.4]   /version 호출로 반영 확인                        ← 건너뛰지 마세요
```

배포 후 되돌리려면 담당자 머지 절차를 다시 거쳐야 하므로 비용이 큽니다.

### 0.3 작업 범위

| 작업 목적 | 수정 위치 | 관련 절 |
|---|---|---|
| **새 문서 유형 추가** | `resource/custom_field_<유형>.yaml` + 등록 블록 | 1장 |
| 문서 유형별 옵션 조정 | facade의 `CONFIG_BY_DOC_TYPE` | 2.3 |
| 입력 데이터 구조 조정 | facade의 훅 메소드 | 2.4 |
| 공통 동작 옵션 | `resource/*_processor_config.yaml` | 5장 |

### 0.4 엔드포인트

전처리기는 목적이 다른 facade 5개를 하나의 서버에서 실행합니다. **이 문서의 대상은 파싱과 청킹
2종**이며, 나머지 facade는 본 문서의 범위에 포함하지 않습니다.

| 엔드포인트 | facade 파일 | 이 문서에서 |
|---|---|---|
| `POST /parser` · `/parser_upload` | `facade/parser_processor.py` | **주 대상** |
| `POST /chunker` | `facade/chunking_processor.py` | **주 대상** |
| `POST /preprocess` · `/preprocess_intelligent` | `intelligent_processor.py` | 다루지 않음 |
| `POST /preprocess_convert` | `convert_processor.py` | 다루지 않음 |
| `POST /preprocess_attachment` | `attachment_processor.py` | 다루지 않음 |
| `GET /health` · `/version` | 없음 | [4.4](#44-반영-확인--version) |

파싱과 청킹은 2단계로 나뉩니다. 무거운 처리(레이아웃 분석·OCR·LLM 보강)는 파싱 단계에서 이뤄집니다.

```
원본 문서 ──POST /parser──▶ 파싱 결과 JSON ──POST /chunker──▶ 청크 목록
```

---

## 1. 새 문서 유형 추가하기

새 입력 데이터 유형(엑셀 FAQ, JSON 이벤트 목록, 계약서 PDF 등)을 추가할 때 수행하는 작업입니다.
**대부분은 설정 파일 하나를 추가하는 것으로 완료됩니다.** 파이썬 코드는 필요하지 않습니다.

### 1.1 `doc_type`의 역할

`doc_type`은 요청 `params`로 넘기는 문서유형 키입니다. "이 문서는 계약서다 / FAQ 엑셀이다"를
알려 주면 전처리기가 해당 문서 유형에 대한 전용 처리를 적용합니다. 이때 수행되는 작업은 세 가지입니다.

1. 등록된 `custom_fields` 중 **`doc_type`이 일치하는 것만** 동작시킵니다.
2. 엑셀과 JSON은 일치하는 매핑 설정이 있으면 행(레코드)별로 파싱합니다.
   그 결과는 청커에서 1건 = 1청크가 됩니다.
3. 문서 metadata에 `doc_type`을 기록하고, 해당 문서에서 생성된 모든 청크에 포함합니다.

> `doc_type` 비교는 **양끝 공백 제거 + 소문자화 후 정확 일치**입니다. `"Contract "`와 `contract`는
> 같지만 `contracts`는 다릅니다. 리스트도 됩니다 (`doc_type: [notice, notice_v2]`).

### 1.2 ① 입력 데이터 구조에 따라 `kind`를 선택합니다

이 선택에 따라 이후 설정이 결정됩니다.

| 입력 데이터가 다음 구조인 경우 | `source.kind` | 검색 단위 | LLM 호출 |
|---|---|---|---|
| 엑셀·CSV 한 행이 한 건 (FAQ, 용어사전, 메뉴) | `rows` | 행 1개 = 청크 1개 | 없음 |
| JSON 배열의 한 요소가 한 건 (이벤트 목록, 공지) | `records` | 레코드 1개 = 청크 1개 | 없음 |
| 대상 하나를 깊게 설명한 중첩 JSON (상품 상세) | `sections` | 섹션 1개 = 청크 1개 | 없음 |
| 문서에서 필드 몇 개를 뽑는다 (계약서, 카드 안내) | `document` | 문서 metadata, 모든 청크에 같은 값 | **문서당 1회** |
| 값의 위치를 class·속성으로 지정할 수 있는 HTML (크롤 산출물) | `html` | 문서 metadata, 모든 청크에 같은 값 | 없음 |

- `rows` · `records` · `sections`는 **파싱 이전** 확장자 분기에서 처리됩니다(docling을 거치지 않음).
- `document` · `html`은 파싱 후 enrichment 단계에서 붙습니다.
- 앞의 세 kind는 `llm:` 블록을 추가하면 입력 데이터에 없는 필드(요약문 등)를 만들어 붙일 수 있습니다.
  `rows`는 행마다, `records`는 레코드마다, `sections`는 문서당 1회 호출합니다.

### 1.3 ② 템플릿을 복사합니다

템플릿에는 항목마다 `[필수]`/`[선택]` 표시와 뜻, 흔한 실수, 확인 명령이 주석으로 들어 있습니다.
빈 파일에서 새로 쓰는 것보다 빠릅니다.

| `kind` | 복사할 템플릿 (`resource/templates/`) | 제공 예시 (`resource/`) |
|---|---|---|
| `rows` | `custom_field_TEMPLATE_rows.yaml` | `custom_field_faq.yaml` · `custom_field_term.yaml` |
| `records` | `custom_field_TEMPLATE_records.yaml` | `custom_field_monimo_event.yaml` |
| `sections` | `custom_field_TEMPLATE_sections.yaml` | `custom_field_product_hpp.yaml` |
| `document` | `custom_field_TEMPLATE_document.yaml` | `custom_field_card.yaml` |
| `html` | `custom_field_TEMPLATE_html.yaml` | `custom_field_monimo_news.yaml` |

```bash
# 실행 위치: 저장소 루트
cp genon/preprocessor/resource/templates/custom_field_TEMPLATE_rows.yaml \
   genon/preprocessor/resource/custom_field_notice.yaml
```

### 1.4 ③ 네 개의 블록을 구성합니다

모든 `kind`는 동일한 기본 구조를 사용합니다.

```yaml
schema: v2                    # 첫 줄. 이 줄이 없으면 기동에 실패합니다

source:                       # 입력 데이터를 어떻게 볼 것인가
  kind: rows

fields:                       # 만들 목표필드(= 적재 DB 컬럼). 값은 언제나 object 입니다
  QUESTION:
    alias: [질문, 문의내용]                    # 입력 데이터의 컬럼·key 이름들. 표기가 흔들리면 여러 개
  ANSWER:
    alias: [답변]
    transform: text                           # 값 변환(체이닝 가능)
  REG_DT:
    alias: [등록일]
    transform: date_int_flex                  # "26.07.01" -> 20260701
  USE_YN:
    alias: [노출여부]
    values: {"Y": [노출, 사용], "N": [미노출]}  # 값 표기를 표준 코드로 통일합니다
    default: "N"
  GROUP_C: {const: "IFP"}                      # 입력 데이터 값이 있어도 덮어씁니다

require:                      # 이 필드가 비면 그 건은 건너뜁니다
  fields: [QUESTION]

body:                         # 청크 본문(= 임베딩 입력)을 어떻게 만들 것인가
  fields: [QUESTION, ANSWER]
  labels: {QUESTION: 질문, ANSWER: 답변}
```

| 블록 | 하는 일 |
|---|---|
| `source` | `kind`, 레코드 배열 위치(`records_at`), 행 접기(`merge_rows`), 포맷 전처리(`pre`) |
| `fields` | **목표필드 하나의 규칙을 한 자리에** 모읍니다 (`alias` `const` `default` `values` `transform` `template` `pack` `meta` `seq`) |
| `require` · `filter` | 빈 값으로 거르기 / 값으로 거르기 |
| `body` | 청크 본문 구성(`fields`), 항목명(`labels`), 긴 본문 분할(`split`), 접두(`repeat`·`once`) |
| `llm` | 입력 데이터에 없는 필드를 LLM으로 생성 (`out`·`in` 필수) |

값이 만들어지는 순서는 kind와 무관하게 같습니다.

```
alias 매핑 -> default(빈 값만) -> const(덮어씀) -> values -> transform -> template
          -> filter·require 선별 -> seq 번호 -> llm 필드 -> pack 묶기
```

변환기는 인자 없는 3종(`date_int` `date_int_flex` `text_norm`)과 인자를 받는 7종
(`regex_sub` `regex_extract` `to_int` `truncate` `html_text` `text` `to_json`), 합쳐서 10종입니다.
목록에 없는 이름을 적으면 기동에 실패합니다. 사이트 전용 변환기를 더하는 방법은 [2.6](#26-설정에서-이름으로-불러-쓰는-세-가지-확장-지점)에 있습니다.

> 여기 적는 이름이 **설정 표기**입니다. 오류 메시지에는 `column_map` · `text_fields` · `key_map`
> 같은 **내부 이름**이 나오는데, 그것은 설정에 적는 이름이 아닙니다. 둘의 대응표는
> [부록 C.1](#c1-설정-표기와-내부-이름)에 있습니다.

#### 청크 본문 구성 — `body`

metadata 컬럼에만 있는 값은 **필터 검색에만** 걸리고 임베딩 검색에는 걸리지 않습니다. 그래서 검색어에
나올 값은 본문에 실어야 합니다.

| 키 | 하는 일 | 쓰는 곳 |
|---|---|---|
| `body.fields` | 본문을 구성할 필드와 순서 (개행 결합) | `rows` · `records`는 **본문의 전부** |
| `body.labels` | `항목명: 값` 형태로 냅니다 | 사람이 검색어로 쓰는 말을 적습니다. DB 컬럼명 금지 |
| `body.split` | 본문이 `chunk_size`를 넘으면 여러 청크로 나눕니다 | 긴 상세 HTML을 가진 행·레코드 |
| `body.repeat` | 나뉜 **모든** 조각 앞에 반복할 식별 필드 | `split: true` 일 때만. 제목·메뉴명 1~2개 |
| `body.once` | **첫 청크에만 1회** 얹습니다 | 문서 단위 분류 (`sections` · `document`) |
| `body.mirror_to` | 본문과 글자 그대로 같은 값을 받을 메타 필드 | 적재 측의 본문 컬럼용. `document` · `html` 전용 |

> 접두는 청크마다 쓸 수 있는 `chunk_size`를 그만큼 줄입니다(청커가 미리 그 몫을 떼어 둡니다).
> 짧은 식별 필드 1~2개로 제한하세요. `body.once`만 쓰면 **그 값으로 임베딩 검색을 할 때 첫 청크만** 걸립니다.
> 값 자체는 모든 청크의 metadata에 실리므로 필터 검색은 전 청크에서 됩니다.

### 1.5 ④ 프로세서 설정에 등록합니다

```yaml
# genon/preprocessor/resource/parser_processor_config.yaml의 enrichment 목록 끝에
enrichment:
  # … 기존 항목 …
  - custom_fields:
      enable: true
      doc_type: notice                          # 요청 params로 넘길 키
      config_file: custom_field_notice.yaml     # 파일명만. 이 yaml과 같은 폴더 기준
```

쓰지 않을 유형은 `enable: false`로 끕니다.

### 1.6 ⑤ 요청을 호출합니다

```json
{ "file_path": "/app/src/service/.../notice.xlsx", "params": { "doc_type": "notice" } }
```

`/chunker`는 **수정할 것이 없습니다.** 파서 결과의 값을 그대로 이어받으므로 등록 블록도 필요 없습니다.

### 1.7 ⑥ 결과 값을 확인합니다

"청크가 나왔다"는 **검증이 아닙니다.** 별칭이 입력 데이터 표기와 어긋나면 키는 모두 맞는데 값만
비어 있습니다. 에러는 나지 않습니다.

#### 명시적 오류 없이 실패할 수 있는 경우

설정 파일에 모르는 키가 있으면 **기동에 실패합니다.** 오타는 가장 가까운 이름을 제안하고, 다른 kind
전용 키는 어디 전용인지 알려 줍니다. 문제는 **기동 검사가 못 잡는 것들**입니다.

| 상황 | 결과 |
|---|---|
| `alias`·CSS 선택자가 입력 데이터 표기와 어긋남 | 값이 `null`. 경고만 |
| `body.fields`에 **어디서도 만들어지지 않는 필드** | 본문에서 조용히 빠짐. `llm`을 주석 처리하고 그 출력 필드를 남기는 실수가 가장 흔합니다 |
| `require` 미충족 | 해당 항목만 제외됩니다. **모든 항목이 제외되면 청크는 0건이지만 요청은 성공합니다** |
| `values`에 없는 값 | 원값 통과(fail-open), 경고만 |
| 같은 이름 key가 얕은 곳과 깊은 곳에 모두 있음 | **얕은 쪽이 우선합니다.** 경고 없이 엉뚱한 값이 실립니다 |
| 같은 이름의 레코드 배열이 여러 곳에 있음 | `records_at`은 최초 매칭 **1개**만 찾습니다. 나머지는 경고 없이 빠집니다 |

#### 세 단계 검증 절차

(가) 설정만 사전에 검사합니다. 파싱과 LLM 호출이 없어 즉시 완료되며, 기동 실패를 이 단계에서 확인할 수 있습니다.

```bash
# 실행 위치: 저장소 루트
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh
```

(나) 결과 값을 확인합니다. facade 단독 실행이 가장 빠릅니다([3.3](#33-신속한-확인-방법--facade-단독-실행)).
LLM을 쓰지 않는 `kind`는 모델 서빙 없이도 전체 과정을 실행할 수 있습니다.

```bash
# 실행 위치: 저장소 루트
python -m genon.preprocessor.facade.parser_processor 공지사항.xlsx --doc-type notice -o parsed.json
python -m genon.preprocessor.facade.chunking_processor parsed.json --doc-type notice -o chunks.json

python -c "
import json
r = json.load(open('parsed.json'))
els = r.get('elements') or []
if els:                                             # rows / records / sections
    print('건수:', len(els))
    for el in els[:3]:
        print(el['category'], el.get('metadata'))   # 목표필드가 의도한 값인가
        print('본문:', repr(el['content'])[:160], '\n')
else:                                               # document / html
    print(r.get('metadata'))
"
```

확인 항목은 다음 세 가지입니다.

| 보는 것 | 어긋나면 |
|---|---|
| **건수** | 예상보다 적으면 `require`로 걸러진 것 |
| **각 목표필드 값** | 엉뚱한 컬럼이 들어왔거나 `None` 이면 `alias` 불일치 |
| **본문** | 비었거나 일부가 빠졌으면 `body.fields`에 만들 수 없는 필드가 있음 |

`kind: document`와 `html`은 element가 아니라 문서 metadata에 실립니다. 청킹까지 돌리면
`chunks.json`의 모든 청크에 같은 값이 붙습니다. 기동 로그의 `WARNING`도 함께 보세요.

(다) 기존 문서의 결과가 변경되지 않았는지 확인합니다. 검증 대상 문서로 기준선(골든)을 생성합니다.

```bash
# 실행 위치: genon/preprocessor/examples/parse_chunk
cat > my_cases.yaml <<'EOF'
- {doc_type: notice, path: /data/samples/notice.xlsx}
- {doc_type: card,   path: /data/samples/card.pdf}
EOF

./parse_chunk_golden.py --record --cases my_cases.yaml --golden ~/my_golden   # 고치기 전
# … 설정 수정 …
./parse_chunk_golden.py --check  --cases my_cases.yaml --golden ~/my_golden   # 차이 0 이어야 통과
```

### 1.8 문제 해결

| 증상 | 원인 |
|---|---|
| `doc_type`을 줬는데 아무 일도 안 일어남 | 등록이 `enable: false` 이거나 `doc_type` **값**의 문자열 불일치. 값 오타는 에러 없이 무시됩니다 |
| `doc_type`을 안 줬는데 custom_fields가 동작함 | config yaml의 등록 블록에 `doc_type` 키가 없으면 **wildcard** 로 모든 요청에 매칭됩니다 |
| 청크는 생성되지만 metadata가 비어 있음 | 일치하는 등록이 없어 일반 처리 경로로 처리되었거나 `alias`가 일치하지 않음 |
| 특정 필드만 계속 `null` | `llm`의 `out` 이름과 프롬프트가 내놓는 JSON 키가 다릅니다. `kind: html` 이면 선택자가 안 걸린 것 |
| 모든 레코드가 제외됨 | `skipped N/N records (missing required)` 경고 확인. 입력 데이터 표기가 `alias`와 달라 `require` 필드가 null이 된 경우입니다 |
| 본문이 빈 레코드가 빠짐 | 정상입니다. 빈 벡터 적재를 막으려고 경고와 함께 제외합니다 |
| xlsx가 행별로 나뉘지 않음 | 매칭되는 매핑이 없으면 `formats.xlsx.processing_mode`가 결정합니다 (`tabular` 인지 확인) |
| `tabular custom_fields config 없음: …` | `config_file`은 **프로세서 config와 같은 폴더** 기준. 파일명만 적으세요 |
| `등록되지 않은 transforms 변환기: …` | 기본 제공 10종과 `tb.register_transform`으로 등록한 것만 쓸 수 있습니다([2.6](#26-설정에서-이름으로-불러-쓰는-세-가지-확장-지점)) |
| 필드는 안 붙는데 `doc_type`만 모든 청크에 붙음 | 매칭되는 등록이 없는 상태. 스탬프는 매칭 여부와 무관하게 동작합니다 |
| csv/xlsx 인데 `doc_type` 조차 안 붙음 | 정상입니다. 엑셀은 **매칭되는 행 매핑이 있을 때만** `doc_type`이 실립니다 |

**설정만으로 처리할 수 없는 경우** 입력 데이터 구조가 예상과 다른 것입니다. 예를 들어 레코드가 한 단계 더
중첩되어 있거나, 목록과 상세 데이터가 분리되어 있거나, 확장자가 `.xml`인 경우가 있습니다. 이 경우에는 [2장](#2-코드-수정)을 참조합니다.

---

## 2. 코드 수정

### 2.1 수정 방법 선택

아래 표의 순서대로 적용합니다. 순서를 뒤집으면 설정 한 줄로 처리할 수 있는 작업을 코드로 구현하게 되고,
그 코드는 릴리스마다 다시 반영해야 하는 유지보수 부담이 됩니다.

| 순서 | 수단 | 적용 범위 | 수정 위치 |
|---|---|---|---|
| ① | 요청 `params` | 해당 요청 | 없음 ([5.3](#53-요청-params로-재배포-없이-값-변경)) |
| ② | 프로세서 설정 YAML | 모든 문서 공통 동작 | `resource/*_processor_config.yaml` ([5장](#5-구성-yaml-옵션)) |
| ③ | `custom_field_*.yaml` | 그 **문서유형**의 값 추출과 청크 본문 | `resource/custom_field_<유형>.yaml` ([1장](#1-새-문서-유형-추가하기)) |
| ④ | **facade 2개** | 설정으로 표현할 수 없는 처리 | `facade/parser_processor.py` · `facade/chunking_processor.py` |
| ⑤ | 기타 | 앞선 네 가지 방법으로 처리할 수 없는 경우 | 담당자에게 문의 |

**④ 까지가 고객 개발자의 범위입니다.** 처리 본체는 `processing/core/parser.py`와 `core/chunker.py`에
한 벌씩 있고 **직접 수정할 필요가 없습니다.** 본체를 고쳐야 할 것 같으면 ①~④ 중 하나를 놓쳤거나
훅 메소드가 부족하다는 신호입니다.

### 2.2 수정 대상 파일

수정 대상은 다음 두 파일입니다. 두 파일 모두 확장 지점이 파일 뒤쪽에 배치되어 있습니다.

| 파일 | 줄수 | 구획 |
|---|---|---|
| `facade/parser_processor.py` | 282 | 파일 상단 주석(흐름 요약) → `ROUTES` → `CONFIG_BY_DOC_TYPE` → 훅 3종 → 오버라이드 |
| `facade/chunking_processor.py` | 264 | `GenOSVectorMeta` → `GenosSmartChunker` → `ROW_CATEGORIES` → `CONFIG_BY_DOC_TYPE` → 훅 3종 |

먼저 **파일 상단 주석**을 확인하세요. 처리 흐름 요약과 결과 형식이 포함되어 있습니다.

처리 흐름에서 훅이 호출되는 자리는 다음과 같습니다.

```
파싱   요청 -> 확장자 판정 -> doc_type별 설정 -> [edit_input] -> ROUTES -> 파싱
            -> [edit_document] -> LLM enrichment -> [edit_output] -> 응답

청킹   파서 결과 -> 형태 판별 -> doc_type별 설정 -> [edit_input] -> 분할 -> [edit_chunk]
            -> vector_meta 조립 -> [edit_output] -> 응답
```

> `_`로 시작하는 메소드(`_start_job`, `_call_*`)는 **오버라이드하지 않습니다.** 훅 호출과 설정
> 적용 순서를 맡고 있습니다.

### 2.3 문서 유형마다 설정을 다르게 — `CONFIG_BY_DOC_TYPE`

설정 파일은 모든 문서에 동일하게 적용됩니다. "계약서만 OCR을 강제 적용", "FAQ만 청크를 짧게 설정"과 같은
문서 유형별 설정은 훅이 아니라 이 표에 정의합니다. 두 facade 모두 동일한 위치에 설정합니다.

```python
# facade/parser_processor.py
    CONFIG_BY_DOC_TYPE = {
        "press":    {"enrichment.table_description.enable": False},  # 표가 없어 불필요한 LLM 호출
        "contract": {"ocr.ocr_mode": "force"},                       # 스캔본이 많다
    }

# facade/chunking_processor.py
    CONFIG_BY_DOC_TYPE = {
        "faq":    {"chunking.chunk_size": 500},                      # 문답 1건이 짧다
        "manual": {"chunking.chunk_mode": "split_only"},
    }
```

키는 설정 파일 경로를 점 표기법으로 작성합니다. 같은 의미의 요청 파라미터 이름(괄호 안 표기)을 사용해도 동일하게
동작합니다. **청킹 설정은 파서가 아니라 청커의 표**에 정의합니다.

| 파서 | | 청커 | |
|---|---|---|---|
| `enrichment.table_description.enable` (`table_desc`) | 표 설명 | `chunking.chunk_size` (`chunk_size`) | 청크 최대 크기 |
| `enrichment.image_description.enable` (`img_desc`) | 이미지 설명 | `chunking.chunk_mode` (`chunk_mode`) | `split_only` / `resize_all` |
| `enrichment.doc_summary.enable` (`doc_summary`) | 문서 요약 | `chunking.recursive.chunk_overlap` (`chunk_overlap`) | 청크 간 겹침 |
| `enrichment.toc.enable` (`toc`) | 목차 보강 | | |
| `ocr.ocr_mode` | `auto` / `force` / `disable` | | |

표로 표현할 수 없는 조건은 `config_by_condition()`에서 정의합니다. 동일한 형식의 dict를 반환하며,
빈 dict를 반환하면 설정은 변경되지 않습니다.

```python
    def config_by_condition(self, job):
        if job.params.get("dept") == "IR":
            return {"enrichment.doc_summary.enable": True}
        return {}
```

우선순위는 뒤에 있는 항목일수록 높습니다. 설정 파일 → `CONFIG_BY_DOC_TYPE` → `config_by_condition()` → 요청
파라미터 순서입니다. 요청 파라미터로 전달한 값은 다른 설정으로 덮어쓰지 않습니다.

> 모든 설정을 요청마다 바꿀 수 있는 것은 아닙니다. 엔드포인트 주소, 프롬프트, 토크나이저
> 경로처럼 기동 시 한 번 읽혀 고정되는 설정은 여기 적어도 무시되고 로그에 경고가 남습니다.

### 2.4 설정만으로 입력 데이터 구조를 처리할 수 없는 경우 — 훅 메소드

`custom_field_*.yaml`은 입력 데이터가 예상한 구조일 때 값을 추출합니다. 입력 데이터 구조가 다르면
설정만으로 처리할 수 없습니다.

훅의 역할은 **값을 생성하는 것이 아니라, 설정이 값을 찾을 수 있도록 입력 데이터 구조를 정규화하는 것입니다.**
`alias`·`transform`·`values`·`require`는 그대로 동작하며, 훅은 그 이전 단계에서 구조만 조정합니다.

| 파일 | 훅 메소드 | 자리 | 받는 것 |
|---|---|---|---|
| `parser_processor.py` | `edit_input(ext, doc_type, data, work_dir=None, **kwargs)` | 파싱 **전** | `.json`은 dict/list, `.md .html`은 str, 엑셀은 `{시트명: 2차원 행}`, 그 밖은 파일 경로 |
| | `edit_document(job, doc)` | 파싱 후, **LLM enrichment 전** | `DoclingDocument` 객체 |
| | `edit_output(ext, doc_type, result, **kwargs)` | 응답 직전 | 응답 dict |
| `chunking_processor.py` | `edit_input(kind, data, **kwargs)` | 청킹 전 | `kind=="parse"` 면 `list[dict]`, `"docling"` 이면 직렬화된 dict |
| | `edit_chunk(text, info, **kwargs)` | **청크 1건마다** | 본문 str + `info` dict |
| | `edit_output(vector_metas, **kwargs)` | 응답 직전 | `GenOSVectorMeta` 목록 |

> `edit_document`가 **enrichment 앞**이라는 점이 중요합니다. 표를 LLM 설명 대상에서 빼려면 여기서
> 빼야 합니다. `edit_output`은 enrichment 뒤라 LLM 비용을 이미 치른 뒤입니다.

훅에서 사용하는 `tb`는 파일 상단에서 이미 import한 toolbox입니다. 직접 구현하기 전에 toolbox에서 제공하는
기능을 먼저 확인하세요. 값 변환기는 yaml의 `transform:`이 호출하는 함수와 같으므로 설정과 코드의 변환 결과가 일치합니다.

#### 공통 규칙 네 가지

1. `doc_type`으로 처리 대상을 제한합니다. 그렇지 않으면 해당 확장자의 모든 문서 결과가 변경됩니다. `doc_type`은
   소문자로 정규화되어 전달되므로 `"MyType"`과 비교하면 일치하지 않습니다.
2. **수정이 필요하지 않으면 입력값을 그대로 반환합니다.** 그래야 core가 파생 입력을 만들지 않고 기존
   처리 경로를 사용합니다.
3. **요청 파라미터가 필요하면 시그니처 끝에 `**kwargs`를 붙입니다.** 요청의 `params`가 그대로
   전달되며 `kwargs["job"]`으로 요청 컨텍스트도 조회할 수 있습니다. 요청별 상태를 `self`에 저장하지 마세요.
   인스턴스 하나가 모든 요청을 처리하므로 `await` 사이에 값이 섞일 수 있습니다. 단계 간 값 전달에는 `job.notes`를 사용합니다.
4. **외부 호출이 필요하면 `async def`로 구현합니다.** core가 코루틴 완료를 기다립니다. 동기 함수
   안에서 외부 호출을 수행하면 이벤트 루프가 차단되어 다른 문서의 요청도 함께 지연됩니다.

#### `job` — 요청별 정보

`kwargs["job"]`으로 조회합니다(시그니처에는 포함되지 않음).

| 필드 | 값 |
|---|---|
| `job.ext` · `job.doc_type` | 표준 확장자(소문자), 문서 유형(소문자) |
| `job.file_path` | 요청이 넘긴 **원본** 경로 |
| `job.source` | 실제로 처리할 입력. `edit_input`이 만든 파생 입력이면 원본과 다릅니다 |
| `job.params` | 요청 `params` |
| `job.config` | **실제로 적용된 설정.** 해당 문서에 적용된 설정을 추적할 수 있습니다 |
| `job.notes` | 단계 간 값 전달용 dict. `self` 대신 사용합니다 |

> 임시 디렉터리가 필요하면 `job.temp_dir("접두")`로 만듭니다(요청이 끝나면 자동 삭제).
> `edit_input`은 그 디렉터리를 `work_dir` 인자로 미리 받습니다.

#### 예시 — 입력 데이터 구조가 변경된 두 경우

입력 데이터는 `genon/preprocessor/sample_files/drill/`에 있으며 그대로 재현할 수 있습니다. 기준 데이터는
`source.records_at: eventList`로 레코드를 찾고, 각 필드는 `alias`로 레코드 내부에서 이름을 찾습니다.

**변형 ① 관계사별로 한 단계 더 중첩된 경우**

```json
{ "companyList": [ { "mnmFncoCd": "1", "eventList": [ {…}, {…} ] },
                   { "mnmFncoCd": "3", "eventList": [ {…} ] } ] }
```

| 증상 | 원인 |
|---|---|
| 이벤트 3건 중 **2건만** 청크가 된다 | `records_at`은 이름이 맞는 **첫 배열만** 찾는다 |
| `GROUP_C`가 전부 기본값 | `mnmFncoCd`가 레코드 **밖** 부모에 있어 `alias`가 못 찾는다 |

오류가 발생하지 않으므로 **결과를 확인하지 않으면 문제를 알 수 없습니다.** 훅에서 중첩 구조를 평탄화하고 부모 값을
레코드에 추가합니다.

```python
    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
        if ext == ".json" and doc_type == "monimo_event" and "companyList" in data:
            return {"eventList": [
                {**event, "mnmFncoCd": company.get("mnmFncoCd")}
                for company in data["companyList"]
                for event in company.get("eventList") or []
            ]}
        return data
```

`records_at: eventList`를 그대로 사용하려면 **`eventList` 키를 가진 dict를** 반환해야 합니다. 목록만
반환하면 해당 키를 찾지 못해 `source.on_missing` 정책이 적용됩니다.
적용 후 실측값은 청크가 2건에서 3건으로, `GROUP_C`가 `IFP`/`IFP`에서 `HPP`/`HPP`/`SSF`로 바뀝니다.

**변형 ② 목록과 상세 데이터가 분리된 경우**

```json
{ "eventList":  [ { "cmpId": "M101", "evtTodayMainCopy": "여행자보험 가입 이벤트" } ],
  "detailList": [ { "cmpId": "M101", "htmlText": "<p>해외 여행자보험 <b>30%</b> 할인</p>" } ] }
```

증상은 `DETAIL_HTML`·`DETAIL_TEXT`가 전부 비어 있는 것입니다. `body.fields`에 `DETAIL_TEXT`가 있으므로
청크 본문이 제목만 남습니다. `alias`는 레코드 안에서만 찾고 `detailList`는 그 밖입니다.

```python
        if ext == ".json" and doc_type == "monimo_event" and "detailList" in data:
            detail = {d.get("cmpId"): d for d in data["detailList"]}
            return {**data, "eventList": [
                {**event, **detail.get(event.get("cmpId"), {})}
                for event in data["eventList"]
            ]}
```

적용 후 `DETAIL_TEXT`가 채워지고, 설정의 `transform: html_text`가 그때부터 동작합니다.

#### 청커 훅 — 청크 단위 수정

본문을 수정하거나 청크를 제외하는 작업은 **`edit_output`이 아니라 `edit_chunk`**에서 수행하세요. 통계와
순번이 부여되기 전 단계이므로 core가 자동으로 다시 계산합니다.

```python
    def edit_chunk(self, text, info, **kwargs):
        if "상담직원용" in text:
            return tb.DROP                     # 이 청크를 버린다 (순번·개수는 코어가 재계산)
        if "손실" in text:
            info["fields"]["RISK"] = "high"    # 이 청크에만 실릴 값
        return text.replace("■", "")           # 고친 본문을 돌려준다
```

| 반환값 | 뜻 |
|---|---|
| 문자열 | 그 문자열이 청크 본문이 됩니다 |
| `None` | 손대지 않습니다. `return`을 빠뜨려도 청크가 사라지지 않습니다 |
| `tb.DROP` | 이 청크를 버립니다 (빈 문자열·공백만 돌려줘도 같습니다) |

`info`는 **처리 경로와 관계없이 구조가 동일합니다.** 문서·레코드·평문 등 어떤 입력 데이터에도 동일한 훅을 사용할 수 있습니다.

| 키 | 값 |
|---|---|
| `kind` | `"docling"`(문서) · `"row"`(레코드/표 행) · `"text"`(그 밖) |
| `page` · `index` | 1-based 페이지 · 현재 순번(참고용) |
| `headings` | 섹션 경로 목록. `docling` 경로만 채워집니다 |
| `metadata` | 문서 또는 레코드 메타데이터. **복사본이라 고쳐도 저장되지 않습니다** |
| `fields` | 이 청크에만 실을 값. `GenOSVectorMeta` 필드로 나갑니다 |

`text`는 접두와 `HEADER:` 라인까지 붙은 뒤의 본문입니다. 훅이 돌려준 값에 마스킹과 정제가
뒤이어 적용됩니다.

> `edit_output`에서 본문을 고쳤거나 청크를 지웠으면 통계를 다시 맞춰야 합니다. 개수가 바뀌었으면
> `tb.refresh_stats(vector_metas)`, 본문만 고쳤으면 `reindex=False`. 호출하지 않으면 `n_char`가
> 예전 값으로 남습니다.

#### 훅으로 처리할 수 없는 기능

다음 기능은 값 파이프라인 내부 또는 순회 방식 자체를 변경하므로 훅으로 재현할 수 없습니다.
`custom_field_*.yaml`에서 설정하세요.

| 기능 | 이유 |
|---|---|
| `source.merge_rows` | 값 파이프라인 이전에 값을 이어붙여 최종 출력까지 바꿉니다 |
| `source.sections` · `source.ignore_keys` | `kind: sections`의 트리 순회 자체를 좌우합니다 |
| `source.pre.markdown.front_matter` 승격 | 본문 제외는 되지만 metadata 승격은 안 됩니다 |

실행해 볼 수 있는 예시는 `genon/preprocessor/examples/facade_hooks/`에 있습니다.

### 2.5 새 확장자 지원 추가 — `ROUTES`

파서는 확장자에 따라 핸들러를 선택합니다. 일치하는 첫 번째 항목의 핸들러를 호출하고, 핸들러가 `None`을 반환하면
다음 후보를 처리합니다(폴스루). 마지막 항목은 항상 캐치올입니다.

| 확장자 | 핸들러 | 결과 |
|---|---|---|
| `.csv .xlsx .xlsm` | `route_tabular` | 행 매핑 설정이 우선. 없으면 시트를 문서로, 또는 행을 레코드로 |
| `.hwp .hwpx .hml` | `route_hwp` | 문서형 |
| `.docx` | `route_docx` | 문서형 |
| `.pdf .html .htm .md` | `route_docling` | 문서형 |
| `.json` | `route_json` | 레코드 매핑이 일치하는 경우에만 처리. 없으면 캐치올로 대체 처리 |
| `.ppt .pptx` | `route_ppt` | 문서형. PDF 변환 실패 시 텍스트만 |
| 그 외 | `route_other` | 텍스트면 문서형, 아니면 텍스트 추출 |

대부분의 경우 `route_*`를 새로 구현할 필요가 없습니다. `edit_input`에서 입력 데이터를 기존 핸들러가 처리할 수 있는
형식으로 변환하여 전달하면 됩니다. `.md` · `.html` · `.json` · 엑셀 이외의 확장자에서는
`edit_input`이 파일 경로와 `work_dir`을 받으므로, 해당 디렉터리에 변환 결과를 저장하고 그 경로를 반환합니다.

```python
    ROUTES = (((".xml",), "route_json"),        # 새 확장자는 표 맨 앞에
              ((".tsv",), "route_tabular")) + (
        # ... 기존 ROUTES 표 그대로 ...
    )

    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
        if ext == ".xml" and doc_type == "monimo_event":
            events = [{c.tag: c.text for c in ev}
                      for ev in ET.parse(data).getroot().find("eventList")]
            out = os.path.join(work_dir, "converted.json")
            json.dump({"eventList": events}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
            return out                          # 새 경로를 돌려주면 그것으로 파싱합니다
        return data
```

동작을 검증하기 전에 **먼저 `ROUTES`에 등록**하세요. 등록하지 않으면 캐치올이 처리하여 목표필드가 비어 있는 청크 1개만 생성됩니다.

표준 형식으로 변환할 수 없는 입력 데이터(로그, 고정폭 텍스트, 사내 전문)에 한해서만 핸들러를 직접
구현합니다. 해당 핸들러도 facade 파일에 정의합니다.

```python
    async def route_log(self, job):
        lines = [l for l in tb.read_text_with_fallback(job.source).splitlines() if l.strip()]
        return {"elements": tb.make_elements(lines)}          # 배관 필드는 tb가 채웁니다
```

| 계약 | |
|---|---|
| 시그니처 | `async def route_<이름>(self, job) -> dict \| None` |
| 응답 | `{"elements": [...]}`만 채우면 됩니다. `content`·`usage`는 core가 설정합니다 |
| 폴스루 | `None`을 반환하면 `ROUTES`의 다음 후보를 처리합니다 |
| 행 1건 = 청크 1개 | `tb.make_elements(..., category="custom_fields_row")`로 청커의 행 기반 처리 경로에 전달합니다 |

> 새 category 이름을 만들기보다 `custom_fields_row`를 재사용하는 편이 안전합니다.
> 청커는 `doc_type`이 아니라 `category`로 분기하므로, 기존 이름을 사용하면 청커를 수정할 필요가
> 없습니다. 새 이름을 사용해야 한다면 `chunking_processor.py`의 `ROW_CATEGORIES`에 추가합니다.

> 파서에서 청커로 넘기는 element 계약상, 행 기반 경로는 행 element만 청킹하고 **섞여 온 다른
> element는 경고 한 줄을 남기고 버립니다.** category 문자열이 틀리면 조용히 일반 텍스트 분할로
> 빠져 metadata가 청크에 실리지 않습니다.

### 2.6 설정에서 이름으로 불러 쓰는 세 가지 확장 지점

훅 외에도 설정에 이름을 적어 실행할 수 있는 확장 지점이 세 가지 있습니다. 훅보다 적용 범위가 좁고
정확하므로, 해당하는 경우에는 이 방식을 먼저 사용하세요. 세 가지 확장 지점에서 참조하는 파일은 모두
**config yaml과 같은 폴더 아래**에 둡니다(경로 탈출은 거부).

| 확장 지점 | 무엇을 맡기나 | 어떻게 |
|---|---|---|
| **값 추출 자체** | 정규식 추출, 사내 마스터 조회처럼 **LLM이 아닌 방법** | `custom_field_*.yaml`의 `python:` 블록 |
| 값 변환기 | 금액 파싱, 사번에서 부서명처럼 **사이트 전용 값 변환** | facade에서 `tb.register_transform()`으로 등록한 뒤 yaml `transform:`에서 이름으로 호출 |
| LLM 출력 파서 | 표준 JSON이 아닌 응답 해석 | `custom_field_*.yaml`의 `parser: {type: python, file, callable}` |

```yaml
# custom_field_contract.yaml — 계약번호처럼 규칙이 분명한 값은 LLM에 물을 이유가 없습니다
schema: v2
source: {kind: document}
python:
  file: contract_extract.py     # 이 yaml과 같은 폴더
  callable: extract             # 기본값 extract
  out: [CONTRACT_NO, AMOUNT]
fields:
  AMOUNT: {transform: [to_int]} # 값 파이프라인은 LLM 경로와 완전히 같습니다
```

```python
# contract_extract.py
import re

CONTRACT = re.compile(r"계약번호[:\s]*([A-Z0-9-]+)")

def extract(text, document=None, output_fields=None, **kwargs):
    m = CONTRACT.search(text or "")
    return {"CONTRACT_NO": m.group(1) if m else None, "AMOUNT": ...}
```

- 돌려주는 것은 dict 하나입니다. 그 뒤로는 LLM 경로와 완전히 같은 파이프라인을 거칩니다.
- `async def`로 써도 됩니다(사내 API 조회). 파일이 없거나 함수 이름이 틀리면 기동에서 실패합니다.
- `url`·`system_prompt` 같은 LLM 전용 키는 이 자리에서 쓸 수 없습니다(기동 실패).

사이트 전용 값 변환기는 **core를 고치지 말고** facade 파일 최상위에서 등록합니다. core를 고치면
릴리스 갱신에서 사라집니다.

```python
tb.register_transform("won_to_int", lambda v: int(str(v).replace(",", "").replace("원", "")))
```

```yaml
fields:
  AMT: {alias: [연회비], transform: [won_to_int]}    # '1,200원' -> 1200
```

### 2.7 청크 메타데이터 추가

| 추가 대상 | 수정 위치 | 비고 |
|---|---|---|
| 그 **문서의 모든 청크**에 같은 값 | 파서의 `edit_output`에서 `tb.set_chunk_metadata(result, {...})` | `result["metadata"]`에 직접 쓰면 **이 API 응답에만** 남고 청크에는 반영되지 않습니다 |
| **청크마다 다른 값** | 청커의 `edit_chunk`에서 `info["fields"]["RISK"] = "high"` | 본문·통계·순번 필드는 넣을 수 없습니다 |
| **적재 컬럼으로 선언** | `chunking_processor.py`의 `GenOSVectorMeta` | 선언하면 타입까지 검사됩니다. 선언이 없어도 실립니다(`extra=allow`) |

```python
    def edit_output(self, ext, doc_type, result, **kwargs):
        if doc_type == "contract":
            tb.set_chunk_metadata(result, {
                "SOURCE_SYSTEM": "CRM",
                tb.FIRST_CHUNK_FIELDS_KEY: ["PRODUCT_NM"],   # 첫 청크에만 붙일 필드
            })
        return result
```

> 적재 스키마를 고칠 때의 주의는 [6.3](#63-출력-스키마-청크)에 있습니다.

### 2.8 제한 사항 및 유의 사항

| | 왜 |
|---|---|
| facade 끼리 import | facade 파일 하나만 배포하는 방식이 깨집니다 |
| `self`에 요청 상태 저장 | 인스턴스는 **프로세스당 1개**입니다. `await` 사이에 다른 요청의 값이 섞입니다. 단계 간 전달은 `job.notes` |
| `_`로 시작하는 메소드 오버라이드 | 훅 호출과 설정 적용 순서를 맡고 있습니다 |
| `processing/core/` 수정 | 릴리스 갱신에서 사라집니다. 필요하면 솔루션 개발자에게 훅 추가를 요청하세요 |
| docling 수정 | 소스가 아니라 wheel로 들어옵니다. 수정할 수 없습니다 |
| element 5키 스키마에서 필드 삭제·개명 | `/chunker`가 의존합니다. **추가는 안전** |

예외를 발생시킬 때는 공용 예외를 사용합니다.

```python
raise GenosServiceException(
    error_code='1',              # 응답의 error_code로 그대로 나감
    error_msg='읽을 수 없는 파일입니다.',
    stage='parse',               # 선택: 실패 단계
    error_type='permanent',      # 선택: transient / permanent / timeout
)
```

`stage`와 `error_type`을 지정하면 응답의 `stage`·`error_kind`에 포함되어 호출 측에서 재시도 여부를 판단할
수 있습니다. **부분 실패를 허용하려면** 예외를 발생시키지 않고 해당 항목만 제외한 뒤 결과에 기록하세요.

---

## 3. 개발 환경

### 3.1 코드스페이스 접속

웹 UI의 개발 > 코드 스페이스 메뉴에서 배포된 코드스페이스를 찾아 **`연결`** 의 VSCode 아이콘을
누릅니다. 상태가 **`배포 완료`** 여야 접속됩니다. 중지 상태면 `시작`을 먼저 누릅니다.

### 3.2 사전 구성 항목

| 항목 | 상태 |
|---|---|
| 파이썬과 의존성 | **설치 완료.** `pip install`을 다시 실행할 필요가 없습니다 |
| Bitbucket 저장소 클론 | 완료되어 있습니다 |
| `resource/*.yaml` | 모델 서빙 주소와 인증키가 구성되어 있습니다 |
| 샘플 문서 | `genon/preprocessor/sample_files/` |

주요 경로는 다음과 같습니다.

| 경로 | 설명 |
|---|---|
| `UPDATED_AT` | 갱신 시각 기록. **배포할 때마다 갱신합니다** ([4.3](#43-배포-절차)) |
| `genon/preprocessor/facade/parser_processor.py` | 파싱. 주 수정 대상 |
| `genon/preprocessor/facade/chunking_processor.py` | 청킹. 주 수정 대상 |
| `genon/preprocessor/resource/` | config yaml + `custom_field_*.yaml` + 프롬프트 |
| `genon/preprocessor/resource/templates/` | 새 문서 유형 템플릿 5종 |
| `genon/preprocessor/examples/` | 검증 스크립트 |
| `genon/preprocessor/sample_files/` | 샘플 문서 |
| `main.py` | FastAPI 앱. 수정 대상이 아닙니다 |

### 3.3 신속한 확인 방법 — facade 단독 실행

facade 두 파일은 **독립적으로 실행할 수 있습니다.** 서버를 실행하지 않고 문서 한 건을 처리하여 결과를 확인할 수 있습니다.
훅이나 `custom_field_*.yaml`을 수정한 직후 확인하는 방법으로 가장 빠릅니다.

```bash
# 실행 위치: 저장소 루트 (import 경로 때문에 반드시 -m으로 실행합니다)
python -m genon.preprocessor.facade.parser_processor 계약서.pdf --doc-type contract -o parsed.json
python -m genon.preprocessor.facade.chunking_processor parsed.json --doc-type contract -o chunks.json
```

| 인자 | 뜻 |
|---|---|
| `--doc-type` | `custom_field_*.yaml` 매칭과 훅 게이팅에 쓰입니다. **생략하면 해당 처리가 통째로 동작하지 않습니다** |
| `--config` | 프로세서 설정 yaml 경로. 미지정 시 기본 경로를 찾습니다 |
| `-o, --out` | 결과 JSON 경로. 생략하면 stdout |
| `--log-level` | `5` DEBUG / `4` INFO / `3` WARNING / `2` ERROR / `1` CRITICAL / `0` 로그 없음 |

청커의 입력은 원본 문서가 아니라 **파서가 생성한 결과 JSON**입니다. 청킹만 반복해서 검증하는
경우에는 파싱을 다시 실행하지 말고 저장한 `parsed.json`을 재사용하세요. 모델 서빙을 호출하지
않으므로 몇 초 안에 완료됩니다.

### 3.4 배포된 코드서빙 직접 호출

코드스페이스는 Genos 클러스터 안에 있으므로 코드서빙을 직접 호출할 수 있습니다. 게이트웨이를
거치지 않으니 인증키가 필요 없습니다.

```
http://code-serving-<ID>-<리비전>:8080/<route>
```

`<ID>`와 `<리비전>`은 제공받은 코드서빙 접속 정보입니다([0.1](#01-제공-항목)).

```bash
# 실행 위치: 코드스페이스 터미널
export CS="http://code-serving-<ID>-<리비전>:8080"

curl "${CS}/health"
# -> {"status":"ok"}

curl "${CS}/version"
# -> 4.4 참고

curl "${CS}/parser" -H 'Content-Type: application/json' \
  --data '{"file_path": "/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf", "params": {}}'
```

> `file_path`는 **서빙 컨테이너 내부의 경로**입니다. 업로드 경로나 스토리지 키가 아닙니다.
> 저장소에 동봉된 샘플 파일을 쓰면 확실합니다.

응답은 성공과 실패 모두 **HTTP 200** 입니다. `code` 값으로 판단하세요.

### 3.5 모델 서빙 연결 설정

연결 설정은 `resource/*.yaml`에 이미 구성되어 있습니다.

| 용도 | config 안의 이름 |
|---|---|
| 문서 레이아웃 분석 | `layout.genos_layout.endpoint` |
| 목차·메타데이터·표 설명 | 각 `enrichment` 항목의 `url` |
| OCR | `ocr.paddle.ocr_endpoint` (서빙 ID가 아니라 **주소**입니다) |

미치환 플레이스홀더의 존재 여부를 확인하는 명령입니다. 주석 줄은 제외합니다.

```bash
# 실행 위치: 저장소 루트. 아무것도 안 나와야 정상
grep -rn "<[A-Z_]*>" genon/preprocessor/resource/ | grep -vE ':[0-9]+: *#'
```

미치환 값이 있으면 기동 시 `미치환 placeholder 발견` 경고가 남습니다. 기동 자체는 됩니다.
사용하지 않는 기능은 연결값을 설정하지 않고 **비활성화할 수 있습니다.** OCR을 사용하지 않으면 `ocr.ocr_mode: disable`, enrichment를
사용하지 않으면 각 항목의 `enable: false`를 설정합니다.

> API 키는 **비밀번호처럼** 취급합니다. 문서·이슈·채팅에 실제 값을 넣지 마세요.

> 저장소의 **예시값에 주의하세요.** `README.md`와 `examples/` 예제에 나오는 주소·ID·인증키는
> 다른 환경의 값입니다. 그대로 복사하여 사용하면 현재 환경이 아닌 다른 서빙으로 요청이
> 전송될 수 있습니다.

---

## 4. 배포

### 4.1 모니모 배포 구조

일반 Genos 코드서빙은 개발자가 gitea에 push 하고 웹 UI에서 리비전을 만들어 배포합니다.
**모니모 환경의 배포 방식은 일반 Genos 코드서빙과 다릅니다.** 코드서빙은 고정되어 있으며, 배포는 Bitbucket 브랜치와 전용 CI/CD를 통해 수행됩니다.

| | 일반 Genos 코드서빙 | **모니모** |
|---|---|---|
| 소스 저장소 | gitea (Genos 내부) | **Bitbucket** |
| 배포 트리거 | 웹 UI에서 리비전 생성 | **`prd` / `dev` 브랜치 머지** |
| 코드 반영 방식 | 컨테이너 기동 시 git clone | **CI/CD가 도커 이미지를 빌드하여 지정 위치에 적재** |
| 코드서빙 | 개발자가 생성하고 리비전을 배포 | **고정. 개발자가 생성하지 않음** |
| 머지·배포 권한 | 개발자 | **별도 담당자** |

```
[코드스페이스]   feature/prd 또는 feature/dev에서 수정
       │ ① UPDATED_AT 갱신  ② commit / push
       ▼
[Bitbucket]      ③ PR 생성
       │ ④ 담당자가 prd / dev로 merge, sync
       ▼
[CI/CD]          도커 이미지 빌드 -> 컨테이너 지정 위치에 코드 적재
       │
       ▼
[Genos]          고정 전처리기 코드서빙에 반영 (재기동)
       │
       ▼
[코드스페이스]   ⑤ /version 호출로 반영 확인      ← 건너뛰지 마세요
```

### 4.2 브랜치 모델

| 브랜치 | 용도 |
|---|---|
| `prd` | **운영** 코드서빙에 적용됩니다 |
| `dev` | **개발** 코드서빙에 적용됩니다 |
| `feature/prd` | 운영 대상 작업 브랜치 |
| `feature/dev` | 개발 대상 작업 브랜치 |

`prd`와 `dev`에 **직접 push 하지 않습니다.** 머지와 sync는 담당자가 합니다.

### 4.3 배포 절차

#### ① `UPDATED_AT`을 먼저 갱신합니다

**이 단계를 빠뜨리면 ⑤ 에서 반영 여부를 확인할 방법이 없습니다.**

Genos 웹 UI는 "지금 실행 중인 코드서빙이 내 수정본으로 기동된 것인지"를 알려 주지 않습니다.
코드서빙이 고정되어 있어 이름도 리비전도 그대로이기 때문입니다. 확인할 수 있는 유일한 단서는
소스에 직접 적어 넣은 갱신 시각입니다.

저장소 루트(`main.py`와 같은 위치)의 `UPDATED_AT` 파일 **첫 번째 줄**을 갱신합니다. 형식은 자유이며
최대 100자까지 읽습니다. 파일이 없으면 생성하세요.

```bash
# 실행 위치: 저장소 루트
echo "2026-09-17 14:30 공지사항 doc_type 추가" > UPDATED_AT
```

> `VERSION` 파일은 **직접 수정하지 마세요.** JSON이 손상되면 `/version` 응답의 `version`까지
> `unknown`으로 나옵니다.

#### ② 커밋 / 푸시

```bash
# 실행 위치: 코드스페이스 터미널, 저장소 루트
git status --short                                   # 변경 파일 확인
git add genon/preprocessor/resource UPDATED_AT       # 고친 경로만 명시
git commit -m "공지사항 doc_type 추가"
git push origin feature/dev
```

> **`git add .`를 사용하지 마세요.** `.venv/`, `__pycache__/`, 결과 JSON, 로컬 실험용 API 키가 포함될 수 있습니다.
> push 전에 `git diff --stat`으로 의도한 파일만 포함되었는지 확인하세요.

#### ③ PR 생성

Bitbucket에서 작업 브랜치를 대상 브랜치로 PR을 만듭니다.

| 작업 브랜치 | 대상 |
|---|---|
| `feature/dev` | `dev` |
| `feature/prd` | `prd` |

#### ④ 담당자에게 머지를 요청합니다

머지와 sync는 담당자가 처리합니다. 이후 CI/CD가 도커 이미지를 빌드하고 코드를
컨테이너의 지정 위치에 적재한 뒤 코드서빙이 재기동됩니다. **빌드에 몇 분 걸립니다.**

### 4.4 반영 확인 — `/version`

코드스페이스에서 코드서빙을 직접 호출합니다([3.4](#34-배포된-코드서빙-직접-호출)).

```bash
curl "http://code-serving-<ID>-<리비전>:8080/version"
```

| 응답 필드 | 뜻 |
|---|---|
| `manual_updated_at` | `UPDATED_AT`의 첫 줄. **① 에서 적은 값** |
| `started_at` | 컨테이너 기동 시각 |
| `version` · `commit` | 릴리스 스탬프 |

판정 기준은 다음과 같습니다.

| `manual_updated_at` | 뜻 | 할 일 |
|---|---|---|
| 내가 적은 값 | **반영됨** | 기능 확인으로 넘어갑니다 |
| 옛 값 | 아직 머지 전이거나 빌드 중 | 담당자에게 머지 여부 확인 |
| `started_at`보다 **늦음** | 코드는 올라갔는데 **재기동되지 않음** | 담당자에게 재기동 요청 |

> `/health` 로는 **판정하지 마세요.** 컨테이너가 기동했다는 사실만 알려 줍니다. 예전 코드로
> 실행 중이어도 `{"status":"ok"}`가 나옵니다.

반영이 확인되면 실제 문서 한 건을 처리하여 기능을 확인합니다.

```bash
curl "${CS}/parser" -H 'Content-Type: application/json' \
  --data '{"file_path": "/app/src/service/.../notice.xlsx", "params": {"doc_type": "notice"}}'
```

| 바꾼 것 | 확인 지점 |
|---|---|
| 새 `doc_type` | 응답 `data.elements`의 `metadata`에 목표필드가 실렸는지 |
| `chunk_size` · `chunk_mode` | `/chunker` 결과의 청크 개수와 길이 |
| enrichment `enable` | 응답의 해당 항목 유무, 처리 시간 변화 |
| 플레이스홀더 치환 | 로그에서 `미치환 placeholder` 경고가 사라졌는지 |

### 4.5 릴리스 갱신 시 사용자 수정 사항 유지

전처리기는 릴리스 단위로 전체 갱신되므로 수정한 facade 2개와 `resource/` 설정도 함께
덮어써집니다. 갱신 전에 별도로 보관하고 갱신 후 다시 적용하세요.

```bash
# 실행 위치: 저장소 루트. 고친 경로만 한정합니다
git diff -- genon/preprocessor/facade/parser_processor.py \
             genon/preprocessor/facade/chunking_processor.py \
             genon/preprocessor/resource > my_change.patch

git apply --stat my_change.patch     # 담긴 파일 목록 확인

# … 릴리스 전체 갱신 …

git apply my_change.patch            # 충돌하면 patch를 확인하여 수동으로 반영
```

훅 시그니처(`edit_input` / `edit_document` / `edit_chunk` / `edit_output`)와 `ROUTES` 형태는
**고정 API** 로 유지됩니다. 그것이 바뀌지 않은 릴리스에서는 `git apply`가 그대로 적용됩니다. 릴리스
노트의 "템플릿 변경 있음 / 없음" 표시를 먼저 확인하세요.

> `resource/` 전체를 백업한 뒤 그대로 **복원하면 안 됩니다.** 새 릴리스가 추가한 config 키와
> 프롬프트 파일까지 이전 버전으로 되돌아갑니다. **새 파일을 기준으로** 사용자 설정값만 옮겨 적용하세요.
> 그다음 [3.5](#35-모델-서빙-연결-설정)의 미치환 플레이스홀더 검사를 실행합니다.

---

## 5. 구성 YAML 옵션

코드를 수정하지 않고 동작을 변경하는 방법입니다. 기본 설정값은 이미 구성되어 있습니다.

> facade 인스턴스는 **모듈 로드 시점에 한 번만** 생성됩니다. 따라서 **구성 YAML을 변경하면
> 재배포해야 반영됩니다.** 재배포 없이 값을 변경하려면 요청 `params`를 사용하세요([5.3](#53-요청-params로-재배포-없이-값-변경)).

### 5.1 구성 파일별 적용 대상

| config 파일 (`genon/preprocessor/resource/`) | 엔드포인트 |
|---|---|
| `parser_processor_config.yaml` | `/parser`, `/parser_upload` |
| `chunking_processor_config.yaml` | `/chunker` |
| `custom_field_*.yaml` (기본 제공 15개) | 문서 유형별 값 추출과 청크 본문 ([1장](#1-새-문서-유형-추가하기)) |
| `templates/custom_field_TEMPLATE_*.yaml` | 새 문서 유형을 만들 때 복사할 원본 5종. 등록하지 않습니다 |

### 5.2 주요 구성 옵션

"적용 파일" 열을 반드시 확인하세요. 파일마다 키 구조가 다릅니다.

| 섹션 · 키 | 적용 파일 | 기본값 | 가능한 값 | 변경 시점 |
|---|---|---|---|---|
| `output.format` | **parser 전용** | `docling` | `json` / `html` / `markdown` / `docling` | **`/chunker`에 넘기려면 `docling`** 이어야 `data.document`가 생깁니다 |
| `ocr.ocr_mode` | parser | `auto` | `auto` / `force` / `disable` | 스캔 문서가 많으면 `force`, OCR 서버가 없으면 `disable` |
| `chunking.chunk_size` | chunking | `1000` | 정수 | 청크 길이. `0` 초과 `1024` 미만은 `1024`로 보정되므로 기본 실효값은 `1024` |
| `chunking.chunk_mode` | chunking | `split_only` | `split_only` / `resize_all` | `split_only`는 섹션 구조 유지(작은 청크 다수), `resize_all`은 크기 기준 재조립(균일) |
| `chunking.tokenizer_type` | chunking | `char` | `char` / `huggingface` | `chunk_size`의 **단위가 바뀝니다** |
| `chunking.include_chunk_header` | chunking | 켜짐 | `0` / `1` | 청크 선두의 `HEADER:` 줄이 필요 없을 때 `0` |
| `chunking.text_cleanup` | chunking | 없음 | 정규식 규칙 | 본문에서 특수문자·노이즈를 걷어냅니다 |
| `enrichment` 각 항목의 `enable` | parser | 항목별 상이 | `true` / `false` | LLM 호출 비용과 시간을 줄일 때 |
| `formats.xlsx.processing_mode` | parser | `tabular` | `tabular` / `docling` | 엑셀을 표로 다룰지 문서로 다룰지 |
| `defaults.log_level` | 전부 | `4` | `5`=DEBUG ~ `1`=CRITICAL, `0`=NOLOG | 디버깅할 때 `5` |

> `chunk_size: 0`은 "청크 1개"가 아닙니다. 크기 기반 **병합과 분할을 끄는** 값입니다.
> docling 문서 입력이면 섹션 구조 기준 청크가 그대로 남아 오히려 더 많아질 수 있습니다.
> 청크를 크게 합치려는 목적이라면 `0`이 아니라 충분히 큰 값을 주세요.

> `tokenizer_type`을 바꾸면 **`chunk_size`의 단위가 바뀝니다.** `10000`은 `char`에서 1만 자,
> `huggingface`에서 1만 토큰(대략 2~3만 자)입니다. 함께 조정하세요.

### 5.3 요청 `params`로 재배포 없이 값 변경

`params`는 **YAML보다 우선**합니다. 옵션을 검증할 때 사용합니다.

```bash
curl "${CS}/parser" -H 'Content-Type: application/json' \
  --data '{"file_path": "...", "params": {"log_level": 5, "toc": 0, "img_desc": 1}}'
```

| facade | 자주 쓰는 `params` 키 |
|---|---|
| parser | `doc_type`, `toc`, `img_desc`, `chart_desc`, `table_desc`, `table_refine`, `doc_summary`, `save_images`, `use_hwp_sdk`, `log_level` |
| chunking | `document`, `chunk_size`, `chunk_mode`, `include_chunk_header`, `chunk_overlap`, `table_as_chunk`, `export_to_html`, `log_level` |
| 공통 | `llm_cache`, `error_policy`(`strict`/`lenient`), `request_deadline`(초) — 의미는 [부록 C.5](#c5-공통-요청-params) |

0/1 플래그 형태의 키는 `0`/`1` 또는 `true`/`false` 둘 다 받습니다.

`/chunker`가 입력을 받는 통로는 두 가지입니다. `params.document`에 파싱 결과를 **인라인 전달**하는 것이
우선이고, 없으면 `file_path`가 가리키는 **서버 내부의 `.json` 파일**을 읽습니다.

### 5.4 프롬프트 파일 구성

enrichment 항목은 프롬프트를 별도 md 파일로 분리합니다.

```yaml
enrichment:
  - image_description:
      enable: true
      prompt_template_file: "prompt_image_description_default.md"
```

우선순위는 **`prompt_template_file` > YAML 내부의 inline `prompt_template` > 코드 내장 기본값**
입니다. 경로는 구성 YAML이 있는 폴더를 기준으로 합니다. 프롬프트만 변경하는 경우에는 `resource/prompt_*.md`를
수정하여 push합니다.

---

## 6. 주요 동작 방식

### 6.1 파싱 결과 형식

| 형식 | 내용 | 생성 경로 |
|---|---|---|
| 문서형 `{"document": {...}}` | DoclingDocument 직렬화 | pdf · hwp · docx · ppt · md · html, 매핑 설정이 있는 json과 엑셀 |
| 요소형 `{"elements": [...]}` | element 배열 | 엑셀 행, JSON 레코드, csv, txt, 이미지, 그 밖 |

파싱 결과는 두 가지 형식으로 반환됩니다. `/chunker`는 두 형식 중 어느 것이 입력되어도 자동으로 판별합니다.
한 응답에 두 형식이 모두 있으면 **`document`만** 사용합니다. element는 `{category, content, coordinates, id, page}`의 5개 키로 구성되며, 행 기반 element에는 행 metadata를 담은 `metadata` 키가 추가됩니다.

> `ppt`/`pptx`는 PDF로 변환한 뒤 파싱합니다. **변환에 실패하면 요소형으로 대체 처리**되므로 같은
> 파일이 환경에 따라 다른 형태로 나올 수 있습니다.

두 처리 경로의 청킹 방식은 다릅니다.

| | 문서형 경로 | 요소형 경로 |
|---|---|---|
| 청커 | `GenosSmartChunker` (구조 인식) | 문자 기반 splitter |
| 크기 단위 | `char` / `huggingface` 선택 | **항상 문자 수** |
| overlap | 없음 | `chunking.recursive.chunk_overlap` (기본 100) |
| 좌표·미디어 | 실제 값 | `"."` 고정값 |

### 6.2 청킹 동작

facade의 `GenosSmartChunker` ClassVar는 청크 표기 방식을 정합니다.

| ClassVar | 기본값 | 의미 |
|---|---|---|
| `CHUNK_HEADER_PREFIX` | `"HEADER: "` | 청크 선두 라벨. 빈 문자열이면 경로만 붙습니다 |
| `CHUNK_HEADER_SEP` | `" > "` | 섹션 경로 안 구분자(부모에서 자식) |
| `CHUNK_PATH_SEP` | `" \| "` | 형제 경로 사이. 위와 달라야 구분됩니다 |
| `CHUNK_PATH_MAX_LEAVES` | `5` | 다경로 청크의 리프 상한. 초과분은 `… 외 N개` |
| `PICTURE_ANNOTATION_TEXT` | `True` | 그림 annotation을 청크 본문에 싣습니다 |
| `TABLE_DESCRIPTION_MODE` | `prefix_only` | 표 설명은 검색용 접두만 |

크기와 병합 동작은 ClassVar가 아니라 설정에서 정의합니다([5.2](#52-주요-구성-옵션)).

> 섹션 인식은 **정규식이 아닙니다.** 파서가 붙인 라벨(`SECTION_HEADER`/`TITLE`)로 판정합니다.
> "제N조" 같은 텍스트 패턴으로 자르려면 공용 모듈 세 곳을 함께 고쳐야 하므로, 직접 하지 말고
> 솔루션 개발자에게 요청하세요.

어느 쪽이든 청크 본문이나 경계가 바뀌면 **재색인이 필요합니다.**

### 6.3 출력 스키마 (청크)

청크 하나가 벡터 DB 1행이 됩니다. 스키마는 **추가 필드를 허용**하므로(`extra=allow`) 선언 없이도
`custom_fields` 목표필드와 문서 metadata가 그대로 실립니다.

| 분류 | 필드 |
|---|---|
| 본문 | `text` (앞에 `HEADER: <섹션 제목들>` 줄이 붙습니다) |
| 통계 | `n_char` · `n_word` · `n_line` (본문에서 자동 계산) |
| 위치 | `i_page` · `e_page` · `n_page` · `i_chunk_on_page` · `n_chunk_of_page` · `i_chunk_on_doc` · `n_chunk_of_doc` |
| 참조 | `chunk_bboxes` · `media_files` (**둘 다 JSON 문자열**입니다) |
| 문서 메타 | `title` · `reg_date` · `created_date` · `appendix` · `file_path` · `guardrail_categories`(마스킹 미사용 환경에서는 빈 값) |
| 표 메타 | `has_table` · `table_refs` · `table_split_index` · `table_split_total` |

> **필드 추가는 안전하지만, 삭제와 타입 변경은 위험합니다.** 이 스키마는 벡터 DB 적재 형식과 같으므로 필드를
> 삭제하거나 이름을 변경하면 이미 적재된 데이터와 불일치할 수 있습니다.

### 6.4 응답 형식 및 오류

응답 형식은 `main.py`가 구성합니다. facade는 `data`에 포함할 값만 반환합니다.

```json
{ "code": 0, "errMsg": "success", "data": { } }
```

- 성공과 실패 **모두 HTTP 200**. 실패는 `code`가 0이 아닙니다.
- 실패 응답에는 `error_code`, `error_type`, `stage`, `traceback`이 함께 실립니다.
- facade가 `GenosServiceException`으로 던진 오류는 `error_code`가 보존됩니다. 그 외 예외는
  타입에 따라 `INPUT_ERROR` / `TIMEOUT_ERROR` / `INTERNAL_ERROR`로 자동 분류됩니다.

**응답이 예상과 다른 경우**

| 응답 | 원인 | 확인할 것 |
|---|---|---|
| `…지원하지 않습니다` (`code:1`) | facade에 `IS_PARSER`/`IS_CHUNKER` 마커가 없어 요청이 도달하지 못함 | 클래스명(`DocumentProcessor`)과 마커를 바꾸지 않았는지 |
| `/parser`는 되는데 `data.document`가 없음 | `output.format`이 `docling`이 아님 | `parser_processor_config.yaml` |
| 청크가 1개만 나오거나 합쳐지지 않음 | `chunk_size: 0`은 병합·분할을 끄는 값 | `0` 대신 충분히 큰 양수, 필요하면 `chunk_mode: resize_all` |
| `code:1` + 연결 오류 | 모델 서빙 주소나 키가 실행 환경과 일치하지 않음 | [3.5](#35-모델-서빙-연결-설정) |

---

## 부록 A. 용어집

**Genos 플랫폼**

| 용어 | 뜻 |
|---|---|
| **코드서빙(Code Serving)** | FastAPI 앱을 Genos에서 실행하는 기능. 모니모에서는 **고정**되어 있습니다 |
| **코드스페이스(Code Space)** | 브라우저에서 열리는 VSCode 개발 환경 |
| **모델 서빙** | LLM·OCR·레이아웃 모델을 API로 제공하는 서비스 |
| **게이트웨이** | 서빙 호출을 중계하는 진입점. 코드스페이스에서는 거치지 않습니다 |

**전처리기(doc parser)**

| 용어 | 뜻 |
|---|---|
| **facade** | 전처리기 유형별 외부 확장 지점을 제공하는 파일(`facade/*_processor.py`). 진입 클래스는 `DocumentProcessor` |
| **훅 메소드(hook)** | facade에서 오버라이드하면 core가 정해진 시점에 호출하는 메소드. `edit_input` · `edit_document` · `edit_chunk` · `edit_output` |
| **toolbox (`tb`)** | 훅에서 사용할 수 있도록 다시 노출한 기능 모음. YAML `transform:`이 호출하는 함수와 같습니다 |
| **custom_fields** | 문서 유형별 값 추출 설정(`custom_field_*.yaml`). `source.kind` 5가지로 구분됩니다 |
| **doc_type(문서유형)** | 요청 `params`로 전달하는 문서유형 키. 일치하는 `custom_fields`만 적용하고 모든 청크에 기록합니다 |
| **docling** | 문서 파싱 엔진. wheel로 제공되므로 직접 수정할 수 없습니다 |
| **element** | 요소형 결과의 한 조각. `{category, content, coordinates, id, page}` |
| **청크(chunk)** | 벡터 DB에 넣을 텍스트 조각 |
| **enrichment** | 목차·메타데이터·이미지/표 설명을 LLM으로 덧붙이는 단계 |
| **목표필드** | `custom_field_*.yaml`의 `fields`에 선언한, 만들어 낼 값. 적재 DB 컬럼이 됩니다 |

## 부록 B. 지원 요청이 필요한 경우

다음은 직접 해결할 수 없는 항목입니다. **솔루션 개발자 또는 배포 담당자에게 요청하세요.**

| 상황 | 요청할 것 |
|---|---|
| 코드스페이스 개발 환경에 문제가 발생함 | 재구축 |
| 모델 서빙 주소·인증키를 모름 | 값 확인 또는 권한 |
| 코드서빙 `ID` · 리비전을 모름 | 접속 정보 |
| PR을 올렸는데 머지가 안 됨 | `prd` / `dev` 머지와 sync |
| `/version`의 `manual_updated_at`이 `started_at`보다 늦음 | 코드서빙 재기동 |
| 빌드가 실패함 | CI/CD 로그 확인 |
| `processing/core/`를 고쳐야 할 것 같음 | 훅 추가 (고치기 전에 문의하세요) |
| docling 자체 동작을 바꿔야 함 | 엔진 수정 요청 |
| 새 파이썬 패키지가 필요함 | 베이스 이미지 의존성 추가 |

## 부록 C. custom_fields 설정 레퍼런스

1장에서 쓰는 설정 키의 전체 목록입니다. `kind`마다 지원 여부가 다르고, **지원하지 않는 키를
적으면 기동에 실패합니다.**

✔ = 지원 / ✗ = 그 키를 적으면 **기동 실패** / ⚠ = 통과하지만 무효이거나 제한

### C.1 설정 표기와 내부 이름

괄호 안이 **내부 이름**입니다. 설정에 적는 이름이 아니라 **오류 메시지에 나오는 이름**이므로,
기동 실패 로그는 이 표로 되짚습니다. 헷갈리는 동음이의 하나 — `template`의 내부 이름은 `derive`입니다.

**값 가져오기 (`fields:`)**

| 기능 | 설정 키 (내부 이름) | rows | records | sections | document |
|---|---|:-:|:-:|:-:|:-:|
| 별칭 | `alias` | ✔ | ✔ | ✔ | ✔ |
| 반복 key 전부 수집 | `collect` (`collect_key_map`) | ✗ | ✔ | ✗ | ✗ |
| 상수 | `const` (`constants`) | ✔ | ✔ | ✔ | ✔ |
| 기본값(빈 값만) | `default` (`defaults`) | ✔ | ✔ | ✔ | ✔ |
| 값 표기 통일 | `values` (`value_map`) | ✔ | ✔ | ✔ | ✔ |
| 값 변환(체이닝) | `transform` (`transforms`) | ✔ | ✔ | ⚠ 표 뭉갬 | ⚠ 표 뭉갬 |
| 필드 결합 | `template` (**`derive`**) | ✔ | ✔ | ✔ | ✔ |
| JSON 한 칸에 묶기 | `pack` | ✔ | ✔ | ✔ | ✔ |
| 청크 메타에서 빼기 | `meta: false` (`meta_include`) | ✔ | ✔ | ✔ | ✔ |
| 객체를 값으로 받기 | `raw` (`raw_fields`) | ✗ | ✔ | ✔ 루트만 | ✗ |
| 순번 매기기 | `seq` (`sequence`) | ✔ | ✔ | ✗ | ✗ |

`pack`은 **정말 맨 뒤**입니다 — `seq`로 매긴 순번과 `llm`이 채운 값까지 확정된 뒤에 묶습니다.
`meta: false`인데 본문·선별·파생 어디에도 쓰이지 않으면 경고만 남기고 기동합니다(값을 만들고 버리는
죽은 설정입니다).

**청크 본문 (`body:`)**

| 기능 | 설정 키 (내부 이름) | rows | records | sections | document |
|---|---|:-:|:-:|:-:|:-:|
| 본문 구성 필드 | `body.fields` (`text_fields`) | ✔ 선택 | ✔ **필수** | ⚠ 뜻이 다름¹ | ✗ |
| 항목명 | `body.labels` (`field_labels`) | ✔ | ✔ | ✔ | ✔ |
| 긴 본문 분할 | `body.split` (`split`) | ✔ | ✔ | ⚠ 항상 분할 | ✗ |
| 모든 청크에 반복 접두 | `body.repeat` (`chunk_prefix_fields`) | ✔ split 시만 | ✔ split 시만 | ⚠ 자동 생성 | ✔ |
| 첫 청크에만 1회 | `body.once` (`first_chunk_fields`) | ✗ | ✗ | ✔ | ✔ |
| 본문을 메타 필드에 복사 | `body.mirror_to` (`body_fields`) | ✗ | ✗ | ✗ | ✔ |

¹ sections의 `body.fields`는 본문 구성이 아니라 **공통 필드를 청크 접두에 실을지 정하는
스위치**입니다. 본문은 트리 순회가 만듭니다.

**입력 데이터 구조 · 선별 · LLM**

| 기능 | 설정 키 (내부 이름) | rows | records | sections | document |
|---|---|:-:|:-:|:-:|:-:|
| 레코드 배열 위치 | `source.records_at` (`records`) | ✗ | ✔ | ✗ | ✗ |
| 못 찾을 때 정책 | `source.on_missing` (`missing_policy`) | ✗ | ✔ | ✔ | ✗ |
| 여러 건 접기 | `source.merge_rows` (`row_merge`) | ✔ | ✔ | ✗ | ✗ |
| 섹션 표시 이름 | `source.sections` | ✗ | ✗ | ✔ | ✗ |
| 서브트리 제외 | `source.ignore_keys` | ✗ | ✗ | ✔ | ✗ |
| 포맷 전처리 | `source.pre.markdown` / `.html` | ⚠ **무시** | ⚠ **무시** | ⚠ **무시** | ✔ |
| 필수값(빈 값 제외) | `require.fields` (`required`) | ✔ 건별 | ✔ 건별 | ✔ 문서 전체 | ✗ |
| 값 기반 제외 | `filter` | ✔ | ✔ | ✗ | ✗ |
| 필드별 LLM 생성 | `llm:` (`llm_fields`) | ✔ 행별 | ✔ 레코드별 | ✔ 문서 1회 | (본체가 LLM) |

> `extractor`는 **적지 않는 것이 기본입니다.** 설정 파일의 `source.kind`가 정합니다. 굳이 적으면
> 그 값과 일치해야 하고, 어긋나면 **설정에 적은 적도 없는 키 이름**으로 기동이 실패해 원인을
> 짚기 어렵습니다.

> `enrichment.toc.doc_type`은 **완전히 다른 값**입니다. 그쪽은 목차 추출 알고리즘을 고르는
> 옵션이라 `normal` / `law`만 받습니다. `custom_fields`의 `doc_type`과 섞지 마세요.

### C.2 `transform` 10종

체이닝됩니다. 잘못된 이름·빠진 인자·컴파일되지 않는 정규식은 **기동 시** 걸립니다.

| 이름 | 인자 | 하는 일 |
|---|---|---|
| `date_int` | — | 날짜 텍스트 → `YYYYMMDD` 정수 |
| `date_int_flex` | — | 위 + 2자리 연도(`26.07.01`)·구분자 없는 `260701` |
| `text_norm` | — | NFKC + 공백 축약 + casefold (중복 판정용) |
| `regex_sub` | `pattern`, `repl` | 정규식 치환 (`"18,000원"` → `"18000"`) |
| `regex_extract` | `pattern`, `group` | 정규식 오려내기. 미매칭 시 `None` |
| `to_int` | `on_error` | 숫자만 남겨 정수화 |
| `truncate` | `length`, `suffix` | 길이 자르기(적재 컬럼 길이 맞춤) |
| `html_text` | — | HTML로 **강제** 평문화. 표·목록 유지 |
| `text` | — | JSON/HTML/평문 **자동 판별** 후 평문화 |
| `to_json` | `on_scalar`, `key` | 값을 **유효한 JSON 문자열**로 맞춤(적재 DB의 JSON 컬럼용) |

인자가 있는 변환기는 `{name: ...}` 형태로 적고, 여러 개를 이어 붙일 수 있습니다.

```yaml
fields:
  FEE_AMT:
    alias: [수수료]
    transform:
      - {name: regex_sub, pattern: "[^0-9]", repl: ""}
      - {name: to_int}
```

`to_json`은 적재 DB의 JSON 컬럼에 넣을 필드에 씁니다. 스칼라가 들어오면 `on_scalar: wrap`(기본)이
`{key: 값}`으로 감싸고, `on_scalar: drop`은 `null`로 버립니다. `key`는 감쌀 때 쓸 이름입니다(기본
`value`). **체인 맨 뒤여야 합니다** — 뒤에 `truncate`가 오면 JSON이 잘려 기동에 실패합니다.
`body.fields`·`body.repeat`·`body.once`·`body.mirror_to`에 쓸 수 없고 `pack`으로 다시 묶을 수도
없습니다(둘 다 기동 실패).

`pack`과 역할이 다릅니다 — `pack`은 필드 **여럿**을 컬럼 하나에 담고, `to_json`은 필드 **하나**의
모양을 보장합니다.

### C.3 `source` 보조 키

```yaml
source:
  kind: records
  records_at: eventList       # 레코드 배열이 담긴 key. 키 이름만 적습니다(임의 깊이에서 찾음)
  on_missing: skip            # skip(기본, 경고만) | error
  merge_rows:                 # 여러 건에 걸쳐 쪼개진 값을 한 건으로 접습니다
    group_by:  [CMP_ID]
    order_by:  SEQ_NO
    concat:    [DETAIL_HTML]
    separator: ""
```

| 키 | 하는 일 |
|---|---|
| `records_at` | 레코드 배열의 key 이름. 생략하면 payload가 배열이면 원소마다 1건, 단일 object면 그 자체가 1건 |
| `on_missing` | `records_at`·섹션을 못 찾았을 때. `skip`(기본, 경고만) / `error`(요청 실패) |
| `merge_rows` | `group_by`가 같은 **연속** 건만 한 묶음으로 접습니다. 병합은 값 변환·파생보다 **먼저** 실행됩니다 |
| `sections` | `kind: sections`의 섹션 표시 이름 |
| `ignore_keys` | `kind: sections`에서 제외할 **key 이름 glob**(경로가 아닙니다). 매칭되면 서브트리 통째 제외 |
| `pre.markdown` / `pre.html` | `kind: document`의 포맷 전처리. 다른 kind에서는 무시됩니다 |

값으로 거르는 `filter`는 별도 블록입니다. 연산자는 `in`과 `not_in` 두 가지입니다.

```yaml
filter:
  - {field: DEL_YN, not_in: [Y]}
```

### C.4 별칭이 값을 찾는 범위

**이름으로 찾고, 경로로는 찾지 못합니다.** `a.b.c` 처럼 적어도 경로로 해석되지 않습니다.

| 대상 | 탐색 범위 | 우선순위 |
|---|---|---|
| records `source.records_at` | 문서 **임의 깊이 BFS** | 최초 매칭 1개 |
| records `alias` | **레코드 안 임의 깊이 BFS** | **깊이 우선**, 같은 레벨에서만 선언 순서 |
| records `collect` | 레코드 안 BFS **레벨 순서**, 중복 제거 | 전부(중복 제외) |
| sections `alias` | **문서 루트의 스칼라만** | 루트 1회 확정 후 불변 |
| sections 본문 | 트리 **자동 전수 순회**(깊이 ≤ 12, 노드 ≤ 5000) | 미설정 key도 자동 포함 |
| sections `ignore_keys` | **key 이름 glob**, 경로 아님 | 매칭 시 서브트리 통째 제외 |
| rows `alias` | **시트 첫 행 헤더**(깊이 없음) + 시트 컨텍스트 | 선언 순서, 실제 컬럼 우선 |
| document `alias` | **front matter 최상위 키만**(중첩 미탐색) | **순수 선언 순서** |

> "여러 개 적으면 먼저 찾은 것"이라는 설명은 **document에만** 문자 그대로 맞습니다.
> records·rows·sections는 깊이와 헤더가 먼저 좌우합니다.

**`raw` — 객체를 값으로 받습니다 (records · sections)**

기본 규칙은 "중괄호로 묶인 객체는 값이 아니라 **더 파고들 구조**"입니다. 그래야 `eventList` 같은
레코드 배열이 필드 하나의 값으로 잘못 잡히지 않습니다. 그래서 아래에서 `attrs`는 `alias`만으로는
`null`이 됩니다.

```json
{ "title": "삼성 iD ON 카드",
  "attrs": { "annual_fee": 18000, "benefits": ["온라인 5% 할인"] } }
```

객체를 통째로 적재 컬럼에 담아야 하면 그 필드에만 판정을 끕니다.

```yaml
fields:
  PRODUCT_ATTRS:
    alias: [attrs]
    raw: true
    transform: [{name: to_json}]   # 적재 DB의 JSON 컬럼에 넣을 것이라면 함께
```

`alias`로 값을 찾는 필드에만 쓸 수 있습니다(`const`·`default`로만 만드는 필드에 걸면 기동 실패).
`raw`만 쓰면 값이 객체 그대로 실려 청크 출력 모양이 경로마다 갈리므로, 적재 컬럼에 넣을 값이라면
`to_json`이나 `pack`을 함께 거세요.

### C.5 공통 요청 `params`

[5.3](#53-요청-params로-재배포-없이-값-변경)의 표에 있는 공통 키의 의미입니다.

| 키 | 값 | 하는 일 |
|---|---|---|
| `llm_cache` | `0` / `1` | 같은 입력에 대한 LLM 응답을 재사용합니다. 설정을 바꿔 가며 비교할 때 켜면 호출 비용과 시간이 줄어듭니다 |
| `error_policy` | `strict` / `lenient` | `strict`는 보강 단계 실패를 요청 실패로 올리고, `lenient`는 경고만 남기고 계속합니다 |
| `request_deadline` | 초 | 요청 전체의 시간 상한. 넘으면 타임아웃으로 끊고 `error_kind`에 `timeout`이 실립니다 |

---

※ 이 문서는 모니모 환경 전용입니다. 코드서빙이 고정되어 있고 Bitbucket 브랜치와 전용 CI/CD 로
배포하는 환경을 전제로 작성했습니다.
