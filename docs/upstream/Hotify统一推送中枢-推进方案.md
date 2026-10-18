> **脱敏说明**：本文是原运行手册的公开版，内网 IP、宿主路径、域名、主机名均已替换为占位值（`192.168.1.x` / `/srv/...` / `example.com`）。渠道名（nas/btc/device/general/mp…）保留，它们是逻辑标签而非身份信息。凭据一律以 `<key>`、`<KEY>` 形式出现。

# Hotify 统一推送中枢 推进方案

> 状态：in_progress（方案 C 已获批执行；Phase A 迁移完成，Phase B 经 S0 证据调整为搁置）
> 创建时间：2026-09-09 11:23 +08:00（11:42 按老大三问改写为方案 C）
> 开始时间：2026-09-09 12:49 +08:00（老大批准「去改造吧」；端口指示「8000 附近」→ 选定 8002）
> 完成时间：

## 1. 目标与验收

- 产品目标：推送系统收敛为**一个中枢 Hotify**——docker 版迁入 /srv/push/ compose 栈，取代 fnOS 应用中心安装；日常操作统一在 Hotify /console（设备、消息历史、插件）；MagicPush 以 hooks 插件接入；**Discord/Telegram 经一个小桥接容器（hotify-fanout）融入 Hotify**——消息先进 Hotify 落库，桥订阅后转发，Hotify console 即全部消息的总界面；gotify 客户端不换 App 只改地址（Hotify 官方兼容）；可替代老容器（apprise/gotify/bark-server/3004）分阶段退役。
- 核心场景：
  ```
  HA / MagicPush 发送端 / 随手 curl ──► Hotify（唯一入口·唯一库·console 管一切）
                                        │ 广播/定向(slug)
        ┌─────────┬─────────┬─────────┼──────────────┐
        ▼         ▼         ▼         ▼              ▼
    鸿蒙手机   iPhone    Windows   gotify App    hotify-fanout 桥
    (华为PushKit (Bark App (gotify桌面 (Android,      │
    锁屏直达)  改址即可)  客户端)   密码=key1)    ┌───┴───┐
                                                ▼       ▼
                                            Discord  Telegram
  ```
- 全部已批准验收：
  - A. push 栈内 hotify 容器运行（/ping 200、/api/v1/info=v1.2 起步），App Center 版数据（设备/消息/附件/凭据）完整迁移——手机 App 无需重新注册
  - B. MagicPush 链路端到端：原 token 经 nginx 桥转发 → 手机收到（发送端零改动）；无凭证 401
  - C. HA 渠道改配后到达手机（.cfg 热加载）
  - F. hotify-fanout 桥：curl 推一条 → 手机 + Discord + Telegram 全收到（秒级）
  - G. gotify App/桌面客户端改指 Hotify 后收消息正常（真机测试通——老大要求「要测试通」）
  - D. 退役项停用后无功能损失（老大确认），数据备份到 /srv/docker/backups/
- 排除项与最小假设：
  - Hotify v1.4 出站只有设备（Push Kit/Bark App/gotify 客户端/WebSocket），**config 全文已核：无出站 webhook/渠道配置项**——Discord/Telegram 融入 = 外挂桥（本方案 F 阶段），桥的下游配置（Discord webhook URL / Telegram bot token）在桥的环境变量里，**不在 console**——这是当前架构的诚实边界，老大知情
  - 桥 v1 语义 = 转发全部新消息（与现状一致：所有项目渠道本就全量镜像）；按项目过滤留作后续可选
  - TLS 公网加固（8443 明文公网可达）独立后续任务（需域名/证书决策）
  - 3004 的企微/钉钉/飞书/pushplus 方言翻译不在 hooks 能力内（mapping 只做字段提取）；S0.1 确认有用户则 3004 保留
  - key1/slug 等凭证由老大从 Hotify 客户端「设备信息」或 console 提供；实施时写入文件不回显聊天

## 2. 授权、依赖与回退

- 已有任务授权：`ssh nas` 只读访问（已验证）。本方案所有 NAS 写操作待老大批准方案后执行；fnOS 应用中心界面操作（停用/卸载）由老大亲自点。
- 独立闸门：A1 方案 C 批准（含桥设计）、A2 逐项退役批准、A3 App Center 卸载时机、A4 TLS/域名。
- 必要备份与回退：
  - 迁移前整卷 tar 备份 → /srv/docker/backups/hotify-pre-migration-2026-09-09.tar.gz（官方 alpine 打包方式）
  - Phase A 全程 App Center 版只停不卸：新容器异常 → 停新容器 + fnOS 拉回原容器（原卷未动）
  - 老四容器与 3004 在 C/F 验证通过前一律不动；退役前数据备份 → backups/abandoned/
  - apprise .cfg 改动前整目录备份（热加载可秒回退：改回 URL 即生效）
  - 桥独立小容器，故障不影响手机推送；停桥 = 回到无 Discord/Telegram 状态，Discord/Telegram 侧无残留
