---
name: create-patch-bundle
description: genon 전처리기 핫픽스 패치 번들(dist/<이름>) 생성. "패치 번들", "핫픽스 전달", "create-patch-bundle", "create_patch"(옛 이름) 관련 작업일 때 사용한다.
---

# 패치 번들 생성

운영 환경에 genon 전처리기 변경만 얹을 때 쓰는 overlay 번들을 만든다.

```bash
bash build-script/create-patch-bundle.sh patch_20260829
# → dist/patch_20260829/ 에 생성
```

저장소 루트가 아닌 곳에서 호출해도 된다. 스크립트가 `git rev-parse --show-toplevel` 로
루트를 찾으므로 출력은 항상 저장소 루트의 `dist/` 다.

## 동작

`genon/preprocessor` 아래에서 **git 이 추적 중인** `*.py`, `*.md`, `*.yaml`, `*.sh` 만 rsync 로 복사한다. 추적되지 않은 파일은 포함되지 않으므로, 새로 만든 파일은 반드시 먼저 `git add` 해야 번들에 들어간다.

**`resource_dev/` 는 제외된다** (스크립트의 `PATCH_EXCLUDES`). 로컬 개발용 설정이고 실 API 키가
커밋되어 있어, 현장에 전달하는 산출물에 키가 섞여 나가지 않게 한다. 운영 설정은 `resource/` 다.

인자는 경로가 아니라 폴더 이름 하나여야 한다(`.`, `..`, 슬래시 포함 시 거부).

**이미 파일이 있는 목적지에는 만들지 않는다.** rsync 는 지우지 않으므로 번들 대상에서 빠진
파일이 옛 사본으로 남아 저장소와 어긋난 번들이 된다. 같은 이름으로 다시 만들려면 먼저 지운다.

```bash
rm -rf dist/patch_20260829
bash build-script/create-patch-bundle.sh patch_20260829
```

## 한계 — 중요

**이 overlay 는 genon 전용이다.** `docling/` 변경은 절대 전달되지 않는다. docling 을 고쳤다면 패치 번들이 아니라 wheel 재빌드 + 코드서빙 배포 경로를 써야 한다. `deploy-code-serving` 스킬을 참고한다.

번들을 만들기 전에 docling 변경이 섞여 있지 않은지 확인한다. 작업 트리만 보면 놓친다 —
브랜치가 develop 이후에 건드린 경로를 함께 본다. 루트 `main.py` 와 `build-script/` 도
overlay 범위 밖이므로 거기 기능 변경이 있으면 번들만으로는 부족하다.

```bash
git status --short docling/
git diff --name-only origin/develop...HEAD | awk -F/ '{print $1}' | sort -u
```
