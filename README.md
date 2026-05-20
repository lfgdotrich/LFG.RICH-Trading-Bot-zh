# LFG.RICH 社区交易机器人

这是一个面向 **BNB Smart Chain** 的 Python 交易机器人示例，专门用于通过官方 **LFG.RICH 协议合约** 交易 LFG.RICH launchpad 代币。

当前版本只支持 **LFG.RICH**：

- 通过 LFG.RICH `SwapRouter` 买入
- 通过 LFG.RICH `SwapRouter` 卖出
- 通过 LFG.RICH Uniswap V4 Hook 的 `Buy` / `Sell` 事件生成内部 K 线
- 通过 LFG.RICH Uniswap V4 Hook 获取价格和预估报价
- 不支持借贷功能：不会调用 `borrow`、`borrowMore` 或 `repay`

> 建议使用专用钱包，并先用小金额测试配置和策略，再进行正式交易。

---

## 1. 工作原理

机器人会循环执行以下步骤：

1. 连接 BSC RPC。
2. 读取 `config.yaml` 中的 LFG.RICH 协议地址和代币 watchlist。
3. 通过 LFG.RICH Hook 查询代币的 `poolId`、价格和交易事件。
4. 根据 `Buy` / `Sell` 事件生成内部 K 线。
5. 运行趋势和动量策略。
6. 根据策略结果决定 `BUY`、`SELL` 或 `HOLD`。
7. 如果 `dry_run: true`，只打印计划动作，不发送交易。
8. 如果 `dry_run: false`，通过 LFG.RICH `SwapRouter` 执行真实买入或卖出。
9. 将交易、仓位、lots 和本地状态保存到 SQLite 数据库 `state.db`。

---

## 2. 系统要求

### 2.1 推荐环境

已测试环境：

```text
Ubuntu 20.04 / Debian 风格服务器
Python 3.8
SQLite 本地状态数据库
支持 eth_getLogs 的 BNB Smart Chain RPC
```

机器人也可以在 Linux、macOS 和 Windows 上运行，只要安装了合适的 Python 版本和依赖。

推荐 Python 版本：

```text
Python 3.10 或 3.11
```

为了兼容旧服务器，本仓库的依赖也支持 Python 3.8。

不要使用 Python 2。如果错误里出现 `/usr/lib/python2.7/...`，说明你正在使用错误的 Python。

### 2.2 Linux / Ubuntu 系统包

在 Ubuntu/Debian 上先安装：

```bash
apt update
apt install -y python3.8-venv python3-pip python3-dev build-essential sqlite3
```

如果系统使用其他 Python 3 版本，可以安装通用包：

```bash
apt install -y python3-venv python3-pip python3-dev build-essential sqlite3
```

### 2.3 Python 依赖

运行机器人需要：

```text
requirements.txt
```

运行 Dashboard 需要：

```text
requirements-dashboard.txt
```

兼容性说明：

- 没有使用 `aiohappyeyeballs==2.6.1`，因为它在 Python 3.8 环境中不可用。
- Dashboard 使用 `streamlit==1.40.1`，因为 `1.40.2` 在已测试的 Python 3.8 环境中无法安装。
- Dashboard 使用 `use_container_width=True`，而不是较新的 `width="stretch"` 语法。
- 代码包含 Python 3.8 兼容性修复。

---

## 3. Linux / VPS 安装

进入你想安装的目录：

```bash
cd /var/www
```

克隆仓库，或上传并解压项目：

```bash
git clone https://github.com/lfgdotrich/LFG.RICH-Trading-Bot-zh.git
cd LFG.RICH-Trading-Bot-zh
```

