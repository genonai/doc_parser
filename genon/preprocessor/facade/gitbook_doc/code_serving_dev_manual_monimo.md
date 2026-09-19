# 모니모 전처리기 개발 매뉴얼

모니모 환경에 배포된 **문서 전처리기(doc parser)** 의 설정과 코드를 수정하고 반영하는 방법을 다룹니다.

전처리기 코드서빙은 **이미 배포되어 동작하고 있습니다.** 이 문서는 실행 중인 전처리기를 우리
요구에 맞게 고치는 방법을 다룹니다. 개발자가 새 코드서빙을 만들거나 도커 이미지를 직접 빌드하지는 않습니다.
이미지 빌드와 코드 반영은 담당자 승인 후 CI/CD가 수행합니다.

대상 독자는 Python·REST API·Git의 기본 사용 경험이 있는 개발자입니다. Genos와 전처리기의 사전 지식은
필수로 요구하지 않습니다. 실행에는 제공된 코드스페이스, 저장소, 환경별 접속 정보가 필요합니다.
이 문서는 `schema: v2` 설정과 두 facade의 확장 API를 기준으로 합니다. 작업 전 제공받은 릴리스 태그 또는
커밋과 `/version` 응답을 기록하고, 릴리스 갱신 시 이 문서와 템플릿도 함께 확인하세요. 문서만으로 현재 운영
서버의 버전을 확정할 수는 없습니다.

## 목차

