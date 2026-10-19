# 通知系统运行手册

> 建立时间：2026-08-21 · **终态重写：2026-09-09 22:15** · **更新：2026-09-10 02:00**（端口拆分 + 文档标签页 + 成对双投修复）· **更新：2026-09-10 09:50**（H6 渠道分离：一键=一渠道，全部定向）· **更新：2026-10-09 19:30**（hideck 短信通知正文行序重排：内容置顶 → §19）· **更新：2026-10-19 15:40**（新增飞书出站腿：12 频道 × 飞书群「通知中心」 → §20）
> 涉及服务：Hotify、Apprise、3004 翻译器（2999 展示端口、3806 飞书出站端口）、push-bridge、Home Assistant、Telegram、Discord、飞书（小鲸应用）
> 退役服务（回滚锚点见 §10）：Gotify 8081、Bark 8082、MagicPush 桥 8001、App Center hotify

---

## 1. 架构总览（终态）

```
发送端
  ├─ 公式一: POST :8000/notify/{key}          （直推 apprise）
  ├─ 公式二: :3004 翻译器（方言/MagicPush/GET/notify，自动加 [key] 前缀）★推荐
  │          3004=公网推送端口（仅推送路由，页面一律 404）；2999=内网展示端口（矩阵+文档）
  └─ 公式三: POST :8002/message?token=<key1>  （直连 Hotify，gotify 兼容）
       │
       ▼
apprise :8000（键路由，**H7 后 21 键全部定向**：gotify_X→安卓 ACE、bark_X→iPad（X 分组）、telegram_X→TG topic、discord_X→DC 频道；textverified 保留广播=一键测链路，**仅到 iPad+安卓**（桥无广播路由，TG/DC 不收）；HA 无专用键，直接引用 device 行四渠道）
       │
       ▼
hotify :8002（统一中枢 v1.4.2；广播=全部注册设备但双桥不转发，**实际广播仅到 iPad+安卓**；定向 /<slug或uuid>/=单设备）
  ├─ ACE（Android gotify App，uuid 73f9…，slug gotify）
  ├─ ipad（Bark App，slug brak，uuid 1e45…1f03 ← 2026-09-10 14:23 接管，APNs token 已换新 feb2…5baf）
  ├─ Telegram 桥设备（slug tgram，WS）──► push-bridge ──► TG：topic 20264/7/2 + General
  └─ Discord 桥设备（slug discord，WS）─► push-bridge ──► Discord：nas/device/btc 频道 webhook
                                         （TG/Discord 出站经 mihomo :7890 代理）

Home Assistant（4 开关，H7 起直接引用 device 行四渠道，地址定死）：
  script.unified_notify → apprise_notify.send（无 channels=按开关过滤）
     ├─ 开关"安卓手机" on → gotify_device  → 定向 ACE
     ├─ 开关"iPad" on    → bark_device    → 定向 ipad（group=device sound=chime）
     ├─ 开关"Telegram" on → telegram_device → 定向 tgram 桥 → TG topic 7
     └─ 开关"Discord" on → discord_device  → 定向 discord 桥 → #device
   集成出口 :3004/notify/{key}（带 [key] 前缀，不再直连 apprise）

频道路由矩阵 + 推送文档（实时）：http://192.168.1.10:2999/ ← 展示端口：矩阵/文档双标签，点格子看推送地址
```

**设计要点**：
- 单一枢纽 Hotify：所有业务键的 apprise 腿都指向它；设备页四席（含 TG/Discord 占位设备）
- 3004 前缀（2026-09-09 22:35）：经 3004 的消息标题自动加 `[key]`——Hotify 控制台可见来源渠道；空标题取 body 首行
- 桥路由按标题 `[key]` 前缀匹配：telegram_nas/btc/device/general 四键→TG topic，discord_nas/btc/device 三键→DC 频道（共 7 路），其余只到 Hotify 设备
- Hotify 定向推送（2026-09-09 23:04 验证）：`POST/GET :8002/<uuid或slug>/<title>/<body>` 或 json:// 腿 path=slug——只投递该设备；**WS 按设备投递**（桥只看到广播+发给自己的，定向给 ACE 的消息桥收不到）
- HA 四开关（H4，23:35）：每开关一个定向键，单开关只响单端、互不串台；组件 send 不带 channels 时自动按开关过滤

## 2. 关键端口与进程

| 服务 | 端口/形态 | 健康检查 | 进程 |
|-----|----------|---------|------|
| Hotify | :8002 容器 | `curl :8002/ping` → pong | docker `hotify` |
| Apprise API | :8000 容器 | `curl :8000/status` → OK | docker `apprise` |
| 3004 翻译器 | :3004 推送（公网映射）+ :2999 展示（仅内网）+ :8003 安卓过滤代理 + :3005 apprise→Hotify 正文重映射中继 + :3806 飞书出站 同进程 | `curl :3004/health` / `curl :2999/health` / `curl :8003/health` / `curl :3005/health` / `curl :3806/health` → ok | `push-translator.service`（ExecStart 五端口 `3004 2999 8003 3005 3806`） |
| push-bridge | systemd 无端口 | `systemctl status push-bridge` | `push-bridge.service` |
| mihomo 代理 | :7890 容器 | 桥日志无连接错误即可 | docker `mihomo` |

- 容器编排：`/srv/push/docker-compose.yml`（apprise/hotify；mihomo 独立）
- systemd 单元：`/etc/systemd/system/push-translator.service`、`push-bridge.service`（均 enabled，User=nasuser）

## 3. 推送怎么发（日常操作）

**首选公式二（3004，带前缀）**：

```bash
# 方言：wecom/dingtalk/feishu/pushplus
curl -X POST http://192.168.1.10:3004/feishu/gotify_nas -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"标题\n正文"}}'
# MagicPush：POST :3004/api/push，Authorization: Bearer <key>，{"title","content"}
# 极简 GET：curl ":3004/push/gotify_btc?title=标题&body=正文"
# apprise 兼容（HA 等集成出口）：POST :3004/notify/{key}，form title/body 或 JSON
```

公式一 `POST :8000/notify/{key}`、公式三 `POST :8002/message?token=<key1>`（body 字段名 message）亦可，无前缀。完整变量表、方言明细、推导演算 → `docs/configs/apprise-configs.md`（权威索引）。

**频道路由矩阵 + 推送文档 + 消息历史页**：`http://192.168.1.10:2999/`（展示端口，仅内网）。标签页切换「推送矩阵 / 推送文档 / 消息历史」：矩阵=频道×设备表格，点格子看推送地址（TG/Discord 直推 webhook URL 已掩码，数据实时读 cfg+桥配置）；文档=本手册/配置索引/推进方案三份 md（实时读 NAS `…/apprise/docs/`）；消息历史=Hotify 最近 30 条消息（`/messages.json` 服务端带 key1 代理 Hotify API——key 不落浏览器，**时间倒序最新在顶**），每条解析 `[key]` 前缀并实时标注投递去向（广播=安卓+iPad、定向=单设备、TG topic、Discord 频道、未知键/直推旧消息）。弹窗居中显示，✕/Esc 关闭。推送端口 3004 上页面一律 404——公网映射只暴露推送路由。

**预期效果**：推 `gotify_nas` → 仅安卓 ACE 收到（标题+正文），Hotify 控制台显示 `[gotify_nas] 标题`；要 iPad/TG/DC 同步就同场推 `bark_nas` / `telegram_nas` / `discord_nas`（H6 渠道分离语义：一键=一渠道）。

## 4. 凭据与路由

| 凭据 | 值 | 存储位置 |
|-----|---|---------|
| Hotify key1 | `<hotify-key1>` | 桥 config.json、apprise cfg 腿 |
| TG Bot | `1234567890:AAH0…`（nasxinxitzbot） | `/srv/push/bridge/config.json`（0600） |
| TG Chat | `-1001234567890`（topics：nas=20264 / device=7 / btc=2 / general=无） | 同上 |
| Discord webhook | nas …4194 / device …9347 / btc …2917（token 全值在桥 config） | 同上 |
| 21 个 apprise 键 | 见 apprise-configs.md 变量表 | `…/apprise/config/*.cfg` |
| HA | apprise_notify 出口 :3004 + 4 开关引用 device 行四渠道键 | `/srv/docker/homeassistant/configuration.yaml` |