创建干净的虚拟环境：

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
```

升级 Python 打包工具：

```bash
python -m pip install --upgrade pip setuptools wheel
```

安装运行依赖：

```bash
python -m pip install -r requirements.txt
```

创建本地配置文件：

```bash
cp .env.example .env
cp config.yaml.example config.yaml
```

确认虚拟环境已启用：

```bash
which python
python --version
python -m pip show web3
```

`which python` 应该指向项目目录内：

```text
/var/www/LFG.RICH-Trading-Bot-zh/.venv/bin/python
```

---

## 4. Windows 安装

建议使用 PowerShell。

进入项目目录：

```powershell
cd C:\path\to\LFG.RICH-Trading-Bot-zh
```

创建并启用虚拟环境：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止脚本运行，先执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新启用虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

复制配置文件：

```powershell
copy .env.example .env
copy config.yaml.example config.yaml
```

---

## 5. `.env` 配置

在项目根目录创建 `.env`。

示例：

```env
# 已使用 NowNodes、QuickNode 和 PublicNode 测试。其他 RPC 通常也可以使用。
BSC_RPC_URL="https://bsc-rpc.publicnode.com"
#BSC_RPC_URL="https://bsc.nownodes.io/some-api-key"
#BSC_RPC_URL="https://fittest-few-fog.bsc.quiknode.pro/some-api-key/"
PRIVATE_KEY="0xSecret"
WALLET_ADDRESS="0xWalletAddress"
```

### `.env` 设置说明

| 设置 | 是否必需 | 说明 |
|---|---:|---|
| `BSC_RPC_URL` | 是 | BNB Smart Chain RPC 地址。机器人需要普通合约调用和 `eth_getLogs` 来读取 LFG Hook 事件。 |
| `PRIVATE_KEY` | 真实交易时必需 | 用于签名买入、卖出和授权交易的钱包私钥。纯 dry-run 观察不需要。 |
| `WALLET_ADDRESS` | 建议填写 | 钱包公开地址，用于日志和 Dashboard 上下文。 |

不要把 `.env` 提交到 GitHub，也不要分享私钥。

---

## 6. `config.yaml` 配置

复制示例文件：

```bash
cp config.yaml.example config.yaml
```

默认 watchlist 包含 LFGBULL：

```yaml
watchlist:
  tokens:
    - symbol: "LFGBULL"
      address: "0x29Ede1E1254419Fe5a3c803fC2b015042075A49A"
      dex: "lfg"
```

如果你创建了自己的 LFG.RICH 代币，可以修改 `symbol` 和 `address`，并保留：

```yaml
dex: "lfg"
```

LFG.RICH 协议合约地址默认如下：

```yaml
lfg:
  factory: "0xaf6d6F359a630Ec6eb2BaFDc338b86d3E84CaF66"
  hook: "0xCA8D8D8d3d97cfac290a3850d32c2E8330CCe888"
  swap_router: "0x092043364f39f7f57C4E1D32116f453BfaE37440"
  pool_manager: "0x28e2Ea090877bF75740558f6BFB36A5ffeE9e9dF"
```

除非 LFG.RICH 官方部署新合约，否则不要修改这些地址。

---

## 7. 首次 Smoke Test

创建 `.env` 和 `config.yaml` 后运行：

```bash
source .venv/bin/activate
python -m bot.smoke_test_lfg
```

成功时应看到类似输出：

```text
代币: 0x...
pool_id: 0x...
是否已初始化: True
totalFeeBps: ...
有效价格 BNB/token: ...
预估买入 0.001 BNB:
```

`estimateBuy` 返回的值是链上原始单位：

```text
(tokensOut, platformFee, floorBoostFee)
```

代币数量和 BNB 数量都使用 18 位小数。

---

## 8. 运行交易机器人

第一次运行建议开启 dry-run：

```yaml
bot:
  dry_run: true
```

运行机器人：

```bash
source .venv/bin/activate
python -m bot.main
```

在 dry-run 模式中，机器人会扫描事件、生成 K 线、计算信号并打印计划交易，但不会发送真实交易。

### 8.1 测试真实买入或卖出

准备真实测试时，使用专用钱包和很小金额：

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

成功测试后关闭测试模式：

```yaml
bot:
  test_mode: false
