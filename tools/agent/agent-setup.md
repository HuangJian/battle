# 采样节点搭建手册（sampler-agent）

> 目标：把额外的机器接入 RL 分布式采样队列，作为协调器之外的采样算力。
> 已验证/规划的平台：**Android 平板**（Termux）、**Google Cloud Shell**（cloudflared 隧道）；
> 任何能跑 bun 1.4.x 且满足三道硬门的盒子同理。
> 本文是自足 runbook：按序执行即可，无需其他上下文。仓库约束见根目录 `AGENTS.md`。

## 0. 架构与数据流（谁连谁）

```
协调器（Windows, run_rl.py 常驻）
  │  每轮迭代开始时：读 dist-nodes.json → GET /v1/ping 各节点
  │  → POST /v1/weights 下发权重 → 中央队列派局
  ▼
各节点 = HTTP 服务端（bun tools/agent/sampler-agent.ts --port 8443 --workers N）
  每个任务：GET /v1/task?stage&seed&... → 本地 spawn 一局 headless 游戏
  → 子进程内打 BCV2 容器（v3.6，无 base64）→ 异步模式经 /v1/result 轮询取包
    （旧同步长连接模式保留兼容）→ 协调器落盘 itN/dist/<node>/
```

要点：
- **节点是服务端**，协调器主动连它。节点只需允许入站 8443（或用出站隧道暴露，
  见 §4）。
- **配置动态生效**：协调器每轮 rollout 前重读 `nn-training/dist-nodes.json`，
  加/改节点**不需要重启训练**，下一轮迭代自动生效。
- 任务级超时 900s 由协调器兜底回队重试；协议自带 20s 心跳字节防中间设备
  空闲回收连接——长任务可安全穿过反向代理。

## 1. 三道硬门（任一不过，节点当轮被剔除）

| 门 | 要求 | 当前基准 |
|---|---|---|
| bun 版本 | major.minor 必须等于协调器本机 | **1.4.x**（patch 不同只警告可过） |
| codeHash | `src/nn/**` + `tools/sim/export-rl-rollout.ts` 与协调器字节一致 | checkout 到协调器当前 commit（在协调器上 `git rev-parse HEAD` 取实时值） |
| 网络与保活 | 协调器 → `<节点>:8443` 可达；进程不被系统杀 | 各平台保活方式见下文分节 |

## 2. 通用流程（所有平台一致）

```bash
# ① 安装 bun 1.4.x（平台差异见 §3/§4），验证：
bun --version                 # 必须 1.4.x

# ② 克隆仓库并锚定 commit（codeHash 门）
git clone <repo-url> battle2 && cd battle2
git checkout <协调器当前的完整 commit hash>
git status                    # 必须干净

# ③ 启动 agent（首启生成 tools/agent/agent.auth 并打印 authKey）
bun tools/agent/sampler-agent.ts --port 8443 --workers <N>
#    日志应出现: [sampler-agent] listening on 0.0.0.0:8443 workers=<N> ...

# ④ 解决"协调器如何连到我"——局域网直连或出站隧道（§3/§4）
# ⑤ 记下访问地址与 authKey，进入 §5 协调器侧接入
```

磁盘余量 ≥2GB（agent 自检 `minDiskFreeMB=2048`，不足拒单）。

## 3. Android 平板（Termux，局域网直连）

### 3.1 环境

- 安装 ADB
- 安装 Termux： https://github.com/termux/termux-app/releases
- adb 连接 Termux
  ```bash
  export ANDROID_SERIAL="318b9188c234" #192.168.0.96 MI CC 9e
  export ANDROID_SERIAL="DYEYUOLNBI5LH6FQ" #192.168.0.97 unused
  export ANDROID_SERIAL="4P7TY5SWOJ6HK7S8" #192.168.0.98 weread

  adb shell

  run-as com.termux

  export PATH=/data/data/com.termux/files/usr/bin:$PATH
  export HOME=/data/data/com.termux/files/home
  cd $HOME
  /data/data/com.termux/files/usr/bin/bash
  ```

- 安装 git
  ```bash
  pkg update && pkg upgrade -y
  termux-wake-lock              # 防 CPU 休眠；另到系统设置关 Termux 电池优化
  pkg install git -y
  ```

