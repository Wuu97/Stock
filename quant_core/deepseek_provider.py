"""Minimal DeepSeek JSON provider; business code still validates every response."""

import json
from os import environ
from urllib.request import Request, urlopen


class DeepSeekProvider:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or environ.get("DEEPSEEK_API_KEY", "")
        self.model = model or environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured")

    def json_completion(self, system_prompt: str, user_prompt: str, max_tokens: int = 800) -> dict:
        payload = json.dumps({"model": self.model, "stream": False, "max_tokens": max_tokens,
                              "response_format": {"type": "json_object"},
                              "messages": [{"role": "system", "content": system_prompt},
                                           {"role": "user", "content": user_prompt}]}).encode("utf-8")
        request = Request("https://api.deepseek.com/chat/completions", data=payload,
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=45) as response:
            body = json.loads(response.read().decode("utf-8"))
        try:
            content = body["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("DeepSeek returned an invalid JSON completion") from error
