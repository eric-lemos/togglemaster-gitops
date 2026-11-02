#!/usr/bin/env python3
import sys
import base64
import requests
import subprocess
import secrets
import json
import time

r="\033[31m" # red
g="\033[32m" # green
y="\033[1;33m" # yellow
c="\033[90m" # gray
b="\033[1m"  # bold
_="\033[0m"  # reset

class Validator:

    def __init__(self, base_url: str, master_key: str = "togglemaster"):
        self.base_url = base_url
        self.master_key = master_key
        self.created_flag = None
        self.api_key = None

    #######################################################
    # Helper Methods
    #######################################################
    @property
    def apps(self) -> list[str]:
        return [
            "auth-service", 
            "flag-service", 
            "targeting-service", 
            "evaluation-service", 
            "analytics-service"
        ]

    @property
    def titles(self) -> dict[str, str]:
        return {
            "check_health": "Checking health of all apps",
            "create_key": "Creating auth API key",
            "create_flag": "Creating a new flag",
            "create_rule": "Creating a new rule",
            "exec_evaluation": "Evaluating the flag for users",
            "check_dynamodb": "Checking DynamoDB events for the created flag"
        }

    @property
    def subtitles(self) -> dict[str, str]:
        return {
            "check_health": "Checking the status of all pods",
            "check_valid_key": "Checking if auth API key is valid",
            "check_invalid_key": "Exemple checking invalid API key",
            "get_flags": "Getting the latest flags",
            "update_flag": "Enabling the created flag",
            "get_rule": "Getting the created rule",
            "update_rule": "Updating the created rule",
            "check_deterministic": "Evaluating the same user twice",
            "update_key_on_k8s": "Updating the evaluation API key on Kubernetes",
            "check_cache_hits": "Checking cache hits for the evaluation service on Kubernetes logs",
        }

    def display_title(self, message: str) -> None:
        print(f"{y}\n======================================================================================{_}")
        print(f"{y}{message}{_}")
        print(f"{y}======================================================================================{_}")

    def display_subtitle(self, message: str) -> None:
        print(f"{b}\n--------------------------------------------------------------------------------------{_}")
        print(f"{b}>> {message}{_}")
        print(f"{b}--------------------------------------------------------------------------------------{_}")

    def set_delay(self, function, delay: float) -> None:
        time.sleep(delay)
        function()
        time.sleep(delay)

    #######################################################
    # Request Methods
    #######################################################
    def request(self, method: str, path: str, *, headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None, body: dict[str, object] | None = None,
        timeout: float = 15) -> requests.Response:
        if headers is None: headers = {}
        return requests.request(
            method=method,
            url=f"{self.base_url}{path}",
            headers=headers,
            params=params,
            json=body,
            timeout=timeout,
        )

    def success(self, endpoint: str, response: requests.Response, break_line: bool = False, limit: int | None = None) -> None:
        print(f"[{g}OK{_}] Successfully validated endpoint {b}{endpoint}{_}")
        try: data = response.json()
        except requests.exceptions.JSONDecodeError: data = None
        if isinstance(data, list) and limit:
            if all(isinstance(item, dict) and "id" in item for item in data): 
                data = sorted(data, key=lambda item: item["id"], reverse=True)
            data = data[:limit]
        if isinstance(data, (dict, list)): print(f"{c}{json.dumps(data, indent=4, ensure_ascii=False)}{_}")
        else: print(f"{c}{response.text.strip()}{_}")
        if break_line: print()

    def fail(self, endpoint: str, response: requests.Response) -> None:
        print(f"[{r}ERROR{_}] Unable to access endpoint {b}{endpoint}{_}")
        print(f"        {b}Reason:{_} HTTP {response.status_code} {response.reason}: {response.text[:200].strip()}")
        sys.exit(1)

    def check_health(self) -> None:
        for app in self.apps:
            endpoint = f"/{app}/health"
            response = self.request("GET", endpoint)
            if(response.status_code == 200):self.success(endpoint, response)
            else: self.fail(endpoint, response)
            time.sleep(0.25)

    def create_key(self) -> None:
        if self.api_key is None:
            endpoint = f"/auth-service/admin/keys"
            headers = {"Authorization": f"Bearer {self.master_key}"}
            body = {"name": f"apikey-{secrets.token_hex(12)}"}
            response = self.request("POST", endpoint, headers=headers, body=body)
            if(response.status_code == 201): 
                self.success(endpoint, response)
                self.api_key = response.json().get("key")
            else: self.fail(endpoint, response)
        else:
            print(f"[{g}OK{_}] API key is already set")

    def check_valid_key(self) -> None:
        if self.api_key:
            endpoint = f"/auth-service/validate"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            response = self.request("GET", endpoint, headers=headers)
            if(response.status_code == 200): self.success(endpoint, response)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set")
            sys.exit(1)

    def check_invalid_key(self) -> None:
        if self.api_key:
            endpoint = f"/auth-service/validate"
            headers = {"Authorization": f"Bearer invalid-{self.api_key}"}
            response = self.request("GET", endpoint, headers=headers)
            if(response.status_code != 200): self.success(endpoint, response)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set")
            sys.exit(1)

    def create_flag(self) -> None:
        if self.api_key:
            endpoint = f"/flag-service/flags"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            body = {
                "name": f"flag-{secrets.token_hex(6)}",
                "description": f"Generated automatically for tests",
                "is_enabled": False
            }
            response = self.request("POST", endpoint, headers=headers, body=body)
            if(response.status_code == 201):
                self.success(endpoint, response)
                self.created_flag = response.json().get("name")
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set")
            sys.exit(1)

    def get_flags(self) -> None:
        if self.api_key:
            endpoint = f"/flag-service/flags"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            response = self.request("GET", endpoint, headers=headers)
            if(response.status_code == 200): self.success(endpoint, response, limit=3)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set")
            sys.exit(1)

    def update_flag(self) -> None:
        if self.api_key and self.created_flag:
            endpoint = f"/flag-service/flags/{self.created_flag}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            response = self.request("PUT", endpoint, headers=headers, body={"is_enabled": True})
            if(response.status_code == 200): self.success(endpoint, response)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set or no flag created to enable")
            sys.exit(1)

    def create_rule(self) -> None:
        if self.api_key and self.created_flag:
            endpoint = f"/targeting-service/rules"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            body = {
                "flag_name": self.created_flag,
                "is_enabled": True,
                "rules": {
                    "type": "PERCENTAGE",
                    "value": 75
                }
            }
            response = self.request("POST", endpoint, headers=headers, body=body)
            if(response.status_code == 201): self.success(endpoint, response)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set or no flag created to create rule")
            sys.exit(1)

    def get_rule(self) -> None:
        if self.api_key and self.created_flag:
            endpoint = f"/targeting-service/rules/{self.created_flag}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            response = self.request("GET", endpoint, headers=headers)
            if(response.status_code == 200): self.success(endpoint, response)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set or no flag created to get rule")
            sys.exit(1)

    def update_rule(self) -> None:
        if self.api_key and self.created_flag:
            endpoint = f"/targeting-service/rules/{self.created_flag}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            body = {
                "rules": {
                    "type": "PERCENTAGE",
                    "value": 50
                }
            }
            response = self.request("PUT", endpoint, headers=headers, body=body)
            if(response.status_code == 200): self.success(endpoint, response)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set or no flag created to update rule")
            sys.exit(1)

    def update_key_on_k8s(self) -> None:
        if not self.api_key:
            print(f"[{r}ERROR{_}] API key is not set")
            sys.exit(1)

        encoded_key = base64.b64encode(self.api_key.encode()).decode("ascii")
        secret = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "togglemaster-runtime", "namespace": "togglemaster"},
            "type": "Opaque",
            "data": {"SERVICE_API_KEY": encoded_key},
        }
        commands = [
            ["kubectl", "--namespace", "togglemaster", "apply", "-f", "-"],
            ["kubectl", "--namespace", "togglemaster", "rollout", "restart", "deployment/evaluation-service"],
            ["kubectl", "--namespace", "togglemaster", "rollout", "status", "deployment/evaluation-service", "--timeout=120s"],
        ]
        for index, command in enumerate(commands):
            try:
                subprocess.run(
                    command,
                    input=json.dumps(secret) if index == 0 else None,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=150,
                )
            except FileNotFoundError:
                print(f"[{r}ERROR{_}] kubectl was not found. Install it and configure access to the test cluster.")
                sys.exit(1)
            except subprocess.TimeoutExpired:
                print(f"[{r}ERROR{_}] Kubernetes command timed out: {' '.join(command)}")
                sys.exit(1)
            except subprocess.CalledProcessError as error:
                details = (error.stderr or error.stdout or "No error details returned").strip()
                details = details.replace(self.api_key, "[REDACTED]").replace(encoded_key, "[REDACTED]")
                print(f"[{r}ERROR{_}] Kubernetes command failed: {' '.join(command)}")
                print(f"        {b}Reason:{_} {details}")
                sys.exit(1)

        print(f"[{g}OK{_}] API key updated and evaluation-service rollout completed on kubernetes")

    def evaluate(self, user_id: str) -> bool:
        endpoint = "/evaluation-service/evaluate"
        params = {"user_id": user_id, "flag_name": self.created_flag}
        response = self.request("GET", endpoint, params=params)
        if(response.status_code == 200):
            self.success(f"{endpoint}?user_id={user_id}", response)
            time.sleep(0.25)
        else: self.fail(endpoint, response)
        return response.json().get("result")

    def exec_evaluation(self, total_users: int = 5) -> None:
        if self.created_flag:
            users = [f"user-{secrets.token_hex(3)}" for i in range(total_users)]
            enabled = sum(self.evaluate(user) for user in users)
            print(f"\n[{g}OK{_}] {enabled}/{total_users} users received the flag enabled")
        else:
            print(f"[{r}ERROR{_}] No flag created to evaluate")
            sys.exit(1)

    def check_deterministic(self) -> None:
        user = f"static-user-{secrets.token_hex(3)}"
        first, second = self.evaluate(user), self.evaluate(user)
        if first == second:
            print(f"\n[{g}OK{_}] Same result for {b}{user}{_} on repeated calls (deterministic)")
        else:
            print(f"[{r}ERROR{_}] Different results for {b}{user}{_}: {first} and {second}")
            sys.exit(1)

    def show_cache_hits(self) -> None:
        if not self.created_flag:
            print(f"[{r}ERROR{_}] No flag created to check cache logs")
            sys.exit(1)

        command = [
            "kubectl", "logs", "--namespace", "togglemaster",
            "-l", "app=evaluation-service", "--since=10m",
            "--tail=-1", "--prefix=true", "--request-timeout=30s",
        ]

        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=45)
        if result.stderr.strip(): print(f"[{y}WARNING{_}] {result.stderr.strip()}")

        hits = [
            line for line in result.stdout.splitlines()
            if f"Cache HIT para flag '{self.created_flag}'" in line
        ]

        if hits:
            for line in hits: print(f"{c}{line}{_}")
        else: print(f"[{y}WARNING{_}] No Cache HIT found for flag '{self.created_flag}' in the last 10 minutes.")

    def check_dynamodb(self, table_name: str = "ToggleMasterAnalytics", region: str = "us-east-1") -> None:
        if not self.created_flag:
            print(f"[{r}ERROR{_}] No flag created to check analytics")
            sys.exit(1)

        scan = {
            "TableName": table_name,
            "FilterExpression": "flag_name = :flag",
            "ExpressionAttributeValues": {":flag": {"S": self.created_flag}},
        }

        command = [
            "aws", "dynamodb", "scan", "--cli-input-json", json.dumps(scan),
            "--region", region, "--output", "json", "--no-cli-pager",
            "--query", "Items[].{event_id:event_id.S,flag_name:flag_name.S,result:result.BOOL,timestamp:timestamp.S,user_id:user_id.S}",
        ]
        
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)

        if result.stderr.strip():
            print(f"[{y}WARNING{_}] {result.stderr.strip()}")
        try:
            items = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            print(f"[{r}ERROR{_}] Invalid JSON returned by AWS CLI: {error}")
            sys.exit(1)
        if not isinstance(items, list) or not all(
            isinstance(item, dict) and isinstance(item.get("timestamp"), str) for item in items
        ):
            print(f"[{r}ERROR{_}] Unexpected analytics response: expected a list of events with timestamps")
            sys.exit(1)
        if not items:
            print(f"[{y}WARNING{_}] No events found for flag '{self.created_flag}'. SQS processing is asynchronous; try again shortly.")
            return
        
        items.sort(key=lambda item: item["timestamp"], reverse=True)
        print(f"{c}{json.dumps(items, indent=4, ensure_ascii=False)}{_}")

    #######################################################
    # Display Methods
    #######################################################
    def display_health(self) -> None:
        self.display_title(self.titles["check_health"]); self.check_health()
        self.display_subtitle(self.subtitles["check_health"])

    def display_auth(self) -> None:
        self.display_title(self.titles["create_key"]); self.set_delay(self.create_key, 1)
        self.display_subtitle(self.subtitles["check_valid_key"]); self.set_delay(self.check_valid_key, 1)
        self.display_subtitle(self.subtitles["check_invalid_key"]); self.set_delay(self.check_invalid_key, 1)

    def display_flag(self) -> None:
        self.display_title(self.titles["create_flag"]); self.set_delay(self.create_flag, 1)
        self.display_subtitle(self.subtitles["get_flags"]); self.set_delay(self.get_flags, 1)
        self.display_subtitle(self.subtitles["update_flag"]); self.set_delay(self.update_flag, 1)

    def display_rule(self) -> None:
        self.display_title(self.titles["create_rule"]); self.set_delay(self.create_rule, 1)
        self.display_subtitle(self.subtitles["get_rule"]); self.set_delay(self.get_rule, 1)
        self.display_subtitle(self.subtitles["update_rule"]); self.set_delay(self.update_rule, 1)

    def display_evaluation(self) -> None:
        self.display_title(self.titles["exec_evaluation"]); 
        self.update_key_on_k8s(); self.set_delay(self.exec_evaluation, 1)
        self.display_subtitle(self.subtitles["check_deterministic"]); self.set_delay(self.check_deterministic, 1)
        self.display_subtitle(self.subtitles["check_cache_hits"]); self.show_cache_hits()

    def display_analytics(self) -> None:
        self.display_title(self.titles["check_dynamodb"]); self.set_delay(self.check_dynamodb, 1)

    def run_tests(self) -> None:
        subprocess.run(["clear"])
        print(f"\n*** {b}Starting validation of Togglemaster applications{_} ***"); time.sleep(1)
        self.display_health()
        self.display_auth()
        self.display_flag()
        self.display_rule()
        self.display_evaluation()
        self.display_analytics()
        print(f"\n*** {b}Validation of Togglemaster applications completed{_} ***"); time.sleep(1)

if __name__ == "__main__":
    v = Validator(
        base_url="http://a4468981d15cb4cf0a096121abde47ad-447831217.us-east-1.elb.amazonaws.com"
    ).run_tests()