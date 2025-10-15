# Regression 테스트 가이드

## 📋 개요

Regression 테스트는 코드 변경 후 기존 기능이 제대로 동작하는지 확인하는 테스트입니다.
현재 코드의 출력 결과를 baseline과 비교하여 예상치 못한 변경사항을 감지합니다.

## 📁 디렉토리 구조

```
tests_our/regression/
├── baselines/          # 기준이 되는 baseline 데이터
│   ├── md_sample.json
│   ├── pdf_sample.json
│   └── ...
├── rebase/            # pytest -m rebase로 생성된 현재 코드 결과
│   ├── md_sample.json
│   ├── pdf_sample.json
│   └── ...
├── test_md_regression.py
├── test_pdf_regression.py
├── test_docx_regression.py
├── test_pptx_regression.py
├── test_hwpx_regression.py
└── README.md
```

## 🚀 사용법

### 1. 기본 Regression 테스트 실행

```bash
# 모든 regression 테스트 실행
poetry run pytest tests_our/regression/ -v

# 특정 파일 형식만 테스트
poetry run pytest tests_our/regression/test_md_regression.py -v
poetry run pytest tests_our/regression/test_pdf_regression.py -v

# 특정 파일만 테스트
poetry run pytest tests_our/regression/test_md_regression.py::test_md_regression[md_sample] -v
```

### 2. Baseline 업데이트

코드 변경이 의도된 것이고, baseline을 업데이트하고 싶을 때:

```bash
# 모든 baseline 업데이트
poetry run pytest -m update_baseline tests_our/regression/ -v

# 특정 형식만 업데이트
poetry run pytest -m update_baseline tests_our/regression/test_md_regression.py -v
```

### 3. **Rebase: 현재 코드 결과 저장** ✨

**`pytest -m rebase` 명령어를 사용하면 현재 코드의 출력 결과를 `rebase/` 폴더에 저장합니다.**

이를 통해 baseline과 현재 코드의 차이를 쉽게 비교할 수 있습니다.

```bash
# 현재 코드 결과를 rebase 폴더에 저장
poetry run pytest -m rebase tests_our/regression/ -v

# MD 파일만 rebase 생성
poetry run pytest -m rebase tests_our/regression/test_md_regression.py -v

# 특정 형식 조합
poetry run pytest -m rebase tests_our/regression/test_md_regression.py tests_our/regression/test_pdf_regression.py -v
```

**생성된 파일 위치:**
- `tests_our/regression/rebase/md_sample.json`
- `tests_our/regression/rebase/pdf_sample.json`
- 등등...

**활용 방법:**

```bash
# baseline과 rebase 비교
diff tests_our/regression/baselines/md_sample.json tests_our/regression/rebase/md_sample.json

# VSCode에서 비교
code --diff tests_our/regression/baselines/md_sample.json tests_our/regression/rebase/md_sample.json

# jq로 label_distribution만 비교
echo "Baseline:"
cat tests_our/regression/baselines/md_sample.json | jq '.label_distribution'
echo ""
echo "Rebase:"
cat tests_our/regression/rebase/md_sample.json | jq '.label_distribution'
```

## 📊 테스트 항목

각 regression 테스트는 다음 항목들을 검증합니다:

1. **Vector Count**: 생성된 벡터 개수
2. **Label Distribution**: 각 라벨 타입의 개수 분포
3. **Character Count**: 전체 문자 수 (5% 이내 허용)
4. **Text Similarity**: 각 벡터의 텍스트 유사도 (85% 이상)

## 🔍 테스트 실패 시 대응 방법

### 1. 의도된 변경인 경우

```bash
# baseline 업데이트
poetry run pytest -m update_baseline tests_our/regression/ -v
```

### 2. 의도되지 않은 변경인 경우

```bash
# 1. rebase 파일 생성하여 차이 확인
poetry run pytest -m rebase tests_our/regression/ -v

# 2. baseline과 rebase 비교
diff tests_our/regression/baselines/md_sample.json \
     tests_our/regression/rebase/md_sample.json

# 3. 코드 수정 후 다시 테스트
poetry run pytest tests_our/regression/ -v
```

## 📝 워크플로우 예시

### 코드 수정 후 검증

```bash
# 1. 코드 수정
vim doc_preprocessors/basic_processor.py

# 2. regression 테스트 실행
poetry run pytest tests_our/regression/ -v

# 3. 실패한 경우, rebase 생성하여 차이 확인
poetry run pytest -m rebase tests_our/regression/ -v

# 4. baseline과 rebase 비교
diff tests_our/regression/baselines/md_sample.json \
     tests_our/regression/rebase/md_sample.json

# 5-A. 의도된 변경: baseline 업데이트
poetry run pytest -m update_baseline tests_our/regression/ -v

# 5-B. 의도되지 않은 변경: 코드 수정 후 2번부터 반복
```

