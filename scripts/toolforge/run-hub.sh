#!/bin/sh
# Start the data hub and report schedules with existing account configuration.
set -eu
umask 077
tool_dir=${ARANAMI_TOOL_DIR:-/data/project/aranami}
. "$tool_dir/app/scripts/toolforge/runtime-env.sh"
export PYWIKIBOT_DIR="$tool_dir"
exec "$tool_dir/pyvenv-3.15/bin/python" -m backend "$@"
