#!/usr/bin/env python3
"""분리 배포된 parser/chunker 서비스의 /run 엔드포인트 테스트 도구.

기본 호출 대상:
    parser:  http://preprocessor-695:8080/run
    chunker: http://preprocessor-698:8080/run
    document: /nfs-root/DEV/volume/50/file_test/test_small_page.pdf

처리 흐름:
    parser /run  -> data.document 또는 data.elements
    chunker /run <- params.document

외부 패키지 없이 Python 표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_PARSER_URL = os.environ.get(
    "PARSER_URL", "http://preprocessor-695:8080/run"
)
DEFAULT_CHUNKER_URL = os.environ.get(
    "CHUNKER_URL", "http://preprocessor-698:8080/run"
)
DEFAULT_FILE_PATH = os.environ.get(
    "FILE_PATH", "/nfs-root/DEV/volume/50/file_test/test_small_page.pdf"
)


def _healthcheck_url(run_url: str) -> str:
    """끝의 /run을 /healthcheck로 바꾼다."""
    normalized = run_url.rstrip("/")
    if normalized.endswith("/run"):
        return f"{normalized[:-4]}/healthcheck"
    return f"{normalized}/healthcheck"


def _request_json(
    url: str,
    *,
    label: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"

    request = urllib.request.Request(
        url, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"[{label}] HTTP {exc.code} 오류: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"[{label}] 연결 실패: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SystemExit(f"[{label}] {timeout:g}초 내 응답이 없습니다") from exc

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[{label}] JSON 응답이 아닙니다: {raw_body[:500]}") from exc

    if isinstance(body, dict) and "code" in body:
        if body.get("code") != 0:
            message = body.get("errMsg") or body.get("error_msg") or body
            details = []
            for key in ("stage", "error_kind", "error_code"):
                if body.get(key) is not None:
                    details.append(f"{key}={body[key]}")
            suffix = f" ({', '.join(details)})" if details else ""
            raise SystemExit(
                f"[{label}] 요청 실패(code={body.get('code')}): {message}{suffix}"
            )
        return body.get("data")
    return body


def _output_path(raw_path: str, default_filename: str) -> Path:
    path = Path(raw_path).expanduser()
    if raw_path.endswith(("/", "\\")) or (path.exists() and path.is_dir()):
        path.mkdir(parents=True, exist_ok=True)
        return path / default_filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(value: Any, raw_path: str, default_filename: str) -> Path:
    path = _output_path(raw_path, default_filename)
    with path.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return path


def _extra_params(args: argparse.Namespace) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for raw in args.param:
        if "=" not in raw:
            raise SystemExit(f"--param은 KEY=VALUE 형식이어야 합니다: {raw!r}")
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise SystemExit(f"--param의 KEY가 비어 있습니다: {raw!r}")
        try:
            params[key] = json.loads(value)
        except json.JSONDecodeError:
            params[key] = value
    if args.doc_type:
        params.setdefault("doc_type", args.doc_type)
    return params


def _handle_parser_data(data: Any, out_doc: str | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SystemExit("[parser] data가 JSON 객체가 아닙니다")

    document = data.get("document")
    if isinstance(document, dict):
        pages = (data.get("usage") or {}).get("pages")
        print(f"[parser] docling 문서 수신: pages={pages}")
        forwarded = document
        default_filename = "doc.json"
    elif isinstance(data.get("elements"), list):
        elements = data["elements"]
        pages = (data.get("usage") or {}).get("pages")
        print(f"[parser] parse-format 수신: elements={len(elements)}, pages={pages}")
        # chunker가 data.elements 형식을 판별할 수 있도록 data 전체를 전달한다.
        forwarded = data
        default_filename = "parse.json"
    else:
        raise SystemExit(
            "[parser] 응답에 data.document 또는 data.elements가 없습니다"
        )

    if out_doc:
        path = _write_json(forwarded, out_doc, default_filename)
        print(f"[parser] 문서 저장: {path}")
    return forwarded


def do_health(args: argparse.Namespace) -> int:
    targets = (
        ("parser healthcheck", _healthcheck_url(args.parser_url)),
        ("chunker healthcheck", _healthcheck_url(args.chunker_url)),
    )
    for label, url in targets:
        data = _request_json(url, label=label, timeout=args.timeout)
        print(f"[{label}] {json.dumps(data, ensure_ascii=False)}")
    return 0


def do_parser(args: argparse.Namespace) -> dict[str, Any]:
    if not args.file_path:
        raise SystemExit("parser 모드에는 --file-path가 필요합니다")
    params = _extra_params(args)
    data = _request_json(
        args.parser_url,
        label="parser",
        timeout=args.timeout,
        payload={"file_path": args.file_path, "params": params},
    )
    return _handle_parser_data(data, args.out_doc)


def _load_document(raw_path: str) -> dict[str, Any]:
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise SystemExit(f"chunker 입력 파일이 없습니다: {path}")
    try:
        with path.open("r", encoding="utf-8") as source:
            document = json.load(source)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"chunker 입력이 유효한 JSON이 아닙니다: {path}") from exc

    # parser의 data 객체나 전체 envelope를 저장한 파일도 입력으로 허용한다.
    if isinstance(document, dict) and document.get("code") == 0:
        document = document.get("data")
    if (
        isinstance(document, dict)
        and isinstance(document.get("document"), dict)
        and "schema_name" not in document
    ):
        document = document["document"]
    if not isinstance(document, dict):
        raise SystemExit(f"chunker 입력 JSON이 객체가 아닙니다: {path}")
    return document


def do_chunker(
    args: argparse.Namespace,
    document: dict[str, Any] | None = None,
) -> list[Any]:
    if document is None:
        if not args.doc_json:
            raise SystemExit("chunker 모드에는 --doc-json이 필요합니다")
        document = _load_document(args.doc_json)

    params: dict[str, Any] = {"document": document}
    if args.chunk_size is not None:
        params["chunk_size"] = args.chunk_size

    data = _request_json(
        args.chunker_url,
        label="chunker",
        timeout=args.timeout,
        payload={"file_path": Path(args.file_path).name, "params": params},
    )
    if not isinstance(data, list):
        raise SystemExit("[chunker] data가 청크 배열이 아닙니다")

    print(f"[chunker] 청크 {len(data)}개 생성")
    for chunk in data[:20]:
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text", "")).replace("\n", " ")
        print(
            f"  - [{chunk.get('i_chunk_on_doc')}] "
            f"page={chunk.get('i_page')} {text[:80]}"
        )

    if args.out:
        path = _write_json(data, args.out, "chunks.json")
        print(f"[chunker] 결과 저장: {path}")
    return data


def do_e2e(args: argparse.Namespace) -> int:
    document = do_parser(args)
    do_chunker(args, document)
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="분리 배포된 parser/chunker /run 엔드포인트 테스트",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=("health", "parser", "chunker", "e2e"),
        default="e2e",
        help="실행 모드",
    )
    parser.add_argument(
        "--parser-url", default=DEFAULT_PARSER_URL, help="parser /run URL"
    )
    parser.add_argument(
        "--chunker-url", default=DEFAULT_CHUNKER_URL, help="chunker /run URL"
    )
    parser.add_argument(
        "--file-path",
        default=DEFAULT_FILE_PATH,
        help="parser Pod에서 접근 가능한 테스트 문서 경로",
    )
    parser.add_argument(
        "--doc-json", help="chunker 모드에서 사용할 parser 결과 JSON"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="chunker에 전달할 chunk_size. 생략하면 서버 설정 사용",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="parser params 값. 반복 지정 가능",
    )
    parser.add_argument(
        "--doc-type", help="parser params.doc_type 값(--param doc_type=...도 가능)"
    )
    parser.add_argument(
        "--out-doc", help="parser의 document 또는 parse-format JSON 저장 경로"
    )
    parser.add_argument("--out", help="chunker 결과 JSON 저장 경로")
    parser.add_argument(
        "--timeout", type=float, default=3600.0, help="각 HTTP 요청 제한 시간(초)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.mode == "health":
        return do_health(args)
    if args.mode == "parser":
        do_parser(args)
        return 0
    if args.mode == "chunker":
        do_chunker(args)
        return 0
    return do_e2e(args)


if __name__ == "__main__":
    sys.exit(main())