- 安装 bun
  ```bash
  export http_proxy="http://192.168.0.99:7890"
  export https_proxy="http://192.168.0.99:7890"

  pkg install proot-distro
  proot-distro install ubuntu
  proot-distro login ubuntu

  apt update && apt install curl unzip -y

  curl -fsSL https://bun.sh/install | bash
  source ~/.bashrc
  bun --version

  git clone https://github.com/HuangJian/battle.git
  cd battle; bun install
  bun battle/tools/agent/sampler-agent.ts --port 8443 --workers 7
  ```

- 配置 ssh
  ```bash
  proot-distro login ubuntu

  apt update && apt install openssh-server -y
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  nano ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys

  nano /etc/ssh/sshd_config

  killall sshd
  /usr/sbin/sshd
  nano ~/.bashrc
  ```


### 3.2 启动与暴露

按 §2 启动 agent 后，平板在局域网内即天然可达——协调器直接填
`http://<平板IP>:8443`（`ip addr | grep inet` 查 IP）。无隧道环节。

`--workers` 建议：8 核平板先 **4**，稳定后升 6。一局一个单线程进程。
全程插电；热节流导致后期变慢是物理现象，不是故障。

## 4. Google Cloud Shell（cloudflared 出站隧道）

### 4.1 为什么必须走隧道

Cloud Shell 的端口预览（`*.cloudshell.dev`）有账号鉴权墙：程序化客户端
（curl / Python urllib）访问会被 302 重定向到
`ssh.cloud.google.com/cloudshell/jwt?...` 登录页——拿回来的不是 JSON 而是
HTML `<a>Found</a>` 页。协调器的 `node_ping` 必然失败。**换任何 header 都绕不过。**
另外 Cloud Shell 禁一切入站 TCP，只允许出站连接。

解法：**cloudflared quick tunnel** —— 只发出站连接，把流量反向送到本机 8443；
免注册免费。我们协议自带的 20s 心跳正好能穿过 CF 代理的空闲超时。

### 4.2 步骤

```bash
# 在 Cloud Shell 终端里（agent 本体按 §2 先跑起来并 curl localhost 自证）：
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared
chmod +x cloudflared && mkdir -p ~/bin && mv cloudflared ~/bin/
~/bin/cloudflared tunnel --url http://localhost:8443
# 输出会给一个 https://xxxx-yyyy.trycloudflare.com
```

⚠️ quick tunnel 的 URL **每次重启 cloudflared 都会变**，变了要同步改
dist-nodes.json。要固定 URL 需注册 Cloudflare 账号建命名隧道（免费档即可）。

### 4.3 Cloud Shell 特有坑

- **每周 50 小时配额 + ~20 分钟无活动回收 VM**：进程死了别慌——任务会在
  900s 超时后自动回队重试，零数据丢失；重连后重启 agent + cloudflared，
  权重下一轮自动重新 POST（幂等设计）。要主动压低回收频率，见 §4.4。
- 只有 1~2 vCPU：定位为**实验节点**，`--workers 2` 即可。
- bash 里 URL 用单引号包裹（反引号是命令替换，会执行失败）。

### 4.4 保活：协调器侧 SSH 心跳对抗空闲回收

Cloud Shell 的"inactivity"判定是**客户端 ↔ 会话通道之间有无数据传输**，
与机器负载无关：agent 的 CPU 占用、cloudflared 的出站隧道流量都不算数——
人一关浏览器标签页，约 20 分钟后 VM 必被回收。
对策：从协调器维持一条带心跳的 SSH 会话（`gcloud cloud-shell ssh` 可从本机直连）。

协调器侧（Windows）一次性准备：

```powershell
winget install Google.CloudSDK   # 或官方安装包：cloud.google.com/sdk/docs/install
gcloud auth login                # 浏览器完成 OAuth
# 首次 gcloud cloud-shell ssh 会生成 SSH 密钥对并请求会话授权，按提示确认即可
```

保活循环（随训练窗口启停；断线自动重连，可并进 run_rl.py 的启动脚本）：

