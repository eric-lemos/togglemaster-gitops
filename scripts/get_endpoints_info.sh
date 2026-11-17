#!/usr/bin/env bash

set +x
set -Eeuo pipefail

CONTEXT=""

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    RED=$'\033[31m'
    GREEN=$'\033[32m'
    YELLOW=$'\033[1;33m'
    BOLD=$'\033[1m'
    RESET=$'\033[0m'
else
    RED=""
    GREEN=""
    YELLOW=""
    BOLD=""
    RESET=""
fi

usage() {
    echo "Usage: bash scripts/get_info.sh [all|apps|grafana|prometheus|alertmanager|argocd]"
    echo "Without an argument, retrieves all tools and application endpoints."
}

displayTitle() {
    local message="$1"
    printf '\n%s======================================================================================%s\n' "$YELLOW" "$RESET"
    printf '%s%s%s\n' "$YELLOW" "$message" "$RESET"
    printf '%s======================================================================================%s\n' "$YELLOW" "$RESET"
}

displaySubtitle() {
    local message="$1"
    printf '\n%s--------------------------------------------------------------------------------------%s\n' "$BOLD" "$RESET"
    printf '%s>> %s%s\n' "$BOLD" "$message" "$RESET"
    printf '%s--------------------------------------------------------------------------------------%s\n' "$BOLD" "$RESET"
}

displaySuccess() {
    printf '[%sOK%s] %s\n' "$GREEN" "$RESET" "$1"
}

displayWarning() {
    printf '[%sWARNING%s] %s\n' "$YELLOW" "$RESET" "$1"
}

displayError() {
    printf '[%sERROR%s] %s\n' "$RED" "$RESET" "$1" >&2
}

checkPrerequisites() {
    local selection="$1"
    command -v kubectl >/dev/null || {
        displayError "kubectl was not found."
        return 1
    }
    if [[ "$selection" != apps ]] && ! command -v base64 >/dev/null; then
        displayError "base64 was not found."
        return 1
    fi
    if ! CONTEXT="$(kubectl config current-context)" || [[ -z "$CONTEXT" ]]; then
        displayError "Could not determine the current kubectl context."
        return 1
    fi
}

readLoadBalancerAddress() {
    local namespace="$1" kind="$2" name="$3" address field

    for field in hostname ip; do
        if ! address="$(kubectl --context "$CONTEXT" -n "$namespace" get "$kind" "$name" -o "jsonpath={.status.loadBalancer.ingress[0].$field}")"; then
            displayError "Could not query $kind $namespace/$name."
            return 1
        fi
        if [[ -n "$address" ]]; then
            if [[ "$address" == *:* ]]; then
                address="[$address]"
            fi
            printf '%s' "$address"
            return 0
        fi
    done
    displayError "$kind $namespace/$name does not have a LoadBalancer address yet."
    return 1
}

showEndpoint() {
    local address="$1" path="$2" scheme="${3:-http}"
    if [[ -n "$address" ]]; then
        printf '  Endpoint: %s%s://%s%s%s\n' "$BOLD" "$scheme" "$address" "$path" "$RESET"
    else
        displayWarning "Endpoint unavailable; check the LoadBalancer address."
    fi
}

readSecretField() {
    local namespace="$1" secret="$2" key="$3" encoded value

    if ! encoded="$(kubectl --context "$CONTEXT" -n "$namespace" get secret "$secret" -o "jsonpath={.data.$key}")"; then
        displayError "Could not read Secret $namespace/$secret."
        return 1
    fi
    if [[ -z "$encoded" ]]; then
        displayError "Field $key is missing or empty in Secret $namespace/$secret."
        return 1
    fi
    if ! value="$(printf '%s' "$encoded" | base64 --decode)" || [[ -z "$value" ]]; then
        displayError "Field $key is invalid or empty in Secret $namespace/$secret."
        return 1
    fi
    printf '%s' "$value"
}

showGrafana() {
    local address="$1" username password
    displaySubtitle "Grafana"
    showEndpoint "$address" "/grafana/"
    if ! username="$(readSecretField observability monitoring-grafana admin-user)" ||
        ! password="$(readSecretField observability monitoring-grafana admin-password)"; then
        return 1
    fi
    printf '  Username: %s\n  Password: %s\n' "$username" "$password"
    displayWarning "The password stored in the Secret may not reflect changes made in Grafana."
}

showMonitoring() {
    local selection="$1" address="$2" username password
    if [[ "$selection" == all || "$selection" == prometheus ]]; then
        displaySubtitle "Prometheus"
        showEndpoint "$address" "/prometheus/"
    fi
    if [[ "$selection" == all || "$selection" == alertmanager ]]; then
        displaySubtitle "Alertmanager"
        showEndpoint "$address" "/alertmanager/"
    fi
    if ! username="$(readSecretField observability monitoring-ui-basic-auth username)" ||
        ! password="$(readSecretField observability monitoring-ui-basic-auth password)"; then
        return 1
    fi
    if [[ "$selection" == all ]]; then
        printf '  Shared Prometheus and Alertmanager credentials:\n'
    fi
    printf '  Username: %s\n  Password: %s\n' "$username" "$password"
}

showApps() {
    local address="$1" app
    displaySubtitle "ToggleMaster application health endpoints"
    if [[ -z "$address" ]]; then
        displayWarning "Endpoints unavailable; check the Ingress address."
        return
    fi
    printf '  %-24s %s\n' "APPLICATION" "ENDPOINT"
    for app in auth-service flag-service targeting-service evaluation-service analytics-service; do
        printf '  %-24s http://%s/%s/health\n' "$app" "$address" "$app"
    done
}

showArgoCD() {
    local address="" password failed=0
    displaySubtitle "Argo CD"
    if ! address="$(readLoadBalancerAddress argocd service argocd-server)"; then
        failed=1
    fi
    showEndpoint "$address" "/" "https"
    displayWarning "The default Argo CD certificate may be self-signed."
    if ! password="$(readSecretField argocd argocd-initial-admin-secret password)"; then
        displayError "If the initial Secret was removed, consult the Argo CD password reset documentation."
        return 1
    fi
    printf '  Initial username: admin\n  Initial password: %s\n' "$password"
    displayWarning "The initial password may no longer be current if it has been changed."
    return "$failed"
}

main() {
    local selection="${1:-all}" address="" failed=0

    if (( $# > 1 )); then
        usage >&2
        return 2
    fi
    case "$selection" in
        -h|--help) usage; return 0 ;;
        all|apps|grafana|prometheus|alertmanager|argocd) ;;
        *) usage >&2; return 2 ;;
    esac

    checkPrerequisites "$selection"
    displayTitle "ToggleMaster — Endpoints and credentials"
    displaySuccess "Kubernetes context: $CONTEXT"
    if [[ "$selection" != apps ]]; then
        displayWarning "Passwords will be displayed in the terminal. Do not record or share your screen."
    fi

    if [[ "$selection" != argocd ]]; then
        if ! address="$(readLoadBalancerAddress togglemaster ingress togglemaster-ingress)"; then
            failed=1
        fi
    fi

    case "$selection" in
        all)
            showGrafana "$address" || failed=1
            showMonitoring all "$address" || failed=1
            showArgoCD || failed=1
            showApps "$address"
            ;;
        apps) showApps "$address" ;;
        grafana) showGrafana "$address" || failed=1 ;;
        prometheus|alertmanager) showMonitoring "$selection" "$address" || failed=1 ;;
        argocd) showArgoCD || failed=1 ;;
    esac
    return "$failed"
}

main "$@"
