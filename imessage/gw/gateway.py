#!/usr/bin/env python3
"""imessage-gw — SMS → iMessage 网关（NAS Docker，单文件、仅标准库）。

链路：
    SmsForwarder --(HMAC-SHA256 签名)--> POST /hook/imessage
    或 Apprise 自定义 JSON 目标          --> POST /hook/apprise
        -> 验签 -> 过滤 -> 去重 -> SQLite 持久队列 -> Matrix 房间 -> mautrix-imessage 桥 -> Apple iMessage

设计要点：
  * 去重：fingerprint UNIQUE(sha256(from|timestamp|body))，重放/重试不会重复发。
  * 幂等：Matrix `PUT /rooms/{rid}/send/{type}/{txnId}` 的 txnId 由本地消息 id 派生，
    同一 id 重投对 homeserver 是幂等的 —— 因此超时后可安全重试（真正的不确定点在
    桥 -> iMessage 那一跳，桥自己负责）。
  * 三态：pending / sending / sent / failed / unknown，unknown 默认不自动重发。
  * 脱敏：日志只记 id / 状态 / 耗时 / 长度；正文与号码默认不入日志。
  * 认证：SmsForwarder 侧 HMAC(secret, timestamp + "\n" + secret) 与 ±s`MAX_SKEW` 时间窗；
    Apprise 侧 Bearer Token（内网/容器网络专用）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------- 配置
ENV = os.environ


def _env(key: str, default: str = "") -> str:
    return (ENV.get(key) or default).strip()


LISTEN = _env("SMS_GW_LISTEN", "0.0.0.0:8099")
STATE_DIR = _env("STATE_DIR", "/data")
DB_PATH = os.path.join(STATE_DIR, "queue.db")
SMS_SECRET = _env("SMS_GW_SECRET")
APP_TOKEN = _env("SMS_GW_TOKEN")
MAX_SKEW_SEC = int(_env("SMS_GW_MAX_SKEW_SEC", "300") or 300)
ALLOW_FROM = [p.strip() for p in _env("SMS_ALLOW_FROM").split(",") if p.strip()]
MAX_LEN = int(_env("SMS_MAX_LEN", "1200") or 1200)
REDACT = _env("REDACT_LOGS", "1") != "0"

# ---- 2999/3004 推送体系兼容（频道 × 格式）---------------------------------
# 键名沿用既有约定 <service>_<channel>，例如 imessage_nas；"*" 表示不校验。
PUSH_KEYS = [k.strip() for k in _env("PUSH_KEYS", "imessage_nas").split(",") if k.strip()]
# 推送类消息的渲染模板（短信仍用 MSG_TEMPLATE）
PUSH_TEMPLATE = _env("PUSH_TEMPLATE", "{body}")

MATRIX_HS = _env("MATRIX_HOMESERVER", "http://homeserver:8008").rstrip("/")
MATRIX_TOKEN = _env("MATRIX_TOKEN")
TARGET_ROOM = _env("TARGET_ROOM_ID")
TARGET_HANDLE = _env("TARGET_HANDLE")

# ---- 发送后端（可插拔）----------------------------------------------------
# imessaged    : Linux 原生 imessage-linux daemon（rustpush + 真 Mac anisette），调 `imsg send`
# matrix       : 经 mautrix-imessage 桥的 portal 房间（方案 A，被 Apple -80045 阻断）
# bluebubbles  : Mac 上 BlueBubbles Server 的 REST API（方案 C-3）
# macbridge    : Mac 上自建的最小 AppleScript 发送服务（方案 C-1）
BACKEND = _env("IMESSAGE_BACKEND", "imessaged").strip().lower()
# imessage-linux daemon
IMSG_BIN = _env("IMSG_BIN", "imsg")
IMESSAGED_BUS = _env("IMESSAGED_BUS")              # 例 unix:path=/bus/bus；空则用 daemon 默认 session bus
IMSG_TIMEOUT_SEC = int(_env("IMSG_TIMEOUT_SEC", "120") or 120)
# BlueBubbles
BB_URL = _env("BB_URL").rstrip("/")
BB_PASSWORD = _env("BB_PASSWORD")
BB_METHOD = _env("BB_METHOD", "apple-script")      # apple-script | private-api
TARGET_CHAT_GUID = _env("TARGET_CHAT_GUID")        # 例 iMessage;-;+8613800138000
# 自建 Mac 发送服务
MAC_BRIDGE_URL = _env("MAC_BRIDGE_URL").rstrip("/")
MAC_BRIDGE_TOKEN = _env("MAC_BRIDGE_TOKEN")

TEMPLATE = _env(
    "MSG_TEMPLATE",
    "【短信】{from}\n{body}\n\n收于 {received}",
).replace("\\n", "\n")

RETRY_MAX = int(_env("RETRY_MAX", "5") or 5)
RETRY_BACKOFF = [int(x) for x in _env("RETRY_BACKOFF_SEC", "5,15,60,300,900").split(",") if x.strip()]
HTTP_TIMEOUT = int(_env("HTTP_TIMEOUT_SEC", "20") or 20)

LOG_LOCK = threading.Lock()


def log(event: str, **kw) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event}
    rec.update(kw)
    with LOG_LOCK:
        sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
        sys.stdout.flush()


# ----------------------------------------------------------------- 数据库
SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT NOT NULL UNIQUE,
    sender          TEXT,
    body            TEXT NOT NULL,
    received_ts     INTEGER,
    created_at      INTEGER NOT NULL,
    state           TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    txn_id          TEXT NOT NULL,
    matrix_event_id TEXT,
    last_error      TEXT,
    kind            TEXT NOT NULL DEFAULT 'sms',
    updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state ON messages(state, next_attempt_at);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    conn = db()
    try:
        conn.executescript(SCHEMA)
        # 兼容旧库：kind 列（sms | push）后加
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        if "kind" not in cols:
            conn.execute("ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'sms'")
            log("db.migrate", column="kind")
    finally:
        conn.close()


# ----------------------------------------------------------------- 工具
_TS_KEYS = ("timestamp", "ts", "time")


def _fingerprint(sender: str, ts: str, body: str) -> str:
    raw = f"{sender}|{ts}|{body}".encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:40]


def _mask_sender(sender: str) -> str:
    if not sender:
        return "-"
    if not REDACT:
        return sender
    if len(sender) <= 4:
        return sender[0] + "***"
    return sender[:3] + "***" + sender[-2:]


def _check_hook_auth(headers, params: dict) -> tuple[bool, str]:
    """SmsForwarder 风格：timestamp + sign(HMAC-SHA256, Base64)。"""
    if not SMS_SECRET:
        return True, "no-secret-configured"
    ts = ""
    for k in _TS_KEYS:
        if params.get(k):
            ts = str(params[k])
            break
    sign = str(params.get("sign") or "")
    if not ts or not sign:
        return False, "missing timestamp/sign"
    try:
        delta = abs(int(time.time()) - int(int(ts) / 1000))
    except (TypeError, ValueError):
        return False, "bad timestamp"
    if delta > MAX_SKEW_SEC:
        return False, f"timestamp skew {delta}s"
    msg = f"{ts}\n{SMS_SECRET}".encode("utf-8")
    expected = base64.b64encode(hmac.new(SMS_SECRET.encode("utf-8"), msg, hashlib.sha256).digest()).decode()
    # SmsForwarder 会对 sign 再做一次 urlEncode（+ → %2B、= → %3D），JSON 体里原样带着 %xx
    candidates = {sign, urllib.parse.unquote_plus(sign), urllib.parse.unquote(sign)}
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        return False, "bad signature"
    return True, "ok"


def _parse_received(raw) -> int:
    """把 receive_time 归一成秒级时间戳。

    SmsForwarder 的 [receive_time] 是 `yyyy-MM-dd HH:mm:ss` 文本，[timestamp] 是毫秒数；
    两者都要能接受，解析失败退回当前时间。
    """
    s = str(raw or "").strip()
    if not s:
        return int(time.time())
    try:
        v = int(float(s))
        return int(v / 1000) if v > 10 ** 11 else int(v)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(s[:19], fmt)))
        except ValueError:
            continue
    return int(time.time())


def _allow_sender(sender: str) -> bool:
    if not ALLOW_FROM:
        return True
    return any(sender.startswith(p) for p in ALLOW_FROM)


def _render(sender: str, body: str, received_ts: int | None) -> str:
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(received_ts)) if received_ts else "-"
    return TEMPLATE.format(**{"from": sender or "未知", "body": body, "received": when})


# ----------------------------------------------------------------- 后端发送
def _post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[str, str, str | None]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
            return "sent", "ok", str(data.get("guid") or data.get("identifier") or data.get("id") or "")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (401, 403):
            return "failed", f"auth {exc.code}", None
        if exc.code == 429:
            return "unknown", "rate limited 429", None
        if 400 <= exc.code < 500:
            return "failed", f"http {exc.code} {detail[:80]}", None
        return "unknown", f"http {exc.code}", None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 连不上 = 未发出；超时 = 不确定，两种情况都交给上层按退避重投
        return "unknown", f"{type(exc).__name__}: {exc}", None


def imessaged_send(text: str, txn_id: str) -> tuple[str, str, str | None]:
    """Linux 原生 daemon：`imsg send <handle> <text> --json`（走 session bus）。

    退出码语义（crates/imsg）：
      0 ok / 1 error / 2 usage / 3 not-logged-in / 4 two-factor /
      5 no-daemon / 6 not-found / 7 transient
    其中 7 与超时按 unknown（可退避重试），其余失败为 failed（不自动重试）。
    """
    if not TARGET_HANDLE:
        return "failed", "imessaged 未配置 TARGET_HANDLE", None
    cmd = [IMSG_BIN]
    if IMESSAGED_BUS:
        cmd += ["--bus-address", IMESSAGED_BUS]
    cmd += ["send", TARGET_HANDLE, text, "--json"]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=IMSG_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired:
        return "unknown", f"imsg timeout {IMSG_TIMEOUT_SEC}s", None
    except OSError as exc:
        return "failed", f"imsg spawn failed: {exc}", None
    elapsed = int((time.time() - started) * 1000)
    out = (proc.stdout or "").strip()
    if proc.returncode == 0:
        try:
            payload = json.loads(out)
            mid = payload.get("id") or payload.get("message_id")
        except (json.JSONDecodeError, AttributeError):
            mid = None
        return "sent", f"ok {elapsed}ms", str(mid or "")
    err = (proc.stderr or out).strip().splitlines()
    detail = (err[0][:160] if err else "") or f"exit {proc.returncode}"
    if proc.returncode == 7:                       # transient
        return "unknown", f"transient: {detail}", None
    return "failed", f"imsg rc={proc.returncode}: {detail}", None


def bluebubbles_send(text: str, txn_id: str) -> tuple[str, str, str | None]:
    """BlueBubbles REST: POST /api/v1/message/text?password=...

    tempGuid 用本地 txn_id：BlueBubbles 会用它做去重与回执关联。
    """
    if not BB_URL or not BB_PASSWORD or not TARGET_CHAT_GUID:
        return "failed", "BlueBubbles 未配置（BB_URL/BB_PASSWORD/TARGET_CHAT_GUID）", None
    url = f"{BB_URL}/api/v1/message/text?password={urllib.parse.quote(BB_PASSWORD, safe='')}"
    payload = {
        "chatGuid": TARGET_CHAT_GUID,
        "tempGuid": txn_id,
        "message": text,
        "method": BB_METHOD,
    }
    return _post_json(url, payload)


def macbridge_send(text: str, txn_id: str) -> tuple[str, str, str | None]:
    """自建 Mac 发送服务（osascript）：POST /send，Bearer 鉴权。"""
    if not MAC_BRIDGE_URL or not MAC_BRIDGE_TOKEN or not TARGET_HANDLE:
        return "failed", "macbridge 未配置（MAC_BRIDGE_URL/MAC_BRIDGE_TOKEN/TARGET_HANDLE）", None
    payload = {"to": TARGET_HANDLE, "text": text, "id": txn_id}
    return _post_json(
        f"{MAC_BRIDGE_URL}/send", payload, {"Authorization": f"Bearer {MAC_BRIDGE_TOKEN}"}
    )



def matrix_send(room_id: str, text: str, txn_id: str) -> tuple[str, str, str | None]:
    """返回 (state, detail, event_id)。state ∈ sent/failed/unknown。"""
    url = (
        f"{MATRIX_HS}/_matrix/client/v3/rooms/"
        f"{urllib.parse.quote(room_id, safe='')}/send/m.room.message/{urllib.parse.quote(txn_id, safe='')}"
    )
    payload = json.dumps({"msgtype": "m.text", "body": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {MATRIX_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
            return "sent", "ok", data.get("event_id")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code in (401, 403):
            return "failed", f"auth {exc.code}", None
        if 400 <= exc.code < 500:
            return "failed", f"http {exc.code}", None
        return "unknown", f"http {exc.code}", None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 连接失败 = 未发出；超时 = 不确定。二者都用同一 txn_id 安全重投。
        return "unknown", type(exc).__name__, None


# ----------------------------------------------------------------- 队列写入
def enqueue(sender: str, body: str, received_ts: int | None, kind: str = "sms") -> tuple[str, int]:
    ts_key = str(received_ts or int(time.time() * 1000))
    fp = _fingerprint(sender, ts_key, body)
    now = int(time.time())
    conn = db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO messages "
            "(fingerprint, sender, body, received_ts, created_at, state, txn_id, kind, updated_at) "
            "VALUES (?,?,?,?,?,'pending',?,?,?)",
            (fp, sender, body, received_ts, now, "", kind, now),
        )
        if cur.rowcount == 0:
            row = conn.execute("SELECT id, state FROM messages WHERE fingerprint=?", (fp,)).fetchone()
            return "duplicate", int(row["id"]) if row else 0
        msg_id = int(cur.lastrowid)
        conn.execute("UPDATE messages SET txn_id=? WHERE id=?", (f"smsgw-{msg_id}", msg_id))
        return "queued", msg_id
    finally:
        conn.close()


# ----------------------------------------------------------------- 工作线程
_stop = threading.Event()


def worker() -> None:
    log("worker.start", room=bool(TARGET_ROOM), backend=BACKEND)
    while not _stop.is_set():
        try:
            _drain_once()
        except Exception as exc:  # 单条异常不得拖垮工作线程
            log("worker.error", error=f"{type(exc).__name__}: {exc}")
            _stop.wait(5)
            continue
        _stop.wait(2)


def _drain_once() -> None:
    now = int(time.time())
    conn = db()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE state='pending' AND next_attempt_at<=? ORDER BY id LIMIT 5",
            (now,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        _process(row)


def _process(row: sqlite3.Row) -> None:
    msg_id = int(row["id"])
    conn = db()
    try:
        claimed = conn.execute(
            "UPDATE messages SET state='sending', attempts=attempts+1, updated_at=? "
            "WHERE id=? AND state='pending'",
            (int(time.time()), msg_id),
        ).rowcount
    finally:
        conn.close()
    if not claimed:
        return

    sender = row["sender"] or ""
    kind = (row["kind"] if "kind" in row.keys() else "sms") or "sms"
    if kind == "push":
        text = PUSH_TEMPLATE.format(**{"from": sender or "-", "body": row["body"] or ""})
    else:
        text = _render(sender, row["body"], row["received_ts"])
    started = time.time()
    txn_id = row["txn_id"] or f"smsgw-{msg_id}"

    if BACKEND == "imessaged":
        state, detail, event_id = imessaged_send(text, txn_id)
    elif BACKEND == "bluebubbles":
        state, detail, event_id = bluebubbles_send(text, txn_id)
    elif BACKEND == "macbridge":
        state, detail, event_id = macbridge_send(text, txn_id)
    elif BACKEND == "matrix":
        if not TARGET_ROOM or not MATRIX_TOKEN:
            _finish(msg_id, "failed", "未配置 TARGET_ROOM_ID / MATRIX_TOKEN")
            log("send.skip", id=msg_id, reason="no-room-or-token")
            return
        state, detail, event_id = matrix_send(TARGET_ROOM, text, txn_id)
    else:
        _finish(msg_id, "failed", f"未知 backend: {BACKEND}")
        log("send.skip", id=msg_id, reason="unknown-backend", backend=BACKEND)
        return

    elapsed = int((time.time() - started) * 1000)
    _finish(msg_id, state, detail, event_id)
    log(
        "send",
        id=msg_id,
        state=state,
        detail=detail,
        ms=elapsed,
        len=len(row["body"]),
        sender=_mask_sender(sender),
        attempt=int(row["attempts"]) + 1,
    )


def _finish(msg_id: int, state: str, detail: str, event_id: str | None = None) -> None:
    now = int(time.time())
    conn = db()
    try:
        row = conn.execute("SELECT attempts, state FROM messages WHERE id=?", (msg_id,)).fetchone()
        attempts = int(row["attempts"]) if row else 1
        if state == "sent":
            conn.execute(
                "UPDATE messages SET state='sent', matrix_event_id=?, last_error=NULL, updated_at=? WHERE id=?",
                (event_id, now, msg_id),
            )
            return
        if state == "unknown" and attempts < RETRY_MAX:
            # 用同一 txn_id 重投：homeserver 侧幂等，因此安全。
            backoff = RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)] if RETRY_BACKOFF else 60
            conn.execute(
                "UPDATE messages SET state='pending', next_attempt_at=?, last_error=?, updated_at=? WHERE id=?",
                (now + backoff, detail, now, msg_id),
            )
            log("send.retry", id=msg_id, in_sec=backoff, detail=detail)
            return
        final = "unknown" if state == "unknown" else "failed"
        conn.execute(
            "UPDATE messages SET state=?, last_error=?, updated_at=? WHERE id=?",
            (final, detail, now, msg_id),
        )
        log("send.final", id=msg_id, state=final, detail=detail)
    finally:
        conn.close()


def _backend_ready() -> bool:
    if BACKEND == "imessaged":
        return bool(TARGET_HANDLE and shutil.which(IMSG_BIN))
    if BACKEND == "bluebubbles":
        return bool(BB_URL and BB_PASSWORD and TARGET_CHAT_GUID)
    if BACKEND == "macbridge":
        return bool(MAC_BRIDGE_URL and MAC_BRIDGE_TOKEN and TARGET_HANDLE)
    if BACKEND == "matrix":
        return bool(TARGET_ROOM and MATRIX_TOKEN)
    return False


def stats() -> dict:
    conn = db()
    try:
        rows = conn.execute("SELECT state, COUNT(*) c FROM messages GROUP BY state").fetchall()
        counts = {r["state"]: r["c"] for r in rows}
        last = conn.execute(
            "SELECT id, updated_at FROM messages WHERE state='sent' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return {
        "counts": counts,
        "last_sent_at": last["updated_at"] if last else None,
        "backend": BACKEND,
        "backend_ready": _backend_ready(),
        "target_configured": bool(TARGET_HANDLE or TARGET_CHAT_GUID),
    }


# ----------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "imessage-gw/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默默认访问日志（避免把 URL 参数写进日志）
        return

    # -- helpers
    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _params(self) -> tuple[dict, dict]:
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        ctype = (self.headers.get("Content-Type") or "").lower()
        body: dict = {}
        if raw:
            if "application/json" in ctype:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    body = {"_raw": raw.decode("utf-8", "replace")}
            else:
                body = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True).items()}
        merged = dict(query)
        merged.update({k: v for k, v in body.items() if not isinstance(v, (dict, list))})
        self._last_body = body
        return parsed, merged

    # -- GET
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        if parsed.path == "/healthz":
            # 进程存活即 200（容器健康检查用；未配置发送目标时也保持健康）
            st = stats()
            self._send(200, {"ok": True, **st})
            return
        if parsed.path == "/readyz":
            # 就绪：当前后端的目标与凭据齐备才 200
            st = stats()
            code = 200 if st["backend_ready"] else 503
            self._send(code, {"ready": code == 200, **st})
            return
        if parsed.path == "/queue":
            if not self._token_ok():
                self._send(401, {"error": "unauthorized"})
                return
            self._send(200, stats())
            return
        # ---- 2999/3004 兼容：GET /push/<key>?title=&body= 与 /push/<key>/<msg>
        if parsed.path == "/health":
            self._send(200, {"status": "ok"})
            return
        if parsed.path == "/matrix.json":
            self._send(200, push_matrix())
            return
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) in (2, 3) and parts[0] == "push":
            key = parts[1]
            title = str(params.get("title") or "")
            if len(parts) == 3:
                body = urllib.parse.unquote(parts[2])
            else:
                body = str(params.get("body") or params.get("content") or params.get("message") or "")
            self._push_send(key, title, body, "push")
            return
        self._send(404, {"error": "not found"})

    # -- POST
    def do_POST(self) -> None:
        parsed, params = self._params()
        if parsed.path == "/hook/imessage":
            self._hook_sms(params)
        elif parsed.path == "/hook/apprise":
            self._hook_apprise(params)
        elif parsed.path == "/api/push":
            # MagicPush 旧约：Bearer <key> + JSON {title, content|body|message}
            auth = self.headers.get("Authorization") or ""
            key = auth[7:].strip() if auth.startswith("Bearer ") else ""
            if not key:
                self._send(401, {"error": "Unauthorized"})
                return
            title = str(params.get("title") or "")
            body = str(params.get("content") or params.get("body") or params.get("message") or "")
            self._push_send(key, title, body, "api-push")
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) == 2 and parts[0] == "notify":
                # Apprise 兼容：表单或 JSON {title, body|message|content}
                title = str(params.get("title") or "")
                body = str(params.get("body") or params.get("message") or params.get("content") or "")
                if not body:
                    self._send(400, {"error": "missing body"})
                    return
                self._push_send(parts[1], title, body, "apprise")
            elif len(parts) == 2 and parts[0] == "feishu":
                # 飞书兼容：{content:{text}} 或 {text}
                node = (self._last_body or {}).get("content")
                text = str((node.get("text") if isinstance(node, dict) else node) or params.get("text") or "")
                if not text:
                    self._send(400, {"error": "empty content"})
                    return
                self._push_send(parts[1], str(params.get("title") or ""), text, "feishu", resp="feishu")
            else:
                self._send(404, {"error": "not found"})

    def _token_ok(self) -> bool:
        if not APP_TOKEN:
            return True
        auth = self.headers.get("Authorization") or ""
        return auth == f"Bearer {APP_TOKEN}"

    def _hook_sms(self, params: dict) -> None:
        ok, reason = _check_hook_auth(self.headers, params)
        if not ok:
            log("hook.reject", reason=reason, sender=_mask_sender(str(params.get("from", ""))))
            self._send(401, {"error": reason})
            return
        sender = str(params.get("from") or params.get("sender") or "").strip()
        body = str(params.get("content") or params.get("body") or params.get("msg") or "").strip()
        if not body:
            self._send(400, {"error": "missing content"})
            return
        if len(body) > MAX_LEN:
            body = body[:MAX_LEN] + "…"
        if not _allow_sender(sender):
            log("hook.filtered", reason="sender-not-allowed", sender=_mask_sender(sender))
            self._send(200, {"status": "filtered"})
            return
        received_sec = _parse_received(params.get("receive_time") or params.get("timestamp"))
        status, msg_id = enqueue(sender, body, received_sec)
        log("hook.sms", status=status, id=msg_id, sender=_mask_sender(sender), len=len(body))
        self._send(200, {"status": status, "id": msg_id})

    def _push_send(self, key: str, title: str, body: str, source: str, resp: str = "apprise") -> None:
        """2999/3004 三种格式的统一落点：<key> 只决定频道，正文由 title+body 组成。"""
        if not push_key_ok(key):
            log("push.reject", reason="unknown-key", key=key)
            self._send(404, {"error": "unknown key"})
            return
        body = (body or "").strip()
        if not body:
            self._send(400, {"error": "missing body"})
            return
        title = (title or "").strip()
        body = body.strip()
        # 3004 转发时会把同一段文本同时放进 title（带 [key] 前缀）与 body，避免重复一遍
        if body and title.endswith(body):
            text = title
        else:
            text = f"{title}\n{body}" if title else body
        if len(text) > MAX_LEN:
            text = text[:MAX_LEN] + "…"
        status, msg_id = enqueue(key, text, int(time.time()), kind="push")
        log("push.enqueue", source=source, key=key, status=status, id=msg_id, len=len(text))
        if resp == "feishu":
            self._send(200, {"StatusCode": 0, "StatusMessage": "success", "code": 0,
                             "queue_status": status, "id": msg_id})
        else:
            self._send(200, {"apprise_status": 200, "status": status, "id": msg_id})

    def _hook_apprise(self, params: dict) -> None:
        if not self._token_ok():
            self._send(401, {"error": "unauthorized"})
            return
        body = str(params.get("body") or params.get("message") or params.get("content") or "").strip()
        sender = str(params.get("title") or "Apprise").strip()
        if not body:
            self._send(400, {"error": "missing body"})
            return
        if len(body) > MAX_LEN:
            body = body[:MAX_LEN] + "…"
        status, msg_id = enqueue(sender, body, int(time.time()))
        log("hook.apprise", status=status, id=msg_id, len=len(body))
        self._send(200, {"status": status, "id": msg_id})


def _mask_target(target: str) -> str:
    """目标句柄脱敏（邮箱保域名，号码保后四位）。"""
    if not target:
        return "-"
    if "@" in target:
        local, _, domain = target.partition("@")
        return (local[:2] + "***@" + domain) if len(local) > 2 else "***@" + domain
    digits = re.sub(r"\D", "", target)
    return "+" + "*" * max(0, len(digits) - 4) + digits[-4:] if digits else "***"


def push_key_ok(key: str) -> bool:
    if not PUSH_KEYS or "*" in PUSH_KEYS:
        return True
    return key in PUSH_KEYS


def push_matrix() -> dict:
    """与 2999/3004 的 /matrix.json 同构：channels[].imessage 提供 5 种调用格式。"""
    public = _env("PUBLIC_HOST", "127.0.0.1")
    port = _env("PUBLIC_PORT") or LISTEN.rsplit(":", 1)[-1]
    base = f"http://{public}:{port}"
    keys = PUSH_KEYS if PUSH_KEYS and "*" not in PUSH_KEYS else ["imessage_nas"]
    rows = []
    for key in keys:
        ch = key.split("_", 1)[1] if "_" in key else key
        rows.append({
            "channel": ch,
            "imessage": {
                "ok": True,
                "key": key,
                "target": _mask_target(TARGET_HANDLE),
                "backend": BACKEND,
                "formula": f'curl "{base}/push/{key}?title=标题&body=内容"',
                "formula_path": f'{base}/push/{key}/' + urllib.parse.quote("内容"),
                "bearer": f'curl -X POST -H "Authorization: Bearer {key}" '
                          f'-d \'{{"title":"标题","content":"内容"}}\' {base}/api/push',
                "apprise": f"POST {base}/notify/{key}",
                "feishu_lan": f"{base}/feishu/{key}",
                "sms_hook": f"POST {base}/hook/imessage (HMAC)",
            },
        })
    return {"service": "imessage", "hub": "2999/3004 兼容", "channels": rows,
            "counts": stats().get("counts", {})}


def main() -> int:
    if not _backend_ready():
        log("start.warn", backend=BACKEND, detail="后端目标/凭据未配置：将只入队不发送")
    init_db()
    host, _, port = LISTEN.rpartition(":")
    server = ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)
    threading.Thread(target=worker, name="sender", daemon=True).start()
    log("listen", addr=LISTEN, db=DB_PATH, redact=REDACT, allow_from=len(ALLOW_FROM))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
