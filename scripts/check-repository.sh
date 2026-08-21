#!/usr/bin/env bash

set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repository_root}"

if [[ "$(uv --version)" != "uv 0.12.0"* ]]; then
  echo "uv 0.12.0 is required." >&2
  exit 1
fi

required_files=(
  ".editorconfig"
  ".gitattributes"
  ".github/workflows/repository-checks.yml"
  ".gitignore"
  ".python-version"
  "AGENTS.md"
  "LICENSE.md"
  "README.md"
  "docs/architecture.md"
  "docs/decisions/README.md"
  "docs/decisions/0001-establish-the-python-package-foundation.md"
  "pyproject.toml"
  "scripts/dependency-age-gate.sh"
  "scripts/check_public_types.py"
  "scripts/__init__.py"
  "src/opendle/__init__.py"
  "src/opendle/py.typed"
  "tests/test_package.py"
  "tests/test_check_public_types.py"
  "uv.lock"
  ".claude/skills/repository-tooling/SKILL.md"
  ".claude/skills/shared-library-change/SKILL.md"
  ".claude/skills/selfreview/SKILL.md"
  ".claude/skills/repository-tooling/agents/openai.yaml"
  ".claude/skills/shared-library-change/agents/openai.yaml"
  ".claude/skills/selfreview/agents/openai.yaml"
)

for required_file in "${required_files[@]}"; do
  if [[ ! -f "${required_file}" ]]; then
    echo "Required file is missing: ${required_file}" >&2
    exit 1
  fi
done

for required_link in "CLAUDE.md" ".agents/skills" ".codex/skills"; do
  if [[ ! -L "${required_link}" ]] || [[ ! -e "${required_link}" ]]; then
    echo "Required link is missing or broken: ${required_link}" >&2
    exit 1
  fi
done

for skill_path in .claude/skills/*/SKILL.md; do
  skill_name="$(basename "${skill_path%/SKILL.md}")"
  grep -Fqx "name: ${skill_name}" "${skill_path}"
  grep -Eq '^description: .+' "${skill_path}"
  if grep -Fq '[TODO' "${skill_path}"; then
    echo "Skill contains an unfinished placeholder: ${skill_path}" >&2
    exit 1
  fi
done

for shell_script in scripts/*.sh; do
  if [[ ! -x "${shell_script}" ]]; then
    echo "Script is not executable: ${shell_script}" >&2
    exit 1
  fi
  bash -n "${shell_script}"
done

./scripts/dependency-age-gate.sh check
if OPENDLE_LIB_DEPENDENCY_MIN_AGE_DAYS=13 \
  ./scripts/dependency-age-gate.sh check >/dev/null 2>&1; then
  echo "The dependency age expected-failure check passed unexpectedly." >&2
  exit 1
fi
if ./scripts/dependency-age-gate.sh unsafe-action >/dev/null 2>&1; then
  echo "The dependency age unsafe-input check passed unexpectedly." >&2
  exit 1
fi
./scripts/dependency-age-gate.sh sync
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen pyright
uv run --frozen mypy
uv run --frozen python scripts/check_public_types.py
uv run --frozen pytest

temporary_directory="$(mktemp -d)"
trap 'rm -rf "${temporary_directory}"' EXIT
uv export --quiet --frozen --all-groups --no-emit-project \
  --format requirements-txt \
  --output-file "${temporary_directory}/requirements.txt"
uv run --frozen pip-audit --strict --require-hashes \
  --cache-dir "${temporary_directory}/audit-cache" \
  --requirement "${temporary_directory}/requirements.txt"

distribution_directory="${temporary_directory}/dist"
mkdir "${distribution_directory}"
uv build --no-sources --out-dir "${distribution_directory}"
uv run --frozen twine check "${distribution_directory}"/*

wheel_file="$(find "${distribution_directory}" -maxdepth 1 -name '*.whl' -print -quit)"
uv run --isolated --no-project --with "${wheel_file}" python -c \
  'import importlib.resources; import opendle; assert importlib.resources.files(opendle).joinpath("py.typed").is_file()'

./scripts/dependency-age-gate.sh check
git diff --check
git diff --cached --check

grep -Fqx "Copyright 2026 tubededentifrice" LICENSE.md
grep -Fq "FSL-1.1-ALv2" README.md
grep -Fq "public and source-available" README.md

echo "Repository checks passed."
