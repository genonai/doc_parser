"""파드 사이에는 상대 참조만 오간다 — 루트는 이 파드의 env 로 해석한다.

    read_ref        실제로 읽을 파일. {"root": "nfs"|"artifact", "path": 상대경로}
                    nfs = 원본·업로드 하드링크(NFS_ROOT), artifact = 파이프라인이 만든 JSON 복구본(ARTIFACT_ROOT)
    ref             산출물. `<문서키>/<name>` → ARTIFACT_ROOT/<문서키>/<name>.json
                    (파이프라인 app/common/artifacts.py 의 ref 규약과 같다)

절대경로나 `..` 로 루트 밖을 가리키면 거부한다 — 같은 입력이면 늘 실패하므로 ValueError.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _inside(root: str, rel: str, label: str) -> Path:
    rel = str(rel or "").strip()
    if not rel or os.path.isabs(rel):
        raise ValueError(f"{label} 는 루트 기준 상대경로여야 합니다: {rel!r}")
    base = Path(root).resolve()
    target = (base / rel).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"{label} 가 루트 밖을 가리킵니다: {rel!r}")
    return target


def read_path(nfs_root: str, artifact_root: str, read_ref) -> Path:
    if not isinstance(read_ref, dict) or read_ref.get("root") not in ("nfs", "artifact"):
        raise ValueError(f'read_ref 는 {{"root": "nfs"|"artifact", "path": …}} 여야 합니다: {read_ref!r}')
    root = nfs_root if read_ref["root"] == "nfs" else artifact_root
    return _inside(root, read_ref.get("path"), "read_ref.path")


def artifact_path(artifact_root: str, ref: str) -> Path:
    return _inside(artifact_root, f"{ref}.json", "ref")


def load_json(artifact_root: str, ref: str):
    return json.loads(artifact_path(artifact_root, ref).read_text(encoding="utf-8"))


def save_json(artifact_root: str, ref: str, payload) -> int:
    """payload 를 ref 위치에 쓰고 바이트 수를 돌려준다. 임시 파일 → rename 이라 반쯤 쓴 파일이 안 남는다."""
    target = artifact_path(artifact_root, ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return len(data)
