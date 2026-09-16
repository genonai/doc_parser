# 09. 흐름이 보이는 facade — 처리 순서를 파사드가 갖는다

전제: [08](08-customer-facade.md) 완료(파싱·청킹 파사드가 101·123줄로 축소, 훅 5개 + core 분리).

**이 문서 하나로 다른 세션이 이어받을 수 있게 쓴다.** 결정되지 않은 것과 확정된 것을
구분해 적고, 착수 지점과 관문을 명시한다.

## 왜 또 하는가

08 이 만든 파사드는 작지만 **흐름이 안 보인다.** `__call__` 이 사실상 한 줄
(`await self.run(...)`)이고 그 안에 확장자 판별·라우팅·파싱·enrichment·응답 조립이 다 들어 있다.
훅 이름은 보이지만 **그 훅이 무엇과 무엇 사이인지**가 파사드에 없다.

고객 개발자 역할극으로 확인한 결과다.

> "표 설명을 우리 규칙으로 바꾸고 싶다" 는 요청에서, 그 일이 어느 단계에서 벌어지는지
> 파사드만 보고는 알 수 없었다. 매뉴얼 473줄을 읽거나 열지 말라고 한 core 를 열어야 했다.

한편 core 는 이미 단계로 갈라져 있다. 라우트 8개의 본문이 3~15줄이고 전부 같은 모양
(만들고 → 설명 붙이고 → 조립한다)이며, 문서를 만드는 5개는 `_docling_response()` 하나를,
행·레코드를 만드는 2개는 `_describe_record_tables()` 를 공유한다.
**없는 것은 구조가 아니라 그 구조를 보여 주는 자리다.** 그래서 로직을 옮기지 않고
흐름을 읽는 줄만 파사드로 올릴 수 있다.

## README 원칙과의 관계 (중요)

README 의 원칙 2 는 "새 설정 키·확장 메커니즘·플러그인 지점을 만들지 않는다" 이고
08 은 그것을 한 번 깨면서 훅 4개 + ClassVar 2개 + toolbox 1개로 한정했다.
**09 는 두 가지를 더 신설한다.**

| 신설 | 무엇 | 근거 |
|---|---|---|
| `job` 객체 | 요청 한 건의 상태를 담는 값 객체 | 단계마다 인자가 달라 흐름이 표처럼 안 읽힌다. 훅 사이 값 전달 통로가 없다 |
| 문서 종류별 설정 오버레이 | `CONFIG_BY_DOC_TYPE` + `config_for()` | 검증에서 나온 **치명 약점 1**. 아래 참조 |

그 밖은 전부 **이동**이다. 골든 차이 0 을 매 커밋 관문으로 다시 건다.

## 검증 결과 — 이 계획의 근거

역할극 2회. 실제로 파일을 열고 요건을 읽고 어디를 고칠지 답하게 했다.
막히면 core 를 열어도 되지만 열었으면 보고하게 했다 —
설계 의도가 "두 파일만으로 일한다" 이므로 **다른 곳을 열어야 했다는 사실 자체가 결과**다.

| 회차 | 누가 | 무엇을 | 결과 |
|---|---|---|---|
| 1차 | 3년차 1명 | 업무 요청 7건 | 5건 해결 · 1건 헤맴 · 1건 실패 → 문서 결함 3건 |
| 2차 | 2년차 1명, 3년차 1명 (독립) | 요건 14건([09-requirements.md](09-requirements.md)) | 9건 가능 · 5건 불가. **설정만으로 끝나는 요건 0건** |
| 3차 | 10년차 1명 | 처리 기법 20건([09-special-cases.md](09-special-cases.md)) | 실제로 파싱·청킹해서 판정. 훅 5종만으로 11건 · 라우트/단계 오버라이드 4건 · 이미 되는 것 2건. **yaml 이 반드시 필요한 것 0건** |

요건 14건은 현업 요건 담당자 역할이 작성했다(해결 방법을 못 쓰게 하고, 난이도를 섞음 —
전부 어렵게 만들면 "다 안 된다" 는 쓸모없는 결론만 나온다).

### 문서 결함 4건 — 제안 코드에 반영 완료

| # | 결함 | 조치 |
|---|---|---|
| D | `job.notes` 를 쓰라면서 훅 인자에 `job` 이 없다 | `kwargs["job"]` 으로 꺼낸다고 명시 + 예시 |
| E | 이미 있는 기능(마커 heading 승격 등)을 몰라 새로 짤 뻔했다 | 파일 첫머리에 "이미 있는 기능부터" 목록 |
| F | 실패를 어떻게 알리는지가 없다(`GenosServiceException` 미등장) | 공통 규칙 4번 + 부분 실패 보고 예시 |
| G | 파서의 `job` 과 청커의 `job` 이 다른 객체인데 설명이 없다 | 명시 + `kind` 대응표 |

