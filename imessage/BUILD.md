# 构建与准备（iMessage 侧）

本仓库**不包含**两类不可公开的材料：

| 材料 | 为什么不在仓库里 |
| --- | --- |
| `imessage/bin/imessaged`、`imessage/bin/imsg`（37 MB / 8 MB） | 体积大、且必须与运行环境 glibc 匹配；按下面的步骤自己编 |
| `imessage/apple-data/imessage-linux/IMDAppleServices`、`cert-1.0.plist` | Apple 私有框架二进制，**版权归 Apple**；本项目只记录来源与哈希，由使用者自行获取 |

---

## 1. 编译 `imessaged` / `imsg`

上游：<https://github.com/thescruggs/imessage-linux>（引擎为 <https://github.com/OpenBubbles/rustpush> 分支）。

本次验证过的版本组合（2026-10）：

| 仓库 | 固定版本 |
| --- | --- |
| `thescruggs/imessage-linux` | `5f10c5fee4c13ab296bf7b4546c8e97f9a1dcb1e` |
| `rustpush` | `f35c4ee062b3c3eae54dc96b89b90ee99f5e1d0c` |
| 其它子模块 | apple-private-apis `b8598d2c` · rtc `87589665` · rustls `cdb821cf` · rustls-platform-verifier `2c5b3b01` · quinn `bb2c42e0` |

### 1.1 环境

- **Ubuntu 24.04（glibc 2.39）**：编译机与运行机用同一发行版最稳；成果物要求 **GLIBC ≤ 2.39**，直接丢到 glibc 2.36 的宿主上跑会 `version 'GLIBC_2.39' not found`。
- `rustc 1.99.1`（`rustup toolchain install stable --profile minimal`）
- 依赖：`build-essential pkg-config libssl-dev clang libdbus-1-dev zlib1g-dev`

```bash
export CARGO_NET_GIT_FETCH_WITH_CLI=true          # 否则 cargo 可能卡在 git 依赖
export CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse
```

### 1.2 取源码 + 打补丁

四个补丁是这套方案的关键（缺任何一个都跑不通），仓库里放在 `patches/`：

| 补丁 | 作用 |
| --- | --- |
| `patches/apple-private-apis/01-local-anisette.patch` | 让 anisette 走**外部命令**（真 Mac）而不是公共服务器 |
| `patches/rustpush/01-rustpush.patch` | 引擎适配（本地 NAC / 配置项） |
| `patches/rustpush/02-open-absinthe-path.patch` | 指定 `IMDAppleServices` 的本地路径 |
| `patches/rustpush/03-phone-handle.patch` | 规避 IDS 注册 6001（handle 处理） |

```bash
git clone https://github.com/thescruggs/imessage-linux.git && cd imessage-linux
git checkout 5f10c5fee4c13ab296bf7b4546c8e97f9a1dcb1e
# 子模块（注意：部分子模块的 .gitmodules 用的是 git@ 形式）
git -c url.https://github.com/.insteadOf=git@github.com: submodule update --init --recursive
# 打补丁（路径用绝对路径，避免 -C 找不到文件）
git apply /path/to/patches/apple-private-apis/01-local-anisette.patch
git apply /path/to/patches/rustpush/01-rustpush.patch
git apply /path/to/patches/rustpush/02-open-absinthe-path.patch
git apply /path/to/patches/rustpush/03-phone-handle.patch
```

> 遇到 `patch …/quinn does not contain packages matching quinn`：说明子模块文件被暂存删除，
> 逐个 `git -C <submodule> reset --hard <上面记录的 sha>` 再打补丁。

### 1.3 编译

```bash
cargo build --release --bin imessaged --bin imsg     # 首次约 30–40 分钟（4 线程）
ls -l target/release/imessaged target/release/imsg
```

放到本仓库：

```bash
install -m 755 target/release/imessaged  <repo>/imessage/bin/imessaged
install -m 755 target/release/imsg       <repo>/imessage/bin/imsg
```

已知产物指纹（仅作「编出来的东西对不对」的一致性参考，不是信任根）：

