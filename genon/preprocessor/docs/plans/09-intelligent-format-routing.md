# 09. intelligent 가 원천 포맷을 PDF 로 바꾸지 않고 받게

전제: [README.md](README.md) 와 [07-intelligent-parity.md](07-intelligent-parity.md) 를 먼저 읽는다.
07 로드맵의 **2단계. 네 작업 중 위험이 가장 크다.** 10 의 선행 조건이다.

## 왜 필요한가

`kind: records`/`sections` 와 `source.pre.*` 는 **원천 payload 를 필요로 한다.**
그런데 intelligent 는 그것을 먼저 PDF 로 바꾼다.

```python
# intelligent_processor.py:1418  __call__
if ext not in _XLSX_DIRECT_EXTS and kwargs.get('auto_convert_to_pdf', True) and not _is_pdf(file_path):
    file_path, converted_pdf_path = self._convert_to_pdf(file_path, **kwargs)
```

`.json` 이 PDF 가 된 뒤에는 레코드도 섹션도 복원할 수 없다. **배선을 아무리 추가해도
값이 없다.** 그래서 라우팅이 먼저다.

parser 는 확장자별 네이티브 경로가 먼저이고 PDF 변환이 폴백이다.

## 바꿀 것

`_XLSX_DIRECT_EXTS`(현재 xlsx/xlsm/csv)라는 **하나짜리 예외 목록을 일반화**해서,
"이 요청에서 네이티브로 처리할 포맷인가" 를 판정하는 자리를 만든다.

```python
# 지금 — 확장자 상수 하나가 변환 가드와 디스패치 양쪽을 겸한다
if ext not in _XLSX_DIRECT_EXTS and auto_convert_to_pdf and not _is_pdf(file_path):
    ...convert...
if ext in _XLSX_DIRECT_EXTS:
    return await self._process_xlsx(...)

# 후 — "네이티브로 다룰 이유가 있는가" 를 한 곳에서 판정한다
native = self._native_route_for(file_path, **kwargs)   # None 이면 종전대로 PDF 변환
if native is None and auto_convert_to_pdf and not _is_pdf(file_path):
    ...convert...
if native is not None:
    return await native(request, file_path, **kwargs)
```

판정 기준은 **설정이지 내용이 아니다.** `.json` 이라고 무조건 네이티브로 가면 안 되고,
**그 doc_type 에 그 포맷을 소비하는 custom_fields 설정이 등록돼 있을 때만** 간다.
이유는 두 가지다.

- 지금 `.json`/`.md`/`.html` 을 intelligent 에 넣고 있는 기존 사용자가 있다. 그들에게는
  PDF 변환 결과가 정상 동작이다. 무조건 네이티브로 바꾸면 그 전부가 회귀한다.
- CLAUDE.md 기준 5 — "무조건 처리" 류의 확장은 의도하지 않은 입력까지 끌어온다.
  내용으로 포맷을 판정하지 않는다(`formats.extension_aliases` 가 그 목적의 기구다).

즉 **설정이 요구할 때만 라우팅이 바뀐다.** 설정이 없으면 지금과 100% 같다.
이것이 이 작업의 안전장치이고, 회귀 판정 기준이기도 하다.

## 이 단계의 범위

**라우팅 자리를 만드는 것까지다.** 실제로 `records`/`sections`/`source.pre` 를 배선하는 것은 10 이다.
이 단계가 끝난 시점의 관찰 가능한 변화는 다음 하나여야 한다.

- 해당 설정이 없으면: 동작 무변화
- 해당 설정이 있으면: PDF 변환을 건너뛰고 새 경로로 들어가되, 10 이전에는 **명시적인
  "아직 미지원" 오류**로 끝난다 (무음으로 빈 결과를 내지 않는다)

한 번에 라우팅과 배선을 다 하면, 결과가 달라졌을 때 라우팅 탓인지 배선 탓인지 가릴 수 없다.

## 바꾸지 말 것

