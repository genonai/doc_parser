---
name: create-patch-bundle
description: genon 전처리기 핫픽스 패치 번들(dist/<이름>) 생성. "패치 번들", "핫픽스 전달", "create_patch" 관련 작업일 때 사용한다.
---

# 패치 번들 생성

운영 환경에 genon 전처리기 변경만 얹을 때 쓰는 overlay 번들을 만든다.

```bash
bash create_patch.sh patch_20260829
# → dist/patch_20260829/ 에 생성
```

## 동작

`genon/preprocessor` 아래에서 **git 이 추적 중인** `*.py`, `*.md`, `*.yaml`, `*.sh` 만 rsync 로 복사한다. 추적되지 않은 파일은 포함되지 않으므로, 새로 만든 파일은 반드시 먼저 `git add` 해야 번들에 들어간다.

인자는 경로가 아니라 폴더 이름 하나여야 한다(`.`, `..`, 슬래시 포함 시 거부).

## 한계 — 중요

**이 overlay 는 genon 전용이다.** `docling/` 변경은 절대 전달되지 않는다. docling 을 고쳤다면 패치 번들이 아니라 wheel 재빌드 + 코드서빙 배포 경로를 써야 한다. `deploy-code-serving` 스킬을 참고한다.

번들을 만들기 전에 docling 변경이 섞여 있지 않은지 확인한다.

```bash
git status --short docling/
```
