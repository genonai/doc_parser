# 묶음 1 — 배포본 빌드와 테스트 브랜치 push

doc_parser 소스에서 코드서빙 배포본을 조립해 `genonai/doc_parser_code_serving` 의 테스트 브랜치로 올린다.
배포본 생성 원리와 운영 캐비앗은 `build-script/sync-serving-repo.sh` 상단 주석이 정본이다.

## 만들어지는 것

| 산출물 | 내용 | 플랫폼 의존 |
|---|---|---|
| 배포본 폴더 | `genon/`, `main.py`, `requirements.txt`, `Dockerfile`, README, `VERSION` 스탬프 | 없음(소스 복사) |
| docling wheel | `packages/genon_docling-<ver>+genon.<N>-py3-none-any.whl` | 없음(순수 파이썬, `py3-none-any`) |

Linux 에서만 만들어지는 것(docling-parse 패치, LibreOffice, rhwp, 폰트, 모델)은 base 이미지에 미리 들어 있고
리비전 부팅 때 파드 안에서 설치된다. 그래서 이 묶음은 Mac 과 Linux 어느 쪽에서 해도 같은 바이트가 나온다.

## 준비물

- `git`, `gh`(로그인 상태), `uv`. Mac 이면 `brew install uv`.
- 배포본 저장소 push 권한(`genonai/doc_parser_code_serving`).
- Mac 은 bsdtar 분기가 들어간 스크립트(#492, PR #493 머지 이후의 develop)여야 한다. 이전 스크립트는 간헐적으로
  `[INFO]` 헤더만 찍고 조용히 끝난다.

## 절차

아래 명령은 모두 doc_parser 저장소 루트(예: `/Users/<me>/workspace/doc_parser`)에서 실행한다. 먼저 그 폴더로 이동한다.

```bash
cd /Users/<me>/workspace/doc_parser
```

### 1. 소스를 빌드할 커밋으로 맞춘다

작업 중인 변경이 있으면 먼저 커밋하거나 치워 둔다. 조립은 커밋된 내용(`git archive`)으로 하지만
docling wheel 은 작업 트리 기준이라, 작업 트리가 더러우면 wheel 이 커밋과 어긋난다.

```bash
git status --short
```

대조군(수정 전)은 develop 최신이다.

```bash
git checkout develop && git pull --ff-only origin develop
```

수정본은 검증 대상 브랜치다. 이번 검증은 #481 과 #482 를 합친 `test/481-482-concurrency` 다.

```bash
git checkout test/481-482-concurrency
```

### 2. 배포본 저장소를 한 번만 클론한다

`code-serving/` 은 gitignore 되어 있어 doc_parser 에 섞이지 않는다. 스크립트는 이 폴더의 origin 이
배포본 저장소일 때만 commit 을 만든다(무관한 저장소 보호).

```bash
git clone git@github.com:genonai/doc_parser_code_serving.git code-serving
```

SSH 키가 없으면 `https://github.com/genonai/doc_parser_code_serving.git` 로 클론한다.

### 3. 클론 안에서 테스트 브랜치를 먼저 만든다

스크립트는 클론의 현재 브랜치에 commit 한다. main 에 테스트 commit 이 쌓이지 않도록 브랜치를 먼저 바꾼다.

```bash
git -C code-serving checkout -b test/develop-base
```

수정본을 조립할 때는 `test/481-482` 로 바꾼다. 이미 있는 브랜치면 `checkout` 만 한다.

### 4. 조립한다

wheel 빌드까지 1~2분이다. `GENON_BUILD` 는 wheel 의 local 세그먼트(`+genon.N`)이고, docling 을 고친 빌드는
대조군과 구분되게 올린다(대조군 0, #481 포함 수정본 1).

```bash
PUSH=false GENON_BUILD=0 bash build-script/sync-serving-repo.sh
```

정상이면 마지막에 다음 두 줄이 있다. `[WARN] … dev 빌드로 태깅됩니다` 는 릴리스 태그 커밋이 아니라는 뜻이라 정상이다.

```
[SMOKE] wheel 내용 검증 OK (genon 패치 파일 포함)
[SMOKE] 배포본 청결성 OK — docling 소스 없음, genon/sites 없음, …
```

Mac 에서는 `[INFO] tar=bsdtar — export 를 임시 tar 파일 경유로 수행합니다.` 가 함께 보인다.

### 5. 어느 원본에서 나왔는지 확인한다

`VERSION` 의 `source_commit` 이 1번에서 맞춘 커밋과 같아야 한다.

```bash
cat code-serving/VERSION && git log --oneline -1
```

### 6. 테스트 브랜치로 push 하고 커밋 해시를 적어 둔다

스크립트가 로컬에 붙인 dev 미러 태그(`2.2.4-5NN-g…`)는 push 하지 않는다. 마지막 줄의 해시가 묶음 2 에서
리비전에 들어간다.

```bash
git -C code-serving push origin HEAD:refs/heads/test/develop-base && git -C code-serving rev-parse HEAD
```

## 막혔을 때

| 증상 | 원인과 조치 |
|---|---|
| `[INFO]` 헤더만 찍고 끝남, 종료 코드 141 | bsdtar 분기 이전 스크립트. develop 을 당겨 #492 수정본으로 다시 실행 |
| `tar: Write error` 로 wheel 단계 실패 | 같은 원인. 위와 동일 |
| `[WARN] 로컬 docling/ 이 HEAD 와 다릅니다` | 작업 트리의 docling 이 커밋과 다름. 1번으로 돌아가 정리 |
| `[ERROR] 배포본 Dockerfile 이 코드서빙용이 아닙니다` | 루트 `Dockerfile` 변경을 커밋하지 않음. export 는 커밋 기준이다 |
| `uv: command not found` | `brew install uv` 또는 astral 설치 스크립트 |
| `PUSH=true` 로 실행해 `HEAD -> main` 으로 push 됨 | 스크립트는 클론의 현재 브랜치로 push 한다. 3번(테스트 브랜치 체크아웃)을 건너뛰면 main 에 dev 빌드가 올라간다. `git -C code-serving push origin <커밋>:refs/heads/test/develop-base` 로 브랜치를 만들고, main 은 릴리스 커밋으로 되돌린다 |
| `[ERROR] 태그 … 가 이미 다른 커밋을 가리킵니다` | 앞서 실행한 조립이 로컬에 같은 이름의 dev 태그를 남겼다. 테스트 빌드는 태그를 쓰지 않으므로 무시해도 된다(push 와 commit 은 이미 끝난 뒤다) |

## 이 검증에서 실제로 쓴 값

| 빌드 | 원본 커밋 | 배포본 브랜치 | 배포본 커밋 | wheel |
|---|---|---|---|---|
| 수정본(#481+#482) | `28f8b94f` (`test/481-482-concurrency`) | `test/481-482` | `7239a563` | `+genon.1` |
| 대조군(develop) | `49d8f176` (develop, #493 머지 직후) | `test/develop-base` | `3abe9ac2` | `+genon.0` |