**桥设备**（Hotify 设备页）：Telegram（uuid 20f6faa7-…，slug **tgram**，platform=windows 占位）+ Discord（uuid 6e88028d-…，slug **discord**）。注册每次启动幂等刷新。

**桥路由表**（config.json `routes`，H6/H7 后共 7 路）：
- `telegram_nas` → TG topic 20264；`telegram_btc` → topic 2；`telegram_device` → topic 7；`telegram_general` → General（无 topic）
- `discord_nas` → Discord nas；`discord_device` → device；`discord_btc` → btc
- 其余 14 键（gotify_*/bark_*/textverified）无 TG/Discord 路由（只到 Hotify 设备）

**`sms` 键（2026-09-27 新增，双腿复合频道，用于 hideck 短信通知）**：cfg = `apprise/config/sms.cfg`，腿1 同 `gotify_device`（→安卓 ACE），腿2 `bark -group=sms -sound=bell`（→iPad 独立分组）。**入口**：hideck 通知设置里的「企业微信」渠道 → `POST :3004/wecom/sms`（模板 `{"msgtype":"text","text":{"content":{{message}}}}`）；hideck 校验响应 `errcode=0`，与翻译器返回一致。实测 2026-09-27 两轮自检：apprise `KEY: sms`、消息历史各两条（安卓+iPad）。**注意**：hideck 的 `PUT /api/settings/notifications` 按默认值合并（未提交字段会被重置，改完需 `docker compose restart hideck`）。**正文行序**：「内容」提到最上面的重排由推送代理的 `/wecom` 分支完成（§19），面板模板保持原样、hideck 无需重启。

**HA 四渠道引用**（H7 起无专用键，地址定死、直接引用 device 行）：
- `gotify_device` → json://:8002/<ACE uuid>（定向安卓；slug "gotify" 有路径歧义 404，用 uuid）
- `bark_device` → json://:8002/brak?-group=device&-sound=chime（定向 iPad）
- `telegram_device` → json://:8002/tgram + 桥路由 → TG topic 7
- `discord_device` → json://:8002/discord + 桥路由 → Discord #device

## 5. Home Assistant 通知链（H4：四开关定向）

- `script.unified_notify`（title/message 参数）→ `apprise_notify.send`（**不带 channels**——组件遍历 channels 表，按对应 input_boolean 开关 on/off 过滤后逐键发送）
- 出口 URL `:3004/notify/{key}`（apprise 兼容路由，自动加 [key] 前缀；原直连 :8000 已切走，python3.14 直推发送端即此集成，迁移完成）
- 键映射（H7 起）：gotify→`gotify_device`（定向 ACE）、bark→`bark_device`（定向 iPad）、telegram→`telegram_device`（→TG topic 7）、discord→`discord_device`（→#device）；ha_* 专用键已退役删除。**configuration.yaml 已切换（2026-09-10 11:56）并实测**：webhook 驱动全开（4 端齐到：ACE uuid/brak+APNs/TG topic7/#device）+ 逐开（4 轮各恰好 1 条零串台），开关恢复全开，测试自动化已删除
- 四开关 `input_boolean.apprise_notify_{gotify,bark,telegram,discord}`（通知-安卓手机/通知-iPad/通知-Telegram/通知-Discord）；旧 dashboard 卡片实体名未变、自动复活
- 调用方（自动化 `auto_close_ev_bike_0800`、`battery_full`）传 title/message，接口未变
- 日志排错：`docker logs home-assistant --since 10m | grep -i apprise`；改造备份 `configuration.yaml.bak-h4-20260909`、`scripts.yaml.bak-h4-20260909`（目录 `/srv/docker/homeassistant/`）

## 6. 桥（push-bridge）运维

```bash
systemctl status push-bridge            # 状态
journalctl -u push-bridge -f            # 实时：sent/FAILED/error per 消息
sudo systemctl restart push-bridge      # 重启（幂等：重新注册+重连，since 增量补漏）
```

- 源码：NAS `/srv/push/bridge/hotify-bridge.py`（本地 `tools\hotify-bridge.py`）；配置 `bridge/config.json`
- 断线重连 5s 退避；重连后 `since=<last hlc>` 补漏，不丢消息
- 桥停摆只影响 TG/Discord 扇出（手机/iPad 不受影响）；重启后 5 分钟内消息窗口自动补发
- **首次连接历史重放**：服务端无 since 会重放全量历史——桥已按 ts>now-300s 过滤防轰炸；改桥代码时保持该逻辑

## 7. 备份与恢复

| 对象 | 备份 | 恢复 |
|-----|------|------|
| Hotify 整库 | `curl -H "Authorization: Bearer <key1>" :8002/api/v1/backup -o backup.db`（不停机） | 拷回数据目录 |
| apprise cfg | `…/backups/apprise-config-pre-f-merge-2026-09-09.tar.gz`（c1/d2 期另存） | 解压回 config/ |
| 多腿期 cfg（桥改造前） | `…/apprise/backups/retired-legs-bridge-20260909/` | 见 §10 回滚 |
| 旧 discord/telegram 键 | `…/apprise/backups/retired-legs-20260909/` | 归档 |
| HA 配置 | `configuration.yaml.bak-h4-20260909`、`scripts.yaml.bak-h4-20260909`（homeassistant/ 目录内）；H1 单键期另有 `ha-config-pre-notify-update-2026-09-09.tar.gz` | 解压/拷回 homeassistant/ 重启 HA |
| 桥 config | `…/bridge/config.json`（0600） | 手工拷贝 |

建议 cron 拉取 Hotify 备份（curl 一条即可，见 apprise-configs.md）。

## 8. 监控与告警自检

```bash
# 全链自检：推四条 nas 键，四端应各收一条
curl ":3004/push/gotify_nas?title=自检&body=$(date +%F_%T)"
curl ":3004/push/bark_nas?title=自检&body=$(date +%F_%T)"
curl ":3004/push/telegram_nas?title=自检&body=$(date +%F_%T)"
curl ":3004/push/discord_nas?title=自检&body=$(date +%F_%T)"
# 判定：安卓+iPad 各 1 条（标题+正文）；TG topic 20264 1 条；Discord nas 频道 1 条
# Hotify 控制台 4 条 [键名] 自检；正文应完整非空（H9 修复后）
# 桥日志应有 sent telegram_nas/discord_nas 两行
journalctl -u push-bridge --since "1 min ago" | grep sent

# 单服务：
curl -s :8000/status && curl -s :3004/health && curl -s :2999/health && curl -s :8002/ping
systemctl is-active push-translator push-bridge
```

14 键全量回归脚本 → apprise-configs.md 附录 A。

## 9. 排错速查

| 症状 | 原因 | 处置 |
|-----|------|-----|
| 手机收到 TG/Discord 没收到 | 桥停/路由缺/mihomo 挂 | `systemctl status push-bridge`；journalctl 看 FAILED；`docker ps` 看 mihomo |
| Discord 报 error 1010 | UA 被 Cloudflare 拦 | 桥内置浏览器 UA 已修；自写脚本必须带 User-Agent |
| TG 报 thread not found | topic_id 不对 | 对照群里实际 topic；改 config.json 后 restart 桥 |
| 消息无 [key] 前缀 | 发送端没走 3004 | 改公式二；或接受无前缀 |
| 3004 401 | /api/push 缺 Bearer | `Authorization: Bearer <key>` |
| notify 404 | key 写错/不存在 | 对照变量表；cfg 热加载但 key 要先建 |
| Hotify 日志刷 unknown platform windows | 桥占位设备推送通道 | 预期噪音，忽略 |
| HA 不发通知 | 开关全 off / 集成报错 | 逐个检查四开关；`docker logs home-assistant --since 10m \| grep -i apprise`；出口应是 :3004 |
| HA 某端不到 | 对应开关 off 或渠道键错 | 对照 §5 键映射；curl `:3004/notify/gotify_device` 等手测该键 |
| 定向推送 404 device not registered | slug 写错/未设 | `PUT :8002/api/v1/devices/<uuid>/slug {"slug":"x"}`；注意 slug "gotify" 路径歧义 404，ACE 用 uuid |
| gotify/bark 旧地址 404/拒绝 | 服务已退役 | 改公式一/二/三；别再发 8081/8082/8001 |
| 桥重启后旧消息重发 | ts 过滤窗口内(5min) | 预期补漏行为；如误发检查 state.json 的 hlc |
| 矩阵页某列 ✗ | 键/路由未建 | bark/gotify 列=建对应 cfg；TG/DC 列=桥 config.json routes 加键后 restart 桥 |
| 矩阵/文档页 404 | 走了 3004（推送端口无页面） | 用 `http://192.168.1.10:2999/`（展示端口） |
| hideck 短信通知正文行序不对 | 行序由代理常量 `_HIDECK_LINE_ORDER` 决定（§19） | 改常量 → 跑 `python tools\hideck-notify-order-test.py` → 按 §4.3 部署 + `sudo systemctl restart push-translator` |

