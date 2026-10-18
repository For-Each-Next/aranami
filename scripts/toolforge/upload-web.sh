#!/bin/sh
# Keep the existing web deployment command working from the standalone tool.
set -eu

wrapper_dir=$(CDPATH='' cd -P "$(dirname "$0")" && pwd)
exec sh "$wrapper_dir/../../src/toolforge/upload-web.sh" "$@"
