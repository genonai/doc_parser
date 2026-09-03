"""v1 ↔ v2 병행 검증 — v2 로 옮겨도 결과가 같은지 **전환 전에** 확인한다.

## 무엇을 대조하나

두 층을 본다. 위층이 통과하면 아래층은 원리상 통과하지만, 번역 실수를 잡으려면 둘 다 본다.

1. **설정 왕복** — `to_v2(v1)` 로 옮긴 뒤 `normalize()` 로 되돌린 것이 원본 v1 과 같은가.
   여기서 같으면 v2 는 그 설정을 **온전히 표현한다**는 뜻이다. `to_v2` 는 옮기지 못한 키가
   남으면 예외를 던지므로 "조용히 버려서 통과"가 생기지 않는다.

2. **매퍼 산출** — 같은 샘플을 v1 매퍼와 v2 매퍼에 넣어 목표필드 dict 가 같은가.
   v2 는 내부 형태로 정규화해 **같은 매퍼 코드**를 타므로 구조상 같아야 하고, 이 검사는
   그 전제가 실제로 지켜지는지 본다(LLM 은 부르지 않는다).

## 쓰는 법

    ./verify_v2_equivalence.sh                  # 저장소 resource/
    ./verify_v2_equivalence.sh <설정_디렉터리>

하나라도 어긋나면 비-0 으로 끝난다. **전환은 이게 전부 통과한 뒤에 한다.**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from genon.preprocessor.facade.enrichment import config_v2 as v2  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from precheck_custom_fields import load_yaml, registered_blocks  # noqa: E402


def normalized(cfg: dict) -> dict:
    """비교용 정규화 — 빈 컨테이너는 "없음"과 같게 본다.

    v1 은 `defaults: {}` 를 쓰지 않고 아예 생략하므로, 왕복에서 빈 dict 가 생겨도
    의미 차이가 아니다. 값이 있는 키만 남겨 표기 차이로 오탐이 나지 않게 한다.
    """
    return {k: v for k, v in (cfg or {}).items() if v not in (None, {}, [], "")}


def compare_configs(label: str, cfg_v1: dict, extractor: str) -> list[str]:
    """설정 왕복 대조. 어긋난 키를 사람이 읽는 줄로 돌려준다."""
    try:
        as_v2 = v2.to_v2(cfg_v1, extractor)
    except v2.ConfigV2Error as exc:
        return [f"[표현불가] {label}: {exc}"]
    try:
        back, back_extractor = v2.normalize(as_v2, label=label)
    except v2.ConfigV2Error as exc:
        return [f"[역변환실패] {label}: {exc}"]

    problems = []
    if back_extractor != extractor:
        problems.append(f"[extractor] {label}: {extractor} → {back_extractor}")

    left, right = normalized(cfg_v1), normalized(back)
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            problems.append(
                f"[왕복불일치] {label}.{key}\n"
                f"      v1  : {json.dumps(left.get(key), ensure_ascii=False)[:200]}\n"
                f"      왕복: {json.dumps(right.get(key), ensure_ascii=False)[:200]}"
            )
    return problems


# ── 매퍼 산출 대조 ──────────────────────────────────────────────────────────
# LLM 을 부르지 않는 레코드형만 대상이다. 문서형(llm)은 설정 왕복으로만 본다.


def _sample_record(cfg: dict, extractor: str) -> dict:
    """설정에서 입력을 합성한다 — 매핑되는 컬럼/키마다 서로 다른 더미 값을 넣는다.

    빈 입력을 쓰면 required 검사에 걸려 v1/v2 양쪽이 똑같이 죽고, 정작 비교하려던
    "같은 입력에 같은 결과"를 못 본다. 값을 필드마다 다르게 두어 매핑이 뒤섞이면 드러나게 한다.
    """
    block = cfg.get("column_map") if extractor == "tabular_mapping" else cfg.get("key_map")
    record: dict = {}
    for index, (target, aliases) in enumerate(sorted((block or {}).items())):
        names = aliases if isinstance(aliases, list) else [aliases]
        source = str(names[0]) if names else str(target)
        record[source] = f"값{index}"
    return record


def _sample_payload(cfg: dict, extractor: str):
    record = _sample_record(cfg, extractor)
    if extractor == "tabular_mapping":
        return {"data": [{"sheet_name": "검증시트", "data_rows": [record]}]}
    records_key = cfg.get("records")
    return {str(records_key): [record]} if records_key else [record]


def compare_mapper_output(label: str, cfg_v1: dict, extractor: str, tmp: Path) -> list[str]:
    """같은 입력을 v1/v2 설정으로 매핑해 목표필드가 같은지 본다."""
    if extractor not in ("tabular_mapping", "json_mapping"):
        return []
    try:
        from genon.preprocessor.facade.enrichment.json_records import JsonRecordsMapper
        from genon.preprocessor.facade.enrichment.tabular_custom_fields import (
            TabularCustomFieldsMapper,
        )
    except Exception as exc:  # noqa: BLE001 - 의존성 미가용 환경에서는 건너뛴다
        return [f"[건너뜀] {label}: 매퍼를 불러올 수 없습니다({exc})"]

    def build(cfg: dict, name: str):
        path = tmp / name
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        cls = (TabularCustomFieldsMapper if extractor == "tabular_mapping"
               else JsonRecordsMapper)
        return cls(config_file=path.name, resource_path=str(tmp),
                   doc_type="__verify__", extractor=extractor)

    try:
        as_v2 = v2.to_v2(cfg_v1, extractor)
    except v2.ConfigV2Error:
        return []  # 설정 왕복 단계에서 이미 보고했다

    try:
        mapper_v1 = build(cfg_v1, "custom_field_v1.yaml")
        mapper_v2 = build(as_v2, "custom_field_v2.yaml")
    except Exception as exc:  # noqa: BLE001
        return [f"[매퍼생성] {label}: {exc}"]

    payload = _sample_payload(cfg_v1, extractor)
    try:
        rows_v1 = mapper_v1.build_fields(payload, "__verify__")
        rows_v2 = mapper_v2.build_fields(payload, "__verify__")
    except Exception as exc:  # noqa: BLE001 - required 로 전건 skip 되는 설정도 있다
        return [f"[매핑실행] {label}: {exc}"]

    if rows_v1 != rows_v2:
        return [f"[산출불일치] {label}: v1={rows_v1} / v2={rows_v2}"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="v1 ↔ v2 병행 검증")
    ap.add_argument(
        "resource_dir", nargs="?",
        default=str(REPO_ROOT / "genon" / "preprocessor" / "resource"),
    )
    ap.add_argument("--verbose", action="store_true", help="통과한 설정도 모두 출력")
    args = ap.parse_args()

    root = Path(args.resource_dir).expanduser().resolve()
    if not root.is_dir():
        print(f"[치명] 디렉터리가 없습니다: {root}")
        return 2

    print(f"검사 대상: {root}\n")
    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []
    problems: list[str] = []

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for _source, block in registered_blocks(root):
            config_file = block.get("config_file")
            if not config_file or str(config_file) in seen:
                continue
            seen.add(str(config_file))
            path = root / str(config_file)
            if not path.exists():
                continue
            extractor = str(block.get("extractor") or "llm")
            cfg = load_yaml(path)
            if v2.is_v2(cfg):
                rows.append((str(config_file), extractor, "이미 v2"))
                continue

            found = compare_configs(str(config_file), cfg, extractor)
            found += compare_mapper_output(str(config_file), cfg, extractor, tmp)
            problems.extend(found)
            rows.append((str(config_file), extractor, "FAIL" if found else "PASS"))

    width = max((len(r[0]) for r in rows), default=20)
    for name, extractor, status in rows:
        if status != "PASS" or args.verbose:
            print(f"  {name.ljust(width)}  {extractor:16s} {status}")
    if problems:
        print()
        for line in problems:
            print(line)

    passed = sum(1 for r in rows if r[2] == "PASS")
    print("\n" + "=" * 70)
    print(f"설정 {len(rows)}건 | PASS {passed} | FAIL {len(rows) - passed}")
    if problems:
        print("\nv2 가 아직 표현하지 못하는 것이 있습니다. **전환하지 마세요** —")
        print("위 항목을 config_v2.py 에 반영한 뒤 이 검증이 전부 통과해야 합니다.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
