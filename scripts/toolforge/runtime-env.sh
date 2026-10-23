#!/bin/sh
# Source this shared guard before starting either deployed application.
cd "$tool_dir"
test ! -e deployment.lock || { printf '%s\n' 'Source deployment is still in progress.' >&2; exit 1; }
python3 app/scripts/toolforge/validate-source.py receipt "$tool_dir/app" "$tool_dir/active-deployment.json"
export PYTHONPATH="$tool_dir/app/src"
export PYTHONPYCACHEPREFIX="$tool_dir/cache/pycache"