## 10. 回滚锚点（退役服务清单）

| 退役服务 | 端口 | 数据/备份 | 恢复方式 |
|---------|------|----------|---------|
| Gotify | 8081 | 容器已删，数据目录留 | 恢复 compose 片段（plan doc D1） |
| Bark server | 8082 | 同上 | 同上（plan doc D2） |
| MagicPush 桥 | 8001 | nginx 配置留 | 恢复监听 8001（plan doc D-bridge） |
| App Center hotify | 8083 | fnOS 卸载 | 重装（plan doc D5） |
| 7 旧 discord/telegram 键 | — | `backups/retired-legs-20260909/` | 拷回 config/ 即复活 |
| apprise 多腿扇出 | — | `backups/retired-legs-bridge-20260909/`（4 键拆前 cfg） | 停桥 + 拷回 4 个 cfg → 回多腿 |
| HA 四开关通知 | — | `ha-config-pre-notify-update-2026-09-09.tar.gz` | 解压回 homeassistant/ 重启 HA |
| crontab @reboot push | — | （fnOS 不执行用户 @reboot，已由 systemd 替代） | — |

**桥回退**（回 apprise 多腿）：
```bash
sudo systemctl disable --now push-bridge
cp /srv/push/apprise/backups/retired-legs-bridge-20260909/*.cfg /srv/push/apprise/config/
# 可选：删除 Hotify 桥设备（curl -X DELETE -H "Authorization: Bearer <key1>" :8002/api/v1/devices/<uuid>）
```

## 11. 历史与迁移记录

