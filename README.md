# nas-push-stack

自建推送栈：**Apprise 中枢 + 多通道投递**，其中一个通道是**真正的 iMessage**——
不需要把 Mac 常开，也不需要 BlueBubbles / osascript，消息由一台 Linux 服务器直接发到 Apple 网络。

> 仅用于自用与通知。涉及 Apple 私有 API，非官方支持，**可能触发账号风控**，风险自担。

**关键词**：HTTP webhook · 5 种推送方言 · HMAC 签名 · 持久队列 · 真 iMessage · 零常开 Mac

---

## 一、解决什么痛点

| # | 痛点 | 常见做法 | 本项目怎么做 |
| --- | --- | --- | --- |
| 1 | **推送 ≠ 消息**。Bark / gotify / Hotify 只是"通知横幅"，过目即忘；真正想在「信息」App 里留一条会话、能回、能跨设备同步，必须是 iMessage | 凑合用推送 | 发**真 iMessage**：iPhone/iPad/Mac 三端同一条会话、有历史、可回复 |
| 2 | **想发真 iMessage，就得让 Mac 常开**。BlueBubbles / AppleScript 方案要求一台 Mac 24 小时开机 + 授予辅助功能权限；笔记本合盖就断 | 买台 Mac mini 常开 | 消息由 **Linux 直接发出**；Mac 只在**首次登录**和**极低频静默重登录**时出现几分钟，平时可以一直睡 |
| 3 | **已有开源方案被 Apple 拦死**：公共 anisette-v3 服务器为"自称 macOS"的客户端签发的设备证明会被拒，登录永远 `-80045` | 换 IP、换账号、降版本……全无效 | 让**真 Mac** 用系统 AOSKit/AuthKit 铸造 anisette；`-80045` 消失，连 2FA 都不弹（[根因分析](docs/iMessage网关-经验与踩坑.md)） |
| 4 | **推送入口五花八门**：gotify 用 `GET /push/<key>?title=&body=`，MagicPush 用 `POST /api/push` + Bearer，Apprise 用 `POST /notify/<key>`，飞书用 `{"content":{"text":…}}` …每接一个新服务就要写一套适配 | 每个服务写一个适配层 | **一个网关同时说 5 种方言**，键名沿用 `<service>_<channel>`（如 `imessage_nas`），老调用方一行不改就能投到 iMessage |
| 5 | **短信转发不可靠**：手机重试、断网重连、服务重启都会重复投递或丢消息 | 裸 webhook，无重试 | 指纹去重（同一条只发一次）+ SQLite 持久队列 + 三态（`sent`/`failed`/`unknown`）+ 退避重试 `5/15/60/300/900s` |
| 6 | **不知道到底送到没有** | 靠肉眼看手机 | 用 Apple 侧回执 `delivery: delivered` 做程序化验收（`tests/check_delivery.py`） |

一句话：**把"发一条真 iMessage"变成一次普通的 HTTP 请求**，并且不用养一台常开的 Mac。

---

## 二、支持哪些发送渠道

### 主入口：HTTP Webhook（这就是全部）

网关只暴露 HTTP，不依赖任何 MQTT / 长连接 / 私有 SDK。**能发 HTTP 的东西都能接**：

| 端点 | 方法 | 认证 | 典型来源 |
| --- | --- | --- | --- |
| **`/hook/imessage`** | POST | **HMAC-SHA256 签名**（`SMS_GW_SECRET`） | **安卓短信转发**（SmsForwarder 报文格式）、任何带签名的自建钩子 |
| **`/push/<key>`** | GET | 键名（`imessage_nas` 等） | gotify 风格"公式"：`?title=&body=`，一行 curl 就能用 |
| **`/push/<key>/<msg>`** | GET | 键名 | 最省事：把内容直接拼进 URL 路径（urlencode） |
| **`/api/push`** | POST | `Authorization: Bearer <key>` | MagicPush 兼容：`{"title":"…","content":"…"}` |
| **`/notify/<key>`** | POST | 键名 | **Apprise `json://` 目标**：`{"title","body"}`，让 Apprise 中枢帮你转发 |
| **`/feishu/<key>`** | POST | 键名 | 飞书/企业微信风格：`{"msg_type":"text","content":{"text":"…"}}` |
| `/health` `/healthz` `/readyz` `/queue` `/matrix.json` | GET | 可选 token | 运维与接入（`/matrix.json` 给出"频道 × 调用方式"，可直接渲染成矩阵页） |

