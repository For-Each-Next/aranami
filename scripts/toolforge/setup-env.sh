#!/bin/sh
# Run inside a Toolforge python3.13 job after `become aranami`.
set -eu
umask 077

tool_dir=/data/project/aranami
venv_dir="$tool_dir/pyvenv"
wheel_manifest="$tool_dir/aranami-wheel.txt"

set_file_permissions() {
    permission_mode=$1
    permission_path=$2

    if [ -L "$permission_path" ] || [ ! -f "$permission_path" ]; then
        printf '%s\n' "Expected a regular file, not a symlink: $permission_path" >&2
        exit 1
    fi

    if ! chmod "$permission_mode" "$permission_path"; then
        printf '%s\n' \
            "Cannot set permissions: $permission_path" \
            'On the bastion, become aranami and use take to transfer ownership first.' >&2
        exit 1
    fi
}

if [ "$(uname -s)" != Linux ] || [ "$(id -un)" != tools.aranami ]; then
    printf '%s\n' 'Run this script in a Toolforge python3.13 job as tools.aranami.' >&2
    exit 1
fi

# Use the same Linux UTF-8 locale for setup and runtime jobs.
export LC_ALL=C.UTF-8 LANG=C.UTF-8 LC_CTYPE=C.UTF-8

cd "$tool_dir"

# The uploader records the exact wheel selected from the local dist/ directory.
if [ -L "$wheel_manifest" ] || [ ! -f "$wheel_manifest" ]; then
    printf '%s\n' 'Missing regular aranami-wheel.txt; run scripts/upload_toolforge.py first.' >&2
    exit 1
fi
wheel_name=$(cat "$wheel_manifest")
case "$wheel_name" in
    aranami-*.whl) ;;
    *)
        printf '%s\n' 'Invalid Aranami wheel filename in aranami-wheel.txt.' >&2
        exit 1
        ;;
esac
case "$wheel_name" in
    *[!A-Za-z0-9_.+-]*)
        printf '%s\n' 'Expected a wheel basename without paths or whitespace.' >&2
        exit 1
        ;;
esac
wheel="$tool_dir/$wheel_name"
printf 'Selected wheel: %s\n' "$wheel_name"

# Keep uploaded code accessible to the tool's group of maintainers.
set_file_permissions 750 "$tool_dir/setup-env.sh"
set_file_permissions 640 "$wheel"
set_file_permissions 600 "$wheel_manifest"

# Protect user-created configuration without reading its contents.
for credential in user-config.py user-password.py user-password.cfg; do
    credential_path="$tool_dir/$credential"
    if [ -e "$credential_path" ] || [ -L "$credential_path" ]; then
        set_file_permissions 600 "$credential_path"
    fi
done

# Toolforge manages replica.my.cnf; its ownership and permissions are read-only.
if [ -e "$tool_dir/replica.my.cnf" ] || [ -L "$tool_dir/replica.my.cnf" ]; then
    if [ ! -f "$tool_dir/replica.my.cnf" ] || [ ! -r "$tool_dir/replica.my.cnf" ]; then
        printf '%s\n' 'Toolforge replica.my.cnf is not a readable file; contact Toolforge support.' >&2
        exit 1
    fi
fi

# Support the .my.cnf -> replica.my.cnf link used by Aranami on Toolforge.
if [ -L "$tool_dir/.my.cnf" ]; then
    case "$(readlink "$tool_dir/.my.cnf")" in
        replica.my.cnf|"$tool_dir/replica.my.cnf")
            if [ ! -f "$tool_dir/.my.cnf" ] || [ ! -r "$tool_dir/.my.cnf" ]; then
                printf '%s\n' 'The .my.cnf link does not point to readable replica credentials.' >&2
                exit 1
            fi
            ;;
        *)
            printf '%s\n' 'Expected .my.cnf to link to the tool account replica.my.cnf.' >&2
            exit 1
            ;;
    esac
elif [ -e "$tool_dir/.my.cnf" ]; then
    set_file_permissions 600 "$tool_dir/.my.cnf"
fi

if [ -L "$venv_dir" ]; then
    printf '%s\n' "Expected an environment directory, not a symlink: $venv_dir" >&2
    exit 1
fi

if [ ! -r "$wheel" ]; then
    printf '%s\n' "Missing or unreadable wheel: $wheel" >&2
    exit 1
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else "Use the Toolforge python3.13 image")'

if [ -e "$venv_dir" ]; then
    if [ ! -d "$venv_dir" ] || [ ! -f "$venv_dir/pyvenv.cfg" ] || [ ! -x "$venv_dir/bin/python" ]; then
        printf '%s\n' "Existing path is not a usable virtual environment: $venv_dir" >&2
        exit 1
    fi
    printf '%s\n' "Reusing existing environment: $venv_dir"
else
    python3 -m venv "$venv_dir"
fi

# Check the interpreter before allowing pip to modify an existing environment.
"$venv_dir/bin/python" - "$venv_dir" <<'PY'
import sys
from pathlib import Path

if sys.version_info[:2] != (3, 13):
    raise SystemExit("The virtual environment must use Python 3.13.")
if sys.prefix == sys.base_prefix or Path(sys.prefix).resolve() != Path(sys.argv[1]).resolve():
    raise SystemExit("The interpreter does not belong to the expected virtual environment.")
PY

chmod 700 "$venv_dir"
"$venv_dir/bin/python" -m pip install --no-input --force-reinstall --no-deps "$wheel"
"$venv_dir/bin/python" -m pip install --no-input "$wheel" 'pywikibot[mwoauth]'
"$venv_dir/bin/python" -m pip check

# Isolate import validation from any existing bot configuration and credentials.
validation_dir=$(mktemp -d)
trap 'rm -rf -- "$validation_dir"' 0
: > "$validation_dir/user-config.py"
PYWIKIBOT_DIR="$validation_dir" PYWIKIBOT_NO_USER_CONFIG=2 "$venv_dir/bin/python" - <<'PY'
from importlib.metadata import version

import aranami
import mwoauth
import polars
import sqlalchemy

print(f"Aranami {version('aranami')}: imports OK; OAuth support installed")
PY

printf '%s\n' "Environment ready: $venv_dir"
