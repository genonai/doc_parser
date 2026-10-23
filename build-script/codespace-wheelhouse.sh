#!/usr/bin/env bash
# 서빙본 코드스페이스용 wheelhouse 를 만든다 — 인터넷이 되는 곳에서 한 번 돌리고 결과 폴더를 코드스페이스로 옮긴다.
# (서빙본 루트에서는 make-wheelhouse.sh 라는 이름이다 — sync-serving-repo.sh 가 복사한다.)
#
#   ./make-wheelhouse.sh [<출력 폴더>]      # 기본 ./wheelhouse-preprocessor-cp311-linux_x86_64
#
# 목록의 정본은 genon/preprocessor/uv.lock(워커·전처리기 의존성 전부). uv export 로 고정 목록을 뽑아
# requirements-lock.txt 로 함께 담는다 — setup.sh 가 그 목록을 wheelhouse 에서만(--no-index) 설치한다.
# - docling 은 저장소 wheel(packages/)이라 목록에서 뺀다(setup.sh 가 --no-deps 로 따로 설치).
# - torch 는 CPU wheel(+cpu, download.pytorch.org/whl/cpu)로 바꾸고 nvidia-*/cuda-*/triton 은 뺀다.
# - 대상은 SCP 코드스페이스(Ubuntu 22.04 · x86_64 · Python 3.11). 같은 환경(Linux x86_64 + Python 3.11)에서 돌리는 것이
#   가장 정확하다. macOS 에서도 받을 수 있지만(교차 다운로드) 마커는 실행 중인 python 으로 평가되므로 **Python 3.11 로 돌린다**
#   (PYTHON=<3.11>) — sys_platform·platform_machine 마커는 win32 전용만 버리고 나머지는 마커를 떼고 받는다.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[ -f "${ROOT}/genon/preprocessor/uv.lock" ] || ROOT="$(cd -- "${ROOT}/.." && pwd)"     # build-script/ 에서 돌릴 때
LOCK_DIR="${ROOT}/genon/preprocessor"
OUT="${1:-${ROOT}/wheelhouse-preprocessor-cp311-linux_x86_64}"
PY_VER="${PY_VER:-3.11}"
PYTHON="${PYTHON:-python3}"
# manylinux 태그는 정확히 나열해야 잡힌다(glibc 상한 = Ubuntu 22.04 의 2.35)
PLATFORMS=(${PLATFORMS:-manylinux1_x86_64 manylinux2010_x86_64 manylinux2014_x86_64 manylinux_2_17_x86_64 manylinux_2_24_x86_64 manylinux_2_27_x86_64 manylinux_2_28_x86_64 manylinux_2_31_x86_64 manylinux_2_34_x86_64 manylinux_2_35_x86_64 linux_x86_64})
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cpu}"

got="$("${PYTHON}" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
[ "${got}" = "${PY_VER}" ] || { echo "[ERROR] PYTHON=${PYTHON} 은 ${got} — 대상 ${PY_VER} 과 같은 python 으로 돌리십시오(마커 평가)" >&2; exit 1; }
command -v uv >/dev/null || { echo "[ERROR] uv 가 필요합니다(목록 추출): pip install uv" >&2; exit 1; }

mkdir -p "${OUT}"
uv export --frozen --no-hashes --no-dev --no-emit-project --no-emit-package docling --format requirements-txt \
  --project "${LOCK_DIR}" -o "${OUT}/requirements-lock.raw" >/dev/null
# 필터: nvidia/cuda/triton 제외 · torch(+cpu) · win32 전용 제외 · 플랫폼 마커 제거(교차 다운로드에서 빠지지 않게)
grep -Ev '^(nvidia-|cuda-|triton)' "${OUT}/requirements-lock.raw" \
  | grep -Ev "sys_platform == 'win32'" \
  | sed -E "s/^(torch|torchvision)==([0-9.]+)/\1==\2+cpu/; s/ ; .*(sys_platform|platform_machine).*$//" \
  > "${OUT}/requirements-lock.txt"
rm -f "${OUT}/requirements-lock.raw"

