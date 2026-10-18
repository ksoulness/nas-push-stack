# iMessage 短信转发通道（NAS 自建）

> 建立于 2026-10-18。用途：把安卓手机收到的短信，用**真正的 iMessage**（不是 Bark/推送模拟）发到 iPhone/iPad。
> 入口：`http://192.168.1.10:8098`（网关直连）**或** `http://192.168.1.10:3004`（推送入口，经 apprise 转发）。
> 所属项目：`push`（`/srv/push/imessage/`）。2999 页面的路由矩阵已有 `imessage` 列。

## 1. 链路

```
安卓手机 (SmsForwarder 规则)
   │  POST /hook/imessage  (HMAC-SHA256 签名, ±300s 时间窗)
   ▼
imessage-gw-svc  ── 验签 → 去重(指纹) → SQLite 持久队列
   │  imsg send（容器内 session bus）
   ▼
imessaged  ── rustpush → GSA/IDS 会话 → APNs
   │
   ▼
Apple iMessage 网络  ──►  iPhone「信息」App
```

- **不需要 Mac 转发**：消息由 NAS 直接发出，Mac 只在登录/极低频静默重登录时提供 anisette（设备证明）。
- 发送账号：`sender@example.com`；发送目标：`recipient@example.com`（固定）。
- 手机收到的样式：
  ```
  【短信】95588
  【银行】您的验证码是 123456

  收于 2026-10-18 22:24
  ```

## 2. 两种用法

### 2.1 短信转发（SmsForwarder 专用，带签名）

| 项 | 值 |
| --- | --- |
| 方法 / 地址 | `POST http://192.168.1.10:8098/hook/imessage` |
| 请求头 | `Content-Type: application/json` |
| 请求体 | `{"from":"[from]","content":"[content]","timestamp":"[timestamp]","sign":"[sign]","receive_time":"[receive_time]","device_mark":"[device_mark]"}` |
| 密钥 | `SMS_GW_SECRET`（见 `imessage/gw/gw.env`，0600，不要外传） |
| 签名算法 | `Base64(HMAC_SHA256(secret, timestamp + "\n" + secret))`；SmsForwarder 会对结果再做一次 urlEncode，网关两种都接受 |
| 老版本（4.0 前） | 表单形式：`from=[from]&content=[content]&timestamp=[timestamp]&sign=[sign]`，头改 `x-www-form-urlencoded` |

返回：`{"status":"queued","id":N}`；重复投递返回 `{"status":"duplicate","id":N}`。

### 2.2 作为推送通道（与 3004/2999 同一套格式）

键名沿用既有约定 `<service>_<channel>`，当前提供 8 个频道键：
`imessage_nas`、`imessage_btc`、`imessage_device`、`imessage_horizon`、`imessage_codex`、`imessage_deepseek`、`imessage_general`、`imessage_textverified`
（目前 8 个键都指向同一个目标；将来可按键映射到不同收件人或不同 Apple ID。）

| # | 格式 | 示例 |
| --- | --- | --- |
| 1 | gotify 公式（**推荐，走 3004**） | `curl -G "http://192.168.1.10:3004/push/imessage_nas" --data-urlencode "title=标题" --data-urlencode "body=内容"` |
| 2 | 路径式 | `GET http://192.168.1.10:8098/push/imessage_nas/<urlencode(内容)>` |
| 3 | MagicPush | `POST http://192.168.1.10:3004/api/push` + `Authorization: Bearer imessage_nas` + `{"title":"…","content":"…"}` |
| 4 | Apprise 兼容 | `POST http://192.168.1.10:8000/notify/imessage_nas`（表单或 JSON `{title, body}`） |
| 5 | 飞书兼容 | `POST http://192.168.1.10:3004/feishu/imessage_nas` + `{"msg_type":"text","content":{"text":"…"}}` |

> **3004 为什么能用**：Apprise 侧新增 8 个目标文件
> `/srv/push/apprise/config/imessage_<channel>.cfg`，每个一行
> `json://192.168.1.10:8098/notify/imessage_<channel>`；链路为
> `3004 → apprise → 8098 网关 → iMessage`，与其它列完全同构。8098 也可直连（少一跳），矩阵 `direct` 字段给的是直连公式。

