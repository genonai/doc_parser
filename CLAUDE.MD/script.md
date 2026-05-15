# 실행 스크립트 모음

> 모든 명령어는 프로젝트 루트(`doc_parser/`)에서 실행

## 공통

```bash
PYTHON=.venv-cu126/bin/python3
```

---

## 스모크 테스트

### 전체 스모크 (hwp·오디오·엑셀 제외)

```bash
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/ -v
```

### 확장자별

```bash
# PDF
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_pdf_smoke.py -v

# DOCX
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_docx_smoke.py -v

# PPTX
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_pptx_smoke.py -v

# Markdown
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_md_smoke.py -v

# HWPX
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_hwpx_smoke.py -v

# HWP (제외 권장)
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/smoke/test_hwp_smoke.py -v
```

---

## 유닛 테스트

```bash
# 전체 유닛
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/unit/ -v

# intelligent_processor 유닛
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/unit/test_intelligent_processor_unit.py -v
```

---

## 리그레션 테스트

```bash
# 전체 리그레션
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/regression/ -v

# 베이스라인 업데이트 (변경 시에만)
.venv-cu126/bin/python3 -m pytest genon/preprocessor/tests/regression/ -m update_baseline -v
```

