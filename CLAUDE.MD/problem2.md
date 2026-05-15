# pytest 설정 구조 문제

## 상황

루트의 `pytest.ini`와 CI(PR)에서 실제로 돌아가는 테스트 경로가 다르다.

## 루트 pytest.ini

```ini
[pytest]
testpaths = tests_our
```

- 루트에서 `pytest`를 실행하면 `tests_our/` 폴더를 바라봄
- 현재 작업 중인 테스트 파일(`genon/preprocessor/tests/`)과 **무관**

## CI (PR → develop)

`.github/workflows/develop.yml` 91번 줄:

```bash
uv run pytest tests -m "unit or smoke or regression" -v
```

- `working-directory: genon/preprocessor` 기준으로 실행
- `genon/preprocessor/tests/` 폴더 전체를 대상으로 함
- 루트 `pytest.ini`는 CI에서 **참조되지 않음**

## 결론

- 루트 `pytest.ini`는 실질적으로 CI와 무관한 파일
- 새 테스트 파일은 `genon/preprocessor/tests/unit/`에 위치해야 CI에서 자동으로 감지됨
- 로컬에서 CI와 동일하게 실행하려면:

```bash
cd genon/preprocessor
uv run pytest tests -m "unit or smoke or regression" -v
```