- 完整迁移过程、验证证据、决策记录 → `docs/planned/2026-09-09-1123-Hotify统一推送中枢-推进方案.md`
- 旧 telegram_notify HA 集成源码 → 项目 `通知与集成\telegram_notify\`
- 大事记：2026-08-26 旧 telegram_notify 移除 → 2026-09-09 Hotify 迁移（D1/D2/D5/8001 退役、F 桥、E1 v1.4.2）→ 2026-09-09 晚 3004 systemd 化 + 单键扇出改造（H1，已被 H4 取代）+ 渠道前缀（H2）+ 桥扇出 TG/Discord 设备化（H3）→ 2026-09-09 深夜 **H4：HA 四开关定向（单键扇出废弃）+ python3.14 发送端迁移 3004 + 3004 矩阵页 + Hotify slug/定向语义启用（tgram/discord）** → 2026-09-10 凌晨 **H5：端口拆分（3004 仅推送/2999 展示+文档标签页）+ 弹窗居中可关 + 成对双投修复（§12 结案）** → 2026-09-10 上午 **H6：渠道分离（一键=一渠道全定向，telegram_*/discord_* 键族上线，广播仅剩 textverified 测试键）** → 2026-09-10 午间 **H7：ha_* 键族退役（HA 直接引用 device 行四渠道）+ 安卓 gotify App 过滤代理 :8003 + 消息历史 30 条倒序 + 未命名 bark 设备清理** → 2026-09-10 下午 **H8：安卓 gotify App 分频道图标（appid 改写+合成应用列表+自托管 PNG）+ bark 寄生注册断根（iPad APNs token 更换→删旧 brak→重注册 1e45→slug 接管零改 cfg）+ 21 键带编号全量回归（21/21 全绿零串台）** → 2026-09-11 早 **H9：正文修复（apprise json:// 腿 message 字段 vs Hotify 定向腿 body 字段不匹配→H6 起全部推送正文空；21 cfg 腿切 :3005 重映射中继，四端正文复验通过）**

## 12. 已知事项与待决

- **全量备份（2026-09-10 15:01，老大点名含全部敏感文件）**：`/srv/docker/backups/push-full-20260910-1457.tar.gz`（220KB/84 文件，root 700）——compose+21 cfg（含 token）+代理三端口源码+图标+hotify.db（设备注册表）+桥 config.json（TG/DC token）+2 systemd 单元；恢复手册=`docs\runbooks\push-backup-restore.md`（同步 NAS `…/apprise/docs/`，2999 文档标签可见）。同轮删除 9 项旧 push 备份（内容全被新全量覆盖，含 retired-legs-20260909）；保留 ha-config-pre-notify-update（HA 侧非 push）
- **成对双投已修复（2026-09-10 02:00，方案 C：发送端改单键）**：①horizon compose `HORIZON_WEBHOOK_URLS` 原为 `bark_horizon||gotify_horizon` 双推 → 改单 URL `http://push.example.com:3004/feishu/gotify_horizon`（容器当前未运行，下次启动即单份）；②VPS `/home/pm/polymarket/ledger/persistent/config/webhook-config.json` 原有 discord（bark_btc）+webhook（gotify_btc）双通道 → `discord.enabled=false` 仅保留 gotify_btc。双端各 2 份 → 各 1 份；历史双推证据=Horizon 日报/btc 报告曾同秒成对落 Hotify
- **H6 渠道分离（2026-09-10 09:50，老大晨间需求：一键=一渠道、地址唯一、互不干涉）**：广播架构退役——apprise 25 键全部定向化。`gotify_X`（6）→`json://…:8002/<ACE uuid>`（仅安卓）；`bark_X`（7）→`json://…:8002/brak?-group=X`（仅 iPad，分组/铃声保留）；新增 `telegram_X`（4：nas/btc/device/general）→`…/tgram` + 桥路由 TG topic；新增 `discord_X`（3：nas/btc/device）→`…/discord` + 桥路由 DC 频道；textverified 去重留单广播腿（测试键，一键 iPad+安卓——桥无广播路由，TG/DC 不收，09-11 实测更正）。桥 routes 改造：telegram_*/discord_* 替代原 gotify_* 扇出（旧 gotify_nas/btc/device、bark_general 路由删除，ha_tgram/ha_discord 保留）。**发送端语义变化**：想多渠道就推多个键（每端恰好 1 份，成对推不再双投）；VPS/外部系统地址=飞书兼容 `http://push.example.com:3004/feishu/<key>`（公网）/`http://192.168.1.10:3004/feishu/<key>`（内网），2999 矩阵页点任意格子弹窗可复制全部地址形态。配对备份：`backups/cfg-bridge-pre-H6-20260910-0946.tar.gz`。验证：矩阵 8 行四列全对齐、四键实测各只到一端（桥日志 telegram_btc/discord_btc sent ok）、消息历史标注「定向：安卓 ACE/iPad Bark/TG topic N/Discord #X」
- **H7 渠道分离收尾（2026-09-10 11:30，老大：ha_* 不需要、直接引用 device 四渠道；bark 只留一台 iPad；安卓串台处理）**：①`ha_android/ha_bark/ha_tgram/ha_discord` 四 cfg 删除、桥 routes 同步删（7 路：telegram_nas/btc/device/general + discord_nas/btc/device），HA 通知直接引用 `gotify_device/bark_device/telegram_device/discord_device`（地址定死，见 §4 HA 四渠道引用）；②未命名 bark 设备（uuid d694…，9/10 09:14 经公网 198.51.100.7 注册=老大 iPad 蜂窝网重注册）已删——10:40 全量巡检 7 条 bark 全走 ipad/brak 设备（✓apns 6127c36c），未命名设备零投递；③**Hotify gotify 兼容层缺陷**：`GET /message` / WS `/stream` 不按 token 过滤（任何 token 返回全局消息流，appid 全=1），安卓 gotify App 轮询/流式拉到全部 20 键的消息——第三方镜像 v1.4.2 无配置项、无源码，hooks 插件是入站 webhook 方向解决不了；④**修复=push-translator 三端口 :8003**：ACE 过滤代理（`universal-push-proxy.py` AceFilterHandler）——`GET /message` 代理+按标题过滤、WS `/stream` 101 握手转接+帧级过滤、`/application`/`/health` 透传、其余一律 404（admin API 不外露）。过滤规则=**只丢已知跨渠道标签**（`[bark_*] [telegram_*] [discord_*] [ha_bark/tgram/discord]`），[gotify_*]/[textverified]/[ha_android]/无前缀/旧广播时代标签（[nas]/[VPS] 等）全保留，安卓历史零误伤。验证：100→73 条（0 泄漏）、WS 实测 [gotify_nas] 帧到达 [bark_nas] 帧被滤、101 握手正常。**安卓 App 改连 `http://192.168.1.10:8003`（token 不变）**；若需蜂窝网访问需在路由器给 8003 加公网映射。⚠️ 8002 公网暴露=任何人可拉全部消息（token 随便填都过），建议关闭 8002 公网映射只留 3004 推送口
- **8002 公网保留（老大决策，2026-09-10 12:00）**：外网设备（iPad Bark、安卓 gotify App）的接收地址=8002，蜂窝网访问必须公网映射，**不关**。已知代价=任意 token 可拉全部消息（Hotify 兼容层缺陷），接受。安卓内网走 :8003 过滤代理；蜂窝网若也要过滤，可给 8003 加公网映射、App 填 DDNS:8003（老大定）
- **H8 安卓 gotify App 分频道图标（2026-09-10 13:51 部署，老大需求：分频道做图标）**：8003 代理将消息 appid 按标题标签改写——`[X_nas]`→101（NAS 蓝 N）、`[X_device]`→102（Device 绿 D）、`[X_btc]`→103（BTC 橙 B），其余=1（Hotify 紫 H 默认，codex/deepseek/general/horizon/textverified 键落此）；`/application` 返回本地合成 4 应用列表（各带 `image:"/icon/<img>.png"`，sortKey a1/a101/a102/a103），`/icon/*.png` 静态服务自 `/srv/push/apprise/icons/`（Cache-Control 86400s），WS 帧同步改写 appid。图标源=本地 `tools/icons/*.png`（System.Drawing 128×128 圆角方块+白字母）。App 图标缓存=装好后需杀掉重开 App 拉新应用列表。实测：/application 4 应用、/icon/nas.png 200、消息 appid 改写生效
- **H8 bark 寄生注册断根（2026-09-10 14:23，根因=App 存死 key）**：iPad Bark App 蜂窝网（公网 198.51.100.7）冷启动自动 `GET /register`——App 存的服务器 URL key 失效即新建设备（今早 d694、13:42 7f76 两台寄生设备，App 里 URL 固定不可改）；且 iPad APNs token 已换新（devicetoken=feb2…5baf，旧 6127…为宽限期投递）。**接管方案（cfg 21 键零改动）**：删旧 brak 设备（6127…）→ iPad 切飞行模式触发 App 重注册（14:23 新 key `1e45…1f03`）→ **admin API 字段级编辑** `PUT /api/v1/devices/<uuid>/slug`（json `{"slug":"brak"}`，从 Hotify console 前端 JS 挖出；同类 `/name` `/sound` `/type` `/source`）设 slug=brak + name=ipad → 新设备无缝接管推送（✓apns 1e45 实证）。断根原理=App 存的 1e45 是活 key，冷启动认领不再新建。⚠️ 若 iPad 再出现寄生注册：先查 App URL 尾部 key 是否仍=1e45…，再按本条流程接管
- **H9 正文修复：全链路正文字段错配（2026-09-11 08:46，老大报告 gotify 只有标题没内容）**：根因=Apprise `json://` 腿 POST 的 payload 是 `{"version","title","message","attachments","type"}`（正文在 **message**），而 Hotify 定向腿 `POST /<key或slug>` 是 bark 兼容入站只认 **body**（title+body，JSON/表单均可）——字段名不匹配，正文自 H6 起全部丢弃（昨日 21 键回归只核了标题+投递，未核正文）。排查中逐一排除的腿：`bark://`（Apprise 发 `{"title","body","device_key"}` 到 `/push`，Hotify `/push` 对任何 device_key 都 400）；`gotify://`（发 `{"title","message"}` 到 `/message?token=`，Hotify 该端点是**广播**语义——`[push] saved target_profile_id=0`+全部设备投递，不能当定向腿用，实测时曾把测试消息广播到 iPad/TG/DC）；`form://`（表单字段仍叫 message）。**修复=3005 重映射中继**（`universal-push-proxy.py` 第 4 监听口 RelayHandler）：收 apprise JSON/表单→`message`→`body` 重写→原样转发 `127.0.0.1:8002/<path+query>`（路径/分组/铃声参数透传），响应透传。21 cfg 腿 `json://…:8002/`→`json://…:3005/` 仅换端口（改动前备份 `backups/cfg-pre-relay-20260911.tar.gz`）；systemd ExecStart 加第 4 口 `3004 2999 8003 3005`。验证四端：安卓存储 `'[gotify_nas] K中继修复验证-安卓' msg='这是正文内容安卓应显示'`、iPad `✓apns 1e45`+`category=nas`（分组透传）、TG `[telegram] sent key=telegram_nas message_id=20807`、DC `[discord] sent key=discord_nas`。附带：Apprise json:// 的 `?-group=X&-sound=Y` 参数实测透传为目标 URL query（`/brak?group=X&sound=Y`），中继原样带过 ✓
- **H9b bark 寄生注册复发（2026-09-11 08:24，H8 断根理论修正）**：iPad 又注册了新设备 `7d35…8cc1`（devicetoken 仍=feb2…5baf，与 1e45 同 token）——**且这次 App 存的 1e45 是活 key**，证明 H8 的「死 key 才触发重注册」不完整：Bark App 冷启动（蜂窝网）会重注册，与 key 死活无关。处置：7d35 无 slug 零投递，直接 DELETE（投递经 slug brak→1e45，devicetoken 相同 iPad 照常收到；E 测试曾双弹横幅=同 token 双设备的证据）。设备表已回 4 台（brak/tgram/discord/gotify）。**遗留**：寄生注册会随冷启动复发（每天一次量级），slug 接管保投递不受影响，但设备表会持续积垃圾——彻底断根需在 8002 前加 /register 拦截代理（固定返回 1e45），涉及公网口改造，待老大定夺（当前日常处置=见 7d35 类无 slug 设备就删）
- **21 键全量回归（2026-09-10 14:38，老大要求每键一条带数字消息 No.01-21）**：**21/21 全绿零串台**（老大确认"这次完美了"）——iPad 8 条 ✓APNs（No.07-13 bark 7 键+No.21 广播）、安卓 6 条 WS+广播存史（No.01-06）、TG 4 条 message_id 20803-20806（No.14-17）、DC 3 条 sent（No.18-20）；textverified 广播=iPad ✓apns + 安卓存历史（App 关闭时无实时帧，开 App 可见）；TG/DC 桥经 WS 收到该帧但无 textverified 路由不转发——**TG/DC 实际收不到广播**（09-11 老大实测确认；pushkit "unknown platform windows" 为桥设备预期噪音）
- **文档标签页数据源**：NAS `/srv/push/apprise/docs/*.md`（本手册/apprise-configs/推进方案三份同步）；改本地文档后需同步到 NAS 才反映到网页
- TG/Discord 频道扩展：老大新建 TG topic / Discord 频道后把 topic ID / webhook URL 给我 → 桥 config.json 加路由 + （若新频道属新业务源）apprise 建键
- TG bot token / Discord webhook token 曾在终端输出中全文出现（19:12、23:26 两次）；介意则 regenerate（BotFather /revoke、频道 webhook 重置），改后更新桥 config.json 并 restart 桥
- dashboard：旧 4 开关卡片已复活（实体名未变）；gotify 卡片显示名从「统一通知」变为「通知-安卓手机」，可按需改 UI 文案

---

## 13. 2026-09-25 新增 `mp` 频道（MoviePilot 专用）

**起因**：用户指令「你给 mp 新建一个频道」——为 MoviePilot 单独开一个通知频道，便于识别与单独分组。

