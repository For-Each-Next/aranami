#!/bin/sh
# Serve saved public data and browser assets without starting report updates.
set -eu
umask 077
tool_dir=${ARANAMI_TOOL_DIR:-/data/project/aranami}
. "$tool_dir/app/scripts/toolforge/runtime-env.sh"
export PYWIKIBOT_NO_USER_CONFIG=2
exec "$tool_dir/pyvenv-3.15/bin/python" -m backend.public --host 0.0.0.0 --port 8000 --runtime "$tool_dir" "$@"
