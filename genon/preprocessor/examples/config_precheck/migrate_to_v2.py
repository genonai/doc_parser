"""v1 custom_field yaml → v2 로 옮긴다. **기본은 미리보기**이고 `--write` 로만 기록한다.

## 안전 장치

파일을 고치기 전에 그 파일 하나에 대해 병행 검증 세 층을 다시 돌린다 — 변환 결과를 되돌린
것이 원본과 같고, 매퍼·enricher 가 해석해 낸 상태가 같고, 매퍼 산출도 같아야 기록한다.
하나라도 어긋나면 그 파일은 건너뛴다.
`nulls` 마이그레이션에서 "의미는 같은데 파일 여백이 망가진" 사고가 있었으므로, 의미 대조와
별개로 **바뀐 줄 수**도 함께 보여 준다.

주석은 옮기지 못한다. v1 의 주석은 키 옆에 붙어 있고 v2 는 구조가 달라 붙일 자리가 없다 —
그래서 변환본에는 원본 주석을 파일 머리에 통째로 남기고, 사람이 보면서 필요한 것만 각
필드 스펙 옆으로 옮기게 한다. **자동 변환만으로 끝내지 말 것.**

## 쓰는 법

    ./migrate_to_v2.sh                       # 미리보기(파일을 고치지 않는다)
    ./migrate_to_v2.sh --only custom_field_faq.yaml
    ./migrate_to_v2.sh --write               # 실제 기록
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from genon.preprocessor.facade.enrichment import config_v2 as v2  # noqa: E402

from precheck_custom_fields import load_yaml, registered_blocks  # noqa: E402
from verify_v2_equivalence import (  # noqa: E402
    compare_configs, compare_mapper_output, compare_resolved_state, stage_siblings,
)

_HEADER = """# ─────────────────────────────────────────────────────────────────────────
#  v2 스키마로 자동 변환된 설정입니다.
#
#  자동 변환은 **값만** 옮깁니다. 아래는 원본 v1 의 주석 전문이며, 필요한 설명을 각
#  필드 스펙 옆으로 손수 옮긴 뒤 이 블록을 지우세요. 주석이 사라지면 왜 그런 설정인지
#  아는 사람이 없어집니다.
# ─────────────────────────────────────────────────────────────────────────
"""


def original_comments(text: str) -> str:
    """원본에서 주석 줄만 뽑아 머리말로 쓸 블록을 만든다.

    줄을 **그대로** 옮긴다. `#` 뒤 공백을 정규화하면 주석 안의 설정 예시가 들여쓰기를
    잃어 구조가 뭉개진다(`#   - custom_fields:` / `#       enable: true` 가 같은 열이 된다).
    주석을 남기는 목적이 "왜 그런 설정인지"를 보존하는 것이므로 모양도 그대로 둔다.
    """
    lines = [ln for ln in text.splitlines() if ln.lstrip().startswith("#")]
    if not lines:
        return ""
    return _HEADER + "\n".join(lines) + "\n\n"


class _V2Dumper(yaml.SafeDumper):
    """짧은 스칼라 목록은 한 줄로 쓴다 — `alias: [질문, 대표질문]`.

    기본 블록 스타일이면 별칭 하나가 한 줄씩 차지해 v2 가 v1 보다 길어진다. 필드 하나의
    규칙을 한눈에 보이게 하는 것이 v2 의 목적이므로 여기서 되돌린다.
    """


def _flow_for_scalar_seq(dumper, data):
    flow = all(isinstance(item, (str, int, float, bool)) or item is None for item in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


def _block_for_multiline_str(dumper, data):
    r"""여러 줄 문자열은 `|` 리터럴 블록으로 쓴다.

    기본 직렬화는 개행을 `\n` 이스케이프와 줄바꿈 연속 기호(`\`)로 접어 프롬프트를
    사람이 읽지도 고치지도 못하게 만든다. v1 이 `|` 를 쓰고 있었으므로 그대로 두는 것이
    맞다 — 설정을 읽기 쉽게 하려고 v2 로 옮기면서 프롬프트를 못 읽게 하면 앞뒤가 안 맞는다.
    """
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_V2Dumper.add_representer(list, _flow_for_scalar_seq)
_V2Dumper.add_representer(str, _block_for_multiline_str)


def render(cfg_v2: dict) -> str:
    """v2 설정을 사람이 읽기 좋은 순서로 직렬화한다(최상위 7키 순서 고정)."""
    order = ["schema", "source", "fields", "require", "filter", "body", "llm"]
    ordered = {k: cfg_v2[k] for k in order if k in cfg_v2}
    return yaml.dump(ordered, Dumper=_V2Dumper, allow_unicode=True,
                     sort_keys=False, width=100, default_style=None)


def migrate_one(path: Path, extractor: str, tmp: Path, write: bool) -> tuple[str, str]:
    """`(상태, 메모)`. 검증을 통과하지 못하면 기록하지 않는다."""
    text = path.read_text(encoding="utf-8")
    cfg = load_yaml(path)
    if v2.is_v2(cfg):
        return "SKIP", "이미 v2"

    problems = compare_configs(path.name, cfg, extractor)
    # 해석된 상태까지 본다. 문서형(llm)은 매핑 산출이 없어 이 층이 빠지면 프롬프트·연결·
    # 출력필드가 그대로 옮겨졌는지 아무도 확인하지 않은 채 파일이 덮인다.
    problems += compare_resolved_state(path.name, cfg, extractor, tmp)
    problems += compare_mapper_output(path.name, cfg, extractor, tmp)
    if problems:
        return "FAIL", problems[0].splitlines()[0][:90]

    as_v2 = v2.to_v2(cfg, extractor)
    body = original_comments(text) + render(as_v2)
    before_lines = len(text.splitlines())
    after_lines = len(body.splitlines())

    if write:
        path.write_text(body, encoding="utf-8")
        # 기록한 파일이 실제로 다시 읽히는지 확인한다(직렬화 사고 방지).
        reloaded, back_extractor = v2.normalize(load_yaml(path), label=path.name)
        if back_extractor != extractor:
            return "FAIL", f"기록 후 extractor 불일치: {back_extractor}"
        return "WRITE", f"{before_lines}줄 → {after_lines}줄"
    return "OK", f"{before_lines}줄 → {after_lines}줄 (미리보기)"


def main() -> int:
    ap = argparse.ArgumentParser(description="v1 → v2 설정 변환")
    ap.add_argument("resource_dir", nargs="?",
                    default=str(REPO_ROOT / "genon" / "preprocessor" / "resource"))
    ap.add_argument("--write", action="store_true", help="실제로 파일을 고친다(기본은 미리보기)")
    ap.add_argument("--only", nargs="*", default=None, help="이 파일만 변환")
    ap.add_argument("--show", default=None, help="이 파일의 변환 결과를 출력만 한다")
    args = ap.parse_args()

    root = Path(args.resource_dir).expanduser().resolve()
    if not root.is_dir():
        print(f"[치명] 디렉터리가 없습니다: {root}")
        return 2

    import tempfile
    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 프롬프트 파일을 참조하는 설정이 임시 디렉터리에서도 읽히게 한다.
        stage_siblings(root, tmp)
        for _source, block in registered_blocks(root):
            name = str(block.get("config_file") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            if args.only and name not in args.only:
                continue
            path = root / name
            if not path.exists():
                continue
            extractor = str(block.get("extractor") or "llm")

            if args.show and name == args.show:
                print(original_comments(path.read_text(encoding="utf-8"))
                      + render(v2.to_v2(load_yaml(path), extractor)))
                return 0

            status, note = migrate_one(path, extractor, tmp, args.write)
            rows.append((name, status, note))

    width = max((len(r[0]) for r in rows), default=20)
    for name, status, note in rows:
        print(f"  {name.ljust(width)}  {status:6s} {note}")

    failed = [r for r in rows if r[1] == "FAIL"]
    print("\n" + "=" * 70)
    print(f"대상 {len(rows)}건 | 변환가능 {sum(1 for r in rows if r[1] in ('OK', 'WRITE'))} "
          f"| 실패 {len(failed)}")
    if not args.write and not failed:
        print("\n미리보기입니다. 실제로 고치려면 --write 를 붙이세요.")
        print("변환 후에는 주석을 각 필드 스펙 옆으로 손수 옮겨야 합니다.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
