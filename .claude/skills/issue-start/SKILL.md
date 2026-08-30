---
name: issue-start
description: GitHub 이슈를 등록하고 그 이슈에 연결된 작업 브랜치를 만들어 체크아웃한다. "이슈 등록", "작업 시작", "브랜치 생성" 관련 작업일 때 사용한다.
---

# 작업 시작 (이슈 등록 + 브랜치 생성)

`/issue-start <작업 설명>` 으로 호출한다. 팀 컨벤션에 맞는 이슈를 만들고, 그 이슈에 링크된
`<type>/<이슈번호>-<slug>` 브랜치를 생성해 체크아웃하는 것까지가 이 스킬의 범위다.

저장소 상수: `genonai/doc_parser`, 기본 브랜치 `develop`.

## 절차

### 1. 인증 확인

```bash
gh auth status
```

실패하면 여기서 중단하고 사용자에게 안내한다: 프롬프트에 `! gh auth login` 을 입력해 1회 로그인할 것.
`gh` 인증 없이는 이 스킬의 모든 단계가 동작하지 않는다.

이슈는 `@me`, 즉 **인증된 gh 계정**에 할당된다. `git config user.email` 과 gh 로그인 계정이
다를 수 있으니(현재 환경이 그렇다) assignee 가 의도와 다르면 사용자에게 알린다.

### 2. 유형 판정

작업 설명에서 유형을 하나 고른다. 이 단어가 이슈 type 과 브랜치 prefix 양쪽에 쓰인다.

| 판정 | 브랜치 prefix | 이슈 type |
|---|---|---|
| 기존 동작의 결함 수정 | `bug` | `Bug` |
| 신규 기능 | `feature` | `Feature` |
| 기존 기능의 개선 | `feature` | `Improvement` |
| 그 외 (리팩터링, 문서, 조사, 인프라) | `task` | `Task` |

`fix`, `bugfix`, `hotfix` 도 저장소에 전례가 있으나 새로 만들 때는 위 3종 prefix 로 통일한다.

이슈 type 은 `genonai` 조직에 정의된 것만 쓸 수 있다. 현재 `Task`, `Bug`, `Feature`, `Improvement`
4종이며, 확인하려면 `gh api orgs/genonai/issue-types --jq '.[].name'`.

### 3. 이슈 초안 작성

제목은 한국어 한 줄. 본문은 `.github/ISSUE_TEMPLATE/task_report.yaml` 의 구조를 따른다.

```markdown
## Background
- <왜 이 작업이 필요한지. 증상이나 요구의 출처>

## To Do
- <구체적인 작업 항목>

## See Also
- <관련 이슈/PR, 근거가 되는 파일 경로>
```

현재 브랜치에 미커밋 변경이 있으면 `git status --porcelain` 결과를 근거로 삼아 See Also 에 요약한다.

slug 는 영문 소문자와 하이픈만 쓴다(예: `task/358-pptx-pymupdf`). 한글 slug 는 만들지 않는다.
과거 브랜치 중 한글 slug 는 GitHub 웹 UI 가 자동 생성한 것이므로 따라하지 않는다.

### 4. 승인 요청

제목, 본문, 유형, 예정 브랜치명을 사용자에게 보여주고 승인받는다. 승인 전에는 이슈를 만들지 않는다.

### 5. 이슈 생성

본문은 스크래치패드 디렉터리에 임시 파일로 쓴 뒤 넘긴다.

```bash
gh issue create --repo genonai/doc_parser \
  --title "<제목>" \
  --body-file "<스크래치패드>/issue_body.md" \
  --assignee "@me"
```

출력된 URL 에서 이슈 번호 N 을 뽑는다.

### 6. 이슈 type 설정

`gh issue create` 에는 `--type` 플래그가 없으므로(2.86 기준) REST 로 따로 붙인다. 검증 완료된 경로다.

```bash
gh api -X PATCH repos/genonai/doc_parser/issues/<N> -f type=<Task|Bug|Feature|Improvement>
```

실패해도 흐름을 멈추지 않는다. 경고만 남기고 다음으로 넘어간 뒤, 마지막 보고에
"이슈 type 은 웹에서 수동 설정 필요" 로 적는다.

프로젝트 보드는 붙이지 않는다. 이슈 템플릿의 `projects: mindsandcompany/3` 은 현재 계정에서
접근되지 않는 조직을 가리키고(실제 이슈의 `projectItems` 도 비어 있다), `gh project` 계열은
기본 토큰에 없는 `read:project`/`project` 스코프를 요구한다. 프로젝트 연동이 필요해지면
`gh auth refresh -s project` 로 스코프를 늘린 뒤 이 단계를 다시 넣는다.

### 7. 브랜치 생성

```bash
gh issue develop <N> --repo genonai/doc_parser \
  --base develop \
  --name "<type>/<N>-<slug>" \
  --checkout
```

`gh issue develop` 는 브랜치를 이슈에 링크하므로 머지 시 이슈가 자동으로 닫힌다.

미커밋 변경이 있으면 체크아웃 전에 사용자에게 알린다. stash 나 checkout 으로 작업 트리를
임의로 되돌리지 않는다.

### 8. 보고

이슈 번호와 URL, 생성된 브랜치명, 6단계에서 실패한 항목이 있으면 그 사실을 보고한다.

결과 확인이 필요하면 아래를 쓴다. `gh issue view --json` 에는 `issueType` 필드가 없으므로
type 확인은 REST 로 한다.

```bash
gh issue view <N> --repo genonai/doc_parser --json number,title,assignees
gh api repos/genonai/doc_parser/issues/<N> --jq '.type.name'
gh issue develop --list <N> --repo genonai/doc_parser
```
