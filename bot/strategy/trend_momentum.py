from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Signal:
    score: float
    trend: str
    rsi: float
    reason: str


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    period = max(1, int(period))
    k = 2.0 / (period + 1.0)
    ema = float(values[0])
    for v in values[1:]:
        ema = (float(v) * k) + (ema * (1.0 - k))
    return float(ema)


def _rsi(values: List[float], period: int) -> float:
    period = max(1, int(period))
    if len(values) < period + 1:
        return 50.0
    gains = 0.0
    losses = 0.0
    # 使用最近一个周期的变化
    window = values[-(period + 1):]
    for i in range(1, len(window)):
        ch = float(window[i]) - float(window[i - 1])
        if ch > 0:
            gains += ch
        elif ch < 0:
            losses += -ch
    if gains == 0.0 and losses == 0.0:
        return 50.0
    if losses == 0.0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def compute_signal(
    hist,
    ema_fast: int,
    ema_slow: int,
    rsi_period: int,
    *,
    confirm_candles: int = 3,
    ema_deadband_pct: float = 0.20,
    dump_lookback: int = 3,
    dump_drop_pct: float = 0.50,
    **_ignored,
) -> Signal:
    """
    重要：
    - Trend/RSI 只基于真实 K 线计算（volume_token > 0）。
    - 增加砸盘/冲击覆盖：如果价格在最近 dump_lookback 根真实 K 线内下跌 >= dump_drop_pct，
      即使 EMA 尚未交叉，也强制 DOWN。
    """

    if not hist:
        return Signal(0.0, "FLAT", 50.0, "没有 K 线")

    # 1) 指标只使用真实 K 线
    real = [c for c in hist if float(getattr(c, "volume_token", 0.0) or 0.0) > 0.0]
    if len(real) < max(int(ema_slow), int(rsi_period), 10):
        # 真实 K 线不足：回退到最近 N 个收盘价，但仍避免长时间平铺填充。
        # 使用最近 200 根 K 线，但压缩相同收盘价。
        closes = []
        for c in hist[-200:]:
            v = float(getattr(c, "close", 0.0) or 0.0)
            if v <= 0:
                continue
            if not closes or v != closes[-1]:
                closes.append(v)
    else:
        closes = [float(c.close) for c in real if float(getattr(c, "close", 0.0) or 0.0) > 0.0]

    if len(closes) < max(int(ema_slow), int(rsi_period), 10):
        return Signal(0.0, "FLAT", 50.0, f"数据不足 closes={len(closes)}")

    # 2) 使用真实 K 线的冲击覆盖（快速砸盘 / 快速拉盘）
    # 同时要求：
    # - 总波动 >= 阈值
    # - 有足够的方向步数确认，避免单根尖刺噪声
    shock_lookback = max(2, int(dump_lookback))  # 复用已有参数名以保持兼容
    shock_down_pct = abs(float(dump_drop_pct))  # 复用现有配置名
    shock_up_pct = abs(float(_ignored.get("pump_rise_pct", 0.50)))  # 如果传入则为可选参数
    shock_min_steps = int(_ignored.get("pump_lookback", 2))  # 如果传入则为可选参数

    if len(closes) >= shock_lookback + 1:
        window = closes[-(shock_lookback + 1):]
        first = window[0]
        last = window[-1]

        # 统计窗口内的方向步数
        up_steps = 0
        down_steps = 0
        for i in range(1, len(window)):
            if window[i] > window[i - 1]:
                up_steps += 1
            elif window[i] < window[i - 1]:
                down_steps += 1

        if first > 0:
            move_pct = (last / first - 1.0) * 100.0

            # 快速砸盘
            if move_pct <= -shock_down_pct and down_steps >= shock_min_steps:
                return Signal(
                    -0.60,
                    "DOWN",
                    _rsi(closes, int(rsi_period)),
                    f"砸盘覆盖：{move_pct:.3f}% 于最近 {shock_lookback} 根真实 K 线（down_steps={down_steps})",
                )

            # 快速拉盘
            if move_pct >= shock_up_pct and up_steps >= shock_min_steps:
                return Signal(
                    0.60,
                    "UP",
                    _rsi(closes, int(rsi_period)),
                    f"拉盘覆盖：{move_pct:.3f}% 于最近 {shock_lookback} 根真实 K 线（up_steps={up_steps})",
                )

    # 2.5) 慢跌覆盖（捕捉 EMA 差值停留在死区内的长期磨跌趋势）
    # 使用真实收盘价（已在 `closes` 中）。如果较长窗口内净变化足够大则触发
    # 并且有足够步数与方向一致（避免震荡噪声）。
    bleed_lookback = int(_ignored.get("bleed_lookback", 30))          # 回看的真实 K 线数量
    bleed_drop_pct = float(_ignored.get("bleed_drop_pct", 0.40))      # 窗口内强制 DOWN 的百分比跌幅
    bleed_rise_pct = float(_ignored.get("bleed_rise_pct", 0.40))      # 窗口内强制 UP 的百分比涨幅
    bleed_min_steps = int(_ignored.get("bleed_min_steps", max(2, int(bleed_lookback * 0.60))))

    if len(closes) >= bleed_lookback + 1:
        window = closes[-(bleed_lookback + 1):]
        first = window[0]
        last = window[-1]

        up_steps = 0
        down_steps = 0
        for i in range(1, len(window)):
            if window[i] > window[i - 1]:
                up_steps += 1
            elif window[i] < window[i - 1]:
                down_steps += 1

        if first > 0:
            move_pct = (last / first - 1.0) * 100.0

            if move_pct <= -abs(bleed_drop_pct) and down_steps >= bleed_min_steps:
                return Signal(
                    -0.60,
                    "DOWN",
                    _rsi(closes, int(rsi_period)),
                    f"慢跌覆盖：{move_pct:.3f}% 于最近 {bleed_lookback} 根真实 K 线（down_steps={down_steps}/{bleed_lookback})",
                )

            if move_pct >= abs(bleed_rise_pct) and up_steps >= bleed_min_steps:
                return Signal(
                    0.60,
                    "UP",
                    _rsi(closes, int(rsi_period)),
                    f"慢跌覆盖：{move_pct:.3f}% 于最近 {bleed_lookback} 根真实 K 线（up_steps={up_steps}/{bleed_lookback})",
                )


    # 3) 基于真实收盘价的 EMA 交叉 + 死区 + 确认
    ef = _ema(closes[-max(len(closes), int(ema_slow) * 3):], int(ema_fast))
    es = _ema(closes[-max(len(closes), int(ema_slow) * 3):], int(ema_slow))

    if es <= 0:
        return Signal(0.0, "FLAT", _rsi(closes, int(rsi_period)), "EMA 基准无效")

    diff_pct = ((ef - es) / es) * 100.0
    deadband = abs(float(ema_deadband_pct))

    # 使用最近 K 个 EMA 差值确认（通过最近 K 个收盘价重新计算 EMA 差值近似）
    k = max(1, int(confirm_candles))
    diffs = []
    tail = closes[-(max(k + 20, int(ema_slow) + k + 5)):]  # 小尾部窗口
    for i in range(k):
        sub = tail[: len(tail) - (k - 1 - i)]
        if len(sub) < int(ema_slow) + 2:
            continue
        ef_i = _ema(sub, int(ema_fast))
        es_i = _ema(sub, int(ema_slow))
        if es_i > 0:
            diffs.append((ef_i - es_i) / es_i)

    confirmed_up = len(diffs) >= k and all(d > 0 for d in diffs[-k:])
    confirmed_down = len(diffs) >= k and all(d < 0 for d in diffs[-k:])

    rsi = _rsi(closes, int(rsi_period))

    # 死区 -> FLAT
    if abs(diff_pct) < deadband:
        return Signal(
            0.0,
            "FLAT",
            rsi,
            f"死区：|EMA diff| {abs(diff_pct):.4f}% < {deadband:.4f}% (EMA{ema_fast}={ef:.5g}, EMA{ema_slow}={es:.5g}, RSI={rsi:.2f})",
        )

    if confirmed_up and diff_pct > 0:
        return Signal(0.60, "UP", rsi, f"真实：EMA{ema_fast}={ef:.5g} > EMA{ema_slow}={es:.5g}, RSI={rsi:.2f}")
    if confirmed_down and diff_pct < 0:
        return Signal(-0.60, "DOWN", rsi, f"真实：EMA{ema_fast}={ef:.5g} < EMA{ema_slow}={es:.5g}, RSI={rsi:.2f}")

    # 未确认 -> FLAT
    return Signal(0.0, "FLAT", rsi, f"未确认：EMA diff {diff_pct:.4f}%（需要 {k} 根 K 线确认)")
