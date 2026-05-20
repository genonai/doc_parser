#!/usr/bin/env python3
"""
ad_review 데이터셋 파싱 테스트 스크립트.

서버(main.py)가 실행 중이어야 합니다:
    cd genon/preprocessor/src
    python -m uvicorn main:app --host 127.0.0.1 --port 7085 --reload

사용 예시:
    # test_guide.pdf 파싱 (기본값: /parser API)
    python genon/preprocessor/tests/test_ad_review.py

    # /run API로 test_guide.pdf 파싱
    python genon/preprocessor/tests/test_ad_review.py --api run

    # 특정 파일 지정
    python genon/preprocessor/tests/test_ad_review.py --file "dataset_ad_review/(5-1) 회사홈페이지(자동차)_검토용.pdf"

    # dataset_ad_review 내 모든 PDF 처리
    python genon/preprocessor/tests/test_ad_review.py --all

    # 서버 주소 및 출력 디렉토리 지정
    python genon/preprocessor/tests/test_ad_review.py --all --host 127.0.0.1 --port 7085 --output results/
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# genon/preprocessor/tests/ → genon/preprocessor/ → genon/ → repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATASET_DIR = REPO_ROOT / "dataset_ad_review"
DEFAULT_FILE = DATASET_DIR / "test_guide.pdf"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7085


def build_url(host: str, port: int, api: str) -> str:
    endpoint = "/parser" if api == "parser" else "/run"
    return f"http://{host}:{port}{endpoint}"


def check_server(host: str, port: int, timeout: int = 5) -> bool:
    try:
        r = requests.get(f"http://{host}:{port}/healthcheck", timeout=timeout)
        return r.status_code == 200
    except requests.exceptions.ConnectionError:
        return False


def extract_file_num(file_path: Path) -> str | None:
    m = re.match(r'^\((\d+-\d+)\)', file_path.stem)
    return m.group(1) if m else None


def call_api(url: str, file_path: Path, params: dict, timeout: int) -> dict:
    payload = {
        "file_path": str(file_path.resolve()),
        "params": params,
    }
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()

def save_result(result: dict, file_path: Path, output_dir: Path, elapsed: float = 0.0) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    kst = timezone(timedelta(hours=9))
    now = datetime.now(tz=kst)
    ts = now.strftime("%Y%m%d_%H%M%S")
    metadata = {
        "file_num": extract_file_num(file_path),
        "file_name": file_path.name,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(elapsed, 2),
    }
    ordered = {}
    for k, v in result.items():
        if k == "data":
            ordered["metadata"] = metadata
        ordered[k] = v
    if "metadata" not in ordered:
        ordered["metadata"] = metadata
    out_file = output_dir / f"{file_path.stem}_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
    return out_file



def print_summary(result: dict, file_path: Path, elapsed: float):
    data = result.get("data", {})
    code = result.get("code", -1)
    if code != 0:
        print(f"  [FAIL] {result.get('errMsg', 'unknown error')}")
        return
    elements = data.get("elements", [])
    usage = data.get("usage", {})
    pages = usage.get("pages", "?")
    print(f"  pages  : {pages}")
    print(f"  elements: {len(elements)}")
    if elements:
        cats = {}
        for e in elements:
            c = e.get("category", "unknown")
            cats[c] = cats.get(c, 0) + 1
        cat_str = ", ".join(f"{k}={v}" for k, v in sorted(cats.items()))
        print(f"  categories: {cat_str}")

    print(f"  elapsed : {elapsed:.2f}s")


def process_file(url: str, file_path: Path, output_dir: Path, params: dict, timeout: int) -> bool:
    if not file_path.exists():
        print(f"  [ERROR] 파일 없음: {file_path}")
        return False

    print(f"\n처리 중: {file_path.name}")
    t0 = time.time()
    try:
        result = call_api(url, file_path, params, timeout)
    except requests.exceptions.Timeout:
        print(f"  [ERROR] 타임아웃 ({timeout}s 초과)")
        return False
    except requests.exceptions.RequestException as e:
        print(f"  [ERROR] 요청 실패: {e}")
        return False

    elapsed = time.time() - t0
    out_file = save_result(result, file_path, output_dir, elapsed=elapsed)
    print(f"  저장   : {out_file}")
    print_summary(result, file_path, elapsed)
    return result.get("code", -1) == 0


def resolve_file(file_arg: str | None) -> Path:
    if file_arg is None:
        return DEFAULT_FILE
    if file_arg.isdigit():
        pattern = f"({file_arg}-1) *.pdf"
        matches = sorted(DATASET_DIR.glob(pattern))
        if not matches:
            print(f"[ERROR] ({file_arg}-1) 패턴에 매칭되는 파일 없음: {DATASET_DIR}")
            sys.exit(1)
        return matches[0]
    return Path(file_arg)


def main():
    parser = argparse.ArgumentParser(
        description="main.py의 /parser 또는 /run API로 ad_review PDF를 파싱합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--api",
        choices=["parser", "run"],
        default="parser",
        help="호출할 API 엔드포인트 (기본값: parser)",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="처리할 파일 경로 (기본값: dataset_ad_review/test_guide.pdf)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="dataset_ad_review 내 모든 PDF를 처리",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"서버 호스트 (기본값: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"서버 포트 (기본값: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="결과 JSON 저장 디렉토리 (기본값: dataset_ad_review/results/<api>)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=500,
        help="요청 타임아웃(초) (기본값: 500)",
    )
    parser.add_argument(
        "--log-level",
        type=int,
        default=5,
        choices=[0, 1, 2, 3, 4, 5],
        help="서버 로그 레벨 (0=NOLOG ~ 5=DEBUG, 기본값: 5=DEBUG)",
    )
    args = parser.parse_args()

    # 서버 상태 확인
    print(f"서버 확인 중: http://{args.host}:{args.port} ...")
    if not check_server(args.host, args.port):
        print(
            f"[ERROR] 서버에 연결할 수 없습니다.\n"
            f"        다음 명령으로 서버를 먼저 실행하세요:\n"
            f"        cd genon/preprocessor/src\n"
            f"        python -m uvicorn main:app --host {args.host} --port {args.port} --reload"
        )
        sys.exit(1)
    print("서버 연결 OK")

    url = build_url(args.host, args.port, args.api)
    output_dir = Path(args.output) if args.output else DATASET_DIR / "results" / args.api
    params = {"log_level": args.log_level}

    # 처리할 파일 목록 결정
    if args.all:
        files = sorted(DATASET_DIR.glob("*.pdf"))
        if not files:
            print(f"[ERROR] PDF 파일 없음: {DATASET_DIR}")
            sys.exit(1)
        print(f"\n{len(files)}개 PDF 발견 (/{args.api} API, 출력: {output_dir})")
    else:
        target = resolve_file(args.file)
        files = [target]
        print(f"\n파일: {files[0].name} (/{args.api} API, 출력: {output_dir})")

    # 파일 처리
    success, failure = 0, 0
    total_t0 = time.time()

    for pdf in files:
        ok = process_file(url, pdf, output_dir, params, args.timeout)
        if ok:
            success += 1
        else:
            failure += 1

    total_elapsed = time.time() - total_t0
    print(f"\n{'=' * 50}")
    print(f"완료: {success}성공 / {failure}실패  (총 {total_elapsed:.1f}s)")
    print(f"결과 저장 위치: {output_dir.resolve()}")

    sys.exit(0 if failure == 0 else 1)


if __name__ == "__main__":
    main()
