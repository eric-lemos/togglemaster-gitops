#!/usr/bin/env python3
import requests

r="\033[31m" # red
g="\033[32m" # green
y="\033[33m" # yellow
b="\033[1m"  # bold
_="\033[0m"  # reset

class Togglemaster:

    def __init__(self, base_url: str, master_key: str = "togglemaster"):
        self.master_key = master_key
        self.base_url = base_url
        self.api_key = None

    @property
    def apps(self):
        return [
            "auth-service", 
            "flag-service", 
            "targeting-service", 
            "evaluation-service", 
            "analytics-service"
        ]

    def request(self, method: str, path: str, *, headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None, body: dict[str, object] | None = None,
        timeout: float = 15) -> requests.Response:

        if headers is None: headers = {}
        headers["Authorization"] = f"Bearer {self.master_key}"
        
        return requests.request(
            method=method,
            url=f"{self.base_url}{path}",
            headers=headers,
            params=params,
            json=body,
            timeout=timeout,
        )

    def check_health(self):
        for app in self.apps:
            path = f"/{app}/health"
            response = self.request("GET", path)
            if response.status_code == 200: print(f"[{g}OK{_}] {app} is healthy.")
            else: print(f"[{r}ERROR{_}] Unable to access {app}."); exit(1)

if __name__ == "__main__":
    endpoints = Togglemaster(base_url="http://ab38ebef887384917b35c05b10e0f1f0-1735597899.us-east-1.elb.amazonaws.com")
    endpoints.check_health()