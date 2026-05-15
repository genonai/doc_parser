# 버그: 섹션 헤더 텍스트 중복 삽입

## 발생 상황

`attachment_processor.py`의 `DocxProcessor`, `HwpProcessor`로 아래 파일을 처리했을 때,
최종 벡터의 `text` 필드에 섹션 헤더가 두 번 들어가는 문제가 발생했다.

- `genon/preprocessor/sample_files/docx_sample.docx`
- `genon/preprocessor/sample_files/hwpx_sample.hwpx`

**실제 출력 예시 (hwpx_sample.hwpx)**

```
"1. 혼인 건수\n1. 혼인 건수"
"2. 유형별 혼인\n2. 유형별 혼인"
"1. 사망자 수\n1. 사망자 수"
```

---

## 원인 코드

`DocxProcessor.compose_vectors` (및 `HwpProcessor` 동일)에서 content를 아래와 같이 조립한다.

```python
content = self.safe_join(chunk.meta.headings) + chunk.text
```

`HierarchicalChunker`는 섹션 헤더 아이템을 청크로 만들 때 `chunk.text`와 `chunk.meta.headings`에 **동일한 값**을 넣는다.

```python
# HierarchicalChunker 내부
text = ''.join(str(value) for value in heading_by_level.values())  # "1. 혼인 건수"

DocChunk(
    text=text,                                          # "1. 혼인 건수"
    meta=DocMeta(
        headings=[...heading_by_level values...],       # ["1. 혼인 건수"]
    ),
)
```

따라서 `safe_join(headings)` + `chunk.text` 를 합치면 헤더가 두 번 들어간다.

---

# 신 processor(test_attachment_processor)도 동일한 문제

## 원인 코드

`metadata/builder.py:330`에서 content를 아래와 같이 조립한다.

```python
headers_text = "HEADER: " + ", ".join(chunk.meta.headings) + "\n" if chunk.meta.headings else ""
content = headers_text + chunk.text
```

섹션 헤더 청크의 경우 `chunk.meta.headings`와 `chunk.text`가 동일한 값이므로 헤더가 두 번 들어간다.

**실제 출력 예시**

```
"HEADER: 1. 혼인 건수\n1. 혼인 건수"
"HEADER: 2. 유형별 혼인\n2. 유형별 혼인"
```

구 processor와 포맷만 다를 뿐(`HEADER:` 프리픽스 유무) 중복 문제는 동일하다.

## 미결 질문

1. `HEADER:` 프리픽스가 의도된 설계인가?
2. 섹션 헤더 청크에서 `chunk.text == chunk.meta.headings` 인 경우 헤더를 중복으로 붙이지 않도록 수정해야 하는가?
