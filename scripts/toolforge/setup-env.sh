#!/bin/sh
# Validate a staged generation in a fresh environment without publication.
set -eu
umask 027
tool_dir=${ARANAMI_TOOL_DIR:-/data/project/aranami}
test "$#" -eq 3 || { printf '%s\n' 'Usage: setup-env.sh STAGING_DIR RECEIPT NEW_ENVIRONMENT' >&2; exit 2; }
app_dir=$1
receipt=$2
environment=$3
case "$tool_dir" in
    ''|/|*[!A-Za-z0-9_./-]*) printf '%s\n' 'Expected a canonical tool directory.' >&2; exit 2 ;;
esac
test -d "$tool_dir" && test ! -L "$tool_dir"
# Preserve the mount name shared by jobs even when the bastion resolves its
# ancestors to a physical NFS path. Deployment descendants must not be links.
test "$(CDPATH='' cd -L "$tool_dir" && pwd -L)" = "$tool_dir"
physical_tool_dir=$(CDPATH='' cd -P "$tool_dir" && pwd)
token=${app_dir#"$tool_dir/app-staging-"}
case "$token" in ''|*[!a-f0-9]*) exit 2 ;; esac
test "${#token}" -eq 32
test "$app_dir" = "$tool_dir/app-staging-$token"
test "$(dirname "$environment")" = "$tool_dir/deployment-envs"
case "${environment##*/}" in ''|*[!A-Za-z0-9_.-]*) exit 2 ;; esac
test ! -e "$environment" && test ! -L "$environment"
test ! -L "$tool_dir/deployment-envs"
test "$(dirname "$(dirname "$receipt")")" = "$tool_dir/deployment-backups"
test "${receipt##*/}" = setup-success.json
test ! -L "$tool_dir/deployment-backups"
test ! -L "$(dirname "$receipt")"
test "$(CDPATH='' cd -P "$(dirname "$receipt")" && pwd)" = "$physical_tool_dir/deployment-backups/$(basename "$(dirname "$receipt")")"
test ! -e "$receipt" && test ! -L "$receipt"
validator="$app_dir/scripts/toolforge/validate-source.py"
python3 "$validator" verify "$app_dir"
cd "$tool_dir"
export LC_ALL=C.UTF-8 LANG=C.UTF-8 PYWIKIBOT_NO_USER_CONFIG=2
export PYTHONPYCACHEPREFIX="$tool_dir/cache/pycache"
export PYTHONPATH="$app_dir/src"
export UV_CACHE_DIR="$tool_dir/cache/uv"
export UV_PYTHON_INSTALL_DIR="$tool_dir/python"
export UV_PROJECT_ENVIRONMENT="$environment"
bootstrap="$tool_dir/uv-bootstrap"
test ! -L "$bootstrap"
if [ ! -x "$bootstrap/bin/uv" ]; then
    python3 -m venv "$bootstrap"
fi
if [ ! -x "$bootstrap/bin/uv" ] || [ "$("$bootstrap/bin/uv" --version)" != 'uv 0.13.3' ]; then
    "$bootstrap/bin/python" -m pip install --no-input 'uv==0.13.3'
fi
"$bootstrap/bin/uv" python install 3.15.0
managed_python=$("$bootstrap/bin/uv" python find --managed-python --no-project 3.15.0)
case "$managed_python" in
    "$tool_dir"/python/*/bin/python3.15|"$physical_tool_dir"/python/*/bin/python3.15) ;;
    *) printf '%s\n' 'Expected the managed Python 3.15.0 interpreter.' >&2; exit 1 ;;
esac
"$bootstrap/bin/uv" sync --locked --no-dev --python "$managed_python" --project "$app_dir"
"$environment/bin/python" - <<'PY'
import importlib
import json
import os
import sys
from pathlib import Path

assert sys.version_info[:3] == (3, 15, 0), sys.version
assert sys.version_info.releaselevel == "final", sys.version
root = Path(os.environ["PYTHONPATH"]).parent
from shared.config import TASK_DEFINITIONS, routine_configuration
from shared.deployment import project_version
json.dumps(routine_configuration(), allow_nan=False)
assert json.loads((root / "src/shared/version.json").read_text())["version"] == project_version()
for name in TASK_DEFINITIONS:
    importlib.import_module(f"updater.jobs.{name}")
import backend.hub
import backend.public
import backend.server
import backend.website.collect
import webservice.pages
print(f"Validated Aranami {project_version()} on Python {sys.version.split()[0]}; publication was not started.")
PY
# This unique proof is emitted only after hashes, dependencies, and imports pass.
"$environment/bin/python" "$validator" record "$app_dir" "$receipt" --environment "$environment"
