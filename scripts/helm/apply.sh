#!/usr/bin/env bash
set -euo pipefail

if [[ ! "${DISCORD_WEBHOOK_URL:-}" =~ ^https://(discord\.com|discordapp\.com)/api/webhooks/[0-9]+/[A-Za-z0-9._-]+/?$ ]]; then
  echo "DISCORD_WEBHOOK_URL must be a Discord webhook URL without /slack or query parameters." >&2
  exit 1
fi
if [[ -z "${PAGERDUTY_INTEGRATION_KEY:-}" || "$PAGERDUTY_INTEGRATION_KEY" =~ [[:space:]] ]]; then
  echo "PAGERDUTY_INTEGRATION_KEY must be a non-empty Events API v2 integration key without whitespace." >&2
  exit 1
fi
route_enabled="${SELF_HEALING_ALERT_ROUTE_ENABLED:-false}"
if [[ "$route_enabled" != true && "$route_enabled" != false ]]; then
  echo "SELF_HEALING_ALERT_ROUTE_ENABLED must be true or false." >&2
  exit 1
fi
command -v htpasswd >/dev/null || { echo "htpasswd is required to create monitoring credentials." >&2; exit 1; }

umask 077
work_dir="$(mktemp -d)"
trap 'rm -f "$work_dir"/url "$work_dir"/routing-key "$work_dir"/password "$work_dir"/username "$work_dir"/auth "$work_dir"/alert-rules.yaml; rmdir "$work_dir"' EXIT

kubectl create namespace observability --dry-run=client -o yaml | kubectl apply -f -
printf '%s/slack' "${DISCORD_WEBHOOK_URL%/}" > "$work_dir/url"
printf '%s' "$PAGERDUTY_INTEGRATION_KEY" > "$work_dir/routing-key"
kubectl -n observability create secret generic discord-webhook \
  --from-file="url=$work_dir/url" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n observability create secret generic pagerduty-integration \
  --from-file="routing-key=$work_dir/routing-key" --dry-run=client -o yaml | kubectl apply -f -

if [[ -z "$(kubectl -n observability get secret monitoring-ui-basic-auth --ignore-not-found -o name)" ]]; then
  openssl rand -hex 24 | tr -d '\n' > "$work_dir/password"
  printf admin > "$work_dir/username"
  htpasswd -nBi admin < "$work_dir/password" > "$work_dir/auth"
  kubectl -n observability create secret generic monitoring-ui-basic-auth \
    --from-file="auth=$work_dir/auth" --from-file="username=$work_dir/username" \
    --from-file="password=$work_dir/password"
elif ! kubectl -n observability get secret monitoring-ui-basic-auth -o json |
  jq -e 'all(.data.auth, .data.username, .data.password; type == "string" and length > 0)' >/dev/null; then
  echo "The existing monitoring-ui-basic-auth secret must contain auth, username and password." >&2
  exit 1
fi

monitoring_values=(-f helm/values/monitoring.yaml)
if [[ "$route_enabled" == true ]]; then
  if ! kubectl -n observability get secret self-healing -o json |
    jq -e 'all(.data.token, .data["ca.crt"]; type == "string" and length > 0)' >/dev/null; then
    echo "The observability/self-healing secret must contain token and ca.crt before enabling the alert route." >&2
    exit 1
  fi
  monitoring_values+=(-f helm/values/monitoring-self-healing.yaml)
fi
{
  printf 'additionalPrometheusRulesMap:\n  togglemaster-auth:\n'
  sed 's/^/    /' helm/alert-rules/auth-service.yaml
} > "$work_dir/alert-rules.yaml"

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

common=(--install --atomic --cleanup-on-fail --wait --timeout 10m --create-namespace)
helm upgrade ingress-nginx ingress-nginx/ingress-nginx --version 4.15.1 --namespace ingress-nginx "${common[@]}" -f helm/values/ingress-nginx.yaml
helm upgrade metrics-server metrics-server/metrics-server --version 3.14.0 --namespace kube-system "${common[@]}" -f helm/values/metrics-server.yaml
helm upgrade argocd argo/argo-cd --version 10.6.0 --namespace argocd "${common[@]}" -f helm/values/argocd.yaml
helm upgrade monitoring prometheus-community/kube-prometheus-stack --version 92.17.0 --namespace observability "${common[@]}" \
  "${monitoring_values[@]}" -f "$work_dir/alert-rules.yaml" \
  --set-file grafana.dashboards.togglemaster.ecosystem.json=helm/dashboards/togglemaster.json \
  --set-file grafana.dashboards.togglemaster.otel-collector.json=helm/dashboards/otel-collector.json
helm upgrade loki grafana/loki --version 7.5.0 --namespace observability "${common[@]}" -f helm/values/loki.yaml
helm upgrade otel-agent open-telemetry/opentelemetry-collector --version 0.178.2 --namespace observability "${common[@]}" -f helm/values/otel-agent.yaml
helm upgrade otel-gateway open-telemetry/opentelemetry-collector --version 0.178.2 --namespace observability "${common[@]}" -f helm/values/otel-gateway.yaml
