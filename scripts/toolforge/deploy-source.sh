#!/bin/sh
# Validate an isolated generation, then promote it with applications stopped.
set -eu
umask 027
tool_dir=${ARANAMI_TOOL_DIR:-/data/project/aranami}
case "$tool_dir" in
    ''|/|*[!A-Za-z0-9_./-]*) printf '%s\n' 'Expected a canonical tool directory.' >&2; exit 2 ;;
esac
test -d "$tool_dir" && test ! -L "$tool_dir"
# Toolforge's /data/project mount has a different physical path on the bastion.
# Keep the portable mount name, but reject non-normalized caller paths.
test "$(CDPATH='' cd -L "$tool_dir" && pwd -L)" = "$tool_dir"
stage=${1-}
token=${stage#"$tool_dir/app-staging-"}
case "$token" in
    ''|*[!a-f0-9]*) printf '%s\n' 'Supply the exact uploaded staging directory.' >&2; exit 2 ;;
esac
test "${#token}" -eq 32
test "$stage" = "$tool_dir/app-staging-$token"
validator="$stage/scripts/toolforge/validate-source.py"
# No jobs, source directories, or runtime data change before this succeeds.
python3 "$validator" verify "$stage"
cd "$tool_dir"
for path in app app.previous deployment-backups deployment-envs; do
    test ! -L "$path"
    if [ -e "$path" ]; then test -d "$path"; fi
done
for path in .lighttpd.conf active-deployment.json; do
    test ! -L "$path"
    if [ -e "$path" ]; then test -f "$path"; fi
done
if [ -L public_html ]; then
    test "$(readlink public_html)" = app/src/webservice/public_html
fi
test ! -L public_html.previous
if [ -e public_html.previous ]; then test -d public_html.previous; fi
if [ -e public_html ] && [ ! -L public_html ]; then test -d public_html; fi
mkdir deployment.lock || { printf '%s\n' 'Another deployment holds deployment.lock.' >&2; exit 1; }
backup=
setup_started=false
promoted=false
saved_app=false
rotated_previous=false
environment_changed=false
environment_kind=absent
public_changed=false
public_kind=absent
rotated_public=false
configuration_changed=false
configuration_existed=false
active_changed=false
active_existed=false

wait_stopped() {
    selector=$1
    remaining=$(kubectl get pods -l "$selector" -o name) || return 1
    if [ -n "$remaining" ]; then
        kubectl wait --for=delete pod -l "$selector" --timeout=180s || return 1
    fi
    remaining=$(kubectl get pods -l "$selector" -o name) || return 1
    test -z "$remaining"
}

finish() {
    status=$?
    trap - 0 HUP INT TERM
    set +e
    rollback_safe=true
    if [ "$status" -ne 0 ] && [ "$setup_started" = true ]; then
        toolforge jobs delete "$setup_job" >/dev/null 2>&1
        if ! wait_stopped "app.kubernetes.io/name=$setup_job"; then
            rollback_safe=false
            printf '%s\n' 'Setup shutdown could not be confirmed; inspect before restoring source.' >&2
        fi
    fi
    if [ "$status" -ne 0 ] && [ "$rollback_safe" = true ]; then
        if [ "$active_changed" = true ]; then
            if [ "$active_existed" = true ]; then
                cp -p "$backup/active-deployment.before" active-deployment.json
            else
                rm -f active-deployment.json
            fi
        fi
        if [ "$configuration_changed" = true ]; then
            if [ "$configuration_existed" = true ]; then
                cp -p "$backup/lighttpd.conf.before" .lighttpd.conf
            else
                rm -f .lighttpd.conf
            fi
        fi
        if [ "$public_changed" = true ]; then
            if [ -L public_html ] && [ "$(readlink public_html)" = app/src/webservice/public_html ]; then
                rm public_html
            fi
            if [ "$public_kind" = directory ]; then mv public_html.previous public_html; fi
        fi
        if [ "$rotated_public" = true ]; then mv "$backup/older-public-html" public_html.previous; fi
        if [ "$environment_changed" = true ]; then
            if [ -L pyvenv-3.15 ]; then rm pyvenv-3.15; fi
            if [ "$environment_kind" = directory ]; then
                mv "$backup/previous-environment" pyvenv-3.15
            elif [ "$environment_kind" = link ]; then
                previous_environment=$(cat "$backup/previous-environment-link")
                ln -s "$previous_environment" pyvenv-3.15
            fi
        fi
        if [ "$promoted" = true ]; then mv app "$backup/rejected-app"; fi
        if [ "$saved_app" = true ]; then mv app.previous app; fi
        if [ "$rotated_previous" = true ]; then mv "$backup/older-app" app.previous; fi
        printf 'Deployment failed. Prior source was retained; no application was started. Backup: %s\n' "$backup" >&2
    fi
    rmdir deployment.lock
    exit "$status"
}
trap finish 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p deployment-backups deployment-envs
backup=$(mktemp -d "$tool_dir/deployment-backups/source-$token.XXXXXXXX")
environment="$tool_dir/deployment-envs/${backup##*/}"
receipt="$backup/setup-success.json"
setup_job="aranami-setup-$token"
toolforge jobs dump -f "$backup/jobs.yaml"
# Dependency installation never mutates the active environment or source.
setup_started=true
toolforge jobs run "$setup_job" --image python3.13 --mount=all \
    --command "env ARANAMI_TOOL_DIR=$tool_dir sh $stage/scripts/toolforge/setup-env.sh $stage $receipt $environment" --wait 1800
