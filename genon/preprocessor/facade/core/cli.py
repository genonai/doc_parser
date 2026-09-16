"""파일 단독 실행 진입점 (#363 08-3).

FastAPI 없이 프로세서를 직접 돌려 본다. 배포되는 facade 는 두 줄만 갖는다.

    if __name__ == "__main__":
        DocumentProcessor.cli()

파서 산출은 dict, 청커 산출은 list[VECTOR_META] 라 양쪽을 알아서 직렬화한다.
두 배포본이 그대로 이어진다.

    python preprocessor.py 지점현황.xlsx --doc-type branch_list -o parsed.json   # 파서
    python preprocessor.py parsed.json -o chunks.json                            # 청커

--doc-type 이 사실상 필수다. 훅이 전부 doc_type 게이팅이라 없으면 훅을 못 시험한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time


def mock_request():
    """FastAPI Request 최소본. 프로세서는 업로드/취소 확인에만 쓴다.

    클래스를 직접 부를 때도 쓴다. 청커는 첫 인자를 미디어 업로드에 넘기므로 None 을 주면
    거기서 실패한다. toolbox 가 tb.mock_request 로 재수출한다.
    """
    from fastapi import Request

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope={
        "type": "http", "method": "POST", "path": "/", "headers": [],
        "query_string": b"",
    }, receive=_receive)


def _to_jsonable(result):
    if isinstance(result, list):
        return [r.model_dump() if hasattr(r, "model_dump") else r for r in result]
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return result


def _summary(payload) -> str:
    """산출 모양에 맞는 한 줄 요약.

    세는 대상이 형식마다 다르다. 요소 개수만 세면 문서형 파싱이 늘 0건으로 보인다 —
    정상 처리된 문서를 실패로 오해하게 된다.
    """
    if isinstance(payload, list):
        return f"청크 {len(payload)}건"
    if not isinstance(payload, dict):
        return "산출 1건"
    elements = payload.get("elements") or []
    if elements:
        return f"요소 {len(elements)}건"
    document = payload.get("document") or {}
    if document:
        return (f"문서형 · 텍스트 {len(document.get('texts') or [])}개"
                f" · 표 {len(document.get('tables') or [])}개")
    return "산출 없음"


def run_cli(processor_cls, argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=f"{processor_cls.__module__}.{processor_cls.__name__} 단독 실행")
    ap.add_argument("file", help="입력 파일(파서: 원천 / 청커: 파서 결과 .json)")
    ap.add_argument("--doc-type", default=None, help="custom_fields doc_type. 훅 게이팅에 쓰인다")
    ap.add_argument("--config", default=None, help="프로세서 설정 yaml. 미지정 시 기본 해석")
    ap.add_argument("-o", "--out", default=None, help="결과 JSON 경로. 미지정 시 stdout")
    ap.add_argument("--log-level", type=int, default=None,
                    help="5 DEBUG / 4 INFO / 3 WARNING / 2 ERROR / 1 CRITICAL / 0 끔")
    args = ap.parse_args(argv)

    proc = processor_cls(config_path=args.config) if args.config else processor_cls()
    kwargs: dict = {}
    if args.doc_type:
        kwargs["doc_type"] = args.doc_type
    if args.log_level is not None:
        kwargs["log_level"] = args.log_level

    begin = time.time()
    result = asyncio.run(proc(mock_request(), args.file, **kwargs))
    payload = _to_jsonable(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        where = args.out
    else:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        where = "stdout"

    print(f"\n[cli] {where} · {_summary(payload)} · {time.time() - begin:.1f}초", file=sys.stderr)
    return 0
