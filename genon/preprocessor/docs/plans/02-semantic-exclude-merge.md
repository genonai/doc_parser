# 02. sections 의 제외 수단을 `ignore_keys` 하나로

전제: [README.md](README.md) 의 "공통 규칙" 을 먼저 읽는다.

## 보고된 요구

> semantic 타입: `sections.<key>.include false` 로 지정하는 것과 `ignore_keys` 로 지정하는 것의
> 차이가 없는 것 같음. 하나로 합쳐도 무방하다면 하나로 정리하면 좋겠다.
> `include: false` 인 경우에 name 이 필요하지 않은 것 같은데.

## 현재 동작 — 차이 없음을 확인했다

둘 다 같은 루프 안에서 앞뒤로 서브트리를 통째로 건너뛴다.

```python
# facade/enrichment/json_semantic.py  _walk() 안
if _ignored(key, ctx.ignore_keys):        # 464행 — fnmatch(glob), 대소문자 구분
    continue
...
child_key, child_name, include = _resolve_child_context(...)
if not include:                            # 479행 — sections[key].include
    continue
```

확인한 사실 세 가지.

1. **`include: false` 인 항목의 `name` 은 죽은 값이다.** `_resolve_child_context` 가 돌려주지만
   바로 다음 줄에서 `continue` 라 쓰이는 곳이 없다.
2. **제외해도 metadata 는 안전하다.** 공통 필드는 `_find_root_field(payload, aliases)` 로
   원천 payload 루트에서 직접 찾으므로(712행) 본문 순회와 무관하다. 두 방식 모두 동일하다.
3. **정확한 이름을 `ignore_keys` 에 넣어도 동작한다.** fnmatch 는 와일드카드가 없으면 정확 일치다.

실질 차이는 `ignore_keys` 만 glob 을 쓸 수 있다는 것 하나뿐이다.

`resource/custom_field_product_hpp_semantic.yaml` 이 이미 두 방식을 섞어 쓰고 있다 —
`include: false` 항목과 `ignore_keys` 목록이 같은 파일에 나란히 있다.

## 바꿀 것

제외는 `ignore_keys` 로 모으고, `sections` 는 **순수한 이름표**로 만든다.
그러면 중첩 object 자체가 필요 없어져 문자열로 납작해진다.

```yaml
# 전
source:
  sections:
    benefitList:   {name: 혜택, include: true}
    feeUrl:        {name: 연회비, include: true}
    recommendList: {name: 추천상품, include: false}   # name 은 안 쓰인다
  ignore_keys: ['*Img*', fontColor, bgColor]

# 후
source:
  sections:                    # 표시 이름을 붙일 key 만. 없으면 자동(값 안의 제목 -> key 이름)
    benefitList: 혜택
    feeUrl: 연회비
  ignore_keys:                 # 검색에서 뺄 key. 이 대상이 아닌 내용 + 쓸모없는 값
    - recommendList
    - '*Img*'
    - fontColor
    - bgColor
```

1. `sections` 값으로 **문자열**을 받는다. `{name: X, include: true}` object 도 계속 받아
   하위호환을 유지하되, `include: false` 는 `normalize()` 에서 `ignore_keys` 항목으로 옮긴다.
2. `sections` 를 **선택**으로 바꾼다. 지금은 비어 있으면 기동이 실패한다
   (`json_semantic.py:610` "json_semantic custom_fields 에는 sections 가 필요합니다").
   제외만 필요한 설정이 정상적으로 존재할 수 있다.
3. `include` 를 어휘에서 지운다.
4. `resource/custom_field_product_hpp_semantic.yaml` + `resource_dev/` 사본을 새 표기로 옮긴다.
5. `resource/templates/custom_field_TEMPLATE_semantic.yaml` 을 고친다.
   - "① fields ② source.sections ③ source.ignore_keys" 세 항목 설명
   - **"여기 안쪽 오타는 검사되지 않으니 철자에 주의" 경고문을 지운다** — 값이 문자열이 되면
     틀릴 하위 키가 없다. 이 구멍이 부수적으로 막히는 것이 이 작업의 덤이다.
   - `include: false` 의 대표 사례(추천 상품·관련 문서 같은 "다른 대상") 설명을
     `ignore_keys` 쪽 주석으로 옮긴다. 사람이 판단해야 하는 부분이라 유지 가치가 있다.

## 바꾸지 말 것

- **`sections` 키에 fnmatch 를 허용하지 않는다.** 이 작업의 목적은 제외 수단 통합이지
  매칭 규칙 변경이 아니다. `ignore_keys`(fnmatch)와 `sections`(정확 일치)의 규칙이 갈린 채
  남는 것은 알고 있고, README 의 "별도 이슈" 에 적혀 있다.
- 이름 상속(컨테이너 하나 = SECTION_NM 하나) 동작을 건드리지 않는다.
- `on_missing`, `require`, 공통 필드 해석 경로는 이 작업과 무관하다.

## 영향 파일

- `facade/enrichment/json_semantic.py` (sections_cfg 파싱 609~622, `_resolve_child_context` 306~322)
- `facade/enrichment/config_v2.py` (`_SOURCE_TO_V1`, `_normalize_source` 에 include→ignore 이관)
- `resource/custom_field_product_hpp_semantic.yaml` + `resource_dev/` 사본
- `resource/templates/custom_field_TEMPLATE_semantic.yaml`

## 검증

### 테스트 데이터 (실제 문서로 검증한다)

01 과 같은 `product_hpp` 3건을 쓴다.

| 용도 | 파일 |
|---|---|
| `include: false` + `ignore_keys` 를 실제로 섞어 쓰는 원천 | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_wcms_sample.json` |
| 최소 구조 | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_sample.json` |
| rich cell 표 | `genon/preprocessor/sample_files/monimo/monimo_product_hpp_rich_table_sample.json` |

`monimo_product_hpp_wcms_sample.json` 이 핵심이다 — 현행 yaml 이 두 방식을 나란히 쓰고 있어
이관 전후 대조가 그대로 된다. 새로 만들 것은 없다.

```bash
cd genon/preprocessor
.venv/bin/python -m pytest tests/unit -q -p no:randomly --color=no \
  -k 'semantic or config_v2'
```

```bash
genon/preprocessor/examples/config_precheck/verify_v2_equivalence.sh
genon/preprocessor/examples/parse_chunk/parse_chunk_verify.sh --only product_hpp
```

이관이 정확한지는 **섹션 개수와 SECTION_NM 목록**으로 본다.
청크 본문 전문을 출력하지 말고 단정문으로 고정한다.

## 재색인

정확히 옮기면 청크는 **동일**해야 한다. 달라지면 이관이 틀린 것이다.
`*Img*` 같은 glob 과 새로 옮긴 정확 이름이 겹쳐 의도보다 많이 잘리는 경우를 특히 본다.