- [0. 개요](#0-개요) — 제공 항목 · 작업 흐름 · 작업 범위
- [1. 새 문서 유형 추가하기](#1-새-문서-유형-추가하기) — `custom_field_*.yaml` 6단계 · 입력/기대 결과 예제
- [2. 코드 수정](#2-코드-수정) — facade 2개의 훅 메소드와 확장 지점
- [3. 개발 환경](#3-개발-환경) — 코드스페이스 사용법 · 검증 절차
- [4. 배포](#4-배포) — Bitbucket 브랜치 → PR → CI/CD → 반영 확인 · 릴리스 갱신 · 롤백
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
| 코드서빙 접속 정보 | 개발·운영별 코드서빙 `ID`, 리비전, 배포 담당자와 요청 경로 | 4.4의 `/version`이 응답하는지 |
| 채워진 `resource/*.yaml` | 모델 서빙 주소와 인증키가 들어 있습니다 | 5.1 |
| 설정 템플릿 5종 | `resource/templates/custom_field_TEMPLATE_*.yaml` | 1.3 |
| 매뉴얼 | 이 문서 (주요 설정 요약과 최소 예제는 [부록 C](#부록-c-custom_fields-설정-레퍼런스)) | — |

### 0.2 작업 흐름

```
 [3장]   작업 브랜치 확인 → 기존 결과 기준선 저장(1.7)
        │
 [1장 또는 2장]  코드스페이스에서 설정 또는 코드 수정
        │
 [3장]   facade 단독 실행으로 결과 확인                    ← 파싱 시간은 파일 크기와 모델 호출에 따라 달라집니다
        │
 [4장]   UPDATED_AT 갱신 → commit/push → PR → 담당자 merge
        │
 [4.4]   /version 호출로 반영 확인                        ← 건너뛰지 마세요
```

배포 후 되돌릴 때도 담당자 머지·배포 절차가 필요합니다. 변경 전 정상 버전과 검증 결과를 보관하세요([4.6](#46-배포-실패와-롤백)).

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

- **facade**: 파싱·청킹의 진입 클래스와 수정 가능한 확장 지점을 제공하는 파일입니다.
- **enrichment(보강)**: 파싱한 결과에 필드·요약·표 설명 등을 추가하는 단계입니다. 기능에 따라 LLM, CSS 선택자, Python 함수를 사용합니다.
- **문서 메타데이터**: 제목·문서 유형처럼 문서 전체에 속하는 값입니다. **청크 메타데이터**는 최종 청크에 함께 실리는 값이며, 행·레코드별 값도 포함할 수 있습니다.
- **청크 본문**: 임베딩을 만들 텍스트입니다. 메타데이터에만 있는 값은 본문에 자동으로 포함되지 않습니다.

HTTP 응답은 `{code, errMsg, data}`로 감싸집니다. facade 단독 실행 파일에는 이 포장 없이 결과 자체가
저장됩니다. 두 방식의 연결 예제는 [3.4](#34-배포된-코드서빙-직접-호출)에 있습니다.

---

## 1. 새 문서 유형 추가하기

새 입력 데이터 유형(엑셀 FAQ, JSON 이벤트 목록, 계약서 PDF 등)을 추가할 때 수행하는 작업입니다.
**지원하는 입력 구조라면 유형별 설정 파일을 추가하고 프로세서 설정에 등록하는 것으로 완료됩니다.**
입력 구조를 별도로 변환해야 할 때만 Python 코드를 수정합니다.

### 1.1 `doc_type`의 역할

`doc_type`은 요청 `params`로 넘기는 문서유형 키입니다. "이 문서는 계약서다 / FAQ 엑셀이다"를
알려 주면 전처리기가 해당 문서 유형에 대한 전용 처리를 적용합니다. 이때 수행되는 작업은 세 가지입니다.

1. `enable: true`인 등록 중 `doc_type` 조건이 일치하는 설정을 적용합니다. 등록 블록에 `doc_type`이 없으면 모든 요청에 매칭됩니다.
2. 엑셀의 행 매핑, JSON의 레코드·섹션 매핑이 일치하면 해당 구조로 파싱합니다. 행·레코드는 기본적으로 1건당 1청크이며, 본문 분할이나 표 분리 설정에 따라 여러 청크가 될 수 있습니다.
3. 문서형 경로와 매핑된 행·레코드 경로에서는 전달받은 `doc_type`을 청크에 이어 줍니다. 매핑 없는 엑셀·CSV의 일반 tabular 경로에서는 기록되지 않을 수 있습니다.

> **요청**에는 `params: {"doc_type": "notice"}`처럼 문자열 하나를 전달합니다.
> **등록 YAML**에는 `doc_type: notice` 또는 `doc_type: [notice, notice_v2]`처럼 허용할 유형을 적습니다.
> 비교는 양끝 공백 제거 후 소문자로 정규화한 정확 일치입니다. `"Contract "`와 `contract`는 같지만 `contracts`는 다릅니다.
> 유형을 생략해도 조건 없는 등록과 공통 처리는 실행될 수 있습니다.

### 1.2 ① 입력 데이터 구조에 따라 `kind`를 선택합니다

이 선택에 따라 이후 설정이 결정됩니다.

| 입력 데이터가 다음 구조인 경우 | `source.kind` | 검색 단위 | 필드 추출을 위한 LLM 호출 |
|---|---|---|---|
| 엑셀·CSV 한 행이 한 건 (FAQ, 용어사전, 메뉴) | `rows` | 기본: 행 1개 = 청크 1개 | 선택 |
| JSON 배열의 한 요소가 한 건 (이벤트 목록, 공지) | `records` | 기본: 레코드 1개 = 청크 1개 | 선택 |
| 대상 하나를 깊게 설명한 중첩 JSON (상품 상세) | `sections` | 섹션별 본문, 크기에 따라 분할 | 선택 |
| 문서에서 필드 몇 개를 뽑는다 (계약서, 카드 안내) | `document` | 문서 metadata, 모든 청크에 같은 값 | LLM 추출 시 문서 단위 호출 |
| 값의 위치를 class·속성으로 지정할 수 있는 HTML (크롤 산출물) | `html` | 문서 metadata, 모든 청크에 같은 값 | 없음 |

- `rows` · `records` · `sections`는 확장자별 처리 경로에서 요소형 결과를 만듭니다(Docling 문서 변환을 거치지 않음).
- `document` · `html`은 파싱 후 enrichment 단계에서 붙습니다.
- 앞의 세 kind는 `llm:` 블록을 추가하면 입력 데이터에 없는 필드(요약문 등)를 만들어 붙일 수 있습니다.
  LLM 설정 항목별로 `rows`는 행마다, `records`는 레코드마다, `sections`는 문서 단위로 호출합니다.
- `document`는 [2.6](#26-설정에서-이름으로-불러-쓰는-세-가지-확장-지점)의 Python 추출로 대체할 수 있습니다.
- 위 표는 custom_fields 값 추출 기준입니다. 별도로 켜 둔 표 설명·문서 요약·OCR·레이아웃 분석은 모델을 호출할 수 있습니다.

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

### 1.4 ③ 유형에 맞는 블록을 구성합니다

다음은 `rows`의 예제입니다. `source`·`fields`를 중심으로 구성하되, 지원하는 블록은 `kind`에 따라 다릅니다.
`document`·`html`에는 아래의 `require`와 `body.fields`를 그대로 복사하지 마세요.
유형별 최소 예제는 [부록 C.6](#c6-유형별-최소-설정-예제), 지원 범위는 부록 C.1에 있습니다.

```yaml
schema: v2                    # 최상위 필수 키. 파일의 첫 줄일 필요는 없습니다

source:                       # 입력 데이터를 어떻게 볼 것인가
  kind: rows

fields:                       # 각 목표필드의 설정은 YAML 매핑으로 작성합니다
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
| `llm` | LLM 추출·생성 설정 목록. `out`은 출력 필드, 행·레코드·섹션형의 `in`은 입력 필드입니다. 문서형은 본문을 입력으로 사용합니다([C.7](#c7-필드-조합과-llm-설정)) |

값을 얻는 단계와 후처리 단계를 구분합니다.

| kind | 값을 얻는 방법 |
|---|---|
| `rows` · `records` · `sections` | `alias`로 원본 필드를 매핑합니다. 지원 범위 안에서 선별·순번·선택적 LLM 생성을 적용합니다. |
| `document` | 문서 본문에서 LLM 또는 Python 함수로 추출합니다. `alias`는 front matter 최상위 키의 매핑에 사용합니다. |
| `html` | 파싱 전 원문 HTML에 `select`를 적용하고, 필요하면 `attr` 속성값을 읽습니다. |

공통 후처리는 빈 값에 `default` 적용 → `const`로 덮어쓰기 → `values` → `transform` → `template` 순서입니다.
`pack`은 순번과 LLM 출력까지 확정된 뒤에 적용합니다. 행·레코드형의 `filter`·`require`는 추가 LLM 생성보다
앞서 적용되므로, LLM이 나중에 생성할 필드의 유효성은 결과 검증 단계에서 확인하세요.

기본 변환기는 10종입니다. 인자가 필요 없는 사용법과 선택 인자를 받는 사용법은
[부록 C.2](#c2-transform-10종)에 정리되어 있습니다. 목록에 없는 이름은 별도 등록하지 않으면 기동에 실패합니다.
사이트 전용 변환기 등록은 [2.6](#26-설정에서-이름으로-불러-쓰는-세-가지-확장-지점)을 참조하세요.

> 여기 적는 이름이 **설정 표기**입니다. 오류 메시지에는 `column_map` · `text_fields` · `key_map`
> 같은 **내부 이름**이 나오는데, 그것은 설정에 적는 이름이 아닙니다. 둘의 대응표는
> [부록 C.1](#c1-설정-표기와-내부-이름)에 있습니다.

#### 청크 본문 구성 — `body`

본문만 임베딩하는 적재 구성을 기준으로, 메타데이터에만 있는 값은 임베딩 입력에 포함되지 않습니다.
그 값으로 검색되도록 의도했다면 본문에도 포함하세요. 메타데이터 필터 검색은 적재·검색 측에서 해당 필드를 지원해야 합니다.

| 키 | 하는 일 | 쓰는 곳 |
|---|---|---|
| `body.fields` | 본문을 구성할 필드와 순서 (개행 결합) | `rows` · `records`는 **본문의 전부** |
| `body.labels` | `항목명: 값` 형태로 냅니다 | 사람이 검색어로 쓰는 자연스러운 항목명을 권장합니다 |
| `body.split` | 본문이 `chunk_size`를 넘으면 여러 청크로 나눕니다 | 긴 상세 HTML을 가진 행·레코드 |
| `body.repeat` | 나뉜 **모든** 조각 앞에 반복할 식별 필드 | `rows`·`records`는 `split: true`일 때. `document`·`html`은 모든 청크에 적용 |
| `body.once` | **첫 청크에만 1회** 얹습니다 | 문서 단위 분류 (`sections` · `document` · `html`) |
| `body.mirror_to` | 본문과 글자 그대로 같은 값을 받을 메타 필드 | 적재 측의 본문 컬럼용. `document` · `html` 전용 |

> 접두는 청크마다 쓸 수 있는 `chunk_size`를 그만큼 줄입니다(청커가 미리 그 몫을 떼어 둡니다).
> 짧은 식별 필드 1~2개를 권장합니다. `body.once`로만 추가한 값은 첫 청크의 임베딩 입력에만 직접 포함됩니다.
> 다른 청크가 의미적으로 유사해 검색될 가능성까지 배제하는 뜻은 아닙니다. `meta: false`로 제외하지 않은
> 문서 공통 값은 모든 청크의 메타데이터에 전달되며, 적재·검색 측이 지원하면 필터로 사용할 수 있습니다.

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

수정 직후에는 [3.3](#33-신속한-확인-방법--facade-단독-실행)의 단독 실행으로 검증합니다. 아래는 배포된
설정을 사용하는 `POST /parser`의 요청 본문 예시이며, 경로는 실제 서버 내부 경로로 바꿔야 합니다.
코드스페이스에서 파일만 고친 상태로 서버를 호출하면 기존 배포본이 실행됩니다.

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
| `require` 미충족 | 해당 항목만 제외됩니다. **파서는 요소 0건으로 성공할 수 있습니다. 빈 결과를 청커에 전달하면 오류가 날 수 있으므로 별도로 검사합니다** |
| `values`에 없는 값 | 원값 통과(fail-open), 경고만 |
| 같은 이름 key가 얕은 곳과 깊은 곳에 모두 있음 | **얕은 쪽이 우선합니다.** 경고 없이 엉뚱한 값이 실립니다 |
| 같은 이름의 레코드 배열이 여러 곳에 있음 | `records_at`은 최초 매칭 **1개**만 찾습니다. 나머지는 경고 없이 빠집니다 |
| 보강 단계가 예외로 실패함 | **기본값 `lenient`에서는 경고만 남기고 그 단계를 건너뛴 채 성공 응답을 냅니다** (아래) |

#### 보강 실패는 기본적으로 요청을 실패시키지 않습니다

`error_policy`의 기본값은 `lenient`입니다. 모델 서버 장애, 프롬프트 오류, 추출 예외처럼 **보강 단계에서
발생한 예외는 경고 로그만 남기고 그 단계를 건너뜁니다.** 요청은 `code: 0`으로 성공하고 해당 필드만
비어 있으므로, 응답만 보면 설정 오류와 구분되지 않습니다.

이 정책이 적용되는 단계는 다음과 같습니다. 파싱 자체의 실패는 해당하지 않으며 그대로 요청 실패가 됩니다.

| `stage` | 단계 |
|---|---|
| `custom_fields` | 목표필드 추출 |
| `doc_summary` · `image_description` | 문서 요약 · 이미지 설명 |
| `metadata` · `doc_type_stamp` | 문서 메타데이터 · 문서 유형 기록 |

> **검증할 때는 `error_policy: strict`로 실행하세요.** 보강 실패가 조용히 넘어가지 않고 `stage`가 실린
> 오류로 올라오므로, 설정이 틀린 것인지 모델 호출이 실패한 것인지 바로 구분됩니다. 운영 요청은 기본값
> `lenient`를 유지해 일부 보강 실패로 적재 전체가 멈추지 않게 합니다. `llm_cache`와 달리
> `workflow_id` 없이도 항상 적용됩니다.

```bash
curl "${CS}/parser" -H 'Content-Type: application/json' \
  --data '{"file_path": "...", "params": {"doc_type": "notice", "error_policy": "strict"}}'
```

#### 세 단계 검증 절차

(가) 설정만 사전에 검사합니다. 파싱과 LLM 호출이 없어 즉시 완료되며, 기동 실패를 이 단계에서 확인할 수 있습니다.

```bash
# 실행 위치: 저장소 루트
genon/preprocessor/examples/config_precheck/precheck_custom_fields.sh
```

(나) 결과 값을 확인합니다. facade 단독 실행이 가장 빠릅니다([3.3](#33-신속한-확인-방법--facade-단독-실행)).
행·레코드·섹션 매핑은 필드 생성 LLM과 표 설명 등 모델을 호출하는 보강 기능을 모두 끄면 모델 서빙 없이 검증할 수 있습니다.
`html`의 필드 추출에 LLM이 없다는 사실만으로 전체 파싱의 모델 호출이 없다고 판단하지 마세요.

```bash
# 실행 위치: 저장소 루트
python -m genon.preprocessor.facade.parser_processor --config genon/preprocessor/resource/parser_processor_config.yaml 공지사항.xlsx --doc-type notice -o parsed.json
python -m genon.preprocessor.facade.chunking_processor --config genon/preprocessor/resource/chunking_processor_config.yaml parsed.json --doc-type notice -o chunks.json

python - <<'PY'
import json
from pathlib import Path

r = json.loads(Path('parsed.json').read_text(encoding='utf-8'))
if isinstance(r.get('document'), dict):
    print('파싱 형식: 문서형')
    print('문서 메타데이터:', r.get('metadata'))
elif isinstance(r.get('elements'), list):
    elements = r['elements']
    print('파싱 형식: 요소형 / 건수:', len(elements))
    assert elements, '파싱 결과 0건: 선별 조건과 입력 구조를 확인하세요.'
    for el in elements[:3]:
        print(el.get('category'), el.get('metadata'))
        print('본문:', repr(el.get('content', ''))[:160])
else:
    raise AssertionError('예상하지 못한 파싱 결과 형식')

chunks = json.loads(Path('chunks.json').read_text(encoding='utf-8'))
assert isinstance(chunks, list) and chunks, '최종 청크가 없거나 형식이 다릅니다.'
for chunk in chunks:
    assert str(chunk.get('text', '')).strip(), '빈 청크 본문'
print('최종 청크 수:', len(chunks))
for chunk in chunks[:3]:
    print(json.dumps(chunk, ensure_ascii=False, indent=2))
PY
```

위 코드는 형식·빈 결과를 확인하는 기본 검사입니다. 아래의 업무별 기대값 검사를 추가해야 검증이 완료됩니다.

| 보는 것 | 확인 기준과 문제 발생 시 점검할 곳 |
|---|---|
| **건수** | 입력 건수에서 필터·필수값·빈 본문 제외·병합을 반영한 예상 건수와 비교합니다. 배열 최초 매칭과 청크 분할·표 분리도 확인합니다. |
| **각 목표필드 값** | 샘플의 기대값과 직접 비교합니다. `alias`·선택자·기본값·LLM 출력 키를 확인합니다. |
| **최종 청크 본문** | 필요한 값과 항목명이 들어 있고 불필요한 값이 빠졌는지 확인합니다. 긴 본문은 분할 후에도 내용이 보존되어야 합니다. |
| **최종 청크 메타데이터** | `chunks.json`의 각 객체에 목표필드가 전달되는지 확인합니다. `meta: false` 필드는 제외되어야 합니다. |

`document`·`html`의 추출값은 문서 메타데이터에서 먼저 확인한 뒤 최종 청크에서 다시 확인합니다.
행·레코드형은 `elements[].metadata`와 최종 청크를 비교합니다. 기동·처리 로그의 `WARNING`도 함께 확인하세요.

(다) 기존 동작의 회귀 검사를 수행합니다. **기준선(골든)은 코드를 수정하기 전에 저장해야 합니다.**
이미 수정했다면 기존 정상 커밋의 별도 작업 디렉터리에서 기준선을 만드세요. 현재 수정 결과를 기준선으로
저장해 놓고 같은 결과와 비교하면 회귀를 찾을 수 없습니다.

```bash
# 실행 위치: genon/preprocessor/examples/parse_chunk
# 변경으로 결과가 달라지면 안 되는 기존 문서를 지정합니다. 경로는 실제 샘플로 교체합니다.
cat > my_cases.yaml <<'EOF'
- {doc_type: card, path: /data/samples/card.pdf}
EOF

# 수정 전 실행. 기준선은 소스 갱신으로 덮어쓰지 않는 별도 디렉터리에 보관합니다.
python parse_chunk_golden.py --record --cases my_cases.yaml --golden ~/my_golden

# 수정 후 실행
python parse_chunk_golden.py --check --cases my_cases.yaml --golden ~/my_golden
```

기존 동작을 유지할 케이스는 차이가 없어야 합니다. 신규 유형과 의도적으로 동작을 바꾼 케이스는 별도로
예상 건수·필드값·본문을 검증하고, 확인이 끝난 뒤 새 기준선에 포함합니다. LLM·문서 변환처럼 실행마다
달라질 수 있는 항목은 동일 입력·설정·실행 환경으로 비교하고, 차이가 변경 때문인지 먼저 조사하세요.

#### 처음 실행할 때 사용할 입력과 기대 결과

1.4의 `rows` 설정을 `custom_field_notice.yaml`에 저장하고 1.5처럼 등록한 상태에서 다음 샘플로 확인할 수 있습니다.
입력 파일은 저장소 루트에서 생성합니다. 이 예제는 원본 1행을 최종 청크 1개로 만드는 설정입니다.

```bash
cat > notice.csv <<'EOF'
질문,답변,등록일,노출여부
앱 알림은 어디서 설정하나요?,앱 설정에서 알림을 변경할 수 있습니다.,26.07.01,노출
EOF
python -m genon.preprocessor.facade.parser_processor --config genon/preprocessor/resource/parser_processor_config.yaml notice.csv --doc-type notice -o parsed.json
python -m genon.preprocessor.facade.chunking_processor --config genon/preprocessor/resource/chunking_processor_config.yaml parsed.json --doc-type notice -o chunks.json
```

`parsed.json`은 요소형이며 `elements`에 1건이 있어야 합니다. 해당 요소의 `metadata`와 최종 청크의
필드를 아래 기대값과 비교합니다. 추가 공통 필드가 있는 것은 정상입니다.

```json
{
  "QUESTION": "앱 알림은 어디서 설정하나요?",
  "ANSWER": "앱 설정에서 알림을 변경할 수 있습니다.",
  "REG_DT": 20260701,
  "USE_YN": "Y",
  "GROUP_C": "IFP"
}
```

최종 `chunks.json`은 객체 1개가 담긴 목록입니다. `text`에는 다음 두 줄이 포함되어야 합니다.

```text
질문: 앱 알림은 어디서 설정하나요?
답변: 앱 설정에서 알림을 변경할 수 있습니다.
```

```bash
python - <<'PY'
import json
from pathlib import Path
parsed = json.loads(Path('parsed.json').read_text(encoding='utf-8'))
chunks = json.loads(Path('chunks.json').read_text(encoding='utf-8'))
assert len(parsed['elements']) == 1
assert len(chunks) == 1
expected = {
    'QUESTION': '앱 알림은 어디서 설정하나요?',
    'ANSWER': '앱 설정에서 알림을 변경할 수 있습니다.',
    'REG_DT': 20260701, 'USE_YN': 'Y', 'GROUP_C': 'IFP',
}
for key, value in expected.items():
    assert parsed['elements'][0]['metadata'][key] == value, f'파서 필드 불일치: {key}'
    assert chunks[0][key] == value, f'청크 필드 불일치: {key}'
assert '질문: ' + expected['QUESTION'] in chunks[0]['text']
assert '답변: ' + expected['ANSWER'] in chunks[0]['text']
print('샘플 검증 통과: 파싱 1건, 최종 청크 1건, 필드·본문 일치')
PY
```

공통 훅이나 마스킹 정책이 이 예제의 값을 변경한다면, 적용 이유를 확인하고 해당 환경의 승인된 기대값으로
검사를 조정합니다. 샘플이 통과해도 실제 입력의 빈 값·긴 본문·다중 레코드 등 경계 사례를 추가로 확인해야 합니다.

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
| csv/xlsx인데 `doc_type`도 안 붙음 | 매핑 없는 일반 tabular 경로인지 확인합니다. 매핑된 행 경로와 Docling 문서 경로는 별도로 확인합니다 |

**설정만으로 처리할 수 없는 경우** 입력 데이터 구조가 예상과 다른 것입니다. 예를 들어 같은 이름의 레코드 배열이 여러 부모 아래에 나뉘어 있거나, 목록과 상세 데이터가 분리되어 있거나,
확장자가 `.xml`인 경우가 있습니다. 단순히 중첩 깊이만 늘어난 JSON은 이름 탐색으로 처리될 수 있습니다. 이 경우에는 [2장](#2-코드-수정)을 참조합니다.

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

| 파일 | 구획 |
|---|---|
| `facade/parser_processor.py` | 파일 상단 주석(흐름 요약) → `ROUTES` → `CONFIG_BY_DOC_TYPE` → 훅 3종 → 오버라이드 |
| `facade/chunking_processor.py` | `GenOSVectorMeta` → `GenosSmartChunker` → `ROW_CATEGORIES` → `CONFIG_BY_DOC_TYPE` → 훅 3종 |

먼저 **파일 상단 주석**을 확인하세요. 처리 흐름 요약과 결과 형식이 포함되어 있습니다.

처리 흐름에서 훅이 호출되는 자리는 다음과 같습니다.

```
파싱   요청 -> 확장자 판정 -> doc_type별 설정 -> ROUTES -> 입력 읽기/변환 -> 파싱
            -> 문서 경로: [edit_document] -> enrichment -> [edit_output] -> 응답
            -> 행·레코드 경로: 매핑/선택적 보강 -> [edit_output] -> 응답

       edit_input: 경로형 입력은 ROUTES 앞, JSON·표·Markdown·HTML은 해당 처리 경로 안에서 호출

청킹   파서 결과 -> 형태 판별 -> doc_type별 설정 -> [edit_input] -> 분할 -> [edit_chunk]
            -> vector_meta 조립 -> [edit_output] -> 응답
```

> `_`로 시작하는 메소드(`_start_job`, `_call_*`)는 **오버라이드하지 않습니다.** 훅 호출과 설정
> 적용 순서를 맡고 있습니다.

### 2.3 문서 유형마다 설정을 다르게 — `CONFIG_BY_DOC_TYPE`

설정 파일은 모든 문서에 동일하게 적용됩니다. "계약서만 OCR을 강제 적용", "매뉴얼만 청크 분할 방식을 변경"과 같은
문서 유형별 설정은 훅이 아니라 이 표에 정의합니다. 두 facade 모두 동일한 위치에 설정합니다.

```python
# facade/parser_processor.py
    CONFIG_BY_DOC_TYPE = {
        "press":    {"enrichment.table_description.enable": False},  # 표가 없어 불필요한 LLM 호출
        "contract": {"ocr.ocr_mode": "force"},                       # 스캔본이 많다
    }

# facade/chunking_processor.py
    CONFIG_BY_DOC_TYPE = {
        "contract": {"chunking.chunk_size": 1500},                   # 문서형 청크 크기
        "manual": {"chunking.chunk_mode": "split_only"},
    }
```

키는 설정 파일 경로를 점 표기법으로 작성합니다. 같은 의미의 요청 파라미터 이름(괄호 안 표기)을 사용해도 동일하게
동작합니다. **청킹 설정은 파서가 아니라 청커의 표**에 정의합니다.
행·레코드형은 `body.split`이 꺼져 있으면 `chunk_size`만 낮춰도 본문을 나누지 않습니다.
문서형의 크기 하한은 [5.2](#52-주요-구성-옵션)의 `min_chunk_size` 설명을 확인하세요.

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

> 모든 설정을 요청마다 바꿀 수 있는 것은 아닙니다. 점 표기 키는 `CONFIG_PATH_ALIASES`에 등록된 것만
> 변환되며, 없는 점 표기 키는 경고 후 건너뜁니다. 점이 없는 이름은 요청 인자로 전달되므로 **오타여도 경고 없이
> 사용되지 않을 수 있습니다.** 아래 표나 5.3에 제시된 소비 가능한 키를 사용하세요. 엔드포인트·프롬프트·토크나이저
> 경로·`min_chunk_size` 등 초기화 시 읽는 값은 YAML에서 수정하고 프로세스를 다시 시작해야 합니다.

### 2.4 설정만으로 입력 데이터 구조를 처리할 수 없는 경우 — 훅 메소드

`custom_field_*.yaml`은 입력 데이터가 예상한 구조일 때 값을 추출합니다. 입력 데이터 구조가 다르면
설정만으로 처리할 수 없습니다.

입력 구조가 문제일 때는 **`edit_input`에서 설정이 값을 찾을 수 있도록 입력을 정규화**합니다.
`alias`·`transform`·`values`·`require`는 이후 단계에서 적용됩니다. 다른 훅은 역할이 다릅니다.
`edit_document`는 파싱된 문서를, `edit_chunk`는 청크 본문·제외 여부·추가 필드를, `edit_output`은 최종 결과를 조정합니다.

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

1. `doc_type`으로 처리 대상을 제한합니다. 파서 훅의 명시적 `doc_type` 인자는 소문자로 정규화됩니다.
   청커 훅에서는 `tb.normalize_doc_type(kwargs.get("doc_type"))`으로 정규화한 값을 비교하세요.
   파서 결과의 메타데이터에 문서 유형이 있더라도 청커 요청의 `params.doc_type`을 생략하지 않습니다.
2. **`edit_input`에서 수정이 필요하지 않으면 입력값을 그대로 반환합니다.** 그래야 core가 불필요한
   파생 입력을 만들지 않습니다. 다른 훅의 반환 규칙은 해당 훅 설명을 따릅니다.
3. **요청 파라미터가 필요하면 시그니처 끝에 `**kwargs`를 붙입니다.** 이름이 명시적 인자와 겹치거나
   내부 처리용으로 제외된 키를 빼고 전달합니다. **현재 구현은 일반 훅의 `kwargs`에 `job`을 자동으로 넣지 않습니다.**
   `kwargs["job"]`에 의존하지 마세요. 요청별 상태를 `self`에 저장하면 동시 요청 사이에 값이 섞일 수 있습니다.
4. **외부 호출이 필요하면 `async def`와 비동기 클라이언트를 사용하고 호출을 `await`합니다.**
   `async def` 안에서도 동기 HTTP·파일 작업이 오래 걸리면 이벤트 루프가 차단됩니다. 동기 라이브러리만
   지원되는 I/O는 `await asyncio.to_thread(...)`로 별도 스레드에서 실행하는 방법을 검토하세요.

#### `job` — 명시적으로 전달받는 위치에서만 사용

`config_by_condition(job)`, 파서의 `edit_document(job, doc)`, 직접 구현하는 `route_*(job)`처럼
**시그니처에 `job`이 있는 메소드**에서는 요청 객체를 사용할 수 있습니다. 일반 `edit_input`·`edit_output`·
`edit_chunk`의 `**kwargs`에는 자동 전달되지 않습니다. facade 주석의 `kwargs["job"]` 안내와 현재 호출부가
일치하지 않으므로, 이 문서는 실행 코드를 기준으로 설명합니다.

| 필드 | 파서 | 청커 | 실제 의미 |
|---|:-:|:-:|---|
| `job.doc_type` | ✔ | ✔ | 파서는 정규화된 값, 청커는 요청에서 받은 값. 청커 요청도 소문자 표준값으로 전달합니다. |
| `job.file_path` | ✔ | ✔ | 해당 요청의 `file_path`. 청커에서는 파싱 결과 JSON 경로나 인라인 입력의 식별값일 수 있습니다. |
| `job.params` | ✔ | ✔ | 요청 인자와 적용된 런타임 값. 사용자가 보낸 원본 dict와 완전히 같지는 않습니다. |
| `job.config` | ✔ | ✔ | **문서 유형·조건별 설정 중 요청 인자로 실제 추가한 값만** 담습니다. YAML 전체와 최종 설정 전체의 스냅샷이 아닙니다. |
| `job.notes` | ✔ | ✔ | 내부 처리에도 쓰는 요청별 dict. `job`을 명시적으로 받는 단계 사이에서만 사용하고 내부 키와 겹치지 않는 이름을 씁니다. |
| `job.ext` | ✔ | ✗ | 별칭 처리 후 소문자 확장자 |
| `job.source` | ✔ | ✗ | 실제 파싱할 **파일 경로**. 훅의 `data` 인자와 구별합니다. |
| `job.temp_dir("접두")` | ✔ | ✗ | 파서 요청 종료 시 정리하는 임시 디렉터리 생성 |
| `job.kind` · `job.data` | ✗ | ✔ | `docling` / `parse`와 해당 파싱 결과 |

`config_by_condition`은 파싱·청킹 전 실행됩니다. 청커의 `job.metadata`는 이 시점에 만들어져 있지 않으므로
읽지 마세요. 요청 조건은 `job.params`를 사용하고, 청크의 문서·행 메타데이터는 `edit_chunk`의
`info["metadata"]`에서 읽습니다. 일반 훅 여러 단계가 별도 요청 상태를 공유해야 한다면 같은 훅 안에서
처리할 수 있는지 먼저 검토하고, 공통 코드의 명시적 상태 전달 지원은 솔루션 개발자에게 요청하세요.

#### `edit_input`의 타입·임시 경로·변경 판정

| 실제 처리 경로 | 훅의 `ext`와 `data` | `work_dir` |
|---|---|---|
| JSON 매핑·본문 추출 | `.json`, dict/list. JSON 구문 해석 실패 시 복구 기회로 원문 str 전달 | **없음(`None`)** |
| 엑셀·CSV 표 로더 | CSV·XLSM도 훅에는 `.xlsx`로 전달, `{시트명: 2차원 행}` | 제공 |
| Markdown·HTML 전처리 | 해당 확장자와 원문 str | **없음(`None`)** |
| 그 밖의 경로형 처리 | 확장자와 파일 경로 str | 제공 |

`work_dir`은 표 로더와 경로형 처리에만 전달됩니다. 데이터형 훅에서는 **항상 `None`이므로** 그 값을 쓰는
코드를 두지 마세요.

설정에 따라 훅을 아예 거치지 않는 폴백이 있습니다. JSON에 매핑·본문 추출 설정이 없으면 일반 텍스트
경로로 넘어가 JSON 데이터 훅을 거치지 않고, `formats.md.processing_mode`가 `text`이면 Markdown 훅도
호출되지 않습니다. 모든 확장자가 같은 위치에서 같은 횟수로 훅을 호출한다고 가정하지 마세요.

파서의 변경 판정은 값 비교가 아니라 **객체 동일성(`out is not data`)** 검사입니다. 내용을 바꿀 때는 새 dict·list·
문자열을 반환하세요. 입력 객체를 제자리에서 고친 뒤 그대로 반환하면 경로에 따라 변경이 반영되지 않을 수 있습니다.
변경하지 않을 때는 받은 객체를 그대로 반환합니다. `work_dir`이 없는 데이터형 훅은 가능한 한 메모리에서
수정하고, 직접 임시 파일을 만들면 읽기가 끝날 때까지 유지한 뒤 정리해야 합니다.

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
        if (ext == ".json" and doc_type == "monimo_event"
                and isinstance(data, dict) and "companyList" in data):
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
        if (ext == ".json" and doc_type == "monimo_event"
                and isinstance(data, dict) and "detailList" in data):
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
        if tb.normalize_doc_type(kwargs.get("doc_type")) != "contract":
            return text
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

`info`의 키 구조는 일반 문서·행·텍스트 경로에서 같습니다. 다만 오디오 `[AUDIO]`와 레거시 표 `[DA]`를
단일 청크로 만드는 특수 경로는 `edit_chunk`를 건너뜁니다. 해당 결과도 수정해야 한다면 `edit_output`에서
처리하고 통계를 갱신하세요.

| 키 | 값 |
|---|---|
| `kind` | `"docling"`(문서) · `"row"`(레코드/표 행) · `"text"`(그 밖) |
| `page` · `index` | 페이지는 보통 1부터 시작하며 문서 위치 정보가 없으면 0일 수 있습니다. `index`는 0부터 시작하는 분할 결과 순번으로, 청크 제외 후 최종 순번과 다를 수 있습니다. |
| `headings` | 문서형의 섹션 경로 목록. 없으면 `None`일 수 있으므로 순회할 때 `info.get("headings") or []`를 사용합니다 |
| `metadata` | 문서 또는 레코드 메타데이터. **복사본이라 고쳐도 저장되지 않습니다** |
| `fields` | 이 청크에만 실을 값. `GenOSVectorMeta` 필드로 나갑니다 |

`text`는 접두와 `HEADER:` 라인까지 붙은 뒤의 본문입니다. 훅이 돌려준 값에 마스킹과 정제가
뒤이어 적용됩니다.

> `edit_output`에서 본문을 고쳤거나 청크를 지웠으면 통계를 다시 맞춰야 합니다. 개수가 바뀌었으면
> `tb.refresh_stats(vector_metas)`, 본문만 고쳤으면 `tb.refresh_stats(vector_metas, reindex=False)`. 호출하지 않으면 `n_char`가
> 예전 값으로 남습니다.

#### 훅보다 설정을 우선할 기능

다음 기능은 이미 설정으로 제공되므로 훅에서 중복 구현하지 않는 것을 권장합니다.
값 파이프라인과 순회 방식에 영향을 주므로 `custom_field_*.yaml`에서 설정하세요.

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

위 XML 예제는 파일 상단에 `import xml.etree.ElementTree as ET`, `import os`, `import json`이 필요합니다.
`ROUTES`와 `edit_input`은 기존 클래스의 해당 정의에 반영하고, 중복 정의하지 않습니다.
동작을 검증하기 전에 **먼저 `ROUTES`에 등록**하세요. 등록하지 않으면 캐치올이 처리하여 목표필드가 비어 있는 청크 1개만 생성됩니다.

> **새 확장자를 표·JSON 라우트에 붙이면 `edit_input`이 한 요청에서 두 번 호출됩니다.** 처음은
> `ROUTES` 앞에서 원래 확장자와 파일 경로로, 두 번째는 라우트 안에서 변환된 형식으로 호출됩니다.
> 위 예제에서 `.xml → route_json`은 `.xml`(경로)과 `.json`(dict) 두 번, `.tsv → route_tabular`는
> `.tsv`(경로)와 `.xlsx`(시트 dict) 두 번입니다. **위 예제가 안전한 이유는 `ext == ".xml"`로
> 게이팅했기 때문입니다** — 두 번째 호출은 조건에 걸리지 않아 입력을 그대로 돌려줍니다.
> 확장자 조건 없이 훅을 쓰면 두 번째 호출에서 이미 변환된 데이터를 다시 변환하려다 실패합니다.

표준 형식으로 변환할 수 없는 입력 데이터(로그, 고정폭 텍스트, 사내 전문)에 한해서만 핸들러를 직접
구현합니다. 해당 핸들러도 facade 파일에 정의합니다.

```python
    async def route_log(self, job):
        lines = [l for l in tb.read_text_with_fallback(job.source).splitlines() if l.strip()]
        return {"elements": tb.make_elements(lines)}          # 요소 스키마의 공통 필드는 tb가 채웁니다
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
| LLM 출력 파서 | 표준 JSON이 아닌 응답 해석 | `custom_field_*.yaml`의 `llm` 항목 안 `parser: {type: python, file, callable}` |

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
    amount = re.search(r"계약금액[:\s]*([0-9,]+)\s*원", text or "")
    return {"CONTRACT_NO": m.group(1) if m else None,
            "AMOUNT": amount.group(1).replace(",", "") if amount else None}
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
| facade끼리 import | 각 facade의 독립 실행과 교체가 어려워집니다. 공통 기능은 제공된 toolbox를 사용합니다 |
| `self`에 요청 상태 저장 | 인스턴스는 **프로세스당 1개**입니다. `await` 사이에 다른 요청의 값이 섞입니다. `job`을 명시적으로 받는 단계에서는 `job.notes`를 사용할 수 있습니다. 일반 훅에는 `job`이 자동 전달되지 않습니다 |
| `_`로 시작하는 메소드 오버라이드 | 훅 호출과 설정 적용 순서를 맡고 있습니다 |
| `processing/core/` 수정 | 릴리스 갱신에서 사라집니다. 필요하면 솔루션 개발자에게 훅 추가를 요청하세요 |
| docling 수정 | 소스가 아니라 wheel로 들어옵니다. 수정할 수 없습니다 |
| element 5키 스키마에서 필드 삭제·개명 | `/chunker`가 의존합니다. 추가 필드도 예약 필드 충돌과 소비 측 호환성을 확인합니다 |

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

#### 훅에서 발생한 예외가 가는 곳

**훅이 던진 예외는 그 요청 전체를 실패시킵니다.** core가 대신 삼켜 주지 않으므로, 문서 한 건이
아니라 요청 하나가 통째로 실패합니다. `error_policy: lenient`는 보강 단계에만 적용되며 훅 예외에는
적용되지 않습니다.

| 던진 것 | 응답 |
|---|---|
| `GenosServiceException` | `error_code`가 그대로 보존되고, 지정했다면 `stage`·`error_kind`도 함께 실립니다 |
| 그 밖의 예외 | 타입에 따라 `INPUT_ERROR` / `TIMEOUT_ERROR` / `INTERNAL_ERROR`로 자동 분류됩니다. `error_type`에는 예외 클래스명이 들어갑니다 |

`error_code`는 문자열로 그대로 전달되므로 현장에서 쓰는 코드 체계가 있으면 그 값을 넣습니다.
예제의 `'1'`은 특별한 뜻이 없는 기본값입니다.

여러 건을 처리하는 훅에서 일부만 실패했을 때는 다음과 같이 나눠 판단합니다.

```python
    def edit_input(self, ext, doc_type, data, work_dir=None, **kwargs):
        if ext != ".json" or doc_type != "notice":
            return data
        kept, dropped = [], []
        for item in data.get("items") or []:
            try:
                kept.append(self.normalize(item))
            except ValueError as exc:
                dropped.append((item.get("id"), str(exc)))   # 그 건만 제외하고 계속
        if not kept:                                          # 전부 실패하면 멈춘다
            raise GenosServiceException(
                error_code='1', error_msg=f'처리할 수 있는 항목이 없습니다: {dropped}',
                stage='edit_input', error_type='permanent')
        return {"items": kept}
```

> 목표필드 이름이 `title`·`created_date`·`appendix` 같은 **예약 필드와 겹치면** 청킹 단계에서
> `stage: custom_fields`로 요청이 실패합니다. 오류 메시지가 겹친 필드 이름을 알려 주므로 그 이름을
> 바꾸면 됩니다. 예약 필드 목록은 [6.3](#63-출력-스키마-청크)에 있습니다.

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
| `UPDATED_AT` | 갱신 시각 기록. **배포할 때마다 갱신합니다** ([4.3](#43-배포-절차)). 아직 없으면 4.3 에서 만듭니다 |
| `genon/preprocessor/facade/parser_processor.py` | 파싱. 주 수정 대상 |
| `genon/preprocessor/facade/chunking_processor.py` | 청킹. 주 수정 대상 |
| `genon/preprocessor/resource/` | config yaml + `custom_field_*.yaml` + 프롬프트 |
| `genon/preprocessor/resource/templates/` | 새 문서 유형 템플릿 5종 |
| `genon/preprocessor/examples/` | 검증 스크립트 |
| `genon/preprocessor/sample_files/` | 샘플 문서 |
| `main.py` | FastAPI 앱. 수정 대상이 아닙니다 |

#### 작업 브랜치와 기준선 준비

아래 명령은 개발 환경의 기존 `feature/dev` 브랜치에서 작업하는 예입니다. 운영 대상은 담당자와
합의한 `feature/prd`를 사용합니다. 공유 작업 브랜치인지, 담당자가 말하는 sync가 어떤 저장소·브랜치를
동기화하는 작업인지 먼저 인계 정보에서 확인하세요.

```bash
git rev-parse --show-toplevel   # 출력된 저장소 루트로 이동한 뒤 이후 명령을 실행합니다
git status --short             # 기존 변경이 있으면 소유자와 보관 여부를 확인합니다
git fetch origin
git switch feature/dev        # 로컬 브랜치가 이미 있을 때
git branch --show-current
git log -1 --oneline
```

로컬 브랜치가 없고 원격에만 있다면 `git switch --track origin/feature/dev`를 사용합니다.
원격에도 없다면 담당자가 지정한 기준 커밋에서 브랜치를 만들며, 임의로 기준을 선택하지 않습니다.
작업 시작 시 기준 커밋을 기록하고 [1.7](#17-⑥-결과-값을-확인합니다)의 회귀 검사 기준선을 먼저 저장하세요.

### 3.3 신속한 확인 방법 — facade 단독 실행

facade 두 파일은 **독립적으로 실행할 수 있습니다.** 서버를 실행하지 않고 문서 한 건을 처리하여 결과를 확인할 수 있습니다.
훅이나 `custom_field_*.yaml`을 수정한 직후 확인하는 방법으로 가장 빠릅니다.

```bash
# 실행 위치: 저장소 루트 (import 경로 때문에 반드시 -m으로 실행합니다)
python -m genon.preprocessor.facade.parser_processor --config genon/preprocessor/resource/parser_processor_config.yaml 계약서.pdf --doc-type contract -o parsed.json
python -m genon.preprocessor.facade.chunking_processor --config genon/preprocessor/resource/chunking_processor_config.yaml parsed.json --doc-type contract -o chunks.json
```

위 명령은 **제공된 코드스페이스의 파이썬 환경을 전제로 합니다**([3.2](#32-사전-구성-항목)). 개인 PC처럼
직접 구성한 환경에서는 `PYTHONPATH`와 의존성을 따로 맞춰야 하므로, 검증은 코드스페이스에서 수행하세요.

| 인자 | 뜻 |
|---|---|
| `--doc-type` | 유형별 설정 매칭과 훅의 처리 대상 제한에 쓰입니다. 생략하면 특정 유형 전용 처리는 적용되지 않지만, 조건 없는 등록과 공통 처리는 실행될 수 있습니다 |
| `--config` | 프로세서 설정 YAML 경로. 생략하면 `resource_dev/` 우선, 없으면 `resource/`. 배포 설정과 비교할 때는 명시합니다 |
| `-o, --out` | 결과 JSON 경로. 생략하면 stdout |
| `--log-level` | `5` DEBUG / `4` INFO / `3` WARNING / `2` ERROR / `1` CRITICAL / `0` 로그 없음 |

**설정 파일을 명시하세요.** facade 단독 실행은 `--config`가 없으면 `resource_dev/`의 같은 이름 파일을 먼저
찾고, 없을 때 `resource/`를 사용합니다. 반면 루트 `main.py`는 `resource/`를 명시합니다.
`resource/`를 고쳤는데 테스트에 반영되지 않으면 가장 먼저 이 차이를 확인하세요.

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

처리 결과는 HTTP 상태뿐 아니라 응답의 `code`로 판단합니다. `code: 0`이 성공입니다.
요청 형식 오류나 네트워크·게이트웨이 오류는 비-200으로 반환될 수도 있습니다.

#### 로컬 파일 업로드 → 파싱 결과 전달 → 청크 확인

아래 예제는 코드스페이스의 파일을 업로드합니다. `notice` 설정을 서버에 배포한 뒤 실행하세요.
로컬에 저장한 파싱 응답은 자동으로 서빙 컨테이너에 생기지 않으므로, 청커에는 JSON을 인라인으로 전달합니다.

```bash
# 실행 위치: 샘플 notice.csv가 있는 코드스페이스 디렉터리
curl --fail-with-body "${CS}/parser_upload" \
  -F 'file=@notice.csv' -F 'params={"doc_type":"notice"}' -o parser_response.json

python - <<'PY'
import json
from pathlib import Path
r = json.loads(Path('parser_response.json').read_text(encoding='utf-8'))
if r.get('code') != 0:
    raise SystemExit(f"파싱 실패: {r.get('errMsg')}")
payload = r['data']
assert isinstance(payload, dict), '파싱 data가 객체가 아닙니다.'
if not isinstance(payload.get('document'), dict):
    assert payload.get('elements'), '요소형 파싱 결과가 0건입니다.'
request = {'file_path': 'notice.csv',
           'params': {'doc_type': 'notice', 'document': payload}}
Path('chunker_request.json').write_text(
    json.dumps(request, ensure_ascii=False), encoding='utf-8')
PY

curl --fail-with-body "${CS}/chunker" -H 'Content-Type: application/json' \
  --data-binary @chunker_request.json -o chunker_response.json

python - <<'PY'
import json
from pathlib import Path
r = json.loads(Path('chunker_response.json').read_text(encoding='utf-8'))
if r.get('code') != 0:
    raise SystemExit(f"청킹 실패: {r.get('errMsg')}")
chunks = r['data']
assert isinstance(chunks, list) and chunks, '최종 청크가 없습니다.'
print(json.dumps(chunks, ensure_ascii=False, indent=2))
PY
```

앞 단계가 실패하면 다음 명령으로 진행하지 않습니다. 인라인 전달에서는 `file_path`를 원본 식별용으로 사용하며,
예제의 `notice.csv`를 청커 서버에서 읽지 않습니다. 인라인 `params.document`가 없으면 `file_path`는
**서버 내부에 저장된 파싱 결과 JSON 파일의 경로**여야 합니다.

파서 HTTP 응답의 `data` 전체를 `params.document`에 넣습니다. 문서형이면 `{document: {...}}`,
요소형이면 `{elements: [...]}` 구조가 전달됩니다. 구현은 파서 응답 전체도 받아들이지만, 성공 여부를 먼저
확인한 후 `data`를 전달하는 방식을 권장합니다. facade 단독 실행의 `parsed.json`은 이미 이 `data`에 해당합니다.

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

**이 값은 고객 수정본을 구분하는 표식입니다. 배포할 변경에 함께 포함하세요.**

Genos 웹 UI는 "지금 실행 중인 코드서빙이 내 수정본으로 기동된 것인지"를 알려 주지 않습니다.
코드서빙이 고정되어 있어 이름도 리비전도 그대로이기 때문입니다. 릴리스 스탬프만으로 고객 수정까지 식별하기 어려우므로
수정 시각, 배포 이력, 재기동 시각, 기능 확인 결과를 함께 사용합니다.

저장소 루트(`main.py`와 같은 위치)의 `UPDATED_AT` 파일 **첫 번째 줄**을 갱신합니다. 최대 100자까지 읽으며 구현은 형식을 강제하지 않습니다.
운영 비교를 위해 **시간대가 포함된 ISO 8601 시각만** 기록하세요(예: `2026-09-19T14:30:00+09:00`).
변경 설명은 커밋·PR에 기록합니다. 파일이 없으면 생성하세요.

```bash
# 실행 위치: 저장소 루트
python - <<'PY'
from datetime import datetime
from pathlib import Path
Path("UPDATED_AT").write_text(
    datetime.now().astimezone().isoformat(timespec="seconds") + "\n", encoding="utf-8")
PY
```

> `VERSION` 파일은 **직접 수정하지 마세요.** JSON이 손상되면 `/version` 응답의 `version`까지
> `unknown`으로 나옵니다.

#### ② 커밋 / 푸시

```bash
# 실행 위치: 코드스페이스 터미널, 저장소 루트
git branch --show-current                    # 개발 배포 예제: feature/dev인지 확인
git status --short
git diff                                     # 아직 스테이징하지 않은 변경 검토

# 아래는 notice 설정을 추가한 예입니다. facade도 고쳤다면 해당 파일을 명시적으로 추가합니다.
git add genon/preprocessor/resource/custom_field_notice.yaml \
        genon/preprocessor/resource/parser_processor_config.yaml UPDATED_AT
git diff --cached --stat                     # 커밋할 파일 목록
git diff --cached                            # 커밋할 실제 내용 확인
git commit -m "공지사항 doc_type 추가"

git fetch origin
git log --oneline origin/dev..HEAD            # 대상 브랜치에 없는 커밋 확인
git diff --stat origin/dev...HEAD             # PR에 포함될 변경 범위 확인
git push origin HEAD:feature/dev
```

개발 배포 예제를 운영에 적용할 때는 대상 브랜치와 작업 브랜치를 모두 변경합니다. 공유 브랜치라면 다른
작업자의 변경 여부도 확인하세요. push가 거부되면 강제 push하지 말고 원격 변경을 확인합니다.
`git add .`로 전체를 추가하지 말고 수정한 파일만 지정하세요. `git diff`는 커밋할 내용이나 이미 커밋한 내용을
대신 보여주지 않으므로, 위와 같이 스테이징·커밋·PR 범위별로 검사합니다.

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
| `manual_updated_at` | `UPDATED_AT`의 첫 줄. **① 에서 적은 값.** 이것만 호출할 때마다 파일에서 새로 읽습니다 |
| `started_at` | 서버 프로세스의 버전 모듈 로드 시각. 재기동 확인에 사용 |
| `version` · `commit` | 릴리스 스탬프 |
| `updated_at` | **릴리스 커밋 날짜입니다.** 이름이 비슷하지만 ① 에서 적은 값이 아닙니다 |
| `source` | 스탬프를 읽은 곳. `file`(배포본) · `git`(소스 실행) · `unknown`(스탬프 없음) |
| `docling` · `docling_wheel` | 실제 설치된 엔진 버전과 배포 시점 wheel 파일명 |

> `updated_at`과 `manual_updated_at`을 혼동하지 마세요. **내가 적은 값은 `manual_updated_at`
> 하나뿐입니다.** `source`가 `unknown`이면 릴리스 스탬프 자체가 실리지 않은 것이므로
> `version`·`commit`으로는 아무것도 판정할 수 없습니다.

다음 순서로 판정합니다. `manual_updated_at` 일치만으로 실행 중인 코드까지 갱신됐다고 확정하지 않습니다.
이 값은 호출 시 파일에서 읽고, `started_at`은 프로세스가 모듈을 로드한 시각입니다.

| 확인 순서 | 결과 | 판단과 조치 |
|---|---|---|
| ① 수정 표식 비교 | 기대한 `manual_updated_at`과 다르거나 없음 | 호출한 환경·배포 대상·머지·빌드 상태를 확인합니다. 여기서 막히면 ② 로 넘어가지 않습니다. |
| ② 재기동 확인 | 기대한 값과 같지만 수정 시각이 `started_at`보다 늦음 | 파일은 바뀌었으나 실행 코드가 갱신되지 않았을 수 있습니다. 담당자에게 배포·재기동 이력을 확인합니다. |
| ② 재기동 확인 | 기존 표식이 자유 형식이거나 시계가 맞지 않아 비교할 수 없음 | 문자열의 대소로 비교하지 않습니다. CI/CD 배포 이력과 담당자의 재기동 기록으로 확인합니다. |
| ② 재기동 확인 | 기대한 값과 같고 배포 후 프로세스 기동이 확인됨 | ③ 으로 진행합니다. 시간대가 다르면 같은 기준으로 변환해 비교합니다. |
| ③ 기능 검증 | 예상 필드·본문·청크 결과가 모두 일치 | 해당 검증 범위의 배포 완료로 판단합니다. |

수정 시각보다 기동 시각이 늦다는 사실만으로 파일 반영 시점을 증명할 수는 없습니다. 표식은 커밋 시각에
작성되므로, 실제 배포 이력도 함께 확인해야 합니다. 여러 인스턴스가 있다면 전체 교체 완료 여부를 담당자에게
확인하세요. `/health`는 서비스 응답 여부만 알려 주며 수정본 반영 증거가 아닙니다.

반영이 확인되면 실제 문서 한 건을 처리하여 기능을 확인합니다.

```bash
curl "${CS}/parser" -H 'Content-Type: application/json' \
  --data '{"file_path": "/app/src/service/.../notice.xlsx", "params": {"doc_type": "notice"}}'
```

| 바꾼 것 | 확인 지점 |
|---|---|
| 새 `doc_type` | 요소형은 `data.elements[].metadata`, 문서형은 `data.metadata`를 확인하고, 최종 청크에서도 목표필드·본문을 검증합니다 |
| `chunk_size` · `chunk_mode` | `/chunker` 결과의 청크 개수와 길이 |
| enrichment `enable` | 응답의 해당 항목 유무, 처리 시간 변화 |
| 플레이스홀더 치환 | 로그에서 `미치환 placeholder` 경고가 사라졌는지 |

### 4.5 릴리스 갱신 시 사용자 수정 사항 유지

릴리스 갱신은 고객 수정 파일을 덮어쓸 수 있습니다. **기존 공급 릴리스와 고객 수정본의 차이**를 보관한 뒤,
새 릴리스에 필요한 변경만 다시 적용합니다. 인자 없는 `git diff`는 커밋된 수정과 미추적 파일을 보관하지 않습니다.

1. `git status --short`로 미커밋·미추적 파일을 확인합니다. 보존할 소스·설정·새 파일을 명시적으로 추가하고 커밋합니다. 결과 JSON이나 임시 파일은 별도로 보관합니다.
2. 기존 공급 릴리스의 **정확한 태그 또는 커밋**을 담당자에게 확인합니다. 임의로 직전 커밋을 기준으로 삼지 않습니다.
3. 작업 트리가 정리된 상태에서 고객 수정본 커밋, 복구용 Git 번들, 변경 패치를 저장소 밖에 보관합니다.

```bash
# 실행 위치: 저장소 루트. 값을 실제 인계받은 기준으로 교체합니다.
BASE_RELEASE='<기존 공급 릴리스 태그 또는 커밋>'
BACKUP_DIR='../monimo-backup-20260919'          # 릴리스 갱신 대상 밖의 새 디렉터리
mkdir "$BACKUP_DIR"                          # 이미 존재하면 다른 이름을 사용합니다
git rev-parse HEAD > "$BACKUP_DIR/customer-commit.txt"
git rev-parse "$BASE_RELEASE" > "$BACKUP_DIR/base-commit.txt"
git bundle create "$BACKUP_DIR/customer.bundle" HEAD
git bundle verify "$BACKUP_DIR/customer.bundle"

# 관련 Python 추출기·프롬프트·신규 설정 파일도 커밋되어 있어야 합니다.
git diff --binary "$BASE_RELEASE" HEAD -- \
  genon/preprocessor/facade/parser_processor.py \
  genon/preprocessor/facade/chunking_processor.py \
  genon/preprocessor/resource > "$BACKUP_DIR/my_change.patch"
git apply --stat "$BACKUP_DIR/my_change.patch"
```

위 경로 밖에 고객 수정이 있다면 패치 범위에 추가합니다. 신규 파일은 **커밋해야** 패치에 포함됩니다.
번들은 복구용 이력이며, 새 릴리스 전체를 이전 고객 수정본으로 덮어쓰는 용도로 사용하지 않습니다.
설정·이력이 포함된 백업은 접근이 제한된 장소에 보관합니다.

담당자 절차로 새 릴리스를 작업 브랜치에 반영한 뒤 다음을 수행합니다.

```bash
git apply --check "$BACKUP_DIR/my_change.patch"  # 적용 가능 여부만 검사
# 검사가 성공하고 변경 내용을 검토했을 때만 실행
git apply "$BACKUP_DIR/my_change.patch"
git diff
```

검사가 실패하면 패치의 변경 의도를 새 파일에 수동 반영합니다. 훅 시그니처가 같아도 주변 코드가 바뀌면
충돌할 수 있습니다. `resource/` 전체를 옛 파일로 복원하지 말고 새 설정 키·프롬프트를 유지하며 고객 설정값을
이관하세요. 사전 설정 검사, 기존 문서 회귀 검사, 기능 검증을 완료한 뒤 4.3의 배포 절차를 따릅니다.

### 4.6 배포 실패와 롤백

| 상황 | 조치 |
|---|---|
| 빌드·배포 실패 | 담당자에게 대상 환경, PR, 커밋, 실패 시각과 로그 위치를 전달합니다. 기존 정상 버전이 계속 실행 중인지 확인합니다. |
| 표식·재기동 확인 실패 | 4.4의 순서로 대상 환경과 배포 이력을 확인합니다. 확인되지 않은 상태를 배포 완료로 처리하지 않습니다. |
| 배포 후 결과 이상 | 문제 입력과 기대 결과·실제 결과를 보관하고 담당자에게 롤백을 요청합니다. |

롤백 대상은 작업 전에 기록한 정상 배포 커밋 또는 배포 이미지입니다. 담당자가 정한 방식으로 이전 이미지를
재배포하거나 되돌림 커밋을 PR로 반영합니다. 공유 브랜치의 이력을 강제로 되돌리지 않습니다.
코드 되돌림 PR을 만들 때는 `UPDATED_AT`에 새 시각을 기록하고 되돌린 대상 커밋을 PR에 남깁니다.
이전 이미지를 그대로 재배포하면 표식도 이전 값이 되므로 그 이미지의 기대값과 비교합니다.
롤백 후에도 `/version`, 재기동, 대표 문서의 파싱·청킹 결과를 확인합니다.
이미 변경된 청크가 적재되었다면 코드 롤백만으로 DB 데이터가 복구되지는 않습니다. 재적재·재색인 범위는 적재 담당자와 확인합니다.

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
| `custom_field_*.yaml` (유형별 제공 파일) | 문서 유형별 값 추출과 청크 본문 ([1장](#1-새-문서-유형-추가하기)) |
| `templates/custom_field_TEMPLATE_*.yaml` | 새 문서 유형을 만들 때 복사할 원본 5종. 등록하지 않습니다 |

### 5.2 주요 구성 옵션

"적용 파일" 열을 반드시 확인하세요. 파일마다 키 구조가 다릅니다.

| 섹션 · 키 | 적용 파일 | 기본값 | 가능한 값 | 변경 시점 |
|---|---|---|---|---|
| `output.format` | **parser 전용** | `docling` | `json` / `html` / `markdown` / `docling` | Docling 문서의 구조를 청커에 보존하려면 `docling`. 요소형 `elements`도 청커 입력으로 지원됩니다 |
| `ocr.ocr_mode` | parser | `auto` | `auto` / `force` / `disable` | 스캔 문서가 많으면 `force`, OCR 서버가 없으면 `disable` |
| `chunking.chunk_size` | chunking | `1000` | 정수 | 크기 기준. 문서형은 양수일 때 `min_chunk_size` 하한을 적용합니다. 요소형은 별도 분할 경로를 사용합니다 |
| `chunking.min_chunk_size` | chunking | `1024` | 정수 | **문서형 경로 전용** 크기 하한. `0` 이하이면 하한 보정을 끕니다. 기본 설정의 문서형 실효 크기는 `1024`입니다 |
| `chunking.chunk_mode` | chunking | `split_only` | `split_only` / `resize_all` | `split_only`는 섹션 구조 유지(작은 청크 다수), `resize_all`은 크기 기준 재조립(균일) |
| `chunking.tokenizer_type` | chunking | `char` | `char` / `huggingface` | `chunk_size`의 **단위가 바뀝니다** |
| `chunking.include_chunk_header` | chunking | 켜짐 | `0` / `1` | 청크 선두의 `HEADER:` 줄이 필요 없을 때 `0` |
| `chunking.text_cleanup` | chunking | 코드 기본 `off`; 현재 제공 YAML은 `mode: safe`와 규칙 지정 | `off` / `safe` 또는 `{mode, rules}` | 문자 정규화와 선택적 정규식 정제. 실제 설정 파일의 값을 확인합니다 |
| `enrichment` 각 항목의 `enable` | parser | 항목별 상이 | `true` / `false` | LLM 호출 비용과 시간을 줄일 때 |
| `formats.xlsx.processing_mode` | parser | `tabular` | `tabular` / `docling` | 엑셀을 표로 다룰지 문서로 다룰지 |
| `defaults.log_level` | 전부 | `4` | `5`=DEBUG ~ `1`=CRITICAL, `0`=NOLOG | 디버깅할 때 `5` |

> `chunk_size: 0`은 "청크 1개"가 아닙니다. 크기 기반 **병합과 분할을 끄는** 값입니다.
> docling 문서 입력이면 섹션 구조 기준 청크가 그대로 남아 오히려 더 많아질 수 있습니다.
> 청크를 크게 합치려는 목적이라면 `0`이 아니라 충분히 큰 값을 주세요.
> 일반 요소형·분할 가능한 행 경로에서 `0` 또는 음수는 내부적으로 1,000,000자 기준으로 처리됩니다.
> 크기 분할이 절대 일어나지 않는다는 보장은 아니며, 표 분리도 별도 설정에 따라 계속 적용됩니다.

> `tokenizer_type`을 바꾸면 **`chunk_size`의 단위가 바뀝니다.** `10000`은 `char`에서 1만 자,
> `huggingface`에서 1만 토큰입니다. 토큰당 글자 수는 언어와 토크나이저에 따라 달라집니다.
> 이 옵션은 문서형 경로에 적용되며, 요소형의 크기는 문자 수를 기준으로 합니다.

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

**파서와 청커는 별도 요청입니다.** 파서 결과에 기록된 `doc_type`이 청커의 요청 인자로 자동 승격되지는 않습니다.
청커의 `CONFIG_BY_DOC_TYPE`과 유형별 훅을 적용하려면 청커 요청에도 같은 `params.doc_type`을 명시합니다.
현재 청커의 설정표 조회는 전달받은 문자열 그대로 수행하므로 양쪽 모두 공백 없는 소문자 표준값을 사용하세요.

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
입니다. 경로는 구성 YAML이 있는 폴더를 기준으로 합니다. 프롬프트만 변경하는 경우에도 `resource/prompt_*.md` 수정 후
4장의 커밋·PR·머지·배포·검증 절차를 따릅니다.

---

## 6. 주요 동작 방식

### 6.1 파싱 결과 형식

반환 형식은 확장자뿐 아니라 적용된 매핑과 `output.format`으로 결정됩니다.

| 입력과 처리 조건 | 파서 결과 |
|---|---|
| 엑셀·CSV에 `rows` 매핑이 적용됨 | 요소형 `{elements: [...]}` |
| JSON에 `records`·`sections` 매핑이 적용됨 | 요소형 `{elements: [...]}` |
| 매핑 없는 엑셀·CSV, `processing_mode: tabular` | 요소형 `{elements: [...]}` |
| 매핑 없는 엑셀·CSV, `processing_mode: docling` | Docling 문서로 파싱한 뒤 출력 형식에 따라 직렬화 |
| PDF·HWP·DOCX·HTML·Markdown 등 문서 경로 | `output.format: docling`이면 문서형 `{document: {...}}`, 다른 출력 형식이면 요소형 |
| JSON의 본문 추출 설정이 적용되거나, 매핑 없는 JSON·TXT 등이 텍스트로 판정됨 | Docling 문서 경로. 확장자만으로 요소형이라고 판단하지 않음 |
| PPT·PPTX | PDF 변환에 성공하면 문서 경로, 변환 실패 시 요소형 대체 경로 |
| 이미지·기타 입력 | 해당 핸들러의 결과를 사용하므로 실제 응답 구조 확인 필요 |

`/chunker`는 `document`가 객체이면 문서형으로, 그렇지 않고 `elements`가 목록이면 요소형으로 판별합니다.
두 키가 함께 있으면 `document`를 우선합니다. 문서형 응답에 빈 `elements: []`가 있어도 오류가 아닙니다.
element의 공통 키는 `{category, content, coordinates, id, page}`이며, 행 기반 element에는
`metadata`와 분할 관련 정보가 추가될 수 있습니다. 최종 청크의 개수는 element 개수와 항상 같지는 않습니다.

| 구분 | 문서형 경로 | 행·레코드형 요소 경로 | 일반 요소형 경로 |
|---|---|---|---|
| 분할 방식 | `GenosSmartChunker`의 문서 구조 기반 분할·병합 | 기본 1건당 1청크. 분할 허용 시 긴 본문을 나누며 표 분리도 적용 가능 | 일반 텍스트는 문자 기반 분할. 오디오·레거시 표 등 예외 경로 있음 |
| 크기 단위 | `char` / `huggingface` | 문자 수 | 문자 수 |
| 크기 하한 | `chunking.min_chunk_size` | 문서형 하한 규칙을 적용하지 않음 | 문서형 하한 규칙을 적용하지 않음 |
| 설정 확인 | `chunk_size`, `chunk_mode`, `tokenizer_type` | `body.split`, `body.repeat`, `chunk_size`, 표 분리 설정 | `chunking.recursive.chunk_overlap` 등 |

문서형에서는 문서의 좌표·미디어 정보를 활용합니다. 요소형에서는 최종 `chunk_bboxes`·`media_files`가
`"."`으로 채워지는 경로가 있으므로 원문 좌표가 보존된다고 가정하지 마세요.

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
| 본문 | `text` (문서형에서 헤더 설정이 켜져 있고 섹션 경로가 있을 때 `HEADER:` 줄이 붙습니다) |
| 통계 | `n_char` · `n_word` · `n_line` (본문에서 자동 계산) |
| 위치 | `i_page` · `e_page` · `n_page` · `i_chunk_on_page` · `n_chunk_of_page` · `i_chunk_on_doc` · `n_chunk_of_doc` |
| 참조 | `chunk_bboxes` · `media_files` (문서형에서는 JSON 문자열, 요소형 등의 미제공 경로에서는 `"."` 표식일 수 있습니다) |
| 문서 메타 | `title` · `reg_date` · `created_date` · `appendix` · `file_path` · `guardrail_categories`(마스킹 미사용 환경에서는 빈 값) |
| 표 메타 | `has_table` · `table_refs` · `table_split_index` · `table_split_total` |

> **출력 모델이 추가 필드를 허용한다는 것이 적재 DB의 자동 확장을 뜻하지는 않습니다.** 추가·삭제·이름·타입 변경 시
> 적재 매핑, DB 컬럼, 검색 필터와의 호환성을 확인하세요. 기본 본문·통계·위치 필드와 같은 이름의 목표필드를 만들지 마세요.

### 6.4 응답 형식 및 오류

응답 형식은 `main.py`가 구성합니다. facade는 `data`에 포함할 값만 반환합니다.

```json
{ "code": 0, "errMsg": "success", "data": { } }
```

- 애플리케이션이 처리한 성공·실패는 HTTP 200으로 반환되며 `code: 0`이 성공입니다. 요청 형식 검증·네트워크·게이트웨이 오류는 비-200일 수 있으므로 HTTP 상태와 JSON 본문을 모두 검사합니다.
- 공통 오류 응답의 `error_type`은 예외 클래스명입니다. `error_code`, `traceback`도 포함됩니다.
- `stage`는 실패 단계, `error_kind`는 `transient` / `permanent` / `timeout`과 같은 실패 성격이며, 정보가 있을 때만 포함됩니다. 예외 생성자의 `error_type` 인자는 응답에서 `error_kind`로 나갑니다.
- core가 넣는 `stage` 값은 `custom_fields` · `doc_summary` · `image_description` · `metadata` · `doc_type_stamp`(보강 단계, `error_policy: strict`일 때)와 `request`(요청 제한 시간 초과)입니다. 훅에서 직접 지정한 값은 그대로 나갑니다.
- facade가 `GenosServiceException`으로 던진 오류는 `error_code`가 보존됩니다. 그 외 예외는
  타입에 따라 `INPUT_ERROR` / `TIMEOUT_ERROR` / `INTERNAL_ERROR`로 자동 분류됩니다.

**응답이 예상과 다른 경우**

| 응답 | 원인 | 확인할 것 |
|---|---|---|
| `…지원하지 않습니다` (`code:1`) | facade에 `IS_PARSER`/`IS_CHUNKER` 마커가 없어 요청이 도달하지 못함 | 클래스명(`DocumentProcessor`)과 마커를 바꾸지 않았는지 |
| `/parser`는 되는데 `data.document`가 없음 | 정상 요소형 경로일 수 있음 | 먼저 `data.elements`와 적용 매핑을 확인합니다. 문서형을 기대했다면 `output.format`과 6.1의 분기 조건을 확인합니다 |
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
| **doc_type(문서유형)** | 요청의 문서 유형 문자열. 등록 조건과 매칭하며 조건 없는 등록은 공통 적용됩니다. 청크 전달 범위는 1.1 참조 |
| **docling** | 문서 파싱 엔진. wheel로 제공되므로 직접 수정할 수 없습니다 |
| **element** | 요소형 결과의 한 조각. `{category, content, coordinates, id, page}` |
| **청크(chunk)** | 벡터 DB에 넣을 텍스트 조각 |
| **enrichment** | 파싱 결과에 필드·요약·설명을 덧붙이는 단계. 기능에 따라 LLM 또는 다른 추출기를 사용 |
| **목표필드** | `custom_field_*.yaml`의 `fields`에 선언한, 만들어 낼 값. 적재 DB 컬럼이 됩니다 |

## 부록 B. 지원 요청이 필요한 경우

다음은 직접 해결할 수 없는 항목입니다. **솔루션 개발자 또는 배포 담당자에게 요청하세요.**

| 상황 | 요청할 것 |
|---|---|
| 코드스페이스 개발 환경에 문제가 발생함 | 재구축 |
| 모델 서빙 주소·인증키를 모름 | 값 확인 또는 권한 |
| 코드서빙 `ID` · 리비전을 모름 | 접속 정보 |
| PR을 올렸는데 머지가 안 됨 | `prd` / `dev` 머지와 sync |
| `/version`의 표식은 맞지만 배포 후 재기동이 확인되지 않음 | 배포·재기동 이력 확인 후 필요한 조치 |
| 빌드가 실패함 | CI/CD 로그 확인 |
| `processing/core/`를 고쳐야 할 것 같음 | 훅 추가 (고치기 전에 문의하세요) |
| docling 자체 동작을 바꿔야 함 | 엔진 수정 요청 |
| 새 파이썬 패키지가 필요함 | 베이스 이미지 의존성 추가 |

## 부록 C. custom_fields 설정 레퍼런스

이 문서에서 사용하는 주요 설정의 지원 범위와 최소 예제입니다. 세부 포맷 옵션은 같은 릴리스의
`resource/templates/custom_field_TEMPLATE_*.yaml` 주석을 함께 사용합니다. 지원하지 않는 키는
대체로 기동 오류가 되며, 일부 포맷 전처리 키는 통과해도 해당 경로에서 사용되지 않습니다.

✔ = 지원 / ✗ = 그 키를 적으면 **기동 실패** / ⚠ = 통과하지만 무효이거나 제한

### C.1 설정 표기와 내부 이름

괄호 안이 **내부 이름**입니다. 설정에 적는 이름이 아니라 **오류 메시지에 나오는 이름**이므로,
기동 실패 로그는 이 표로 되짚습니다. 설정 이름과 내부 이름이 다른 예로 `template`은 내부에서 `derive`로 표시됩니다.

**값 가져오기 (`fields:`)**

| 기능 | 설정 키 (내부 이름) | rows | records | sections | document | html |
|---|---|:-:|:-:|:-:|:-:|:-:|
| 별칭 | `alias` | ✔ | ✔ | ✔ | ✔ | ✗ (`select` 사용) |
| 반복 key 전부 수집 | `collect` (`collect_key_map`) | ✗ | ✔ | ✗ | ✗ | ✗ |
| 상수 | `const` (`constants`) | ✔ | ✔ | ✔ | ✔ | ✔ |
| 기본값(빈 값만) | `default` (`defaults`) | ✔ | ✔ | ✔ | ✔ | ✔ |
| 값 표기 통일 | `values` (`value_map`) | ✔ | ✔ | ✔ | ✔ | ✔ |
| 값 변환(체이닝) | `transform` (`transforms`) | ✔ | ✔ | ✔ 주의² | ✔ 주의² | ✔ 주의² |
| 필드 결합 | `template` (**`derive`**) | ✔ | ✔ | ✔ | ✔ | ✔ |
| JSON 한 칸에 묶기 | `pack` | ✔ | ✔ | ✔ | ✔ | ✔ |
| 청크 메타에서 빼기 | `meta: false` (`meta_include`) | ✔ | ✔ | ✔ | ✔ | ✔ |
| 객체를 값으로 받기 | `raw` (`raw_fields`) | ✗ | ✔ | ✔ 루트만 | ✗ | ✗ |
| 순번 매기기 | `seq` (`sequence`) | ✔ | ✔ | ✗ | ✗ | ✗ |

`pack`은 **값 확정 후 마지막 단계**입니다 — `seq`로 매긴 순번과 `llm`이 채운 값까지 확정된 뒤에 묶습니다.
`meta: false`인데 본문·선별·파생 어디에도 쓰이지 않으면 경고만 남기고 기동합니다(값을 만들고 버리는
설정입니다).

**청크 본문 (`body:`)**

| 기능 | 설정 키 (내부 이름) | rows | records | sections | document | html |
|---|---|:-:|:-:|:-:|:-:|:-:|
| 본문 구성 필드 | `body.fields` (`text_fields`) | ✔ 선택 | ✔ **필수** | ⚠ 뜻이 다름¹ | ✗ | ✗ |
| 항목명 | `body.labels` (`field_labels`) | ✔ | ✔ | ✔ | ✔ | ✔ |
| 긴 본문 분할 | `body.split` (`split`) | ✔ | ✔ | ✗ 주의³ | ✗ | ✗ |
| 모든 청크에 반복 접두 | `body.repeat` (`chunk_prefix_fields`) | ✔ split 시만 | ✔ split 시만 | ✗ 주의³ | ✔ | ✔ |
| 첫 청크에만 1회 | `body.once` (`first_chunk_fields`) | ✗ | ✗ | ✔ | ✔ | ✔ |
| 본문을 메타 필드에 복사 | `body.mirror_to` (`body_fields`) | ✗ | ✗ | ✗ | ✔ | ✔ |

¹ sections의 `body.fields`는 본문 구성이 아니라 **공통 필드를 청크 접두에 실을지 정하는
스위치**입니다. 본문은 트리 순회가 만듭니다.

³ sections는 긴 본문을 **항상 분할**하고 접두도 **자동 생성**하므로 설정할 것이 없습니다.
동작이 그렇다는 것이지 키를 적어도 된다는 뜻이 아닙니다. `body.split`·`body.repeat`을 적으면
sections가 읽지 않는 키이므로 **기동에 실패합니다.**

**입력 데이터 구조 · 선별 · LLM**

| 기능 | 설정 키 (내부 이름) | rows | records | sections | document | html |
|---|---|:-:|:-:|:-:|:-:|:-:|
| 레코드 배열 위치 | `source.records_at` (`records`) | ✗ | ✔ | ✗ | ✗ | ✗ |
| 못 찾을 때 정책 | `source.on_missing` (`missing_policy`) | ✗ | ✔ | ✔ | ✗ | ✗ |
| 여러 건 접기 | `source.merge_rows` (`row_merge`) | ✔ | ✔ | ✗ | ✗ | ✗ |
| 섹션 표시 이름 | `source.sections` | ✗ | ✗ | ✔ | ✗ | ✗ |
| 서브트리 제외 | `source.ignore_keys` | ✗ | ✗ | ✔ | ✗ | ✗ |
| 포맷 전처리 | `source.pre.markdown` / `.html` | ⚠ **무시** | ⚠ **무시** | ⚠ **무시** | ✔ | ⚠ `.html` 적용, `.markdown` 무시 |
| 필수값(빈 값 제외) | `require.fields` (`required`) | ✔ 건별 | ✔ 건별 | ✔ 문서 전체 | ✗ | ✗ |
| 값 기반 제외 | `filter` | ✔ | ✔ | ✗ | ✗ | ✗ |
| 필드별 LLM 생성 | `llm:` (`llm_fields`) | ✔ 행별 | ✔ 레코드별 | ✔ 문서 1회 | (본체가 LLM) | ✗ (선택자 추출) |


² `transform` 자체는 지원됩니다. 일부 문자열 변환은 표 구조나 줄바꿈을 바꿀 수 있으므로 변환 후 본문을 확인하세요.

HTML 필드에는 `select: ".title"`처럼 CSS 선택자를 지정합니다. 기본은 선택한 첫 요소의 내용을 텍스트로
변환하며, `attr: href`를 함께 쓰면 그 요소의 속성값을 읽습니다. `attr`은 `select`와 함께 사용합니다.
HTML 원문이 입력으로 전달되어야 하며, JSON 안에 HTML이 들어 있으면 C.6의 `source.pre.json` 설정이 필요합니다.

> `extractor`는 **적지 않는 것이 기본입니다.** 설정 파일의 `source.kind`가 정합니다. 굳이 적으면
> 그 값과 일치해야 하고, 어긋나면 **설정에 적은 적도 없는 키 이름**으로 기동이 실패해 원인을
> 짚기 어렵습니다.

> `enrichment.toc.doc_type`은 **완전히 다른 값**입니다. 그쪽은 목차 추출 알고리즘을 고르는
> 옵션이라 `normal` / `law`만 받습니다. `custom_fields`의 `doc_type`과 섞지 마세요.

내부 이름이 kind마다 갈리는 키가 셋 있습니다. 위 표의 괄호 안 이름으로 로그를 찾지 못하면 여기를 봅니다.

| 설정 키 | kind별 내부 이름 |
|---|---|
| `alias` | `rows: column_map` · `records: key_map` · `sections: shared_fields` · `document: front_matter_map`. `html`은 `alias` 대신 `select`를 사용합니다 |
| `require.fields` | `sections`만 **`required_shared_fields`**, 나머지는 `required` |
| `llm` | `rows` · `records` · `sections`는 `llm_fields`. `document`는 항목이 **최상위 키로 펼쳐져** `output_fields` · `system_prompt` · `user_prompt` 같은 이름으로 나옵니다 |

### C.2 `transform` 10종

체이닝됩니다. 잘못된 이름·빠진 인자·컴파일되지 않는 정규식은 **기동 시** 걸립니다.

| 이름 | 인자 | 하는 일 |
|---|---|---|
| `date_int` | — | 날짜 텍스트 → `YYYYMMDD` 정수 |
| `date_int_flex` | — | 위 + 2자리 연도(`26.07.01`)·구분자 없는 `260701` |
| `text_norm` | — | NFKC + 공백 축약 + casefold (중복 판정용) |
| `regex_sub` | `pattern` 필수, `repl` 기본 `""` | 정규식 치환 (`"18,000원"` → `"18000"`) |
| `regex_extract` | `pattern` 필수, `group` 기본 `1` | 정규식 오려내기. 미매칭 시 `None` |
| `to_int` | `on_error` 기본 `null` | 숫자와 `-`를 남겨 정수화. 소수점 단위 변환은 하지 않음 |
| `truncate` | `length` 필수, `suffix` 기본 `""` | 길이 자르기(적재 컬럼 길이 맞춤) |
| `html_text` | — | HTML로 **강제** 평문화. 표·목록 유지 |
| `text` | — | JSON/HTML/평문 **자동 판별** 후 평문화 |
| `to_json` | `on_scalar`, `key` | 값을 **유효한 JSON 문자열**로 맞춤(적재 DB의 JSON 컬럼용) |

`regex_extract`의 기본 `group: 1`은 첫 번째 캡처 그룹을 뜻합니다. 패턴에 그룹이 없으면 매칭되어도 `None`이
나오므로 전체 매칭값을 원하면 `group: 0`을 지정합니다. `to_int("1.5")`는 `15`이므로 소수 금액·비율의
변환에는 사용하지 마세요. `truncate`는 `suffix`가 `length`보다 길면 길이 상한을 넘을 수 있으므로 접미사는
제한 길이 이하로 설정합니다.

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
`value`). **체인 맨 뒤에 배치해야 합니다.** 직렬화 후 문자열을 자르면 JSON 형식이 손상될 수 있으므로, 이런 구성은 사전 검증에서 거부됩니다.
`body.fields`·`body.repeat`·`body.once`·`body.mirror_to`에 쓸 수 없고 `pack`으로 다시 묶을 수도
없습니다(둘 다 기동 실패).

`pack`과 역할이 다릅니다 — `pack`은 필드 **여럿**을 컬럼 하나에 담고, `to_json`은 필드 **하나**의
모양을 보장합니다.

### C.3 `source` 보조 키

```yaml
source:
  kind: records
  records_at: eventList       # 레코드 배열이 담긴 key. 키 이름만 적습니다(임의 깊이에서 찾음)
  on_missing: skip            # skip(경고 후 0건) | error(기본, 요청 실패)
  merge_rows:                 # 여러 건에 걸쳐 쪼개진 값을 한 건으로 접습니다
    group_by:  [CMP_ID]
    order_by:  SEQ_NO
    concat:    [DETAIL_HTML]
    separator: ""
```

| 키 | 하는 일 |
|---|---|
| `records_at` | 레코드 배열의 key 이름. 생략하면 payload가 배열이면 원소마다 1건, 단일 object면 그 자체가 1건 |
| `on_missing` | `records`는 지정 배열을 못 찾았을 때, `sections`는 문서 공통 필수값이 없을 때의 정책. 두 경로 모두 `error`가 기본이며 `skip`은 경고 후 0건 처리 |
| `merge_rows` | `group_by`가 같은 **연속** 건만 한 묶음으로 접습니다. 병합은 값 변환·파생보다 **먼저** 실행됩니다 |
| `sections` | `kind: sections`의 섹션 표시 이름 |
| `ignore_keys` | `kind: sections`에서 제외할 **key 이름 glob**(경로가 아닙니다). 매칭되면 서브트리 통째 제외 |
| `pre.markdown` / `pre.html` | 문서 경로의 포맷 전처리. `html` 유형도 HTML 전처리를 사용할 수 있으며, 행·레코드·섹션 매핑 경로에서는 적용되지 않습니다 |

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
| records `alias` | **레코드 안 임의 깊이 BFS** | **얕은 깊이부터**, 같은 깊이에서는 별칭 선언 순서 |
| records `collect` | 레코드 안 BFS **레벨 순서**, 중복 제거 | 전부(중복 제외) |
| sections `alias` | **문서 루트의 스칼라만** | 루트 1회 확정 후 불변 |
| sections 본문 | 트리 **자동 전수 순회**(깊이 ≤ 12, 노드 ≤ 5000) | 미설정 key도 자동 포함 |
| sections `ignore_keys` | **key 이름 glob**, 경로 아님 | 매칭 시 서브트리 통째 제외 |
| rows `alias` | **시트 첫 행 헤더**(깊이 없음) + 시트 컨텍스트 | 선언 순서, 실제 컬럼 우선 |
| document `alias` | **front matter 최상위 키만**(중첩 미탐색) | **별칭 선언 순서** |

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
| `llm_cache` | `0` / `1` | 파싱 단계의 LLM 응답 캐시 사용 여부. 같은 입력·모델·프롬프트 등 캐시 조건이 일치할 때 재사용합니다. 청커 자체가 LLM을 호출하도록 만드는 옵션은 아닙니다. `workflow_id` 조건은 아래 참조 |
| `error_policy` | `strict` / `lenient` | **기본 `lenient`.** 보강 단계(`custom_fields` · `doc_summary` · `image_description` · `metadata` · `doc_type_stamp`) 실패를 `strict`는 요청 실패로 올리고, `lenient`는 경고만 남기고 그 단계를 건너뜁니다. 훅이 던진 예외와 파싱 실패에는 적용되지 않습니다. `workflow_id` 없이도 항상 적용됩니다 |
| `request_deadline` | 양수 초 | HTTP 처리 래퍼가 제한 시간을 적용하며 타임아웃 시 `error_kind: timeout`을 반환합니다. 생략·0 이하·숫자 변환 불가는 이 래퍼의 제한을 설정하지 않습니다. 단독 CLI에는 같은 HTTP 래퍼가 없으며, 동기 코드가 이벤트 루프를 점유하면 취소 응답이 지연될 수 있습니다 |

> `llm_cache`는 **`params.workflow_id`가 함께 전달될 때만** 동작합니다. 캐시 디렉터리를
> `<interim_root>/<workflow_id>/<run_id 또는 default>/llm_cache`로 잡기 때문입니다.
> `workflow_id` 없이 `llm_cache: 1`만 주면 **경고 없이 캐시가 꺼진 채로 처리됩니다.**
> 적재 워크플로를 거치지 않고 `curl`로 직접 호출하며 반복 비교할 때는 임의의 고정 문자열을
> `workflow_id`로 함께 넘기세요.
>
> ```bash
> curl "${CS}/parser" -H 'Content-Type: application/json' \
>   --data '{"file_path": "...", "params": {"llm_cache": 1, "workflow_id": "local-tune-notice"}}'
> ```

### C.6 유형별 최소 설정 예제

각 예제는 별도의 `custom_field_<유형>.yaml` 파일입니다. **여러 예제를 한 파일에 이어 붙이지 않습니다.**
`rows`는 1.4와 1.7의 실행 예제를 사용합니다. 아래 파일도 1.5의 방식으로 등록하고 요청의 `doc_type`을
등록한 값과 맞추세요. 본문 출력은 공통 청킹·보강 설정에 따라 달라질 수 있습니다.

#### records — JSON 배열의 각 항목을 검색 단위로 처리

입력: `{"items": [{"title": "알림 설정", "detail": "앱 설정에서 변경합니다."}]}`

```yaml
schema: v2
source:
  kind: records
  records_at: items
  on_missing: error
fields:
  TITLE: {alias: [title]}
  DETAIL: {alias: [detail], transform: text}
require:
  fields: [TITLE]
body:
  fields: [TITLE, DETAIL]
  labels: {TITLE: 제목, DETAIL: 내용}
  split: false
```

이 입력은 요소 1건을 만들며 최종 청크에 `TITLE: 알림 설정`과 `DETAIL` 값이 실려야 합니다.
`split: true`로 바꾸면 긴 본문을 여러 청크로 나눌 수 있습니다.

#### sections — 중첩 JSON을 섹션별로 처리

입력: `{"name": "예시 상품", "benefits": {"detail": "온라인 이용 혜택"}, "imageUrl": "sample.png"}`

```yaml
schema: v2
source:
  kind: sections
  sections: {benefits: 혜택}
  ignore_keys: [imageUrl]
  on_missing: error
fields:
  PRODUCT_NM: {alias: [name]}
require:
  fields: [PRODUCT_NM]
body:
  fields: [PRODUCT_NM]
  labels: {PRODUCT_NM: 상품명}
```

본문은 JSON 트리 순회로 만들어집니다. `body.fields`가 본문 전체를 지정하는 것은 아닙니다.
청크 메타데이터에 `PRODUCT_NM`이 있고, 본문에 혜택 내용이 포함되며 `sample.png`는 제외되는지 확인하세요.
섹션 수와 최종 청크 수는 구조·본문 길이·분할 설정에 따라 달라집니다.

#### document — 문서 본문에서 LLM으로 추출

다음 예제는 프로세서 설정의 `model_presets.default`에 유효한 모델 접속 정보가 있다는 전제입니다.
테스트 문서에 `문서 제목: 알림 설정 안내`를 포함하고 `TITLE`의 추출값과 최종 청크 전달 여부를 확인합니다.
LLM 결과는 결정적이지 않으므로 정확한 기대값 검사를 별도로 수행합니다.

```yaml
schema: v2
source: {kind: document}
fields:
  TITLE: {default: null}
body:
  repeat: [TITLE]
  labels: {TITLE: 문서 제목}
llm:
  - out: [TITLE]
    endpoint: {model_preset: default}
    parser: {type: json}
    params: {temperature: 0.0, max_tokens: 1000, timeout: 60}
    prompt:
      system: '문서 제목을 TITLE 키의 JSON 객체로 반환하세요. 제목이 없으면 null을 반환하세요.'
      user: |
        다음 문서에서 제목을 추출하세요.
        {{raw_text}}
```

문서형에는 행 단위 `require`·`filter`·`seq`·`body.fields`·`body.split`을 넣지 않습니다.
추출에 모델이 필요하지 않으면 2.6의 `python` 예제를 사용합니다.

#### html — 원문 마크업의 CSS 선택자로 추출

입력 HTML 예: `<article><h1 class="title">알림 설정 안내</h1><a class="detail" href="/notice/1">자세히</a></article>`

```yaml
schema: v2
source: {kind: html}
fields:
  TITLE: {select: 'h1.title'}
  LINK: {select: 'a.detail', attr: href}
body:
  repeat: [TITLE]
  labels: {TITLE: 제목}
```

`TITLE`은 `알림 설정 안내`, `LINK`는 `/notice/1`이어야 합니다. 선택자는 첫 번째 일치 요소를 사용합니다.
값은 문서 메타데이터와 최종 청크에 전달되며, 본문 자체는 Docling 문서 경로가 만듭니다.
원문이 JSON 안에 HTML 문자열로 들어 있다면 위 예제의 `source`를 다음처럼 바꿉니다.

```yaml
source:
  kind: html
  pre:
    json:
      body_from: [content]
      format: html
      on_missing: error
```

입력 예는 `{"content": "<article><h1 class='title'>알림 설정 안내</h1></article>"}`입니다.
`body_from`은 경로가 아니라 원본 key 이름 목록입니다. 이 입력에는 링크가 없으므로 `LINK`는 빈 값이 됩니다.

### C.7 필드 조합과 LLM 설정

#### template · pack · seq

다음은 `rows`·`records` 설정의 `fields`에 추가할 수 있는 예입니다.

```yaml
fields:
  QUESTION: {alias: [question, 질문]}
  ANSWER: {alias: [answer, 답변]}
  SUMMARY: {template: '{{QUESTION}} / {{ANSWER}}'}
  ROW_NO: {seq: {prefix: 'FAQ-', width: 4, start: 1}}
  EXTRA: {pack: [QUESTION, ANSWER, ROW_NO]}
```

- `template`: 이미 만들어진 필드를 `{{필드명}}`으로 참조해 값을 조합합니다.
- `seq`: 선별 후 순번을 부여합니다. 예제의 결과는 `FAQ-0001`, `FAQ-0002`입니다. 재처리 시 입력 순서가 달라질 수 있으므로 영구 식별자로 사용하지 않습니다.
- `pack`: 지정한 여러 필드를 하나의 JSON 컬럼 값으로 묶습니다. LLM 생성 필드와 순번까지 확정된 뒤 실행합니다.
- `meta: false`: 값을 계산하되 최종 청크 메타데이터에서는 제외합니다. 본문·파생값의 재료로는 사용할 수 있습니다.

#### 행·레코드·섹션형의 선택적 LLM 생성

아래는 `records` 예제에 이미 있는 `DETAIL`에서 `SUMMARY`를 생성하는 추가 블록입니다.
생성한 요약도 본문에 넣으려면 기존 `body.fields`에 `SUMMARY`를 추가하세요.

```yaml
llm:
  - out: [SUMMARY]
    in: [DETAIL]
    endpoint: {model_preset: default}
    parser: {type: json}
    params: {temperature: 0.0, max_tokens: 1000, timeout: 60}
    prompt:
      system: '입력을 한 문장으로 요약하여 SUMMARY 키를 가진 JSON 객체로 반환하세요.'
      user: |
        {{raw_text}}
```

| 항목 | 의미와 기본 동작 |
|---|---|
| `llm` | 설정 항목의 목록입니다. 문서형 예제는 하나의 항목으로 구성합니다. |
| `out` | 출력할 목표필드 이름 목록. 프롬프트가 반환하는 JSON 키와 일치해야 합니다. |
| `in` | 행·레코드·섹션형에서 LLM 입력으로 결합할 필드 목록. 문서형은 본문을 사용하므로 생략합니다. |
| `endpoint.model_preset` | 프로세서 설정의 모델 프리셋 이름. 예제의 `default`가 실제로 등록되어 있어야 합니다. |
| `parser.type` | 기본 `json`. 외부 응답 해석기를 쓰면 `python`을 지정합니다. |
| `params` | 생성 옵션. 문서형 템플릿의 생략 기본값은 `max_tokens: 1000`, `temperature: 0.0`, `timeout: 60`입니다. 필요한 경우 명시합니다. |
| `prompt.user` | `{{raw_text}}`를 포함해 실제 입력을 모델에 전달합니다. 출력 필드가 비면 프롬프트와 응답 키부터 확인합니다. |

외부 LLM 응답 파서는 **`llm` 항목 안**에 지정합니다. `python:` 추출 블록은 문서에서 직접 값을 얻는 별도 기능입니다.

```yaml
parser: {type: python, file: response_parser.py, callable: parse}
```

`response_parser.py`는 설정 YAML과 같은 디렉터리 아래에 두며, 다음처럼 문자열을 받아 dict를 반환합니다.
아래 코드는 JSON의 `result` 객체를 꺼내는 최소 예제입니다. 실제 모델 응답 형식에 맞게 구현합니다.

```python
import json

def parse(llm_output, **kwargs):
    value = json.loads(llm_output)
    result = value.get('result', value)
    if not isinstance(result, dict):
        raise ValueError('LLM 출력은 객체여야 합니다.')
    return result
```

### C.8 구현 확인 위치와 현재 제약

아래 경로는 저장소 루트 기준입니다. 주석·예제와 실행 코드가 다르면 실제 호출부와 결과를 먼저 확인합니다.
이 절의 내용은 문서 작성 시 확인한 로컬 소스 기준이며, 배포 서버가 동일한 리비전인지는 `/version`과 배포 이력으로 확인합니다.

| 확인할 내용 | 소스와 진입점 |
|---|---|
| HTTP 입력·응답, 설정 경로, 요청 제한 시간 | `main.py`: `_cfg`, `_run`, `parse`, `parse_upload`, `chunker` |
| facade의 변경 가능한 처리 흐름 | `genon/preprocessor/facade/parser_processor.py`, `chunking_processor.py` |
| 파서 훅의 실제 인자·호출 위치 | `processing/core/parser.py`: `_hook_edit_input`, `_call_edit_document`, `_load_json_payload`, `_hook_tabular_sheets` 등 |
| 청커 문서 유형·설정 덮어쓰기 | `processing/core/chunker.py`: `_start_chunk_job`, `_apply_config_overlay` |
| 훅 kwargs·반환값 처리 | `processing/common/hooks.py`: `hook_kwargs`, `call_hook`, `call_chunk_hook` |
| job 필드·임시 파일 | `processing/common/job.py`: `ParseJob`, `ChunkJob` |
| 점 표기 설정 이름 | `processing/common/config_parse.py`: `CONFIG_PATH_ALIASES`, `resolve_overlay_key` |
| v2 설정과 유형별 허용 키 | `processing/enrichment/config_v2.py`, `config_schema.py` |
| 값 변환과 인자 기본값 | `processing/enrichment/field_transforms.py` |
| 청크 메타데이터 전달·통계 보정 | `processing/core/toolbox.py`, `processing/common/vector_meta.py` |

`processing/`로 시작하는 경로에는 앞에 `genon/preprocessor/`를 붙입니다. 확인용 위치이며 수정 허용 범위를 넓히는 뜻은 아닙니다.

현재 구현에서 주의할 점은 다음과 같습니다.

- 일반 훅에는 `job`이 자동 전달되지 않습니다. `kwargs["job"]`을 사용하는 예제를 그대로 실행하지 않습니다.
- `job.config`는 전체 설정이 아닙니다. 초기화 YAML, 요청 params, 덮어쓴 값을 함께 확인합니다.
- CLI와 HTTP 서버의 기본 설정 탐색 경로가 다릅니다. 비교 실행 시 `--config`를 명시합니다.
- `request_deadline`은 이벤트 루프를 차단하는 동기 작업까지 즉시 강제 종료하는 장치가 아닙니다.
- 청크 순번은 0부터 시작하며 좌표·미디어 미제공 표식 `"."`은 JSON으로 해석하지 않습니다.

---

※ 이 문서는 모니모 환경 전용입니다. 코드서빙이 고정되어 있고 Bitbucket 브랜치와 전용 CI/CD 로
배포하는 환경을 전제로 작성했습니다.
