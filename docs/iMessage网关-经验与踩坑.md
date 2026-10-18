# iMessage 网关：经验与踩坑（可复用方法论）

> 记录：2026-10-18 · 项目落点 `/srv/push/imessage/`（并入 `push` 项目）· 立项与逐条实测见
> `docs/in-progress/2026-10-18-1715-安卓短信转iMessage网关选型评估-推进方案.md`（§9.2a–§9.2m）。
> 本文只留**结论、根因、方法、红线**，不重复时间线。

---

## 0. 三句话版本

1. **想在没有常开 Mac 的情况下发出真 iMessage，路径是**：Linux 上的 `thescruggs/imessage-linux`（rustpush 引擎 + 本地 NAC 模拟）＋ **一台真 Mac 只负责铸造 anisette 请求头**（登录与极低频静默重登录时用）。
2. **`-80045` 不是密码/IP/账号问题，而是"设备证明"问题**：Apple 自 2026-10-12 起拒收**公开 anisette-v3 服务器**为"自称 Mac"的客户端签发的头；换成真 Mac 用 AOSKit/AuthKit 原生铸造，一次通过、连 2FA 都不问。
3. **稳态完全不依赖 Mac**：登录完成后 daemon 只和 Apple 通信（GSA/IDS/APNs），Mac 可以一直睡着。

---

## 1. 最终架构

```
短信入口                                   推送入口
SmsForwarder ──POST /hook/imessage──┐     3004 /push|/api/push|/notify|/feishu
  (HMAC-SHA256, ±300s)              │        │ (现有 push 体系，键名 imessage_<channel>)
                                    ▼        ▼
                             apprise(8000) ──json://──┐
                                    │                │
                              imessage-gw-svc(8098)：验签 → 指纹去重 → SQLite 队列（pending/sending/sent/failed/unknown，退避 5/15/60/300/900）
                                    │  imsg send（容器内私有 session bus，socket 在共享卷 /bus）
                                    ▼
                              imessaged（rustpush）：GSA 登录 → IDS 注册 → APNs 长连接
                                    │  anisette（仅在登录/重登录时）：
                                    │     ssh -F /secrets/ssh_config anisette-host .local/bin/imessage-anisette
                                    ▼
                              Mac（真机 AOSKit/AuthKit 铸造头）        Apple iMessage 网络 ──► iPhone「信息」
```

| 组件 | 职责 | 关键约束 |
| --- | --- | --- |
| `imessaged` | 账号会话、IDS 注册、APNs、消息收发 | 单实例；glibc ≥ 2.39；需要 session bus |
| `imsg` | CLI/D-Bus 客户端（`send`/`lookup`/`status`/`doctor`/`hwconfig`） | 与 daemon 同版本、同镜像 |
| `imessage-gw-svc` | 验签、去重、持久队列、退避重试、5 种推送方言 | 不记录正文；不持有账号凭据 |
| Apprise | 3004 → 网关的转发层（一级 `json://` 目标） | 每键一个 `.cfg`，加文件即生效 |
| Mac | **仅** anisette 供给（设备证明铸造） | `womp=0`：无法远程唤醒 |

---

## 2. 可以直接复用的硬结论（每条都有实测支撑）

