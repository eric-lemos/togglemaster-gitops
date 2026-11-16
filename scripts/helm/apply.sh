#!/usr/bin/env bash

set -Eeuo pipefail

readonly ROUTE_ENABLED="${SELF_HEALING_ENABLED:-false}"
WORK_DIR=""
DATADOG_KEY_CHECKSUM=""
MONITORING_VALUES=()

checkPrerequisites() {
    if [[ ! "${DISCORD_WEBHOOK_URL:-}" =~ ^https://(discord\.com|discordapp\.com)/api/webhooks/[0-9]+/[A-Za-z0-9._-]+/?$ ]]; then
        echo "DISCORD_WEBHOOK_URL must be a Discord webhook URL without /slack or query parameters." >&2
        return 1
    fi
    if [[ -z "${PAGERDUTY_INTEGRATION_KEY:-}" || "$PAGERDUTY_INTEGRATION_KEY" =~ [[:space:]] ]]; then
        echo "PAGERDUTY_INTEGRATION_KEY must be a non-empty Events API v2 integration key without whitespace." >&2
        return 1
    fi
    if [[ -z "${DATADOG_API_KEY:-}" || "$DATADOG_API_KEY" =~ [[:space:]] ]]; then
        echo "DATADOG_API_KEY must be a non-empty Datadog API key without whitespace." >&2
        return 1
    fi
    if [[ "$ROUTE_ENABLED" != true && "$ROUTE_ENABLED" != false ]]; then
        echo "SELF_HEALING_ENABLED must be true or false." >&2
        return 1
    fi
    command -v htpasswd >/dev/null || {
        echo "htpasswd is required to create monitoring credentials." >&2
        return 1
    }
}

cleanupTemporaryFiles() {
    rm -f "$WORK_DIR"/url "$WORK_DIR"/routing-key "$WORK_DIR"/datadog-api-key \
        "$WORK_DIR"/password "$WORK_DIR"/username "$WORK_DIR"/auth "$WORK_DIR"/alert-rules.yaml
    rmdir "$WORK_DIR"
}

createSecrets() {
    kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f -
    printf '%s/slack' "${DISCORD_WEBHOOK_URL%/}" > "$WORK_DIR/url"
    printf '%s' "$PAGERDUTY_INTEGRATION_KEY" > "$WORK_DIR/routing-key"
    printf '%s' "$DATADOG_API_KEY" > "$WORK_DIR/datadog-api-key"
    unset DATADOG_API_KEY
    DATADOG_KEY_CHECKSUM="$(sha256sum "$WORK_DIR/datadog-api-key" | cut -d ' ' -f 1)"
    kubectl -n observability create secret generic discord-webhook \
        --from-file="url=$WORK_DIR/url" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n observability create secret generic pagerduty-integration \
        --from-file="routing-key=$WORK_DIR/routing-key" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n observability create secret generic datadog \
        --from-file="api-key=$WORK_DIR/datadog-api-key" --dry-run=client -o yaml | kubectl apply -f -

    if [[ -z "$(kubectl -n observability get secret monitoring-ui-basic-auth --ignore-not-found -o name)" ]]; then
        openssl rand -hex 24 | tr -d '\n' > "$WORK_DIR/password"
        printf admin > "$WORK_DIR/username"
        htpasswd -nBi admin < "$WORK_DIR/password" > "$WORK_DIR/auth"
        kubectl -n observability create secret generic monitoring-ui-basic-auth \
            --from-file="auth=$WORK_DIR/auth" --from-file="username=$WORK_DIR/username" \
            --from-file="password=$WORK_DIR/password"
    elif ! kubectl -n observability get secret monitoring-ui-basic-auth -o json |
        jq -e 'all(.data.auth, .data.username, .data.password; type == "string" and length > 0)' >/dev/null; then
        echo "The existing monitoring-ui-basic-auth secret must contain auth, username and password." >&2
        return 1
    fi
}

configureMonitoring() {
    MONITORING_VALUES=(-f helm/values/monitoring.yaml)
    if [[ "$ROUTE_ENABLED" == true ]]; then
        if ! kubectl -n observability get secret self-healing -o json |
            jq -e 'all(.data.token, .data["ca.crt"]; type == "string" and length > 0)' >/dev/null; then
            echo "The observability/self-healing secret must contain token and ca.crt before enabling the alert route." >&2
            return 1
        fi
        MONITORING_VALUES+=(-f helm/values/alertmanager.yaml)
    fi
    {
        printf 'additionalPrometheusRulesMap:\n  togglemaster-auth:\n'
        sed 's/^/    /' helm/alert-rules/auth-service.yaml
    } > "$WORK_DIR/alert-rules.yaml"
}

applyHelmCharts() {
    local -a common=(--install --atomic --cleanup-on-fail --wait --timeout 10m --create-namespace)

    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo add grafana https://grafana.github.io/helm-charts
    helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
    helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
    helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
    helm repo add argo https://argoproj.github.io/argo-helm
    helm repo update

    helm upgrade ingress-nginx ingress-nginx/ingress-nginx --version 4.15.1 --namespace ingress-nginx "${common[@]}" -f helm/values/ingress.yaml
    helm upgrade metrics-server metrics-server/metrics-server --version 3.14.0 --namespace kube-system "${common[@]}" -f helm/values/metrics-server.yaml
    helm upgrade argocd argo/argo-cd --version 10.6.0 --namespace argocd "${common[@]}" -f helm/values/argocd.yaml
    helm upgrade monitoring prometheus-community/kube-prometheus-stack --version 92.17.0 --namespace observability "${common[@]}" \
        "${MONITORING_VALUES[@]}" -f "$WORK_DIR/alert-rules.yaml" \
        --set-file grafana.dashboards.togglemaster.ecosystem.json=helm/dashboards/togglemaster.json \
        --set-file grafana.dashboards.togglemaster.otel-collector.json=helm/dashboards/otel-collector.json
    helm upgrade loki grafana/loki --version 7.5.0 --namespace observability "${common[@]}" -f helm/values/loki.yaml
    helm upgrade otel-agent open-telemetry/opentelemetry-collector --version 0.178.2 --namespace observability "${common[@]}" -f helm/values/otel-agent.yaml
    helm upgrade otel-gateway open-telemetry/opentelemetry-collector --version 0.178.2 --namespace observability "${common[@]}" \
        -f helm/values/otel-gateway.yaml --set-string "podAnnotations.datadog-key-checksum=$DATADOG_KEY_CHECKSUM"
}

main() {
    checkPrerequisites
    umask 077
    WORK_DIR="$(mktemp -d)"
    trap cleanupTemporaryFiles EXIT
    createSecrets
    configureMonitoring
    applyHelmCharts
}

main "$@"
