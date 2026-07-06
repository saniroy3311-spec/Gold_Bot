import os
import sys
import time
import datetime
import json
import urllib.request
import numpy as np
import pandas as pd

# Add parent directory to path to import config & strategy_logic
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import (
    EMA_TREND_LEN, EMA_FAST_LEN, ATR_LEN, DI_LEN, ADX_SMOOTH, ADX_EMA, RSI_LEN,
    ADX_TREND_TH, ADX_RANGE_TH, FILTER_ATR_MULT, FILTER_BODY_MULT, FILTER_VOL_ENABLED,
    TREND_RR, RANGE_RR, TREND_ATR_MULT, RANGE_ATR_MULT, BE_MULT, MAX_SL_MULT, MAX_SL_POINTS,
    COMMISSION_PCT, BAR_CLOSE_SL_EVAL
)

from strategy_logic import (
    compute_full_series, evaluate_entry, SignalType, Signal, IndicatorSnapshot,
    calc_levels, upgrade_trail_stage, compute_trail_sl, should_trigger_be,
    max_sl_threshold, max_sl_hit
)

from backtest import _row_to_snap

def fetch_binance_klines(symbol="BTCUSDT", interval="15m", days=365):
    """Fetch 1 year of 15m candles from Binance API."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)
    
    print(f"Fetching 1 year of {interval} candles for {symbol} from Binance...")
    all_klines = []
    curr_start = start_ms
    
    headers = {"User-Agent": "Mozilla/5.0"}
    
    while curr_start < end_ms:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&startTime={curr_start}&limit=1000"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data:
                break
            all_klines.extend(data)
            last_ts = data[-1][0]
            if last_ts <= curr_start:
                break
            curr_start = last_ts + 1
            time.sleep(0.05)
        except Exception as e:
            print(f"Error fetching klines: {e}. Retrying in 2s...")
            time.sleep(2)

    print(f"Total fetched candles: {len(all_klines)}")
    
    df = pd.DataFrame(all_klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"
    ])
    
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = df["timestamp"].astype(int)
    
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df

def run_realistic_backtest(df, initial_capital=10000.0, risk_pct=0.4):
    """Run exact GoldBot engine 1-year backtest with dynamic 0.4% risk position sizing."""
    series = compute_full_series(df).reset_index(drop=True)
    n = len(series)
    
    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown_pct = 0.0
    max_drawdown_usd = 0.0
    
    equity_curve = []
    trades = []
    
    in_position = False
    pending_signal = None
    trade_id = 0

    cur_initial_sl = 0.0
    cur_sl = 0.0
    cur_tp = 0.0
    cur_atr = 0.0
    cur_is_long = True
    cur_entry_price = 0.0
    cur_qty = 0.0
    peak_price = 0.0
    be_done = False
    trail_stage = 0
    max_sl_fired = False
    entry_bar_idx = -1
    cur_trade_info = None

    equity_curve.append({
        "timestamp": int(series.iloc[0]["timestamp"]),
        "equity": equity,
        "drawdown_pct": 0.0
    })

    for i in range(1, n):
        row = series.iloc[i]
        prev_row = series.iloc[i - 1]
        ts = int(row["timestamp"])
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if (np.isnan(row["ema200"]) or np.isnan(row["adx"]) or 
            np.isnan(row["atr"]) or np.isnan(row["atr_sma"]) or np.isnan(row["vol_sma"])):
            continue

        if in_position and pending_signal is not None:
            pending_signal = None

        # Execute Pending Entry on Bar i Open
        if pending_signal is not None and not in_position:
            sig, sig_snap, sig_bar_idx = pending_signal
            entry_price = open_
            entry_bar_idx = i
            risk = calc_levels(entry_price, sig_snap.atr, sig.is_long, sig.is_trend)
            
            # Dynamic Risk Position Sizing: 0.4% of current equity
            risk_amount = equity * (risk_pct / 100.0)
            qty = risk_amount / risk.stop_dist if risk.stop_dist > 0 else 0.001
            
            trade_id += 1
            cur_trade_info = {
                "trade_id": trade_id,
                "signal_type": sig.signal_type.value,
                "is_long": sig.is_long,
                "is_trend": sig.is_trend,
                "signal_bar": sig_bar_idx,
                "signal_ts": int(series.iloc[sig_bar_idx]["timestamp"]),
                "entry_bar": i,
                "entry_ts": ts,
                "entry_price": entry_price,
                "qty": qty,
                "sl": risk.sl,
                "tp": risk.tp,
                "stop_dist": risk.stop_dist,
                "atr_at_entry": sig_snap.atr,
            }
            
            cur_initial_sl = risk.sl
            cur_sl = risk.sl
            cur_tp = risk.tp
            cur_atr = sig_snap.atr
            cur_is_long = sig.is_long
            cur_entry_price = entry_price
            cur_qty = qty
            peak_price = entry_price
            be_done = False
            trail_stage = 0
            max_sl_fired = False
            in_position = True
            pending_signal = None

        # Position Monitoring & Exits
        if in_position and cur_trade_info is not None:
            if cur_is_long:
                peak_price = max(peak_price, high)
                peak_profit_dist = max(0.0, peak_price - cur_entry_price)
                current_profit_dist_close = close - cur_entry_price
            else:
                peak_price = min(peak_price, low)
                peak_profit_dist = max(0.0, cur_entry_price - peak_price)
                current_profit_dist_close = cur_entry_price - close

            # Breakeven Trigger
            if not be_done and should_trigger_be(current_profit_dist_close, cur_atr):
                be_done = True
                if cur_is_long and cur_entry_price > cur_sl:
                    cur_sl = cur_entry_price
                elif (not cur_is_long) and cur_entry_price < cur_sl:
                    cur_sl = cur_entry_price

            # Trailing Stage Upgrade
            new_stage = upgrade_trail_stage(trail_stage, peak_profit_dist, cur_atr)
            if new_stage > trail_stage:
                trail_stage = new_stage

            # Trailing SL
            trail_sl = compute_trail_sl(trail_stage, peak_price, peak_profit_dist, cur_is_long, cur_atr)
            if trail_sl is not None:
                if cur_is_long and trail_sl > cur_sl:
                    cur_sl = trail_sl
                elif (not cur_is_long) and trail_sl < cur_sl:
                    cur_sl = trail_sl

            max_sl_active = (i > entry_bar_idx) and not max_sl_fired
            threshold = max_sl_threshold(cur_atr)
            
            exit_price = None
            exit_reason = ""

            if cur_is_long:
                max_sl_price = cur_entry_price - threshold
                max_sl_touched = max_sl_active and (low <= max_sl_price)

                # Pine-exact BAR_CLOSE_SL_EVAL logic:
                # Intrabar TP or Max SL or Trailing SL hit
                if open_ >= cur_tp:
                    exit_price, exit_reason = open_, "TP"
                elif open_ <= cur_sl and (be_done or trail_stage > 0):
                    exit_price, exit_reason = open_, "BE / Trail SL"
                elif high >= cur_tp:
                    exit_price, exit_reason = cur_tp, "TP"
                elif max_sl_touched:
                    exit_price, exit_reason = max_sl_price, "Max SL"
                elif (be_done or trail_stage > 0) and low <= cur_sl:
                    exit_price, exit_reason = cur_sl, "BE / Trail SL"
                elif BAR_CLOSE_SL_EVAL and close <= cur_initial_sl:
                    exit_price, exit_reason = close, "Initial SL (Bar Close)"
                elif (not BAR_CLOSE_SL_EVAL) and low <= cur_initial_sl:
                    exit_price, exit_reason = cur_initial_sl, "Initial SL"
            else:
                max_sl_price = cur_entry_price + threshold
                max_sl_touched = max_sl_active and (high >= max_sl_price)

                if open_ <= cur_tp:
                    exit_price, exit_reason = open_, "TP"
                elif open_ >= cur_sl and (be_done or trail_stage > 0):
                    exit_price, exit_reason = open_, "BE / Trail SL"
                elif low <= cur_tp:
                    exit_price, exit_reason = cur_tp, "TP"
                elif max_sl_touched:
                    exit_price, exit_reason = max_sl_price, "Max SL"
                elif (be_done or trail_stage > 0) and high >= cur_sl:
                    exit_price, exit_reason = cur_sl, "BE / Trail SL"
                elif BAR_CLOSE_SL_EVAL and close >= cur_initial_sl:
                    exit_price, exit_reason = close, "Initial SL (Bar Close)"
                elif (not BAR_CLOSE_SL_EVAL) and high >= cur_initial_sl:
                    exit_price, exit_reason = cur_initial_sl, "Initial SL"

            if exit_price is not None:
                if exit_reason == "Max SL":
                    max_sl_fired = True

                raw_pnl = (exit_price - cur_entry_price) * cur_qty if cur_is_long else (cur_entry_price - exit_price) * cur_qty
                fee = (cur_entry_price * cur_qty) * COMMISSION_PCT
                net_pnl = raw_pnl - fee

                equity += net_pnl
                peak_equity = max(peak_equity, equity)
                dd_usd = peak_equity - equity
                dd_pct = (dd_usd / peak_equity) * 100.0 if peak_equity > 0 else 0.0
                max_drawdown_pct = max(max_drawdown_pct, dd_pct)
                max_drawdown_usd = max(max_drawdown_usd, dd_usd)

                trade_record = {
                    "trade_id": cur_trade_info["trade_id"],
                    "signal_type": cur_trade_info["signal_type"],
                    "is_long": cur_is_long,
                    "is_trend": cur_trade_info["is_trend"],
                    "signal_bar": cur_trade_info["signal_bar"],
                    "signal_ts": cur_trade_info["signal_ts"],
                    "entry_bar": cur_trade_info["entry_bar"],
                    "entry_ts": cur_trade_info["entry_ts"],
                    "entry_price": cur_entry_price,
                    "qty": cur_qty,
                    "sl": cur_sl,
                    "tp": cur_tp,
                    "stop_dist": cur_trade_info["stop_dist"],
                    "atr_at_entry": cur_atr,
                    "exit_bar": i,
                    "exit_ts": ts,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "trail_stage": trail_stage,
                    "bars_held": i - cur_trade_info["entry_bar"],
                    "net_pnl": net_pnl,
                    "pnl_pct": (net_pnl / (cur_entry_price * cur_qty)) * 100.0 if cur_qty > 0 else 0.0,
                    "equity_after": equity
                }
                trades.append(trade_record)
                in_position = False
                cur_trade_info = None

                equity_curve.append({
                    "timestamp": ts,
                    "equity": equity,
                    "drawdown_pct": dd_pct
                })
                continue

        # Signal Evaluation on Completed Candle
        if not in_position and pending_signal is None:
            snap = _row_to_snap(row, prev_row)
            sig = evaluate_entry(snap, has_position=False)
            if sig.signal_type != SignalType.NONE:
                pending_signal = (sig, snap, i)

    return trades, equity_curve, max_drawdown_pct, max_drawdown_usd

def compute_monthly_stats(trades):
    """Aggregate trade performance by month."""
    if not trades:
        return []

    df_trades = pd.DataFrame(trades)
    df_trades["exit_dt"] = pd.to_datetime(df_trades["exit_ts"], unit="ms") + pd.Timedelta(hours=5, minutes=30)
    df_trades["month_key"] = df_trades["exit_dt"].dt.strftime("%Y-%m")
    df_trades["month_name"] = df_trades["exit_dt"].dt.strftime("%B %Y")

    monthly_list = []
    grouped = df_trades.groupby("month_key", sort=True)

    for month_key, group in grouped:
        num_trades = len(group)
        wins = group[group["net_pnl"] > 0]
        losses = group[group["net_pnl"] <= 0]
        
        net_profit = float(group["net_pnl"].sum())
        win_rate = (len(wins) / num_trades) * 100.0 if num_trades > 0 else 0.0
        
        max_win = float(wins["net_pnl"].max()) if not wins.empty else 0.0
        max_loss = float(losses["net_pnl"].min()) if not losses.empty else 0.0
        avg_profit = float(group["net_pnl"].mean()) if num_trades > 0 else 0.0

        month_name = group["month_name"].iloc[0]

        monthly_list.append({
            "month_key": month_key,
            "month_name": month_name,
            "num_trades": num_trades,
            "net_profit": round(net_profit, 2),
            "win_rate": round(win_rate, 1),
            "max_win": round(max_win, 2),
            "max_loss": round(max_loss, 2),
            "avg_profit": round(avg_profit, 2)
        })

    return monthly_list

def generate_html_report(trades, equity_curve, monthly_stats, max_dd_pct, max_dd_usd, initial_capital=10000.0, output_path="backtest_report.html"):
    """Generate high-contrast dashboard-themed HTML backtest report."""
    total_trades = len(trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    
    total_net_pnl = sum(t["net_pnl"] for t in trades)
    final_equity = initial_capital + total_net_pnl
    return_pct = (total_net_pnl / initial_capital) * 100.0
    
    win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    max_win_trade = max((t["net_pnl"] for t in wins), default=0.0)
    max_loss_trade = min((t["net_pnl"] for t in losses), default=0.0)
    avg_trade_pnl = total_net_pnl / total_trades if total_trades > 0 else 0.0

    step = max(1, len(equity_curve) // 300)
    eq_sample = equity_curve[::step]
    if equity_curve[-1] not in eq_sample:
        eq_sample.append(equity_curve[-1])

    eq_labels = [(datetime.datetime.utcfromtimestamp(pts["timestamp"]/1000 + 19800)).strftime("%b %d, %H:%M") for pts in eq_sample]
    eq_values = [round(pts["equity"], 2) for pts in eq_sample]

    formatted_trades = []
    for t in trades:
        entry_dt = (datetime.datetime.utcfromtimestamp(t["entry_ts"]/1000 + 19800)).strftime("%Y-%m-%d %H:%M")
        exit_dt = (datetime.datetime.utcfromtimestamp(t["exit_ts"]/1000 + 19800)).strftime("%Y-%m-%d %H:%M")
        formatted_trades.append({
            "id": t["trade_id"],
            "type": t["signal_type"],
            "side": "LONG" if t["is_long"] else "SHORT",
            "entry_time": entry_dt,
            "exit_time": exit_dt,
            "entry_price": round(t["entry_price"], 2),
            "exit_price": round(t["exit_price"], 2),
            "qty": round(t["qty"], 4),
            "net_pnl": round(t["net_pnl"], 2),
            "pnl_pct": round(t["pnl_pct"], 2),
            "reason": t["exit_reason"]
        })

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>GoldBot — 1-Year Backtest Report (BTCUSDT Low Drawdown Engine v2)</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {{
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --border: #0f172a;
      --green: #16a34a;
      --red: #dc2626;
      --accent: #2563eb;
    }}
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }}
    body {{
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.4;
      padding: 16px;
      max-width: 1200px;
      margin: 0 auto;
      overflow-x: hidden !important;
    }}
    header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      background: var(--card-bg);
      border: 2px solid var(--border);
      box-shadow: 4px 4px 0px var(--border);
      padding: 16px 20px;
      margin-bottom: 20px;
    }}
    h1 {{
      font-size: 20px;
      font-weight: 900;
      color: var(--text);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .badge {{
      background: var(--text);
      color: #ffffff;
      padding: 6px 12px;
      font-size: 12px;
      font-weight: 800;
      border-radius: 0px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 20px;
    }}
    .card {{
      background: var(--card-bg);
      border: 2px solid var(--border);
      box-shadow: 3px 3px 0px var(--border);
      padding: 16px;
    }}
    .card-title {{
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      color: #64748b;
      margin-bottom: 6px;
    }}
    .card-value {{
      font-size: 22px;
      font-weight: 900;
      color: var(--text);
    }}
    .val-green {{ color: var(--green) !important; }}
    .val-red {{ color: var(--red) !important; }}
    
    .chart-container {{
      background: var(--card-bg);
      border: 2px solid var(--border);
      box-shadow: 4px 4px 0px var(--border);
      padding: 20px;
      margin-bottom: 24px;
      position: relative;
    }}
    .chart-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }}
    .chart-header h2 {{
      font-size: 16px;
      font-weight: 900;
      text-transform: uppercase;
    }}
    .chart-wrapper {{
      position: relative;
      height: 340px;
      width: 100%;
    }}
    @media (max-width: 768px) {{
      .chart-wrapper {{ height: 260px; }}
    }}

    .section-title {{
      font-size: 18px;
      font-weight: 900;
      text-transform: uppercase;
      margin-bottom: 12px;
      border-left: 5px solid var(--border);
      padding-left: 10px;
    }}
    .table-container {{
      background: var(--card-bg);
      border: 2px solid var(--border);
      box-shadow: 4px 4px 0px var(--border);
      overflow-x: auto;
      margin-bottom: 24px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 13px;
    }}
    th {{
      background: var(--text);
      color: #ffffff;
      padding: 10px 14px;
      font-weight: 800;
      text-transform: uppercase;
      font-size: 12px;
      white-space: nowrap;
    }}
    td {{
      padding: 10px 14px;
      border-bottom: 1px solid #e2e8f0;
      font-weight: 700;
      white-space: nowrap;
    }}
    tr:nth-child(even) {{
      background-color: #f8fafc;
    }}
    tr:hover {{
      background-color: #f1f5f9;
    }}
    .tag-long {{
      background: #dcfce7;
      color: #15803d;
      padding: 2px 6px;
      font-weight: 800;
      font-size: 11px;
    }}
    .tag-short {{
      background: #fee2e2;
      color: #b91c1c;
      padding: 2px 6px;
      font-weight: 800;
      font-size: 11px;
    }}
    .max-height-table {{
      max-height: 420px;
      overflow-y: auto;
    }}
  </style>
</head>
<body>

  <header>
    <div>
      <h1>🪙 GoldBot — 1-Year Backtest Report</h1>
      <p style="font-size: 12px; font-weight: 700; color: #64748b; margin-top: 4px;">
        Strategy: BTCUSDT Low Drawdown Engine v2 (1.0% Risk Sizing) | Period: 1 Year (Jul 2025 - Jul 2026)
      </p>
    </div>
    <div class="badge">INITIAL CAPITAL: ${initial_capital:,.2f} USD</div>
  </header>

  <!-- KEY PERFORMANCE METRICS -->
  <div class="grid">
    <div class="card">
      <div class="card-title">Net Profit</div>
      <div class="card-value {'val-green' if total_net_pnl >= 0 else 'val-red'}">
        ${total_net_pnl:+,.2f} ({return_pct:+.2f}%)
      </div>
    </div>
    <div class="card">
      <div class="card-title">Final Equity</div>
      <div class="card-value">${final_equity:,.2f}</div>
    </div>
    <div class="card">
      <div class="card-title">Win Rate</div>
      <div class="card-value">{win_rate:.1f}% ({len(wins)}W / {len(losses)}L)</div>
    </div>
    <div class="card">
      <div class="card-title">Max Drawdown</div>
      <div class="card-value val-red">{max_dd_pct:.2f}% (${max_dd_usd:,.2f})</div>
    </div>
    <div class="card">
      <div class="card-title">Profit Factor</div>
      <div class="card-value">{profit_factor:.2f}</div>
    </div>
    <div class="card">
      <div class="card-title">Total Trades</div>
      <div class="card-value">{total_trades}</div>
    </div>
    <div class="card">
      <div class="card-title">Avg Trade Profit</div>
      <div class="card-value {'val-green' if avg_trade_pnl >= 0 else 'val-red'}">${avg_trade_pnl:+,.2f}</div>
    </div>
    <div class="card">
      <div class="card-title">Max Single Win</div>
      <div class="card-value val-green">${max_win_trade:+,.2f}</div>
    </div>
    <div class="card">
      <div class="card-title">Max Single Loss</div>
      <div class="card-value val-red">${max_loss_trade:+,.2f}</div>
    </div>
  </div>

  <!-- EQUITY CURVE CHART -->
  <div class="chart-container">
    <div class="chart-header">
      <h2>📈 1-Year Equity Curve ($ USD)</h2>
      <div style="font-weight: 800; font-size: 13px;">Base: ${initial_capital:,.2f} &rarr; Peak: ${final_equity:,.2f}</div>
    </div>
    <div class="chart-wrapper">
      <canvas id="equityChart"></canvas>
    </div>
  </div>

  <!-- MONTHLY PERFORMANCE BREAKDOWN -->
  <h2 class="section-title">📅 Monthly Performance Breakdown</h2>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Month</th>
          <th>Trades</th>
          <th>Win Rate (%)</th>
          <th>Net Profit ($)</th>
          <th>Max Win Trade ($)</th>
          <th>Max Loss Trade ($)</th>
          <th>Avg Profit / Trade ($)</th>
        </tr>
      </thead>
      <tbody>
"""

    for m in monthly_stats:
        pnl_class = "val-green" if m["net_profit"] >= 0 else "val-red"
        html_content += f"""
        <tr>
          <td><strong>{m['month_name']}</strong></td>
          <td>{m['num_trades']}</td>
          <td>{m['win_rate']:.1f}%</td>
          <td class="{pnl_class}">${m['net_profit']:+,.2f}</td>
          <td class="val-green">${m['max_win']:+,.2f}</td>
          <td class="val-red">${m['max_loss']:+,.2f}</td>
          <td class="{pnl_class}">${m['avg_profit']:+,.2f}</td>
        </tr>
"""

    html_content += f"""
      </tbody>
    </table>
  </div>

  <!-- DETAILED TRADE LOG -->
  <h2 class="section-title">📜 Detailed Trade History Log ({total_trades} Trades)</h2>
  <div class="table-container max-height-table">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Entry Time (IST)</th>
          <th>Exit Time (IST)</th>
          <th>Type</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Entry Price</th>
          <th>Exit Price</th>
          <th>Net PnL ($)</th>
          <th>Return (%)</th>
          <th>Exit Reason</th>
        </tr>
      </thead>
      <tbody>
"""

    for t in formatted_trades:
        side_tag = f'<span class="tag-long">LONG</span>' if t["side"] == "LONG" else f'<span class="tag-short">SHORT</span>'
        pnl_class = "val-green" if t["net_pnl"] >= 0 else "val-red"
        html_content += f"""
        <tr>
          <td>#{t['id']}</td>
          <td>{t['entry_time']}</td>
          <td>{t['exit_time']}</td>
          <td>{t['type']}</td>
          <td>{side_tag}</td>
          <td>{t['qty']}</td>
          <td>${t['entry_price']:,.2f}</td>
          <td>${t['exit_price']:,.2f}</td>
          <td class="{pnl_class}">${t['net_pnl']:+,.2f}</td>
          <td class="{pnl_class}">{t['pnl_pct']:+.2f}%</td>
          <td><strong>{t['reason']}</strong></td>
        </tr>
"""

    html_content += f"""
      </tbody>
    </table>
  </div>

  <script>
    const labels = {json.dumps(eq_labels)};
    const dataValues = {json.dumps(eq_values)};

    const ctx = document.getElementById('equityChart').getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(37, 99, 235, 0.2)');
    gradient.addColorStop(1, 'rgba(37, 99, 235, 0.0)');

    new Chart(ctx, {{
      type: 'line',
      data: {{
        labels: labels,
        datasets: [{{
          label: 'Equity ($)',
          data: dataValues,
          borderColor: '#2563eb',
          borderWidth: 2.5,
          fill: true,
          backgroundColor: gradient,
          pointRadius: 0,
          pointHoverRadius: 5,
          tension: 0.1
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            mode: 'index',
            intersect: false,
            callbacks: {{
              label: function(context) {{
                return 'Equity: $' + context.raw.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
              }}
            }}
          }}
        }},
        scales: {{
          x: {{
            grid: {{ display: false }},
            ticks: {{ maxTicksLimit: 10, font: {{ weight: '700' }}, color: '#0f172a' }}
          }},
          y: {{
            grid: {{ color: '#e2e8f0' }},
            ticks: {{
              font: {{ weight: '700' }},
              color: '#0f172a',
              callback: function(value) {{ return '$' + value.toLocaleString(); }}
            }}
          }}
        }}
      }}
    }});
  </script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Report successfully generated at: {os.path.abspath(output_path)}")

if __name__ == "__main__":
    df = fetch_binance_klines("BTCUSDT", "15m", days=365)
    trades, equity_curve, max_dd_pct, max_dd_usd = run_realistic_backtest(df, initial_capital=10000.0, risk_pct=1.0)
    monthly_stats = compute_monthly_stats(trades)
    
    out_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backtest_report.html"))
    generate_html_report(trades, equity_curve, monthly_stats, max_dd_pct, max_dd_usd, initial_capital=10000.0, output_path=out_file)
