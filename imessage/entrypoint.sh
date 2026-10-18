#!/bin/sh
# imessaged 入口：容器内私有 session bus（socket 放在共享卷 /bus，供 gateway 容器连接）
# 不执行任何 Apple 认证动作；认证只由 `imsg login` 触发。
set -eu

STATE_DIR="${IMESSAGED_STATE_DIR:-/state}"
BUS_DIR="${IMESSAGED_BUS_DIR:-/run/dbus-session}"
BUS_SOCK="$BUS_DIR/bus"

mkdir -p "$STATE_DIR" "$BUS_DIR" "$XDG_RUNTIME_DIR"
chmod 700 "$BUS_DIR" "$XDG_RUNTIME_DIR" 2>/dev/null || true

# 容器重启会留下旧的 socket 文件，先清掉
rm -f "$BUS_SOCK"

dbus-daemon --session --address="unix:path=$BUS_SOCK" --nofork --nopidfile &
BUS_PID=$!
trap 'kill "$BUS_PID" 2>/dev/null || true' INT TERM EXIT

i=0
while [ ! -S "$BUS_SOCK" ]; do
  i=$((i+1))
  [ "$i" -gt 100 ] && { echo "entrypoint: session bus did not come up" >&2; exit 1; }
  sleep 0.1
done
export DBUS_SESSION_BUS_ADDRESS="unix:path=$BUS_SOCK"

ANI="${IMESSAGED_ANISETTE_COMMAND:-}"
if [ -n "$ANI" ]; then
  echo "entrypoint: bus=$BUS_SOCK state=$STATE_DIR anisette=command(configured)"
  exec /usr/local/bin/imessaged \
    --state-dir "$STATE_DIR" \
    --key-provider file \
    --anisette-command "$ANI" \
    --no-link-previews
else
  echo "entrypoint: bus=$BUS_SOCK state=$STATE_DIR anisette=canary"
  exec /usr/local/bin/imessaged \
    --state-dir "$STATE_DIR" \
    --key-provider file \
    --anisette-command "touch '$STATE_DIR/ANI_CALLED'" \
    --no-link-previews
fi
