#!/usr/bin/env python3
"""Small standard-library CLI for iphone-control's newline JSON MCP transport."""
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

BIN = os.environ.get("IPHONE_CONTROL_BIN", str(Path.home() / ".local/bin/iphone-control"))


class MCP:
    def __init__(self):
        env = dict(os.environ)
        env.setdefault("IPHONE_CONTROL_LANG", "en")
        self.p = subprocess.Popen(
            [BIN, "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=sys.stderr, env=env, text=True, bufsize=1,
        )
        self.messages = queue.Queue()
        self.request_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                self.messages.put(json.loads(line))
            except json.JSONDecodeError:
                print("Ignoring non-JSON MCP stdout line", file=sys.stderr)
        self.messages.put(None)

    def send(self, message):
        self.p.stdin.write(json.dumps(message) + "\n")
        self.p.stdin.flush()

    def call(self, method, params):
        self.request_id += 1
        self.send({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params})
        while True:
            message = self.messages.get(timeout=180)
            if message is None:
                raise RuntimeError("MCP server closed stdout before replying")
            if message.get("id") == self.request_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            try:
                self.p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        self.p.stdout.close()


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("mcpc.py tools | shot [PATH] | els [LIMIT] [FILTER] | tap X Y | tapt TEXT | type TEXT | app BUNDLE | call TOOL [JSON]")
        return 0
    cmd = argv[0]
    if cmd == "tools":
        name, args = None, {}
    elif cmd == "shot":
        name, args = "iphone_screenshot", {}
    elif cmd == "els":
        name, args = "iphone_list_elements", {"limit": int(argv[1]) if len(argv) > 1 else 120, "filter": argv[2] if len(argv) > 2 else ""}
    elif cmd == "tap":
        name, args = "iphone_tap", {"x": float(argv[1]), "y": float(argv[2]), "then": "elements"}
    elif cmd == "tapt":
        name, args = "iphone_tap_element", {"text": argv[1], "then": "elements"}
    elif cmd == "type":
        name, args = "iphone_type_text", {"text": argv[1]}
    elif cmd == "app":
        name, args = "iphone_launch_app", {"app": argv[1], "then": "elements"}
    elif cmd == "call":
        name, args = argv[1], json.loads(argv[2]) if len(argv) > 2 else {}
    else:
        raise ValueError("Unknown command: " + cmd)
    client = MCP()
    try:
        client.call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "nas-push-cli", "version": "1"}})
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        result = client.call("tools/call", {"name": name, "arguments": args}) if name else client.call("tools/list", {})
        if name is None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for item in result.get("content", []):
                if item.get("type") == "text":
                    print(item["text"])
                elif item.get("type") == "image":
                    path = Path(argv[1] if cmd == "shot" and len(argv) > 1 else "/tmp/mcp_shot.jpg")
                    path.write_bytes(base64.b64decode(item["data"], validate=True))
                    print("[image] " + str(path))
        return 1 if result.get("isError") else 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