**键名规则**：`<service>_<channel>`，例如 `imessage_nas`、`imessage_device`。
同一个目标可以挂多个键（区分来源/设备），键名只做频道标签，不携带权限。

### 可以直接接进来的东西（举例）

| 来源 | 怎么接 |
| --- | --- |
| **安卓手机短信**（SmsForwarder / 短信转发器） | `POST /hook/imessage` + 签名，见 [用法 A](#a-安卓短信转发推荐) |
| **Home Assistant** | `rest_command` 调 `POST /api/push`（Bearer）或 `GET /push/imessage_nas` |
| **Uptime Kuma / Grafana / Alertmanager** | 它们的 "Webhook" 通知 → `POST /api/push`（Bearer，`content` 里塞告警文本） |
| **Apprise**（你已有的推送中枢） | 加一个目标 `json://<网关>:8098/notify/imessage_<channel>`，之后所有调用方只认中枢 |
| **shell / cron / NAS 定时任务** | `curl -G 'http://<网关>:8098/push/imessage_nas' --data-urlencode 'body=备份完成'` |
| **CI（GitHub Actions 等）/ 爬虫 / 监控脚本** | 任意 HTTP 客户端，POST JSON 即可 |
| **自建推送控制台** | 读 `/matrix.json` 渲染成"频道 × 通道"矩阵，见 [extras/2999-matrix](extras/2999-matrix/) |
| 企业微信 / 钉钉 / 飞书机器人 | 已支持飞书体；其它方言按同一模式加个分支即可 |

> **不需要**公网暴露：网关默认只绑 `127.0.0.1`，要局域网设备用就把 `GW_BIND=0.0.0.0`。
> 出站只需要 Apple 的端点（GSA / IDS / APNs:443、5223），外加登录时到那台 Mac 的 SSH。

---

## 三、架构

```
SmsForwarder ─┐                          ┌─ Apprise(8000) ─┐
              ├─► imessage-gw (8098) ◄───┤                 │
3004 推送入口 ─┘   验签 / 去重 / 队列      └─ 直连 5 种方言 ──┘
                        │
                        ▼
                   imessaged（rustpush 引擎）
                        │  GSA 登录 → IDS 注册 → APNs 长连接
                        │  anisette（仅登录时）→ 一台真 Mac
                        ▼
                   Apple iMessage 网络 ──► iPhone「信息」
```

- **稳态零依赖 Mac**：登录完成后 daemon 只与 Apple 通信，Mac 可以一直睡着。
- **Mac 只在两件事上出现**：首次登录、Apple 作废会话后的静默重登录（可能几个月一次）。
- **为什么必须真 Mac**：Apple 会校验"客户端声明的设备"与"设备证明（anisette）"是否一致。
  公共 anisette-v3 服务给"自称 macOS"的客户端签头会被拒（`-80045`）。
  详见 [docs/iMessage网关-经验与踩坑.md](docs/iMessage网关-经验与踩坑.md)。

---

## 四、目录

```
├── docker-compose.yml          5 个服务：apprise / hotify / bark-server / imessaged / imessage-gw
├── .env.example                TZ、GW_BIND、SECRETS_DIR
├── imessage/
│   ├── Dockerfile              ubuntu:24.04 + dbus + openssh-client + 两个二进制
│   ├── entrypoint.sh           起私有 session bus（socket 落在共享卷 /bus），再 exec imessaged
│   ├── BUILD.md                ★ 编译二进制、准备 Apple 文件、Mac 侧 anisette、验收
│   └── gw/
│       ├── Dockerfile          网关镜像（自带 imsg 客户端 + python3）
│       ├── gateway.py          单文件网关（仅标准库，约 770 行）
│       └── gw.env.example      复制成 gw.env 后填密钥
├── docs/
│   ├── iMessage短信转发通道.md     使用文档（SmsForwarder 配置、5 种方言、排错表）
│   ├── iMessage网关-经验与踩坑.md  ★ 方法论：根因、走过的弯路、分层排除法、红线
│   └── upstream/               上游推送中枢的原始运行手册（脱敏版）
├── extras/2999-matrix/         把 iMessage 接进自建推送控制台（路由矩阵列）的补丁与说明
└── tests/                      端到端验证脚本
```

---

## 五、快速开始

1. **读 [imessage/BUILD.md](imessage/BUILD.md)**：编译 `imessaged` / `imsg`，放好 Apple 两个文件（含哈希校验）。
2. 准备 Mac 侧 anisette（BUILD.md §3），把受限 SSH 私钥放进 `secrets/`。
3. 准备目录与密钥：

   ```bash
   cp .env.example .env
   cp imessage/gw/gw.env.example imessage/gw/gw.env   # 填 SMS_GW_SECRET / SMS_GW_TOKEN / TARGET_HANDLE
   mkdir -p imessage/state imessage/bus imessage/gw/data
   chmod 700 imessage/state imessage/bus imessage/gw/data
   chown -R 10001:10001 imessage/state imessage/bus imessage/gw/data imessage/gw/gw.env
   ```

4. 起服务：`docker compose up -d --build imessaged imessage-gw`
5. 登录（一次性，**先确认你接受账号风险**）：

   ```bash
   printf '%s\n' "$PW" | docker exec -i imessaged imsg --bus-address unix:path=/bus/bus \
     login --apple-id you@example.com --password-stdin
   docker exec imessaged imsg --bus-address unix:path=/bus/bus status   # 期望 connected / registered
   ```

6. 端到端验证：

   ```bash
   export GW_BASE=http://127.0.0.1:8098 GW_ENV=./imessage/gw/gw.env
   python3 tests/test_dialects.py      # 5 种方言各发一条 + 反例（404/400）
   python3 tests/check_delivery.py     # 看 Apple 侧投递回执
   python3 tests/verify_matrix.py      # 校验 /matrix.json 字段完整
   ```

---

## 六、两种用法

### A. 安卓短信转发（推荐）

SmsForwarder → 自定义 Webhook：

| 项 | 值 |
| --- | --- |
| 地址 | `http://<NAS>:8098/hook/imessage` |
| 方法 / 头 | `POST` · `Content-Type: application/json` |
| 体 | `{"from":"[from]","content":"[content]","timestamp":"[timestamp]","sign":"[sign]","receive_time":"[receive_time]"}` |
| 密钥 | `SMS_GW_SECRET`（`gw.env` 里） |

签名：`Base64(HMAC_SHA256(secret, timestamp + "\n" + secret))`，时间窗 ±300s。
SmsForwarder 会对结果再 urlEncode 一次，网关两种都接受。

### B. 作为推送通道（5 种方言）

键名沿用 `<service>_<channel>`，例如 `imessage_nas`：

| # | 格式 | 调用 |
| --- | --- | --- |
| 1 | gotify 公式 | `GET /push/imessage_nas?title=…&body=…` |
| 2 | 路径式 | `GET /push/imessage_nas/<urlencode(内容)>` |
| 3 | Bearer | `POST /api/push` + `Authorization: Bearer imessage_nas` + `{"title","content"}` |
| 4 | Apprise | `POST /notify/imessage_nas`（表单或 JSON `{title, body}`） |
| 5 | 飞书 | `POST /feishu/imessage_nas` + `{"msg_type":"text","content":{"text":"…"}}` |

> 若你已有 Apprise 中枢，只要加一个 `json://<NAS>:8098/notify/imessage_<channel>` 目标，
> 所有调用方就只认中枢一个入口（本仓库 compose 里的 `apprise` 就是这个角色）。

**中文提示**：参数放 query string 时必须百分号编码（`curl -G --data-urlencode`）；
原始 UTF-8 字节会被 `http.server` 按 latin-1 解析成乱码。放 POST body 没这个问题。

---

## 七、安全模型（重要）

| 材料 | 性质 | 处理 |
| --- | --- | --- |
| `imessage/state/state.enc` | 含 `sha256(密码)`，**等价密码** | 目录 0700，永不提交/外传/备份到共享盘 |
| `imessage/state/hwconfig.plist` | 设备身份（机型/序列号/MLB/ROM） | 0600，只在同一台 Mac 上生成 |
| `secrets/id_ed25519` | 能登录 Mac 的私钥 | 0600，Mac 侧公钥带 `from=` / `command=` 限制 |
| `imessage/gw/gw.env` | HMAC 密钥 / API token | 0600，从 `gw.env.example` 生成 |
| `apprise/config/*.cfg` | 每个都是 token / webhook | 永不提交 |

容器加固：`read_only` + `cap_drop: [ALL]` + `no-new-privileges` + `tmpfs /run:1777` + 资源上限；
网关端口默认只绑 `127.0.0.1`；日志只记 id / 状态 / 耗时 / 长度，号码打码、不记正文。

---

## 八、已知限制

| 限制 | 说明 |
| --- | --- |
| Mac 必须会醒 | 登录 / 静默重登录时 Mac 要在场；`pmset womp=0` 的机型**无法被网络唤醒** |
| 单一身份 | 同一 Apple ID 同时只能跑**一个** `imessaged` |
| 号码 handle | 想让发件人显示手机号，需 iPhone 用**同一 Apple ID** 启用 iMessage；否则显示邮箱 |
| `profiles` 告警 | `-22421`（共享名片/头像需重新登录）**不影响收发**，不要为它反复登录 |
| 消息库明文 | `messages.db` 里正文是明文（附件定位符单独加封），请自行做磁盘加密 |

---

## 九、排错速查

| 现象 | 看这里 |
| --- | --- |
| 登录 `-80045` | anisette 不是真 Mac 铸造的 → [经验与踩坑](docs/iMessage网关-经验与踩坑.md) |
| `anisette: command … failed` | Mac 没醒 / SSH 不通 / 公钥限制写错 |
| 网关 `401 bad signature` | `SMS_GW_SECRET` 不一致，或手机时间偏差超 300s |
| `/readyz` 503 | `TARGET_HANDLE` 未配，或容器里没有 `imsg` |
| 队列一直 `pending` | `imessaged` 未 `connected`（`imsg status`） |
| 中文乱码 | query 未百分号编码 |

---

## 十、致谢与许可

- 引擎与 daemon：[`thescruggs/imessage-linux`](https://github.com/thescruggs/imessage-linux)（SSPL-1.0）、[`rustpush`](https://github.com/OpenBubbles/rustpush)
- NAC 模拟思路：`open-absinthe` / `JJTech0130/nacserver`
- 推送中枢：[Apprise](https://github.com/caronc/apprise)、Hotify 控制台、[Bark](https://github.com/Finb/bark-server)

**本仓库自身代码以 MIT 许可发布**（见 [LICENSE](LICENSE)）；仓库不含任何 Apple 二进制，也不含任何第三方源码副本。
`docs/upstream/` 下的原始运行手册同样按 MIT 发布，其中的 IP / 路径 / 域名均为占位值。
