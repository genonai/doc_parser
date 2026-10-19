#!/usr/bin/env bash
# 표준 설정을 각 사이트 완성본(genon/sites/<site>/)에 맞춘다.
#
#   build-script/sync-sites.sh            # manifest.yaml 이 있는 모든 사이트
#   build-script/sync-sites.sh monimo     # 지정한 사이트만
#
# 완성본은 두 자리다(genon/sites/README.md).
#   resource/              ← 표준 genon/preprocessor/resource/
#   activities/<slug>/     ← 표준 genon/preprocessor/activities/<slug>/ 의 설정 파일(코드 processor.py·__init__.py·__main__.py
#                            는 저장소 것이라 복사하지 않는다). 저장소의 **모든** slug 를 맞추고, 사이트에 없는 slug 폴더는
#                            표준 사본으로 만든다 — 사이트 배포는 사이트 완성본만 읽고 저장소로 떨어지지 않는다(#561)
# 사이트 manifest.yaml 의 owned(사이트 루트 기준 경로)에 적은 파일은 사이트가 소유하므로 건드리지 않는다.
# 나머지는 표준의 사본이라 표준과 똑같이 맞추고, 표준에서 지워진 파일은 사이트에서도 지운다.
# 표준을 고친 뒤 실행한다. 사본이 표준과 다르면 단위 테스트(test_sites_sync_unit.py)가 실패한다.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel)"
PREPROCESSOR_DIR="${REPO_ROOT}/genon/preprocessor"

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
  for manifest in "${REPO_ROOT}"/genon/sites/*/manifest.yaml; do
    [ -f "${manifest}" ] && SITES+=("$(basename "$(dirname "${manifest}")")")
  done
fi

# sync_dir <site> <사이트 루트 기준 상대 폴더> <표준 폴더> [rsync 제외...]
#   owned 중 그 폴더 아래 파일은 제외하고, 표준을 사이트 폴더에 --delete 로 맞춘다.
sync_dir() {
  local site="$1" rel="$2" standard="$3"; shift 3
  local site_dir="${REPO_ROOT}/genon/sites/${site}"
  local excludes=(--exclude '__pycache__' "$@")
  while IFS= read -r name; do
    [ -n "${name}" ] || continue
    case "${name}" in
      "${rel}"/*)
        excludes+=(--exclude "/${name#"${rel}"/}")
        [ -f "${site_dir}/${name}" ] || echo "[WARN] ${site}: owned 파일이 없습니다: ${name}" >&2 ;;
    esac
  done <<< "${OWNED}"
  mkdir -p "${site_dir}/${rel}"
  rsync -a --delete --itemize-changes "${excludes[@]}" "${standard}/" "${site_dir}/${rel}/" \
    | sed "s|^|[${site}] ${rel}: |"
}

for site in "${SITES[@]}"; do
  site_dir="${REPO_ROOT}/genon/sites/${site}"
  [ -f "${site_dir}/manifest.yaml" ] || { echo "[ERROR] ${site_dir}/manifest.yaml 이 없습니다." >&2; exit 1; }
  OWNED="$("${PYTHON}" -c 'import sys, yaml; print("\n".join((yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("owned") or []))' "${site_dir}/manifest.yaml")" \
    || { echo "[ERROR] ${site}: manifest.yaml 의 owned 목록을 읽지 못했습니다(PYTHON=${PYTHON}). PyYAML 이 설치된 python 을 PYTHON=<venv python> 으로 지정하십시오." >&2; exit 1; }
  while IFS= read -r name; do
    [ -n "${name}" ] || continue
    case "${name}" in
      resource/*|activities/*/*) ;;
      *) echo "[ERROR] ${site}: owned 경로는 resource/ 또는 activities/<slug>/ 아래여야 합니다: ${name}" >&2; exit 1 ;;
    esac
  done <<< "${OWNED}"

  sync_dir "${site}" resource "${PREPROCESSOR_DIR}/resource"
  for slug_dir in "${site_dir}"/activities/*/; do
    [ -d "${slug_dir}" ] || continue
    slug="$(basename "${slug_dir}")"
    [ -f "${PREPROCESSOR_DIR}/activities/${slug}/processor.py" ] \
      || { echo "[ERROR] ${site}: 표준에 없는 액티비티 폴더입니다: activities/${slug}" >&2; exit 1; }
  done
  for standard in "${PREPROCESSOR_DIR}"/activities/*/; do
    standard="${standard%/}"
    slug="$(basename "${standard}")"
    case "${slug}" in _*|__pycache__) continue ;; esac
    [ -f "${standard}/processor.py" ] || continue
    [ -d "${site_dir}/activities/${slug}" ] || echo "[${site}] activities/${slug}: 사이트에 없어 표준 사본으로 만듭니다"
    sync_dir "${site}" "activities/${slug}" "${standard}" --exclude '/processor.py' --exclude '/__init__.py' --exclude '/__main__.py'
  done
  echo "[OK] ${site}: 표준 사본을 맞췄습니다."
done
