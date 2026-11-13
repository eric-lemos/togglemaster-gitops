import argparse
import base64
import datetime
import hmac
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HISTORY = "togglemaster.io/self-healing-history"
RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"
QUERY = 'ALERTS{alertname="ToggleMasterAuthHighErrorRate",alertstate="firing",service="auth-service"}'
PROMETHEUS_PROXY = (
    "/api/v1/namespaces/observability/services/monitoring-prometheus:9090"
    "/proxy/prometheus/api/v1/query"
)
ALERT_NAME = "ToggleMasterAuthHighErrorRate"
GITHUB_API = "https://api.github.com"
WORKFLOW_PATH = "/repos/eric-lemos/togglemaster-gitops/actions/workflows/self-healing.yml/dispatches"
MAX_BODY = 64 * 1024

def kubectl(*args):
    result = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode:
        raise RuntimeError(f"kubectl failed: {result.stderr.strip()}")
    return result.stdout

def alert_is_firing():
    url = f"{PROMETHEUS_PROXY}?{urllib.parse.urlencode({'query': QUERY})}"
    response = json.loads(kubectl("get", "--raw", url))
    data = response.get("data", {})
    if response.get("status") != "success" or data.get("resultType") != "vector":
        raise ValueError("Invalid Prometheus response")
    results = data.get("result")
    if not isinstance(results, list):
        raise ValueError("Invalid Prometheus alert results")
    for result in results:
        labels = result.get("metric", {})
        value = result.get("value")
        if (
            labels.get("alertname") != "ToggleMasterAuthHighErrorRate"
            or labels.get("alertstate") != "firing"
            or labels.get("service") != "auth-service"
            or not isinstance(value, list)
            or len(value) != 2
            or value[1] != "1"
        ):
            raise ValueError("Invalid Prometheus firing alert")
    return bool(results)

def build_patch(deployment, now):
    metadata = deployment["metadata"]
    history = json.loads(metadata.get("annotations", {}).get(HISTORY, "[]"))
    if not isinstance(history, list) or any(
        type(entry) is not int or entry < 0 or entry > now for entry in history
    ):
        raise ValueError("Invalid self-healing history; refusing to restart")
    history = sorted(entry for entry in history if entry > now - 3600)
    if history and now - history[-1] < 900:
        print("Restart skipped: minimum interval has not elapsed")
        return None
    if len(history) >= 3:
        print("Restart skipped: hourly limit reached")
        return None
    restarted_at = datetime.datetime.fromtimestamp(
        now, datetime.timezone.utc
    ).isoformat().replace("+00:00", "Z")
    return {
        "metadata": {
            "resourceVersion": metadata["resourceVersion"],
            "annotations": {HISTORY: json.dumps(history + [now])},
        },
        "spec": {"template": {"metadata": {"annotations": {RESTARTED_AT: restarted_at}}}},
    }

def restart():
    if not alert_is_firing():
        print("No firing auth-service alert; no action required")
        return
    deployment = json.loads(
        kubectl("-n", "togglemaster", "get", "deployment", "auth-service", "-o", "json")
    )
    patch = build_patch(deployment, int(time.time()))
    if patch is None:
        return
    kubectl(
        "-n", "togglemaster", "patch", "deployment", "auth-service",
        "--type=strategic", "--patch", json.dumps(patch),
    )
    print("Restart requested; checking rollout", flush=True)
    result = subprocess.run(
        ["kubectl", "-n", "togglemaster", "rollout", "status",
         "deployment/auth-service", "--timeout=300s"],
        timeout=330,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Rollout did not complete successfully")

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def github_post(path, token, body, expected_status):
    request = urllib.request.Request(
        GITHUB_API + path,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            if response.status not in expected_status:
                raise RuntimeError(f"GitHub API returned HTTP {response.status}")
            return response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise RuntimeError(f"GitHub API returned HTTP {code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError("GitHub API request failed") from error

def dispatch(app_id, installation_id, key):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    def encode(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    now = int(time.time())
    header = encode(b'{"alg":"RS256","typ":"JWT"}')
    claims = encode(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    unsigned = f"{header}.{claims}"
    signature = key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
    jwt = f"{unsigned}.{encode(signature)}"
    response = github_post(
        f"/app/installations/{installation_id}/access_tokens",
        jwt,
        {"repositories": ["togglemaster-gitops"], "permissions": {"actions": "write"}},
        {201},
    )
    token_response = json.loads(response)
    if not isinstance(token_response, dict):
        raise ValueError("Invalid GitHub installation token response")
    token = token_response.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("Invalid GitHub installation token response")
    github_post(WORKFLOW_PATH, token, {"ref": "main", "inputs": {"operation": "restart"}}, {200, 204})

def valid_notification(payload):
    if not isinstance(payload, dict) or payload.get("status") != "firing":
        return False
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        return False
    firing = False
    for alert in alerts:
        if not isinstance(alert, dict) or not isinstance(alert.get("labels"), dict):
            return False
        labels = alert["labels"]
        if (alert.get("status") not in ("firing", "resolved")
                or labels.get("alertname") != ALERT_NAME
                or labels.get("service") != "auth-service"):
            return False
        firing = firing or alert["status"] == "firing"
    return firing

class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/webhook":
            self.send_error(404)
            return
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.server.token).encode()):
            self.send_error(401)
            return
        length = self.headers.get("Content-Length", "")
        if not length.isascii() or not length.isdigit() or len(length) > 5 or int(length) > MAX_BODY:
            self.send_error(400, "Invalid content length")
            return
        try:
            body = self.rfile.read(int(length))
            if len(body) != int(length) or not valid_notification(json.loads(body)):
                self.send_error(400, "Invalid notification")
                return
        except (ValueError, UnicodeDecodeError, TimeoutError):
            self.send_error(400, "Invalid notification")
            return
        try:
            dispatch(self.server.app_id, self.server.installation_id, self.server.key)
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            print(f"Dispatch failed: {type(error).__name__}", file=sys.stderr, flush=True)
            self.send_error(502, "Dispatch failed")
            return
        print("Auth self-healing workflow dispatched", flush=True)
        self.send_response(202)
        self.end_headers()

class WebhookServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        try:
            return self.tls_context.wrap_socket(connection, server_side=True), address
        except (OSError, ssl.SSLError):
            connection.close()
            raise

def serve():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    app_id = os.environ["GITHUB_APP_ID"]
    installation_id = os.environ["GITHUB_INSTALLATION_ID"]
    if any(not value.isascii() or not value.isdigit() or int(value) == 0
           for value in (app_id, installation_id)):
        raise ValueError("GitHub App and installation IDs must be positive integers")
    key = serialization.load_pem_private_key(Path("/secrets/app/private-key.pem").read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("GitHub App key must be RSA")
    token = Path("/secrets/webhook/token").read_text(encoding="utf-8")
    if len(token) < 32 or token.strip() != token:
        raise ValueError("Invalid webhook token")
    server = WebhookServer(("", 8443), WebhookHandler)
    server.app_id, server.installation_id, server.key, server.token = app_id, installation_id, key, token
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/secrets/tls/tls.crt", "/secrets/tls/tls.key")
    server.tls_context = context
    print("GitHub webhook listening on port 8443", flush=True)
    server.serve_forever()

def main():
    parser = argparse.ArgumentParser(description="Receive alerts or restart auth-service.")
    parser.add_argument("--restart", action="store_true", help="Check the alert and restart if allowed")
    if parser.parse_args().restart:
        restart()
    else:
        serve()

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired, RuntimeError) as error:
        print(f"Self-healing failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
