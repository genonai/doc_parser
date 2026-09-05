# 00. 골든 출력 회귀 하네스

**이 작업이 01~05 보다 먼저다.** 이것 없이 시작한 리팩터링은 "동일하게 동작한다" 를
증명할 수 없고, 되돌릴 지점도 잡을 수 없다.

## 목표

parser / chunker 의 산출을 리팩터링 전후로 **바이트 단위 대조**한다.
차이가 나오면 그 자리에서 멈춘다.

## 자산 — 이미 저장소 안에 있다

| 자산 | 위치 | 규모 |
|---|---|---|
| 모니모 실 원천 샘플 | `sample_files/monimo/` | 48개 파일 |
| 그 외 포맷 샘플 | `sample_files/` | pdf·docx·hwp·hwpx·xlsx·html·md·ppt·이미지 등 |
| doc_type 검증 케이스 | `examples/parse_chunk/parse_chunk_verify.py` `CASES` | **25케이스** / 14 doc_type |
| 단건 실행기 | `examples/parse_chunk/parse_chunk_test.py` | CLI |

`parse_chunk_verify.py` 는 **설정이 약속한 필드가 실렸는지**를 단정한다(required/constants/
doc_type 스탬프). 값이 무엇인지까지는 보지 않는다. 골든 대조는 그 위에 얹는 별개 층이다.

## 실행 단위

```bash
examples/parse_chunk/parse_chunk_test.py <입력> <출력디렉터리>/ --doc_type <type> \
  --llm_cache --interim_root <캐시루트> --workflow_id golden --run_id base
```

산출물 2종:

- `<stem>.parse.json` 또는 `<stem>.docling.json` — 파서 출력
- `<stem>.chunks.json` — 청커 출력 (= 적재되는 벡터)

**두 산출물을 모두 고정한다.** 청크만 보면 파서 단계의 회귀가 청킹에서 상쇄돼 보일 수 있다.

## 케이스 커버리지 — 지금 CASES 로는 부족하다

검증에서 드러난 구멍이다. `CASES` 의 포맷 분포를 실측하면:

```
json 10 · xlsx 6 · html 5 · md 3 · .parsed 1
PDF 0 · hwp/hwpx 0 · docx 0 · ppt/pptx 0 · csv 0 · 오디오 0
```

**그런데 포맷보다 더 큰 구멍이 먼저 있다.** 아래 절을 먼저 읽는다.

## 최우선 구멍 — 골든이 3c 의 절반을 실행하지 않는다

`parse_chunk_test.py:91` 이 `_parser._output_format = "docling"` 으로 **출력 포맷을 강제**한다
(출고 설정도 `output.format: "docling"`).

`_build_docling_response`(`parser_processor.py:2162`)는 포맷에 따라 분기하므로,
03 의 3c 가 옮기는 15개 함수 중 **5개만 골든이 본다.**

| 골든이 보는 것 | 골든이 못 보는 것 |
|---|---|
| `_serialize_docling_document`, `_docling_page_count`, `_normalize_response`, `_tabular_to_parse_format` | `_docling_to_parse_format`, `_export_table_content`, `_get_normalized_coords`, `_docling_sheet_prefix`, `_docling_to_content`, `_content_response`, `_replace_markdown_tables_with_html`, `_langchain_to_parse_format`, `_audio_to_parse_format` |

**PDF 케이스를 추가해도 이 구멍은 그대로다.** 포맷 축이 다르기 때문이다.

→ **`parse_chunk_test.py` 에 `--output-format` 인자를 추가하고 골든을 `docling` / `json`
두 벌로 기록한다.** 이것이 없으면 3c 는 골든 안전망 없이 진행하는 것이다.

부수 정정: 아래 표에서 `docx_sample.docx` 의 이유로 든 "`clear_coordinates=True` 분기" 는
**docling 포맷에서 무시된다.** json 골든이 생겨야 의미가 있다.

**02 와 03 이 정확히 그 비어 있는 경로를 건드린다.** 02 는 PDF/hwp/docx 가 전부 지나가는
docling 런타임 배선을 통째로 옮기고, 03 의 3c 는 모든 docling 산출의 최종 직렬화를 옮긴다.
그 두 단계의 유일한 안전망이 PDF·hwp·docx 를 한 건도 보지 않는다.

`sample_files/` 에 `pdf_sample.pdf` · `hwp_sample_table.hwp` · `docx_sample.docx` ·
`pptx_sample.pptx` 가 전부 있는데 하나도 `CASES` 에 없다.

**골든 전용 케이스로 최소 아래를 추가한다.**