**改动方式**（依据本手册 §9「矩阵页某列 ✗ → bark/gotify 列=建对应 cfg」）：
| 步骤 | 内容 |
| --- | --- |
| 备份 | `push/apprise/backups/cfg-pre-mp-20260925-192934.tar.gz` |
| 新增键 | `apprise/config/bark_mp.cfg`（复制 `bark_nas.cfg`，**仅把 Bark 分组 `-group=nas` 改为 `-group=mp`**，凭据部分未动）<br>`apprise/config/gotify_mp.cfg`（复制 `gotify_nas.cfg`，逐字节相同） |
| 权限 | `nasuser:Users` / `600`（与既有键一致） |
| 是否重启 | **不需要** —— 2999 矩阵与文档页实时读 cfg |
| 验证 | `GET :2999/matrix.json` 新增 `mp` 行，`gotify` 与 `bark` 均为 `ok: true` |

**效果**：`mp` 频道与 `nas`/`btc`/`device` 等同级，可在矩阵页点格子复制地址：
- 通用：`http://192.168.1.10:3004/push/<KEY>?title=&body=`（KEY 见矩阵页对应格子）
- MagicPush：`POST http://192.168.1.10:3004/api/push`，`Authorization: Bearer <KEY>`，体 `{"title","content"}`
- 安卓 gotify 腿 → Hotify ACE 设备；iPad Bark 腿 → Hotify `brak?-group=mp`（iPad 上独立分组）

**备注**：`gotify_mp` 与 `gotify_nas` 指向同一 ACE 设备（仅键名与投递标注不同）；若希望 ACE 应用里给 `mp` 单独图标/过滤，需走 H8 的 appid 改写流程（本次未做，不影响投递）。

---

## 14. MoviePilot 接入推送栈（2026-09-25 19:44 查证）

**背景**：用户问「MP 的新建通知渠道里选『自定义』怎么设置」。查证结论如下。

### 14.1 MP v3 的通知渠道类型与字段（从前端 `NotificationChannelInfoDialog` 提取，权威）

| 类型（type 标识） | 界面名 | 配置字段（config 键） |
| --- | --- | --- |
| `custom` | **自定义** | ❗**该弹窗里没有任何专属字段**（只有 类型/名称/启用） |
| `dingtalk` | DingTalk | `DINGTALK_WEBHOOK`、`DINGTALK_SECRET` |
| `discord` | Discord | `DISCORD_BOT_TOKEN`、`DISCORD_CHANNEL_ID`、`DISCORD_GUILD_ID`、`DISCORD_ADMINS` |
| `feishu` | 飞书 | `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_CHAT_ID`、`FEISHU_ENCRYPT_KEY`、`FEISHU_OPEN_ID`、`FEISHU_VERIFICATION_TOKEN`、`FEISHU_ADMINS` |
| `qqbot` | QQ | `QQ_APP_ID`、`QQ_APP_SECRET`、`QQ_GROUP_OPENID`、`QQ_OPENID`、`QQBOT_ADMINS` |
| `slack` | Slack | `SLACK_OAUTH_TOKEN`、`SLACK_APP_TOKEN`、`SLACK_CHANNEL`、`SLACK_ADMINS` |
| `synologychat` | Synology Chat | `SYNOLOGYCHAT_WEBHOOK`、`SYNOLOGYCHAT_TOKEN`、`SYNOLOGYCHAT_ADMINS` |
| `telegram` | Telegram | `TELEGRAM_TOKEN`、`TELEGRAM_CHAT_ID`、`TELEGRAM_USERS`、`TELEGRAM_ADMINS`、`API_URL`(自定义 API 地址) |
| `vocechat` | VoceChat | `VOCECHAT_HOST`、`VOCECHAT_API_KEY`、`VOCECHAT_CHANNEL_ID`、`VOCECHAT_ADMINS` |
| `webpush` | WebPush | `WEBPUSH_USERNAME`（当前唯一已配的渠道） |
| `wechat` | 企业微信 | `WECHAT_APP_ID`、`WECHAT_APP_SECRET`、`WECHAT_ADMINS` |
| `wechatclawbot` | 微信 ClawBot | `WECHATCLAWBOT_BASE_URL`、`WECHATCLAWBOT_ADMINS` |

> **「自定义」为什么没人能用**：它的类型入口存在（类型下拉里有），但**渠道弹窗不为它渲染任何字段**，且后端 `POST /api/v1/notification/manage` 对 `custom` 一律回「该通知渠道未启用或不支持此管理动作」——它是留给**插件注册的渠道**的占位，不是"自定义 webhook"。**所以不要用它。**

### 14.2 正解：MP 用 **DingTalk** 渠道接本代理（唯一带"裸 webhook URL"的类型）

MP 的类型里只有 **DingTalk**（`DINGTALK_WEBHOOK`）与 **Synology Chat**（`SYNOLOGYCHAT_WEBHOOK`）是纯 URL 型；而本代理**恰好有 dingtalk 方言接收端**，且**忽略签名**（`sign ignored`），完全对得上：

| MP 里新增的渠道 | 类型 | `DINGTALK_WEBHOOK` 填 |
| --- | --- | --- |
| `Bark（iPad）` | DingTalk | `http://192.168.1.10:3004/dingtalk/bark_mp` |
| `Gotify（安卓 ACE）` | DingTalk | `http://192.168.1.10:3004/dingtalk/gotify_mp` |

- `DINGTALK_SECRET` 可留空或随便填 —— 代理不校验签名
- 传两个渠道即等于「bark + gotify 双推」，且走的是新建的 `mp` 频道分组
- ⚠️ **消息格式必须选「文本」而不是「markdown」**：代理 dingtalk 处理器取的是 `text.content`（`node = data.get("text")`），markdown 消息体是 `markdown:{title,text}`，会取成空串并回 `400 empty content`
- 若 MP 的 DingTalk 渠道固定发 markdown 且不可改 → 备选路径见 §14.3

### 14.3 其它可用方言（备选）

| 方言路径 | 期望载荷 | MP 侧对应类型 |
| --- | --- | --- |
| `POST /wecom/<key>` | `{msgtype, text:{content}}` | 企业微信（但 MP 用 APP_ID/SECRET，非 webhook） |
| `POST /feishu/<key>` | `{msg_type, content:{text}}` | 飞书（MP 用 APP 凭证，非 webhook） |
| `POST /notify/<key>` | form 或 JSON `{title, body\|message}` | 无直接对应类型 |
| `POST /api/push` | JSON `{title,content}` + `Authorization: Bearer <key>` | 无（MP 无法自定义请求头） |
| `GET /push/<key>` | `?title=&body=` | 无（MP 只发 POST） |

（各方言返回的平台成功响应格式见代理脚本第 66-69 行，MP 会认为发送成功。）

---

## 15. 2026-09-25 MoviePilot 接入打通（含一次代理补丁）

### 15.1 现象与根因
按 §14 在 MP 里加钉钉渠道后测试，MP 日志报：
```
message.py - 发送消息：mtype=订阅 ctype=subscribeAdded title='沙丘 (2021) 已添加订阅'
dingtalk.py - 钉钉自定义机器人返回 HTTP 400
```
**根因**：MP 的钉钉模块发送的是 **markdown 形态** `{"msgtype":"markdown","markdown":{title,text}}`，而代理的 `wecom/dingtalk` 分支只读 `data["text"]["content"]` → 取到空串 → 返回 `400 {"error":"empty content"}`。

### 15.2 代理补丁（`universal-push-proxy.py`）
在 dialect 解析处新增 markdown 兜底（原逻辑完全保留）：

```python
if dialect in ("wecom", "dingtalk"):
    node = data.get("text") or {}
    text = str(node.get("content") or data.get("content") or "")
    if not text:
        # MoviePilot 等发送端用钉钉 markdown 形态：{msgtype:"markdown", markdown:{title,text}}
        _md = data.get("markdown") or {}
        _md_text = str(_md.get("text") or _md.get("content") or "").strip()
        _md_title = str(_md.get("title") or "").strip()
        text = ((_md_title + "\n" + _md_text).strip() if _md_text else _md_title)
```
- 补丁前先备份：`apprise/universal-push-proxy.py.bak-20260925-195634`（已修正属主为 `nasuser:Users`）
- 语法校验：`python3 -m py_compile` 通过
- 生效方式：`sudo systemctl restart push-translator` → `3004/health` 与 `2999/health` 均 `200 {"status":"ok"}`
- **回滚**：`sudo cp <bak> universal-push-proxy.py && sudo systemctl restart push-translator`

