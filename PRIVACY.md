# 隐私与脱敏说明

这个仓库是**公开模板**：只发布"可复用的骨架"，不发布任何能代表某个人 / 某台设备 / 某个账号的东西。

## 1. 永远不会入库的东西

| 类别 | 具体内容 | 原因 |
| --- | --- | --- |
| 账号会话 | `imessage/state/state.enc`、`state.key` | `state.enc` 内含 `sha256(密码)` = **等价密码**（Apple GSA 的 SRP 直接吃这个哈希） |
| 设备身份 | `imessage/state/hwconfig.plist`、`id_cache.plist` | 机型/序列号/MLB/ROM/主板 UUID；能冒充设备做认证 |
| 消息内容 | `imessage/state/messages.db*`、`imessage/gw/data/queue.db` | 真实短信正文与号码 |
| 通道密钥 | `imessage/gw/gw.env`、`apprise/config/*.cfg` | HMAC 密钥、API token、webhook URL、bot token |
| SSH 材料 | `secrets/`（私钥、`ssh_config`、`known_hosts`） | 能登录那台 Mac |
| Apple 二进制 | `imessage/apple-data/imessage-linux/*` | Apple 版权；只发布来源与哈希 |
| 编译产物 | `imessage/bin/*` | 体积 + 需与目标环境 glibc 匹配 |
| 运行数据 | `bark/`、`gotify/`、`hotify/`、`bridge/`、日志、`*.bak-*` | 设备台账 / token / 号码 |

## 2. 做了哪些替换（类别，不含真实值）

| 类别 | 替换为 |
| --- | --- |
| 发件账号 | `sender@example.com` |
| 其它 handle | `other@example.com` |
| 收件目标 | `recipient@example.com` |
| 手机号 | `+15550100` / `+1 555 0100` |
| 内网地址 | `192.168.1.x` |
| 容器内/宿主路径 | `/srv/push...` |
| 主机名 / 用户名 | `nas` / `mac` / `macuser` |
| Mac 机型与构建号 | `MacModelX,1` / `00A000` |
| SSH 别名 | `anisette-host` |
| 公网域名 | `push.example.com` |
| 端口绑定 | `${GW_BIND:-127.0.0.1}` |

## 3. 提交前自检

真实敏感值**不要写进这个文件**。把它们放进一个被 git 忽略的清单，再用清单去扫：

```bash
# 0) 准备清单（该目录已列入 .gitignore，永远不会被提交）
mkdir -p .private
cat > .private/forbidden.txt <<'EOF'
<发件账号>
<收件目标>
<手机号>
<内网网段>
<宿主路径前缀>
<主机名/用户名>
<公网域名>
<Mac 机型与构建号>
EOF

# 1) 硬编码敏感串（-F 按字面量匹配，一行一个）
git grep -nF -f .private/forbidden.txt -- . ':!.private' || echo "clean"

# 2) 不该被追踪的文件
git ls-files | grep -E 'state/|gw\.env$|apprise/config/|secrets/|\.db$|\.enc$|\.key$|apple-data|imessage/bin/' \
  || echo "no runtime files tracked"

# 3) 语法与配置自检
python3 -m py_compile imessage/gw/gateway.py tests/*.py
docker compose config -q

# 4) 占位符之外是否混入真密钥
grep -rn 'SMS_GW_SECRET=' --include='*.env*' . | grep -v '__GENERATED__' || echo "ok"
```

## 4. 万一误提交了

按"**撤销 + 轮换**"处理，缺一不可：

1. 立刻改代码：从历史中清除（`git filter-repo`）并强推，然后**当作已泄露**；
2. 轮换 `SMS_GW_SECRET` / `SMS_GW_TOKEN`（改 `gw.env` 并重启网关）；
3. 换掉 Mac 侧那把 SSH 密钥（删 `authorized_keys` 里的旧行，重新生成）；
4. `imsg logout` + 用新凭据重新登录（Apple 侧会话作废）；
5. 如果 `hwconfig.plist` 泄露：它在同一台 Mac 上重新生成，并注意该设备的身份已不可信；
6. 如果 `state.enc` 泄露：立刻改 Apple ID 密码（等价密码已外泄，2FA 只能拦住新会话）。
