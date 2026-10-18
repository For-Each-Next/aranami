#!/bin/sh
# Verify fixed deployment payloads and SSH options without network access.
set -eu

unset TOOLFORGE_SSH_TARGET TOOLFORGE_KNOWN_HOSTS
test_dir=$(CDPATH='' cd -P "$(dirname "$0")" && pwd)
repository_dir=$(CDPATH='' cd "$test_dir/../.." && pwd)
deployment_dir="$repository_dir/src/toolforge"
uploader="$deployment_dir/upload-web.sh"
wrapper="$repository_dir/scripts/toolforge/upload-web.sh"
fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' 0
mkdir "$fixture/bin" "$fixture/deployed"

cat > "$fixture/bin/ssh" <<'FAKE'
#!/bin/sh
set -eu
target_found=false
hosts_found=false
strict_found=false
for argument do
    remote_command=$argument
    if [ "$argument" = "${TOOLFORGE_SSH_TARGET:-for-each-next@login.toolforge.org}" ]; then
        target_found=true
    fi
    if [ "$argument" = "UserKnownHostsFile=${TOOLFORGE_KNOWN_HOSTS-}" ]; then
        hosts_found=true
    fi
    if [ "$argument" = 'StrictHostKeyChecking=yes' ]; then
        strict_found=true
    fi
done
test "$target_found" = true
if [ -n "${TOOLFORGE_KNOWN_HOSTS-}" ]; then
    test "$hosts_found" = true
    test "$strict_found" = true
fi
case "$remote_command" in
    'become aranami tar --no-same-owner -xf - -C /data/project/aranami')
        cat > "$DEPLOYMENT_FIXTURE/upload.tar"
        printf '%s\n' upload >> "$DEPLOYMENT_FIXTURE/calls"
        ;;
    'become aranami sh /data/project/aranami/setup-web.sh')
        printf '%s\n' setup >> "$DEPLOYMENT_FIXTURE/calls"
        ;;
    *)
        printf 'Unexpected SSH command: %s\n' "$remote_command" >&2
        exit 1
        ;;
esac
FAKE
chmod 700 "$fixture/bin/ssh"
PATH="$fixture/bin:$PATH"
DEPLOYMENT_FIXTURE=$fixture
export PATH DEPLOYMENT_FIXTURE

# Both entry points must work independently of the caller's working directory.
cd "$fixture"
sh "$uploader" --dry-run > direct.txt
sh "$wrapper" --dry-run > wrapper.txt
cmp direct.txt wrapper.txt
test ! -e calls
if sh "$uploader" --invalid > invalid.txt 2>&1; then
    printf '%s\n' 'The uploader accepted an unsupported argument.' >&2
    exit 1
fi
if sh "$wrapper" --dry-run extra > invalid.txt 2>&1; then
    printf '%s\n' 'The compatibility wrapper accepted extra arguments.' >&2
    exit 1
fi
test ! -e calls

sh "$wrapper" > upload.txt
printf '%s\n' upload setup > expected-calls
cmp expected-calls calls
tar -tf upload.tar > manifest
LC_ALL=C sort manifest > sorted-manifest
cat > expected-manifest <<'FILES'
i18n.php
log-viewer.lighttpd.conf
logs.php
project.php
public_html/aranami.svg
public_html/dashboard.js
public_html/index.php
public_html/logs/aranami/index.php
public_html/logs/index.php
run-dashboard.sh
setup-web.sh
status.php
toolforge/__init__.py
toolforge/__main__.py
toolforge/collect.py
toolforge/homepage.py
toolforge/pageviews.py
toolforge/quality.py
toolforge/server.py
FILES
cmp expected-manifest sorted-manifest
tar -xf upload.tar -C deployed
while IFS= read -r file; do
    case "$file" in toolforge/*) source_file=${file#toolforge/} ;; *) source_file=$file ;; esac
    cmp "$deployment_dir/$source_file" "deployed/$file"
done < expected-manifest

# A verified temporary known-hosts path, including spaces, remains one SSH argument.
: > 'known hosts'
TOOLFORGE_SSH_TARGET=for-each-next@dev.toolforge.org \
    TOOLFORGE_KNOWN_HOSTS="$fixture/known hosts" \
    sh "$wrapper" > override.txt
printf '%s\n' upload setup >> expected-calls
cmp expected-calls calls
IFS= read -r summary < override.txt
test "$summary" = 'Target: for-each-next@dev.toolforge.org (tool aranami)'

# A missing private PHP helper or linked source module must fail before SSH.
mkdir -p incomplete/public_html/logs/aranami
cp "$uploader" incomplete/upload-web.sh
while IFS= read -r file; do
    case "$file" in toolforge/*) source_file=${file#toolforge/} ;; *) source_file=$file ;; esac
    cp "$deployment_dir/$source_file" "incomplete/$source_file"
done < expected-manifest
rm incomplete/project.php
if sh incomplete/upload-web.sh --dry-run > invalid.txt 2>&1; then
    printf '%s\n' 'The uploader accepted a deployment without project.php.' >&2
    exit 1
fi
cp "$deployment_dir/project.php" incomplete/project.php
rm incomplete/quality.py
ln -s "$deployment_dir/quality.py" incomplete/quality.py
if sh incomplete/upload-web.sh --dry-run > invalid.txt 2>&1; then
    printf '%s\n' 'The uploader accepted a linked source module.' >&2
    exit 1
fi
cmp expected-calls calls

printf '%s\n' 'Toolforge deployment checks passed.'
