#!/usr/bin/env python3
"""5 种推送方言的端到端验证。

用法：
    GW_BASE=http://127.0.0.1:8098 GW_ENV=./imessage/gw/gw.env python3 tests/test_dialects.py

GW_ENV 指向网关的 env 文件（用到里面的 SMS_GW_SECRET）。
注意：每种格式都会真的发一条 iMessage，别在生产号上反复刷。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("GW_BASE", "http://127.0.0.1:8098").rstrip("/")
ENV_FILE = os.environ.get("GW_ENV", "./imessage/gw/gw.env")
KEY = os.environ.get("GW_KEY", "imessage_nas")
STAMP = time.strftime("%H:%M:%S")
failures: list[str] = []


def load_env(path: str) -> dict:
    env = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except OSError as exc:
        print(f"warn: 读不到 {path}（{exc}）；签名用例会跳过")
    return env


def call(method: str, path: str, body=None, headers=None, form=False) -> tuple[int, str]:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:200]


def check(label: str, got: tuple[int, str], expect=(200,)) -> None:
    code, text = got
    ok = code in expect
    print(f"{'PASS' if ok else 'FAIL'}  {label:<28} {code} {text[:110]}")
    if not ok:
        failures.append(label)


env = load_env(ENV_FILE)

check("GET /push/<key>", call("GET", f"/push/{KEY}?" + urllib.parse.urlencode(
    {"title": "dialect-1-gotify", "body": f"GET /push {STAMP}"})))
check("GET /push/<key>/<msg>", call("GET", f"/push/{KEY}/" + urllib.parse.quote(f"dialect-2-path {STAMP}")))
check("POST /api/push", call("POST", "/api/push",
                             {"title": "dialect-3-bearer", "content": f"API push {STAMP}"},
                             {"Authorization": f"Bearer {KEY}"}))
check("POST /notify/<key>", call("POST", f"/notify/{KEY}",
                                 {"title": "dialect-4-apprise", "body": f"notify {STAMP}"}, form=True))
check("POST /feishu/<key>", call("POST", f"/feishu/{KEY}",
                                 {"msg_type": "text", "content": {"text": f"dialect-5-feishu {STAMP}"}}))

check("unknown key -> 404", call("GET", "/push/unknown_key?body=x"), expect=(404,))
check("missing body -> 400", call("GET", f"/push/{KEY}"), expect=(400,))

secret = env.get("SMS_GW_SECRET", "")
if secret and "__GENERATED__" not in secret and "your-secret" not in secret:
    ts = str(int(time.time() * 1000))
    sign = base64.b64encode(
        hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()
    payload = {
        "from": "10086",
        "content": f"SmsForwarder 报文模拟 {STAMP}",
        "timestamp": ts,
        "sign": urllib.parse.quote_plus(sign),   # SmsForwarder 会再 urlEncode 一次
        "receive_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device_mark": "e2e-test",
    }
    check("POST /hook/imessage (HMAC)", call("POST", "/hook/imessage", payload))
else:
    print("SKIP  POST /hook/imessage         （gw.env 里没有可用的 SMS_GW_SECRET）")

print()
print("全部通过" if not failures else "失败项: " + ", ".join(failures))
sys.exit(1 if failures else 0)