⚠️ **中文注意**：参数放在 query string 时必须百分号编码（`curl -G --data-urlencode`，HTTP 库默认都会做）。直接往 URL 塞原始 UTF-8 字节会被 `http.server` 按 latin-1 解码成乱码（通用行为，与本网关无关）。放在 POST body（表单/JSON）里没有这个限制。

辅助端点：`/health`、`/healthz`、`/readyz`、`/queue`（需 `Authorization: Bearer <SMS_GW_TOKEN>`）、`/matrix.json`（与 2999 矩阵同构）。

## 3. SmsForwarder 设置步骤

1. **发送通道 → 新增 → Webhook**：填上面的地址、POST、请求头、请求体、密钥。
2. **转发规则 → 新增**：只勾「短信 · 收到」，来源号码留空（全部）或填关键词；勾选该 Webhook 通道。
3. **只建一条规则**（多条会重复发；同秒重投由指纹去重兜底）。
4. 手机时间开自动同步（时间窗 ±300s）。

## 4. 验证与排错

```bash
ssh nas "sudo docker logs -f imessage-gw-svc"                                   # 实时
ssh nas "curl -s http://192.168.1.10:8098/health"                              # 存活
ssh nas "sudo docker exec imessaged imsg --bus-address unix:path=/bus/bus status"  # 会话
```

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `401 bad signature` | 密钥不符 / 手机时间偏差 / sign 多次编码 | 核对密钥、校准时间 |
| `401 timestamp skew` | 手机时间漂移超 300s | 校准；或调大 `SMS_GW_MAX_SKEW_SEC` |
| `404 unknown key` | 推送格式键名不对 | 短信走 `/hook/imessage` |
| `400 missing content` | 请求体标签拼错 / 规则未勾短信 | 检查 `[content]` |
| 一直 `pending` | 后端未就绪或 daemon 掉线 | `/readyz` 应为 200 |
| 无任何响应 | 手机不在同网段 | 手机浏览器应能开 `/health` |

## 5. 运维要点（重要）

| 事项 | 说明 |
| --- | --- |
| 单一身份 | **同一 Apple ID 只允许一个 imessaged 进程**；旧 `imessage-linux-p2/` 目录里的 daemon 不可再启动 |
| 会话续期 | IDS 注册续期 `2026-12-02`；届时若 Apple 要求静默重登录，**Mac 必须当场醒着**（`pmset womp=0`，唤不醒） |
| 不重跑登录 | `imsg login` 属认证动作；不要为了排查反复登录 |
| 凭据位置 | `state/state.enc`（含 `sha256(密码)`，等价密码）、`state/hwconfig.plist`（设备身份）；目录 0700、文件 0600，**不外传不打印** |
| 已知告警 | `profiles` 报 `-22421`（iCloud 共享名片/头像需重新登录），**不影响收发** |
| 资源 | `imessaged` 11–22 MiB / ~0% CPU；`imessage-gw-svc` ~18 MiB |
| 日志脱敏 | 只记 id/状态/耗时/长度；号码打码；不记正文 |
| 3004 转发依赖 | Apprise 的 `imessage_<channel>.cfg` 若被删，`3004/push/imessage_*` 会 424；8098 直连不受影响 |
| 矩阵列 | 2999 的 `imessage` 列每次读网关 `/matrix.json`（3s 超时）；网关停机时该列显示 ✗，2999 本身不受影响 |

## 6. 相关文件

| 路径 | 作用 |
| --- | --- |
| `/srv/push/docker-compose.yml` | `imessaged` + `imessage-gw` 两个服务（与 apprise/hotify/bark-server 同栈） |
| `/srv/push/imessage/` | daemon 镜像、二进制、state、bus、apple-data |
| `/srv/push/imessage/gw/` | 网关（`gateway.py`、`gw.env`、队列 `data/queue.db`） |
| `/srv/push-secrets/` | 容器→Mac 的 SSH 私钥（与 Mac 上受限 `authorized_keys` 配对） |
| `E:\Code\主机NAS\docs\in-progress\2026-10-18-1715-安卓短信转iMessage网关选型评估-推进方案.md` | 立项与全部实测记录 |
