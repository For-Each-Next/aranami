#!/bin/sh
# Prepare dashboard routing and its launcher without changing running jobs.
set -eu

cd /data/project/aranami
for file in public_html/index.php public_html/logs/index.php public_html/logs/aranami/index.php \
    public_html/dashboard.js public_html/aranami.svg status.php \
    project.php i18n.php logs.php log-viewer.lighttpd.conf run-dashboard.sh \
    toolforge/__init__.py toolforge/__main__.py toolforge/server.py \
    toolforge/collect.py toolforge/homepage.py toolforge/quality.py toolforge/pageviews.py; do
    test -f "$file"
    test ! -L "$file"
done
chmod 750 run-dashboard.sh

include_line='include "/data/project/aranami/log-viewer.lighttpd.conf"'
if ! grep -Fqx "$include_line" .lighttpd.conf 2>/dev/null; then
    printf '\n%s\n' "$include_line" >> .lighttpd.conf
fi

printf '%s\n' 'Dashboard and log viewer uploaded. Start the webservice with:' \
    'toolforge jobs run aranami-logs --image php8.4 --webservice --mount=all' \
    'For an existing viewer, use: toolforge jobs restart aranami-logs' \
    'Start the separate background updater with:' \
    "toolforge jobs run aranami-dashboard --image python3.13 --continuous --mount=all --command '/bin/sh /data/project/aranami/run-dashboard.sh'" \
    'For an existing updater, use: toolforge jobs restart aranami-dashboard'
