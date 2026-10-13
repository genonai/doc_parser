# 전처리기 모듈 릴리스 매뉴얼

`develop` 에 머지된 변경을 **기본 전처리기 서비스 이미지**로 빌드하고, 사내 GenOS 에 등록·검증한 뒤
GitHub 릴리스와 슬랙 공지까지 마치는 절차다. 처음 릴리스를 맡은 작업자가 이 문서만 보고
끝까지 진행할 수 있도록 순서대로 적는다.

- 대상: `doc-parser-preprocessor` 이미지(기본 전처리기 서비스, `genon/preprocessor/src/main.py` 진입점)
- 제외: 코드서빙 이미지·배포본(`deploy-code-serving` 스킬 참조), 핫픽스 패치 번들(`create-patch-bundle` 스킬 참조)
- 이미지 빌드·등록 스크립트의 옵션 의미와 태그 규칙 상세는 [`genon/README.md`](../genon/README.md) 의 "전처리기 빌드 및 등록" 이 정본이다. 이 문서는 릴리스 순서와 판단 지점만 다룬다.

---

## 0. 전체 흐름

```
2절  버전 결정
3절  도커 이미지 빌드 (cpu + standard)
4절  GenOS 에 이미지 등록
5절  전처리기 1차 등록·확인 (첨부·적재·변환) — 새 이미지로 기동되는지 확인
6절  GitHub 릴리스 등록 (릴리스 메시지 작성)
7절  facade 버전 주석 자동 갱신 확인
8절  전처리기 2차 등록 (버전이 명시된 facade 코드로 교체·재배포)
9절  기본 동작 테스트 (벡터 DB 적재 + 육안 확인)
10절 슬랙 공유
11절 정리 — 운영서버 이미지 삭제(필요 시 빌드 캐시도), (필요 시) built-in 반영 요청
```

**5절과 8절에서 전처리기를 두 번 등록하는 이유.** 기본 전처리기 서비스는 GenOS 화면에 붙여넣은
facade 코드가 `preprocessor.py` 로 마운트되어 실행된다. 이미지 안의 facade 가 아니라 **붙여넣은 코드**가
실제로 동작하는 코드다. 릴리스 전에는 새 이미지가 정상 기동되는지를 먼저 확인하고(5절), 릴리스가 등록되어
facade 첫 줄에 버전이 찍힌 뒤 그 코드로 교체해야(8절) 사이트에 나가는 코드와 사내 등록본이 일치한다.

---

## 1. 사전 준비 (최초 1회)


| 항목         | 확인 방법                                                                                                                        |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------- |
| HWP SDK 토큰 | `build-script/hf_private_token.env` 에 `HWP_SDK_TOKEN=hf_...` 한 줄. 발급·위치는 `genon/README.md` "A. 토큰 / 변수 설정" 1번. Git 에 올리지 않는다 |
| 빌드 서버      | `linux/amd64` 빌드가 가능하고 사내 레지스트리로 push 할 수 있는 서버(보통 사내 운영서버)                                                                  |


---

## 2. 버전 결정

버전 표기는 태그·이미지·facade 주석이 모두 같은 문자열을 쓴다. 앞에 `v` 를 붙이지 않는다.


| 종류      | 형식        | 예         |
| ------- | --------- | --------- |
| 정기 릴리스  | `X.Y.Z`   | `2.2.5`   |
| 핫픽스 릴리스 | `X.Y.Z.N` | `2.2.4.1` |


직전 버전은 아래로 확인한다.

```bash
gh release list --limit 5
```

릴리스에 들어갈 변경은 직전 태그 이후 `develop` 에 머지된 PR 이다. 이 목록은 6.1절의 릴리스 메시지 재료가 된다.

```bash
git fetch origin --tags
git log --merges --oneline <직전태그>..origin/develop
```

---

## 3. 도커 이미지 빌드

빌드 서버에서 `develop` 최신 상태로 진행한다.

```bash
git checkout develop && git pull origin develop
```

[`build-script/doc-parser-build.config`](doc-parser-build.config) 에서 아래 값만 바꾼다.

```bash
IMAGE_VERSION=2.2.5        # 2절에서 정한 버전
BUILD_VARIANT=standard     # 운영 레지스트리에는 standard 만 올린다 (synap 은 push 가 차단된다)
HW_VARIANT=cpu             # GenOS 등록은 cpu 이미지로 충분하다
INSTALL_LIBREOFFICE=true
INSTALL_RHWP=true
PUSH_IMAGE=true
SMOKE_TEST=true
```

