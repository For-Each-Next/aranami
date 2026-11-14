#!/bin/sh
# Validate immutable code, select it atomically, and replace existing services.
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
for path in deployment-backups deployment-envs; do
    test ! -L "$path"
    if [ -e "$path" ]; then test -d "$path"; fi
done
if [ -L app ]; then
    previous_source=$(readlink app)
    previous_token=${previous_source#app-staging-}
    case "$previous_token" in ''|*[!a-f0-9]*) exit 2 ;; esac
    test "${#previous_token}" -eq 32 && test "$previous_source" = "app-staging-$previous_token"
    python3 "$tool_dir/$previous_source/scripts/toolforge/validate-source.py" runtime "$tool_dir/$previous_source" >/dev/null
elif [ -e app ]; then
    printf '%s\n' 'Expected app to select an immutable generation.' >&2
    exit 2
fi
mkdir deployment.lock || { printf '%s\n' 'Another deployment holds deployment.lock.' >&2; exit 1; }
backup=
backup_created=false
setup_started=false
selected=false

wait_stopped() {
    selector=$1
    remaining=$(kubectl get pods -l "$selector" -o name) || return 1
    if [ -n "$remaining" ]; then
        kubectl wait --for=delete pod -l "$selector" --timeout=180s || return 1
    fi
    remaining=$(kubectl get pods -l "$selector" -o name) || return 1
    test -z "$remaining"
}

drain_hub() {
    pods=$(kubectl get pods -l 'app.kubernetes.io/name=aranami-hub' -o name) || return 1
    # Exactly one publisher must identify and acknowledge its own drain.
    set -- $pods
    test "$#" -eq 1 || { printf '%s\n' 'Expected exactly one running hub pod before drain.' >&2; return 1; }
    kubectl exec "$1" -- python3 "$stage/scripts/toolforge/drain-hub.py" || return 1
    touch "$backup/aranami-hub.drained"
}

pause_service() {
    service=$1
    replicas=$(cat "$backup/$service.replicas")
    kubectl scale deployment "$service" --current-replicas="$replicas" --replicas=0 || return 1
    wait_stopped "app.kubernetes.io/name=$service" || return 1
    # Only confirmed termination permits a replacement publisher to start.
    touch "$backup/$service.stopped"
}

resume_service() {
    service=$1
    replicas=$(cat "$backup/$service.replicas")
    kubectl scale deployment "$service" --current-replicas=0 --replicas="$replicas" || return 1
    rm "$backup/$service.stopped"
    if [ "$service" = aranami-hub ]; then rm -f "$backup/aranami-hub.drained"; fi
}

finish() {
    status=$?
    trap - 0 HUP INT TERM
    set +e
    release_lock=true
    if [ -L app ] && [ "$(readlink app)" = "${stage##*/}" ]; then selected=true; fi
    if [ "$status" -ne 0 ]; then
        if [ "$setup_started" = true ]; then
            toolforge jobs delete "$setup_job" >/dev/null 2>&1
            if ! wait_stopped "app.kubernetes.io/name=$setup_job"; then
                release_lock=false
                printf '%s\n' 'Setup shutdown is unconfirmed; deployment.lock retained for inspection.' >&2
            fi
        fi
        if [ "$selected" = false ]; then
            if [ "$backup_created" = true ]; then
                for service in aranami-web aranami-hub; do
                    if [ -f "$backup/$service.stopped" ]; then resume_service "$service"; fi
                done
            fi
            if [ -f "$backup/aranami-hub.drained" ]; then
                # Work already finished; restart the old source to resume scheduling.
                if pause_service aranami-hub; then
                    resume_service aranami-hub
                else
                    release_lock=false
                    printf '%s\n' 'Old hub recovery is unconfirmed; deployment.lock retained for inspection.' >&2
                fi
            fi
            printf 'Deployment failed before activation; prior source retained. Inspect service state and backup: %s\n' "$backup" >&2
        else
            printf 'Code activated, but service replacement failed. Retained both generations; inspect jobs before retrying. Backup: %s\n' "$backup" >&2
        fi
    fi
    if [ "$release_lock" = true ]; then rmdir deployment.lock; fi
    exit "$status"
}
trap finish 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p deployment-backups deployment-envs
backup="$tool_dir/deployment-backups/source-$token"
# A token is used once; never overwrite another transaction's setup proof.
mkdir "$backup"
backup_created=true
environment="$tool_dir/deployment-envs/source-$token"
receipt="$backup/setup-success.json"
setup_job="aranami-setup-$token"
if [ -L app ]; then cp -P app "$backup/app.before"; fi
# Dependency installation never mutates the active environment or source.
setup_started=true
toolforge jobs run "$setup_job" --image python3.13 --mount=all \
    --command "env ARANAMI_TOOL_DIR=$tool_dir sh $stage/scripts/toolforge/setup-env.sh $stage $receipt $environment" --wait 1800