| # | 结论 | 证据/说明 |
| --- | --- | --- |
| 1 | `-80045` 的根因是 **anisette 供给方式**，与密码、IP、账号状态、客户端版本都无关 | 密码错误会是 `-20101`；换 mihomo 美国节点仍 `-80045`；账号解锁后仍 `-80045`；上游 `595ce7e` 无新提交 |
| 2 | **真 Mac 铸造的 anisette 头一次通过，且不弹 2FA** | `logged in; connection connected; handles: sender@example.com, other@example.com` |
| 3 | `hwconfig.plist` 是**设备身份**（机型/序列号/MLB/ROM/主板 UUID），**与 Apple ID 无关**，换号可复用 | 换号只需重新 `imsg login`，不必重新生成 |
| 4 | `X-Mme-Client-Info` 的机型必须与 hwconfig 的 `product_name` 一致，否则 `imsg doctor` 报 WARN/FAIL | 实测 `MacModelX,1` / `macOS;14.0;00A000` 两侧一致 |
| 5 | 注册出来的 handle = **账号邮箱**；手机号要成为 handle，需要 iPhone 用**同一账号**启用 iMessage | 本次只有两个邮箱 handle，没有 `tel:` |
| 6 | **稳态不依赖 Mac** | 登录后 25 秒采样，唯一远端是 Apple（`17.138.222.1:443`、`17.57.145.149:5223` APNs、`17.253.87.206:80`、`222.220.212.24:443`）；无 `192.168.1.20:22` |
| 7 | 发给自己账号的 handle 是 **note to self**：`is_from_me=true`、**不回投递回执**、通常不弹通知 | `delivery` 停在 `sent`；发给 `recipient@example.com` / `+1…` 才有 `delivered` |
| 8 | 投递状态 `delivered` 是 **Apple 侧回执**，可作为程序化验收依据（不需要人肉看手机） | 多条实测 `sent → delivered` |
| 9 | 二进制要求 **GLIBC ≤ 2.39**，必须跑在 ubuntu:24.04 一类镜像；**不能**直接跑在 fnOS 宿主（2.36） | 构建机与运行机同发行版最稳 |
| 10 | 资源极小：`imessaged` **11–22 MiB / ~0% CPU**；`imessage-gw-svc` ~18 MiB | `docker stats` + `VmRSS` 实测 |
| 11 | **同一 Apple 身份同时只能有一个 daemon**；两套并存会互相抢注册 | 换栈前必须先停旧栈 |
| 12 | `state.enc` 内含 `sha256(密码)`（GSA SRP 直接吃这个哈希）⇒ **state 目录 = 等价密码** | 目录 0700、文件 0600、不再复制分发 |
| 13 | 唯一的时间炸弹是 **IDS 续期/静默重登录**（本次续期 2026-12-02）；届时 Mac 必须醒着 | `pmset womp=0`，SSH 唤不醒睡眠中的 Wi-Fi Mac |
| 14 | `profiles`（共享名片/头像）报 `-22421` **不影响收发** | 只在需要该功能时才重登录 |

---

## 3. 走过的弯路（按顺序，含"为什么错"）

| # | 尝试 | 现象 | 根因 | 纠正 | 代价 |
| --- | --- | --- | --- | --- | --- |
| 1 | 用 Bark/Hotify 等推送模拟"iMessage" | 不是真 iMessage（只是通知） | 需求本身就是"要真 iMessage" | 放弃 | — |
| 2 | 方案 A：Tuwunel(Matrix) + `ghcr.io/jasonlaguidice/imessage` 桥 | 登录 `-80045` | **尚未找到**（当时误判为设备/账号） | 继续排查 | 半天 |
| 3 | 换出口 IP（mihomo 美国节点，日志确认 `gsa.apple.com:443 … CF-Edge-Relay[USA34]`，出口 `64.186.248.79`） | 仍 `-80045` | 不是 IP 问题 | 排除 IP 维度 | 1 小时 |
| 4 | 等账号解锁（此前触发过账号锁定） | 解锁后仍 `-80045` | 不是账号状态 | 排除账号维度 | 1 小时 |
| 5 | 升级/重试上游镜像、重跑登录 | 仍 `-80045` | 不是版本问题 | 排除版本维度 | 1 小时 |
| 6 | **找到根因**：公开 anisette-v3 服务器（`ani.sidestore.io` 一类）为"自称 macOS"的客户端签发的头被 Apple 拒收 | — | 客户端**声明自己是 Mac**，但**设备证明来自 Android 端模拟器**，两者不匹配 | 必须由真 Mac 铸造 | — |
| 7 | 方案 C-1/C-3：Mac 上 BlueBubbles / osascript 发送 | 需要 Mac 常开 + 辅助功能/自动化权限 + 会话登录 | 与"Mac 基本不开机"的使用习惯冲突 | 放弃 | 半天评估 |
| 8 | 最终方案：`thescruggs/imessage-linux`（rustpush + 本地 NAC 模拟）+ 真 Mac anisette | **登录成功、无 2FA** | — | — | — |