```powershell
while ($true) {
  gcloud cloud-shell ssh `
    --ssh-flag='-oServerAliveInterval=60' `
    --ssh-flag='-oServerAliveCountMax=10'
  Start-Sleep -Seconds 30
}
```

要点：

- `ServerAliveInterval=60` 每 60s 向会话通道发一次心跳字节，正好压住 20 分钟
  空闲计时器——与 §0 协议自带 20s 心跳穿 CF 空闲超时同一思路，对象换成 Google 会话层。
- Cloud Shell 侧把 agent 与 cloudflared 放 `tmux`（预装）里跑：SSH 瞬断的几十秒
  间隙进程不受影响，重连循环 30s 内把会话续上。
- **绕不过的硬上限**：单会话 12h 硬杀（重连即重置时钟）；50 小时/周配额意味着
  24×7 保活物理不可能（需 168h/周）——保活必须绑定训练窗口，收训即停。
- 收益：少废在跑局（否则等 900s 才回队）；cloudflared 少重启 → quick tunnel URL
  稳定、dist-nodes.json 少改；人工重连次数趋零。
- 节点若要真正 7×24，正确路径是迁 GCE e2-micro 免费层或其他免费 VM：
  Cloud Shell 无配额豁免机制，天生不是常驻算力。

## 5. 协调器侧接入（Windows）

编辑 `nn-training/dist-nodes.json`，`nodes[]` 追加：

```json
{
    "id": "tablet",
    "url": "http://<节点地址>:8443",
    "authKey": "<该节点 tools/agent/agent.auth 内容>",
    "concurrency": 4,
    "enabled": true
}
```

⚠️ **concurrency 必须 ≤ 该节点 agent 实际 `--workers`**。超出的任务会吃
`HTTP 503 busy`，nodeFailStreak=3 连败即整轮熔断出局（2026-08-24 生产实测教训，
详见 docs/nn.progress.md §6.4）。

安全：authKey 是节点唯一门禁。走公网隧道时尤其保管好；疑似泄露就删掉该节点
的 `tools/agent/agent.auth` 重启 agent 轮换，再同步更新 dist-nodes.json。

## 6. 接入验证清单（按序核对）

1. 协调器日志（`tmp/run_rl-*.out.log` 最新一份）出现：
   `[dist] node <id>: online, concurrency=N`
2. 随后一行：`[dist] weights -> <id> (kept/purged)`
3. 逐局结算行出现 `node=<id> sX/seedY elapsed=NNs`
4. 节点侧自证：`curl -s -H "Authorization: Bearer <key>" localhost:8443/v1/status`
   → `gamesDoneTotal > 0` 且 `lastError` 为空
5. 巡检 HTML 的 byNode 统计出现该节点

## 7. 故障排查表

| 症状（协调器日志） | 根因 | 处置 |
|---|---|---|
| `node X: ping failed — excluded` | 休眠 / 网络不通 / 进程没起 | wake-lock；确认 agent 存活；从 PC `Test-NetConnection <ip> -Port 8443` |
| curl 得到 `<a>Found</a>` / jwt 重定向页 | Cloud Shell 端口预览鉴权墙 | 改走 §4 cloudflared 隧道 |
| `codeHash mismatch — excluded (red)` | commit 不一致或有本地改动 | `git status` 必须干净且在指定 commit |
| `bunVersion ... differs — excluded (red)` | 非 1.4.x | 更换 bun 构建（major.minor 必须一致） |
| 大量 `HTTP 503: {"error":"busy"}` | concurrency > workers | 调低 dist-nodes.json 的 concurrency |
| `wver not cached here` (409) | 该节点尚未收到本轮权重 | 等下轮 weights POST；持续出现则查 POST 失败行 |
| `Remote end closed` | 传输中断连（休眠/省电断网） | 插电 + wake-lock；任务 900s 超时自动回队 |

## 8. 确定性说明（为什么跨架构安全）

游戏模拟只用 JS double 四则运算 + 种子化整数 RNG（mulberry32），IEEE 754 语义
跨平台一致；同 major.minor 的 bun 使用同一 JSC 引擎。macos 节点已在生产中跨架构
贡献通过 `validate_result` 校验的战果，arm64-Android / x64-CloudShell 同理。