- `BUILD_VARIANT`·`HW_VARIANT` 는 저장소에 **빈 값으로 커밋되어 있다**. 빌드할 때마다 직접 채운다. 비어 있으면 스크립트가 즉시 중단한다.
- `HWP_SDK_TOKEN` 을 config 파일에 직접 적지 않는다. `hf_private_token.env` 에서 자동으로 읽는다.
- 바꾼 config 는 커밋하지 않는다(로컬 값이다).

실행:

```bash
build-script/doc-parser-build.sh
```

성공 기준:

- 로그 마지막에 `[INFO] Smoke test passed` 와 `[INFO] Push done` 이 보인다. smoke test 는 컨테이너 안에서 샘플 PDF 한 건을 파싱·청킹하고 torch 가 CPU 빌드인지 확인한다. 실패하면 빌드 전체가 실패로 끝난다.
- 생성 태그는 cpu + standard 조합이므로 접미사 없이 `.../mnc/doc-parser-preprocessor:2.2.5` 이다.

---

## 4. GenOS 에 이미지 등록

3절에서 레지스트리에 push 한 이미지를 GenOS 에 등록해야 전처리기 배포 다이얼로그의 이미지 목록에 나타난다.

1. GenOS 웹 UI 에서 **관리 > 리소스 > 도커 이미지** 로 이동한다.
2. 도커 레지스트리에 올라간 이미지명(`mnc/doc-parser-preprocessor`)과 태그(`2.2.5`)를 지정해 등록한다. GenOS 가 레지스트리에서 해당 이미지를 찾아 자동으로 등록한다.
3. 도커 이미지 목록에 `mnc/doc-parser-preprocessor:2.2.5` 가 보이면 등록이 끝난 것이다.

> 예전에 쓰던 [`genon/preprocessor/scripts/register_image.sh`](../genon/preprocessor/scripts/register_image.sh) 방식(스크립트로 push 와 DB 등록)은 더 이상 사용하지 않는다.

---

## 5. 전처리기 1차 등록·확인 (첨부·적재·변환)

새 이미지로 세 전처리기가 기동되는지 먼저 확인한다. GenOS 화면 조작 방법(메뉴 위치, 다이얼로그 필드, 스크린샷)은
[설치 매뉴얼](../genon/preprocessor/manual/installation.md) 3장(전처리기 생성)·5장(리소스 업로드)·6장(배포)을 따른다.


| 전처리기     | facade 코드                                            | 리소스(yaml)                                                                                |
| -------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 첨부용      | `genon/preprocessor/facade/attachment_processor.py`  | `genon/preprocessor/resource/` 의 `attachment_processor_config.yaml` 외                    |
| 적재용(지능형) | `genon/preprocessor/facade/intelligent_processor.py` | `intelligent_processor_config.yaml` 과 이 파일이 참조하는 파일 전부(`model_presets.yaml`, 프롬프트 md 묶음 등) |
| 변환용      | `genon/preprocessor/facade/convert_processor.py`     | `convert_processor_config.yaml` 외                                                        |


- 이미 사내 GenOS 에 등록된 세 전처리기가 있으면 새로 만들지 않고, **코드 교체 → 리소스 재업로드(설정이 바뀐 경우) → 새 이미지 태그로 재배포** 한다.
- 이번 릴리스에서 `resource/` 의 yaml·프롬프트가 바뀌었는지는 `git diff --stat <직전태그>..origin/develop -- genon/preprocessor/resource` 로 확인한다. 바뀌었으면 해당 파일을 다시 업로드한다.
- 배포 다이얼로그에서 **도커 이미지는 4절에서 등록한 태그**(`mnc/doc-parser-preprocessor:2.2.5`)를 고른다.
- 컨테이너 상태가 `배포 완료` 가 되면 다음 단계로 간다. `오류 발생`·기동 실패면 컨테이너 로그(설치 매뉴얼 8장)를 확인하고, 원인을 고친 뒤 3절부터 다시 한다.

---

## 6. GitHub 릴리스 등록

### 6.1 릴리스 메시지 작성

이 메시지는 슬랙 공지(10절)에도 그대로 쓰므로 **사용자(현장 엔지니어·고객) 관점**으로 쓴다.
코드 구조나 함수 이름이 아니라 "무엇이 어떻게 달라졌는지"를 적는다.

형식(직전 릴리스와 동일하게 유지):

