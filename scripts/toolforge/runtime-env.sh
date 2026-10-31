#!/bin/sh
# Pin code and Python to one validated generation for this process's lifetime.
cd "$tool_dir"
source_name=$(readlink "$tool_dir/app")
token=${source_name#app-staging-}
case "$token" in ''|*[!a-f0-9]*) printf '%s\n' 'Expected a selected source generation.' >&2; exit 1 ;; esac
test "${#token}" -eq 32 && test "$source_name" = "app-staging-$token"
app_dir="$tool_dir/$source_name"
runtime_python=$(python3 "$app_dir/scripts/toolforge/validate-source.py" runtime "$app_dir")
export PYTHONPATH="$app_dir/src"
export PYTHONPYCACHEPREFIX="$tool_dir/cache/pycache"