# --wait is not evidence of a successful container exit: require exact proof.
python3 "$validator" receipt "$stage" "$receipt"
setup_started=false
for job in aranami-hub aranami aranami-dashboard aranami-logs; do
    if toolforge jobs show "$job" >/dev/null 2>&1; then toolforge jobs delete "$job"; fi
done
wait_stopped 'app.kubernetes.io/name in (aranami-hub,aranami,aranami-dashboard,aranami-logs)'
if [ -e app.previous ]; then
    mv app.previous "$backup/older-app"
    rotated_previous=true
fi
if [ -e app ]; then
    mv app app.previous
    saved_app=true
fi
mv "$stage" app
promoted=true
if [ -L pyvenv-3.15 ]; then
    readlink pyvenv-3.15 > "$backup/previous-environment-link"
    environment_kind=link
    rm pyvenv-3.15
elif [ -e pyvenv-3.15 ]; then
    test -d pyvenv-3.15
    mv pyvenv-3.15 "$backup/previous-environment"
    environment_kind=directory
fi
environment_changed=true
ln -s "deployment-envs/${environment##*/}" pyvenv-3.15
if [ -e public_html ] && [ ! -L public_html ]; then
    test -d public_html
    public_kind=directory
    if [ -e public_html.previous ]; then
        mv public_html.previous "$backup/older-public-html"
        rotated_public=true
    fi
    mv public_html public_html.previous
    public_changed=true
elif [ -L public_html ]; then
    public_kind=link
fi
if [ ! -L public_html ]; then
    public_changed=true
    ln -s app/src/webservice/public_html public_html
fi
if [ -e .lighttpd.conf ]; then configuration_existed=true; fi
# The validator keeps a byte-for-byte backup before atomic routing replacement.
if [ "$configuration_existed" = true ]; then cp -p .lighttpd.conf "$backup/lighttpd.conf.before"; fi
configuration_changed=true
python3 app/scripts/toolforge/validate-source.py configure-web "$tool_dir" "$backup"
if [ -e active-deployment.json ]; then
    cp -p active-deployment.json "$backup/active-deployment.before"
    active_existed=true
fi
active_changed=true
cp "$receipt" active-deployment.json
python3 app/scripts/toolforge/validate-source.py receipt "$tool_dir/app" "$tool_dir/active-deployment.json"
# Archive only exact obsolete feature caches, preserving unrelated runtime data.
mkdir "$backup/retired-cache"
for obsolete in cache/toolforge/quality-video-games-all.parquet \
    cache/toolforge/recognition-observations.parquet cache/toolforge/quality-refresh.lock; do
    if [ -f "$obsolete" ] && [ ! -L "$obsolete" ]; then
        if ! mv "$obsolete" "$backup/retired-cache/$(basename "$obsolete")"; then
            printf 'Could not archive obsolete cache: %s\n' "$obsolete" >&2
        fi
    fi
done
printf '%s\n' 'Validated source and isolated Python 3.15 environment promoted. Applications remain stopped.' \
    "Backup: $backup" \
    'Start website: toolforge jobs run aranami-logs --image php8.4 --webservice --mount=all' \
    "Start data hub and publication explicitly: toolforge jobs run aranami-hub --image python3.13 --continuous --mount=all --command 'exec sh $tool_dir/app/scripts/toolforge/run-hub.sh'"
