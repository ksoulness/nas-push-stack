# iPhone 通知转发 · 最终形态与 NAS 侧放行/屏蔽（2026-11-17 定稿）

> 前一篇《Mac 控制 iPhone 与通知转发全流程》记录的是「怎么搭」；本篇记录**上线后的固定形态、运维口径与被验证过的事实**，以及本次新增的 **NAS 侧 App 过滤**。
> 手机端只管发，NAS / hw 决定放不放出去。

---

## 1. 最终链路

```
iPhone 捷径「转发手机」                          公网（Cloudflare Tunnel）
  ① 通知触发器（41 个 App）                       │
  ② 文本 = [通知›App][通知]                       │
  ③ URL 编码（输入=②）                            │
  ④ 获取网页内容 GET ────────────────────────────┼─► https://push.example.com/p/<token>/ios2and?body=<③>
                                                  │
                                                  ▼
                              hw nginx :3104 → push-leg-hw :3105 → apprise → hotify
                                                                        ├─ ACE（安卓 gotify 客户端，WS）
                                                                        └─ 飞书群「短信」（im/v1/messages）
```

- **入口**：`GET|POST https://push.example.com/p/<token>/<key>`（token 在路径里，免 `x-push-secret`；手机端只放一个变量也能用）
- **key**：`ios2and`（只投安卓 ACE + 飞书「短信」群）／`and2ios`（只投 iPad）／`ntf`（全设备 + 飞书）
- **两个必须**：`详细信息 → 隐私 → 锁定时允许运行 = 开`；自动化里**不能有任何空筛选**（`标题 包含 标题` 这种会让自动化直接拒跑）
- **iOS 版本**：iOS 27 才有「收到通知」触发器（46 个可用 App 逐个添加，无「全部 App」通配）；iOS 26 只能靠「信息」触发器转发短信

## 2. NAS 侧放行 / 屏蔽（不动手机就能决定谁放出来）

### 2.1 文件（两侧同源，缺一侧就会出现「时而过滤时而不过滤」）

| 文件 | NAS | hw | 作用 |
| --- | --- | --- | --- |
| `mobile-app-allow.txt` | `/vol1/1000/docker/apps/push/apprise/` | `/opt/push/` | 放行名单，**有内容即白名单模式** |
| `mobile-app-block.txt` | 同上 | 同上 | 屏蔽名单，**优先级最高** |

每行一个 App 名（大小写不敏感），`#` 开头为注释。

### 2.2 判定顺序

1. 命中**屏蔽名单** → 丢
2. 放行名单**非空**且不在名单 → 丢
3. 两个文件都空 → **全部放行**（默认状态）
4. **没剥出 App 名**的消息 → 一律放行（防名字词典缺条目导致静默丢消息）
5. **原名与别名都参与匹配**（短信 App：原名 `信息`、别名 `SMS`），不会误杀
6. 改文件 **60 秒热生效**，不重启

### 2.3 命中后的行为

```json
{"ok": true, "filtered": true, "app": "Gmail", "key": "ios2and"}
```

HTTP 200（手机端不报错、也不投递），服务端日志：

```
[mobile] filtered app='Gmail'/'Gmail' key=xxxxxxxx
```

### 2.4 命令

```bash
# 白名单：只放 短信 + Gmail + 微信（两处都要写）
ssh nas "cd /vol1/1000/docker/apps/push/apprise && printf '信息\nGmail\n微信\n' > mobile-app-allow.txt"
ssh hw  "cd /opt/push/apprise && printf '信息\nGmail\n微信\n' > mobile-app-allow.txt"

# 黑名单：屏蔽 Telegram、Bark
ssh nas "printf 'Telegram\nBark\n' > /vol1/1000/docker/apps/push/apprise/mobile-app-block.txt"
ssh hw  "printf 'Telegram\nBark\n' > /opt/push/mobile-app-block.txt"

# 回到默认（全放）
ssh nas "printf '# 留空 = 全部放行\n' > /vol1/1000/docker/apps/push/apprise/mobile-app-allow.txt"
ssh hw  "printf '# 留空 = 全部放行\n' > /opt/push/mobile-app-allow.txt"
```

### 2.5 自测

```bash
curl -s "http://127.0.0.1:3004/p/<token>/ios2and?body=Gmail测试"
# 命中过滤 → {"ok": true, "filtered": true, "app": "Gmail", ...}
# 正常投递 → {"ok": true, "key": "ios2and", "apprise_status": 200, ...}
```

已实测四种情形：屏蔽 `Gmail` → Gmail 被过滤；放行 `Gmail` → Telegram 被过滤、Gmail 放行；放行 `信息` → 短信照常投递；无 App 名的正文照常投递。

