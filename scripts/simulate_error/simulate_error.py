#!/usr/bin/env python3
"""Demonstrate self-healing with real, controlled auth-service errors."""

import json
import os
import re
import shlex
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
    print(f"\033[1;33m\n{'=' * 86}\n{message}\n{'=' * 86}\033[0m", flush=True)


def subtitle(message: str) -> None:
    print(f"\033[1m\n{'-' * 86}\n>> {message}\n{'-' * 86}\033[0m", flush=True)


def display_command(command: list[str]) -> None:
    print(f"$ {shlex.join(command)}", flush=True)


def kubectl(
    *args: str, timeout: int = 30, request_timeout: str = "15s", show_command: bool = True
) -> str:
    command = ["kubectl", f"--request-timeout={request_timeout}", *args]
    if show_command:
        display_command(command)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"kubectl failed: {result.stderr.strip()}")
    return result.stdout


def deployment(name: str, *, show_command: bool = True) -> dict:
    return json.loads(kubectl(
        "-n", NAMESPACE, "get", "deployment", name, "-o", "json",
        show_command=show_command,
    ))


def check_deployment(name: str) -> dict:
    current = deployment(name)
    desired = current["spec"].get("replicas", 1)
    ready = current.get("status", {}).get("availableReplicas", 0)
    if desired < 1 or ready < desired:
        raise RuntimeError(f"Deployment {name} is not ready ({ready}/{desired}).")
    print(f"Deployment {name}: {ready}/{desired} available.", flush=True)
    return current


def alert_states(*, show_command: bool = True) -> set[str]:
    response = json.loads(kubectl("get", "--raw", ALERTS_URL, show_command=show_command))
    data = response.get("data")
    if response.get("status") != "success" or not isinstance(data, dict):
        raise RuntimeError("Prometheus returned an invalid alerts response.")
    alerts = data.get("alerts")
    if not isinstance(alerts, list):
        raise RuntimeError("Prometheus returned an invalid alerts list.")
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
            raise RuntimeError(f"Port-forward exited: {output.read().strip()}")
        output.seek(0)
        match = re.search(r"Forwarding from 127\.0\.0\.1:(\d+) -> 8001", output.read())
        if match:
            return int(match.group(1))
        time.sleep(0.2)
    raise RuntimeError("Port-forward did not become ready in time.")


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
            raise RuntimeError("A key was unexpectedly created; simulation stopped. Check the database.")
        if status != 500 or "Erro ao salvar a chave" not in message:
            raise RuntimeError(
                f"Unexpected HTTP {status} on request {index + 1}; simulation stopped."
            )
        if (index + 1) % 20 == 0 or index + 1 == count:
            print(f"Expected HTTP 500 responses: {index + 1}/{count}.", flush=True)
        if index + 1 < count:
            time.sleep(REQUEST_INTERVAL)


def wait_for_alert(timeout: int = 240, *, resolved: bool = False) -> None:
    deadline = time.monotonic() + timeout
    previous = None
    print("The query below repeats every 5 seconds; output is filtered to this alert.", flush=True)
    while time.monotonic() < deadline:
        states = alert_states(show_command=previous is None)
        status = ", ".join(sorted(states)) if states else "inactive"
        if status != previous:
            print(f"Prometheus (filtered response): {ALERT_NAME}, service=auth-service, state={status}.", flush=True)
            previous = status
        if (resolved and not states) or (not resolved and "firing" in states):
            return
        time.sleep(5)
    if resolved:
        raise RuntimeError("The alert did not become inactive in time.")
    raise RuntimeError("The alert did not reach firing state in time.")