**次生坑（与主线无关，但同样耗时间）**：

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| `cargo` 卡在 `Updating git repository …/android-loader` | git 传输被墙/慢 | `CARGO_NET_GIT_FETCH_WITH_CLI=true` + `CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse` |
| `docker build` 拉 `ubuntu:24.04` 403 | 镜像站限流 | 先 `docker pull` 再 `build` |
| `patch … quinn does not contain packages matching quinn` | 子模块文件被暂存删除 | 对每个子模块 `git reset --hard <记录的 sha>` |
| `Host key verification failed`（子模块用 `git@github.com:`） | 无 GitHub SSH key | `git -c url.https://github.com/.insteadOf=git@github.com: submodule update` |
| rustup「missing manifest in toolchain」 | 安装被中断 | `rustup toolchain uninstall/install stable --profile minimal` |
| 容器内 `mkdir /run/dbus-session: Permission denied` | tmpfs 默认 0755 | `tmpfs: /run:mode=1777` |
| `key file /state/state.key: Permission denied` | 宿主目录属主不是容器 uid | `chown -R 10001:10001 state && chmod 700` |
| `dbus-send --address=` 报 AccessDenied | 必须用 `--session` 且导出 `DBUS_SESSION_BUS_ADDRESS` | 入口脚本里导出环境变量 |
| 上传的 `.sh` 报 `set: -: invalid option` | Windows 侧 CRLF | 上传前统一转 LF |
| 3004 转发中文变 `åå½` | `http.server` 按 latin-1 解析请求行；原始 UTF-8 塞进 query | query 用百分号编码（`curl -G --data-urlencode`），或改放 body |
| iMessage 里同一句出现两遍 | 3004 的 `_forward()` 把首行同时当 title 和 body | 网关侧 `title.endswith(body)` 时只用 title |
| 用 curl 测 `gsa.apple.com` 握手失败 → 误判"网络不通" | Apple 对该端点挑 TLS 指纹 | 以"应用层是否拿到 Apple JSON 错误"为准（rustpush 拿到了 `-80045` 就说明链路通） |

---

## 4. 方法论：遇到"Linux 模拟 macOS 被服务端拒绝"怎么查

**分层排除法**，每层只做**能反驳该层假设的最小实验**，并记录证据：

| 层 | 假设 | 反驳实验 | 本次结论 |
| --- | --- | --- | --- |
| L1 凭据 | 密码错 | 错误码区分（`-20101` vs `-80045`） | 排除 |
| L2 网络/IP | 出口 IP 被风控 | 换住宅/海外节点重试，抓代理日志确认真走了 | 排除 |
| L3 账号 | 账号被锁/受限 | 用同账号在官方客户端登录；解锁后重试 | 排除 |
| L4 客户端 | 版本/协议过旧 | 升到最新 tag，看上游有没有对应 issue/提交 | 排除 |
| L5 **设备证明** | 服务端要求"可信设备"，而我的证明与"自称的设备"不一致 | 看客户端**声明**（`X-Mme-Client-Info` = macOS）与**证明来源**（anisette 服务器返回的 `adi_pb` 由谁铸）是否匹配 | **命中** |

**通用启发**：
- 当客户端"声称自己是谁"和"拿什么证明"来自**两个不同体系**时，服务端一旦开始校验一致性，就会出现"所有配置都对但就是被拒"的现象。
- 这类问题的定位关键是**找到那句声明**（`X-Mme-Client-Info`、User-Agent、`<os;build>` 字段），再问"这条声明有对应的证明吗"。
- **先用最小实验反驳假设，再动手改架构**：本次前 4 层各花 1 小时，正是它们把范围压到了唯一剩下的 L5。

**证据留存规范**（本项目全程遵守）：
- 只记录 **哈希/长度/键名**，绝不打印 OTP、MLB、序列号、密码、token；
- 抓包/采样只记**域名/SNI/端点**，不记正文；
- 生成的 OTP 输出文件用完 `shred`；
- 传输设备凭据（`hwconfig.plist`）时用 `scp` + 两端 sha256 对账 + 用完删副本。

---

## 5. 复用清单：换机器/重建时按这个顺序做

### 5.1 Mac 侧（一次性）
1. `tools/mac-hwinfo.sh -o hwconfig.plist`（`umask 077`，产物 0600）→ 只传 NAS，不留副本；
2. `clang -fobjc-arc -framework Foundation -o ~/.local/bin/imessage-anisette tools/mac-anisette/imessage-anisette.m`；
3. 生成专用 ed25519 密钥（放 NAS 的 `secrets/`，0700/0600），把**受限**公钥写入 Mac `~/.ssh/authorized_keys`：
   `from="<NAS_IP>",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding,command="/Users/<u>/.local/bin/imessage-anisette"`；
4. `ssh-keyscan` 钉扎 `known_hosts`；
5. 验证：`ssh -F ssh_config <alias>` 输出 7 个头、`X-Mme-Client-Info` 机型与 hwconfig 一致。