| 추가 | 이유 | 이 머신에서 |
|---|---|---|
| `pdf_sample.pdf` | 02 가 옮기는 docling 런타임의 주 경로 | **불가** (genos_layout 엔드포인트 미도달) |
| `pptx_sample.pptx` | PDF 재라우팅 + 페이지 설명 | **불가** (동일) |
| `hwp_sample_table.hwp` | 별도 백엔드 + 표 | **머신 종속** (hwp_sdk 미설치 → 폴백 경로) |
| `docx_sample.docx`, `tablecell.docx` | MsWord 백엔드 | 가능 |
| `html_tables.html` | 표 직렬화 난이도 | 가능 |

**PDF·pptx 는 사내망 엔드포인트가 필요하고, 연결 실패는 조용한 SKIP 이 아니라 하드 실패다**
(`genos_dots_ocr_layout_model.py:1219` 가 `RequestException → RuntimeError` 로 전파).
로컬 모델 가중치는 이미 있다 — 막는 것은 가중치가 아니라 엔드포인트다.

두 갈래 중 하나를 고른다.

- (a) `layout.layout_model_type: docling_layout` 로 도는 **오프라인 골든 프로파일**을 따로 둔다.
  실행은 되지만 **운영과 다른 경로**임을 기록한다
- (b) PDF·pptx 는 사내망 전용 케이스로 분리하고, `--check` 가 SKIP 했음을 종료 요약에 남긴다
  (**조용한 SKIP 금지**)

`hwp` 는 `use_hwp_sdk` 를 명시로 고정하거나 골든 메타에 어느 백엔드를 탔는지 기록한다.

**머신 종속 케이스를 분리한다.** 마지막 `card` 케이스가 `REPO_ROOT / "shkim_labs" / ...`
(gitignore 된 개인 디렉터리)에 의존한다. 골든에서는 빼거나 별도 목록으로 옮긴다.

## 비결정성 — 실측으로 교체했다

초판이 3축(LLM·시각·경로)을 추정으로 적었다. **2케이스를 각각 2회 실행해 실제로 대조한 결과:**

```
monimo_menu_sample.xlsx (LLM 없음)   .parse.json   → 완전 동일
                                      .chunks.json  → reg_date 2줄만 상이
monimo_cs_hpp_sample.html (LLM 있음)  .docling.json → 완전 동일 (byte identical)
                                      .chunks.json  → reg_date 10줄만 상이
캐시: 1회차 MISS+STORE, 2회차 HIT
```

| 축 | 판정 |
|---|---|
| LLM 호출 | **맞음.** 캐시 동작 확인. 단 아래 단서 2개 |
| `reg_date` | **맞음. 실측된 유일한 노이즈** |
| `created_date` | **근거 없음** — `_metadata_field_transforms` 경로이지 실행 시각이 아니다 |
| 경로(`file_path`/`media_files`) | **틀림** — `file_path` 는 원천 경로라 안정적이고, `media_files` 는 basename 만 담으며 그 이름은 **내용 해시**다 |
| guardrail | **현재 발동 불가** — `gr.call_enabled` 가 요청 kwargs 의 `guardrail_call` 을 보는데 `parse_chunk_test.py` 에 그 인자가 **없다.** 켜고 끄기 이전에 **호출 수단부터 만들어야 한다** |

**따라서 정규화 대상은 `reg_date` 하나다.** 하드코딩하지 말고 `VOLATILE_FIELDS = ("reg_date",)`
로 두되, `--noise` 가 실제 diff 를 보고 후보를 **출력만** 하게 한다.
자동 확장은 금지 — 회귀를 노이즈로 삼킨다.

### LLM 단서 1 — soft-fail 이 "빈 값으로 일치" 를 만든다

`custom_fields_enricher.py:1022` 의 자체 `except Exception` 이 LLM 실패를 삼키고
`parsed = {}` 로 진행한다. 이 catch 는 `_handle_stage_error` 보다 안쪽이라
**`error_policy=strict` 로도 막히지 않는다.** 같은 패턴이 `body_summary.py:88`,
`prompt_manager.py:530` 에도 있다.

`llm_cache._should_cache` 가 빈 값을 저장하지 않으므로 실패는 캐시에 남지 않고 매번 재시도한다.
결과: **`--record` 와 `--check` 를 둘 다 사내망 밖에서 돌리면 양쪽 다 null → 차이 0 → 통과.**
LLM 경로 전체가 검증되지 않은 채 "동일함" 이 선언된다.

`parse_chunk_verify.py:594-600` 은 이미 `llm_null_rate` 를 리포트한다. **골든에도 넣는다.**

→ `--record` 시작 시 LLM 연결을 사전 점검하고 실패하면 **하드 에러로 중단**한다.
골든 메타(`golden_meta.json`)에 `llm_null_rate`·캐시 hit/miss·엔드포인트 도달 여부를 기록하고,
`--check` 에서 그 값이 달라지면 **차이 0 이어도 경고**한다.

