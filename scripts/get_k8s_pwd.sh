#!/usr/bin/env bash

set +x
set -Eeuo pipefail

CONTEXT=""

usage() {
    echo "Uso: bash scripts/get_web_credentials.sh [all|grafana|prometheus|alertmanager|argocd]"
    echo "Sem argumento, consulta todas as ferramentas."
}

checkPrerequisites() {
    command -v kubectl >/dev/null || {
        echo "Erro: kubectl não encontrado." >&2
        return 1
    }
    command -v base64 >/dev/null || {
        echo "Erro: base64 não encontrado." >&2
        return 1
    }
    if ! CONTEXT="$(kubectl config current-context)" || [[ -z "$CONTEXT" ]]; then
        echo "Erro: não foi possível determinar o contexto atual do kubectl." >&2
        return 1
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
    local username password
    if ! username="$(readSecretField observability monitoring-grafana admin-user)" ||
        ! password="$(readSecretField observability monitoring-grafana admin-password)"; then
        return 1
    fi
    printf '\nGrafana\nUsuário: %s\nSenha do Secret: %s\n' "$username" "$password"
    echo "A senha no Secret pode não refletir alterações feitas na interface do Grafana."
}

showMonitoring() {
    local selection="$1" username password
    if ! username="$(readSecretField observability monitoring-ui-basic-auth username)" ||
        ! password="$(readSecretField observability monitoring-ui-basic-auth password)"; then
        return 1
    fi
    case "$selection" in
        prometheus) echo; echo "Prometheus" ;;
        alertmanager) echo; echo "Alertmanager" ;;
        *) echo; echo "Prometheus e Alertmanager" ;;
    esac
    printf 'Usuário: %s\nSenha: %s\n' "$username" "$password"
}

showArgoCD() {
    local password
    if ! password="$(readSecretField argocd argocd-initial-admin-secret password)"; then
        echo "Se o Secret inicial foi removido, consulte a documentação de redefinição do Argo CD." >&2
        return 1
    fi
    printf '\nArgo CD\nUsuário inicial: admin\nSenha inicial: %s\n' "$password"
    echo "A senha inicial pode não ser a senha atual se ela tiver sido alterada."
}

main() {
    local selection="${1:-all}" failed=0

    if (( $# > 1 )); then
        usage >&2
        return 2
    fi
    case "$selection" in
        -h|--help) usage; return 0 ;;
        all|grafana|prometheus|alertmanager|argocd) ;;
        *) usage >&2; return 2 ;;
    esac

    checkPrerequisites
    echo "Contexto Kubernetes: $CONTEXT"
    echo "Atenção: as senhas serão exibidas no terminal. Não grave nem compartilhe a tela."

    case "$selection" in
        all)
            showGrafana || failed=1
            showMonitoring all || failed=1
            showArgoCD || failed=1
            ;;
        grafana) showGrafana || failed=1 ;;
        prometheus|alertmanager) showMonitoring "$selection" || failed=1 ;;
        argocd) showArgoCD || failed=1 ;;
    esac
    return "$failed"
}

main "$@"