def wait_for_restart(previous_history: str, started_at: int, timeout: int = 240) -> None:
    deadline = time.monotonic() + timeout
    first = True
    print("The query below repeats every 5 seconds to check the restart history.", flush=True)
    while time.monotonic() < deadline:
        current = deployment("auth-service", show_command=first)
        first = False
        history = current["metadata"].get("annotations", {}).get(HISTORY, "[]")
        entries = json.loads(history)
        if not isinstance(entries, list) or any(type(entry) is not int for entry in entries):
            raise RuntimeError("Invalid self-healing history.")
        if history != previous_history and any(entry >= started_at for entry in entries):
            print("A new restart attempt was recorded in the Deployment annotation.", flush=True)
            subtitle("Confirming the Kubernetes rollout")
            print(kubectl(
                "-n", NAMESPACE, "rollout", "status", "deployment/auth-service",
                "--timeout=180s", timeout=210, request_timeout="190s",
            ))
            return
        time.sleep(5)
    raise RuntimeError(
        "No restart was observed. Check the Self Healing workflow, the Alertmanager "
        "route, and the limits of 2 minutes between restarts and 3 restarts in 10 minutes."
    )


def main() -> None:
    master_key = os.environ.get("MASTER_KEY", "")
    if not master_key:
        raise RuntimeError("The MASTER_KEY secret is required.")
    if os.environ.get("SELF_HEALING_ENABLED") != "true":
        raise RuntimeError("SELF_HEALING_ENABLED must be set to true for this test.")

    title("1. Preparation: services and alert")
    print("Flow: auth-service → OpenTelemetry → Prometheus → Alertmanager → self-healing → GitHub Actions → EKS.")
    subtitle("Checking deployments")
    current = check_deployment("auth-service")
    check_deployment("self-healing")
    subtitle("Checking the initial alert state in Prometheus")
    states = alert_states()
    if states:
        raise RuntimeError(f"The alert is already active ({', '.join(sorted(states))}); wait for it to resolve.")
    previous_history = current["metadata"].get("annotations", {}).get(HISTORY, "[]")
    started_at = int(time.time())
    print("Alert inactive. The script does not directly modify deployments or send a synthetic alert.", flush=True)

    title("2. Simulation: controlled HTTP errors")
    print(
        "The name contains NUL and exceeds 100 characters: PostgreSQL should reject "
        "the write and auth-service should return HTTP 500. The master key is not displayed.",
        flush=True,
    )
    subtitle("Opening a local connection to auth-service")
    command = [
        "kubectl", "-n", NAMESPACE, "port-forward", "--address", "127.0.0.1",
        "service/auth-service", ":8001",
    ]
    display_command(command)
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        process = subprocess.Popen(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            port = wait_for_port(process, output)
            subtitle("Sending requests and checking responses")
            print(
                f"POST http://127.0.0.1:{port}/admin/keys (executed by Python; "
                "Authorization omitted from logs).",
                flush=True,
            )
            send_errors(port, master_key)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    title("3. Observation: telemetry and alert")
    print(
        "OpenTelemetry exports metrics; Prometheus evaluates 5xx errors over a "
        "5-minute window and requires more than 5% and at least 10 estimated requests.",
        flush=True,
    )
    subtitle("Checking the alert state in Prometheus")
    wait_for_alert()
    title("4. Recovery: self-healing receiver and workflow")
    print(
        "Expected flow: Alertmanager → self-healing receiver → Self Healing → restart. "
        "Check Discord and PagerDuty separately to confirm notification delivery.",
        flush=True,
    )
    subtitle("Checking the Deployment restart history")
    wait_for_restart(previous_history, started_at)
    print("Rollout complete. Check the Self Healing run and notifications in GitHub, Discord and PagerDuty.")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository:
        print(f"GitHub Actions: https://github.com/{repository}/actions/workflows/self_healing.yml")
    title("5. Resolution: waiting for the alert to close")
    print("The alert can remain active until the errors leave the 5-minute window.", flush=True)
    subtitle("Checking that the alert becomes inactive in Prometheus")
    wait_for_alert(resolved=True, timeout=600)
    print("The alert is inactive in Prometheus. Check notification resolution separately.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
