#!/bin/sh
# Install the log viewer's routing without replacing other webservice settings.
set -eu

cd /data/project/aranami
test -f public_html/index.php
test -f public_html/logs/index.php
test -f log-viewer.lighttpd.conf

include_line='include "/data/project/aranami/log-viewer.lighttpd.conf"'
if ! grep -Fqx "$include_line" .lighttpd.conf 2>/dev/null; then
    printf '\n%s\n' "$include_line" >> .lighttpd.conf
fi

printf '%s\n' 'Log viewer uploaded. Start the webservice with:' \
    'toolforge jobs run aranami-logs --image php8.4 --webservice --mount=all' \
    'For an existing viewer, use: toolforge jobs restart aranami-logs'
