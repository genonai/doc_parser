"""배포 버전 스탬프 조회 (main.py 의 /version 엔드포인트가 사용).

버전의 단일 진실 소스는 원본 저장소(doc_parser)의 git 릴리스 태그이고,
`build-script/sync-serving-repo.sh` 가 배포할 때 그 값을 배포본 루트의 `VERSION`
파일(JSON)에 새긴다. 이 모듈은 그 스탬프를 읽어 줄 뿐이며 버전을 자체적으로
정의하지 않는다. 서비스 코드에 버전 상수를 두면 릴리스마다 사람이 두 곳을
맞춰야 하고 반드시 어긋난다.

읽는 순서는 세 단계다.
  1. VERSION 파일        (배포본. source="file")
  2. git 명령            (소스 저장소에서 직접 실행할 때. source="git")
  3. 값 없음             (source="unknown")
어느 경로였는지를 응답의 source 로 함께 노출해, 버전이 오래된 것인지 스탬프가
아예 실리지 않은 것인지 운영에서 구분할 수 있게 한다.

알려진 한계: 핫픽스 패치 번들(build-script/create-patch-bundle.sh)은 코드만 덮어쓰고
VERSION 스탬프를 갱신하지 않는다. 패치를 얹은 서버는 패치 이전 릴리스 버전을 그대로 보고한다.
폐쇄망 현장처럼 소스를 손으로 갱신하는 경우를 위해, 루트의 평문 `UPDATED_AT` 파일에
적힌 첫 줄을 manual_updated_at 으로 그대로 노출한다. VERSION(JSON)과 분리한 것은 수기
편집 실수가 릴리스 스탬프까지 깨뜨리지 않게 하려는 것이다.
"""

import json
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

# 배포본/소스 저장소 모두에서 루트는 이 파일 기준 4단계 위다.
# <root>/genon/preprocessor/src/common/version_info.py
_DEFAULT_ROOT = Path(__file__).resolve().parents[4]

_STAMP_FILE = 'VERSION'
_MANUAL_FILE = 'UPDATED_AT'
_MANUAL_MAX_LEN = 100
_GIT_TIMEOUT = 3  # 초. git 이 없거나 느린 환경에서 기동/요청이 매달리지 않게 한다.

# 프로세스 기동 시각(모듈 로딩 시점). 재배포가 실제로 반영됐는지 확인하는 용도이며,
# 코드 갱신 시각(updated_at)과는 의미가 다르다.
_STARTED_AT = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def _read_stamp(root: Path) -> dict:
    """배포본 루트의 VERSION 파일을 읽는다. 없거나 깨졌으면 빈 dict."""
    try:
        with open(root / _STAMP_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_manual(root: Path):
    """현장에서 손으로 적은 갱신 시각. 형식은 검사하지 않고 첫 줄을 그대로 돌려준다.
    파일이 없거나 비었으면 None."""
    try:
        with open(root / _MANUAL_FILE, encoding='utf-8-sig', errors='replace') as f:
            first = f.readline(_MANUAL_MAX_LEN + 1)
    except OSError:
        return None
    return first.strip()[:_MANUAL_MAX_LEN] or None


def _git(root: Path, *args: str):
    try:
        out = subprocess.run(('git', '-C', str(root)) + args,
                             capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _read_git(root: Path) -> dict:
    """소스 저장소에서 직접 실행하는 경우의 폴백. .git 이 없으면 빈 dict."""
    if not (root / '.git').exists():
        return {}
    return {
        'source_version': _git(root, 'describe', '--tags', '--always'),
        'source_commit': _git(root, 'log', '-1', '--format=%H'),
        'source_commit_date': _git(root, 'log', '-1', '--format=%cI'),
    }


def _docling_runtime() -> str:
    """실제 설치된 docling 버전. 스탬프의 docling_wheel(배포 시점 파일명)과 대조하면
    이미지와 wheel 이 어긋난 배포를 잡아낼 수 있다."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:
        return None
    for name in ('genon-docling', 'docling'):
        try:
            return version(name)
        except PackageNotFoundError:
            continue
        except Exception:
            break
    return None


@lru_cache(maxsize=4)
def _resolve(root_str: str) -> dict:
    root = Path(root_str)
    source = 'file'
    stamp = _read_stamp(root)
    if not any(stamp.get(k) for k in ('source_version', 'source_commit')):
        stamp = _read_git(root)
        source = 'git' if any(stamp.values()) else 'unknown'
    return {
        'version': stamp.get('source_version') or 'unknown',
        'updated_at': stamp.get('source_commit_date'),
        'commit': stamp.get('source_commit'),
        'docling': _docling_runtime(),
        'docling_wheel': stamp.get('docling_wheel'),
        'source': source,
    }


def get_version_info(base_dir=None) -> dict:
    """버전 스탬프를 dict 로 돌려준다. 값은 프로세스 생애 동안 1회만 계산한다(캐시).

    base_dir 는 VERSION 파일이 놓인 서비스 루트(main.py 와 같은 위치). 생략하면
    이 파일 위치에서 파생한다.
    """
    root = Path(base_dir) if base_dir else _DEFAULT_ROOT
    info = dict(_resolve(str(root)))
    info['started_at'] = _STARTED_AT   # 기동 시각은 캐시 대상이지만 의미상 매 응답에 싣는다
    # 수기 갱신 시각은 캐시하지 않는다. started_at 보다 늦으면 아직 재기동되지 않았다는 뜻이다.
    info['manual_updated_at'] = _read_manual(root)
    return info
