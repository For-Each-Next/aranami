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
test ! -L active-deployment.json
if [ -e active-deployment.json ]; then test -f active-deployment.json; fi
mkdir deployment.lock || { printf '%s\n' 'Another deployment holds deployment.lock.' >&2; exit 1; }
backup=
setup_started=false
promoted=false
saved_app=false
rotated_previous=false
environment_changed=false
environment_kind=absent
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
for job in aranami-hub aranami-web; do
    if toolforge jobs show "$job" >/dev/null 2>&1; then toolforge jobs delete "$job"; fi
done
wait_stopped 'app.kubernetes.io/name in (aranami-hub,aranami-web)'
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
if [ -e active-deployment.json ]; then
    cp -p active-deployment.json "$backup/active-deployment.before"
    active_existed=true
fi
active_changed=true
cp "$receipt" active-deployment.json
python3 app/scripts/toolforge/validate-source.py receipt "$tool_dir/app" "$tool_dir/active-deployment.json"
printf '%s\n' 'Validated source and isolated Python 3.15 environment promoted. Applications remain stopped.' \
    "Backup: $backup" \
    "Start website: toolforge jobs run aranami-web --image python3.13 --continuous --mount=all --port 8000 --publish --command 'exec sh $tool_dir/app/scripts/toolforge/run-web.sh'" \
    "Start data hub and publication explicitly: toolforge jobs run aranami-hub --image python3.13 --continuous --mount=all --mem 4Gi --command 'exec sh $tool_dir/app/scripts/toolforge/run-hub.sh'"