D 와 E 는 **두 사람이 독립적으로 같은 것을 지적**했다.

### 구조적 약점 5건

안 되는 5건은 전부 **"하나" 라는 전제가 실은 "여럿·부분·다른 것" 이어야 하는 자리**다.

| 약점 | 요건 | 코드 근거 | 심각도 | 09 범위 |
|---|---|---|---|---|
| 설정이 문서 종류별로 안 갈린다 | 14, 6 | `table_description`·`ocr`·`layout` 은 전역 단일 블록. `doc_type` 축은 `custom_fields` 뿐. kwargs 오버라이드는 `resolve_*` 키마다 함수 하나인 화이트리스트 | 치명 | **포함** |
| 파일 하나 = 결과 한 벌 | 9 | 라우트가 평면 `{"elements": [...]}` 하나를 반환. 첨부 N개(압축 안 포함)를 담을 자리 없음 | 치명 | 제외 |
| "부분 성공" 상태가 없다 | 12 | `main.py` 의 `request_deadline` 은 `asyncio.wait_for` 로 취소하고 실패 응답. 페이지 단위 훅 없음 | 치명 | 제외 |
| 표를 행 단위와 전체로 동시에 못 낸다 | 7 | 자르는 규칙이 `smart_chunker.py` 안. `set_table_info` 는 한 표가 한 방식으로 나뉜 순번만 추적 | 중요 | 제외 |
| 확장자 판정이 정확일치 1:1 | 1 | `format_alias._normalize_ext` 가 점 2개 이상을 거부하고 별칭 연쇄를 안 따름. `.pdf.v3` 와 `.docx.v12` 는 같은 꼬리표 뒤 원본 확장자가 다름 | 중요 | 제외 |

제외 4건은 각각 라우트 계약·실행 모델·청커 본체·별칭 구현을 건드려야 해서
흐름 공개와 같이 가면 둘 다 늦어진다. **별도 이슈로 세운다.**

### 우려했지만 괜찮았던 것

- 문서 단위 값과 조각 단위 값의 구분 — `set_chunk_metadata` 와 `build_row` 두 경로가 이미 갈려 있다
- 다른 시트·외부 파일 참조 — `pre_source` 가 엑셀 전 시트를 한 번에 받고, 훅에서 코드표 등을 열 수 있다

## 제안 형태

[09-proposed/parser_processor.py](09-proposed/parser_processor.py) (288줄) ·
[09-proposed/chunking_processor.py](09-proposed/chunking_processor.py) (241줄).
**미구현 설계본이다.** 참조하는 core 함수(`start_job`, `enrich`, `row_builder` 등)는 아직 없다.
[09-special-cases.md](09-special-cases.md) 의 반영 목록 ③⑤⑥⑦ 이 이 두 파일에 들어가 있고,
①②④ 는 `tb.html_to_text` · `tb.split_row` · `tb.merge_small_rows` · `tb.drop_fields` 라는
**아직 없는 toolbox 이름**으로 등장한다 — 그 별도 이슈가 닫혀야 예시가 성립한다.
설계에 하나 더 필요해진 것: `parse_document(job, markup, ext=...)` 로 "이 입력을 어느 포맷으로
볼지" 를 넘길 수 있어야 한다(JSON 을 마크다운으로 내려놓고 문서 경로에 태우는 레시피).

파일은 네 덩어리이고 위에서 아래로 읽으면 된다.

```
1부 흐름 · 파일 종류별 처리     읽기만
2~3부 문서 종류별 설정          값만 적는 곳 (신설)
4부 고치는 자리                 훅 3개
5부 단계 통째로 바꾸기          1부 함수를 같은 이름으로 재정의
```

핵심은 **설정이 훅보다 앞**이라는 것이다. 검증에서 설정만으로 끝나는 요건이 0건이었고,
읽는 순서가 곧 우선순위이기 때문이다.

### 흐름

```python
async def __call__(self, request, file_path, **kwargs):
    job = self.start_job(request, file_path, **kwargs)  # ① 확장자·깨진 파일·문서별 설정
    job.source = await self.run_pre_source(job)         # ② pre_source()
    result = await self.run_route(job)                  # ③ ROUTES 에서 골라 실행
    return await self.run_post_parse(job, result)       # ④ post_parse()

async def document_to_response(self, job, doc):         # 문서를 만드는 라우트 5개가 공유
    doc = self.on_document(job, doc)                    # [신설 훅] 자동 설명 전
    doc = await self.enrich(job, doc)
    return self.build_response(job, doc)
```