- 重要取舍（第一性原理 + 对抗性复核）：
  - 目标「一个界面、一套系统」。Hotify 设备/消息模型覆盖手机端全场景；Discord/Telegram 是异构出站——对抗性结论：**Hotify 本体不吸收入站外的出站渠道**，融入只有两条真路：(i) 桥订阅转发（方案 C，老大已表态「想办法融入」）；(ii) apprise 无头后台保留（方案 B 兜底，零开发但 Discord/Telegram 停留在 apprise 域、消息不进 Hotify 历史）。取 C，B 作为桥做不下去时的退路
  - 桥的失败模式：桥挂 = Discord/Telegram 漏收（手机不受影响）——与今天 apprise 挂了等价，风险不劣化；桥无状态 + 断点续传（记 last hlc），重启安全
  - 桥首启跳过积压（首启只记水位不转发）——防迁移后第一波风暴
  - gotify 兼容入口语义 = 广播全部设备（原 gotify App 单收变全设备收）；定向用 bark 路径 /<slug>/——语义变化老大知情
  - HA 侧两步走：C 阶段零 HA 改动（apprise .cfg URL 换 Hotify）；稳定后再选做 HA 原生改造并退役 apprise（D 阶段，可选）
  - 同 tag（v1.2）先行迁移、验证后升 v1.4.2，避免迁移与升级混淆
  - MagicPush 接入走「nginx 桥改转发」而非「发送端直连」：零发送端改动、token 不变

## 3. 实施与短时验证

