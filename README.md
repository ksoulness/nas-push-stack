# nas-push-stack

自建推送栈：**Apprise 中枢 + 多通道投递**，其中一个通道是**真正的 iMessage**——
不需要把 Mac 常开，也不需要 BlueBubbles/osascript，消息由一台 Linux 服务器直接发到 Apple 网络。

> 仅用于自用备份与通知。涉及 Apple 私有 API，非官方支持，**可能触发账号风控**，风险自担。

---

## 它解决什么问题

手机收到的短信（或其他系统事件）→ 家里的 NAS → **以你自己的 iMessage 身份**发到 iPhone/iPad，
而不是"假装是 iMessage"的普通推送。链路：

```
SmsForwarder ─┐
              ├─► imessage-gw (8098)  ──► imessaged ──► Apple iMessage ──► iPhone
3004 推送入口 ─┘     验签/去重/队列        rustpush 引擎
   (Apprise)                                   │
                                          anisette（仅登录时）
                                               ▼
                                        一台真 Mac（偶尔）
```

- **稳态零依赖 Mac**：登录完成后 daemon 只与 Apple 通信（GSA / IDS / APNs），Mac 可以一直睡着。
- **Mac 只在两件事上出现**：首次登录、以及 Apple 作废会话后的静默重登录（可能几个月一次）。
- **为什么必须真 Mac**：Apple 会校验"客户端声明的设备"与"设备证明（anisette）"是否匹配。
  用公共 anisette-v3 服务器为"自称 macOS"的客户端签头，会被直接拒（错误码 `-80045`）。
  详见 [docs/iMessage网关-经验与踩坑.md](docs/iMessage网关-经验与踩坑.md)。

---

## 目录

```
├── docker-compose.yml          5 个服务：apprise / hotify / bark-server / imessaged / imessage-gw
├── .env.example                TZ、GW_BIND、SECRETS_DIR
├── imessage/
│   ├── Dockerfile              ubuntu:24.04 + dbus + openssh-client + 两个二进制
│   ├── entrypoint.sh           起私有 session bus（socket 落在共享卷 /bus），再 exec imessaged
│   ├── BUILD.md                ★ 怎么编二进制、怎么准备 Apple 文件、Mac 侧怎么做、怎么验收
│   └── gw/
│       ├── Dockerfile          网关镜像（自带 imsg 客户端 + python3）
│       ├── gateway.py          单文件网关（仅标准库）
│       └── gw.env.example      复制成 gw.env 后填密钥
├── docs/
│   ├── iMessage短信转发通道.md     使用文档（SmsForwarder 配置、5 种推送方言、排错）
│   └── iMessage网关-经验与踩坑.md  ★ 方法论：根因、弯路、红线、重建清单
└── tests/                      端到端验证脚本（见下）
```

---

## 快速开始

1. **读 [imessage/BUILD.md](imessage/BUILD.md)**，完成两件事：编译 `imessaged`/`imsg`、放好 Apple 两个文件。
2. 准备 Mac 侧 anisette（BUILD.md §3），把 SSH 私钥放进 `secrets/`。
3. 准备目录与密钥：

   ```bash
   cp .env.example .env
   cp imessage/gw/gw.env.example imessage/gw/gw.env   # 填 SMS_GW_SECRET / SMS_GW_TOKEN
   mkdir -p imessage/state imessage/bus imessage/gw/data imessage/gw/data
   chmod 700 imessage/state imessage/bus imessage/gw/data
   chown -R 10001:10001 imessage/state imessage/bus imessage/gw/data imessage/gw/gw.env
   ```

4. 起服务：

   ```bash
   docker compose up -d --build imessaged imessage-gw
   docker compose logs -f imessage-gw
   ```

5. 登录（一次性，**先确保你能接受账号风险**）：

   ```bash
   printf '%s\n' "$PW" | docker exec -i imessaged imsg --bus-address unix:path=/bus/bus \
     login --apple-id you@example.com --password-stdin
   docker exec imessaged imsg --bus-address unix:path=/bus/bus status
   ```

6. 端到端验证：

   ```bash
   export GW_BASE=http://127.0.0.1:8098
   export GW_ENV=./imessage/gw/gw.env
   python3 tests/test_dialects.py      # 5 种推送格式各发一条
   python3 tests/verify_matrix.py      # 检查 /matrix.json（可选，默认那个 2999 页面用不到）
   python3 tests/check_delivery.py     # 查 daemon 侧投递状态（需 docker exec 权限）
   ```

---

## 两种用法

### A. 安卓短信转发（推荐）

SmsForwarder → 自定义 Webhook：