```markdown
## 신규 기능 및 개선사항 (Features & Improvements)

- **<변경 요약 제목>** [(#PR번호)](https://github.com/genonai/doc_parser/pull/PR번호)
  <무엇이 문제였고 / 무엇을 바꿨으며 / 어디에 적용되는지 2~3줄>
  <기본 비활성(opt-in)인지, 특정 전처리기에만 적용되는지 등 적용 조건>

## 버그 수정 (Bug Fixes)

- **<증상 기준 제목> 수정** [(#PR번호)](https://github.com/genonai/doc_parser/pull/PR번호)
  <어떤 입력에서 어떤 증상이 있었고, 이제 어떻게 동작하는지>

## 기타 (Others)

- **<문서·도구·내부 정비>**
  <한 줄 설명>
```

작성 기준:

- 2절에서 뽑은 머지 PR 을 하나씩 세 절 중 하나로 분류한다. 사용자에게 영향이 없는 내부 정리는 "기타" 로 묶는다.
- 재색인(벡터 DB 재적재)이 필요한 변경, 설정 키가 바뀌어 사이트 yaml 수정이 필요한 변경은 해당 항목에 **반드시** 적는다.
- 직전 릴리스 본문은 `gh release view <직전태그>` 로 볼 수 있다. 문체를 맞춘다.

### 6.2 릴리스 생성

**대상 브랜치는 `develop`**, 태그 이름과 릴리스 제목은 모두 버전 문자열이다.

GitHub 웹(Releases > Draft a new release)에서 만들거나, CLI 로 만든다.

```bash
gh release create 2.2.5 --target develop --title 2.2.5 --notes-file release-notes.md
```

- 초안(draft)으로 저장하면 7절의 자동 갱신이 실행되지 않는다. **Publish** 까지 해야 한다.

---

## 7. facade 버전 주석 자동 갱신 확인

릴리스가 publish 되면 GitHub Actions 워크플로 [`Update facade version comment on release`](../.github/workflows/update_facade_version.yml) 가
실행되어 아래 세 파일의 **첫 줄**을 바꾸고 `develop` 에 직접 커밋한다.

```
# 첨부용 전처리기 v.2.2.5 (2026-10-12 Release)        <- attachment_processor.py
# 변환용 전처리기 v.2.2.5 (2026-10-12 Release)        <- convert_processor.py
# 적재용(지능형) 전처리기 v.2.2.5 (2026-10-12 Release) <- intelligent_processor.py
```

확인:

```bash
gh run list --workflow update_facade_version.yml --limit 3   # 최근 실행이 success 인지
git pull origin develop
head -1 genon/preprocessor/facade/{attachment,convert,intelligent}_processor.py
```

- 커밋 메시지는 `chore(facade): stamp release 2.2.5 version comment` 이다.
- 워크플로가 실패했거나 실행되지 않았으면 Actions 화면에서 이 워크플로를 **Run workflow** 로 수동 실행한다(`tag` 에 버전, `published_at` 은 비워 두면 당일 날짜).
- 이 커밋은 태그 **이후**에 생긴다. 태그 시점의 코드에는 버전 주석이 이전 값으로 남아 있는 것이 정상이다.

---

## 8. 전처리기 2차 등록 (버전 명시본으로 교체)

7절에서 `git pull` 한 `develop` 의 세 facade 파일을, 5절에서 등록한 세 전처리기의 **코드**에 다시 붙여넣고 재배포한다.
이미지와 리소스는 5절과 같다.

- 붙여넣은 코드의 첫 줄이 `v.2.2.5` 인지 확인한다.
- 세 전처리기 모두 `배포 완료` 상태가 되면 다음 단계로 간다.

---

## 9. 기본 동작 테스트

[설치 매뉴얼](../genon/preprocessor/manual/installation.md) 7장의 방법으로 벡터 DB 에 문서를 적재하고 결과를 눈으로 확인한다.

1. 테스트용 벡터 DB 를 만든다(데이터 형식: **문서**). 릴리스마다 새로 만들거나, 기존 테스트용 DB 에 버전별 폴더를 나눠 쓴다.
2. 문서 업로드 시 **문서 유형**에 8절에서 재배포한 전처리기를 고른다. 적재용(지능형)을 기본으로 하고, 첨부용·변환용도 각각 한 번씩 적재한다.
3. 여러 형식을 고루 넣는다. 기존 버전 테스트에 사용한 문서가 GenOS 벡터 DB 에 있으므로 그 문서를 활용하거나, `genon/preprocessor/sample_files/` 의 샘플을 사용해도 된다.