| ID | 步骤 | 前置依赖 | 5 分钟内的必要验证 | 状态与证据 |
| --- | --- | --- | --- | --- |
| S0.1 | 只读预检：3004 日志 + :8001 nginx 日志 grep 发送方/路径/方言，确定改 URL 范围、有无企微等方言用户 | 无 | 结论写入进度区 | ✅ 完成（详见进度区 13:15） |
| S0.2 | 核验 fnOS hotify 容器名/重启策略（防 stop 复活）、卷名、宿主端口 8443 | 无 | docker ps + inspect | ✅ 完成（hotify-server Exited、unless-stopped 手动停不自启、卷 hotify-server_hotify-data、8443 已空） |
| S0.3 | 从 bridge/3004 日志取一条 MagicPush 真实 payload 样本 | S0.1 | 样本记入进度区 | ✅ 完成（源码级：JSON {title, content\|body\|message}，B 搁置后备查） |
| A1 | tar 整卷备份 hotify-server_hotify-data → backups/ | S0.2 | tar -tzf 抽检含 hotify.db/blobs/cli-token | ✅ 完成（hotify-pre-migration-2026-09-09.tar.gz 5.7KB，6 项全在） |
| A2 | 建 push/hotify/{data,hooks}；fnOS 容器停用并核验未复活；卷数据拷入 + chown -R 10001:10001 | A1 | 文件清单对照；uid 抽检 | ✅ 完成（data/ 6 文件属主 10001:10001，hotify.db 32768B 与源一致） |
| A3 | push compose 增 hotify 服务：image 同 v1.2、container_name: hotify、端口按老大指示 8002:8443、./hotify/data:/data、TZ、restart unless-stopped | A2 | compose config 语法通过 | ✅ 完成（原 compose 备份 .bak-20260909；healthcheck 留待 E1） |
| A4 | up -d hotify | A3 | /ping 200；/api/v1/info=v1.2；console 设备/消息俱在 | ✅ 完成（/ping pong、v1.2/752d6a22、pushkit 缓存暖启 2 URLs） |
| B1 | 写 hooks/magicpush.yaml：id: magicpush、apiVersion: hooks/v1、verify static-token（header: Authorization，secret 含 Bearer 前缀）、mapping title/body、device ""、examples 用真实样本 | S0.3 | 字段过三关 | ⏸️ 搁置（见进度区 13:15 决策） |
| B2 | compose environment 增 HOOKS_MAGICPUSH_SECRET（从 bridge conf 读原值直填不打印） | B1 | compose config 不回显秘密 | ⏸️ 搁置 |
| B3 | nginx 桥改转发：proxy_pass → http://hotify:8443/hooks/magicpush，Authorization 透传，reload；发送端零改动 | A4,B2 | 日志 [hooks] loaded 1 plugin(s): magicpush；带 token 探针 → 手机收到 | ⏸️ 搁置（:8001 桥实测零流量，发送端全在 :3004；MagicPush 经 3004→apprise→gotify_\*.cfg 改址即可全量进 Hotify） |
| C1 | 老大从客户端/console 提供 key1；apprise .cfg **仅改 gotify_\*（6 个）** → gotify://192.168.1.10:8002/<key1>（广播，消息全量进 Hotify+鸿蒙 PushKit 直达）；bark_\*（7 个）暂留旧 bark-server 待 D2；telegram_\*（历史仅 4 次）与 discord_\*（0 次）暂不动，F 桥承接；cfg 改前整目录备份 | A4, A5(key1) | curl apprise notify 一条 → 手机收到；Discord 无重复 | ✅ 完成（13:25-14:07，14:55 补漏）：key1 由 CLI 直读自取；重置 key1（设备列表空、无依赖，时机完美）；**7 处** gotify 指向切至 8002（6 个 gotify_\*.cfg + textverified.cfg 内嵌 gotify 行——后者 14:55 排查发现补切，grep 全量确认无其他漏网）；端到端实测 apprise→Hotify POST /message 200，消息 hlc:11723958... 落库可查；cfg 整目录备份 apprise-config-pre-c1-2026-09-09.tar.gz；apprise-configs.md 已加迁移状态 banner |
| F1 | ~~写 hotify-fanout watcher~~ → **老大批准改方案 A：apprise 多腿合并**（凭证在 cfg 里现成，路由=键名天然精准，零新服务）| C1 | 合并后 cfg 行数/schema 正确 | ✅ 完成（19:08 老大批准方案 A）：侦察发现 Discord 3 webhook（BTC-5M/设备监控/NAS监控频道）+ Telegram 1 bot 1 群 4 topic（btc=2/device=7/general=-/nas=20264），路由信息天然在 apprise 键名里。apprise 日志查实无发送端推 telegram/discord 键（仅 2 次 405 GET 浏览器试探）→ 合并无双发风险 |
| F2 | 合并执行：gotify_btc/device/nas.cfg 各并 discord+tgram 行（3 行）；bark_general.cfg 并 tgram 行（2 行）；7 个旧键 .cfg 退役移出（凭证归档 backups/retired-legs-20260909）；cfg 整目录备份 | F1 | 行数验证 + 旧键失效 | ✅ 完成（19:11）：备份 apprise-config-pre-f-merge-2026-09-09.tar.gz；合并后 4 文件 schema 正确（gotify_btc=[gotify,discord,tgram] 等）；7 .retired 移入 backups；config 目录 14 活跃键 |
| F3 | 端到端：gotify_nas 一次推送 → 四端到齐（Android+iPad+Discord+Telegram topic 20264）；bark_general → Hotify+Telegram；三路并发 <1s | F2 | 四端全到，无重复 | ✅ 完成（19:12）：gotify_nas Loaded 3 entries → Sent Gotify/Discord/Telegram 全绿 + bark-apns ✓×2；bark_general Loaded 2 → Sent JSON/Telegram。代理走 mihomo（Discord/Telegram 被墙，apprise 容器 HTTP_PROXY 已配）。**F 阶段收官** |
| G1 | gotify 客户端改指 Hotify：Android App（地址=Hotify，用户名任意，密码=key1）/ 桌面（浏览器开 /gotify/<key1> 注册接收设备，uuid 立即粘贴勿刷新，多余 console 删） | A4 | 客户端收到测试消息（V-G） | 未开始 |
| D1 | 退役 gotify 容器（数据备份→backups/） | G 验证 + A2 | 复测无损失 | ✅ 完成（15:41-15:49，老大批准）：退役前检查 48h 日志无直连脚本（唯一流量=老大 15:26 的 web 会话）、cfg 零引用 8081；数据 tar → backups/gotify-pre-retire-2026-09-09.tar.gz；stop+rm 容器；compose 摘除 gotify 块（.bak-20260909-d1）；验证：8081 DEAD、8002 pong、8000 200、8082 pong，compose config -q 通过。回滚=恢复 .bak + up -d（数据目录 ./gotify/data 未动） |
| D2 | 退役 bark-server（前置 iPhone Bark App 改指 Hotify 并测试） | A4 + A2 | iPhone 锁屏收到 | ✅ 完成（17:00-18:12，老大批准）：① 老大 iPad Bark App 添加 Hotify:8002 → 设备「ipad」注册（APNs token 收录，v1.2 bark 兼容实测可用）② **发现 apprise bark:// 与 Hotify 协议不兼容**：apprise bark 插件写死 `POST /push`（Bark-API V2，device_key 在 body），Hotify 是路径式入口（`POST /<key1>`），400 "Unsupported Parameters" 是 apprise 自报，请求根本没到 Hotify。**修复**：7 个 bark_\*.cfg + textverified 的 bark 行全部改 `json://192.168.1.10:8002/<key1>?-group=X&-sound=Y`（json schema 发 POST path + JSON {title,message}，`-`参数透传为 query，group/sound 保留）③ 全量 9 条测试 iPad/Android 双端收到（10 条 bark-apns ✓）④ stop+rm bark-server，compose 摘除（.bak-20260909-d2），8082 DEAD、8002/8000 存活。bark 数据已备份 backups/bark-pre-d2-2026-09-09.tar.gz。**教训**：apprise bark:// ↔ Hotify 不兼容，用 json:// 替代 |
| D3 | ~~退役 3004 宿主进程~~ → **保留**（老大裁定：3004 是飞书/方言翻译器， MagicPush /feishu/{key} 路由在其上，不退） | — | — | ✅ 裁定保留（18:36）：不退役。✅ @reboot 不自启已修复（20:37-20:42，老大批准任务级 root SSH，走今天登记的 `ssh root@nas` 免密入口）：迁移为 systemd `push-translator.service`（User=nasuser、WorkingDirectory=push/apprise、Restart=always、enabled+active）；nasuser crontab 旧 @reboot 行已清（其余 5 条完好：chromium/adb/horizon×3）；根因=fnOS 不执行用户 crontab 的 @reboot（今日 09:31 开机、09:59 才有人工拉起=28 分钟空窗实证）；E2E 复测 3004→apprise→Hotify 通过 |
| D4 | ~~（可选）HA 原生 notify 改造 + 退役 apprise 容器~~ | — | — | ⚠️ **修正（21:20 勘误）**：原"automations.yaml 0 次调用=死配置"结论**错误**——grep 引号经 pwsh→ssh 转义损坏（`\\|` 变字面量，无交替语义）导致漏配。实况：`auto_close_ev_bike_0800`、`battery_full` 两个自动化**真实调用** unified_notify（活配置）。处理：apprise 容器不退役（裁定不变）；HA 配置改单键扇出（见 H1） |
| H1 | HA 通知单键扇出改造：unified_notify 四开关四渠道 → 单键 gotify（=gotify_device）+ 单总开关；修 "appprise" 拼写 | D4 勘误 | 双端不重复响铃 + HA 重启日志干净 | ✅ 完成（21:09-21:24，老大"搞定遗留问题"）：备份 ha-config-pre-notify-update-2026-09-09.tar.gz；configuration.yaml 换单键映射（channels: gotify: gotify_device）+ 四开关并一（apprise_notify_gotify「统一通知」）+ scripts.yaml unified_notify 单分支；HA 重启加载干净（Bluetooth NET_ADMIN 报错为容器历史问题）；修复双重响铃缺陷（改造前 gotify_device+bark_device 双腿都落 Hotify）。⚠️ dashboard 旧 discord/telegram/bark 开关卡片不可用，UI 手动清理 |
| H2 | 3004 渠道前缀：经 3004 的消息标题自动加 `[key]`，Hotify 控制台可见来源渠道；空标题取 body 首行（≤40 字） | 老大 21:26 需求 | 三类路由前缀全落库 | ✅ 完成（21:33-21:36）：_forward() 统一注入覆盖全部 5 路由（两种 GET、/api/push、四方言）；实测 `[gotify_horizon] 测试`、`[bark_general] 没有标题时取body首行`、`[gotify_deepseek] 方言路由也要有前缀` 三形态正确；systemd 重启上线。覆盖 99%（2805/2869 走 3004；直推 8000 的 python3.14 发送端无前缀，可后续迁移） |
| H3 | **TG/Discord 桥设备化**（老大 21:26 提议 + 21:44 批准）：注册为 Hotify 占位设备（设备页四席），WS 流收广播 → 按标题 `[key]` 前缀路由 → TG topic / Discord 频道；apprise 多腿退役 | H2（前缀即路由信号） | 键 e2e 四端同收单条无重复 | ✅ 完成（21:44-22:08）：①可行性验证：假 push_token 注册 windows 占位设备 OK，每广播 1 行 `unknown platform: windows` warning（预期噪音）②提取脚本解析 4 多腿 cfg（tgram topic 在 URL query `?topic=N`，首版解析器丢 query 已修）→ 桥 config.json（0600）③桥 ~250 行纯标准库（自实现 RFC6455 WS：握手/masked 发送/分帧/ping-pong/重连退避）④踩坑：注册 401 忘带 Bearer→补；Discord 1010 Cloudflare 拦 Python UA→加浏览器 UA；**首连无 since 重放全量历史**（与 API.md 文档相悖）→ts>now-300s 过滤防轰炸；state.json 双线程写竞态→全局锁⑤systemd push-bridge 上线；终验 gotify_btc/device/nas e2e TG+Discord `sent` 全绿，设备页 Telegram/Discord **online**。**F 方案 A 多腿被桥取代**（cfg 备份 retired-legs-bridge-20260909）。源码 NAS `…/push/bridge/hotify-bridge.py`（本地 tools\hotify-bridge.py） |
| H4 | **HA 四开关定向 + python3.14 发送端迁移 + 矩阵页**（老大 22:29 三项需求）：①python3.14 直推 8000 发送端查明=HA 容器自身（host-net），迁移=集成出口改 :3004/notify/{key}；②HA 旧 4 开关卡片失效修复→4 个 per-channel 定向键（ha_android→ACE/ha_bark→iPad/ha_tgram→TG topic7/ha_discord→#device），TG/DC 用现有频道（老大 22:54 选定）；③3004 首页=频道×设备路由矩阵页（点格看推送地址，TG/DC 直推 URL 掩码） | H1-H3 | 4 开关独立隔离 e2e + 矩阵页 8 频道数据正确 | ✅ 完成（22:29-00:04）：①**查明 python3.14=HA 容器**（apprise_notify 组件 urllib）；②Hotify 定向语义启用：slug 端点 `PUT /api/v1/devices/{uuid}/slug` 设 tgram/discord；**WS 流按设备投递实证**（tgram 定向→桥转发 ✓，ACE 定向→桥静默 ✓）——故 14 业务键保持广播（桥路由依赖广播），仅新增 ha_* 4 键定向；③apprise 建 4 cfg（ha_android 用 ACE uuid——slug "gotify" 路径歧义 404）；④桥 routes + ha_tgram/ha_discord（复用 gotify_device 目标）；⑤HA configuration.yaml（apprise_url :3004 + channels 4 键 + 4 input_boolean 实体名复旧）+ scripts.yaml（unified_notify 去掉 channels/if 分支，组件按开关自动过滤）；⑥e2e：4 键全 200、定向落库 target_profile_id 各自命中、TG message_id 20788、Discord sent；开关恢复态 gotify/bark=on telegram/discord=off（旧状态延续）；⑦3004 v2：+/notify 路由 + / 矩阵页 + /matrix.json（实时读 cfg+桥配置；textverified 自成频道、ha_* 独立表）；全路由回归冒烟绿。⚠️ 发现**成对双投**（C1 起发送端 gotify_X+bark_X 同秒双推→广播语义下双端各 2 份，见 runbook §12 待老大裁定）。⚠️ TG/DC token 曾两次在终端输出全文（19:12、23:26），可 regenerate |
| D5 | 老大在 fnOS 界面卸载 App Center hotify-server | A-F 验证 + A3 | 新容器照常 | ✅ 完成（18:36 老大手动卸载）。新容器 hotify 8002 正常（退役前后 pong 均通） |
| H5 | **端口拆分 + 文档标签页 + 成对双投修复**（老大 01:30 四项需求）：①弹窗居中+✕ 关闭；②3004/2999 拆分——3004 公网映射仅推送路由（页面 404），2999 内网展示（矩阵+文档双标签，老大 02:12 澄清「把服务和页面拆开」）；③推送相关 md 整理入网页（新标签页，与矩阵表分开）；④成对双投修复（方案 C 单键化）：horizon 单 URL gotify_horizon、VPS discord.enabled=false 留 gotify_btc | H4（矩阵页/前缀/桥就绪） | 2999 页面/矩阵/文档/弹窗正常 + 3004 页面 404 推送路由活 + 双投源单键化 | ✅ 完成（01:40-02:30）：①universal-push-proxy.py 重写为双端口同进程（ThreadingHTTPServer×2，PushHandler/PageHandler 按角色分派；systemd ExecStart 改 `3004 2999`）；②弹窗 CSS position:fixed inset:0 margin:auto 居中 + ✕ 按钮关闭；③2999 页面双标签（推送矩阵/推送文档），文档侧栏+正文区，服务端 stdlib md→HTML 渲染（标题/表格/代码块/列表/链接/引用），/docs.json+/docs/&lt;file&gt; 路由，NAS `…/apprise/docs/` 三份 md 同步；④horizon compose HORIZON_WEBHOOK_URLS 双 URL→单 gotify_horizon（容器未运行，启动即单份）；VPS webhook-config.json discord.enabled=false（保 gotify_btc，3004 公网 URL 不变）。验证：2999 health/页面/矩阵 200、docs.json 列 3 文档、3004 health 200+页面 404+推送路由活（400 missing body 探测）+公网发夹 404、hotify 无新推送打扰 |
| H6 | **渠道分离（一键=一渠道，全定向）**（老大 09:15-09:37 晨间需求：[gotify_btc] 不应到 bark、`[bark_btc]`/`[telegram_btc]`/`[discord_btc]` 各自独立、地址唯一、发哪里老大自己定、TG/DC 新频道等点名）：①apprise 25 键全部定向化——gotify_X（6）→ACE uuid、bark_X（7）→brak（分组/铃声保留）、新增 telegram_X（4：nas/btc/device/general）→tgram、discord_X（3：nas/btc/device）→discord、textverified 去重留唯一广播腿（测试键一键四端）；②桥 routes 重构：telegram_*/discord_* 替代 gotify_* 扇出（删 gotify_nas/btc/device、bark_general 死路由，保 ha_tgram/ha_discord）；③2999 矩阵页适配（telegram_/discord_ 键族入列）+ 弹窗新增飞书兼容 webhook 地址（公网 nas:3004/feishu/&lt;key&gt;、内网 192.168.1.10:3004/feishu/&lt;key&gt;，点格即复制）+ 消息历史标注改 cfg 实时解析（定向·安卓 ACE/iPad Bark（X 分组）/TG 桥/DC 桥）；④VPS/发送端不动——老大自填地址 | H5（双端口/消息历史页就绪） | 矩阵 8 行四列对齐 + 四键实测各只到一端（gotify_btc→安卓、bark_btc→iPad、telegram_btc→TG topic2 桥日志 message_id=20792、discord_btc→#btc）+ 消息历史新键标注正确 | ✅ 完成（09:37-10:00）：cfg 重写 18→25 键（备份 `backups/cfg-bridge-pre-H6-20260910-0946.tar.gz`）；bridge config.json 路由改造（第一次误把数组当 dict 赋值、已从备份恢复重做）；matrix_data 支持 4 键族 + _cell 增 feishu 地址（TG/DC 手工格子补 feishu 字段两轮）；踩坑：新 cfg root 属主致 nasuser 服务读不到（chown 归一）；成对推语义=多渠道推多键、每端恰好 1 份（飞牛 NAS 登录通知 bark_nas+gotify_nas 成对实证正确投递）；horizon/codex/deepseek 的 TG/DC 列留空待老大点名新增 |
| H7 | **渠道分离收尾：ha_* 退役 + 安卓过滤代理 + 设备清理**（老大 10:40-11:30 三项需求）：①ha_* 4 键删除（老大明示「不需要重新新建，直接引用 device 四渠道」）——HA 四开关改引用 gotify_device/bark_device/telegram_device/discord_device（地址定死），桥 routes 删 ha_tgram/ha_discord（余 7 路：telegram 4 + discord 3），25→21 键；②未命名 bark 设备删除（09:14 经公网 203.0.113.10=老大 iPad 蜂窝重注册产生的重复项；10:40 巡检 7 条 bark 全走 ipad/brak 实证旧项为活设备，未命名项零投递）；③**Hotify gotify 兼容层缺陷修复**：GET /message 与 WS /stream 不按 token 过滤（任意 token 返回全局消息流）→ 安卓 gotify App 收全部 20 键消息——v1.4.2 闭源无配置项、hooks 插件为入站方向不适用 → push-translator 加第三端口 **:8003 ACE 过滤代理**（AceFilterHandler：/message 代理+标题过滤、/stream WS 101 转接+帧过滤、admin 一律 404；过滤规则=只丢 [bark_*]/[telegram_*]/[discord_*]/[ha_bark/tgram/discord] 跨渠道标签，其余全保留防误伤历史）；消息历史 30 条倒序（老大 10:21）同版落地 | H6（一键=一渠道就绪） | 8003 /message 100→73 条零泄漏 + WS 实测 [gotify_nas] 帧到/[bark_nas] 帧滤 + 矩阵页 HA 表移除 + 2999 三标签正常 + HA 四开关地址由老大在 HA 侧更新 | ✅ 完成（10:45-11:45）：cfg 删 4 键（21 键）、bridge routes 7 路、systemd ExecStart 改 `3004 2999 8003`；WS 帧过滤为 stdlib 手写（帧解析+重封装，只处理 fin=1 text 帧其余透传）；安卓 App 需改连 `http://192.168.1.10:8003`（token 不变）；发现 8002 公网暴露任何人可拉全部消息（token 随便填都过），已向老大建议关闭 8002 公网映射 |
| E1 | image 升 v1.4.2 → up -d | A-F 稳定 | /api/v1/info + 三端复测 | ✅ 完成（19:30-19:35 老大批准）：数据备份 hotify-data-pre-e1-2026-09-09.tar.gz；compose tag v1.2→v1.4.2（.bak-20260909-e1）；up -d 重建容器；验证：/api/v1/info=v1.4.2 (b8a5f915)、ping pong、四端复测全绿（Gotify/Discord/Telegram Sent + bark-apns ✓）、设备 ipad+ACE 保留、消息库完整。**v1.4.2 改进**：bark group 参数映射为 category 字段，消息分类可查 |
| E2 | TLS/EXTERNAL_URL 加固（配后 bark/gotify 客户端通知显图、附件可点） | A4 决策 | https + 手机复测 | 未开始 |