```
imessaged  sha256 ca8b4f2e709722538ebf1d3a1ed4fe1bd9de1d67629a27afbda260fa2fa3cf14
imsg       sha256 a054b87fa91623aa89fecfcd18069a22398795fb6c517fcb5a81e9c6cde3a6b5
```

---

## 2. 准备 Apple 的两个文件

程序运行时要读 Apple 私有框架 `IMDAppleServices`（在本地 unicorn 里模拟执行，**不原生运行**）和它的证书描述文件。

| 文件 | 来源 | 校验 |
| --- | --- | --- |
| `IMDAppleServices` | 社区转储（如 `JJTech0130/nacserver` 仓库内的同名文件）或自 Apple 固件提取 | sha1 `e1181ccad82e6629d52c6a006645ad87ee59bd13` · **sha256 `74c2a8fe826a478f14f6e17b3709a8b315e8c0e0e34e3fba6b9c4eee2f4516e9`** · 6,222,784 B |
| `cert-1.0.plist` | Apple 明文 HTTP：`http://www.apple.com/appleca/cert-1.0.plist`（或同社区仓库） | sha256 `6ef3521ed899be820e132a6d8c53cac955267c2c3f862684477524be91dc3a06` · 3,474 B |

放置：

```bash
install -d -m 755 <repo>/imessage/apple-data/imessage-linux
install -m 644 IMDAppleServices  <repo>/imessage/apple-data/imessage-linux/
install -m 644 cert-1.0.plist    <repo>/imessage/apple-data/imessage-linux/
```

校验（与你下载的字节对比）：

```bash
sha1sum   imessage/apple-data/imessage-linux/IMDAppleServices
sha256sum imessage/apple-data/imessage-linux/IMDAppleServices imessage/apple-data/imessage-linux/cert-1.0.plist
```

> **版权提示**：`IMDAppleServices` 是 Apple 的私有二进制，不属于本项目的授权范围。
> 仓库只记录哈希；请自行确认在你所在司法辖区的合法性与风险。
>
> **安全提示**：这个文件不会被原生执行——`crates/open-absinthe` 把它平铺映射到地址 0，
> 所有导入符号都绑到一个 `ret` 桩页，由 Rust 侧应答；除了读取该文件本身，模拟器不访问宿主网络/文件/进程。

---

## 3. Mac 侧（anisette 供给）

需要一台**真 Mac**（登录与极低频静默重登录时用）：

1. `mac-hwinfo.sh -o hwconfig.plist`（`umask 077`，产物 0600）→ 只拷到 NAS，不留副本；
2. `clang -fobjc-arc -framework Foundation -o ~/.local/bin/imessage-anisette imessage-anisette.m`；
3. 生成**专用** ed25519 密钥，把**受限**公钥写进 Mac 的 `~/.ssh/authorized_keys`：

```text
from="<NAS_IP>",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding,command="/Users/<user>/.local/bin/imessage-anisette" ssh-ed25519 AAAA... nas-anisette
```

4. `ssh-keyscan` 钉扎 `known_hosts`；
5. 自检：`ssh -F /secrets/ssh_config anisette-host` 应输出 7 个头，`X-Mme-Client-Info` 的机型与
   `hwconfig.plist` 的 `product_name` 一致。

---

## 4. 验收（缺一不可）

```bash
docker compose up -d imessaged imessage-gw
docker exec imessaged imsg --bus-address unix:path=/bus/bus doctor
# 期望：hwconfig ok / IMDAppleServices (sha1 ok) / cert-1.0.plist (sha256 ok) / anisette: command ok: 7 headers
```

登录（**先确认你接受账号风险，且不要反复重试**）：

```bash
printf '%s\n' "$PW" | docker exec -i imessaged imsg --bus-address unix:path=/bus/bus \
  login --apple-id you@example.com --password-stdin
# 需要 2FA：
printf '%s\n' "$CODE" | docker exec -i imessaged imsg --bus-address unix:path=/bus/bus \
  login --code-only --code-stdin
```

```bash
docker exec imessaged imsg --bus-address unix:path=/bus/bus status
# 期望：connection connected / registered yes / handles … / renews at …
docker exec imessaged imsg --bus-address unix:path=/bus/bus lookup recipient@example.com
# 期望：iMessage
```

到这里就可以用 `tests/` 下的脚本做端到端验证了。
