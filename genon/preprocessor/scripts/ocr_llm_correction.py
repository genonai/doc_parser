#!/usr/bin/env python3
"""
OCR LLM 교정 스크립트

dataset_ad_review/results/parser/ 폴더의 JSON 파일을 읽어,
각 element의 content를 LLM(qwen3.5)으로 교정하고
교정된 결과를 dataset_ad_review/results/parser_llm_corrected/ 에 저장합니다.

사용법:
    # API 키 환경변수 설정 후 실행
    GENON_API_KEY=<your_key> python genon/preprocessor/scripts/ocr_llm_correction.py

    # 특정 파일만 처리
    GENON_API_KEY=<your_key> python genon/preprocessor/scripts/ocr_llm_correction.py \
        --file "dataset_ad_review/results/parser/(4-1) 랜딩페이지_실손_검토용_20260520_151123.json"

    # 출력 디렉토리 지정
    GENON_API_KEY=<your_key> python genon/preprocessor/scripts/ocr_llm_correction.py \
        --output dataset_ad_review/results/parser_llm_corrected

    # 원본 파일 덮어쓰기
    GENON_API_KEY=<your_key> python genon/preprocessor/scripts/ocr_llm_correction.py --inplace
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INPUT_DIR  = REPO_ROOT / "dataset_ad_review" / "results" / "parser"
OUTPUT_DIR = REPO_ROOT / "dataset_ad_review" / "results" / "parser_llm_corrected"

GENON_BASE_URL = "https://api.genon.ai/v1"
MODEL_ID       = "qwen/qwen3.5-397b-a17b-fp8"

# 한 번의 LLM 호출에 묶을 최대 element 수
BATCH_SIZE = 20

SYSTEM_PROMPT = """\
You are an expert in correcting OCR output from Korean insurance advertisement documents.
Correct OCR recognition errors (misread characters, unnecessary spaces, spurious special characters, etc.) \
without changing the original meaning or content in any way.

Follow these rules:
1. Correct insurance-domain-specific misrecognition patterns (e.g. 가업→가입, 납업→납입, 보형→보험).
2. Do not make corrections that alter the contextual meaning.
3. Preserve table markdown formatting (|, -, header separator lines, etc.) exactly as-is.
4. Accept a JSON array as input and return a JSON array of the same structure as output.
5. Never change the id of any element.
6. If content is empty or requires no correction, return it unchanged.
7. IMPORTANT: If the content is written in Korean, your corrected output must also be in Korean. \
Never translate Korean content into another language.
"""

USER_PROMPT_TEMPLATE = """\
Please correct the OCR-extracted text in the elements below.
The content is text extracted from Korean insurance advertisement documents and may contain OCR recognition errors.
Correct word errors and spacing errors only — do not make any other changes.
The id of each element is a unique identifier and must remain unchanged in the corrected output.
Return a JSON array containing the corrected content. Each element must have id and content fields.
The content language is Korean — do not translate or convert the language.
Input format:  [{{"id": <int>, "content": "<string>"}}, ...]
Output format: Return only the JSON array in the same structure as the input (no other text).

Input:
{elements_json}
"""


def call_llm(elements: list[dict], api_key: str) -> list[dict]:
    """elements 배열을 LLM에 전달하여 교정된 배열을 반환."""
    payload_items = [{"id": e["id"], "content": e["content"]} for e in elements]
    user_msg = USER_PROMPT_TEMPLATE.format(
        elements_json=json.dumps(payload_items, ensure_ascii=False, indent=2)
    )

    body = {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0.0,
        "max_tokens": 16384,
    }

    resp = requests.post(
        f"{GENON_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=300,
    )
    resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"].strip()

    # 코드블록 래퍼 제거
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    corrected: list[dict] = json.loads(raw)
    return corrected


def correct_file(json_path: Path, api_key: str, inplace: bool, output_dir: Path) -> Path:
    data = json.loads(json_path.read_text(encoding="utf-8"))

    elements: list[dict] = data.get("data", {}).get("elements", [])
    if not elements:
        print(f"  [skip] elements 없음: {json_path.name}")
        return json_path

    # content가 있는 element만 교정 대상
    targets = [e for e in elements if e.get("content", "").strip()]
    id_to_elem = {e["id"]: e for e in elements}

    print(f"  교정 대상 elements: {len(targets)} / {len(elements)}")

    # 배치 처리
    corrected_map: dict[int, str] = {}
    for i in range(0, len(targets), BATCH_SIZE):
        batch = targets[i : i + BATCH_SIZE]
        print(f"    배치 {i // BATCH_SIZE + 1}: elements {batch[0]['id']}~{batch[-1]['id']} ({len(batch)}개)", end="", flush=True)
        t0 = time.time()
        try:
            corrected_batch = call_llm(batch, api_key)
            for item in corrected_batch:
                corrected_map[item["id"]] = item["content"]
            print(f" → 완료 ({time.time()-t0:.1f}s)")
        except Exception as exc:
            print(f" → 실패: {exc}")
            # 실패한 배치는 원본 유지
            for e in batch:
                corrected_map[e["id"]] = e["content"]

    # 원본 데이터 구조에 교정 내용 반영
    changed = 0
    for elem in elements:
        eid = elem["id"]
        if eid in corrected_map and corrected_map[eid] != elem.get("content", ""):
            elem["content"] = corrected_map[eid]
            changed += 1

    print(f"  교정 변경: {changed}개 elements")

    # 저장
    if inplace:
        out_path = json_path
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / json_path.name

    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  저장: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="OCR LLM 교정 스크립트")
    parser.add_argument("--file",    type=str, default=None, help="처리할 특정 JSON 파일 경로")
    parser.add_argument("--output",  type=str, default=str(OUTPUT_DIR), help="출력 디렉토리")
    parser.add_argument("--inplace", action="store_true", help="원본 파일 덮어쓰기")
    args = parser.parse_args()

    api_key = os.environ.get("GENON_API_KEY", "").strip()
    if not api_key:
        print("오류: GENON_API_KEY 환경변수를 설정하세요.", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)

    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(INPUT_DIR.glob("*.json"))

    if not files:
        print(f"JSON 파일이 없습니다: {INPUT_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"처리 파일 수: {len(files)}")
    print(f"모델: {MODEL_ID}")
    print(f"출력: {'원본 덮어쓰기' if args.inplace else output_dir}\n")

    total_t0 = time.time()
    for idx, fp in enumerate(files, 1):
        print(f"[{idx}/{len(files)}] {fp.name}")
        correct_file(fp, api_key, args.inplace, output_dir)
        print()

    print(f"전체 완료: {time.time()-total_t0:.1f}s")


if __name__ == "__main__":
    main()
