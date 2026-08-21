#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: scripts/dependency-age-gate.sh [add|remove|lock|check|sync] [arguments]" >&2
}

action="${1:-check}"
if [[ "$#" -gt 0 ]]; then
  shift
fi

minimum_age_days="${OPENDLE_LIB_DEPENDENCY_MIN_AGE_DAYS:-14}"
if ! [[ "${minimum_age_days}" =~ ^[0-9]+$ ]] || \
  [[ "${minimum_age_days}" -lt 14 ]]; then
  echo "OPENDLE_LIB_DEPENDENCY_MIN_AGE_DAYS must be an integer of 14 or more." >&2
  exit 2
fi

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if date -u -v-1d +%Y-%m-%d >/dev/null 2>&1; then
  cutoff="$(date -u -v-"${minimum_age_days}"d +%Y-%m-%dT00:00:00Z)"
else
  cutoff="$(date -u -d "${minimum_age_days} days ago" +%Y-%m-%dT00:00:00Z)"
fi

case "${action}" in
  add)
    if [[ "$#" -eq 0 ]]; then
      usage
      exit 2
    fi
    uv add --project "${repository_root}" --exclude-newer "${cutoff}" "$@"
    ;;
  remove)
    if [[ "$#" -eq 0 ]]; then
      usage
      exit 2
    fi
    uv remove --project "${repository_root}" --exclude-newer "${cutoff}" "$@"
    ;;
  lock)
    uv lock --project "${repository_root}" --exclude-newer "${cutoff}" "$@"
    ;;
  check)
    if [[ "$#" -ne 0 ]]; then
      usage
      exit 2
    fi
    lock_path="${repository_root}/uv.lock"
    if [[ ! -f "${lock_path}" ]]; then
      echo "uv.lock is missing. Run the lock action first." >&2
      exit 1
    fi
    locked_cutoff="$(sed -n \
      '/^\[options\]/,/^\[/ s/^exclude-newer = "\(.*\)"/\1/p' \
      "${lock_path}")"
    if ! [[ "${locked_cutoff}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T00:00:00Z$ ]]; then
      echo "uv.lock does not contain a valid dependency-age cutoff." >&2
      exit 1
    fi
    if [[ "${locked_cutoff}" > "${cutoff}" ]]; then
      echo "uv.lock permits dependencies that are less than ${minimum_age_days} days old." >&2
      exit 1
    fi
    uv lock --project "${repository_root}" --check \
      --exclude-newer "${locked_cutoff}"
    ;;
  sync)
    uv sync --project "${repository_root}" --frozen "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 2
    ;;
esac

echo "Dependency age gate used a minimum age of ${minimum_age_days} days."
