# 샘플 파일 폴더

이 폴더는 테스트용 샘플 파일들을 저장하는 곳입니다.

## 사용법

1. 이 폴더에 처리할 파일을 추가하세요
2. `examples/facade_run/facade_run.py`를 실행하면 해당 파일을 처리합니다

## 지원되는 파일 형식

- PDF (.pdf)
- HWP (.hwp) 
- DOCX (.docx)
- TXT (.txt)
- 기타 DocumentProcessor가 지원하는 형식

## 파일명 규칙

- 기본적으로 `pdf_sample.pdf`를 찾습니다
- 다른 파일명을 사용하려면 `facade_run.py`의 `file_path` 변수를 수정하세요

## HWP → PDF 회귀 검증 자산 (이슈 #199)

본 폴더에 `*.hwp` / `*.hwpx` 파일을 추가하면 `tests/regression/test_hwp_to_pdf_regression.py` 가 가용한 모든 backend (pdf_sdk / rhwp / libreoffice) 로 자동 변환·검증한다. 별도 등록 절차 없음.

검증 범위 확대를 위해 다음 유형의 HWP 샘플을 추가 권장:

- 표 (병합 셀 / 중첩 표 포함)
- 이미지 / 그림 (PNG / WMF / EMF 혼재)
- 다단 (2단·3단)
- 머리말·꼬리말
- 각주·미주
- 폼·필드

각 자산은 `<유형>_<설명>.hwp` 패턴 권장 (예: `table_merged_cells.hwp`, `image_wmf.hwpx`).

## 반입 구조 QA 샘플 (`monimo/pse/`, 이슈 #608)

`monimo/pse/<원천>/<L2>/base/<파일>` 은 운영 반입 규약과 같은 트리다. SCP 코드서빙·코드스페이스의
지식유형 QA 샘플 자리(`…/samples/pse/<원천>/<L2>/base/`)에 그대로 복사해 쓴다.

- `pse/SSS/ADAC/base/sss_rag_adac_20261028_full_001.txt` — AI차트뷰(`stock_insight`) 1종목(6행).
  `monimo_stock_insight_sample.txt` 의 첫 종목 6행과 같은 내용이고, 필드 구분자는
  `custom_field_stock_insight.yaml` 의 `separator` 값 `\x0f|` 다(`od -c` 로 보면 `017 |`).
  구분자가 바뀌면 `tools/parse_chunk/make_stock_insight_sample.py` 로 원본을 다시 만든 뒤 이 파일도 같이 바꾼다.
