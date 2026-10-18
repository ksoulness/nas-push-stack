#!/usr/bin/env python3
"""查看网关的 /matrix.json（频道 × 调用方式），并做基本校验。

用法：
    GW_BASE=http://127.0.0.1:8098 python3 tests/verify_matrix.py

这个端点同时被推送控制台（例如 2999 页面）用来渲染矩阵列，
所以这里也校验字段完整性——字段缺了控制台就不会显示。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = os.environ.get("GW_BASE", "http://127.0.0.1:8098").rstrip("/")
REQUIRED = ("ok", "key", "formula", "bearer", "apprise", "feishu_lan", "sms_hook")

with urllib.request.urlopen(BASE + "/matrix.json", timeout=10) as r:
    data = json.load(r)

rows = data.get("channels") or []
print(f"service={data.get('service')}  channels={len(rows)}")
print(f"{'channel':<16}{'key':<22}{'target'}")
missing = []
for row in rows:
    cell = row.get("imessage") or {}
    bad = [f for f in REQUIRED if f not in cell]
    if bad:
        missing.append((row.get("channel"), bad))
    print(f"{row.get('channel',''):<16}{cell.get('key',''):<22}{cell.get('target','')}")

if missing:
    print()
    for ch, bad in missing:
        print(f"FAIL  {ch} 缺字段: {', '.join(bad)}")
    sys.exit(1)

print()
print("字段完整 ✓")