```

`test_action` 的值必须保持为 `BUY` 或 `SELL`，不要翻译这些值。

### 8.2 使用 PM2 长期运行

可选示例：

```bash
pm2 start "./.venv/bin/python -m bot.main" --name LFG-TradingBot
pm2 save
```

查看日志：

```bash
pm2 logs LFG-TradingBot --lines 100
```

---

## 9. 运行 Dashboard

Dashboard 是可选功能。

安装 Dashboard 依赖：

```bash
source .venv/bin/activate
python -m pip install -r requirements-dashboard.txt
```

从项目根目录运行：

```bash
python -m streamlit run bot/dashboard.py --server.address 0.0.0.0 --server.port 8501
```

浏览器打开：

```text
http://YOUR_SERVER_IP:8501
```

如果使用 SSH 隧道：

```bash
ssh -L 8501:127.0.0.1:8501 root@YOUR_SERVER_IP
```

然后本地打开：

```text
http://localhost:8501
```

### Dashboard 依赖机器人数据

Dashboard 读取本地 SQLite 数据库：

```text
state.db
```

这个数据库由机器人创建和更新。Dashboard 本身不会交易，也不会创建仓位。

### 什么是 lot？

**lot** 是机器人本地记录的一笔买入仓位。

当机器人确认买入交易成功后，会记录一个 lot，包括：

```text
symbol
买入交易哈希
买入数量
BNB 成本
平均入场价格
剩余未卖出数量
```

Dashboard 使用 open lots 计算：

- 当前仓位价值
- 未实现 PnL
- 卖出后的已实现 PnL
- 平均入场价格
- 每个代币的开放风险敞口

如果 `state.db` 中没有已确认的买入交易，Dashboard 就没有 lots 可以显示。必须先完成真实买入，或从本地已确认交易记录重建 lots。

LFG 代币的 Dashboard 价格优先级：

1. 最新有效 K 线收盘价
2. LFG Hook `getEffectivePrice(poolId)` 作为备用价格
3. 如果两者都不可用，则为 0

---

## 10. SQLite 数据库

机器人使用本地 SQLite：

```text
state.db
```

机器人运行时会自动在项目根目录创建它。

常用查看命令：

```bash
sqlite3 state.db ".tables"
sqlite3 state.db "SELECT * FROM trades ORDER BY id DESC LIMIT 10;"
sqlite3 state.db "SELECT * FROM lots ORDER BY id DESC LIMIT 10;"
```

不要删除 `state.db`，除非你想重置本地交易历史、lots、冷却时间和 K 线缓存。

---

## 11. `config.yaml` 设置完整说明

### `chain`

| 设置 | 说明 |
|---|---|
| `chain.name` | 链的可读名称。保持为 `bsc`。 |
| `chain.chain_id` | BNB Smart Chain 主网 chain ID。保持为 `56`。 |

### `rpc`

| 设置 | 说明 |
|---|---|
| `rpc.request_timeout_sec` | 单个 RPC 请求的超时时间，单位为秒。 |
| `rpc.max_retries` | RPC 调用失败时的重试次数。 |
| `rpc.backoff_sec` | 初始重试延迟，重复失败时会递增。 |

### `lfg`

| 设置 | 说明 |
|---|---|
| `lfg.factory` | LFG.RICH Factory 合约。 |
| `lfg.hook` | LFG.RICH Uniswap V4 Hook，用于事件、状态、价格和预估。 |
| `lfg.swap_router` | LFG.RICH SwapRouter，用于买入和卖出。 |
| `lfg.pool_manager` | LFG.RICH 协议使用的 Uniswap V4 PoolManager。 |

### `bot` 执行设置

| 设置 | 说明 |
|---|---|
| `polling_interval_sec` | 每轮机器人循环之间的秒数。 |
| `trade_cooldown_sec` | 每次交易后，同一代币再次交易前等待的秒数。 |
| `min_hold_minutes` | 正常卖出逻辑允许卖出之前，lot 至少持有的分钟数。 |
| `dry_run` | 为 `true` 时只打印计划交易，不发送交易。 |
| `warmup_approve` | 为 `true` 时启动时检查/授权 LFG SwapRouter 的代币 allowance。 |
| `approve_wait_sec` | 卖出前等待授权交易确认的秒数。 |

### `bot` 风控和交易规模

| 设置 | 说明 |
|---|---|
| `profit_gate_enabled` | 为 `true` 时，普通卖出需要达到 `min_profit_pct`，除非触发止损。 |
| `min_profit_pct` | 开启 profit gate 时普通卖出需要达到的最低利润百分比。 |
| `max_loss_pct` | 止损百分比。例如 `10` 表示 lot 亏损达到 `-10%` 或更差时卖出。 |
| `max_hold_minutes` | 可选强制退出时间，单位分钟。`0` 表示关闭。 |
| `slippage_bps` | 滑点容忍度，单位 bps。`1200` 表示 12%。需要覆盖 LFG 代币费用和波动。 |
| `gas_limit` | LFG 买入/卖出交易使用的 gas limit。 |
| `min_bnb_for_gas` | 预留不用的 BNB，用于支付 gas。机器人不会花到低于这个余额。 |
| `min_trade_bnb` | 买入/卖出动作的最小 BNB 金额。 |
| `max_trade_bnb` | 单笔买入/卖出的最大 BNB 金额。 |

### `bot` 事件扫描和 K 线

| 设置 | 说明 |
|---|---|
| `blocks_per_candle` | 每根内部 K 线包含的 BSC 区块数量。 |
| `confirmations` | 等待多少个区块后才处理事件。 |
| `log_chunk_blocks` | `eth_getLogs` 每次查询的区块范围。公共 RPC 报错或限流时降低此值。 |
| `warmup_lookback_blocks` | 首次运行时向前扫描多少区块，用来为旧代币建立历史 K 线。 |
| `max_history_candles` | 每个代币在内存/缓存中保留的最大 K 线数量。 |

### `bot` 策略设置

| 设置 | 说明 |
|---|---|
| `fast_down_enabled` | 启用快速下跌卖出保护。 |
| `fast_down_candles` | 快速下跌检测使用的最近真实成交 K 线数量。 |
| `fast_down_min_drop_pct` | 在 `fast_down_candles` 范围内触发快速下跌的最小跌幅百分比。 |
| `fast_down_min_steps` | 触发快速下跌所需的最少下跌步骤数量。 |
| `trend_confirm_candles` | 用于确认趋势方向的 K 线数量。 |
| `ema_deadband_pct` | EMA 死区百分比，用于减少 EMA 接近时的噪音翻转。 |
| `dump_lookback` | 检测快速砸盘使用的 K 线数量。 |
| `dump_drop_pct` | 在 `dump_lookback` 范围内触发砸盘判断的跌幅百分比。 |
| `pump_lookback` | 检测快速拉升使用的 K 线数量。 |
| `pump_rise_pct` | 在 `pump_lookback` 范围内触发拉升判断的涨幅百分比。 |
| `bleed_lookback` | 检测慢跌/阴跌使用的 K 线数量。 |
| `bleed_drop_pct` | 在 `bleed_lookback` 范围内用于阴跌判断的跌幅百分比。 |
| `bleed_rise_pct` | 用于区分恢复走势和阴跌的上涨阈值。 |
| `bleed_min_steps` | 触发阴跌判断所需的最少下跌步骤数量。 |

### `bot` 测试模式

| 设置 | 说明 |
|---|---|
| `test_mode` | 为 `true` 时强制执行 `test_action`，不使用策略信号。 |
| `test_action` | `BUY` 或 `SELL`。只在 `test_mode` 为 `true` 时使用。不要翻译这些值。 |
| `test_amount_bnb` | 测试模式动作使用的 BNB 交易金额。 |
| `test_once` | 为 `true` 时执行一次测试动作后退出。 |

### `watchlist.tokens[]`

| 设置 | 说明 |
|---|---|
| `symbol` | 代币符号/简称。 |
| `address` | LFG.RICH 代币合约地址。 |
| `max_alloc_bnb` | 该代币允许使用的最大总 BNB 配置额度。 |
| `add_step_bnb` | 策略决定买入/加仓时使用的 BNB 数量。 |
| `timeframe_sec` | 策略目标时间框架，单位秒。为兼容策略设置和日志保留。 |
| `ema_fast` | 策略使用的快速 EMA 周期。 |
| `ema_slow` | 策略使用的慢速 EMA 周期。 |
| `rsi_period` | 策略使用的 RSI 周期。 |
| `dust_size` | 低于该数量的代币会被视为 dust，并在本地 lot 统计中当作已关闭。 |
| `dex` | 交易场所。LFG.RICH 代币保持为 `lfg`。 |

---

## 12. 常见问题排查

### 出现 `/usr/lib/python2.7/...` 的 `SyntaxError`

你正在运行 Python 2。请使用虚拟环境中的 Python：

```bash
source .venv/bin/activate
python --version
python -m bot.main
```

### `ModuleNotFoundError: No module named 'web3'`

当前环境没有安装依赖：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### `ensurepip is not available`

安装 venv 包：

```bash
apt install -y python3.8-venv python3-pip
```

然后重建虚拟环境：

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
```

