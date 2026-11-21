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

## 전처리 Studio 문서유형 추가 시연·테스트용 `custom_field_*.yaml` (`studio/`, 이슈 #754)

Studio 에서 문서유형을 추가하면 `doc-types add --from <다른 문서유형>` 이 그 문서유형의 `custom_field_*.yaml` 을
복사해 `activities/parse/custom_field_<doc_type>.yaml` 을 만든다. 복사된 내용은 다른 문서유형의 것이라, 시연·테스트에서는
그 파일을 여기 있는 완성본으로 덮어쓴다. 이 폴더는 서빙본에 함께 들어가므로(`sample_files/monimo` 와 달리) 코드스페이스의
서빙본 체크아웃에서 바로 쓸 수 있다. 워커는 이 폴더를 읽지 않는다.

- `studio/custom_field_music_ai.yaml` — music_ai(모니모 뮤직 AI 플레이리스트). 내용은
  `genon/sites/monimo/resource/custom_field_music_ai.yaml` 에 `doc_type: music_ai` 한 줄을 더한 것이다
  (`test_sites_sync_unit.py` 가 대조). 맨 위 `doc_type:` 줄이 등록이므로 덮어쓴 뒤에도 남아 있어야 한다.

```bash
# 서빙본 저장소 루트에서 — Studio 에서 music_ai 를 추가한 뒤
cp genon/preprocessor/sample_files/studio/custom_field_music_ai.yaml genon/preprocessor/activities/parse/
python -m genon.preprocessor.activities doc-types validate --name music_ai --activity parse
# 다시 시험하려면 Studio 에서 music_ai 를 삭제하거나
python -m genon.preprocessor.activities doc-types remove --name music_ai --activity parse
```
