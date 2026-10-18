> **脱敏说明**：本文是原运行手册的公开版，内网 IP、宿主路径、域名、主机名均已替换为占位值（`192.168.1.x` / `/srv/...` / `example.com`）。渠道名（nas/btc/device/general/mp…）保留，它们是逻辑标签而非身份信息。凭据一律以 `<key>`、`<KEY>` 形式出现。

# 推送栈备份与恢复手册

> 建立时间：2026-09-10 15:01 · 备份授权：老大点名含全部 token/key 敏感文件
> 备份文件：`/srv/docker/backups/push-full-20260910-1457.tar.gz`（220KB，84 文件，root:root 700）
> 本手册同步于 NAS `…/push/apprise/docs/`（2999 文档标签可见）与本地 `docs\runbooks\`

## 1. 备份内容（84 文件）

| 路径 | 内容 | 说明 |
|---|---|---|
| `push/docker-compose.yml` | apprise + hotify 两容器编排 | 端口 8000/8002、挂载 |
| `push/apprise/config/*.cfg` ×21 | 21 个定向键（含 key1/token URL） | H8 后全定向体系；H9（2026-09-11）腿改指 `json://…:3005/` 正文重映射中继（备份 tar 内为旧 :8002 腿，恢复后需按 §4.7 重切） |
| `push/apprise/universal-push-proxy.py` | 3004 翻译器 + 2999 展示 + 8003 过滤代理 + 3005 正文中继 | 同进程四端口（H9 后；tar 内 09-10 版为三口，恢复后需重部署当日 py） |
| `push/apprise/icons/*.png` ×4 | 分频道图标 N/D/B/H | 128×128 |
| `push/apprise/docs/*.md` | 2999 文档标签数据源 | 三份 md |
| `push/apprise/backups/` | H6 前 cfg/桥备份、retired-legs-bridge | 历史回滚点（自包含） |
| `push/hotify/data/hotify.db` | **bbolt 设备注册表 + 消息历史**（4 台设备 uuid/token） | 最核心——丢了设备全要重注册 |
| `push/hotify/data/hotify.log` | 运行日志 | 排障用 |
| `push/bridge/config.json` | push-bridge 桥路由（**TG bot token / DC webhook token 明文**） | 7 路 telegram_*/discord_* |
| `push/docs/` | 推送文档快照 | 备份层副本 |
| `etc/systemd/system/push-translator.service` | 翻译器 systemd 单元 | ExecStart `3004 2999 8003 3005`（H9 加第 4 口；tar 内旧版为三口，恢复后需更新） |
| `etc/systemd/system/push-bridge.service` | 桥服务 systemd 单元 | User=nasuser |

**不含**：退役目录 `push/gotify|bark|magicpush-bridge/`（旧服务数据仍原位留在 NAS，未删）、`docker-compose.yml.bak-20260909*` ×5（旧 compose 版本）、`proxy-3004.log`（运行日志）。

## 2. 凭据位置速查（不印明文）

| 凭据 | 文件 |
|---|---|
| key1（Hotify admin Bearer / Basic 密码 / gotify 直推 token） | 各 `*.cfg` URL 内 + `bridge/config.json` |
| ACE 安卓 App token（uuid 73f9…） | 同上（`gotify_*.cfg` 的 json 腿 path） |
| iPad Bark key（uuid 1e45…，2026-09-10 14:23 接管 brak） | `bark_*.cfg` 腿 + hotify.db |
| TG bot token | `bridge/config.json` |
| Discord webhook URL | `bridge/config.json` |
| textverified key | `config/textverified.cfg` |

⚠️ 备份 tar 含上述全部明文——`/srv/docker/backups/` 目录 700 权限，勿外传 tar 本体。

## 3. 全栈恢复（NAS 灾后 / 迁机）

```bash
# 0) 前提：/srv/push 目录存在或将被重建；root 执行
# 1) 解包（tar 内为相对 / 的路径）
cd / && tar -xzpf /srv/docker/backups/push-full-20260910-1457.tar.gz
# 2) 属主修正（compose 项目用户）
chown -R nasuser:users /srv/push/apprise /srv/push/hotify /srv/push/bridge /srv/push/docs
# 3) 起容器（apprise + hotify）
cd /srv/push && docker compose up -d
# 4) systemd 单元（已解包到位）重载并启动
systemctl daemon-reload && systemctl enable --now push-translator push-bridge
# 5) 验证（全绿 = 恢复成功）
curl http://127.0.0.1:8002/ping          # pong（hotify）
curl http://127.0.0.1:8000/status        # OK（apprise）
curl http://127.0.0.1:3004/health        # ok（翻译器推送口）
curl http://127.0.0.1:2999/health        # ok（展示口）
curl http://127.0.0.1:8003/health       # ok（安卓过滤代理）
systemctl is-active push-bridge          # active
curl -X POST -d "title=恢复验证&body=iPad+安卓应各到一条（广播仅两端，TG/DC 不收）" http://127.0.0.1:3004/notify/textverified
# 预期：iPad 弹 + 安卓历史可见 + TG General + DC（桥各一路）+ Hotify 控制台 [textverified]
```