### 15.3 MP 侧配置（最终态）
| name | type | config | 对应腿 |
| --- | --- | --- | --- |
| `bark` | dingtalk | `DINGTALK_WEBHOOK = …:3004/dingtalk/bark_mp`（`DINGTALK_SECRET` 留空） | iPad Bark（mp 分组） |
| `gotify` | dingtalk | `DINGTALK_WEBHOOK = …:3004/dingtalk/gotify_mp` | 安卓 ACE（gotify App） |

- 第二条由本代理侧用 MP API `POST /api/v1/notification/config` 复制第一条生成（webhook 仅把 `bark_mp` 换成 `gotify_mp`）
- 改前备份：NAS `/tmp/notifications-backup-20260925-195928.json`
- ⚠️ **MP v3 没有通知"测试"按钮**（前端 i18n 键与后端路由均无 test），验证只能靠触发真实事件

### 15.4 验证证据（2026-09-25 20:00）
触发真实事件（新增订阅「沙丘 (2021)」）后，2999 `/messages.json` 按时间戳判定新增投递 **2 条**：
```
key=gotify_mp   route=安卓 ACE（gotify App）   title=沙丘 (2021) 已添加订阅
key=bark_mp     route=iPad Bark（mp 分组）     title=沙丘 (2021) 已添加订阅
```
两端实收；测试用的订阅已删除（`DELETE /api/v1/subscribe/1` → `status: deleted`）。MP 日志不再出现 `HTTP 400`。

### 15.5 后续可选项
- 若希望 ACE 应用内给 `mp` 单独图标/过滤，走 H8 的 appid 改写流程
- MP 的其它通知类型（Discord/Telegram/Slack 等）若要接本栈，可复用同一思路：找**纯 URL 型**字段指向代理对应方言（`/wecom` `/feishu` `/notify` `/push`）

---

## 16. 2026-10-01 新增 `brainquant` 频道

**起因**：用户指令「去看 192.168.1.10:2999，给我新增一个推送频道，频道是 `brainquant`」——在推送矩阵页新增一个独立频道，与既有渠道互不干涉。

**改动方式**（依据 §9「矩阵页某列 ✗ → bark/gotify 列 = 建对应 cfg」）：

| 步骤 | 内容 |
| --- | --- |
| 备份 | `push/apprise/backups/cfg-pre-brainquant-20261001-200142.tar.gz`（整个 `config/`，1269 B） |
| 新增键 | `apprise/config/bark_brainquant.cfg`（复制 `bark_nas.cfg`，**仅把 Bark 分组 `-group=nas` 改为 `-group=brainquant`**，其余含 `&-sound` 与凭据段逐字节未动）<br>`apprise/config/gotify_brainquant.cfg`（复制 `gotify_mp.cfg`，SHA-256 `3bddd75c9901e5d3d48824d517143d5ef14a654ffb9f3d1c07430927fab22dd1`，与既有 `gotify_nas/device/horizon/mp` 完全相同） |
| 权限 | `nasuser:Users` / `600`（与 `mp` 键一致） |
| 是否重启 | **不需要** —— 2999 矩阵与文档页实时读 cfg |
| 验证 | `GET :2999/matrix.json` 频道数 11 → **12**，新增 `brainquant` 行，`gotify` 与 `bark` 均 `ok: true` |

**端到端实测（2026-10-01 20:02）**：经 `:3004/push/bark_brainquant` 与 `:3004/push/gotify_brainquant` 各推一条自检，均返回 `{"apprise_status": 200}`；2999 消息历史新增两条且路由标注正确：

```
key=bark_brainquant     route=iPad Bark（brainquant 分组）
key=gotify_brainquant   route=安卓 ACE（gotify App）
```

`push-translator` 与 `push-bridge` 均 `active`，2999/3004 `/health` 均 200（**全程未重启服务**）。

**效果**：`brainquant` 频道与 `mp`/`sms` 同级，矩阵页点格子可复制地址：
- 通用：`http://192.168.1.10:3004/push/<KEY>?title=&body=`（KEY = `bark_brainquant` / `gotify_brainquant`）
- MagicPush：`POST http://192.168.1.10:3004/api/push`，`Authorization: Bearer <KEY>`，体 `{"title","content"}`
- 安卓 gotify 腿 → Hotify ACE 设备；iPad Bark 腿 → Hotify `brak?-group=brainquant`（iPad 上独立分组）
- 飞书兼容（外部系统首选）：`http://push.example.com:3004/feishu/<KEY>`（公网）/ `http://192.168.1.10:3004/feishu/<KEY>`（内网）

**备注**：`gotify_brainquant` 与 `gotify_nas`/`gotify_mp` 指向同一 ACE 设备（仅键名与投递标注不同，消息历史按 `[bark_brainquant]`/`[gotify_brainquant]` 前缀区分）；若希望 ACE 应用内给 `brainquant` 单独图标/过滤，需走 H8 的 appid 改写流程（本次未做，不影响投递）。TG/Discord 腿未建（矩阵列显示 `ok:false`），需要时按 §4 桥路由加 topic/频道。

---

## 17. 2026-10-02 bark 腿 424（APNs 阵发超时）根因与中继保护

**现象**：`brainquant` 频道上线后不久，外部调用方连续 6 次收到 `424 Failed Dependency`（同刻 gotify 腿稳定 200）；apprise 返回体写着 `A Connection error occurred sending JSON notification to 192.168.1.10`；约 10 分钟后自愈。

**根因（2026-10-01/02 诊断；证据为 Hotify 实时日志与持久化 `hotify.log`）**

bark 腿真实链路 = `apprise → 192.168.1.10:3005（push-translator 中继）→ 127.0.0.1:8002 Hotify → Apple APNs(api.push.apple.com)`。**8082 的自建 bark-server 不在这条链路上**（它只服务 iOS App 直连场景）。

```
20:09:40 [bark-apns] ⚠ apns 1e455568… system_error (status=500)
  Post "https://api.push.apple.com/3/device/…": context deadline exceeded
  (Client.Timeout exceeded while awaiting headers)
20:09:40 [http] 200 | 1m0s | POST /brak
```

- Hotify 发 APNs 的客户端超时**硬编码 60s**（`config.yaml` 只有 server/pushkit/store 三段，无 APNs 可调项）；
- 阵发期每条 `/brak` 挂满 1 分钟并积压；
- apprise 的 `json://` 插件只等 **4s** → 判失败 → 424，并把读超时写成 `A Connection error … to <host>`（**不是真的连不上**）；
- **消息并没有丢**：同一批里 20:11:25 出现 `✓ apns`，两条 `POST /brak` 耗时 57.70s / 53.46s 后返回 200 —— 是延迟，不是失败。

**全量统计（`hotify/data/hotify.log`，2026-09-09 起）**：bark 推送 **451** 次、APNs 失败 **38** 次（≈8%），集中在若干阵发窗口：09-27 20:00 ×5、09-30 20:00 ×5、10-01 08:00 ×6、**10-01 20:00 ×12**、10-02 00:00 ×3；其余时段 1–2 秒成功。复测：APNs 直连 1.05–1.85s、经 mihomo 1.07–1.26s。

**处置（用户 2026-10-02 批准“两个都做”）**

| 项 | 内容 | 结果 |
| --- | --- | --- |
| **A. 中继保护（已上线，长期保留）** | `universal-push-proxy.py` 新增 `BARK_WAIT = 3.0` 与 `RelayHandler._forward_bark()`：bark 腿（路径 `/brak`）最多等 3 秒——正常时原样返回 Hotify 结果；超时先回成功形状 `{"code":200,"message":"ok"}`，后台线程继续投递并只记日志（`[relay] bark slow-path` / `[relay] bark result`）。其它路径（gotify 腿等）走原逻辑，不受影响 | ✅ 单测：慢下游 **3.00s** 返回 200；连接拒绝立即抛错 → 外层 502（真实失败仍如实上报）。生产：`[relay] bark result /brak?group=… 200 (56 B)` + `✓ apns` 1.57s |
| **B. APNs 走代理（已试、对 APNs 无效、已回退）** | 给 hotify 加 `HTTP_PROXY/HTTPS_PROXY=http://mihomo:7890` + `NO_PROXY`，并接入 `clash` 网络，意图让 APNs 走境外节点 | ❌ mihomo 侧只看到 hotify（172.24.0.12）到 `gitee.com`（铸票端点拉取）的连接，**完全没有 `api.push.apple.com`** ⇒ APNs 客户端用自定义 HTTP/2 Transport、**不读代理环境变量**。已恢复 compose 原状并重建容器 |

