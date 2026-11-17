#!/usr/bin/env bash

set +x
set -Eeuo pipefail

CONTEXT=""

usage() {
    echo "Uso: bash scripts/get_info.sh [all|apps|grafana|prometheus|alertmanager|argocd]"
    echo "Sem argumento, consulta todas as ferramentas e aplicações."
}

checkPrerequisites() {
    local selection="$1"
    command -v kubectl >/dev/null || {
        echo "Erro: kubectl não encontrado." >&2
        return 1
    }
    if [[ "$selection" != apps ]] && ! command -v base64 >/dev/null; then
        echo "Erro: base64 não encontrado." >&2
        return 1
    fi
    if ! CONTEXT="$(kubectl config current-context)" || [[ -z "$CONTEXT" ]]; then
        echo "Erro: não foi possível determinar o contexto atual do kubectl." >&2
        return 1
    fi
}

readLoadBalancerAddress() {
    local namespace="$1" kind="$2" name="$3" address field

    for field in hostname ip; do
        if ! address="$(kubectl --context "$CONTEXT" -n "$namespace" get "$kind" "$name" -o "jsonpath={.status.loadBalancer.ingress[0].$field}")"; then
            echo "Erro: não foi possível consultar $kind $namespace/$name." >&2
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
    echo "Erro: $kind $namespace/$name ainda não possui endereço de LoadBalancer." >&2
    return 1
}

showEndpoint() {
    local address="$1" path="$2" scheme="${3:-http}"
    if [[ -n "$address" ]]; then
        printf 'Endpoint: %s://%s%s\n' "$scheme" "$address" "$path"
    else
        echo "Endpoint: indisponível"
    fi
}

readSecretField() {
    local namespace="$1" secret="$2" key="$3" encoded value

    if ! encoded="$(kubectl --context "$CONTEXT" -n "$namespace" get secret "$secret" -o "jsonpath={.data.$key}")"; then
        echo "Erro: não foi possível consultar o Secret $namespace/$secret." >&2
        return 1
    fi
    if [[ -z "$encoded" ]]; then
        echo "Erro: campo $key ausente ou vazio no Secret $namespace/$secret." >&2
        return 1
    fi
    if ! value="$(printf '%s' "$encoded" | base64 --decode)" || [[ -z "$value" ]]; then
        echo "Erro: campo $key inválido ou vazio no Secret $namespace/$secret." >&2
        return 1
    fi
    printf '%s' "$value"
}

showGrafana() {
    local address="$1" username password
    printf '\nGrafana\n'
    showEndpoint "$address" "/grafana/"
    if ! username="$(readSecretField observability monitoring-grafana admin-user)" ||
        ! password="$(readSecretField observability monitoring-grafana admin-password)"; then
        return 1
    fi
    printf 'Usuário: %s\nSenha do Secret: %s\n' "$username" "$password"
    echo "A senha no Secret pode não refletir alterações feitas na interface do Grafana."
}

showMonitoring() {
    local selection="$1" address="$2" username password
    if [[ "$selection" == all || "$selection" == prometheus ]]; then
        printf '\nPrometheus\n'
        showEndpoint "$address" "/prometheus/"
    fi
    if [[ "$selection" == all || "$selection" == alertmanager ]]; then
        printf '\nAlertmanager\n'
        showEndpoint "$address" "/alertmanager/"
    fi
    if ! username="$(readSecretField observability monitoring-ui-basic-auth username)" ||
        ! password="$(readSecretField observability monitoring-ui-basic-auth password)"; then
        return 1
    fi
    if [[ "$selection" == all ]]; then
        echo "Credenciais compartilhadas do Prometheus e Alertmanager:"
    fi
    printf 'Usuário: %s\nSenha: %s\n' "$username" "$password"
}

showApps() {
    local address="$1" app
    printf '\nAplicações ToggleMaster (URLs base)\n'
    if [[ -z "$address" ]]; then
        echo "Endpoints: indisponíveis"
        return
    fi
    for app in auth-service flag-service targeting-service evaluation-service analytics-service; do
        printf '%s: http://%s/%s\n' "$app" "$address" "$app"
    done
    echo "Para verificar a saúde, acrescente /health à URL base."
}

showArgoCD() {
    local address="" password failed=0
    printf '\nArgo CD\n'
    if ! address="$(readLoadBalancerAddress argocd service argocd-server)"; then
        failed=1
    fi
    showEndpoint "$address" "/" "https"
    echo "O certificado padrão do Argo CD pode ser autoassinado."
    if ! password="$(readSecretField argocd argocd-initial-admin-secret password)"; then
        echo "Se o Secret inicial foi removido, consulte a documentação de redefinição do Argo CD." >&2
        return 1
    fi
    printf 'Usuário inicial: admin\nSenha inicial: %s\n' "$password"
    echo "A senha inicial pode não ser a senha atual se ela tiver sido alterada."
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
    echo "Contexto Kubernetes: $CONTEXT"
    if [[ "$selection" != apps ]]; then
        echo "Atenção: as senhas serão exibidas no terminal. Não grave nem compartilhe a tela."
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