| 형식    | 샘플 예                                                                       |
| ----- | -------------------------------------------------------------------------- |
| PDF   | `pdf_sample.pdf`, `FinalPaperTemplate.pdf`                                 |
| 한글    | `hwp_sample.hwp`, `hwp_sample_table.hwp`, `hwpx_sample.hwpx`               |
| 오피스   | `docx_sample.docx`, `doc_sample.doc`, `pptx_sample.pptx`, `ppt_sample.ppt` |
| 표·텍스트 | `csv_sample.csv`, `md_sample.md`, `html_tables.html`                       |
| 이미지   | `png_sample.png`, `jpeg_sample.jpeg`                                       |


4. 이번 릴리스에서 고친 결함·추가한 기능이 있으면 **그 PR 의 재현 문서**도 함께 넣어 변경이 실제로 반영됐는지 확인한다.

확인 항목:

- 모든 문서가 `완료` 상태가 되는가(`오류 발생`·장시간 `실행 중` 이 없는가)
- 텍스트가 누락·깨짐 없이 추출되는가
- 청크 크기와 경계가 적절하고, 표가 깨지지 않는가
- 적재용은 청크 첫 줄의 `HEADER:` 태그(섹션 헤더)가 맞게 붙는가

문제가 있으면 슬랙 공유를 보류하고 원인을 이슈로 등록한다. 수정이 필요하면 핫픽스 버전(`X.Y.Z.N`)으로 2절부터 다시 진행한다.

---

## 10. 슬랙 공유

9절까지 모두 정상이면 6.1절의 릴리스 메시지를 슬랙에 공유한다. 기존 슬랙 메세지를 참고한다.

```
[전처리기 릴리스] 2.2.5
- 이미지: mnc/doc-parser-preprocessor:2.2.5 (cpu, standard)
- 릴리스 노트: https://github.com/genonai/doc_parser/releases/tag/2.2.5

<6.1절의 릴리스 메시지 본문>
```

- 재색인이 필요한 변경이나 사이트 yaml 수정이 필요한 변경이 있으면 본문 맨 위에 따로 적는다.

---

## 11. 정리

### 11.1 운영서버 이미지 삭제 (운영서버에서 빌드한 경우)

운영서버는 디스크 여유가 적다. 이미지가 레지스트리에 올라간 것을 확인한 뒤(4절 완료 후) 빌드 서버의 로컬 사본을 지운다.

```bash
docker images | grep doc-parser-preprocessor       # 이번 빌드 태그와 이전 릴리스 태그 확인
docker image rm <레지스트리>/mnc/doc-parser-preprocessor:2.2.5   # 이번 빌드 로컬 사본
```

이미지를 지운 뒤에도 디스크 여유가 부족하면 BuildKit 빌드 캐시를 함께 지운다. 빌드 캐시는 이미지 사본보다 용량을 더 차지하는 경우가 많다.
지우면 다음 빌드가 처음부터 진행되어 시간이 더 걸린다.

```bash
docker system df            # 사용량 확인 (Build Cache 행)
docker builder prune -f     # 빌드 캐시 삭제
```

- `docker system prune -a` 처럼 **모든** 미사용 이미지를 지우는 명령은 운영서버에서 쓰지 않는다. 다른 서비스가 쓰는 이미지까지 지울 수 있다.

### 11.2 GenOS built-in 반영 요청 (필요한 경우만)

이번 릴리스로 **새 facade 가 추가되었거나, 기본 yaml(`genon/preprocessor/resource/`)이 바뀌어** GenOS 설치 시 기본 제공되는
built-in 전처리기 파일도 바꿔야 하면, 김준기 프로에게 반영을 요청한다.

요청에 포함할 것:

- Genos에 등록된 전처리기 링크
- 반영할 내용 목록(facade, yaml·프롬프트)

facade 버전 주석만 바뀐 일반 릴리스는 요청하지 않아도 된다.

---

## 체크리스트

```
[ ] 2  버전 결정, 머지 PR 목록 확보
[ ] 3  doc-parser-build.config 설정 → 빌드 → smoke test passed / push done
[ ] 4  관리 > 리소스 > 도커 이미지에서 이미지명·태그 지정 → 목록에 등록 확인
[ ] 5  첨부·적재·변환 전처리기 새 이미지로 배포 완료
[ ] 6  릴리스 메시지 작성 → develop 대상 릴리스 publish
[ ] 7  버전 스탬프 워크플로 success, facade 첫 줄 확인
[ ] 8  버전 명시본 facade 로 세 전처리기 재배포
[ ] 9  다양한 형식 문서 적재 테스트, 육안 확인
[ ] 10 슬랙 공유
[ ] 11 운영서버 이미지 정리(필요 시 빌드 캐시), (필요 시) built-in 반영 요청
```