**现状与限制**：阵发时 bark 腿**不再返回 424**（发送端看到 200），但 iPad 通知在那 1 分钟内仍会延迟到达——APNs 可达性是网络侧问题，Hotify（闭源镜像）没有可用的低风险修法（透明重定向需改 mihomo tproxy + 宿主 iptables，影响面远超本问题）。gotify 腿（WS，不经 APNs）始终不受影响。**这一保护对所有 bark 腿生效**（nas/device/btc/mp/sms/kefu/horizon/codex/deepseek/general/brainquant）。

**改动与回退位置**：权威源码 `tools\universal-push-proxy.py` 与 NAS `/srv/push/apprise/universal-push-proxy.py` 同步一致（SHA256 `483b0c8a…`）；回滚副本 `universal-push-proxy.py.bak-20261002-091245`、`docker-compose.yml.bak-20261002-091515`（后者已用于回退 B）。

---

## 18. 2026-10-06 Bark 参数透传（静音推送）

**起因**：用户推了几条「bark 静音通知」没有效果，问是不是被 NAS 配置吞了——结论是**是**（两层都吞）。

**根因（代码 + 存储证据）**

1. `:3004` 的推送路由（`/push/<key>`、`/notify/<key>`、`/api/push`）原先**只读 title/body**，调用方附带的 `isSound` / `sound` / `level` / `group` 在网关就被丢掉，但仍回 `{"apprise_status": 200}`（发送端看起来是成功的）；
2. 每个 `bark_*` 键的 apprise 腿里**写死**了 `-group=X&-sound=Y`（如 `bark_brainquant` → `-group=brainquant&-sound=bell`），即使参数透到了 apprise，也会被腿里的默认值盖掉。

**证据**：① 2999 消息历史里 17:07:53–17:10:48 那批探测（A 基线 / B `isSound=0` / C `sound=none` / D `group+level`）**全部带 `[key]` 前缀** ⇒ 都走的 :3004（只有 :3004 会加前缀）；② `hotify.db` 中这几条的 `ext` 只有 `{"group":"brainquant","sound":"bell","type":"info","version":"1.0"}`，没有任何 level/sound 覆盖痕迹 ⇒ 四条投出去的内容**完全一致**，当然听感也一样；③ 部署副本与仓库源码 SHA256 相同（`483b0c8a…`），排除"NAS 被改过"。

另：**`isSound` 根本不是 Bark 参数**（Bark API V2 只有 `level`/`sound`/`group`/…），Bark 的静音参数是 **`level=passive`**。

**Hotify 侧实测支持**（2026-10-06 17:36 直推 `:8002/brak`，绕开网关）：A（`level=active&sound=bell`）**响**、B（`level=passive&sound=bell`）**不响** ⇒ Hotify 认这个参数，问题只在网关。注意 **iOS 的 passive = 不响、不亮屏、不进横幅，只进通知列表**（用户实测确认）。**"弹横幅但不响"在 iOS 上没有逐条开关**：`sound` 留空（D）、写 `silence`（E）、写非法名都会回落铃声照常响（D/E 两条实测；Hotify 源码注释亦写"非法名静默回落系统默认"）。

**修复（用户批准"改网关"）**：`tools/universal-push-proxy.py` 新增 `BARK_PARAM_KEYS` / `_bark_leg()` / `_bark_params()` / `_bark_forward()`——`/push/<key>`、`/notify/<key>`、`/api/push` 上**带了白名单 Bark 参数、且该键的 cfg 腿指向 iPad Bark（path=`brak`）**时，**绕过 apprise 直投 :3005 中继**（中继再原样带 query 给 Hotify）；合并规则 = cfg 腿参数保留、**调用方优先**。**不带这些参数时行为一字未改**，仍走原 apprise 路径。

- 白名单：`level` `sound` `group` `badge` `volume` `call` `isArchive` `url` `copy` `autoCopy` `subtitle` `icon` `action` `ttl`
- 非 bark 键（gotify/telegram/discord）带这些参数时**忽略**、回落原路径，避免把无关字段塞进别人的消息

```bash
# 静音（不响、不弹横幅，只进通知列表）
curl "http://192.168.1.10:3004/push/bark_brainquant?title=标题&body=正文&level=passive"
# 临时换铃声 / 临时换分组
curl "http://192.168.1.10:3004/push/bark_nas?title=标题&body=正文&sound=alarm"
curl "http://192.168.1.10:3004/push/bark_nas?title=标题&body=正文&group=test"
# JSON 分支同样支持
curl -X POST http://192.168.1.10:3004/notify/bark_nas -H 'Content-Type: application/json' \
  -d '{"title":"标题","body":"正文","level":"passive"}'
# MagicPush 分支（Bearer）亦可：{"title","content","level":"passive"} → :3004/api/push
```

**当次验证**：① 无参数回归 `:3004/push/bark_brainquant` → `apprise_status 200`，`hotify.db` 记录 `ext={"group":"brainquant","sound":"bell",…}`（与修复前一致）；② 带 `level=passive` → `ext={"group":"brainquant","level":"passive","sound":"bell"}` + 日志 `✓ apns`；③ `POST /notify` JSON 分支同样落到 `ext.level=passive`；④ 四口（3004/2999/3005/8003）`/health` 全 200、2999 页面含新说明；⑤ 部署副本与仓库源码 SHA256 一致（`935172a8…`）。

**部署与回退**：改动前备份 `universal-push-proxy.py.bak-20261006-174549`（原始 71,974 B / `483b0c8a…`）；部署方式 = scp 到 `/tmp` → `py_compile` → `sudo cp` + `chown nasuser:Users` + `chmod 700` → `systemctl restart push-translator`。**回退** = 用该 `.bak` 覆盖回去 + 重启（一步）。

**已知边界**：透传只对 **bark 腿**生效；iOS 的 passive 通知不进横幅属系统行为，不是投递失败；「看得见但不响」目前无解（除非在 iOS 设置里对 Bark App 整体关声音，那会影响全部 Bark 通知）。

### 18.1 「弹横幅但不响」闭环（2026-10-06 19:55 实测通过）

上文 17:36 的 D/E 两条（`sound=` 空串、`sound=silence`）**当时会响，是因为 App 里还没有名为 `silence` 的铃声文件**——内置铃声列表（alarm/bell/birdsong/…/update）里并没有静音项，名字不存在时 iOS 回落默认铃声。**导入自定义静音铃声后同名参数即生效**：

