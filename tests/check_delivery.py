#!/usr/bin/env python3
"""查 daemon 侧的真实投递状态（需要能 docker exec 到 imessaged 的权限）。

用法：
    python3 tests/check_delivery.py [目标handle]

默认从网关 env 文件读 TARGET_HANDLE；用 `imsg messages --json` 拉最近几条，
打印 delivery 字段（sent / delivered / read），这是 Apple 侧回执，可作为验收依据。

环境变量：
    IMESSAGED_CONTAINER  容器名，默认 imessaged
    IMESSAGED_BUS        容器内 bus 地址，默认 unix:path=/bus/bus
    GW_ENV               网关 env 路径（取 TARGET_HANDLE），默认 ./imessage/gw/gw.env
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

CONTAINER = os.environ.get("IMESSAGED_CONTAINER", "imessaged")
BUS = os.environ.get("IMESSAGED_BUS", "unix:path=/bus/bus")
ENV_FILE = os.environ.get("GW_ENV", "./imessage/gw/gw.env")
LIMIT = int(os.environ.get("LIMIT", "8"))


def target_handle() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    handle = os.environ.get("TARGET_HANDLE", "")
    if not handle:
        try:
            with open(ENV_FILE, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("TARGET_HANDLE="):
                        handle = line.split("=", 1)[1].strip()
                        break
        except OSError:
            pass
    return handle


handle = target_handle()
if not handle:
    print("需要一个目标 handle：tests/check_delivery.py <handle> 或设置 TARGET_HANDLE")
    sys.exit(2)

cmd = ["docker", "exec", CONTAINER, "imsg", "--bus-address", BUS,
       "messages", "--json", handle]
try:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
except (OSError, subprocess.TimeoutExpired) as exc:
    print(f"执行失败: {exc}")
    sys.exit(2)

if proc.returncode != 0:
    print(f"imsg 退出码 {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    sys.exit(2)

data = json.loads(proc.stdout or "{}")
msgs = data if isinstance(data, list) else data.get("messages", [])
if not msgs:
    print(f"{handle}: 没有消息记录")
    sys.exit(1)

print(f"{handle} 最近 {min(LIMIT, len(msgs))} 条：")
for m in msgs[-LIMIT:]:
    text = (m.get("text") or "").replace("\n", " / ")[:64]
    print(f"  {m.get('delivery',''):<10} {m.get('service',''):<9} {text}")

state = {"delivered": 0, "read": 0, "sent": 0, "other": 0}
for m in msgs[-LIMIT:]:
    d = m.get("delivery") or ""
    state[d if d in state else "other"] += 1
print(f"\n汇总: {state}")
sys.exit(0 if state["delivered"] or state["read"] else 1)