### LLM 단서 2 — 캐시 키가 payload 전체다

`llm_cache.cache_key` = `sha256(endpoint + json.dumps(payload, sort_keys=True))` 이고
payload 에는 **렌더된 messages 전체**가 들어간다. user 메시지는 파서가 만든 `raw_text` 를 담는다.

즉 **파서 산출이 한 글자만 달라져도 캐시 미스 → 실호출**이고, temperature 0.0 이지만
seed 가 없어 답이 달라질 수 있다. 리팩터링이 파서 텍스트를 바꾸면 골든 diff 에
**(a) 파서 회귀 + (b) LLM 재호출로 인한 필드 변화**가 겹쳐 나온다.

→ `--check` 는 **캐시 miss 가 1건이라도 나면 "LLM 재호출 발생" 을 별도 라인으로 보고**한다.
`llm_cache` 에 이미 hit/miss 카운터와 `log_summary()` 가 있으므로 로그 파싱이면 된다.

### 실행 부작용 — 산출이 아니라 작업 트리를 더럽힌다

- `TextLoaderBase.output_dir = /tmp/<uuid4>`(`common/loaders.py:62`) — 매 실행 새 디렉터리
- **파생 PDF 가 원천 옆에 써진다** — `file_probe.get_pdf_path:223`. `sample_files/{docx,md,ppt,pptx}_sample.pdf`
  가 이미 그렇게 생겨 있다. `genon/.gitignore` 가 덮고 있으나 **덮이지 않는 새 확장자를 넣으면
  WORKFLOW 종료조건 5(`git status` 청결)를 깬다**
- **`.artifacts/` 를 원천 옆에 mkdir 한다**(`parser_processor.py:1256`). 지금은 빈 디렉터리라
  git 이 안 보지만 **그림 있는 문서를 넣으면 PNG 가 untracked 로 뜬다**
- `_parse_audio` 가 CWD 에 `./tmp_audios_*` 를 만든다

문제없는 것으로 확인된 축: dict/set 순회(`sorted(set(...))` 로 감싸져 있음),
ThreadPool(`executor.map` 이라 입력 순서 보존), `page_chunk_counts`(요청별 초기화됨).
다만 마지막 것은 **A/B 대조기를 in-process 로 만들면 처음으로 시험대에 오른다.**

## 절차

### 1단계 — 노이즈 기준선 (같은 코드로 2회)

```
동일 커밋에서 전체 케이스를 2회 실행 → run_a / run_b
run_a 와 run_b 를 그대로 대조
```

여기서 나오는 차이가 곧 **정규화해야 할 필드 목록**이다. 추정하지 않는다.
2회가 완전히 같아질 때까지 정규화 규칙을 넓히고, 그 규칙을 하네스에 고정한다.

**노이즈가 0 이 되기 전에는 다음 단계로 가지 않는다.** 노이즈가 남은 채로 리팩터링하면
회귀와 잡음을 구분할 수 없다.

### 2단계 — 기준선 확정

```
run_a 를 골든으로 채택 → 저장소 밖(스크래치패드 또는 gitignore 경로)에 보관
```

골든 산출물은 저장소에 커밋하지 않는다. 크고, 원천 데이터를 그대로 담고 있으며,
갱신 주기가 코드와 다르다. 경로는 기록만 남긴다.

### 3단계 — 단계마다 대조

01~05 각 단계 종료 시:

```
전체 케이스 재실행 → 정규화 → 골든과 대조 → 차이 0 이어야 통과
```

차이가 나오면 **그 단계를 되돌리고** 원인을 먼저 밝힌다. 차이를 설명하고 넘어가지 않는다.
설명 가능한 차이(예: 01 단계에서 컨버터 생성이 사라지는 것)는 산출물에 나타나지 않아야
정상이다 — 산출물이 바뀌었다면 그것은 설명이 아니라 회귀다.

## 유닛 테스트 병행 기준

골든 대조가 1차 관문이고 유닛은 보조다.

- 단계 진행 중에는 **바꾼 모듈을 쓰는 테스트 파일만** 지정해 돌린다.
- 전체 유닛(`tests/unit`, 201초)은 **각 단계 종료 시 1회**와 PR 직전에만 돌린다.
- 사전 실패 목록을 0단계에서 **파일로 고정**하고 매번 `diff` 한다. `--lf` 는 못 쓰지만
  `-rf` 요약을 저장하면 된다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q --tb=no -rf --color=no \
  | grep '^FAILED' | sort > baseline_failures.txt