受阻时继续其他前置满足的工作；长验证只引用最后部分的 V 编号。

## 4. 进度与交付记录

- 2026-09-09 11:23：建方案（方案 B/A 二选一框架）。
- 2026-09-09 11:42：按老大三问改写为方案 C：①Discord/Telegram「想办法融入」→ 桥接容器 hotify-fanout（消息先进 Hotify 再转发，console 成为总界面）；②锁屏 vs Bark 已解答（华为 Push Kit vs Apple APNs，体验等价，iPhone 装 Bark App 改址即得）；③gotify「保持唯一可重构要测试通」→ 客户端不换 App 只改地址（官方兼容：Android 密码=key1；桌面 /gotify/<key1> 注册），容器退役，验收 G 真机测试。文档核验补全：README 兼容章（bark 四段路径/gotify 三种凭证/ntfy PUT/广播入口/gotify 客户端接入法）、config.example.yaml 全文（确认无出站渠道配置——桥为唯一融入路径）、v1.4.2 为当前版。
- 2026-09-09 12:49-13:09：**老大批准方案 C（「可以，去改造吧」）并已自行停用 App Center 版；端口指示「8000 附近」→ 选定 8002（8005 已被占）**。执行 Phase A 迁移全绿：S0 预检（fnOS 容器 Exited 确认、unless-stopped 手动停不自启、卷名/端口核验）→ A1 整卷 tar 备份 → A2 数据拷入 push/hotify/data（uid 10001:10001，hotify.db 32768B 字节级一致）→ A3 compose 增 hotify 服务（8002:8443，原 compose 备份 .bak-20260909，healthcheck 留待 E1 升级时加）→ A4 up -d 一次成功（/ping 200 pong、/api/v1/info=v1.2/752d6a22、pushkit 缓存暖启 2 URLs、监听 8443）。旧容器 hotify-server 保持 Exited 作回滚，老卷未动。
- 2026-09-09 13:13-13:15：**S0.1 流量证据落定，架构按证据微调**：①apprise 2869 次 notify 中 bark_\* 1445 + gotify_\* 1419（99.7% 成对手机推送），discord_\* 0 次、telegram_\* 仅 4 次——Discord/Telegram 现状几乎无真实使用，F 桥「全量转发」无按项目分流的顾虑，方案 C 的 F 设计成立；②所有 MagicPush 流量经 :3004（2805 次，UA Python-urllib/3.11），另有独立 python3.14 发送端直连 apprise:8000（40 次，今天 08:00 仍在推，将来退役 apprise 时需定位改造）；③**:8001 nginx 桥实测零流量 → B 阶段（hooks+nginx 改造）搁置**——MagicPush 经 3004→apprise→gotify_\*.cfg 改址即可全量进 Hotify，发送端/nginx/3004 零改动；④现行 apprise 有 22 次 424 Failed Dependency（投递失败，承接后排查）；⑤:8000 与 :3004 均公网暴露（DDNS 可达，扫描器活跃）——安全收敛项记入 D/E。
- **C1 执行范围（据 cfg 实况收窄）**：仅改 gotify_\*.cfg（6 个）→ gotify://192.168.1.10:8002/<key1>；bark_\*.cfg（7 个）暂留旧 bark-server（iPhone 现链路不动，D2 时随 Bark App 改指 Hotify 一并切换）；telegram_\*/discord_\* 几乎无流量不动。待老大提供 key1 即可执行。
- 2026-09-09 13:21-13:36：公网链路三测全通（本机/NAS 回流/外部节点 → `push.example.com:8002` pong；路由器 8002 映射已存在，无需新规则）。老大问密码：Hotify v1.2 密码即 key1（CLI `show password` 直读），fnOS 向导从未生成过密码、设备列表为空（手机仅轮询、PushKit 锁屏从未开通）——**key1 已重置**（旧值在聊天中出现过，轮换是卫生；当时无任何 cfg 依赖，时机完美），新值交老大用于 console/手机 App。
- 2026-09-09 14:04-14:07：**C1 完成**：apprise config 整目录备份（apprise-config-pre-c1-2026-09-09.tar.gz，21 cfg + store）→ 6 个 gotify_\*.cfg 覆写为 `gotify://192.168.1.10:8002/<key1>`（含 gotify_horizon，比预估的 6 个前缀清单多一个项目）→ 端到端实测：apprise 热加载（Loaded 1 entries from memory://）→ Hotify POST /message 200（184ms）→ /api/v1/messages 查到落库消息（hlc:117239580888203264，title/body 完整）。**发送端/HA/3004/nginx 全零改动，消息从现在起全量进 Hotify。** 待手机改址+key1 后真机验证（V2）。
- 关键结论：Hotify 吃掉 gotify（协议兼容+客户端直连）、bark-server（Bark App 改址）、magicpush-bridge（改转发目标）、3004（MagicPush 职能）；Discord/Telegram 经桥融入；apprise 在 C 阶段降级为无头 URL 路由、D4 可选退役。
- 2026-09-09 21:09-21:24：**H1 完成**（HA 单键扇出，细节见 H1 行）。
- 2026-09-09 21:26-22:08：**H2/H3 完成**（老大两项新需求：3004 渠道前缀 + TG/Discord 桥设备化；细节见 H2/H3 行）。桥上线后 **F 方案 A 多腿退役**——扇出引擎从 apprise cfg 移到桥，架构演进为：apprise 纯键路由 → Hotify 广播 → 桥按 [key] 前缀扇出 TG/Discord。
- 2026-09-09 22:15-22:18：**文档收官**：runbook notification-system.md 终态重写（全新架构：桥、前缀、单键 HA、回滚锚点）；apprise-configs.md 终态重写（桥路由表、14 键 Hotify-only、排错表加 1010/thread not found/无前缀条目）；D4 勘误 + H1/H2/H3 入表。遗留：dashboard 旧开关卡片 UI 清理（老大手动）、python3.14 直推发送端迁移 3004（后续）、E2 TLS（老大裁定不做）
- 2026-09-09 22:29-00:04：**H4 完成**（老大 22:29 三项需求全落地：python3.14 发送端迁移 + HA 四开关定向修复 + 3004 矩阵页；细节见 H4 行）。**H1 单键扇出被 H4 取代**（单开关→4 开关定向）。文档二轮收官：runbook §5/§12 重写、apprise-configs 变量表 18 键。待决：成对双投（runbook §12）、TG/DC token 轮换建议、E2 TLS（老大裁定不做）、V3 HA 真机验证（老大面板触发）
- 2026-09-10 00:05-01:20：**H4 尾巴 + 矩阵页 bug 修复**：矩阵页 TG/DC 列空白（onclick 模板串缺闭合单引号→浏览器引号错配吞掉后两个 td）——补引号 + b64 重部署 + 浏览器验证 8 行×4 列全渲染；PROJECT.md 01:15 记录
- 2026-09-10 01:30-02:30：**H5 完成**（老大睡前四项需求，自主执行）：①弹窗居中+✕；②端口拆分（02:12 老大补充澄清「把服务和页面拆开」：3004 公网映射只留推送路由，2999 新增内网展示端口——隐私页面不再公网可达）；③推送文档标签页（三份主 md 同步 NAS `…/apprise/docs/`，服务端渲染）；④成对双投修复（方案 C）：horizon compose 单 URL（gotify_horizon）、VPS webhook-config discord.enabled=false（留 gotify_btc）。老大睡前指示「做完再说、明天讨论、不再问」——全程零测试推送，被动验证（路由探测 400/health/页面渲染/公网发夹）

