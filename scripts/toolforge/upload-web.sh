#!/bin/sh
# Upload the public log viewer using the existing Toolforge SSH configuration.
set -eu

if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != '--dry-run' ]; }; then
    printf '%s\n' 'Usage: sh scripts/toolforge/upload-web.sh [--dry-run]' >&2
    exit 2
fi

deployment_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
for file in public_html/index.php public_html/logs/index.php \
    log-viewer.lighttpd.conf setup-web.sh; do
    if [ ! -f "$deployment_dir/$file" ] || [ -L "$deployment_dir/$file" ]; then
        printf 'Missing regular deployment file: %s\n' "$file" >&2
        exit 1
    fi
done

printf '%s\n' 'Target: for-each-next@login.toolforge.org (tool aranami)' \
    'Files: public_html/, log-viewer.lighttpd.conf, setup-web.sh'
if [ "${1-}" = '--dry-run' ]; then
    exit 0
fi

upload_archive=$(mktemp)
trap 'rm -f "$upload_archive"' EXIT HUP INT TERM
COPYFILE_DISABLE=1 tar --format=ustar -C "$deployment_dir" -cf "$upload_archive" \
    public_html/index.php public_html/logs/index.php \
    log-viewer.lighttpd.conf setup-web.sh

# Extract as the tool account so uploaded code has the correct owner.
ssh -T -o BatchMode=yes -o ConnectTimeout=15 \
    for-each-next@login.toolforge.org \
    'become aranami tar --no-same-owner -xf - -C /data/project/aranami' \
    < "$upload_archive"
ssh -T -o BatchMode=yes -o ConnectTimeout=15 \
    for-each-next@login.toolforge.org \
    'become aranami sh /data/project/aranami/setup-web.sh'