PIPD=("${PYTHON}" -m pip download -q --disable-pip-version-check --no-deps -d "${OUT}" --extra-index-url "${TORCH_INDEX}")
if [ "$(uname -s)" = "Linux" ] && [ "$(uname -m)" = "x86_64" ]; then
  # 같은 환경: wheel 이 없는 것은 sdist 에서 바로 wheel 을 빌드한다
  "${PYTHON}" -m pip wheel -w "${OUT}" -r "${OUT}/requirements-lock.txt" --extra-index-url "${TORCH_INDEX}"
else
  # 교차 다운로드: 목록이 전부 고정(==)이라 패키지마다 --no-deps 로 받는다. 1) 바이너리 wheel
  #   2) 없으면 로컬에서 sdist → wheel 빌드: 순수 파이썬(py3-none-any)이면 동봉, 아니면 sdist 를 동봉(코드스페이스에서 빌드 — gcc 필요)
  args=(--python-version "${PY_VER}" --implementation cp --abi "cp${PY_VER/./}" --abi abi3 --abi none --only-binary=:all:)
  for p in "${PLATFORMS[@]}"; do args+=(--platform "${p}"); done
  SDIST=() ; BUILT=()
  while IFS= read -r req; do
    [ -n "${req}" ] || continue
    if ! "${PIPD[@]}" "${args[@]}" "${req}" >/dev/null 2>&1; then
      tmp="$(mktemp -d)"
      if "${PYTHON}" -m pip wheel -q --disable-pip-version-check --no-deps --no-binary :all: -w "${tmp}" "${req}" >/dev/null 2>&1 \
         && ls "${tmp}"/*-py3-none-any.whl >/dev/null 2>&1; then
        mv "${tmp}"/*-py3-none-any.whl "${OUT}/"; BUILT+=("${req}")
      else
        "${PIPD[@]}" --no-binary :all: "${req}" >/dev/null 2>&1 && SDIST+=("${req}") || { echo "[ERROR] 받지 못함: ${req}" >&2; exit 1; }
      fi
      rm -rf "${tmp}"
    fi
  done < <(grep -Ev '^\s*(#|$)' "${OUT}/requirements-lock.txt")
  [ ${#BUILT[@]} -eq 0 ] || echo "[INFO] 순수 파이썬 sdist 를 wheel 로 빌드해 동봉: ${BUILT[*]}"
  if [ ${#SDIST[@]} -gt 0 ]; then
    "${PIPD[@]}" "${args[@]}" setuptools wheel >/dev/null      # sdist 빌드(격리 환경)가 --no-index 로 찾는 빌드 도구
    echo "[WARN] sdist 로 동봉(코드스페이스 설치 때 빌드 — gcc·python3-dev 필요): ${SDIST[*]}"
  fi
fi
# 직접 URL 참조(en-core-web-sm @ https://…)는 받은 뒤 name==version 으로 바꾼다 — --no-index 설치가 URL 로 나가지 않게
sed -E -i.bak 's#^([A-Za-z0-9_.-]+) @ https?://.*/[A-Za-z0-9_]+-([0-9][A-Za-z0-9.]*)-py[^/]*\.whl$#\1==\2#' "${OUT}/requirements-lock.txt" && rm -f "${OUT}/requirements-lock.txt.bak"
if grep -qE '^[A-Za-z0-9_.-]+ @ ' "${OUT}/requirements-lock.txt"; then
  echo "[ERROR] URL 참조가 남아 있습니다(오프라인 설치 불가): $(grep -E '^[A-Za-z0-9_.-]+ @ ' "${OUT}/requirements-lock.txt" | cut -d' ' -f1 | tr '\n' ' ')" >&2; exit 1
fi
{
  echo "source=$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "target=cp${PY_VER/./}-linux_x86_64"
  echo "wheels=$(ls "${OUT}"/*.whl | wc -l | tr -d ' ')"
  echo "sdists=$(ls "${OUT}"/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
} > "${OUT}/MANIFEST"
echo "[OK] ${OUT} — $(tr '\n' ' ' < "${OUT}/MANIFEST")"
