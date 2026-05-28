# LFG.RICH 社区交易机器人

这是一个用于 BNB Smart Chain 的 Python 交易机器人示例，通过官方 LFG.RICH 协议合约交易 **LFG.RICH launchpad 代币**。

此版本只兼容 **LFG.RICH**：

- 使用代币解析出来的 PoolKey，通过配置的 LFG.RICH `SwapRouter` 买入
- 使用代币解析出来的 PoolKey，通过配置的 LFG.RICH `SwapRouter` 卖出
- 在可用时直接从代币合约解析 `FACTORY()`、`hook()` 和 `poolId()`
- 只有当代币没有暴露某个值时，才回退到解析出的 Factory/Hook 映射
- 从解析出的 Hook 中读取该 `poolId` 的 `Buy` 和 `Sell` 事件来生成 K 线
- 使用解析出的 Hook 获取价格和报价估算
- **不**执行借款、追加借款或还款

这个机器人面向 LFG.RICH launchpad 与协议的社区使用、实验和开发者贡献。

---

## 目录

1. [机器人如何工作](#机器人如何工作)
2. [要求](#要求)
3. [Linux / VPS 安装](#linux--vps-安装)
4. [Windows 安装](#windows-安装)
5. [环境配置](#环境配置)
6. [机器人配置](#机器人配置)
7. [冒烟测试](#冒烟测试)
8. [运行机器人](#运行机器人)
9. [运行仪表盘](#运行仪表盘)
10. [SQLite 数据库和 lots](#sqlite-数据库和-lots)
11. [配置参考](#配置参考)
12. [故障排查](#故障排查)
13. [社区说明](#社区说明)

---

## 机器人如何工作

机器人会监控 `config.yaml` 中配置的一个或多个 LFG.RICH 代币。

对于每个配置的代币，它会：

1. 当这些方法存在时，从代币合约读取 `FACTORY()`、`hook()` 和 `poolId()`。
2. 使用解析出的 Factory 读取 `getPoolKey(token)`，得到 Swap Router 使用的 PoolKey。
3. 只有当代币没有直接暴露 `poolId()` 时，才回退到 Factory 代币信息、Hook `tokenToPoolId(token)` 或 PoolKey 推导。
4. 扫描解析出的 Hook 中该 `poolId` 的 `Buy` 和 `Sell` 事件。
5. 使用正确的价格缩放系数生成内部价格 K 线：V5 风格 Hook 状态使用 `1e22`，较旧的 V3 风格 Hook 状态使用 `1e18`。
6. 计算 EMA、RSI、趋势、拉盘、砸盘和慢跌等指标。
7. 决定执行 `BUY`、`SELL` 或 `HOLD`。
8. 先通过解析出的 Hook 估算，再通过配置的 `SwapRouter` 执行交易，除非启用了 `dry_run`。
9. 将已确认的交易和 lots 记录到本地 SQLite 数据库。

机器人在运行时**不会**调用 LFG.RICH 网站或 API。它是独立运行的，并且在链上解析代币路由。它也不会使用 PancakeSwap V2/V3 来交易 LFG 代币。

---

## 要求

### 已测试环境

机器人已在以下环境测试：

```text
Ubuntu/Debian 风格服务器
Python 3.10 或 Python 3.11
SQLite 状态数据库
支持 eth_getLogs 的 BNB Smart Chain RPC
```

推荐新用户使用的 Python 版本：

```text
Python 3.10 或 Python 3.11
```

不要使用 Python 2 运行机器人。如果你看到类似下面的错误路径：

```text
/usr/lib/python2.7/...
```

说明你正在使用错误的 Python 版本。

### 必需系统组件

机器人需要：

- Python 3
- Python 虚拟环境支持
- `pip`
- SQLite
- 支持 `eth_getLogs` 的 BNB Smart Chain RPC 端点
- 用于真实交易的钱包私钥

### Python 依赖

运行时依赖固定在：

```text
requirements.txt
```

仪表盘依赖单独固定在：

```text
requirements-dashboard.txt
```

重要兼容性说明：

- 故意**不**使用 `aiohappyeyeballs==2.6.1`，因为它不兼容 Python 3.8。
- 使用 `streamlit==1.40.1`，因为在测试的 Python 3.8 环境中无法安装 `1.40.2`。
- 仪表盘使用 `use_container_width=True`，不使用较新的 Streamlit `width="stretch"` 语法。
- 代码包含 Python 3.8 兼容性修复，例如在需要的位置使用 `from __future__ import annotations`。

---

## Linux / VPS 安装

在 Ubuntu/Debian 风格服务器上使用以下步骤。

### 1. 安装系统包

对于 Python 3.8：

```bash
apt update
apt install -y python3.8-venv python3-pip python3-dev build-essential sqlite3
```

如果服务器使用其他 Python 3 版本，请安装通用 venv 包：

```bash
apt update
apt install -y python3-venv python3-pip python3-dev build-essential sqlite3
```

### 2. 下载或克隆项目

示例：

```bash
cd /var/www
git clone https://github.com/lfgdotrich/LFG.RICH-Trading-Bot-zh.git
cd LFG.RICH-Trading-Bot-zh
```

如果你使用 ZIP，请上传/解压后进入项目目录。

### 3. 创建干净的虚拟环境

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
```

终端现在应该显示 `(.venv)`。

### 4. 升级打包工具

```bash
python -m pip install --upgrade pip setuptools wheel
```

### 5. 安装机器人依赖

```bash
python -m pip install -r requirements.txt
```

### 6. 创建本地配置文件

```bash
cp .env.example .env
cp config.yaml.example config.yaml
```

### 7. 确认环境

```bash
which python
python --version
python -m pip show web3
```

预期的 `which python` 应该指向本项目内部：

```text
/var/www/LFG.RICH-Trading-Bot-zh/.venv/bin/python
```

---

## Windows 安装

Windows 支持本地测试、开发和社区使用。  
长期 24/7 运行建议使用 Linux/VPS。

使用 PowerShell。

### 1. 安装 Python

从 Python 官方网站安装 Python 3.10 或 3.11。

安装时启用：

```text
Add Python to PATH
```

### 2. 打开 PowerShell 并进入项目目录

示例：

```powershell
cd C:\path\to\LFG.RICH-Trading-Bot-zh
```

### 3. 创建并激活虚拟环境

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止激活，请运行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后再次激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. 安装依赖

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

### 5. 创建本地配置文件

```powershell
copy .env.example .env
copy config.yaml.example config.yaml
```

---

## 环境配置

在项目根目录创建 `.env` 文件。

示例：

```env
# 已测试 NowNodes、Quicknode 和 publicnode。其他 RPC 理论上也可以工作
BSC_RPC_URL="https://bsc-rpc.publicnode.com"
#BSC_RPC_URL="https://bsc.nownodes.io/some-api-key"
#BSC_RPC_URL="https://fittest-few-fog.bsc.quiknode.pro/some-api-key/"
PRIVATE_KEY="0xSecret"
WALLET_ADDRESS="0xWalletAddress"
```

### `.env` 设置

| 设置 | 是否必需 | 说明 |
|---|---:|---|
| `BSC_RPC_URL` | 是 | BNB Smart Chain RPC URL。机器人需要普通合约调用以及用于 LFG Hook 事件的 `eth_getLogs`。 |
| `PRIVATE_KEY` | 真实交易必需 | 用于签名买入、卖出和授权交易的钱包私钥。纯 dry-run 观察不需要。 |
| `WALLET_ADDRESS` | 推荐 | 用于日志和仪表盘上下文的钱包公开地址。 |

不要把 `.env` 提交到 GitHub，也不要分享你的私钥。

---

## 机器人配置

机器人通过以下文件配置：

```text
config.yaml
```

先复制示例：

```bash
cp config.yaml.example config.yaml
```

默认示例代币是 LFGBULL：

```yaml
watchlist:
  tokens:
    - symbol: "LFGBULL"
      address: "0x29Ede1E1254419Fe5a3c803fC2b015042075A49A"
      dex: "lfg"
```

如果你在 LFG.RICH 创建了自己的代币，请替换 `watchlist.tokens[]` 中的 symbol 和代币地址。

### LFG.RICH 协议合约

默认配置使用官方 LFG.RICH BSC 合约：

```yaml
lfg:
  factory: "0x429a7ef0129649a97bb3f1e961dd56d9bd4ac01f"
  hook: "0xc18e6e1926113cdcf53f3ec1a89d3eb84cc6a888"
  swap_router: "0x4018abd5d24ee48c642e7e518A8Aef03B59EC150"
  pool_manager: "0x28e2Ea090877bF75740558f6BFB36A5ffeE9e9dF"
```

这些只是回退值。解析器会先向每个代币合约询问它自己的 `FACTORY()`、`hook()` 和 `poolId()`。只有当代币没有暴露其中某个值时，机器人才使用配置中的回退 Factory/Hook。

机器人会刻意遵循代币驱动的交互模式：在链上解析代币上下文，从解析出的 Factory 读取 PoolKey，通过解析出的 Hook 估算，从解析出的 Hook 扫描事件，并执行路由器 `buy(poolKey, minTokensOut)` / `sell(poolKey, tokenAmount, minEthOut)`。

---

## 冒烟测试

运行完整机器人之前先运行冒烟测试：

```bash
source .venv/bin/activate
python -m bot.smoke_test_lfg
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m bot.smoke_test_lfg
```

成功的冒烟测试应该输出：

```text
token: 0x...
pool_id: 0x...
initialized: True
totalFeeBps: ...
有效价格 BNB/token: ...
估算买入 0.001 BNB:
  tokensOut: ...
  platformFee: ...
  inviterFee: ...
  totalFee: ...
```

报价值来自当前 LFG.RICH V5 Hook。代币数量和 BNB 数量使用 18 位小数，而 V5 Hook 价格和交易事件 `newPrice` 值在内部使用 `1e22` 价格缩放。

---

## 运行机器人

### 1. 从 dry-run 模式开始

真实交易前，保持启用：

```yaml
bot:
  dry_run: true
```

运行机器人：

```bash
source .venv/bin/activate
python -m bot.main
```

在 dry-run 模式中，机器人会扫描事件、生成 K 线、计算信号并记录计划交易，但不会发送交易。

### 2. 测试一笔小额真实买入

dry-run 日志看起来正确后，使用一个只放少量 BNB 的专用钱包。

启用测试模式：

```yaml
bot:
  dry_run: false
  test_mode: true
  test_action: "BUY"
  test_amount_bnb: 0.005
  test_once: true
```

运行：

```bash
python -m bot.main
```

测试买入成功后，检查仪表盘并确认 lot 已创建。

### 3. 关闭测试模式，使用正常策略交易

```yaml
bot:
  test_mode: false
```

然后运行：

```bash
python -m bot.main
```

### 4. 使用 PM2 保持机器人运行

可选 Linux 示例：

```bash
pm2 start "./.venv/bin/python -m bot.main" --name LFG-TradingBot
pm2 save
```

查看日志：

```bash
pm2 logs LFG-TradingBot --lines 100
```

---

## 运行仪表盘

仪表盘是可选的，但非常适合检查开放 lots、PnL、交易、余额和机器人状态。

### 1. 安装仪表盘依赖

```bash
source .venv/bin/activate
python -m pip install -r requirements-dashboard.txt
```

Windows：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dashboard.txt
```

### 2. 运行仪表盘

请在项目根目录运行，不要在 `bot/` 文件夹内部运行：

```bash
python -m streamlit run bot/dashboard.py --server.address 0.0.0.0 --server.port 8501
```

Windows 本地使用：

```powershell
python -m streamlit run bot/dashboard.py --server.port 8501
```

在浏览器打开：

```text
http://YOUR_SERVER_IP:8501
```

本地使用：

```text
http://localhost:8501
```

### 3. SSH 隧道选项

如果不想公开暴露 `8501` 端口：

```bash
ssh -L 8501:127.0.0.1:8501 root@YOUR_SERVER_IP
```

然后打开：

```text
http://localhost:8501
```

### 4. 使用 PM2 保持仪表盘运行

可选 Linux 示例：

```bash
pm2 start "./.venv/bin/python -m streamlit run bot/dashboard.py --server.address 0.0.0.0 --server.port 8501" --name LFG-TradingBot-Dashboard
pm2 save
```

---

## SQLite 数据库和 lots

机器人使用本地 SQLite 数据库：

```text
state.db
```

机器人运行时会在项目根目录自动创建它。

### 仪表盘依赖机器人

仪表盘读取 `state.db`。

仪表盘本身不会交易，也不会自己创建持仓。它依赖机器人创建和更新交易、持仓和 lots。

如果机器人尚未确认任何买入交易，仪表盘可能没有开放 lots 可显示。

### 什么是 lots？

**lot** 是机器人对一次买入持仓的本地记录。

当机器人确认一笔成功买入交易时，它会记录一个 lot，包含：

```text
symbol
buy transaction
quantity opened
cost in BNB
average entry price
remaining open quantity
```

仪表盘使用开放 lots 计算：

- 当前持仓价值
- 未实现 PnL
- 卖出后的已实现 PnL
- 平均入场价格
- 每个代币的开放敞口

对于 LFG 代币，仪表盘按以下优先级为开放 lots 定价：

1. 最新有效 K 线收盘价
2. LFG Hook `getEffectivePrice(poolId)` 回退
3. 只有当前两者都不可用时才使用零

### 常用 SQLite 命令

```bash
sqlite3 state.db ".tables"
sqlite3 state.db "SELECT * FROM trades ORDER BY id DESC LIMIT 10;"
sqlite3 state.db "SELECT * FROM lots ORDER BY id DESC LIMIT 10;"
```

不要删除 `state.db`，除非你明确想重置本地交易历史、lots、冷却时间和 K 线缓存。

---

## 配置参考

本节说明 `config.yaml` 中的设置。

### `chain`

| 设置 | 说明 |
|---|---|
| `chain.name` | 便于阅读的链名称。保持为 `bsc`。 |
| `chain.chain_id` | BNB Smart Chain 主网 chain ID。保持为 `56`。 |

### `rpc`

| 设置 | 说明 |
|---|---|
| `rpc.request_timeout_sec` | 单个 RPC 请求的超时时间，单位秒。 |
| `rpc.max_retries` | RPC 调用失败时的重试次数。 |
| `rpc.backoff_sec` | 重试之间的初始延迟。重试辅助函数会在连续失败时增加延迟。 |

### `lfg`

| 设置 | 说明 |
|---|---|
| `lfg.factory` | LFG.RICH Factory 合约。用于协议元数据和费用接收方参考。 |
| `lfg.hook` | LFG.RICH Uniswap V4 Hook。用于事件、代币状态、价格和估算。 |
| `lfg.swap_router` | LFG.RICH SwapRouter。用于执行买入和卖出。 |
| `lfg.pool_manager` | LFG.RICH 协议使用的 Uniswap V4 PoolManager。 |

### `bot` 执行设置

| 设置 | 说明 |
|---|---|
| `bot.polling_interval_sec` | 每次机器人循环之间的秒数。 |
| `bot.trade_cooldown_sec` | 交易后再次交易同一代币前等待的秒数。 |
| `bot.min_hold_minutes` | 正常卖出逻辑可以卖出一个 lot 之前必须持有的最短分钟数。 |
| `bot.dry_run` | 如果为 `true`，机器人只记录计划交易，不发送交易。 |
| `bot.warmup_approve` | 如果为 `true`，机器人启动时检查 LFG SwapRouter 的代币授权。 |
| `bot.approve_wait_sec` | 继续执行卖出前等待授权交易确认的秒数。 |

### `bot` 风险和交易大小

| 设置 | 说明 |
|---|---|
| `bot.profit_gate_enabled` | 如果为 `true`，普通卖出需要达到 `min_profit_pct`，除非触发止损。 |
| `bot.min_profit_pct` | profit gate 启用时，普通卖出所需的最低利润百分比。 |
| `bot.max_loss_pct` | 止损百分比。例如 `10` 表示 lot 达到 `-10%` 或更差时卖出。 |
| `bot.max_hold_minutes` | 可选强制退出计时器，单位分钟。`0` 禁用强制退出。 |
| `bot.slippage_bps` | 滑点容忍度，单位基点。`1200` 表示 12%。它必须覆盖固定 V5 交易费和波动。 |
| `bot.gas_limit` | LFG 买入/卖出交易使用的 gas limit。 |
| `bot.min_bnb_for_gas` | 保留用于 gas 的 BNB。机器人不会花掉低于该余额的部分。 |
| `bot.min_trade_bnb` | 买入/卖出操作的最低 BNB 金额。 |
| `bot.max_trade_bnb` | 单笔买入/卖出操作的最高 BNB 金额。 |

### `bot` 事件扫描和 K 线

| 设置 | 说明 |
|---|---|
| `bot.blocks_per_candle` | 每根内部价格 K 线包含的 BSC 区块数。 |
| `bot.confirmations` | 在将事件视为足够安全/最终并处理之前等待的区块数。 |
| `bot.log_chunk_blocks` | `eth_getLogs` 分块大小。如果公共/免费 RPC 限速或失败，请降低它。 |
| `bot.warmup_lookback_blocks` | 首次运行时扫描的区块数，用于为较旧代币生成历史 K 线。如果机器人提示 `not enough data`，可以增加。 |
| `bot.max_history_candles` | 每个代币在内存/缓存中保留的最大 K 线数量。 |

### `bot` 策略设置

| 设置 | 说明 |
|---|---|
| `bot.fast_down_enabled` | 启用快速下跌卖出保护。 |
| `bot.fast_down_candles` | 用于快速下跌检测的最近真实成交量 K 线数量。 |
| `bot.fast_down_min_drop_pct` | 在 `fast_down_candles` 范围内标记快速下跌所需的最低跌幅百分比。 |
| `bot.fast_down_min_steps` | 标记快速下跌所需的最少红/下跌 K 线步数。 |
| `bot.trend_confirm_candles` | 用于确认趋势方向的 K 线数量。 |
| `bot.ema_deadband_pct` | EMA 死区百分比。当 EMA 非常接近时帮助避免噪声导致的趋势翻转。 |
| `bot.dump_lookback` | 用于检测快速砸盘的 K 线数量。 |
| `bot.dump_drop_pct` | 在 `dump_lookback` K 线内标记砸盘的跌幅百分比。 |
| `bot.pump_lookback` | 用于检测快速拉盘的 K 线数量。 |
| `bot.pump_rise_pct` | 在 `pump_lookback` K 线内标记拉盘的涨幅百分比。 |
| `bot.bleed_lookback` | 用于检测慢跌/下跌趋势的 K 线数量。 |
| `bot.bleed_drop_pct` | 对慢跌检测有贡献的 `bleed_lookback` 范围跌幅百分比。 |
| `bot.bleed_rise_pct` | 用于区分恢复和慢跌的涨幅阈值。 |
| `bot.bleed_min_steps` | 慢跌检测所需的最少下跌步数。 |

### `bot` 测试模式

| 设置 | 说明 |
|---|---|
| `bot.test_mode` | 如果为 `true`，强制使用 `test_action`，不使用策略信号。适合真实买卖测试。 |
| `bot.test_action` | `BUY` 或 `SELL`。仅在 `test_mode` 为 true 时使用。 |
| `bot.test_amount_bnb` | 测试模式操作的 BNB 交易大小。 |
| `bot.test_once` | 如果为 `true`，执行一次测试操作后退出。 |

### `watchlist.tokens[]`

每个代币条目定义一个要监控和交易的 LFG.RICH 代币。

| 设置 | 说明 |
|---|---|
| `symbol` | 代币符号/ticker。 |
| `address` | LFG.RICH 代币合约地址。 |
| `max_alloc_bnb` | 该代币允许的最大总 BNB 分配。 |
| `add_step_bnb` | 策略决定买入/加仓时要增加的 BNB 数量。 |
| `timeframe_sec` | 预期策略时间周期，单位秒。保留用于兼容策略设置/日志。 |
| `ema_fast` | 策略使用的快速 EMA 周期。 |
| `ema_slow` | 策略使用的慢速 EMA 周期。 |
| `rsi_period` | 策略使用的 RSI 周期。 |
| `dust_size` | 低于该数量的代币会在本地 lot 记账中被视为 dust/已关闭。 |
| `dex` | 交易场所。LFG.RICH 代币保持为 `lfg`。 |

---

## 故障排查

### `SyntaxError` 显示 `/usr/lib/python2.7/...`

你正在运行 Python 2。

激活 venv 并使用 Python 3：

```bash
source .venv/bin/activate
python --version
python -m bot.main
```

### `ModuleNotFoundError: No module named 'web3'`

当前环境中未安装依赖：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### `ensurepip is not available`

安装 venv 包：

```bash
apt install -y python3.8-venv python3-pip
```

然后重新创建 venv：

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
```

### `pip: command not found`

安装 pip：

```bash
apt install -y python3-pip
```

### `No matching distribution found for aiohappyeyeballs==2.6.1`

你正在使用来自较新 Python 环境的旧/冻结依赖列表。

请使用本 LFG.RICH 机器人包内的 `requirements.txt`。

### `No matching distribution found for streamlit==1.40.2`

使用随附的 `requirements-dashboard.txt`，其中固定为：

```text
streamlit==1.40.1
```

### `invalid argument 0: hex string without 0x prefix`

使用最新的 LFG.RICH 机器人包。LFG pool id 和事件 topic 处理已经更新，会自动标准化为必需的 `0x` 前缀。

### 仪表盘中 `lot_pnl_pct` 为空或 `None`

仪表盘需要有效的当前价格。

对于 LFG 代币，仪表盘现在使用：

1. 最新有效 K 线收盘价
2. LFG Hook `getEffectivePrice(poolId)` 回退
3. 只有当前两者都不可用时才使用零

如果该值仍为空，请确认机器人和仪表盘使用的是同一个 `state.db` 和 `config.yaml`。

---

## 安全运行检查清单

真实运行前：

1. 使用专用钱包。
2. 只在该钱包中保留少量 BNB。
3. 从 `dry_run: true` 开始。
4. 运行 `python -m bot.smoke_test_lfg`。
5. 运行机器人并确认 K 线/信号看起来正确。
6. 使用 `test_mode: true`、`test_action: "BUY"` 和很小的 `test_amount_bnb` 测试。
7. 确认仪表盘显示已创建的 lot。
8. 之后再关闭 `test_mode`。

---

## 社区说明

这个机器人是为 LFG.RICH 社区贡献的项目，用于支持探索 LFG.RICH launchpad 和协议的开发者、建设者和交易者。

你可以自由使用、修改、改进和分享这个项目。在进行真实交易之前，请检查配置，用小金额测试，并根据自己的需要调整策略。

本项目的目标是帮助提升 LFG.RICH 的采用、实验和社区驱动开发。