路由器公网映射（3004/8002/8003/2999 内网）需按原配置重做——映射在路由器侧不在本备份内。

## 4. 单组件恢复场景

### 4.1 只丢 apprise 配置（21 cfg / 键路由）
```bash
cd / && tar -xzpf /srv/docker/backups/push-full-20260910-1457.tar.gz srv/push/apprise/config
chown -R nasuser:users /srv/push/apprise/config
docker exec apprise apprise --help >/dev/null  # 容器自动重读? 否:
docker restart apprise
```

### 4.2 只丢 hotify 数据（设备注册表/消息历史）
```bash
cd / && tar -xzpf /srv/docker/backups/push-full-20260910-1457.tar.gz srv/push/hotify
chown -R nasuser:users /srv/push/hotify
docker restart hotify
# 设备四席（ACE/iPad-brak/tgram/discord）与全部消息历史随 bbolt 回来
# ⚠️ 若 bbolt 恢复后 iPad Bark 不收：按 H8 接管流程重查（runbook §12 bark 寄生注册条目）
```

### 4.3 只丢 8003/2999/3004 代理（universal-push-proxy.py）
本地权威源：`E:\Code\主机NAS\tools\universal-push-proxy.py` → 部署命令：
```powershell
$OutputEncoding=[Text.UTF8Encoding]::new($false)
Get-Content -Raw "E:\Code\主机NAS\tools\universal-push-proxy.py" | ssh root@nas "cat > /srv/push/apprise/universal-push-proxy.py && python3 -m py_compile /srv/push/apprise/universal-push-proxy.py && systemctl restart push-translator"
```
或从 tar 恢复：`tar -xzpf … srv/push/apprise/universal-push-proxy.py srv/push/apprise/icons` 后 `systemctl restart push-translator`。

### 4.4 只丢桥路由（TG/DC 不通但手机/iPad 正常）
```bash
cd / && tar -xzpf /srv/docker/backups/push-full-20260910-1457.tar.gz srv/push/bridge
chown -R nasuser:users /srv/push/bridge && systemctl restart push-bridge
# 桥进程若整丢：etc/systemd/system/push-bridge.service 同 tar 内，daemon-reload 后 enable --now
```

### 4.5 安卓 gotify App 重接
App 服务器地址 `http://push.example.com:8003`，登录向导所有字段随便填（代理强制注入 ACE token + 拦截 client 创建）。收不到=查 8003 公网映射 + `systemctl status push-translator`。

### 4.6 iPad Bark 重接（H8 寄生注册处理流程）
iPad App 服务器 URL 尾部 key 固定不可改——key 失效即自动重注册新设备。发现「未命名设备」：
1. `GET :8002/api/v1/devices`（Bearer key1）拿新 uuid
2. `PUT :8002/api/v1/devices/<uuid>/slug` body `{"slug":"brak"}` + `/name` `{"name":"ipad"}`
3. 删旧 brak 设备（`DELETE /api/v1/devices/<旧uuid>`）→ 21 键 cfg 零改动自动接管

（H9b 2026-09-11 修正：活 key 也会随冷启动复发寄生注册——无 slug 设备直接删即可，投递走 slug brak 不受影响。）

### 4.7 恢复后 H9 正文重映射回切（tar 是 09-10 三口版，恢复必做）
备份 tar 内 py/systemd/cfg 均为 H9 前旧版（腿指 :8002、ExecStart 三口），恢复后正文会再次丢失，按序回切：
1. 重新部署当日 `universal-push-proxy.py`（本地权威源 `tools\universal-push-proxy.py`，RelayHandler/3005 版）+ `sed -i 's|^ExecStart=.*|ExecStart=/usr/bin/python3 /srv/push/apprise/universal-push-proxy.py 3004 2999 8003 3005|' /etc/systemd/system/push-translator.service && systemctl daemon-reload && systemctl restart push-translator`
2. cfg 腿切中继：`cd /srv/push/apprise/config && sed -i "s|json://192.168.1.10:8002/|json://192.168.1.10:3005/|g" *.cfg`
3. 验证：`curl :3005/health` → ok；推一条 `curl -d "title=T&body=B" :3004/notify/gotify_nas`，查 `curl ":8003/message?token=<ACE>&limit=1"` 的 `message` 字段非空 + hotify.log `✓apns 1e45`

## 5. 恢复后回归

跑 `docs/configs/apprise-configs.md` 附录 A 的 21 键回归脚本（No.01-21 带编号）；判定基线 = 2026-09-10 14:38 全绿（iPad 8 ✓APNs / 安卓 6+广播 / TG 4 message_id 连续 / DC 3 sent）。

## 6. 本备份之前的清理记录

2026-09-10 15:02 删除 9 项旧 push 备份（apprise-config-pre-c1/d2/f-merge、bark-pre-d2、gotify-pre-retire、hotify-data-pre-e1、hotify-pre-migration、push-full-20260826、retired-legs-20260909/）——其覆盖的状态均已被本全量取代。保留：`ha-config-pre-notify-update-2026-09-09.tar.gz`（对象是 HA 配置，不属 push 栈）；`push/apprise/backups/` 内 H6/retired-legs-bridge 备份已含入本 tar。

