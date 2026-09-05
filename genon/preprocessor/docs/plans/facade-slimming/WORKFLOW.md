# 작업 방식 — 임시 작업본과 A/B 대조

01~04 의 각 단계는 이 절차로 진행한다.

## 왜 임시 작업본인가

리팩터링 전후가 **정말 같은 결과를 내는지 직접 눈으로 확인**하기 위해서다.
스냅샷 대조만으로는 두 실행 사이에 환경이 달라졌을 가능성을 배제하지 못한다.
같은 프로세스 안에서 옛 것과 새 것을 나란히 돌리면 그 여지가 사라진다.

**영구 병존이 목적이 아니다.** 단계가 끝나면 임시본이 원본을 대체하고 사라진다.

## A/B 가 보장하지 않는 것 — 반드시 읽는다

"같은 프로세스에서 나란히 돌리면 환경 차이 여지가 사라진다" 는 **절반만 맞다.
A 쪽도 현재 트리를 읽는다.**

- 02 는 `intelligent_processor.py`/`convert_processor.py` 를 **제자리에서** 고친다(wip 대상이 아니다)
- **어떤 단계가 `facade/common/*` 이나 `facade/chunking/*` 을 편집하면 원본과 wip 이 같은 모듈
  객체를 공유한다**(둘 다 `genon.preprocessor.facade.*` 절대 import). 그 순간 A 도 함께 바뀌어
  **A/B 차이 0 인데 골든은 차이 발생** 이 된다

**A/B 는 "wip == 현재 트리의 원본" 만 증명한다. "wip == 착수 전 동작" 을 증명하는 것은 골든뿐이다.**
공용 모듈을 편집하는 단계(02·03a·03b)에서는 **골든이 유일한 판정자**다.

## A/B 대조기가 반드시 처리해야 할 것

1. **`docling_settings.perf` 스냅샷·복원·비교** — `parser_processor.py:345` ·
   `chunking_processor.py:527` · `intelligent_processor.py:550` · `convert_processor.py:527` 이
   모두 `__init__` 에서 **프로세스 전역 싱글턴**을 쓴다. 원본과 wip 을 둘 다 인스턴스화하면
   나중 것이 이긴다. wip 이 이 값을 다르게 해석하는 회귀는 **양쪽이 같은 값을 쓰므로 안 보인다**
2. **예외 클래스를 쌍으로 묶는다** — `parse_chunk_test.py:44` 가 chunker 모듈에서
   `_classify_payload` 와 `GenosServiceException` 을 가져와 `try/except` 로 입력을 판별한다.
   **wip 은 자기 예외 클래스를 갖는다.** 섞으면 catch 되지 않고 터진다.
   `(module, DocumentProcessor, GenosServiceException, _classify_payload)` 튜플로 다룬다
3. **양측에 같은 `log_level` 을 준다** — `common/runtime.py:32` 의 `setup_logging` 이
   `logging.basicConfig` + `root.setLevel` 을 되돌리지 않는다. 마지막 것이 전역을 잡아
   A 쪽 진단 로그가 사라진다
4. **정규화 전/후를 둘 다 비교한다** — 00 의 "정규화를 어디에 둘 것인가" 절 참조

## 절차

### 1. 임시 작업본 생성

```
facade/parser_processor.py       ← 원본. 이 단계 동안 건드리지 않는다
facade/parser_processor_wip.py   ← 임시 작업본. 리팩터링은 여기서만 한다
```

청커도 같다(`chunking_processor_wip.py`).

### 2. A/B 대조기

`examples/parse_chunk/parse_chunk_ab.py` (임시) 가 두 모듈을 **동시에 import** 해서
같은 입력을 양쪽에 넣고 산출을 비교한다.

in-process 테스트는 코드서빙의 단일 마운트 제약과 무관하므로 두 facade 동시 import 가
가능하다(`parse_chunk_test.py` 가 이미 parser 와 chunker 를 함께 import 한다).

대조 대상은 파서 출력(`.parse.json` / `.docling.json`)과 청커 출력(`.chunks.json`) **둘 다**다.
청크만 보면 파서 단계의 회귀가 청킹에서 상쇄돼 보일 수 있다.

정규화 규칙(`reg_date`·경로 등 실행마다 흔들리는 필드)은 00번에서 실측으로 확정한 것을
그대로 쓴다.

### 3. 차이 0 확인

```
차이가 있으면 → 그 자리에서 원인 규명. 설명하고 넘어가지 않는다
차이가 0 이면 → 4단계
```

### 4. 교체

```bash
git mv -f facade/parser_processor_wip.py facade/parser_processor.py   # -f 필수: 대상이 존재한다
```

교체 뒤 **유닛 테스트를 돌린다.** 유닛은 `facade.parser_processor` 를 이름으로 import
하므로 임시본 상태에서는 커버되지 않는다. 교체 후가 유일한 검증 시점이다.

### 5. 정리

A/B 대조기는 단계가 끝나면 지운다. 남기면 곧 실제 코드와 어긋난다.

## 임시 파일 취급

**커밋한다.** 브랜치가 개인 작업 브랜치이므로 작업 도중 유실 위험을 없애는 편이 낫다.
교체·삭제도 명시적인 커밋으로 남긴다.

주의할 것 두 가지.

- **패치 번들에 섞이지 않게 한다.** `create-patch-bundle.sh` 는 git 이 추적하는 파일을
  복사하므로, 임시본이 커밋된 상태에서 번들을 만들면 함께 들어간다.
  임시본이 트리에 있는 동안에는 번들을 만들지 않는다.
- **배포 동기화 대상이다.** `sync-serving-repo.sh` 의 제외 목록에 `facade/legacy/`,
  `facade/legal_parser/`, `facade/README.md` 만 있으므로 `*_wip.py` 도 복사된다.
  PR 전에 반드시 사라져 있어야 한다.

## 단계 종료 조건

한 단계는 아래를 모두 만족해야 끝난 것이다.

1. A/B 대조 차이 0
2. 골든 대조(`--check`) 차이 0
3. 표적 유닛 테스트 통과 (교체 후)
4. 임시 파일 삭제 완료
5. `git status` 에 `*_wip.py` 가 없음

## 왜 새 processor 를 영구히 두지 않는가

코드서빙 배포는 facade **한 개**만 `preprocessor.py` 로 마운트한다.
두 벌이 영구히 남으면 같은 수정을 두 곳에 해야 하고, `facade/legacy/` 13개 파일이
정확히 그렇게 갈라진 결과물이다. 임시본은 단계 안에서만 산다.
