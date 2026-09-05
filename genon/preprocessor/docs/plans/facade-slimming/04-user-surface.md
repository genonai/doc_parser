# 04. 고객이 고칠 자리 확정

전제: [01](01-chunking-dead-code.md)~[03](03-parser-decompose.md) 완료. 줄어든 파일 위에서 한다.

**이 단계는 구조 정리와 문서화만 한다. 새 기능·새 설정 키를 만들지 않는다.**

## 판정 기준

노출과 은닉을 감으로 가르지 않는다. 기준은 하나다 — **사이트마다 실제로 달라지는가.**

| 노출(facade 에 남김) | 은닉(공용 모듈로) |
|---|---|
| `GenOSVectorMeta` 필드 = 적재 DB 컬럼 | 파이프라인 배선, 컨버터 생성 |
| `GenosSmartChunker` ClassVar (표 설명 모드, 그림 annotation) | OCR 옵션, 글리프 검사 |
| 헤더 구분자 `_CHUNK_HEADER_SEP` / `_CHUNK_PATH_SEP` / `_CHUNK_PATH_MAX_LEAVES` | 직렬화 |
| `_MIN_CHUNK_SIZE`, 토크나이저 기본 경로 | 설정 빌더 래퍼 |
| 포맷별 라우팅, `__call__` | 로더 구현 |

`chunking_processor.py` 의 `GenosSmartChunker` 블록이 이미 이 형태의 선례다 —
본체는 `chunking/smart_chunker.py` 에 있고 facade 에는 ClassVar 4개만 있다.

## 할 일

### 1. 조정 지점을 파일 상단 한 블록으로

지금은 조정 대상 상수가 파일 여기저기 흩어져 있다. 상단에 모으고 경계를 주석으로 명시한다.
그 아래는 "고칠 일 없는 배관" 이라는 신호가 된다.

값은 바꾸지 않는다. 위치만 옮긴다.

### 2. `__call__` 라우팅을 읽을 수 있게

`parser_processor.__call__` 은 확장자별 `if` 사슬이다(`.wav/.mp3/.m4a` → `.csv/.xlsx/.xlsm`
→ `.hwp/.hwpx/.hml` → `.docx` → `.pdf/.html/.htm/.md` → 캐치올).
**고객이 새 문서 유형을 처리하려고 코드를 넣을 자리가 바로 여기다.**
지금은 사슬 중간을 다 읽어야 어디에 끼워 넣을지 알 수 있다.

`{확장자: 핸들러}` 표로 바꾸면 어디에 무엇을 추가해야 하는지가 한눈에 보인다.
**동작은 동일하다** — 분기 조건과 순서를 그대로 표로 옮기는 것이다.

주의: 순서에 의미가 있는 곳이 있다. `.md` 는 `formats.md.processing_mode` 에 따라
docling 분기와 캐치올로 갈리고, `.csv/.xlsx` 는 `tabular_mapping` 매칭 여부가 먼저다.
표로 옮길 때 **조건부 분기를 잃지 않는 것**이 이 작업의 유일한 위험이다.
골든 대조가 이것을 잡는다.

**이 변경은 선택이 아니다.** 05 와 06 이 "04 가 정한 고칠 자리" 에 의존하므로,
건너뛰면 고칠 자리를 정의하는 산출물이 문서 한 절만 남는다.

다만 **`{확장자: 핸들러}` dict 로는 표현할 수 없다.** `__call__`(2306-2495) 실측:

- 최상위 분기 8개, 중첩 조건 10개
- **폴스루 2곳** — `.json` 이 두 모드 다 미매칭이면 `return` 없이 `.ppt` 검사를 거쳐 캐치올로 흐르고,
  `.md` 도 `processing_mode=text` 면 캐치올로 빠진다
- 분기마다 다른 postlude — `.docx` 만 `clear_coordinates=True`, `.md` 는 `kwargs` 재대입
- `enrichment_context` 가변 dict 를 8개 분기가 공유

정직한 형태는 **순서 있는 `(조건, 핸들러)` 목록**이고, 핸들러가 `None` 을 돌려주면 다음 후보로
넘어가는 구조다. 그것이 폴스루를 잃지 않고 표처럼 읽히는 유일한 형태다.

**`_load_json_payload` 시그니처 조정을 이 단계에 포함한다.** 05 가 그것을 정식 정규화 훅으로
지목하는데 현재 `@staticmethod(file_path)` 라 `doc_type` 을 못 받는다. 게이팅 없는 정규화는
모든 JSON doc_type 의 산출을 바꾼다.

### 3. 설정 프로필 정리

`resource/*_config_simple.yaml` 이 이미 초보자 표면으로 존재한다
(parser 220줄 / 6섹션 vs 정식 413줄 / 9섹션). 이번에 바뀐 노출 표면과 맞춘다.
**새 키를 넣지 않는다** — 기존 키의 배치와 주석만 정리한다.

### 4. 문서

검증에서 이 절이 대상을 잘못 짚은 것이 드러났다.

- **고객이 코드를 고칠 때 실제로 보는 문서는 `gitbook_doc/code_serving_dev_manual.md`(2,412줄)** 다.
  7장 전체가 "코드 수정 가이드" 인데 계획 9개 문서에 이 파일 이름이 **0회** 등장했다.
- **`gitbook_doc/chunking_processor.md` 는 존재하지 않는다.** 프로세서 문서는 attachment·convert·
  intelligent·parser 4종뿐이고 **5종 중 청킹만 레퍼런스가 없다.** 신규 생성이 이 단계의 작업이다.
- `facade/README.md` 는 존재하지 않는 디렉터리를 설명하는 낡은 문서다. 삭제하고 gitbook_doc 로 일원화.

**dev manual 의 줄번호 표는 이미 전면 무효다.** 실측 대조:

| 문서 주장 | 실제 |
|---|---|
| `chunking_processor.py` 2,900줄, `GenosSmartChunker` 342-1376 | 파일 1,536줄, 클래스는 **243** 이고 본체가 아니라 얇은 서브클래스 |
| `_is_section_header(827)` 를 `chunking_processor.py` 에서 고쳐라 | 그 함수는 `facade/chunking/smart_chunker.py:896` — **파일 지목 자체가 틀렸다** |
| `parser_processor.py` 2,700줄, `DocumentProcessor` 1858- | 2,497줄, 클래스는 **896** |
| 각 facade 는 "2,600~3,700줄" | 5개 중 4개가 2,000줄 미만 |

01~03 은 이 상태를 더 악화시킨다. 그러므로 **줄번호 표를 전부 폐기하고 함수·클래스 이름 +
파일 경로로 대체한다.** 좌표 기반 문서는 구조적으로 유지 불가다.

메시지 통일도 필요하다 — `installation.md:338`("AI Search 팀에 문의")과 `:342`("직접 수정")과
dev manual("이대로 고쳐라")이 서로 충돌한다.

## 검증

표면 정리는 동작 변경이 아니다. A/B 대조나 골든 대조에서 차이가 나오면
그것은 정리가 아니라 회귀다.

```bash
examples/parse_chunk/parse_chunk_ab.py          # 임시본 대조
examples/parse_chunk/parse_chunk_golden.py --check
examples/config_precheck/precheck_custom_fields.sh
```
