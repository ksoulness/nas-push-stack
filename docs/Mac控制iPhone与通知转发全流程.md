# Mac 控制 iPhone 与短信、App 通知转发全流程

> 更新：2026-11-17。本文记录一次自有设备实测与现有推送栈的接线方法；版本号按当时设备与工具回报记录，不作为所有机型的最低版本或长期兼容保证。
> 公开仓库：[ksoulness/nas-push-stack](https://github.com/ksoulness/nas-push-stack)。正文中的域名、令牌、号码和群 ID 均使用占位符。

## 1. 最终实现与当前边界

控制手机和日常转发是两条链：

```text
配置维护：Windows ─SSH→ Mac ─USB / iphone-control→ iPhone
日常转发：iPhone 通知自动化
          → 文本（通知.App + 通知.正文）
          → URL 编码
          → HTTPS /p/<MOBILE_TOKEN>/ios2and?body=<编码结果>
          → 翻译入口 → Apprise
                       ├→ Hotify / Gotify WebSocket → 安卓 ACE
                       └→ 飞书出站腿 → 短信群
```

手机配置完成后，转发由手机自身运行，不依赖 Mac 保持开机或 USB 在线。手机仍需网络、有效的自动化与相应通知权限。

已取得的历史实测证据：锁屏时真实短信通知触发 HTTP 请求，Apprise 投递到安卓与飞书；接入正文编码后，处理日志正文长度从默认文案的 23 变成 67，飞书回读出现完整短信正文。这里的长度是 Python 字符数，不是网络字节数。手动运行只能证明 HTTP 动作可用，真实通知才证明触发器与变量可用。

当前覆盖边界：曾确认触发器选中了约 18 个 App；后续尚无完整逐项复核记录，剩余 App 批量补选未完成。服务端名称字典曾含 123 个 App 名，这只是解析字典，**不代表手机选中了 123 个 App**。本手册发布不等于完成所有 App 的触发验收。

最近一次 Mac 检查曾出现 `准备手机失败，稍后重试：读界面失败：超时：连接无障碍服务（8 秒）`，因此不把手机控制通道写为当前持续就绪。该故障与已经在手机上保存的转发自动化分别排查。

## 2. 版本与设备条件

| 项目 | 本次记录 | 适用边界 |
| --- | --- | --- |
| Mac | Intel x86_64，macOS 15.7.7 / 24G720 | 这是已使用环境，其他系统和架构须按工具对应版本验证 |
| 手机 | iPhone14,6，即 iPhone SE 3；升级前回报 26.6.2，升级后回报 27.2.1 | 不据此推导所有机型的支持列表 |
| 控制工具 | [Derrors/iphone-control](https://github.com/Derrors/iphone-control)，0.2.7 | 使用前读该版本 README 与 `--help` |
| 原生控制 | 升级后工具回报 `Control: native`，AX 读界面、HID 触摸与按键 | 这是工具在该设备上的能力报告 |
| WDA 控制 | 升级前通过免费开发者账号签名安装 WebDriverAgent | 本次描述文件有效期约 7 天，需到期重签 |
| 通知触发器 | 升级前在触发列表搜索“通知/收到”无结果；升级后出现通知触发器 | 优先在目标设备上检查实际 UI，再决定是否升级 |

手机需通过 USB 信任电脑；本次开启了开发者模式并按提示重启、确认。Mac 和 iPhone 使用不同 Apple ID 也完成过控制，本方案不依赖 Apple 官方 iPhone 镜像的同账号链路。

## 3. 从 Windows SSH 进入 Mac

先在 Mac 的系统设置开启“远程登录”，用管理账户配置 SSH 公钥。以下配置中的地址、用户名与私钥由自己替换：

```sshconfig
Host mac
    HostName <MAC_LAN_IP>
    User <MAC_USER>
    IdentityFile ~/.ssh/MAC
    IdentitiesOnly yes
```

```bash
ssh mac
# 在 Mac 上核验设备和工具
hostname
id -un
sw_vers
uname -m
$HOME/.local/bin/iphone-control --version
```

休眠可能导致连接超时，先检查唤醒和网络。SSH 的非交互 PATH 可能没有自装目录，命令使用 `$HOME/.local/bin/iphone-control` 等绝对路径。macOS 图形应用还受当前控制台用户影响，可用 `stat -f %Su /dev/console` 检查，别把后台账户看不到 GUI 误判为应用未安装。

## 4. Mac 控制 iPhone

### 4.1 安装与发现设备

本次 Intel Mac 从源码构建 iphone-control；Apple Silicon 可使用上游对应版本的发布包。源码构建需 Rust/Cargo 和该版本要求的构建工具，标签应先在上游发布页核实。

```bash
# 以下在 Mac 执行；进入已核对版本的源码目录
cargo build --release
mkdir -p "$HOME/.local/bin"
install -m 755 target/release/iphone-control "$HOME/.local/bin/iphone-control"
$HOME/.local/bin/iphone-control --version
$HOME/.local/bin/iphone-control devices
$HOME/.local/bin/iphone-control doctor
```

本次已核实的客户端位置是 `/tmp/mcpc.py`、批量补选脚本 `/tmp/addapps.py`、旧 WDA 客户端 `/tmp/w.py`。这些是临时目录文件，不保证重启后保留；没有核实它们已安装在 `~/.local/bin/`。本仓库同时提供可移植的 [MCP 命令行客户端](https://github.com/ksoulness/nas-push-stack/blob/main/extras/iphone/mcpc.py)，可显式安装后使用下文命令。

### 4.2 启动控制守护进程

手机插线、信任电脑、保持解锁。先检查当前状态；仅在进程未运行时启动，避免叠加多个 daemon。

```bash
$HOME/.local/bin/iphone-control status
# 确认未运行后，在 Mac 启动
nohup "$HOME/.local/bin/iphone-control" daemon >/tmp/ictl-daemon.log 2>&1 </dev/null &
$HOME/.local/bin/iphone-control doctor
```

本次 Apple 登录/签名曾遇到 GSA lookup 返回 HTML 503，工具随之报 `ExpectedEndOfEventStream`；经当时的 NAS HTTP 代理才返回 XML plist。这个结果是当时网络现象，不是所有网络都必须用代理。确有同类故障时，将自己的 `HTTP_PROXY`、`HTTPS_PROXY` 设置到启动 daemon 的进程环境；只设置客户端环境而 daemon 仍旧无代理，故障会保留。代理和 anisette 地址按自己的可用服务配置。

锁屏且未配置自动解锁时，工具曾返回：

```text
The phone is locked and no passcode is configured; unlock it by hand
```

维护时先人工解锁，**不要为测试发送 lock 按键**。AX 连接超时也可能来自配对、系统服务或版本兼容，不能仅凭超时就认定锁屏；依次检查设备状态、USB 信任、daemon 日志，再决定是否重启控制进程。

### 4.3 MCP 远程命令

在控制端仓库根目录将客户端放到 Mac 明确的位置：

```bash
scp extras/iphone/mcpc.py mac:/tmp/mcpc.py
ssh mac 'python3 /tmp/mcpc.py tools'
ssh mac 'python3 /tmp/mcpc.py call iphone_status "{}"'
ssh mac 'python3 /tmp/mcpc.py els 120'
ssh mac 'python3 /tmp/mcpc.py app com.apple.shortcuts'
ssh mac 'python3 /tmp/mcpc.py shot /tmp/iphone.jpg'
scp mac:/tmp/iphone.jpg .
ssh mac 'python3 /tmp/mcpc.py tap 188 310'
ssh mac 'python3 /tmp/mcpc.py tapt 自动化'
ssh mac 'python3 /tmp/mcpc.py call iphone_swipe "{\"from_x\":188,\"from_y\":520,\"to_x\":188,\"to_y\":180}"'
```

这些是 Bash/类 Unix shell 引号示例，其他 shell 按其规则转义。客户端使用标准库，与 `iphone-control mcp` 交换每行一个 JSON 的 stdio 消息；用 `IPHONE_CONTROL_BIN` 环境变量覆盖可执行文件位置。先用 `tools` 和上游 schema 核对参数，工具列表会随版本变化。

本次原生截图与坐标为 375×667 逻辑点。旧 WDA 截图曾是 750×1334 像素而点击仍用点，须按该设备缩放换算。始终以当前截图和控件树定位，不照抄历史坐标。通知内容、截图和控件树可能含验证码，留在自己的维护环境。

### 4.4 升级前使用过的 WDA 路线

本次旧系统依赖 `iphone-control login` → `sign` → WDA，曾成功签发安装。免费描述文件到期需重签；实际天数读工具输出。升级后 native 已使用成功，WDA 不再是本次原生控制的必需依赖，也无需为这条路线安装 Xcode 模拟器平台。其他版本的要求以工具诊断为准。

## 5. 推送容器、入口与方向键

| 组件 | 本次 NAS 位置 | 职责 |
| --- | --- | --- |
| `push-translator` systemd | 推送 3004、文档 3020、过滤 8003、继电 3005、飞书 3806 | 运行 `universal-push-proxy.py`，处理手机入口及排版 |
| `apprise` 容器 | 8000；配置目录 `apprise/config/` | 按 key 将同一条消息投递至多条腿 |
| `hotify` 容器 | 8002→8443 | 本次安卓 Gotify/WS 接收端使用的服务 |
| `bark-server` 容器 | 8082→8080 | Bark 后端；与 Hotify 区分 |
| 飞书出站腿 | 翻译进程的 3806 口 | 用自建应用机器人调用飞书消息 API |
| 2999 控制台 | `/doc` 文档页 | 索引并展示维护手册 |

云端副本的历史记录为 `push-leg-hw`（推送 3105、继电 3107、飞书 3108），nginx 3104 将 `/gotify/` 分至 8002，其余转至 3105；公网域名经 Cloudflare Tunnel 接入。**本轮重新核对的是 NAS，未重新核验云端副本状态，也没有确认自动故障切换，勿称为已验收的双活。**

本仓库根 `docker-compose.yml` 包含 Apprise、Hotify、Bark 和 iMessage 相关组件，**未打包目前生产的 `push-translator`/`/p/` 手机入口**；Hotify 镜像名也是需替换的本地镜像占位。克隆后启动 Compose 不会自动得到本文全部功能。本文用于已有翻译入口的接线与维护；全新部署需先实现或补齐该入口、飞书出站腿、HTTPS 和对应 key，再配置手机。[原推送栈说明](https://github.com/ksoulness/nas-push-stack/blob/main/docs/upstream/notification-system.md)可作为背景，勿把旧快照当作当前生产源码。

| key | 本次配置的投递方向 | 作用 |
| --- | --- | --- |
| `ios2and` | 安卓 ACE + 飞书短信群 | iPhone 通知入口，避免回投 iPhone |
| `and2ios` | iPad Bark | 安卓通知送往 iOS 端 |
| `sms` | 安卓 ACE + Bark + 飞书短信群 | 外部短信模块通知 |
| `ntf` | Bark + 安卓 ACE + 飞书通知中心 | 广播，需自行检查循环路径 |

循环取决于完整路由：如监听 iPhone Bark，又将该消息投回同一个 Bark，会再次触发。当前 `ios2and` 没有 Bark 腿；增改方向键时重新核对源 App、出口和其他设备的二次转发规则。

曾为 Gotify 登录制作 nginx `/session`、`/client` 静态兼容响应并接受任意 Basic 头，再注入固定设备 token。这只是历史兼容方案，**不是有效的用户名/密码认证，会扩大消息读取权限**。公开文档不附这份生产配置，不把“任意用户名密码”作为公网接入教程。公网接收端应使用真实账号鉴权或受限网络入口。

## 6. 手机入口契约与服务端解析

```text
GET  https://<PUSH_HOST>/p/<MOBILE_TOKEN>/ios2and?body=<URL_ENCODED_TEXT>
POST https://<PUSH_HOST>/p/<MOBILE_TOKEN>/ios2and
```

POST 可用 JSON `{"title":"测试","body":"内容"}`、表单 `body=...` 或原始文本；当前生产版本也保留 JSON 解析失败时的原文回退。手机 GET 方案已经实测，POST 是服务端支持的另一种接线方式。

令牌优先从环境 `MOBILE_PUSH_TOKEN` 读取，否则从 `MOBILE_TOKEN_FILE` 或 NAS `apprise/mobile-push-token`、云端 `/opt/push/mobile-push-token` 读取；文件内容缓存 60 秒。令牌匹配后按 key 投递，默认每 key 每分钟限速 120，错误令牌返回 403，超速 429；缺正文且无默认文案返回 400。

当前生产解析顺序：

1. 路径中的 key 先 URL 解码，再剥非 ASCII 垃圾，处理曾发生的中文输入法误插字；最终仍应修正手机 URL。
2. 读取 `body/content` 查询参数或请求体；可单独传 `title`。
3. 正文若以 `[发送方]` 开头，提取为标题；若以已知 App 名开头，最长前缀匹配剥离，再识别发送方。
4. `信息/Messages/短信` 默认别名为 `SMS`。切正文按原名长度，避免两字“信息”被三字别名 SMS 多截一个字符。
5. 没有标题的普通 App 用正文前 40 字符作标题，正文仍保留全文；正文为空则尝试默认文案文件。
6. `_forward()` 交给 Apprise；飞书 `_feishu_text()` 排列频道标签、标题、正文、可选尾注与来源机器。

`mobile-apps.txt` 一行一个 App 显示名，缓存 60 秒；它只帮助服务端识别来源。新 App 未入字典时可能无法正确拆标题，手机未选中 App 时则根本没有请求。这两种故障分别处理。

飞书配置使用 `FEISHU_CHAT_ROUTES` 选群、`FEISHU_CHANNEL_LABEL` 定标签、`FEISHU_META_CHANNELS` 决定哪些频道显示尾注。尾注启用后读取服务器时间；若有 `mobile-recv-number` 文件且为 SMS，可加“收件 <RECEIVER_NUMBER>”；其他 App 省略号码。该号码来自静态配置，不是自动识别的双卡收件号码。

```text
[转发自iPhone] （SMS）[EXAMPLE]
示例短信正文，验证码 <CODE>
（<SERVER_TIME> · 收件 <RECEIVER_NUMBER>）
（<SOURCE_HOST>）
```

GET 将令牌和正文放进 URL，反代访问日志可能记录完整查询串，URL 编码并非加密。生产日志应限制访问并按需脱敏；公开资料只放占位符。非常长的通知还会遇到 URL 长度限制，需另测 POST 接线及上游限制。

## 7. iPhone 自动化的具体配置

### 7.1 建立通知触发器

本次升级后的入口在“资料库 → 自动化”；新建时可能先出现“新建快捷指令”，再在动作分类选“自动化 → 通知”。不同界面不要固定假设右上一定有 `+`。

选择“我收到来自 [App] 的通知时”，逐个加入信息、微信、邮件及其他需要的 App。界面未找到“所有 App”通配项。不要留下未配置的标题筛选条件；如有“立即运行/运行前确认”，选立即运行并关闭确认。本次旧系统升级后的自动化曾失效，重新创建后才触发成功，因此升级后必须实际发一条新通知验证。

### 7.2 文本动作：App + 正文

在触发器下增加“文本”，依次放两个通知变量：第一个属性选 App，第二个选正文，中间不加分隔符，匹配当前生产解析方式。

```text
[通知.App][通知.正文]
# 中括号表示变量胶囊，不是要手打的字符
# 示例输出：信息[EXAMPLE] Your verification code is <CODE>.
```

本次“选择变量”面板一直空白，实际可行的做法是聚焦输入框后点画布上游动作的变量胶囊，再打开变量属性选 App/正文。这是该设备的 UI 问题记录，不代表所有设备只支持这种手势。若第三方 App 的通知不暴露 App 属性或正文，应分别验证并调整来源标识，不假定所有通知属性都完整。

### 7.3 URL 编码动作

增加“URL 编码”，输入是上一项“文本”，模式是编码。曾直接拼接含空格的正文只收到 `[CTExcel]`，长度 9；加编码后正文打通。只编码内容变量一次，避免将整个 URL 编码或双重编码。

### 7.4 获取网页内容动作

增加用于 HTTP 请求的“获取网页内容”（对应 Get Contents of URL），确认展开后有方法、头部等选项。粘贴基础地址，再把光标放末尾插入“URL 编码后的文本”变量：

```text
https://<PUSH_HOST>/p/<MOBILE_TOKEN>/ios2and?body=[URL 编码后的文本]
```

方法 GET；保留完整且干净的路径，不附空格、日期或中文输入法候选字。MCP 输入长 URL 本次曾吞字，稳定做法是在手机上粘贴完整基础 URL，然后远程插变量。不要把卡片自动填入的“文本”变量直接当成完整 URL。

### 7.5 保存与权限

编辑器“简介/详细信息 → 隐私”检查锁定时允许运行；首次运行完成域名网络授权；返回列表确认自动化开关打开。手动播放可能没有通知输入，收到默认文案属于服务端兜底，并非正文验收。

## 8. 验收、定位与维护

按依赖分层验证：

1. 自己的 HTTP 客户端向入口提交明确测试正文，核对状态与最终接收端；这一步证明服务端链路。
2. 手机手动运行，完成网络权限，确认请求抵达；这一项还没验证触发器。
3. 通过已有来源产生一条新的真实通知，核对手机 App 被选中、服务端 `[mobile]` 记录、Apprise 结果与接收端全文一致。
4. 在手机自然锁屏后重复第 3 项；不要使用维护脚本主动锁屏，以免把控制通道锁住。

历史链路曾先出现 `HEAD ... 501`，约两秒后 GET 200 并成功投递；单独看到 HEAD 501 不应认定整次转发失败。HTTP 200/Apprise 成功也不等于用户已经看到通知，最终验收读回安卓或飞书实际正文。

| 现象 | 优先检查 |
| --- | --- |
| 手机有短信，服务端无请求 | 该 App 是否已选、自动化是否启用、是否真的产生系统通知、网络与权限 |
| 前台使用 App 时未转发 | App 可能没有产生相应通知事件；通知触发器不是短信数据库轮询 |
| 锁屏仅有隐藏预览 | 通知变量是否含正文需单独实测；隐藏内容不保证可读取 |
| 只收到默认文案 | URL 末尾是否有变量、文本是否来自真实触发输入 |
| 正文到第一个空格就没了 | URL 编码动作及最终变量连接 |
| 标题少 `[` 或首字 | App 别名切片是否按原名长度，解析字典是否匹配 |
| 新 App 没有来源标签 | 手机触发器覆盖与服务端名称字典分别检查 |
| AX 超时 | 手机解锁、USB 配对、daemon 与系统 AX 服务日志 |

本方案依赖 iOS 实际产生并暴露的通知事件。前台不弹通知、摘要/专注延迟、隐藏预览、断网、系统后台调度等情况都要在实际设备上验证。当前记录没有覆盖离线补发队列或持续长测，不承诺每条通知均被完整转发。

## 9. “关定位10秒”捷径的实际动作

这是独立的半自动辅助捷径，与通知转发无关。已建动作是：

1. 飞行模式打开。
2. 打开 `prefs:root=Privacy&path=LOCATION`，尝试跳至定位设置页。
3. 等待 10 秒。
4. 飞行模式关闭。
5. 显示“定位还关着，记得开回来”。

**定位总开关仍需手动关闭、手动恢复**。10 秒从等待动作开始算，不保证定位实际关闭满 10 秒；提示也不检测当前定位状态。私有设置 URL 在其他版本可能跳转不同或失效。飞行模式可能保留 Wi-Fi 状态，也不会等价关闭 GPS 或保证重置定位，因此不要将此捷径称作全自动定位开关或 GPS 重置。

## 10. 本次部署与证据范围

2026-11-17 文档整理时重新检查 NAS：`push-translator` 为 active，Apprise、Hotify、Bark 容器在运行；核对了手机入口与飞书排版当前源码。没有更改生产服务、手机自动化或公网认证，也没有以文档发布替代手机全 App 覆盖验收。

原始参考：[全设备通知推送](https://xn--o1r25w.cn/%E5%85%A8%E8%AE%BE%E5%A4%87%E9%80%9A%E7%9F%A5%E6%8E%A8%E9%80%81/)。控制工具机制参照 [iphone-control 上游](https://github.com/Derrors/iphone-control)，具体兼容性以目标版本说明与设备诊断为准。
