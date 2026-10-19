# 把 iMessage 接进你自己的推送控制台（2999 页面那一列）

这个目录不是可运行的组件，而是**一份针对"自建推送页 + 路由矩阵"的补丁与说明**。

## 背景

很多自建推送栈会有一个 HTML 控制台，用一张"频道 × 通道"的矩阵告诉你：
某个频道能不能走 gotify / bark / telegram / discord，以及**调用地址怎么拼**（复制即用）。

本仓库的 iMessage 网关希望在这张矩阵里也有自己的一列，于是给那个页面的服务端脚本加了四处改动：

| 改动 | 作用 |
| --- | --- |
| 常量 `IMESSAGE_URL` / `HUB_URL` | 网关自身地址（默认 `:8098`）与推送入口（默认 `:3004`），可用环境变量覆盖 |
| `_cell_imessage()` | 生成该频道的调用方式（公式 / Bearer / Apprise / 飞书 / 直连 / 短信钩子） |
| `_imessage_cells()` | 实时拉取网关的 `/matrix.json`，拿到"有哪些频道、键名叫什么"；**网关离线时返回空，页面照常渲染**（该列显示 ✗） |
| `matrix_data()` / 表头 / `show()` / `render()` | 把 `imessage` 这一列并进数据与前端渲染 |

## 文件

- `0001-imessage-matrix.patch` —— 统一 diff（`a/` `b/` 前缀，`git apply -p1` 或 `patch -p1` 皆可）。
  针对的是**我们自己的**那份 `universal-push-proxy.py`（不在本仓库内），因此补丁不能直接应用到你的代码上。
- `0002-feishu-leg.patch` —— 同上格式：给矩阵加第 6 列「飞书群」，并给脚本加一条**飞书出站腿**（见文末「0002 说明」）。

## 怎么移植

1. 找到你页面脚本里**构建矩阵数据**的那个函数，以及前端**渲染表头/单元格**的那段 JS；
2. 在服务端：加两个 URL 常量 + 两个函数（照 `_cell_imessage` / `_imessage_cells` 抄），
   在 `matrix_data()` 的服务前缀列表里加上 `"imessage_"`，并在每行补 `row["imessage"] = cells.get(ch, {"ok": False})`；
3. 在接口层：让 `GET /push/<key>`、`POST /api/push`、`POST /notify/<key>`、`POST /feishu/<key>`
   能转发到网关（两种做法任选：
   **①** 页面服务直接转发到 `http://<gateway>:8098`；
   **②** 走你现有的 Apprise：加一个 `json://<gateway>:8098/notify/imessage_<channel>` 目标）；
4. 在前端：列数组里加 `'imessage'`，`show()` 加一个分支（照补丁里的 `else if(c==='imessage')` 抄），
   并注意别把"飞书兼容"那两个块重复渲染。

## 两个容易踩的点

| 坑 | 说明 |
| --- | --- |
| 中文乱码 | 网关的 `/push/<key>` 收 **query 参数**，调用方必须百分号编码（`curl -G --data-urlencode`）。把原始 UTF-8 直接塞进 URL 会被 HTTP 服务器按 latin-1 解析 |
| 正文重复一遍 | 如果你的转发层把"首行"同时塞进 `title` 和 `body`，网关侧要做 `title.endswith(body)` 判重（本仓库 `gateway.py` 已处理） |

## 验证

```bash
# 网关自己的矩阵（字段是否齐全）
GW_BASE=http://127.0.0.1:8098 python3 ../../tests/verify_matrix.py

# 页面的矩阵里是否出现 imessage 列
curl -s http://<page-host>:<page-port>/matrix.json | python3 -m json.tool | grep -A3 '"imessage"'
```

---

## 0002 说明：飞书出站腿（2026-10-19）

**为什么要单独做一条腿**：Apprise 没有飞书插件；而矩阵里的「飞书兼容 webhook」（`/feishu/<key>`）是**入站方言**（别人以飞书格式把消息推给我们），方向相反，不能当出口用。

**链路**：

```
POST :3004/push/feishu_<channel>  ->  Apprise :8000（key=feishu_<channel>）
   ->  cfg 腿 json://<host>:3806/feishu_<channel>
   ->  脚本第 5 口 FeishuOutHandler -> open.feishu.cn /im/v1/messages（chat_id=群）
```

**补丁加了什么**：

| 改动 | 作用 |
| --- | --- |
| `_feishu_creds` / `_feishu_token` | 读 `feishu.env`（`FEISHU_APP_ID/SECRET/CHAT_ID/GROUP_NAME`）+ token 进程内缓存（到期前 60s 刷新） |
| `_feishu_text` | 首行强制 `[频道: <channel>] <标题>`，并剥掉上游已加的 `[key]` 前缀（避免重复） |
| `_feishu_send` / `FeishuOutHandler` | 第 5 口收 Apprise `json://` 腿，转成飞书 `im/v1/messages` |
| `_cell_feishu` / `matrix_data` / 表头 / `show()` / `render()` | 矩阵新增 `feishu` 列，弹窗给出 chat_id 与全部地址形态 |
| `main` | 第 5 个端口参数（`3004 2999 8003 3005 3806`） |

**四个真踩过的坑**（移植时按此顺序查）：

| 坑 | 现象 | 处置 |
| --- | --- | --- |
| 端口撞 Docker | 脚本起不来：`Address already in use`，服务反复重启 | 别用 Docker 会 publish 的段（我们实测 3006–3010 全被占）；本补丁用 **3806** |
| 腿里写了 `127.0.0.1` | Apprise 在容器里，loopback 是容器自己 → 连不上 | 写**宿主 LAN IP**（`json://<host>:3806/...`） |
| `cfg` 放错目录 | 推送返回 204，群里一条没有 | 必须落在 Apprise 的 `config/` 挂载目录 |
| 把 204 当成功 | 未知键的 `POST /notify/<key>` 返回 **204 且不报错** | 正常键是 **200 + 141 B**；排错看 Apprise 日志 `Delivered Notification(s) - KEY:` |

**群定位**：纯通知群，不绑任何会话——飞书应用机器人必须留在群里才能发消息，而"免 @ 就回"是**机器人级**配置、不能按群关闭，所以约定该群不发消息即可。