## 3. 飞书呈现规则

| 类型 | 首行 | meta 行 |
| --- | --- | --- |
| 短信（`信息`/`短信`/`Messages`） | `[转发自iPhone] （SMS）[发送方]` | `（时间 · 收件 <号码>）` |
| 其它 App | `[转发自iPhone] （App名）标题` | `（时间）` |
| 末行 | — | `（NAS）` 或 `（HW）`（来源主机） |

## 4. 手机端触发器里的 41 个 App（2026-11-17 读取）

信息、微信、Telegram、WhatsApp、Tide、Backpack、Bark、币安、Bybit EU、cashi、ClearScore、dtcpay、Coinbase、Coins、IBKR、Delta、Experian、Gate、iFAST GB、Kraken、Krak、Kraken Pro、Maya、MetaMask、MetaTrader 5、MEXC、Monzo、Yonder、Wise、Zilch、TenPayGo、Starryblu、SafePal、Plasma One、PayPal、Neverless、N26、myEquifax、Gmail、SAVO、Pulsar Money

- **是否转发只由这份清单决定**；服务端 `mobile-apps.txt`（123 条）只是「名字词典」，用来把正文开头的 App 名认出来并写进标题，**不控制转发范围**。
- 加新 App：手机上自己点 `+`；想让标题出现 `（App名）`或让页面同步，把名字追加进 `mobile-apps.txt` 与 `mobile-trigger-apps.txt`。

## 5. 服务端改造清单（全部幂等补丁，marker 可检索）

| marker | 作用 |
| --- | --- |
| `mobile_app_prefix` | 按名字词典最长前缀剥 App 名，标题前置 `（App名）` |
| `mobile_app_alias` | `信息/短信/Messages/SMS → SMS` |
| `mobile_app_filter` | 放行/屏蔽名单（本篇 §2） |
| `mobile_app_filter_alias` | 原名 + 别名都参与匹配 |
| `mobile_title_clean` | 去掉短信短号首行、标题强制单行 |
| `mobile_split_sender` | `[发送方] 正文` → 标题只放发送方，正文完整保留 |
| `mobile_default_body` | 空正文时的降级文案（兜底） |
| `mobile_json_raw` | JSON 体解析失败时原文整段当正文 |
| `meta_sms_only` / `meta_app_no_number` | 只有 `（SMS）` 才带收件号码 |
| `page_ios_push` / `page_ios_trigger_list` / `page_ios_chip_inline_style` / `page_app_filter_line` | 2999 `/push` 页的 iOS 块、App 胶囊、过滤状态行 |

回滚：`cp universal-push-proxy.py.bak-* universal-push-proxy.py && systemctl restart push-translator`（hw 为 `push-leg-hw`）。

## 6. 已退役的东西（别再往回走）

- **Mac 控制手机**：iOS 27 起 `iphone-control` 走原生 CoreDevice HID/AX，不再需要 WDA 与 Xcode；本次收工后开发者模式关闭、Mac 关机、Apple ID 会话删除，**兜里不再留常开 Mac 的依赖**。
- **Xcode / WebDriverAgent**：仅为 iOS 26 阶段签 WDA 而装，已删（Mac 释放约 12 GB），手机上的 `WebDriverAgentRunner-Runner` 已卸载。
- **绝对不要**用 `iphone_press_button lock` 去测锁屏：该机没设自动解锁，锁了就再也远程解不开，只能人工上滑。

## 7. 排障顺序（三分钟定位）

1. `curl -s https://push.example.com/health` → 200 说明隧道通
2. 看 hw：`sudo tail -f /var/log/nginx/push-access.log | grep "/p/"` → 有没有 `GET /p/<token>/ios2and?body=...`
   - 没有 = 手机侧没触发（查自动化开关、空筛选、锁定时允许运行、App 是否在触发器里）
   - 有 200 但没收到 = 看 `docker logs apprise-hw` 的 `Delivered Notification(s) - KEY: ios2and`
   - 有 `filtered` = 被本篇 §2 的名单挡了
3. 飞书侧回读原文：`/tmp/feishu_getmsg.py <message_id>`（message_id 见 `journalctl -u push-leg-hw | grep 'feishu] sent'`）

## 8. 本次收工的验收证据

```
POST https://push.example.com/p/<token>/ios2and  body="[CTExcel] 收工自检"
→ {"ok": true, "key": "ios2and", "apprise_status": 200}
→ apprise: Notifying 2 service(s). → Delivered Notification(s) - KEY: ios2and
   （2 条腿 = 安卓 ACE + 飞书「短信」群）
```
