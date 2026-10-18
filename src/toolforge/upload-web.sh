#!/bin/sh
# Upload the standalone dashboard, updater, and log viewer with existing SSH access.
set -eu

if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != '--dry-run' ]; }; then
    printf '%s\n' 'Usage: sh src/toolforge/upload-web.sh [--dry-run]' >&2
    exit 2
fi

deployment_dir=$(CDPATH='' cd -P "$(dirname "$0")" && pwd)
ssh_target=${TOOLFORGE_SSH_TARGET:-for-each-next@login.toolforge.org}
case "$ssh_target" in
    -*|*[!A-Za-z0-9_.@-]*)
        printf '%s\n' 'Expected an SSH host or user@host in TOOLFORGE_SSH_TARGET.' >&2
        exit 2
        ;;
esac
if [ -n "${TOOLFORGE_KNOWN_HOSTS-}" ] && [ ! -r "$TOOLFORGE_KNOWN_HOSTS" ]; then
    printf '%s\n' 'TOOLFORGE_KNOWN_HOSTS must name a readable known-hosts file.' >&2
    exit 2
fi

for file in public_html/index.php public_html/maintenance/index.php \
    public_html/logs/index.php public_html/logs/aranami/index.php \
    public_html/dashboard.js public_html/aranami.svg \
    status.php project.php assessment.php i18n.php logs.php log-viewer.lighttpd.conf setup-web.sh run-dashboard.sh \
    __init__.py __main__.py server.py collect.py homepage.py quality.py pageviews.py backlog.py \
    backlog_config.py maintenance.json; do
    if [ ! -f "$deployment_dir/$file" ] || [ -L "$deployment_dir/$file" ]; then
        printf 'Missing regular deployment file: %s\n' "$file" >&2
        exit 1
    fi
done

printf 'Target: %s (tool aranami)\n' "$ssh_target"
printf '%s\n' 'Files: public_html/, status.php, project.php, assessment.php, i18n.php, logs.php, toolforge/, log-viewer.lighttpd.conf, setup-web.sh, run-dashboard.sh'
if [ "${1-}" = '--dry-run' ]; then
    exit 0
fi

upload_archive=$(mktemp)
trap 'rm -f "$upload_archive"' 0 HUP INT TERM
# Fixed filenames exclude caches, local configuration, and credentials.
COPYFILE_DISABLE=1 tar --format=ustar -C "$deployment_dir/.." -cf "$upload_archive" \
    toolforge/__init__.py toolforge/__main__.py toolforge/server.py \
    toolforge/collect.py toolforge/homepage.py toolforge/quality.py toolforge/pageviews.py \
    toolforge/backlog.py toolforge/backlog_config.py toolforge/maintenance.json
COPYFILE_DISABLE=1 tar --format=ustar -C "$deployment_dir" -rf "$upload_archive" \
    public_html/index.php public_html/maintenance/index.php \
    public_html/logs/index.php public_html/logs/aranami/index.php \
    public_html/dashboard.js public_html/aranami.svg \
    status.php project.php assessment.php i18n.php logs.php \
    log-viewer.lighttpd.conf setup-web.sh run-dashboard.sh

# Keep optional SSH arguments quoted; never interpret configuration as shell code.
run_ssh() {
    remote_command=$1
    set -- -T -o BatchMode=yes -o ConnectTimeout=15
    if [ -n "${TOOLFORGE_KNOWN_HOSTS-}" ]; then
        set -- "$@" -o StrictHostKeyChecking=yes \
            -o "UserKnownHostsFile=$TOOLFORGE_KNOWN_HOSTS"
    fi
    ssh "$@" "$ssh_target" "$remote_command"
}

# Extract as the tool account so uploaded code has the correct owner.
run_ssh 'become aranami tar --no-same-owner -xf - -C /data/project/aranami' \
    < "$upload_archive"
run_ssh 'become aranami sh /data/project/aranami/setup-web.sh'
