---
name: wip
description: 작업 중 변경을 논리 단위로 나눠 한국어 메시지로 커밋하고 푸시한다. "커밋", "커밋해줘", "푸시", "중간 저장" 관련 작업일 때 사용한다.
---

# 작업 중 커밋·푸시

`/wip [힌트]` 로 호출한다. 한 작업 브랜치에서 여러 번 반복 호출되는 것을 전제로 한다.
힌트가 주어지면 커밋 분할과 메시지 작성에 반영한다.

## 절차

### 1. 변경 파악

```bash
git status --porcelain
git diff --stat
git diff --cached --stat
```

내용 확인이 필요한 파일만 `git diff -- <경로>` 로 본다. 큰 facade 파일의 diff 전문을 통째로 읽지 않는다.

### 2. 커밋하면 안 되는 것 점검

아래 경로가 스테이징 대상에 들어오면 사용자에게 경고하고 제외를 제안한다.

- `reference/`, `shkim_labs/`, `dist/`, `build/`, `debug/`, `tmp/`, `code-serving/` — 모두 gitignore 대상이지만 강제 추가된 경우가 있을 수 있다
- `genon/preprocessor/resource_dev/` 의 API 키가 든 yaml — 이미 커밋된 이력이 있으나 새 키를 더 넣지 않는다
- 대형 fixture(`tests/data/`, `tests/data_scanned/`), `*.whl`, 모델 가중치

### 3. 논리 단위 분할

변경을 의미 단위로 나눈다. "리팩터링 + 그 위의 기능 추가" 처럼 성격이 다른 변경은 나누고,
같은 목적의 여러 파일 수정은 한 커밋으로 묶는다.

각 묶음에 한국어 한 줄 메시지를 만든다. 저장소 스타일 기준:

```
compact_tables off 스위치가 무시되던 버그 수정
docling_core 청커 포크본을 공용 모듈로 이관
_get_pdf_path 를 공용화하면서 확장자 치환 버그를 고침
```

- conventional commits prefix(`feat:`, `fix:` 등)를 쓰지 않는다. mergify 의 conventional 강제는
  `base = main` 조건이라 docling 업스트림 전용이며 이 흐름에는 적용되지 않는다
- 이모지를 쓰지 않는다
- 무엇을 했는지가 아니라 무엇이 달라졌는지를 쓴다
- 설명이 더 필요하면 본문에 한두 줄 덧붙인다

### 4. 승인 요청

묶음별 파일 목록과 메시지를 제시하고 승인받는다. 승인 전에 커밋하지 않는다.

### 5. 커밋

묶음마다 경로를 명시해 스테이징한다. `git add -A` 나 `git add .` 는 쓰지 않는다.

```bash
git add <경로들>
git commit -m "<메시지>"
```

커밋 메시지 말미에는 세션 정책상의 `Co-Authored-By` / `Claude-Session` 트레일러를 붙인다.

### 6. 푸시

```bash
git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "no-upstream"
```

업스트림이 없으면 `git push -u origin HEAD`, 있으면 `git push`.
현재 브랜치가 `develop` 이면 푸시하지 않고 중단한다.

### 7. 보고

커밋 해시와 메시지 목록, 푸시 결과를 보고한다.