청커는 `build_rows()` 가 한 청크가 DB 행이 되기까지 6줄을 그대로 보여 준다 —
본문 조립 → `on_chunk` → 마스킹 → 정리 → 행 만들기 → 순번 매기기.
**이 순서가 검색 품질을 좌우하는데 지금은 core 안에 있다.**

### 설정 오버레이 (신설)

```python
CONFIG_BY_DOC_TYPE = {
    "press":    {"enrichment.table_description.enable": False,
                 "chunking.chunk_size": 500},
    "contract": {"ocr.ocr_mode": "force"},
}

def config_for(self, job):     # 표로 안 되는 경우(문서 내용으로 판별)
    return {}
```

- 키는 설정 파일 경로를 점으로 이어 쓴다. **설정 파일에 있는 항목이면 무엇이든** 된다.
- 적용 순서: `설정 파일 → CONFIG_BY_DOC_TYPE → config_for() → 요청이 보낸 값`
- 적용값은 `self` 가 아니라 **`job.config`** 에 실린다. 요청 스코프라 동시 처리에 안전하고,
  결과에 기록되므로 "이 문서가 어떤 설정으로 처리됐는지" 추적된다.
- `custom_fields` 가 이미 "doc_type 스코프 등록 리스트" 를 증명해 뒀으므로
  **새 개념이 아니라 기존 패턴의 일반화**다. CLAUDE.md 의 "설정 키를 늘리기보다
  일반화된 메커니즘" 원칙과도 맞는다.

## 확정 필요한 결정 4건

착수 전에 정해야 한다. 권고를 함께 적는다.

| # | 결정 | 권고 | 근거 |
|---|---|---|---|
| 1 | `job` 상자를 둘 것인가 | **둔다** | 모든 단계가 같은 인자를 받아 흐름이 표처럼 읽힌다. 훅 사이 값 전달과 ctx 문서화가 덤. 검증자 둘 다 `job` 자체를 어려워하지 않았다 |
| 2 | 라우트 8개를 다 올릴 것인가 | **다 올린다** | 검증에서 `.tsv` 과제를 ROUTES 표 덕분에 바로 풀었다. docling 경로만 올리면 엑셀·JSON 고객은 다시 매뉴얼로 간다 |
| 3 | 나머지 파사드 3종(첨부·변환·지능형)도 같이 갈 것인가 | **두고 본다** | 파싱·청킹이 고객 확장의 주 무대다. 먼저 검증하고 확대한다 |
| 4 | 설정 오버레이를 이번에 같이 할 것인가 | **같이 한다** | 치명 3건 중 유일하게 기존 패턴의 일반화로 풀린다. 흐름 공개와 독립이라 병행 가능 |

## 이행 순서

**0~2 는 결정 없이 진행할 수 있다.**

| 단계 | 내용 | 필요 결정 | 관문 |
|---|---|---|---|
| 0 | 골든 기준선 확정 | — | `--check` 통과 또는 HEAD 기준 `--record` |
| 1 | 무위험 문서 수정 — 결함 E·F 를 **현행** facade 2개 + `facade_hooks.md` 에 반영 | — | 코드 동작 무변화(주석만) |
| 2 | `job` 도입 + `start_job`/`run_route`. **라우트는 옛 시그니처 유지** | 1 | 골든 차이 0 |
| 3 | `_docling_response` 를 `on_document`·`enrich`·`build_response` 로 분해해 파사드로 올림 | 1 | 골든 차이 0 |
| 4 | 라우트 8개 이관 (본문은 위임 줄) | 2 | 골든 차이 0 |
| 5 | `compose_vectors` 173줄 해체 → `build_text`·`build_row`·`number_rows` | 1 | **골든 차이 0 — 유일한 실위험** |
| 6 | 설정 오버레이 (`job.config` + 병합 순서 + 결과 기록) | 4 | 골든 차이 0 + doc_type별 적용 단정 테스트 |
| 7 | `facade_hooks.md` 를 이 흐름 기준으로 다시 씀 | — | — |

**2단계는 라우트 하나만 옮긴다.** `route_hwp` 가 3줄로 가장 짧다.
8개를 한 번에 옮기고 골든이 깨지면 원인이 8곳으로 흩어진다. 왕복을 한 번 성립시킨 뒤
나머지를 붙이면 깨져도 직전 한 줄이 범인이다.

6단계는 흐름 공개와 독립이라 사람이 둘이면 병행할 수 있다.

## 착수 지점

```bash
cd genon/preprocessor
examples/parse_chunk/parse_chunk_golden.py --check     # HEAD 가 기존 골든과 맞는가
examples/parse_chunk/parse_chunk_golden.py --record    # 어긋나면 HEAD 기준 재기록
```

