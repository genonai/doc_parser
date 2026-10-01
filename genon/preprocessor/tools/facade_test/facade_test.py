"""서버 없이(in-process) facade 하나를 파일·디렉터리 단위로 실행하는 업무 테스트 러너.

facade 의 DocumentProcessor 를 직접 import 해 `await doc_processor(mock_request, file_path, **kwargs)`
로 호출한다. 기본 전처리기 서비스의 `/run` 진입과 같은 경로이며 uvicorn/게이트웨이는 필요 없다.
파싱 → 청킹 2단계 파사드 검증은 `tools/parse_chunk/parse_chunk_test.py` 를 쓴다.

사용:
    # 기본: 지능형 facade
    python facade_test.py <input_file|dir> <output_dir>

    # facade 선택(이름 또는 파일 경로)
    python facade_test.py --facade convert <input> <output_dir>
    python facade_test.py --facade genon/sites/<site>/facade/<파일>.py <input> <output_dir>

    # facade kwargs 전달(반복 지정, 값은 JSON 으로 해석하고 실패하면 문자열)
    python facade_test.py <input> <output_dir> --kw toc=1 --kw llm_cache=1 --kw workflow_id=wf-1

출력:
    단일 파일 입력은 <output_dir>/result.json, 디렉터리 입력은 입력 구조를 유지한 <상대경로>.json.
    실패한 파일은 <output_dir>/failed_files.json 에 모은다(--fail-fast 면 첫 실패에서 중단).
"""

import os
import sys
import json
import time
import asyncio
import argparse
import importlib
import importlib.util
import traceback
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[3]
PREPROCESSOR_SRC = PROJECT_ROOT / "genon" / "preprocessor" / "src"
FACADE_DIR = PROJECT_ROOT / "genon" / "preprocessor" / "facade"
for path in (PREPROCESSOR_SRC, PROJECT_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)  # doc_parser 루트 / preprocessor src 참조

from fastapi import Request

# 디렉터리 입력에서 고를 확장자. 단일 파일 입력은 확장자와 무관하게 facade 에 넘긴다.
DIR_EXTENSIONS = {
    ".pdf", ".docx", ".hwp", ".hwpx",
    ".csv", ".xlsx",
    ".doc", ".ppt", ".pptx",
    ".txt", ".json", ".md",
    ".html", ".htm",
}


def facade_names() -> list[str]:
    return sorted(p.stem.removesuffix("_processor") for p in FACADE_DIR.glob("*_processor.py"))


def load_processor_class(spec: str):
    """`intelligent` 같은 facade 이름 또는 .py 파일 경로에서 DocumentProcessor 를 읽는다."""
    if spec in facade_names():
        module = importlib.import_module(f"genon.preprocessor.facade.{spec}_processor")
    else:
        path = Path(spec).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"facade 를 찾을 수 없습니다: {spec} (이름: {', '.join(facade_names())} 또는 .py 경로)")
        # 사이트 전용 facade 는 같은 폴더의 모듈을 import 하는 경우가 있다.
        sys.path.insert(0, str(path.parent))
        module_spec = importlib.util.spec_from_file_location(path.stem, path)
        if module_spec is None or module_spec.loader is None:
            raise SystemExit(f"Python 모듈로 읽을 수 없습니다: {path}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    if not hasattr(module, "DocumentProcessor"):
        raise SystemExit(f"DocumentProcessor 가 없습니다: {spec}")
    return module.DocumentProcessor


def parse_kw(items: list[str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--kw 는 key=value 형식이어야 합니다: {item}")
        try:
            kwargs[key] = json.loads(raw)
        except json.JSONDecodeError:
            kwargs[key] = raw
    return kwargs


def collect_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        files = sorted(
            p for p in input_path.rglob("*")
            if p.is_file() and p.suffix.lower() in DIR_EXTENSIONS
        )
        if not files:
            raise SystemExit(f"처리 가능한 파일이 없습니다: {input_path} (확장자: {', '.join(sorted(DIR_EXTENSIONS))})")
        return files
    raise SystemExit(f"입력 경로를 찾을 수 없습니다: {input_path}")


def to_jsonable(result):
    # parser 계열은 dict 를, 나머지 facade 는 Pydantic 모델 리스트를 반환한다.
    if isinstance(result, list):
        return [item.model_dump() if hasattr(item, "model_dump") else item for item in result]
    return result


def parse_args():
    ap = argparse.ArgumentParser(description="facade in-process 업무 테스트 러너")
    ap.add_argument("input_path", help="입력 파일 또는 디렉터리")
    ap.add_argument("output_dir", help="결과 JSON 저장 디렉터리")
    ap.add_argument(
        "--facade",
        default="intelligent",
        help=f"facade 이름({', '.join(facade_names())}) 또는 DocumentProcessor 를 가진 .py 경로. 기본 intelligent",
    )
    ap.add_argument(
        "--kw",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="facade 에 넘길 kwargs. 반복 지정하며 값은 JSON 으로 해석한다(예: --kw toc=1 --kw llm_cache=1)",
    )
    ap.add_argument("--fail-fast", action="store_true", help="첫 실패에서 중단한다(기본: 기록하고 계속)")
    return ap.parse_args()


def main():
    args = parse_args()
    # 로컬 실행은 로컬(VPN) 모델로 돈다. 이미 설정된 값은 존중한다.
    os.environ.setdefault("GENOS_MODEL_PRESETS_FILE", str(PROJECT_ROOT / "genon" / "sites" / "dev" / "model_presets.yaml"))

    input_path = Path(args.input_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    files = collect_files(input_path)
    is_dir = input_path.is_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    extra_kwargs = parse_kw(args.kw)

    doc_processor = load_processor_class(args.facade)()
    mock_request = Request(scope={"type": "http"})

    begin = time.time()
    saved = 0
    failed: list[dict[str, Any]] = []
    for idx, file_path in enumerate(files, start=1):
        print(f"[{idx}/{len(files)}] {file_path}")
        kwargs = {"org_filename": file_path.name, **extra_kwargs}
        try:
            result = asyncio.run(doc_processor(mock_request, str(file_path), **kwargs))
        except Exception as e:
            if args.fail_fast:
                raise
            failed.append({"file": str(file_path), "error": f"{type(e).__name__}: {e}"})
            print(f"실패: {file_path}\n{traceback.format_exc()}")
            continue

        if is_dir:
            output_path = output_dir / file_path.relative_to(input_path).with_suffix(".json")
        else:
            output_path = output_dir / "result.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(to_jsonable(result), f, ensure_ascii=False, indent=4)
        saved += 1
        print(f"저장: {output_path}")

    if failed:
        failed_path = output_dir / "failed_files.json"
        with open(failed_path, "w", encoding="utf-8") as f:
            json.dump(failed, f, ensure_ascii=False, indent=2)
        print(f"실패 {len(failed)}건: {failed_path}")

    print(f"처리 시간: {time.time() - begin:.2f}초, 저장 {saved}건, 실패 {len(failed)}건")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
