"""LibreOfficeConverter 단위 테스트 — 같은 프로세스의 스레드끼리 soffice 실행이 겹치지 않는지."""
from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from genon.preprocessor.processing.converters.hwp_to_pdf import libreoffice as lo_mod
from genon.preprocessor.processing.converters.hwp_to_pdf.libreoffice import LibreOfficeConverter


@pytest.mark.unit
def test_convert_runs_one_soffice_at_a_time_per_process(monkeypatch, tmp_path: Path):
    """재현 #515: 프로필이 프로세스당 하나라, 스레드 셋이 동시에 변환해도 soffice 는 한 번에 하나만 돈다.
    예전엔 셋이 같은 프로필로 동시에 떠서 뒤 호출이 실패하거나 멈췄다. subprocess 는 가짜(외부 호출 없음)."""
    state = {"running": 0, "peak": 0}
    guard = threading.Lock()

    def fake_run(cmd, **kwargs):
        with guard:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        time.sleep(0.05)                                   # soffice 가 도는 동안 다른 스레드가 들어올 틈
        Path(cmd[-1]).with_suffix(".pdf").write_bytes(b"%PDF")
        with guard:
            state["running"] -= 1
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(lo_mod.subprocess, "run", fake_run)
    files = []
    for i in range(3):
        f = tmp_path / f"doc{i}.hwp"
        f.write_bytes(b"\x00")
        files.append(str(f))

    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(LibreOfficeConverter().convert, files))

    assert (state["peak"], results) == (1, [str(Path(f).with_suffix(".pdf")) for f in files])