### 5.2 NAS 侧
6. 构建镜像（ubuntu:24.04 + dbus + ssh + 二进制），`user: 10001:10001`，`read_only` + `cap_drop ALL` + `no-new-privileges` + `tmpfs /run:mode=1777`；
7. 目录：`state/`（0700）、`bus/`（0700，跨容器 session socket）、`apple-data/`（只读）、`gw/`；
8. `imsg hwconfig import` → `imsg doctor`：`hwconfig ok / IMDAppleServices sha1 ok / cert sha256 ok / anisette: command ok`；
9. **单独授权后**：`imsg login --apple-id … --password-stdin`（密码走 stdin，必要时 `--code-stdin` 提 2FA）；
10. `imsg status` 应为 `connected` + `registered` + handle 列表；`imsg lookup <目标>` 确认 iMessage 可达；
11. `imsg send <目标> "测试" --json` → `messages --json` 看 `delivery: delivered`；
12. 接网关：给 Apprise 加 `imessage_<channel>.cfg`（`json://…:8098/notify/imessage_<channel>`），验证 `3004/push/imessage_<channel>`。

### 5.3 验收关口（缺一不可）
`doctor` 全绿 → `lookup` 可达 → `send` 返回 id → `delivery=delivered` → 手机目视收到。

---

## 6. 反模式与红线

| 红线 | 原因 |
| --- | --- |
| ❌ 用主 Apple ID 反复试错登录 | 会触发账号锁定（本次已发生过一次） |
| ❌ 同时跑两个 `imessaged` | 同一身份抢注册，可能把会话弄坏 |
| ❌ 把 `state/`、`hwconfig.plist`、SSH 私钥、`gw.env` 提交到仓库或贴进聊天 | `state.enc` 含等价密码；`hwconfig` 是设备凭据 |
| ❌ 为了修 `profiles`（`-22421`）反复 `imsg login` | 不影响收发，收益极低、风险高 |
| ❌ 用 curl 能否握手来判断"Apple 是否可达" | 该端点挑 TLS 指纹，curl 失败≠不可达 |
| ❌ 往 URL 里塞原始 UTF-8 | `http.server`/多数网关按 latin-1 解析请求行 → 乱码 |
| ❌ 认为"改出口 IP 就能绕过风控" | 本次实证：根因是设备证明，不是 IP |
| ❌ 依赖"Mac 随叫随醒" | `womp=0`，睡眠中无法远程唤醒；只能约定维护窗口 |

---

## 7. 观测与巡检建议

| 指标 | 正常值 | 异常处置 |
| --- | --- | --- |
| `imsg status` → `connection` | `connected` | `needs_login` ⇒ 需 Mac 醒着重登录 |
| `registration.renews_at` | 距今 > 7 天 | 临近续期先确认 Mac 可用 |
| `/readyz` | 200 | 503 ⇒ 检查 `TARGET_HANDLE`/`imsg` 是否就位 |
| 队列 `counts.failed` | 0（偶发 1~2 可接受） | 看 `last_error`；`unknown` 不要盲目重发 |
| 容器 RSS | `imessaged` ≤ 40 MB | 持续增长 ⇒ 检查消息库/附件 |
| 日志字段 | `hook.sms/push.enqueue/send`，号码打码、无正文 | 出现正文 ⇒ 立刻回滚 `REDACT_LOGS` |

**建议的每日自检**：`imsg status --json` 的 `connection` 与 `registration.renews_at`、`/readyz`、`queue.failed`；任一异常再叫 Mac 醒。

---

## 8. 如果只能记三条

1. **`-80045` = 设备证明不匹配**，不是密码/网络/账号；解法是"让声明自己是 Mac 的客户端，用真 Mac 的证明"。
2. **Mac 只是"偶尔的凭据来源"，不是运行依赖**——稳态零依赖，别为它设计常开架构。
3. **一切凭据（hwconfig/state/私钥/gw.env）都在 state 与 secrets 里，等价密码**；能不外传就不外传，必须传就"哈希对账 + 用完即删"。

---

## 9. 证据索引

| 内容 | 位置 |
| --- | --- |
| 立项、三方案加权评分、逐条实测（§9.2a–§9.2m） | `docs/in-progress/2026-10-18-1715-安卓短信转iMessage网关选型评估-推进方案.md` |
| 部署形态与文件清单 | `PROJECT.md`（「维护 iMessage 网关」导航行 + 2026-10-18 各条变更记录） |
| 对外文档（2999 推送文档页） | `/srv/push/apprise/docs/iMessage短信转发通道.md` |
| 网关源码与部署件 | `tools/imessage-gw/`（`gateway.py`、`push-deploy/`、`mac/`） |
| 构建与补丁 | 构建机 `~/il-build/`（固定 tag `5f10c5f` + 4 个补丁，见推进方案 §9.2g） |
