# Regression 테스트

문서 처리 결과를 baseline과 비교하여 의도하지 않은 변경을 감지합니다.

## 테스트 실행

```bash
# 모든 regression 테스트
pytest -m regression

# PDF만 테스트
pytest tests_our/regression/test_pdf_regression.py

# DOCX만 테스트
pytest tests_our/regression/test_docx_regression.py

# MD만 테스트
pytest tests_our/regression/test_md_regression.py
```

## Baseline 업데이트

코드 변경으로 인한 예상된 출력 변화가 있을 때만 업데이트합니다.

```bash
# 모든 baseline 업데이트
pytest -m update_baseline

# PDF baseline만 업데이트
pytest -m update_baseline -k test_update_pdf

# DOCX baseline만 업데이트
pytest -m update_baseline -k test_update_docx

# MD baseline만 업데이트
pytest -m update_baseline -k test_update_md
```

## 테스트 파일

- 위치: `sample_files/regression_test/`
- 지원: PDF, DOCX, MD 등

## Baseline 관리

- 저장 위치: `tests_our/regression/baselines/`
- **Git에 커밋됨** - 버전 간 변경 추적용
- 형식: JSON (text 샘플 + 메타데이터)

## 파일 구조

```
tests_our/regression/
├── test_pdf_regression.py    # PDF 테스트
├── test_docx_regression.py   # DOCX 테스트
├── test_md_regression.py     # MD 테스트
├── baselines/               # Baseline 데이터 (커밋됨)
│   ├── pdf_*.json
│   ├── docx_*.json
│   └── md_*.json
└── README.md
```