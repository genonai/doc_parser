# 06. 청크 텍스트 패턴 후처리

전제: [README.md](README.md) 의 "공통 규칙" 을 먼저 읽는다.
**이 작업은 v2 스키마와 무관하다.** 다른 5개와 겹치는 파일이 없어 언제든 병행 가능하다.

## 보고된 요구

> 청크텍스트 후처리 기능. RAG 검색에 불필요한 문자, 패턴을 이용해서 삭제할 수 있는 기능.
> 청킹단계에서 후처리하는 것을 검토해줘.

## 현재 동작

`facade/chunking/text_norm.py` 가 이미 "결정적 후처리" 자리를 잡고 있다. 두 단계다.

| 함수 | 하는 일 | 적용 지점 |
|---|---|---|
| `sanitize` | 문자 위생(NFC, 제로폭·제어문자 제거, 특수공백, 전각→반각, 따옴표·대시 통일) | **청킹 입력** |
| `tidy` | 표현 정리(줄끝 공백, 연속 빈 줄, 앞뒤 공백) | 가드레일 마스킹 뒤, 벡터 생성 직전 |

설정은 `chunking.text_cleanup` 하나(`off` 기본 / `safe`)이고, 활성 processor 3종이 읽는다
(`chunking_processor.py:445`, `intelligent_processor.py:442`, `convert_processor.py:420`).

**패턴 기반 삭제 수단은 없다.**

## 바꿀 것

새 최상위 개념을 만들지 않고 `chunking.text_cleanup` 을 스칼라에서 블록으로 확장한다.

```yaml
chunking:
  text_cleanup: safe          # 스칼라 표기는 계속 유효 (하위호환)

chunking:
  text_cleanup:
    mode: safe
    rules:                                        # 순서대로 적용
      - {line: "^\\s*목차\\s*$"}                   # 매칭되는 줄을 지운다
      - {find: "\\[이미지[^\\]]*\\]", replace: ""}  # 부분 치환
      - {chunk: "^본 문서는 참고용"}                # 청크 자체를 버린다
```

키 이름이 곧 액션이라 `action:` 같은 개념을 하나 더 만들지 않는다. 세 액션이면 실무 요구를 덮는다.

### 적용 지점 — 이것이 핵심이다

`line` / `find` 는 **`sanitize`(청킹 입력)와 같은 자리**에 둔다. `tidy`(출력 직전)에 붙이면
청크 경계와 `n_char` 가 이미 확정된 뒤라 삭제량만큼 청크가 작아지고 빈 청크가 남는다.
입력 단계에 두어야 경계 산정(chunk_size/토크나이저)과 표 설명 enrichment 프롬프트가
같은 텍스트를 본다.

`chunk` 만 출력 단계에서 판정하고 기존 `drop_blank_chunks` 를 재사용한다.

### 배선

`mode_from_cfg` 는 모드 문자열만 돌려주므로 계약을 바꾸지 않는다. 대신 규칙용 속성을 하나 더 둔다.

```python
# text_norm.py
def rules_from_cfg(chunking_cfg: dict) -> tuple: ...   # 기동 시 정규식 컴파일
RULES_ATTR = "_text_cleanup_rules"

# 3개 facade __init__ 에 각 1줄
self._text_cleanup_rules = tn.rules_from_cfg(chunking_cfg)
```

`sanitize_document` / `sanitize_elements` / `prepare_document` 는 규칙을
`getattr(owner, "_text_cleanup_rules", ())` 로 읽는다. **`getattr` 기본값이 필수다** —
`object.__new__` 로 `__init__` 을 우회해 만든 인스턴스를 쓰는 단위 테스트가 있다.

## 바꾸지 말 것

- **코드/표 보호를 우회하지 않는다.** `_map_outside_code`(펜스·인라인 백틱)를 그대로 태운다.
  특히 `line` 은 표 안에서 비활성이 기본이어야 한다 — 표 마크다운 한 줄을 지우면 표가 깨진다.
- **내용으로 무엇을 지울지 판정하지 않는다.** 규칙은 설정이 준 정규식만 본다.
  CLAUDE.md 기준 5(설정 한 줄로 되는 일을 코드 판정 로직으로 풀지 않는다).
