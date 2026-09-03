"""배포 전 custom_fields 설정 점검 — 코드를 올리기 **전에** 현장 설정을 검사한다.

## 왜 필요한가

이번 변경으로 설정 오기입이 기동 실패가 된다(예전에는 조용히 무시됐다). 저장소 안의
`resource/` 는 전수 확인했지만, 현장에서 손댄 설정이 있다면 그건 배포해 봐야 안다 —
그 시점에 알면 이미 서비스가 안 뜬다.

이 스크립트는 **파싱과 LLM 호출 없이 yaml 만 읽어** 그 위험을 미리 드러낸다.
판정은 `facade/enrichment/config_schema.py` 를 그대로 import 해서 쓰므로 검증기와
규칙이 갈리지 않는다(스크립트가 규칙을 다시 구현하면 반드시 갈린다).

## 무엇을 잡나

1. **기동을 막는 것** — 이 extractor 가 읽지 않는 키(오타 포함)
2. **제거된 키** — `nulls` / `json_text_fields` / extractor 별칭 4종
3. **청크 본문이 바뀌는 것** — `column_map` 에도 `field_labels` 에도 없는 필드를
   `text_fields` 에 쓴 경우. 예전에는 목표필드명이 라벨로 붙었고 이제는 값만 나간다.
   본문이 바뀌면 임베딩이 바뀌므로 재색인 판단이 필요하다.

## 쓰는 법

    ./precheck_custom_fields.sh                        # 저장소 resource/
    ./precheck_custom_fields.sh /path/to/site/resource # 현장 설정 디렉터리

문제가 하나라도 있으면 비-0 으로 끝난다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from genon.preprocessor.facade.enrichment import config_schema as cs  # noqa: E402
from genon.preprocessor.facade.enrichment import config_v2 as cv2  # noqa: E402

# 이번 정리에서 없앤 키 → 대신 쓸 것.
REMOVED_KEYS = {
    "nulls": "defaults 에 `필드: null` 로 적는다(결과가 같다)",
    "json_text_fields": "text_from 으로 옮긴다(값 종류를 자동 판별한다)",
}
REMOVED_EXTRACTORS = {
    "document_llm": "llm",
    "tabular": "tabular_mapping",
    "column_mapping": "tabular_mapping",
    "json_records": "json_mapping",
}

PROCESSOR_CONFIGS = (
    "parser_processor_config.yaml",
    "parser_processor_config_simple.yaml",
    "intelligent_processor_config.yaml",
    "convert_processor_config.yaml",
    "chunking_processor_config.yaml",
    "attachment_processor_config.yaml",
)


def load_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - 어떤 파싱 실패든 그대로 알린다
        raise SystemExit(f"[치명] {path} 를 읽을 수 없습니다: {exc}")


def registered_blocks(root: Path) -> list[tuple[str, dict]]:
    """(프로세서 config 이름, custom_fields 등록 블록) 목록."""
    blocks: list[tuple[str, dict]] = []
    for name in PROCESSOR_CONFIGS:
        path = root / name
        if not path.exists():
            continue
        for item in (load_yaml(path).get("enrichment") or []):
            block = (item or {}).get("custom_fields")
            if isinstance(block, dict):
                blocks.append((name, block))
    return blocks


def check_block(source: str, block: dict, root: Path, seen_files: set[str]) -> list[str]:
    """등록 블록 하나와 그것이 가리키는 config_file 을 검사한다.

    같은 config_file 이 여러 프로세서에 등록되는 것이 정상이라(같은 doc_type 을 parser 와
    intelligent 가 함께 쓴다) 파일 단위 검사는 `seen_files` 로 한 번만 한다. 등록 블록
    자체는 프로세서마다 다를 수 있으므로 매번 검사한다.
    """
    problems: list[str] = []
    extractor = block.get("extractor") or "llm"
    where = f"{source} [doc_type={block.get('doc_type')}]"

    if extractor in REMOVED_EXTRACTORS:
        problems.append(
            f"[기동실패] {where}: extractor '{extractor}' 는 없어졌습니다 "
            f"→ '{REMOVED_EXTRACTORS[extractor]}' 로 바꾸세요."
        )
        extractor = REMOVED_EXTRACTORS[extractor]

    # 등록 블록 자체도 같은 규칙으로 본다(여기 오타도 기동을 막는다).
    item_cfg = {k: v for k, v in block.items() if k != "enable"}
    diagnosis = cs.diagnose_keys(item_cfg, extractor)
    if diagnosis:
        problems.append(f"[기동실패] {cs.format_diagnosis(where + ' 등록 블록', diagnosis)}")

    config_file = block.get("config_file")
    if not config_file:
        return problems
    path = root / str(config_file)
    if not path.exists():
        problems.append(f"[기동실패] {where}: config_file '{config_file}' 이 없습니다.")
        return problems

    if str(config_file) in seen_files:
        return problems
    seen_files.add(str(config_file))

    cfg = load_yaml(path)
    label = f"{config_file}"
    if cv2.is_v2(cfg):
        # v2 는 내부(v1) 형태로 번역된 뒤에야 extractor 지원키와 대조할 수 있다.
        # 번역 전 원본을 그대로 검사하면 v2 키가 전부 "모르는 키"로 잡힌다.
        try:
            cfg, extractor = cv2.normalize(cfg, label=label)
        except cv2.ConfigV2Error as exc:
            problems.append(f"[기동실패] {exc}")
            return problems
    diagnosis = cs.diagnose_keys(cfg, extractor)
    if diagnosis:
        problems.append(f"[기동실패] {cs.format_diagnosis(label, diagnosis)}")

    for key, hint in REMOVED_KEYS.items():
        if key in cfg:
            problems.append(f"[기동실패] {label}: `{key}` 는 없어졌습니다 → {hint}.")

    problems.extend(check_body_label_change(label, cfg))
    return problems


def check_body_label_change(label: str, cfg: dict) -> list[str]:
    """청크 본문 라벨이 바뀌는 필드를 알린다(재색인 판단용).

    예전에는 `column_map` 에 없는 필드도 목표필드명이 라벨로 붙었다("SUMMARY_TEXT: …").
    이제는 이름이 없으면 값만 나가므로, 그런 필드가 본문에 있으면 청크 텍스트가 달라진다.
    """
    column_map = cfg.get("column_map")
    if not isinstance(column_map, dict) or not column_map:
        return []  # column_map 이 없는 경로는 예전에도 폴백하지 않았다
    raw_labels = cfg.get("field_labels")
    labels: dict = raw_labels if isinstance(raw_labels, dict) else {}
    raw_text_fields = cfg.get("text_fields")
    text_fields: list = raw_text_fields if isinstance(raw_text_fields, list) else []
    derived = set()
    for block_key in ("text_from", "html_text_fields"):
        spec = cfg.get(block_key)
        if isinstance(spec, dict):
            derived |= {str(k) for k in spec}

    affected = [
        str(f) for f in text_fields
        if str(f) not in column_map and str(f) not in labels and str(f) not in derived
    ]
    if not affected:
        return []
    return [
        f"[본문변화] {label}: {affected} 는 청크 본문에서 "
        f"`필드명: 값` → `값` 으로 바뀝니다. 임베딩이 달라지므로 재색인을 검토하거나 "
        f"field_labels 에 사람이 읽는 이름을 주세요."
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="배포 전 custom_fields 설정 점검")
    ap.add_argument(
        "resource_dir", nargs="?",
        default=str(REPO_ROOT / "genon" / "preprocessor" / "resource"),
        help="검사할 설정 디렉터리(프로세서 config 와 custom_field yaml 이 있는 곳)",
    )
    args = ap.parse_args()

    root = Path(args.resource_dir).expanduser().resolve()
    if not root.is_dir():
        print(f"[치명] 디렉터리가 없습니다: {root}")
        return 2

    print(f"검사 대상: {root}\n")
    blocks = registered_blocks(root)
    if not blocks:
        print("[치명] 등록된 custom_fields 블록이 없습니다 — 경로가 맞는지 확인하세요.")
        return 2

    problems: list[str] = []
    seen_files: set[str] = set()
    for source, block in blocks:
        problems.extend(check_block(source, block, root, seen_files))

    # 등록되지 않은 custom_field yaml 은 배포되지만 쓰이지 않는다(정보성).
    registered_files = {str(b.get("config_file")) for _source, b in blocks if b.get("config_file")}
    orphans = sorted(
        p.name for p in root.glob("custom_field_*.yaml") if p.name not in registered_files
    )

    blocking = [p for p in problems if p.startswith("[기동실패]")]
    body = [p for p in problems if p.startswith("[본문변화]")]

    for line in blocking:
        print(line)
    if blocking:
        print()
    for line in body:
        print(line)
    if body:
        print()
    if orphans:
        print(f"[정보] 어느 프로세서에도 등록되지 않은 설정 {len(orphans)}건: {orphans}\n")

    print("=" * 70)
    print(f"등록 블록 {len(blocks)}건 검사 | 기동실패 {len(blocking)}건 | 본문변화 {len(body)}건")
    if blocking:
        print()
        print("기동실패가 있으면 이 설정으로는 서비스가 뜨지 않습니다. 배포 전에 고치거나,")
        print(f"첫 릴리스에 한해 {cs.VALIDATION_POLICY_ENV}=warn 으로 두어 경고만 남기게 하세요.")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
