# 07. 고객 수정분의 수명주기

전제: 없음. **이 문서는 01~06 과 독립이며, 우선순위는 가장 높다.**

## 왜 이 문서가 생겼나

계획 전체가 "고객이 프로세서를 직접 고쳐서 새 문서를 처리한다" 를 목적으로 내걸었는데,
**그 고친 코드가 다음 릴리스에서 어떻게 되는지를 한 줄도 다루지 않았다.**
고객사 개발자 관점 검증에서 가장 먼저 지적된 항목이다.

고쳐도 다음 패치에 사라진다면 파일을 아무리 줄여도 목적은 달성되지 않는다.

## 실측 — 배포 경로 둘 다 고객 수정분을 말없이 덮어쓴다

### 1) 코드서빙 갱신 절차가 git 머지를 스스로 차단한다

`build-script/code-serving-README.md:86` 이 안내하는 공식 절차:

```bash
tar --exclude=.git -cf - . | (cd <gitea_dir> && tar -xf -)
```

**머지가 아니라 tar 추출이다.** `.git` 을 제외하므로 3-way 머지 경로가 애초에 막힌다.
고객이 고친 `preprocessor.py` 는 경고 없이 사라진다.

### 2) 핫픽스 번들도 전량 덮어쓰기다

`build-script/create-patch-bundle.sh:49-51` 은 `git ls-files -- '*.py' '*.md' '*.yaml' '*.sh'`
를 그대로 복사한다. 실측 390개 파일이며 여기에 facade 코드와 `resource/*.yaml` 이 전부 들어간다.

`facade/gitbook_doc/code_serving_dev_manual.md:1947-1951` 에 `resource/*.yaml` 이 덮어써진다는
경고가 있지만 **facade 코드에 대한 같은 경고는 없다.**

## 정해야 할 것

이 문서의 산출은 코드가 아니라 **절차와 그 절차를 적은 고객 문서**다.

### A. 갱신 방식을 하나 고른다

| 안 | 내용 | 대가 |
|---|---|---|
| A1 | gitea 저장소에 공개 배포본을 upstream remote 로 걸고 `git merge` / `rebase` | 현재 안내(`tar --exclude=.git`)를 바꿔야 한다. 고객이 git 충돌을 다뤄야 한다 |
| A2 | 고객 수정분을 `git diff > my_change.patch` 로 보관 → 릴리스 덮어쓰기 → `git apply` 재적용 | 릴리스마다 수동. 충돌 시 고객이 해결. `dev_manual:2209` 에 명령 자체는 이미 있다 |
| A3 | 고객 소유 경로를 번들·동기화 범위에서 분리 | 어느 경로가 고객 소유인지 정의가 필요하다. `resource/` 는 명백하나 facade 는 파일 하나라 분리 불가 |

**A2 를 기본으로 하되 A1 을 권장 경로로 문서화**하는 것이 현실적이다.
A3 는 `resource/` 에 대해서만 별도로 검토한다.

### B. 번들 README 에 경고를 넣는다

`create-patch-bundle.sh` 산출물에 "이 번들은 facade 코드와 `resource/*.yaml` 을 전량 덮어쓴다.
사이트 수정분이 있으면 먼저 `git diff` 로 보관하라" 를 명시한다.

### C. 배포 단위가 둘이라는 사실을 명시한다

`IS_PARSER` 는 `parser_processor.py:904` 에만, `IS_CHUNKER` 는 `chunking_processor.py:384` 에만 있다.
외부 사이트형 배포는 facade **한 개**만 `preprocessor.py` 로 마운트하므로
**한 서빙이 파싱과 청킹을 둘 다 할 수 없다.**

즉 고객은 서빙 2개를 운용하고, JSON doc_type 을 추가할 때 **두 배포본을 각각 갱신·동기화**해야 한다.
06 의 "facade 1개 파일" 기준은 벤더 저장소 기준의 셈이고 고객 운영 단위와 다르다.
고객이 여는 파일 이름도 `parser_processor.py` 가 아니라 `preprocessor.py` 다 —
계획과 고객 문서의 파일명이 전부 어긋나 있다.

### D. 고객이 자기 baseline 을 만드는 절차

`00-golden-baseline.md:66` 은 골든 산출물을 저장소에 커밋하지 않는다고 정했다.
그러면 **고객에게는 벤더 골든이 가지 않는다.** 그런데 05·06 은 고객에게 "골든 대조로
회귀를 확인하라" 고 지시한다. 실행 불가능한 지시다.

`parse_chunk_golden.py --record` 를 **고객이 자기 문서로 돌려 자기 골든을 만드는 것**이
정식 절차임을 고객 문서에 넣는다. 그것이 있어야 "내 수정이 기존 문서를 깨지 않았다" 를
고객이 스스로 확인할 수 있다.

## 함께 처리할 선행 조치 (리팩터링과 무관, 즉시)

고객사 관점 검증에서 함께 지적된 것으로, 이번 리팩터링의 전제 조건이다.

1. **`resource/parser_processor_config.yaml:214-216` 의 실 API 키·엔드포인트 마스킹.**
   `table_text_description` 블록이 `enable: true` 로 사내 게이트웨이 주소와 키를 담고 있다.
   같은 파일의 다른 enrichment 블록 9개는 `api_key: ""` 로 마스킹돼 있어 이 한 블록만 빠졌다.
   `code-serving/` 배포본 동일 위치까지 전파돼 있고, 히스토리에 남으므로 rotate 가 필요하다.
2. **배포본 whitelist 재검토** — `sample_files/monimo/` 의 실 원천 48건과
   `docs/plans/`(이 계획 문서 자체)가 고객에게 배포된다. `sync-serving-repo.sh:76` 의
   whitelist 가 `genon` 전체이고 `EXCLUDE_PATHS` 에 둘 다 없다.
3. **출고 `custom_field_*.yaml` 16개의 고아 주석 정리** — v2 자동 변환 배너와 원본 v1 주석
   전문이 상단에 남아 있다. 이관이 끝난 것은 `custom_field_faq.yaml` 하나뿐이고 9개는
   설정부 주석이 0줄이다. **고객이 예시로 삼을 파일이 이 상태다.**

## 이 문서의 상태

**01~06 착수 전에 A·B·C·D 의 결정이 나 있어야 한다.** 특히 A 는 04·05·06 이 만들 고객 문서의
내용을 좌우한다. 선행 조치 1은 즉시 처리 대상이다.