- `sanitize`/`tidy` 의 기존 치환 테이블을 건드리지 않는다.
- **doc_type 스코프를 지금 넣지 않는다.** 청킹 계층은 doc_type 을 안 보지만
  `kwargs["doc_type"]` 은 이미 넘어오므로, 필요해지면 rule 항목에 `doc_type: [faq]` 한 줄이면 된다.
  요구가 나오기 전에 만들지 않는다.
- 가드레일 마스킹 순서를 건드리지 않는다. 삭제는 마스킹 **앞**(sanitize 자리)이라
  quote 매칭에 영향을 주지 않는다.

## 오류 처리

정규식은 **기동 시** 컴파일해 거른다(`compile_transforms` 선례). 요청 때 터지면 로그만 보고는
어느 설정이 문제인지 알 수 없다. 알 수 없는 액션 키도 기동 시 거부하고 가장 가까운 이름을 제안한다.

## 영향 파일

- `facade/chunking/text_norm.py` (`rules_from_cfg`, 규칙 적용, `__all__`)
- `facade/chunking_processor.py:445`, `intelligent_processor.py:442`, `convert_processor.py:420`
  (각 1줄)
- `resource/chunking_processor_config.yaml` + `intelligent`·`convert` + `resource_dev/` 사본
  (주석과 예시. 기본은 규칙 없음)

## 검증

### 테스트 데이터 (없으면 만든다)

| 용도 | 파일 | 상태 |
|---|---|---|
| 표·코드가 섞인 실문서(보호 확인) | `genon/preprocessor/sample_files/monimo/monimo_cs_hpp_large_table_sample.html` | 있음 |
| 마크다운 실문서 | `genon/preprocessor/sample_files/md_sample.md` | 있음 |
| 일반 PDF(무변화 확인) | `genon/preprocessor/sample_files/pdf_sample.pdf` | 있음 |
| 삭제 대상 노이즈가 든 문서 | `genon/preprocessor/sample_files/text_cleanup_noise_sample.md` | **만들어야 함** |

마지막 1건은 만든다. 넣을 것: 단독 `목차` 줄, `[이미지1]` 류 자리표시자,
"본 문서는 참고용" 으로 시작하는 통째로 버릴 문단, 그리고 **삭제되면 안 되는 대조군** —
표 안에 `목차` 라는 셀, 코드 펜스 안의 `[이미지1]` 문자열.
`examples/parse_chunk/make_text_cleanup_noise_sample.py` 로 생성한다.

대조군이 핵심이다. 삭제가 되는지보다 **삭제되면 안 되는 것이 남는지**가 이 기능의 위험이다.

`text_cleanup` 기본이 `off` 라 규칙을 넣지 않으면 기존 샘플의 산출은 바뀌지 않는다 —
`pdf_sample.pdf`/`md_sample.md` 는 그 무변화를 확인하는 용도다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'text_cleanup or text_norm'
```

기존 파일 3개가 회귀망이다 — `test_text_norm_unit.py`, `test_text_cleanup_config.py`,
`test_text_cleanup_chunk_paths.py`. 여기에 다음을 추가한다.

- 액션 3종 각각의 단정문
- **규칙이 청크 경계에 반영되는지** — 입력 단계 적용이 목적이므로, 삭제 뒤 청크 수/`n_char` 가
  삭제를 반영해야 한다
- 표·코드 블록 안에서 `line` 이 동작하지 않는지
- 잘못된 정규식이 **기동 시** ValueError 인지
- 스칼라 표기(`text_cleanup: safe`)가 그대로 동작하는지

`text_cleanup` 은 기본이 `off` 라 출고 설정에 규칙을 넣지 않으면 파싱·청킹 결과가 바뀌지 않는다.
`parse_chunk_verify.sh` 는 이 작업의 회귀망이 아니다.

## 재색인

기본값(규칙 없음)이면 영향 없다. 규칙을 실제로 켜면 청크 본문이 바뀌므로 재색인이 필요하다.