# --wait is not evidence of a successful container exit: require exact proof.
python3 "$validator" receipt "$stage" "$receipt"
setup_started=false
# Observe current services after setup, which may take several minutes.
toolforge jobs dump -f "$backup/jobs.yaml"
for service in aranami-web aranami-hub; do
    replicas=$(kubectl get deployment "$service" --ignore-not-found -o 'jsonpath={.spec.replicas}')
    case "$replicas" in
        ''|0) continue ;;
        *[!0-9]*) printf 'Invalid replica count for %s.\n' "$service" >&2; exit 1 ;;
    esac
    if [ "$service" = aranami-hub ]; then test "$replicas" = 1; fi
    printf '%s\n' "$replicas" > "$backup/$service.replicas"
done
# Drain before selection or pod deletion; Kubernetes' grace timer has not begun.
if [ -f "$backup/aranami-hub.replicas" ]; then drain_hub; fi
# Preserve the old layout for rollback while installing verified data copies.
toolforge jobs run "aranami-data-$token" --image python3.13 --mount=all \
    --command "env PYTHONPATH=$stage/src PYTHONDONTWRITEBYTECODE=1 $environment/bin/python -m data.migration --receipt $backup/data-success.json" --wait 1800
python3 -c 'import json, os, sys; proof=json.load(open(sys.argv[1])); assert proof["complete"] and os.path.samefile(proof["source"], sys.argv[2])' "$backup/data-success.json" "$stage"
# app is the only selection pointer. Keep every uploaded source at its path.
python3 "$validator" select "$stage"
selected=true
for service in aranami-web aranami-hub; do
    if [ ! -f "$backup/$service.replicas" ]; then continue; fi
    if [ -f "$backup/$service.stopped" ]; then
        resume_service "$service"
    elif [ "$service" = aranami-hub ]; then
        # Do not overlap publishers even if the platform uses rolling restarts.
        pause_service "$service"
        resume_service "$service"
    else
        replicas=$(kubectl get deployment "$service" --ignore-not-found -o 'jsonpath={.spec.replicas}')
        test "$replicas" = "$(cat "$backup/$service.replicas")" || {
            printf 'Service state changed during deployment: %s.\n' "$service" >&2; exit 1;
        }
        old_pods=$(kubectl get pods -l "app.kubernetes.io/name=$service" -o name)
        toolforge jobs restart "$service"
        if [ -n "$old_pods" ]; then
            # Kubernetes supplies whitespace-separated resource names here.
            kubectl wait --for=delete $old_pods --timeout=180s
        fi
    fi
    kubectl rollout status "deployment/$service" --timeout=180s
done
python3 "$validator" retire-data "$stage"
printf '%s\n' 'Validated code activated; existing services replaced. Missing or paused services were not started.' \
    "Backup: $backup" \
    "First website start: toolforge jobs run aranami-web --image python3.13 --continuous --mount=all --port 8000 --publish --command 'exec sh $tool_dir/app/scripts/toolforge/run-web.sh'" \
    "First hub start (live publication): toolforge jobs run aranami-hub --image python3.13 --continuous --mount=all --mem 4Gi --command 'exec sh $tool_dir/app/scripts/toolforge/run-hub.sh'"
