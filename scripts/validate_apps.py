#!/usr/bin/env python3
import sys
import requests
import subprocess
import hashlib
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
        self.master_key = master_key
        self.base_url = base_url
        self.api_key = None

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
        }

    @property
    def subtitles(self) -> dict[str, str]:
        return {
            "check_health": "kubectl get pods",
            "check_valid_key": "Checking if auth API key is valid",
            "check_invalid_key": "Exemple checking invalid API key"
        }

    def display_title(self, message: str) -> None:
        print(f"{y}\n============================================================================{_}")
        print(f"{y}{message}{_}")
        print(f"{y}============================================================================{_}")

    def display_subtitle(self, message: str) -> None:
        print(f"{b}\n----------------------------------------------------------------------------{_}")
        print(f"{b}>> {message}{_}")
        print(f"{b}----------------------------------------------------------------------------{_}")

    def new_hash(self):
        return hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]

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

    def success(self, endpoint: str, response: requests.Response, break_line: bool = False) -> None:
        print(f"[{g}OK{_}] Successfully validated endpoint {b}{endpoint}{_}")
        try: data = response.json()
        except requests.exceptions.JSONDecodeError: data = None
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
            body = {"name": f"apikey-{self.new_hash()}"}
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
            body = {"name": f"apikey-{self.new_hash()}"}
            response = self.request("GET", endpoint, headers=headers, body=body)
            if(response.status_code == 200): self.success(endpoint, response)
            else: self.fail(endpoint, response)
        else:
            print(f"[{r}ERROR{_}] API key is not set")
            sys.exit(1)

    def check_invalid_key(self) -> None:
        if self.api_key:
            endpoint = f"/auth-service/validate"
            headers = {"Authorization": f"Bearer invalid-{self.api_key}"}
            body = {"name": f"apikey-{self.new_hash()}"}
            response = self.request("GET", endpoint, headers=headers, body=body)
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
                "name": f"flag-{self.new_hash()}",
                "description": f"The flag-{self.new_hash()} is generated for tests",
                "is_enabled": True
            }
            response = self.request("POST", endpoint, headers=headers, body=body)
            if(response.status_code == 201): self.success(endpoint, response)
            else: self.fail(endpoint, response)

    def display_health(self) -> None:
        self.display_title(self.titles["check_health"]); self.check_health()
        self.display_subtitle(self.subtitles["check_health"])
        result = subprocess.run(["kubectl", "get", "pods"],
            capture_output=True, 
            text=True
        ); print(f"{c}{result.stdout}{_}")

    def display_auth(self) -> None:
        self.display_title(self.titles["create_key"]); self.create_key()
        self.display_subtitle(self.subtitles["check_valid_key"]); self.check_valid_key()
        self.display_subtitle(self.subtitles["check_invalid_key"]); self.check_invalid_key()

    def run_tests(self) -> None:
        print(f"\n*** {b}Starting validation of Togglemaster applications{_} ***"); time.sleep(1)
        self.display_health()
        self.display_auth()

if __name__ == "__main__":
    v = Validator("http://ab99be290be4c463287afdd647e7cae2-833658577.us-east-1.elb.amazonaws.com")
    v.run_tests()