1. **铃声文件**：`silence.caf`（CAF/Linear PCM，首选）、`silence-be.caf`、`silence.wav`、`silence.aiff`、`silence.m4r`（AAC）五份，均 1.0s/44.1kHz/单声道、`volumedetect` 实测 **mean_volume = -91.0 dB**（数字静音）。副本位置：Windows 桌面 `F:\User\Desktop\Bark静音铃声\`（含 README）。用 NAS 的 jellyfin 容器自带 ffmpeg 生成。
2. **iPad 侧**：把 `silence.m4r` 导入 Bark App 的铃声列表（App 里显示名 = **`silence`**；用户 2026-10-06 19:4x 完成）。
3. **发送端**：`curl "http://192.168.1.10:3004/push/bark_<频道>?title=…&body=…&sound=silence"` → **弹横幅、不响**（19:53:29 经 :3004 网关实测通过；`hotify.db` 该条 `ext={"group":"brainquant","sound":"silence"}` + 日志 `✓ apns`）。

**两个静音层次（可混用）**

| 想要的效果 | 发送端参数 | 依赖 |
| --- | --- | --- |
| **弹横幅 + 不响** | `&sound=silence` | iPad Bark 已导入名为 `silence` 的静音铃声（否则回落默认铃声照常响） |
| **连弹都不弹**（高频批次） | `&level=passive` | 无（Hotify 原生支持，已实测） |
| 全部 Bark 通知「弹但不响」 | 无（设备侧设置） | iPad → 设置 → 通知 → Bark → 关「声音」（影响整个 App） |

---

## 19. 2026-10-09 hideck 短信通知正文行序（「内容」置顶）

**起因**：老大要求 7575（HiDeck）来的短信通知，**短信原文（内容）排在最上面**，其后依次 号码／时间／设备／通道；并问能否显示「哪张卡收到的」。

**为什么不能在面板模板里改**（已按 hideck 源码 v2.1.23 与最新 v2.1.29 逐条核对）：

- 面板「企业微信」渠道的 `JSON 请求体模板` 只做变量替换：每个 `{{var}}` 被换成 **一个完整的 JSON 字符串**（`wecom.go`：`json.Marshal` + `strings.ReplaceAll`）⇒ **一个 JSON 字符串里放不了两个变量**（`{"text":"{{message}}"}` 会被判非法，作者自己的单测就是这么断言的）；未知变量、渲染后不是 JSON 对象一律报错、通知发不出去。
- 可用变量 12 个：`event title message timestamp content number contact_name attribution device_id device_name device_label time`（**面板提示少列了 `contact_name`、`attribution`**，但后端支持）。
- 短信正文这块**行序在服务端写死**（`manager.go` `NotifySMSWithSource` + `titles.go` `notificationLines`，用「标签 + 4 空格 + 值」拼接）：`收到新短信 / 设备 / 通道 / 号码 / [联系人 / 归属] / 时间 / 内容`；模板只能整块搬 `{{message}}`。

**实现**（`tools\universal-push-proxy.py` → 函数 `_hideck_ordered`，用于 `/wecom`、`/dingtalk` 分支）：

- 只认 hideck 的通知块：首行必须命中 hideck 通知标题白名单（收到新短信/来电通知/未接来电/…），且必须有「内容」行；
- 其余行按**标签白名单**分组（设备/通道/号码/联系人/归属/时间/内容/主叫/被叫/旧 IP/新 IP/耗时）；**「内容」之后的所有换行（含看起来像标签的行）都算短信原文**，多行短信不会被拆散；
- 输出行序 = `_HIDECK_LINE_ORDER` = 内容 → 号码 → 联系人 → 归属 → 时间 → 设备 → 通道；首行仍作通知标题（Hotify 里显示为 `[sms] 收到新短信`）；
- 结构不认识（非 hideck 来源、未知标签、缺内容行）⇒ **原样返回**，其它 `/wecom`、`/dingtalk` 调用方（MoviePilot 等）行为不变。

**改动与回退**：权威源 `tools\universal-push-proxy.py`（新 SHA-256 `f0398a7e…`）；改前备份 `apprise/universal-push-proxy.py.bak-20261009-192953`（`935172a8…`）；部署副本与源码逐字节一致。回退 = 用 `.bak` 覆盖 + `sudo systemctl restart push-translator`。

**验证（2026-10-09 19:30）**：① 本地回归 `python tools\hideck-notify-order-test.py` 13 项全过；② 四口 `/health`（3004/2999/3005/8003）全 200；③ 合成 hideck 报文推 `:3004/wecom/sms` ⇒ `:2999/messages.json` 新条目行序＝内容→号码→时间→设备→通道、标题仍「收到新短信」；④ 双腿实收：apprise 日志 `Delivered Notification(s) - KEY: sms`，hotify 日志 `✓ apns 1e4555…`（iPad）+ gotify WS 推 ACE（安卓）。

**已知边界**：

- **「哪张卡收到的」加不了**：hideck 的通知上下文里没有卡/ICCID/卡名（v2.1.23 与最新 v2.1.29 变量表一致），`设备` 只是模组（`ec20-device4`）、`通道` 只是 蜂窝/VoWiFi。要这项只能给作者提 issue/PR。
- **来电通知不重排**（没有「内容」行），保持原样。
- 真实入向短信的最终排版属自然触发验证：下一条真实短信到达后读 `:2999/messages.json` 或看手机即可复核（与合成报文走同一代码路径）。

---

## 20. 2026-10-19 飞书出站腿（`feishu_*` × 12 频道）

**需求**（用户 2026-10-19）：「帮我建群，然后新建一个飞书的推送渠道，所有频道都可以通过飞书推送」＋「推送的频道的信息里都要带上频道名」。

**群**：飞书「通知中心」`oc_00000000000000000000000000000000`（普通群 `chat_mode=group`、群主=用户、机器人「小鲸」`cli_0000000000000000` 已在群）。

**群定位：纯通知，不绑 DSH 工作区**（用户 2026-10-19 决定：「通知中心只通知不会话」）。机器人**必须留在群内**才能发文件/消息，而插件的 `groupResponseMode=all`（群内免 @ 即回）是**机器人级配置、不能按群关闭** ⇒ 运行规则就是**不要在该群发消息**；只要不发，这里永远不会产生会话。🈲 别对 `group:oc_8b26…` 发 `/conv`（也没有必要）。

**为什么自建腿**：apprise 没有飞书插件；既有「飞书兼容 webhook」（`:3004/feishu/<key>`）是**入站方言**（别的系统以飞书格式推给我们），方向相反，不能复用。

**链路**：

```
3004 /push/feishu_<频道>  →  apprise :8000（key=feishu_<频道>）
   →  cfg 腿 json://192.168.1.10:3806/feishu_<频道>
   →  代理第 5 口 FeishuOutHandler → open.feishu.cn /im/v1/messages（chat_id=通知中心）
```

**设计要点（都是踩过坑才定下来的）**：

- **端口用 3806，不是 3006**：30xx 段被 docker publish 占满（实测 3006–3010 全是 `docker-proxy`）。首轮用 3006 直接把服务打成 `Address already in use` 崩溃重启，推送中断数十秒。**宿主代理端口要避开 docker 会发布的段。**
- **腿里必须写宿主 LAN IP `192.168.1.10`**，不能写 `127.0.0.1`：apprise 容器 `net=clash`（与另一容器共享 netns），`127.0.0.1` 打到的是容器自己。
- **凭据** `/srv/push/feishu.env`（0600，nasuser）：`FEISHU_APP_ID` / `FEISHU_APP_SECRET`（小鲸应用）/ `FEISHU_CHAT_ID` / `FEISHU_GROUP_NAME`。token 进程内缓存、到期前 60s 刷新。
- **标题格式**：首行固定 `[频道: <频道>] <标题>`；3004 自动加的 `[feishu_xxx]` 前缀会被剥掉，避免重复。
- **12 个键**（沿用 H6「一键=一渠道」）：`feishu_nas` / `_btc` / `_device` / `_horizon` / `_codex` / `_deepseek` / `_general` / `_textverified` / `_brainquant` / `_kefu` / `_mp` / `_sms`。2999 矩阵新增 **feishu 列**（表头「飞书群 通知中心」），点格子取推送地址。

**验收（2026-10-19 15:26）**：12 键经 `:3004` 实推 ⇒ 群里 12 条、`[频道: X]` 逐一正确；`:2999/matrix.json` 的 feishu 列 **12/12 ✓**；既有 bark/gotify/telegram/discord/imessage 五列零变化；五口 `/health` 全 200。

**⚠️ 两个坑（都真的踩了，排错先看这两条）**：

1. **未知 key 会「静默成功」**：`POST :8000/notify/<不存在的 key>` 返回 **204 且无报错**（不是 404）。所以「12 个请求都 204」**不能**当作投递成功——必须看 apprise 日志有没有 `Delivered Notification(s) - KEY: feishu_xxx`，或代理日志有没有 `[feishu] sent channel=…`。正常的键返回的是 **200 + 141 字节**。
2. **cfg 必须落在 `apprise/config/`**：放到上层 `apprise/` 时，矩阵（读 `config/`）与容器（挂 `config/`）都看不见，表现同样是「推送 204、群里没消息」。

**apprise 热加载**：新增 cfg 键**无需重启 apprise 容器**（实测放入 `config/` 后第一次请求即生效，日志 `Delivered Notification(s) - KEY: feishu_nas`）。

**回退**：`apprise/universal-push-proxy.py.bak-20261019-1530`、`/etc/systemd/system/push-translator.service.bak-20261019-1530`、`backups/cfg-pre-feishu-20261019-1530.tar.gz`。回退 = 覆盖源码 + `sed` 去掉 ExecStart 末尾的 `3806` + `daemon-reload && restart`（12 个 feishu 键随之失效，其余列不受影响）。