| 项 | 值 |
| --- | --- |
| 地址 | `http://<NAS>:8098/hook/imessage` |
| 方法 / 头 | `POST` · `Content-Type: application/json` |
| 体 | `{"from":"[from]","content":"[content]","timestamp":"[timestamp]","sign":"[sign]","receive_time":"[receive_time]"}` |
| 密钥 | `SMS_GW_SECRET`（`gw.env` 里） |

签名：`Base64(HMAC_SHA256(secret, timestamp + "\n" + secret))`，时间窗 ±300s；SmsForwarder 会对结果再 urlEncode 一次，两种都接受。

### B. 作为推送通道（5 种方言）

键名沿用 `<service>_<channel>` 约定，例如 `imessage_nas`：

| # | 格式 | 调用 |
| --- | --- | --- |
| 1 | gotify 公式 | `GET /push/imessage_nas?title=…&body=…` |
| 2 | 路径式 | `GET /push/imessage_nas/<urlencode(内容)>` |
| 3 | Bearer | `POST /api/push` + `Authorization: Bearer imessage_nas` + `{"title","content"}` |
| 4 | Apprise | `POST /notify/imessage_nas`（表单或 JSON） |
| 5 | 飞书 | `POST /feishu/imessage_nas` + `{"content":{"text":"…"}}` |

另外：`/health`、`/readyz`、`/queue`（需 `SMS_GW_TOKEN`）、`/matrix.json`（频道 × 调用方式，便于接到自己的推送控制台）。

> 若你已有 3004 那种"Apprise 中枢"，可以再加一个 `json://<NAS>:8098/notify/imessage_<channel>` 目标，
> 让所有调用方只认中枢一个入口（本仓库的 compose 里 `apprise` 就是这个角色）。

**中文提示**：参数放 query string 时必须百分号编码（`curl -G --data-urlencode`）。原始 UTF-8 字节会被
`http.server` 按 latin-1 解析成乱码；放 POST body 没这个问题。

---

## 安全模型（重要）

| 材料 | 性质 | 处理 |
| --- | --- | --- |
| `imessage/state/state.enc` | 含 `sha256(密码)`，**等价密码** | 目录 0700，永不提交/外传/备份到共享盘 |
| `imessage/state/hwconfig.plist` | 设备身份（机型/序列号/MLB/ROM） | 0600，只在同一台 Mac 上生成 |
| `secrets/id_ed25519` | 能登录 Mac 的私钥 | 0600，且 Mac 侧公钥要带 `from=`/`command=` 限制 |
| `imessage/gw/gw.env` | HMAC 密钥 / API token | 0600，用 `.env.example` 生成 |
| `apprise/config/*.cfg` | 每个都是 token / webhook | 永不提交 |

容器加固：`read_only` + `cap_drop: [ALL]` + `no-new-privileges` + `tmpfs /run:1777` + 资源上限；
网关端口默认只绑 `127.0.0.1`。

---

## 已知限制

| 限制 | 说明 |
| --- | --- |
| Mac 必须会醒 | 登录/静默重登录时 Mac 要在场。`pmset womp=0` 的机型**无法被网络唤醒** |
| 单一身份 | 同一 Apple ID 同时只能跑**一个** `imessaged` |
| 号码 handle | 想让发件人显示手机号，需要 iPhone 用**同一 Apple ID** 启用 iMessage；否则显示邮箱 |
| `profiles` 告警 | `-22421`（共享名片/头像需重新登录）**不影响收发**，不要为它反复登录 |
| 消息库明文 | `messages.db` 里短信正文是明文（附件定位符单独加封），请自行做磁盘加密 |

---

## 排错速查

| 现象 | 看这里 |
| --- | --- |
| 登录 `-80045` | anisette 不是真 Mac 铸造的 → [经验与踩坑 §2/§4](docs/iMessage网关-经验与踩坑.md) |
| `anisette: command … failed` | Mac 没醒 / SSH 不通 / 公钥限制写错 |
| 网关 `401 bad signature` | `SMS_GW_SECRET` 不一致或手机时间偏差 |
| `/readyz` 503 | `TARGET_HANDLE` 未配或容器里没有 `imsg` |
| 队列一直 `pending` | `imessaged` 未 `connected`（`imsg status`） |
| 中文乱码 | query 未百分号编码 |

---

## 致谢与许可

- 引擎与 daemon：[`thescruggs/imessage-linux`](https://github.com/thescruggs/imessage-linux)（SSPL-1.0）、[`rustpush`](https://github.com/OpenBubbles/rustpush)
- NAC 模拟思路：`open-absinthe` / `JJTech0130/nacserver`
- 推送中枢：[Apprise](https://github.com/caronc/apprise)、Hotify 控制台、[Bark](https://github.com/Finb/bark-server)

**本仓库自身代码暂未选择开源许可（All rights reserved）**；`gateway.py` 与 `tests/` 可自由用于自建部署。
仓库不含任何 Apple 二进制，也不含任何第三方源码副本。