## 最后部分：用户触发的验证与待授权事项

### A. 超过 5 分钟的实践验证

| ID | 对象与通过证据 | 前置 | 耗时/窗口 | 最早验证时间（Asia/Singapore） | 到点读回或用户触发启动 | 阻塞步骤 |
| --- | --- | --- | --- | --- | --- | --- |
| V1 | 手机 App 轮询重连：新容器日志出现手机 IP 的 /api/v1/messages 轮询 | A4 + 手机改址（App 服务器地址改 http://<内网IP或DDNS>:8002；公网轮询需同步改路由器映射 8443→8002——老大操作） | ≤5 分钟轮询周期 | 手机改址后即时 | 到点读回 | 后续全部 | ✅ 完成（15:17）：设备 ACE·gotify 注册（uuid 73f91af8...）；gotify App 经 DDNS 回流 203.0.113.10 拉 /message、/application/1/message 全 200；console 登录成功（192.168.1.20）。路由器 8002 映射原已存在，未动 |
| V2 | MagicPush 端到端：老大在原发送端发一条，手机收到（发送端零改动，经 3004→apprise→gotify_\*.cfg→Hotify） | C1 | 1 分钟 | C1 完成后 | 用户触发 | D3 | 🔄 测试消息已推（15:20，apprise Sent→POST /message 200），待老大手机端确认显示 |
| V3 | HA 各渠道通知：HA 面板逐项目触发，手机收到且 Discord 无重复 | C1 | 3 分钟 | C1 完成后 | 用户触发 | D1-D4 |
| V-F | 桥三端同达：curl 一条 → 手机+Discord+Telegram；桥重启断点续传不重发 | F3 | 2 分钟 | F3 完成后 | 用户触发 | D4 |
| V-G | gotify 客户端改址后真机收消息（老大要求「测试通」） | G1 | 5 分钟 | G1 完成后 | 用户触发 | D1 |
| V-B | iPhone Bark App 改址后锁屏收到（若 iPhone 在用） | D2 前 | 2 分钟 | A4 后任意 | 用户触发 | D2 |
| V4 | v1.4.2 升级后稳定性（推送/轮询/console） | E1 | 1-2 天自然使用 | E1 后 | 用户触发 | E2 |

