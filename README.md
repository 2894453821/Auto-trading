# OKX Auto Trader

一个面向 OKX 的短周期自动交易框架。目标是工程可靠、风险可控、可回测、可停止，而不是承诺收益。

默认运行模式是 `paper`，不会实盘下单。实盘必须同时满足：

- `trading_mode: live`
- `live_trading_enabled: true`
- API Key 仅通过 `.env` 或本地环境变量读取
- `require_manual_confirm: true` 时逐笔人工确认

## 功能

- OKX v5 REST 适配层，公共行情可无 Key 使用，私有接口从环境变量读取 Key。
- 支持 demo header：`x-simulated-trading: 1`。
- 行情模块：K 线、Ticker、盘口、价差、成交量、资金费率。
- 策略模块：15m 趋势过滤 + 5m 入场，EMA、RSI、ATR、成交量和价差过滤。
- 风控模块：单笔最大风险、单日最大亏损、最大持仓数、止盈止损、连续亏损暂停、emergency stop。
- 执行模块：纸面交易和 OKX 订单接口框架，默认不会实盘。
- 回测模块：手续费、滑点、胜率、盈亏比、最大回撤、夏普比率、交易次数。
- 日志模块：脱敏日志和每日交易报告。

## 安装依赖

```bash
cd outputs/okx-auto-trader
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

复制示例环境变量文件：

```bash
copy .env.example .env
```

Linux/macOS：

```bash
cp .env.example .env
```

不要把 `.env` 提交到仓库。API Key 只需要读取账户、行情、交易权限，禁止开启提币权限。

核心配置在 `config.yaml`：

```yaml
trading_mode: paper
symbols: ["BTC-USDT", "ETH-USDT"]
timeframe_signal: "15m"
timeframe_entry: "5m"
max_risk_per_trade: 0.005
max_daily_loss: 0.02
max_positions: 2
leverage: 1
require_manual_confirm: true
enable_short: false
enable_futures: false
live_trading_enabled: false
```

## 运行模拟盘

```bash
python -m src.main run-paper --config config.yaml --once
```

连续运行：

```bash
python -m src.main run-paper --config config.yaml --interval 60
```

`paper` 模式只会在本地记录模拟成交，不会向 OKX 发送真实订单。

## 打开图形界面

```bash
python -m src.main ui --config config.yaml --port 8787
```

然后打开：

```text
http://127.0.0.1:8787
```

界面包含 K 线、价差、成交量、账户权益、可用资金、风险暴露、保证金占用、日内 PnL、实时未实现盈亏、AI 最新建议、指标快照、当前持仓、交易日志、确认建议下单、手动模拟做多、手动模拟做空、杠杆选择、平仓、撤单和 emergency stop。默认配置下所有交易按钮只作用于本地 paper broker。

K 线支持 `1m`、`5m`、`15m`、`1H`。图表上方的 `Zoom +` / `Zoom -` 只改变 K 线可见数量，不会缩放右侧账户、持仓和交易面板。

AI 建议是两步流程：

1. 点击“生成 AI 建议”，系统读取行情、账户、指标和风控状态，只生成建议。
2. 只有建议通过风控并且你点击“确认建议下单”，才会执行模拟下单。

AI 不允许绕过 `risk_manager`，也不允许自动实盘下单。

模拟合约说明：

- 默认支持本地模拟做多和做空。
- 模拟盘杠杆可选 `1x`、`2x`、`10x`、`50x`、`100x`，只影响模拟仓位上限和保证金展示。
- 盈亏使用 OKX 当前真实价格计算，但成交和持仓都只保存在本地 paper broker。
- 可用资金会计入实时浮动盈亏，并扣除占用保证金。
- 可在界面中手动设置本地模拟可用资金；这只会重置 paper broker 的本地权益，不影响 OKX 真实账户。
- 鼠标悬停在 K 线上会显示当前 K 线的时间、开高低收、成交量和指针价格。
- 预测面板会显示方向倾向、信心、多空评分、15m/1h 预测波动区间、支撑、压力、触发条件和失效点。
- 模拟盘止盈止损会根据最新 1m K 线的高低点检测触发；触发后会自动结束当前模拟仓位。
- 右侧下单面板支持输入价格、数量、金额 USDT，并提供 0%/25%/50%/75%/100% 的可用资金比例滑杆。
- 如果输入金额 USDT，系统会按 `金额 × 杠杆 / 当前价格` 计算模拟数量，同时仍会校验最大风险。
- 界面提供“取消资金限制”按钮，仅作用于模拟盘。开启后会取消“保证金最多 50%”和“单笔最大亏损 0.5%”两项尺寸限制，但仍保留止盈止损、急停、最大杠杆上限和实盘禁用边界。
- 实盘交易仍然默认关闭。

## 运行回测

使用 CSV：

```bash
python -m src.main backtest --config config.yaml --csv data/BTC-USDT-5m.csv
```

CSV 需要包含：

```text
timestamp,open,high,low,close,volume
```

简单参数扫描：

```bash
python -m src.main scan --config config.yaml --csv data/BTC-USDT-5m.csv --stop-loss-atr 1.0,1.2,1.5 --take-profit-rr 1.5,1.8,2.0
```

参数扫描只用于粗筛风险收益特征，不建议根据单一历史区间过拟合。

## 切换到实盘

1. 在 `.env` 或系统环境变量设置：

```bash
OKX_API_KEY=your_key
OKX_API_SECRET=your_secret
OKX_API_PASSPHRASE=your_passphrase
```

2. 修改 `config.yaml`：

```yaml
trading_mode: live
live_trading_enabled: true
require_manual_confirm: true
```

3. 启动：

```bash
python -m src.main run-live --config config.yaml
```

## 实盘前必须检查

- OKX API Key 没有提币权限。
- `require_manual_confirm: true`。
- `enable_futures: false`，除非你明确要开合约。
- 如果启用合约，`leverage <= 2`，且使用逐仓。
- `max_risk_per_trade <= 0.005`。
- `max_daily_loss <= 0.02`。
- `max_positions <= 2`。
- 每笔订单都有止损和止盈。
- 日志中没有 API Key、Secret、Passphrase。
- 已经跑过回测和至少数天 paper trading。
- emergency stop 可用：触发后停止策略、撤销未成交订单、禁止新开仓。

## 测试

```bash
pytest
```

## 免责声明

本项目不保证盈利。短周期交易存在滑点、断线、成交失败、流动性不足、极端行情和交易所系统风险。请先长期模拟盘验证，再用极小资金实盘灰度。
