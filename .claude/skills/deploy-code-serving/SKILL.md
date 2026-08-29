---
name: deploy-code-serving
description: 코드서빙 배포본(doc_parser_code_serving) 생성·검증·릴리스 절차. "코드서빙 배포", "sync-serving", "배포본 갱신", "릴리스 태그", "wheel 재빌드" 관련 작업일 때 사용한다.
---

# 코드서빙 배포

배포본은 별도 Private 저장소 `genonai/doc_parser_code_serving` 이고, 로컬 `code-serving/` 은 그 클론이다(doc_parser 는 이 폴더를 추적하지 않는다).

## 먼저 할 일

`build-script/sync-serving-repo.sh` 의 파일 상단 주석을 읽는다. 동작 원리, 버전 정합, 릴리스 순서, 운영 캐비앳이 모두 거기에 최신 상태로 적혀 있다. 이 스킬 문서보다 스크립트 주석이 우선한다.

```bash
sed -n '1,60p' build-script/sync-serving-repo.sh
```

## 반드시 지킬 것

**검증 실행은 throwaway 디렉터리로 한다.**
`code-serving/` 클론이 이미 존재하는 상태에서 스크립트를 돌리면 dry-run이 아니라 그 클론에 **실제 커밋이 남는다.** 조립 결과만 보고 싶을 때는 `SERVING_DIR` 을 임시 경로로 지정한다.

```bash
SERVING_DIR=$(mktemp -d) bash build-script/sync-serving-repo.sh
```

**docling 을 고쳤으면 wheel 재빌드가 필수다.**
배포본에는 docling 소스가 들어가지 않고 `packages/` 의 wheel 만 동봉된다. 핫픽스 overlay(`create_patch.sh` 계열)는 genon 전용이라 docling 변경을 전달하지 못한다.

```bash
bash build-script/build-docling-wheel.sh
```

## 릴리스 순서

1. `docling/` 또는 `genon/` 변경을 doc_parser 에 커밋하고 릴리스 태그를 붙인다.
2. 배포본 재생성 + push + 미러 태그. docling 패치가 바뀌었으면 `GENON_BUILD` 를 올린다.
   ```bash
   PUSH=true VERSION=2.2.5 GENON_BUILD=N bash build-script/sync-serving-repo.sh
   ```
   `VERSION` 을 생략하면 `git describe` 로 자동 파생된다.
3. 배포처(gitea)를 새 배포본으로 갱신한 뒤 리비전을 재배포한다. 절차는 `build-script/code-serving-README.md` 참고.

## 주의점

- push 는 `SERVING_DIR` 의 origin 이 배포본 repo 와 일치하는 클론일 때만 허용된다(무관 repo 보호).
- 이미 있는 태그를 다른 커밋으로 옮기려 하면 에러로 중단된다. 의도적일 때만 `FORCE_TAG=true`.
- 매 릴리스 wheel(약 5MB)이 배포본 repo 에 누적된다.
- `.claude/settings.json` 이 `code-serving/` 을 Read/Edit 에서 막아 두었다. 배포본 내용을 직접 확인해야 하면 `SERVING_DIR` 로 만든 임시 디렉터리를 본다.