### B. 无法替代的授权或外部前置

| ID | 对象/所需决定 | 现有授权为何不足或环境缺口 | 受阻步骤 | 其他已完成工作 |
| --- | --- | --- | --- | --- |
| A1 | 方案 C 批准：桥设计（转发全部新消息、桥侧配置 Discord/Telegram 凭证、console 管不到桥下游——知情确认） | 桥为新增自研组件，需老大点头其设计边界 | F1-F3, C1 | Phase 0/A/B/G 与此独立可先行 |
| A2 | 退役逐项批准：gotify、bark-server、3004+crontab、（可选）apprise | 不可逆处置需点名；S0.1 结论影响范围 | D1-D4 | 备份/迁移/桥可先行 |
| A3 | App Center 卸载 hotify-server 时机（fnOS 界面，老大亲自点） | 界面操作无法代行；卸载可能删旧卷 | D5 | A1 备份后即具备条件 |
| A4 | TLS/域名：8443 公网明文（手机公网 IP 在轮询）；EXTERNAL_URL 配后 bark/gotify 客户端通知显图 | 需域名与证书方式信息 | E2 | 不阻塞其他阶段 |
| A5 | 实施时提供 key1/slug（从 Hotify 客户端设备信息页或 console 复制）；Discord webhook URL / Telegram bot token+chat_id（桥 env 用） | 凭证只能由老大提供；聊天中不回显 | C1, F2 | A4 前可先做 |

本部分之后不追加实施阶段；后续事件写回进度区。
