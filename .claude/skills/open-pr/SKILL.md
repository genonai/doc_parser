---
name: open-pr
description: 로컬 테스트를 먼저 돌린 뒤 develop 대상 PR 을 만들고 CI 통과까지 지켜본다. "PR 생성", "PR 올려줘", "풀리퀘" 관련 작업일 때 사용한다.
---

# PR 생성

`/open-pr [--draft]` 로 호출한다. 로컬 검증(테스트·설정·범위) → PR 생성 → CI 결과 확인까지가 범위다.

## 절차

### 1. 사전 점검

```bash
git rev-parse --abbrev-ref HEAD
git status --porcelain
git log origin/develop..HEAD --oneline
```

- 현재 브랜치가 `develop` 이면 중단한다
- 미커밋 변경이 있으면 `/wip` 로 먼저 커밋하도록 안내하고 중단한다
- 브랜치명에서 `^[a-z]+/([0-9]+)-` 로 이슈 번호를 뽑는다. 매칭되지 않으면 사용자에게 이슈 번호를 묻는다
- 푸시되지 않은 커밋이 있으면 먼저 푸시한다

### 2. 로컬 테스트 선실행

```bash
cd genon/preprocessor && .venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no
```

실패하면 실패한 테스트 목록만 보고하고 **PR 을 만들지 않고 중단한다**.
`.claude/hooks/pytest-filter.sh` 가 출력에서 PASSED/SKIPPED 줄을 걷어내므로 별도 필터링은 하지 않는다.

### 3. 설정·doc_type 검증

```bash
git diff develop...HEAD --name-only
```

변경 파일에 따라 아래를 실행한다. 해당 파일이 없으면 이 단계를 건너뛴다.

| 변경 파일 | 실행 |
|---|---|
| `genon/preprocessor/resource/`·`resource_dev/` 의 `custom_field_*.yaml` | `precheck_custom_fields.sh`(운영 `resource/` 기본값) 를 먼저 실행하고, `resource_dev/` 를 바꿨으면 그 경로를 인자로 한 번 더 실행한다. 이어서 `parse_chunk_verify.sh --only <doc_type...>` 를 실행한다 |
| `facade/` 의 파싱·청킹 경로, `processing/{core,common,chunking,enrichment}/` | `parse_chunk_verify.sh` 전체 실행 |

- 스크립트 경로는 `genon/preprocessor/examples/config_precheck/`, `genon/preprocessor/examples/parse_chunk/` 이다.
- doc_type 은 파일명 `custom_field_<doc_type>.yaml` 에서 뽑는다. 뽑은 이름을 `parse_chunk_verify.py` 가 알지 못하거나(미지원 이름 오류), 한 yaml 이 여러 등록 블록에 쓰이는지 확실하지 않으면 전체 실행으로 바꾼다. 영향 범위를 확정할 수 없을 때 검증을 좁히지 않는다. `CASES` 에 없는 doc_type 은 전체 실행에도 포함되지 않으므로, precheck 만 거쳤다고 PR 본문에 적는다.
- `parse_chunk_verify` 는 `resource_dev/` 설정으로 실행된다. 운영 `resource/` 만 바꿨다면 precheck 결과가 그 변경의 유일한 검증임을 PR 본문 "검증" 절에 적는다.
- 실패가 있으면 PR 을 만들지 않고 중단한다. LLM 게이트웨이에 접근할 수 없어 실패한 것으로 보이면 회귀와 구분해 보고하고, 계속할지 사용자에게 묻는다.
- 실행 건수·SKIP 건수와 사유·실패를 PR 본문 "검증" 절에 남긴다. SKIP 만 있고 실행 건수가 0 이면 검증을 완료했다고 적지 않는다.

### 4. 수정 범위 점검

브랜치 접두사가 `fix/` 일 때만 실행한다. 다른 유형은 이 단계를 건너뛴다(`scope-check` 의 판정 기준이 "보고된 결함"을 전제로 한다).

```bash
gh issue view <N> --repo genonai/doc_parser --json title,body
git diff develop...HEAD
```

`scope-check` 서브에이전트에 이슈 제목·본문을 증상으로, `develop...HEAD` diff 를 대상으로 넘긴다.
판정이 "과잉"이면 지적 내용을 사용자에게 보여 주고, 덜어낼지 그대로 진행할지 묻는다. 범위를 덜어내는 것은
판단이 필요한 일이므로 자동으로 고치지 않는다. 판정 결과는 PR 본문의 "검증" 절에 한 줄로 남긴다.

### 5. 테스트 범위 점검

```bash
git diff develop...HEAD --stat -- genon/preprocessor/tests
```

테스트 변경이 있으면 `test-scope-check` 서브에이전트에 테스트 diff 와 프로덕션 diff 를 함께 넘겨
과잉 여부를 판정받는다. 판정이 "과잉"이면 지적된 테스트를 합치거나 빼고 다시 커밋한 뒤 진행한다.
판정 결과는 PR 본문의 "검증" 절에 한 줄로 남긴다.

테스트 변경이 없으면 이 단계를 건너뛴다.

### 6. 변경 요약 수집

```bash
git log develop..HEAD --format='%s'
git diff develop...HEAD --stat
```

### 7. PR 본문 작성

`.github/PULL_REQUEST_TEMPLATE.md` 는 docling 업스트림 잔재(영문)이므로 쓰지 않는다.
아래 한국어 구조로 직접 만든다.

```markdown
Resolves #<N>

## 변경 요약
- <한 줄씩. 커밋 목록을 그대로 옮기지 말고 의미 단위로 묶는다>

## 상세
- <파일/모듈별로 무엇을 왜 바꿨는지>

## 검증
- <실행한 명령과 결과. 통과한 테스트 수 등>

## 영향 범위
- <아래 항목 중 해당하는 것만>
```

"영향 범위" 는 이 저장소의 복제 구조 때문에 두는 고정 섹션이다. 다음을 점검해 해당하는 것만 적는다.

- 청킹 파이프라인 사본(활성 3종은 공용 모듈로 통합됐고 BOK 적재용 3종은 여전히 사본) 을 건드렸는가
- `GenosServiceException` 시그니처를 바꿨는가
- 코드서빙 배포본에 포함되는 경로(`genon/`, `main.py`)를 바꿨는가 — 바꿨다면 `sync-serving-repo.sh` 재실행 필요
- `docling/` 을 바꿨는가 — 바꿨다면 wheel 재빌드 필요(핫픽스 overlay 로는 전달되지 않는다)
- 루트 `main.py` 계층 기능을 바꿨다면 `genon/preprocessor/src/main.py` 도 함께 봤는가

해당 없으면 "해당 없음" 한 줄로 적는다.

### 8. 승인 후 생성

제목(한국어)과 본문을 보여주고 승인받은 뒤, 본문을 스크래치패드에 임시 파일로 쓰고 생성한다.

```bash
gh pr create --repo genonai/doc_parser \
  --base develop \
  --title "<제목>" \
  --body-file "<스크래치패드>/pr_body.md"
```

호출 인자에 `--draft` 가 있으면 플래그를 붙인다.

### 9. CI 대기

```bash
gh pr checks --watch --fail-fast
```

`.github/workflows/develop.yml` 이 py3.12/3.13 매트릭스로 도므로 수 분 걸린다.
실패하면 실패 잡의 로그를 받되 **요약만** 보고한다.

```bash
gh run view <run-id> --log-failed
```

로그 전문을 출력하지 않는다. 실패 원인 한두 줄과 해당 테스트/단계만 정리한다.

### 10. 보고

PR URL, CI 결과, 실패가 있으면 원인 요약.
