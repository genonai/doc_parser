"""평문 텍스트를 docling 입력 HTML 로 감싼다.

## 왜 필요한가

docling 에는 평문 텍스트 백엔드가 없다. 확장자 매핑상 `.txt` 는 XML_USPTO(특허 XML),
`.json` 은 JSON_DOCLING(docling 자체 직렬화 형식)으로 가므로 원문을 그대로 넘길 수 없다.
텍스트를 HTML 로 감싸 HTML 백엔드에 태우는 것이 유일한 경로이며, custom_fields 를 쓰는
`.json`(`processing/converters/json_text.py`)이 이미 같은 방식을 쓴다.

## 왜 `<pre>` 인가

세 가지 표현을 실제로 태워 비교한 결과다.

  <pre>          item 1개, 라벨 code  — 줄바꿈 보존, 들여쓰기 보존
  줄마다 <p>     item N개, 라벨 text  — 줄바꿈 보존, 들여쓰기 소실
  <div> + <br>   item 1개, 라벨 text  — 줄바꿈 소실(한 줄로 합쳐짐)

원문 충실도가 기준이라 `<pre>` 를 쓴다. 대신 docling 이 `<pre>` 를 코드 블록(CodeItem)으로
읽으므로, 호출부가 `docling_ops.demote_code_items` 로 일반 텍스트 라벨로 되돌린다.
평문을 markdown 으로 해석시키는 선택지는 두지 않는다 — `- item` 으로 시작하는 평문이
md 백엔드에서 본문을 잃은 전례가 있다.

## 스타일 근거 (이슈 #333)

`<pre>` 기본값 `white-space: pre` 는 자동 줄바꿈을 하지 않아, A4 폭을 넘는 긴 줄이
weasyprint 렌더 단계에서 잘려 본문이 누락됐다. docling HTML 백엔드는 렌더를 하지 않아
이 문제와 무관하지만, 같은 HTML 을 렌더 경로(attachment)도 쓰므로 스타일을 유지한다.
"""
from __future__ import annotations

import html

# white-space: pre-wrap   원문 줄바꿈/공백 유지 + 폭 초과 시 자동 줄바꿈
# overflow-wrap: anywhere 공백 없는 초장문(URL 등)도 강제 개행
_PRE_STYLE = "white-space: pre-wrap; overflow-wrap: anywhere;"


def text_to_html(text: str) -> str:
    """평문 텍스트를 `<pre>` 한 벌로 감싼 HTML 문서를 돌려준다.

    html.escape 는 필수다. `<`, `&` 가 태그로 해석되면 그 뒤 텍스트가 통째로 유실된다.
    """
    return (
        "<html><meta charset='utf-8'><body>"
        f"<pre style='{_PRE_STYLE}'>"
        f"{html.escape(text)}</pre></body></html>"
    )