```

`--color=no` 는 필수다(ANSI 때문에 `^FAILED` 가 미매치된다).

실측으로 확인된 것:

- `pytest --collect-only -q` → **1,754건 / 4.6초.** 전체를 돌리지 않고 실패 목록을 알 방법은 없다
- **`pytest-randomly` 는 설치돼 있지 않다.** `-p no:randomly` 는 no-op 이고 순서는 이미 결정적이다
- **`pytest-xdist` 는 설치돼 있다.** `-n auto` 로 201초를 크게 줄일 수 있으나, 워커별 프로세스
  분리로 전역 상태(`docling_settings.perf`, 로깅)가 달라진다.
  **먼저 `-n auto` 실패 집합이 순차 실행과 같은지 1회 확인**하고 채택한다

## 하네스를 어디에 둘까

`examples/parse_chunk/` 에 둔다. `parse_chunk_verify.py` 옆에 `parse_chunk_golden.py` 를
만들고 `CASES` 목록을 그것과 공유한다 — 케이스를 두 곳에 적으면 곧 갈라진다.

`run_case`(`parse_chunk_verify.py:602`)를 **그대로 쓸 수 없다.**

- `--llm_cache`/`--workflow_id`/`--interim_root`/`--run_id` 를 **하나도 넘기지 않는다** →
  골든이 요구하는 캐시 고정이 안 걸린다
- `parse_chunk_verify.sh:31` 이 `DYLD_FALLBACK_LIBRARY_PATH` 를 export 한다.
  **없으면 WeasyPrint import 가 실패**해 일부 경로가 조용히 달라진다.
  `--record` 와 `--check` 를 서로 다른 방식으로 부르면 **그 자체가 diff 원인**이 된다

→ `run_case` 에 `extra_args: list[str]` 를 추가해 verify/golden 이 같은 함수를 쓰게 하고,
환경 설정을 `.sh` 가 아니라 `.py` 안(`os.environ.setdefault`)으로 옮긴다.

**케이스 외부 주입**은 선례가 있다 — `examples/config_precheck/precheck_custom_fields.py:174`
의 `--resource-dir` 위치 인자가 같은 문제를 푼 방식이다.
`--cases <yaml|json>` 으로 `[{doc_type, path, note}]` 를 받고 미지정 시 `CASES` 폴백.

**정규화는 별도 모듈** `examples/parse_chunk/golden_normalize.py` 에 둔다.
A/B 대조기는 in-process dict 를, 골든은 파일을 비교하므로 공유해야 하는 것은
"dict → 정규화된 dict" 함수 하나뿐이다.

주의: **A/B 대조기는 정규화를 무조건 걸면 안 된다.** 같은 프로세스·같은 초에 두 번 도는 A/B 는
`reg_date` 가 같을 수 있어 정규화 없이도 통과하는데, 무조건 정규화하면
"`reg_date` 생성 로직 자체가 사라진 회귀" 를 못 잡는다. **정규화 전/후를 둘 다 비교**한다.

세 가지 모드가 필요하다.

| 모드 | 하는 일 |
|---|---|
| `--record` | 전체 케이스를 실행해 골든으로 저장 |
| `--check` | 실행 후 골든과 대조. 차이가 있으면 비-0 종료 |
| `--noise` | 2회 실행해 서로 대조(정규화 규칙 점검용) |

## A/B 대조기와의 관계

[WORKFLOW.md](WORKFLOW.md) 의 A/B 대조기(`parse_chunk_ab.py`)는 **같은 프로세스 안에서**
원본과 임시 작업본을 나란히 돌려 비교한다. 골든 대조는 **시점을 건너뛴** 비교다.

둘 다 쓴다. A/B 는 작업 중 즉시 피드백이고, 골든은 단계 종료 시 최종 확인이다.
정규화 규칙은 여기서 실측으로 확정한 것을 A/B 대조기도 그대로 쓴다 — 두 벌로 두면 갈라진다.

## 고객도 이 하네스를 쓴다

골든 산출물은 저장소에 커밋하지 않으므로 **고객에게는 벤더 골든이 가지 않는다.**
그런데 05·06 은 고객에게 "골든 대조로 회귀를 확인하라" 고 지시한다 — 그대로면 실행 불가능하다.

`--record` 를 **고객이 자기 문서로 돌려 자기 골든을 만드는 것**이 정식 절차임을
고객 문서에 넣는다. 자세한 것은 [07](07-customer-change-lifecycle.md) D 항목.

따라서 하네스는 **케이스 목록을 외부에서 주입받을 수 있어야 한다.** `CASES` 를 코드에
고정하면 고객이 자기 문서로 못 돌린다.

## 이 하네스의 수명

이번 리팩터링이 끝나도 남긴다. 청크 산출을 바꾸는 모든 변경(설정 키 추가, 청커 수정,
docling 갱신)에서 같은 방식으로 영향 범위를 먼저 볼 수 있다.