**주의.** `~/.cache/doc_parser/parse_chunk_golden/` 에 2026-09-06 기록분(384파일)이 있으나,
그 뒤 산출을 바꾸는 커밋이 들어갔다(`73d99c31` 블록 수식, `252d3dc4` 펜스 전처리,
`32260c91` md 수식). **`--check` 가 깨지는 것이 정상일 수 있으므로 먼저 확인하고 재기록한다.**
골든은 git 미추적이고 머신 로컬이며 기록 당시 설정이 박힌다.

## 좌표 (다른 세션이 바로 찾을 것들)

| 무엇 | 어디 |
|---|---|
| 현행 파사드 | `facade/parser_processor.py`(101줄) · `facade/chunking_processor.py`(123줄) |
| 처리 본체 | `processing/core/parser.py`(1,798) · `processing/core/chunker.py`(1,289) |
| 분해 대상 | `core/parser.py` `_docling_response`(1477) · `core/chunker.py` `compose_vectors`(433~606) |
| 훅 호출 규약 | `processing/common/hooks.py` — `hook_kwargs()` 가 사본을 만든다(약점 근거) |
| 확장자 별칭 | `processing/common/format_alias.py` `_normalize_ext` |
| 설정 해석 | `processing/common/config_parse.py` — `resolve_*` 화이트리스트 |
| 고객 매뉴얼 | `facade/gitbook_doc/facade_hooks.md`(473줄) — 7단계에서 갱신 |
| 훅 계약 테스트 | `tests/unit/test_facade_hooks_unit.py`(753줄) — 단계마다 늘린다 |
| 요건 14건 | [09-requirements.md](09-requirements.md) |
| 특이 케이스 20건 판정 | [09-special-cases.md](09-special-cases.md) — 반영 목록 7건이 그 문서 끝에 있다 |
| 제안 코드 | [09-proposed/](09-proposed/) |

검토 자료(외부 링크, 세션 산출):
- 확장 표면 지도 — https://claude.ai/code/artifact/89bb27e5-a081-4cb9-955b-7a29667c0893
- 흐름이 보이는 파사드(이 문서의 시각본) — https://claude.ai/code/artifact/5c2fe98a-87fc-4a3d-9232-020e52f67d1f

## 치러야 할 값

- **지켜야 할 함수 이름이 5개에서 20개 안팎으로 는다.** 단계 이름과 인자가 공개 약속이 되고
  릴리스마다 지켜야 한다. `test_facade_hooks_unit.py` 를 그만큼 늘린다.
- **기존에 직접 만든 라우트가 깨진다.** `route_x(self, file_path, ext, ctx, **kwargs)` →
  `route_x(self, job)`. 옛 형태를 한 릴리스 동안 어댑터로 함께 받는 것을 권한다.
- **파사드 5종의 인상이 갈린다.** 결정 3을 "두고 본다" 로 하면 파싱·청킹만 이 형태가 된다.
- **"단계 통째로 바꾸기" 는 양날이다.** 고객이 `super()` 를 안 부르고 기본 동작을 통째로
  날려도 막을 방법이 없다. 문서에 "기본 동작을 먼저 실행하라" 를 예시로 박아 두는 정도가 최선.

## 범위 밖 — 별도 이슈로 세울 것

| 주제 | 필요한 것 | 관련 요건 |
|---|---|---|
| 한 파일 안의 하위 문서 재귀 파싱 | 라우트가 자식 입력을 선언하고 core 가 같은 ROUTES 로 재귀 처리 | 9 (이메일+첨부) |
| 부분 성공 | 페이지 단위 체크포인트 + deadline 도달 시 실패 대신 부분 완료 응답. docling 파이프라인 내부를 건드려야 해서 "docling 은 되도록 수정하지 않는다" 와 충돌 | 12 |
| 표 이중 표현 | `smart_chunker` 가 행 분할과 전체 보존을 동시 방출 + `table_view` 구분 필드 | 7 |
| 확장자 접미사 스트립 | 확장자 판정 앞에 정규식 접미사 제거 계층 | 1 |

## 다음 세션이 확인할 것

0. [09-special-cases.md](09-special-cases.md) 의 "09 설계에 반영할 것" 7건 —
   ③(job 에 file_path 명시)은 설계, ⑤⑥⑦ 은 제안 파일 머리 주석, ①②④ 는 별도 이슈 후보다
1. 결정 4건이 정해졌는가 (안 정해졌으면 0~1단계만 진행 가능)
2. 골든 `--check` 상태 — 통과인가, 재기록했는가, 어느 커밋 기준인가
3. 1단계(문서 수정)가 커밋됐는가 — 현행 facade 2개 + `facade_hooks.md`
4. 브랜치 — 이 작업은 `task/363-facade-slimming` 의 연장이거나 새 이슈다.
   새로 판다면 `/issue-start` 로 이슈부터 만든다(컨벤션: `<type>/<이슈번호>-<slug>`)
