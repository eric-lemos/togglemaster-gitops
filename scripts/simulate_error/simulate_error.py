#!/usr/bin/env python3
"""Demonstra o fluxo de self-healing com erros reais e controlados no auth-service."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

NAMESPACE = "togglemaster"
ALERT_NAME = "ToggleMasterAuthHighErrorRate"
HISTORY = "togglemaster.io/self-healing-history"
ALERTS_URL = (
    "/api/v1/namespaces/observability/services/monitoring-prometheus:9090"
    "/proxy/prometheus/api/v1/alerts"
)
REQUEST_COUNT = 80
REQUEST_INTERVAL = 0.5


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def title(message: str) -> None:
    print(f"\n{'=' * 78}\n{message}\n{'=' * 78}", flush=True)


def kubectl(*args: str, timeout: int = 30, request_timeout: str = "15s") -> str:
    result = subprocess.run(
        ["kubectl", f"--request-timeout={request_timeout}", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"kubectl falhou: {result.stderr.strip()}")
    return result.stdout


def deployment(name: str) -> dict:
    return json.loads(kubectl("-n", NAMESPACE, "get", "deployment", name, "-o", "json"))


def check_deployment(name: str) -> dict:
    current = deployment(name)
    desired = current["spec"].get("replicas", 1)
    ready = current.get("status", {}).get("availableReplicas", 0)
    if desired < 1 or ready < desired:
        raise RuntimeError(f"Deployment {name} não está pronto ({ready}/{desired}).")
    print(f"Deployment {name}: {ready}/{desired} disponível.", flush=True)
    return current


def alert_states() -> set[str]:
    response = json.loads(kubectl("get", "--raw", ALERTS_URL))
    data = response.get("data")
    if response.get("status") != "success" or not isinstance(data, dict):
        raise RuntimeError("Prometheus retornou uma resposta de alertas inválida.")
    alerts = data.get("alerts")
    if not isinstance(alerts, list):
        raise RuntimeError("Prometheus retornou uma lista de alertas inválida.")
    return {
        alert["state"]
        for alert in alerts
        if alert.get("labels", {}).get("alertname") == ALERT_NAME
        and alert.get("labels", {}).get("service") == "auth-service"
    }


def wait_for_port(process: subprocess.Popen, output, timeout: int = 30) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output.seek(0)
            raise RuntimeError(f"Port-forward terminou: {output.read().strip()}")
        output.seek(0)
        match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 8001", output.read())
        if match:
            return int(match.group(1))
        time.sleep(0.2)
    raise RuntimeError("Port-forward não ficou pronto dentro do prazo.")


def send_errors(port: int, master_key: str, count: int = REQUEST_COUNT) -> None:
    # PostgreSQL text rejects NUL even if the name column's length changes.
    body = json.dumps({"name": "simulate-error-\0" + "x" * 101}).encode("utf-8")
    url = f"http://127.0.0.1:{port}/admin/keys"
    opener = urllib.request.build_opener(NoRedirect())
    for index in range(count):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {master_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with opener.open(request, timeout=15) as response:
                status = response.status
                message = ""
        except urllib.error.HTTPError as error:
            status = error.code
            message = error.read(1024).decode("utf-8", errors="replace")
            error.close()
        if status == 201:
            raise RuntimeError("Uma chave foi criada inesperadamente; simulação interrompida. Verifique o banco.")
        if status != 500 or "Erro ao salvar a chave" not in message:
            raise RuntimeError(
                f"Resposta inesperada HTTP {status} na requisição {index + 1}; simulação interrompida."
            )
        if (index + 1) % 20 == 0 or index + 1 == count:
            print(f"Respostas HTTP 500 esperadas: {index + 1}/{count}.", flush=True)
        if index + 1 < count:
            time.sleep(REQUEST_INTERVAL)


def wait_for_alert(timeout: int = 240) -> None:
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        states = alert_states()
        status = ", ".join(sorted(states)) if states else "inativo"
        if status != previous:
            print(f"Prometheus: alerta {status}.", flush=True)
            previous = status
        if "firing" in states:
            return
        time.sleep(5)
    raise RuntimeError("O alerta não chegou a firing dentro do prazo.")


def wait_for_restart(previous_history: str, started_at: int, timeout: int = 240) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = deployment("auth-service")
        history = current["metadata"].get("annotations", {}).get(HISTORY, "[]")
        entries = json.loads(history)
        if not isinstance(entries, list) or any(type(entry) is not int for entry in entries):
            raise RuntimeError("Histórico de self-healing inválido.")
        if history != previous_history and any(entry >= started_at for entry in entries):
            print("Nova tentativa de restart registrada na annotation do Deployment.", flush=True)
            print(kubectl(
                "-n", NAMESPACE, "rollout", "status", "deployment/auth-service",
                "--timeout=180s", timeout=210, request_timeout="190s",
            ))
            return
        time.sleep(5)
    raise RuntimeError(
        "Nenhum restart foi observado. Confira a execução Self Healing, a rota do "
        "Alertmanager e os limites de 2 minutos e 3 restarts em 10 minutos."
    )


def main() -> None:
    master_key = os.environ.get("MASTER_KEY", "")
    if not master_key:
        raise RuntimeError("O secret MASTER_KEY é obrigatório.")
    if os.environ.get("SELF_HEALING_ENABLED") != "true":
        raise RuntimeError("SELF_HEALING_ENABLED deve estar definido como true para este teste.")

    title("1. Preparação: serviços e alerta")
    print("Fluxo: auth-service → OpenTelemetry → Prometheus → Alertmanager → webhook → GitHub Actions → EKS.")
    current = check_deployment("auth-service")
    check_deployment("webhook")
    states = alert_states()
    if states:
        raise RuntimeError(f"O alerta já está ativo ({', '.join(sorted(states))}); aguarde a resolução.")
    previous_history = current["metadata"].get("annotations", {}).get(HISTORY, "[]")
    started_at = int(time.time())
    print("Alerta inativo. O teste não altera os deployments nem envia alerta sintético.", flush=True)

    title("2. Simulação: requisições HTTP com erro controlado")
    print(
        "O nome contém NUL e excede 100 caracteres: o PostgreSQL deve rejeitar a "
        "gravação e o auth-service responder HTTP 500. A chave mestra não é exibida.",
        flush=True,
    )
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        process = subprocess.Popen(
            ["kubectl", "-n", NAMESPACE, "port-forward", "--address", "127.0.0.1",
             "service/auth-service", ":8001"],
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            port = wait_for_port(process, output)
            send_errors(port, master_key)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    title("3. Observação: telemetria e alerta")
    print(
        "O OpenTelemetry exporta métricas; o Prometheus avalia erros 5xx em uma "
        "janela de 5 minutos e exige mais de 5% e pelo menos 10 requisições estimadas.",
        flush=True,
    )
    wait_for_alert()
    title("4. Recuperação: webhook e Self Healing")
    print(
        "O Alertmanager notifica o webhook, Discord e PagerDuty. O webhook aciona "
        "Self Healing, que confirma o alerta e respeita os limites de restart.",
        flush=True,
    )
    wait_for_restart(previous_history, started_at)
    print("Rollout concluído. Confirme a execução Self Healing e as notificações no GitHub, Discord e PagerDuty.")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository:
        print(f"GitHub Actions: https://github.com/{repository}/actions/workflows/self_healing.yml")
    print("O alerta pode permanecer ativo até os erros saírem da janela de 5 minutos.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f"ERRO: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