## ⚙️ pytest 마커 (Markers)

| 마커 | 설명 | 사용 예 |
|------|------|---------|
| `regression` | 일반 regression 테스트 | `pytest -m regression` |
| `update_baseline` | baseline 업데이트 | `pytest -m update_baseline` |
| `rebase` | 현재 코드 결과를 rebase 폴더에 저장 | `pytest -m rebase` |

## 🎯 비교 예시

### label_distribution만 비교

```bash
# MD 파일 label 비교
poetry run pytest -m rebase tests_our/regression/test_md_regression.py -v

echo "=== Baseline ==="
cat tests_our/regression/baselines/md_sample.json | jq '.label_distribution'

echo ""
echo "=== Rebase ==="
cat tests_our/regression/rebase/md_sample.json | jq '.label_distribution'
```

### 모든 baseline과 rebase 비교

```bash
# rebase 생성
poetry run pytest -m rebase tests_our/regression/ -v

# 각 파일별 diff
for file in tests_our/regression/baselines/*.json; do
    basename=$(basename $file)
    echo "=== $basename ==="
    diff <(cat $file | jq -S '.label_distribution') \
         <(cat tests_our/regression/rebase/$basename | jq -S '.label_distribution') || true
    echo ""
done
```

## 🛠️ 문제 해결

### Q: baseline이 없다는 오류가 나옵니다

```bash
# baseline 생성
poetry run pytest -m update_baseline tests_our/regression/ -v
```

### Q: 테스트가 실패하는데 어떤 부분이 틀렸는지 모르겠습니다

```bash
# 1. rebase 파일 생성
poetry run pytest -m rebase tests_our/regression/ -v

# 2. JSON diff로 상세 비교
diff -u tests_our/regression/baselines/md_sample.json \
        tests_our/regression/rebase/md_sample.json

# 3. label_distribution만 비교
diff <(cat tests_our/regression/baselines/md_sample.json | jq '.label_distribution') \
     <(cat tests_our/regression/rebase/md_sample.json | jq '.label_distribution')
```

### Q: baseline과 rebase를 시각적으로 비교하고 싶습니다

```bash
# VSCode에서 비교
code --diff tests_our/regression/baselines/md_sample.json \
             tests_our/regression/rebase/md_sample.json

# 또는 git diff 사용
git diff --no-index tests_our/regression/baselines/md_sample.json \
                    tests_our/regression/rebase/md_sample.json
```

## 💡 팁

1. **rebase는 테스트를 실패시키지 않습니다**: `pytest -m rebase`는 단순히 현재 결과를 저장만 하므로 항상 성공합니다.
2. **baseline과 rebase를 같이 사용**: 코드 수정 후 `pytest -m rebase`로 결과를 저장하고, 차이를 확인한 뒤 의도된 변경이면 `pytest -m update_baseline`으로 업데이트합니다.
3. **여러 마커 조합**: `pytest -m "regression and not pdf"` 등으로 특정 테스트만 실행할 수 있습니다.

## 🔬 Soft Assertions (여러 실패 항목 동시 확인)

기본적으로 pytest는 첫 번째 assertion 실패 시 테스트를 중단합니다.
하지만 regression 테스트에서는 **모든 실패 항목을 한번에 확인**할 수 있도록 soft assertion 패턴을 사용합니다.

### 동작 방식

각 테스트는 다음 4가지 항목을 모두 체크하고, 실패한 항목들을 한번에 보고합니다:

1. **Vector Count**: 벡터 개수 일치 여부
2. **Label Distribution**: 라벨 분포 일치 여부
3. **Character Count**: 문자 수 차이 (5% 이내)
4. **Text Similarity**: 각 벡터의 텍스트 유사도 (85% 이상, 처음 5개만)

### 예시 출력

```
================================================================================
[md_sample.md] Regression test failed with 3 error(s):
================================================================================

1. [Vector Count] 35 != 32

2. [Label Distribution]
  Current:  {'title': 1, 'text': 22, 'section_header': 8, 'list_item': 26, 'code': 8}
  Baseline: {'title': 1, 'text': 27, 'section_header': 8, 'list_item': 21, 'code': 8}

3. [Text Similarity] Low similarity detected:
  Vector 0: 79.23%
  Vector 2: 82.45%
  Vector 5: 80.11%

================================================================================
```

이렇게 하면 **한 번의 테스트 실행으로 모든 문제를 파악**할 수 있어 디버깅 시간이 크게 단축됩니다.

### 제한사항

- **Text Similarity**: 너무 많은 벡터가 실패할 경우 처음 5개만 표시하고 나머지는 개수만 표시합니다.
  - 예: `... (and 25 more)` - 추가로 25개 벡터의 유사도가 낮음

