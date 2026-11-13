import datetime
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROMETHEUS = "http://monitoring-prometheus.observability.svc:9090/prometheus/api/v1/query"
QUERY = 'ALERTS{alertname="ToggleMasterAuthHighErrorRate",alertstate="firing"}'
API = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ['KUBERNETES_SERVICE_PORT_HTTPS']}"
DEPLOYMENT = "/apis/apps/v1/namespaces/togglemaster/deployments/auth-service"
SERVICE_ACCOUNT = "/var/run/secrets/kubernetes.io/serviceaccount"
HISTORY = "togglemaster.io/self-healing-history"
RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"
MIN_INTERVAL = 15 * 60
WINDOW = 60 * 60
MAX_ATTEMPTS = 3

def request_json(url, *, method="GET", headers=None, data=None, context=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        return json.load(response)

def alert_is_firing():
    response = request_json(f"{PROMETHEUS}?{urllib.parse.urlencode({'query': QUERY})}")
    if response.get("status") != "success" or response.get("data", {}).get("resultType") != "vector":
        raise ValueError("Prometheus returned an invalid alert response")
    results = response["data"]["result"]
    if not isinstance(results, list):
        raise ValueError("Prometheus returned an invalid alert result")
    return bool(results)

def restart_if_allowed(now):
    with open(f"{SERVICE_ACCOUNT}/token", encoding="utf-8") as token_file:
        token = token_file.read().strip()
    context = ssl.create_default_context(cafile=f"{SERVICE_ACCOUNT}/ca.crt")
    headers = {"Authorization": f"Bearer {token}"}
    deployment = request_json(API + DEPLOYMENT, headers=headers, context=context)
    metadata = deployment["metadata"]
    history = json.loads(metadata.get("annotations", {}).get(HISTORY, "[]"))
    if not isinstance(history, list) or any(type(entry) is not int or entry > now for entry in history):
        raise ValueError("Invalid self-healing history; refusing to restart")
    history = sorted(entry for entry in history if entry > now - WINDOW)
    if history and now - history[-1] < MIN_INTERVAL:
        print("Restart skipped: minimum interval has not elapsed")
        return
    if len(history) >= MAX_ATTEMPTS:
        print("Restart skipped: hourly limit reached")
        return

    restarted_at = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    patch = {
        "metadata": {
            "resourceVersion": metadata["resourceVersion"],
            "annotations": {HISTORY: json.dumps(history + [now])},
        },
        "spec": {"template": {"metadata": {"annotations": {RESTARTED_AT: restarted_at}}}},
    }
    headers["Content-Type"] = "application/strategic-merge-patch+json"
    request_json(API + DEPLOYMENT, method="PATCH", headers=headers, data=json.dumps(patch).encode(), context=context)
    print(f"Restart requested for togglemaster/auth-service at {restarted_at}; attempt {len(history) + 1}/{MAX_ATTEMPTS} in the last hour")

def main():
    if not alert_is_firing():
        print("No firing auth-service alert; no action required")
        return
    restart_if_allowed(int(time.time()))

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as error:
        print(f"Self-healing failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