- `_convert_to_pdf` 자체와 `auto_convert_to_pdf` kwargs 의미를 바꾸지 않는다.
- `_detect_unsupported_file`(비정상 파일 사전 컷, 이슈 #278)을 우회하지 않는다.
  네이티브 경로도 이 검사를 지난 뒤여야 한다.
- xlsx/csv 경로를 건드리지 않는다. 일반화된 판정이 xlsx 에 대해 지금과 같은 답을 내는지만 본다.
- HWP/PPT 처럼 parser 가 네이티브로 다루는 다른 포맷을 여기서 끌어오지 않는다.
  이번 요구는 custom_fields yaml 에 관한 것이다.
- convert_processor 는 이 단계에서 건드리지 않는다(07 의 "convert 는 어떻게 하나" 참조).

## 영향 파일

- `facade/intelligent_processor.py` (`__call__` 1377~1432)
- 판정 헬퍼는 `facade/common/` 또는 `facade/enrichment/` 에 둔다 — 09 시점에는 intelligent
  한 곳만 쓰지만, convert 가 뒤따를 것이 이미 보이므로 facade 안에 `_resolve_*` 로 두지 않는다
  (CLAUDE.md: facade 마다 헬퍼를 복제하면 그 자체가 새 lockstep 부채)

## 검증

### 테스트 데이터 (실제 문서로 검증한다)

이 작업의 판정은 **"설정이 없으면 무변화"** 이므로, custom_fields 설정이 **없는** 범용 샘플이
주 재료다. 전부 저장소에 있다.

| 확장자 | 무변화 확인용(설정 없음) | 새 경로 확인용(설정 있음) |
|---|---|---|
| `.json` | `genon/preprocessor/sample_files/table.jsonl` 또는 임의 json | `genon/preprocessor/sample_files/monimo/monimo_faq_json_sample.json` |
| `.md` | `genon/preprocessor/sample_files/md_sample.md`, `md_sample2.md` | `genon/preprocessor/sample_files/monimo/monimo_product_slf_sample.md` |
| `.html` | `genon/preprocessor/sample_files/html_sample.html`, `html_tables.html` | `genon/preprocessor/sample_files/monimo/monimo_cs_hpp_sample.html` |
| `.xlsx` | `genon/preprocessor/sample_files/xlsx_sample.xlsx` | `genon/preprocessor/sample_files/monimo/monimo_faq_sample.xlsx` |
| `.pdf` | `genon/preprocessor/sample_files/pdf_sample.pdf` | — |
| 기타(회귀) | `genon/preprocessor/sample_files/docx_sample.docx`, `hwp_sample.hwp`, `pptx_sample.pptx` | — |

**왼쪽 열 전부가 종전과 똑같은 경로(PDF 변환)를 타는지**를 단정문으로 고정한다.
이 작업은 intelligent 의 모든 비-PDF 입력에 영향을 주므로, 확장자별로 한 건씩 실제 파일을
돌려 확인한다. 새로 만들 샘플은 없다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'intelligent or routing or convert_to_pdf or xlsx'
```

**"설정이 없으면 무변화" 를 단정문으로 고정하는 것이 이 작업의 핵심 테스트다.**
`.json`/`.md`/`.html` 입력에 대해 custom_fields 설정이 없을 때 종전과 같은 경로
(PDF 변환)를 타는지 본다.

```bash
genon/preprocessor/examples/code_serving/serving_gateway_test.sh
```

엔드포인트 확인은 새 pytest 를 만들기보다 게이트웨이 스크립트에 모드를 추가한다.

## 재색인

설정이 없는 doc_type 은 무변화다. 새 경로를 켠 doc_type 은 10 까지 끝난 뒤에 평가한다.

## 위험

이 작업은 **intelligent 에 들어오는 모든 비-PDF 입력의 경로 판정을 건드린다.** 결함이 나면
특정 doc_type 이 아니라 그 프로세서 전체에 퍼진다. 적용 전에 CLAUDE.md 기준 4
("영향 범위가 결함 범위보다 넓지 않은가")를 특히 엄격하게 자문하고,
판정을 서브에이전트에게 한 번 검증받는 것을 권한다.