### `No matching distribution found for aiohappyeyeballs==2.6.1`

你使用了来自较新 Python 环境的旧依赖列表。请使用本仓库提供的 `requirements.txt`。

### `No matching distribution found for streamlit==1.40.2`

请使用本仓库提供的 `requirements-dashboard.txt`，其中固定为：

```text
streamlit==1.40.1
```

### Dashboard 上 `lot_pnl_pct` 为空

通常说明 Dashboard 没有可用价格。当前版本会优先使用最新 K 线收盘价，如果没有足够 K 线，会回退到 LFG Hook `getEffectivePrice(poolId)`。

---

## 13. 上线前检查清单

1. 使用专用钱包。
2. 钱包里只放小额 BNB。
3. 先设置 `dry_run: true`。
4. 运行 `python -m bot.smoke_test_lfg`。
5. 运行机器人并确认 K 线、信号和日志合理。
6. 使用 `test_mode: true`、`test_action: "BUY"` 和很小的 `test_amount_bnb` 做真实测试。
7. 确认 Dashboard 显示已创建的 lot。
8. 最后再关闭 `test_mode`。

---

## 14. 社区说明

这个机器人是为 LFG.RICH 社区提供的贡献，目的是支持正在探索 LFG.RICH launchpad 和协议的开发者、建设者和交易者。

你可以自由使用、修改、改进和分享这个项目。运行真实交易前，请检查配置，先用小金额测试，并根据自己的策略进行调整。

本项目的目标是帮助提高 LFG.RICH 的采用率、实验性和社区驱动开发。
