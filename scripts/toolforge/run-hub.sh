#!/bin/sh
# Start the data hub and report schedules with existing account configuration.
set -eu
umask 077
tool_dir=${ARANAMI_TOOL_DIR:-/data/project/aranami}
cd "$tool_dir"
test ! -e deployment.lock || { printf '%s\n' 'Source deployment is still in progress.' >&2; exit 1; }
python3 app/scripts/toolforge/validate-source.py receipt "$tool_dir/app" "$tool_dir/active-deployment.json"
export PYTHONPATH="$tool_dir/app/src"
export PYTHONPYCACHEPREFIX="$tool_dir/cache/pycache"
export PYWIKIBOT_DIR="$tool_dir"
exec "$tool_dir/pyvenv-3.15/bin/python" -m backend "$@"
