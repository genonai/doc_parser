"""
pytest에서 자동 로드되는 공통 설정 파일.
여기 정의된 픽스처들은 다른 테스트에서 import 없이 바로 사용 가능.
"""

import os
import sys
from pathlib import Path
import pytest

# 이슈 #199 — pytest sys.path 보강.
# pyproject.toml(rootdir=genon/preprocessor) 의 pythonpath 가 "src" 만이라
# 로컬/일부 CI 환경에서 다음 두 가지 절대 import 가 깨질 수 있어 두 경로를 prepend:
#   - `genon.preprocessor.processing.converters.hwp_to_pdf.*` (신규 모듈, src/ 밖)  → repo root 필요
#   - `facade.*` / `processing.*`                        (파사드·처리 라이브러리) → genon/preprocessor 필요
_PREPROC = Path(__file__).resolve().parents[1]   # genon/preprocessor
_REPO_ROOT = Path(__file__).resolve().parents[3]  # repo root
for _p in (_PREPROC, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# 프로젝트 루트 경로 반환
# scope="session" → 테스트 전체 실행 동안 한 번만 계산
@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


# 샘플 파일 디렉터리 경로 반환
# 예: sample_dir / "sample.pdf"
@pytest.fixture(scope="session")
def sample_dir(repo_root: Path) -> Path:
    return repo_root / "sample_files"


# Regression test용 샘플 파일 디렉터리
@pytest.fixture(scope="session")
def regression_test_dir(repo_root: Path) -> Path:
    """Regression test 전용 샘플 파일 디렉터리"""
    return repo_root / "sample_files" / "regression_test"


# DocumentProcessor 클래스를 안전하게 로드
# 모듈이 없으면 해당 테스트를 skip 처리
@pytest.fixture(scope="session")
def basic_processor():
    mod = pytest.importorskip("facade.attachment_processor")
    return mod.DocumentProcessor


# intelligent_processor 픽스처 추가
@pytest.fixture(scope="session")
def intelligent_processor():
    mod = pytest.importorskip("facade.intelligent_processor")
    return mod.DocumentProcessor

@pytest.fixture(scope="session")
def attachment_processor():
    mod = pytest.importorskip("facade.attachment_processor")
    return mod.DocumentProcessor


@pytest.fixture(scope="session")
def parser_processor():
    mod = pytest.importorskip("facade.parser_processor")
    return mod.DocumentProcessor


# TEDS-S 지표 함수 노출 (tests/teds_metric.py)
@pytest.fixture(scope="session")
def teds_s():
    """구조 전용 TEDS(TEDS-S) 계산 함수: teds_s(pred_html, gt_html) -> float."""
    _tests_dir = Path(__file__).resolve().parent
    if str(_tests_dir) not in sys.path:
        sys.path.insert(0, str(_tests_dir))
    from teds_metric import teds_s as _f
    return _f


# table.pdf 각 페이지 표의 GT(ground-truth) 로드.
# table.jsonl: 한 줄당 한 표, index i == table.pdf 페이지 (i+1) 의 표.
@pytest.fixture(scope="session")
def table_gt(repo_root: Path):
    import json

    gt_path = repo_root / "sample_files" / "table.jsonl"
    if not gt_path.exists():
        return []
    lines = gt_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


_UNIT_DIR = Path(__file__).resolve().parent / "unit"


def _is_unit_test(request) -> bool:
    """tests/unit 아래에 있으면 unit 테스트로 본다.

    마커(`@pytest.mark.unit`)로 판정하지 않는 이유가 있다. tests/unit 안에는 마커가
    붙지 않은 테스트가 섞여 있었고, 그런 테스트에는 아래 차단 장치가 걸리지 않아
    실제로 외부 게이트웨이를 호출했다. 위치는 빠뜨릴 수 없지만 마커는 빠뜨릴 수 있다.
    """
    path = getattr(request.node, "path", None)
    if path is None:  # pytest 7 미만 호환
        path = Path(str(request.node.fspath))
    try:
        Path(path).resolve().relative_to(_UNIT_DIR)
    except ValueError:
        return False
    return True


@pytest.fixture(autouse=True)
def _no_external_network_in_unit_tests(request):
    """unit 테스트는 외부 네트워크로 나가지 않는다.

    출고 설정(`resource/`, `resource_dev/`)에는 실제 LLM 게이트웨이 URL 이 들어 있다.
    그 설정을 그대로 복사해 쓰는 테스트가 프로세서를 끝까지 돌리면 진짜 호출이 나간다.
    호출부마다 patch 로 막는 방식은 호출 경로가 하나 늘 때 조용히 새고, 실제로 샜다
    (convert_processor.__call__ 은 enrichment 계열 메서드를 5개 부르는데 테스트는 그중
    하나만 patch 하고 있었다). 그래서 개별 함수가 아니라 소켓에서 막는다.

    루프백은 허용한다(로컬 임시 서버를 띄우는 테스트가 있다). 디버깅 등으로 실제 호출이
    필요하면 GENOS_TESTS_ALLOW_NETWORK=1 로 해제한다.
    """
    if os.environ.get("GENOS_TESTS_ALLOW_NETWORK") == "1" or not _is_unit_test(request):
        yield
        return

    import socket

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _is_local(address) -> bool:
        if not isinstance(address, tuple) or not address:
            return True  # AF_UNIX 등 주소가 튜플이 아니면 대상 밖
        host = str(address[0])
        return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "") or host.startswith("127.")

    def _blocked(address):
        return RuntimeError(
            f"unit 테스트가 외부 네트워크({address}) 로 나가려 했다. "
            "호출부를 스텁으로 막거나, 실제 호출이 필요한 테스트라면 tests/smoke 로 옮겨라. "
            "일시 허용은 GENOS_TESTS_ALLOW_NETWORK=1."
        )

    def guarded_connect(self, address, *args, **kwargs):
        if _is_local(address):
            return real_connect(self, address, *args, **kwargs)
        raise _blocked(address)

    def guarded_connect_ex(self, address, *args, **kwargs):
        if _is_local(address):
            return real_connect_ex(self, address, *args, **kwargs)
        raise _blocked(address)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex


@pytest.fixture(autouse=True)
def _stub_vlm_for_unit_tests(request, monkeypatch):
    """unit 테스트에서는 외부 VLM 호출을 기본 차단한다."""
    if not _is_unit_test(request):
        return

    try:
        # page/image/table description 세 곳의 VLM 호출은 모두 enrichment.image_request
        # (공용 함수 request_image_description)를 지난다. facade 는 절대경로
        # (genon.preprocessor.facade.*)로 이 모듈을 로드하므로 같은 모듈 객체를 얻으려면
        # 동일 경로로 import 해야 한다(이중 import 방지, 이슈 #199).
        import genon.preprocessor.processing.enrichment.image_request as image_request_mod
    except Exception:
        # image_request 를 사용하지 않는 unit 테스트도 있으므로 조용히 패스
        return

    monkeypatch.setattr(
        image_request_mod,
        "api_image_request",
        lambda *args, **kwargs: "",
        raising=False,
    )
