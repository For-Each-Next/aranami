#!/bin/sh
# Run the standalone read-only updater using existing Toolforge configuration.
set -eu

cd /data/project/aranami
: "${PYWIKIBOT_DIR:=/data/project/aranami}"
export PYWIKIBOT_DIR
exec /data/project/aranami/pyvenv/bin/python -m toolforge serve \
    --days 100 \
    --output /data/project/aranami/cache/toolforge/dashboard.json \
    --log-dir /data/project/aranami/logs "$@"
