#!/usr/bin/env bash
# 표준 설정(genon/preprocessor/resource/)을 각 사이트 완성본(sites/<site>/resource/)에 맞춘다.
#
#   build-script/sync-sites.sh            # manifest.yaml 이 있는 모든 사이트
#   build-script/sync-sites.sh monimo     # 지정한 사이트만
#
# 사이트 manifest.yaml 의 owned 에 적은 파일은 사이트가 소유하므로 건드리지 않는다. 나머지는 표준의
# 사본이라 표준과 똑같이 맞추고, 표준에서 지워진 파일은 사이트에서도 지운다.
# 표준을 고친 뒤 실행한다. 사본이 표준과 다르면 단위 테스트(test_sites_sync_unit.py)가 실패한다.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
PREPROCESSOR_DIR="${REPO_ROOT}/genon/preprocessor"
STANDARD="${PREPROCESSOR_DIR}/resource"

# yaml 만 읽으므로 precheck_custom_fields.sh 와 같은 순서로 python 을 찾는다.
if [ -z "${PYTHON:-}" ]; then
  if [ -x "${PREPROCESSOR_DIR}/.venv/bin/python" ]; then
    PYTHON="${PREPROCESSOR_DIR}/.venv/bin/python"
  elif [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi

if [ $# -gt 0 ]; then
  SITES=("$@")
else
  SITES=()
  for manifest in "${REPO_ROOT}"/sites/*/manifest.yaml; do
    [ -f "${manifest}" ] && SITES+=("$(basename "$(dirname "${manifest}")")")
  done
fi

for site in "${SITES[@]}"; do
  site_dir="${REPO_ROOT}/sites/${site}"
  [ -f "${site_dir}/manifest.yaml" ] || { echo "[ERROR] ${site_dir}/manifest.yaml 이 없습니다." >&2; exit 1; }

  excludes=(--exclude '__pycache__')
  while IFS= read -r name; do
    [ -n "${name}" ] || continue
    excludes+=(--exclude "/${name}")
    [ -f "${site_dir}/resource/${name}" ] || echo "[WARN] ${site}: owned 파일이 없습니다: resource/${name}" >&2
  done < <("${PYTHON}" -c 'import sys, yaml; print("\n".join((yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("owned") or []))' "${site_dir}/manifest.yaml")

  mkdir -p "${site_dir}/resource"
  # --delete 는 표준에서 지워진 사본을 지운다. --exclude 로 뺀 owned 파일은 지우지 않는다.
  rsync -a --delete --itemize-changes "${excludes[@]}" "${STANDARD}/" "${site_dir}/resource/" \
    | sed "s|^|[${site}] |"
  echo "[OK] ${site}: 표준 사본을 맞췄습니다."
done
