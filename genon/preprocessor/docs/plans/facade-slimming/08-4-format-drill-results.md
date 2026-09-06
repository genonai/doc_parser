# 08-4. 신규 문서 드릴 — xlsx / md / html 실행 결과 (2026-09-06)

06 은 JSON 15종으로 "고객이 새 문서를 스스로 처리할 수 있는가" 를 물었다. 08-4 는 같은
질문을 나머지 세 포맷에 묻는다. **훅이 실제로 값을 하는지** 를 재는 것이 목적이라
custom_fields 설정을 쓰지 않는다.

## 재현

```bash
# 실행 위치: genon/preprocessor
.venv/bin/python examples/parse_chunk/drill/make_format_fixtures.py   # 픽스처 7개
.venv/bin/python examples/parse_chunk/drill/run_format_drill.py --no-hook   # 1단계
.venv/bin/python examples/parse_chunk/drill/run_format_drill.py            # 2단계(훅)
```

훅은 전부 doc_type 게이팅이라 `--no-hook`(doc_type 미전달)이 곧 "그대로 넣었을 때" 다.

## 판정

| | 1단계 (그대로) | 2단계 (훅) |
|---|---|---|
| 통과 | **2 / 7** | **7 / 7** |
| 고객 코드 | 0줄 | **24줄** |

## 픽스처별

| 키 | 변형 | 1단계 | 훅 | 줄수 |
|---|---|---|---|---:|
| x1 | 상단 2행이 로고·안내문, 3행이 진짜 헤더 | 로고가 청크에 섞임 | 격자 앞 2행 제거 | 1 |
| x2 | 값만 정규화(하이픈) + **진짜 병합 헤더** | 하이픈 그대로 | `tb.regex_sub` 로 셀 정규화 | 3 |
| x3 | 시트 2개 중 '본문' 만 | **그대로 통과** | 불필요 | 0 |
| m1 | h태그 없이 `■ ▶` 마커로만 계층 | 1청크(섹션 미분리) | `tb.promote_markdown_marker_headings` | 2 |
| m2 | 레이아웃 보존 ```text 펜스 | 문장이 줄바꿈으로 끊김 | `tb.unfence_text` | 2 |
| h1 | iframe srcdoc 속성 안 본문 | **그대로 통과**(flatten auto) | 불필요 | 0 |
| h2 | `display:none` 아코디언 안 답변 | 본문 소실 | 숨김 표시 제거 | 2 |

나머지는 import 3줄이다. **doc_type 당 최대 3줄**로 합격 기준(30줄)에 크게 못 미친다.

### x2 가 확인한 것 — 병합 유지 규칙(D6)이 실전에서 동작한다

값만 바꾸고 행·열 개수를 그대로 두면 병합 좌표가 유효하므로 유지된다. 실제로 훅을 태운
뒤에도 컬럼명이 `연락처_전화` 로 나왔다. "무조건 버린다" 였으면 `전화` 가 됐을 것이다.

```python
        if doc_type == "drill_x2":
            return {name: [[tb.regex_sub(c, pattern=r"(?<=\d)-(?=\d)", repl="") for c in row]
                           for row in rows] for name, rows in data.items()}
```

### h2 가 확인한 것 — docling 의 숨김 억제를 훅이 우회한다

docling 은 `display:none` 컨테이너를 버린다(기존 조사와 일치). `flatten` 은 `.html` 원본에
auto 로 걸리지만 이 케이스는 걸리지 않았다. 훅에서 표시만 떼면 본문이 살아난다.

## 드릴 자체에서 배운 것

**"본문에 글자가 있는가" 는 약한 단정이다.** 마커 승격은 글자를 바꾸지 않으므로 텍스트
검사로는 승격 전후가 구분되지 않는다. 처음엔 m1·m2 가 1단계에서 통과했다가, 단정을
**청크 수**와 **문장 연결**로 바꾸고 나서야 실제 신호가 잡혔다(m1: 1청크 → 3청크).

**`sheets_to_xlsx` 는 병합을 못 만든다.** 문서화한 한계인데 픽스처 생성기에서 그대로
드러났다 — 격자로 쓴 x2 는 헤더가 중복으로 보여 기동에 실패했다. openpyxl 로 진짜
병합셀을 만들어야 했다.

## JSON 재실행 (06 대비)

같은 픽스처 15종을 리팩터링본에서 다시 돌렸다.

| | 06 (착수 전) | 08-4 (현재) |
|---|---|---|
| 설정/코드 없이 통과 | 8종 | **13종** |
| 남은 것 | 코드 4 / 경계 3 | b12(JSONL) · b15 |

**`b11`(UTF-8 BOM · CP949)이 고객 코드 0줄이 됐다.** 06 에서 9줄이던 것을 core 의
인코딩 폴백이 흡수했다. `b12`(JSONL)는 훅 3줄로 되며 단위 테스트가 그것을 고정하고 있다
(`test_broken_json_reaches_the_hook_as_raw_text`). `b15`(빈 레코드 배열에서 청커 예외)는
06 이 이미 별도 이슈로 분리한 사전 결함이다.

## 결론

**포맷 4종 전부에서 "설정으로 안 되는 원천을 고객이 facade 한 파일로 처리" 가 성립한다.**
줄 수는 doc_type 당 1~3줄이고, 필요한 도구는 전부 toolbox 에 있었다(신규 함수 0).
