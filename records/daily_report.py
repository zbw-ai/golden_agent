#!/usr/bin/env python3
"""每日贵金属分析报告（规则自动生成）。

数据源：
  1) 融通金自建日K —— 来自 monitor 落盘的 tick（records/prices/{symbol}_YYYY-MM-DD.csv）
     这是用户实际交易的销售价口径，是点位决策的基准。
  2) 国际盘日K —— 新浪财经全球期货（XAU/XAG/XPT），美元/盎司，
     仅用于长均线(MA20/60/120)和大趋势方向参考（与融通金有价差，不用于绝对点位）。

用法：
  python records/daily_report.py --session pre   # 盘前
  python records/daily_report.py --session post  # 收盘后
  python records/daily_report.py --session post --push   # 同时推送 PushPlus
"""
import argparse
import csv
import glob
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml

INTL_SYMBOL = {"platinum": "XPT", "gold": "XAU", "silver": "XAG"}
# 卖出综合成本（元/克）：价差 1.2 + 手续费 3
PLATINUM_SELL_COST = 4.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------- 自建日K ----------
def build_daily_k(symbol: str, price_dir: str) -> List[Dict[str, float]]:
    files = sorted(glob.glob(os.path.join(price_dir, f"{symbol}_*.csv")))
    out: List[Dict[str, float]] = []
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        prices = [float(r["price"]) for r in rows if r.get("price")]
        if not prices:
            continue
        date = os.path.basename(fp).replace(f"{symbol}_", "").replace(".csv", "")
        out.append({
            "date": date, "open": prices[0], "high": max(prices),
            "low": min(prices), "close": prices[-1], "ticks": len(prices),
        })
    return out


def ma(values: List[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


# ---------- 国际盘日K ----------
def fetch_intl_k(intl_sym: str) -> Optional[List[Dict[str, Any]]]:
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/"
           f"GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={intl_sym}")
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        m = re.search(r"(\[.*\])", r.text)
        if not m:
            return None
        return json.loads(m.group(1))
    except Exception:
        return None


def intl_trend(closes: List[float]) -> Tuple[str, Dict[str, Optional[float]]]:
    mas = {f"MA{n}": ma(closes, n) for n in (5, 20, 60, 120)}
    m5, m20, m60 = mas["MA5"], mas["MA20"], mas["MA60"]
    if None in (m5, m20, m60):
        return "数据不足", mas
    if m5 > m20 > m60:
        return "多头排列（上升趋势）", mas
    if m5 < m20 < m60:
        return "空头排列（下降趋势）", mas
    return "均线交织（震荡/转折）", mas


# ---------- 持仓 ----------
def compute_holding(trades_csv: str, symbol: str) -> Tuple[float, float, float]:
    pos, avg, realized = 0.0, 0.0, 0.0
    if not os.path.exists(trades_csv):
        return pos, avg, realized
    with open(trades_csv, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("symbol", "platinum").strip() or "platinum") != symbol:
                continue
            action = r["action"].strip().lower()
            price = float(r["price_per_g"]); weight = float(r["weight_g"]); fee = float(r["fee_per_g"])
            if action == "buy":
                total = avg * pos + (price + fee) * weight
                pos += weight
                avg = total / pos if pos > 0 else 0.0
            elif action == "sell":
                realized += (price - fee - avg) * weight
                pos -= weight
                if pos <= 1e-6:
                    pos, avg = 0.0, 0.0
    return pos, avg, realized


# ---------- 分析 ----------
def trend_label(price: float, m5: Optional[float], m10: Optional[float]) -> str:
    if m5 is None:
        return "数据不足"
    if m10 is None:
        return "短期偏强" if price >= m5 else "短期偏弱"
    if price >= m5 >= m10:
        return "短期多头（站上MA5/MA10）"
    if price <= m5 <= m10:
        return "短期空头（跌破MA5/MA10）"
    return "短期震荡（缠绕均线）"


def signal_for_holding(price: float, lv: Dict[str, float], pos: float, avg: float) -> str:
    if pos <= 0:
        return "无持仓。"
    if price <= lv["buy_2_lte"]:
        return f"⚠️ 跌破深度位 {lv['buy_2_lte']}，进入风控/补仓评估区。"
    if price <= lv["buy_1_lte"]:
        return f"⚠️ 跌破 {lv['buy_1_lte']}，反弹转弱预警，盯紧下一档 {lv['buy_2_lte']}。"
    if price >= lv["take_profit_main_gte"]:
        return f"✅ 触及主减仓/解套位 {lv['take_profit_main_gte']}，强烈建议清仓。"
    if price >= lv["take_profit_watch_gte"]:
        return f"✅ 触及减仓位 {lv['take_profit_watch_gte']}，果断减 100g。"
    if price >= lv["breakout_gte"]:
        return f"📈 突破 {lv['breakout_gte']}，准备减仓，下一目标 {lv['take_profit_watch_gte']}。"
    # 区间内
    up = lv["breakout_gte"] - price
    dn = price - lv["buy_1_lte"]
    return (f"区间内持有。距上方减仓信号 {lv['breakout_gte']} 还差 {up:.1f}，"
            f"距下方转弱 {lv['buy_1_lte']} 还有 {dn:.1f} 缓冲。")


def signal_for_watch(price: float, lv: Dict[str, float]) -> str:
    if price <= lv["buy_2_lte"]:
        return f"⚠️ 跌破 {lv['buy_2_lte']}，深度支撑位，可评估小仓试探。"
    if price <= lv["buy_1_lte"]:
        return f"跌破 {lv['buy_1_lte']}，转弱/回踩，观望或等更明确企稳。"
    if price >= lv["take_profit_watch_gte"]:
        return f"✅ 突破 {lv['take_profit_watch_gte']}，强势延续。"
    if price >= lv["breakout_gte"]:
        return f"📈 突破 {lv['breakout_gte']}，反弹延续信号。"
    return f"区间内，无明确买卖点；突破 {lv['breakout_gte']} 或回踩 {lv['buy_1_lte']} 再看。"


def analyze(symbol: str, disp: str, lv: Dict[str, float], price_dir: str,
            trades_csv: str) -> Dict[str, Any]:
    dk = build_daily_k(symbol, price_dir)
    closes = [d["close"] for d in dk]
    price = closes[-1] if closes else None
    self_ma = {f"MA{n}": ma(closes, n) for n in (5, 10, 20)}

    intl = fetch_intl_k(INTL_SYMBOL[symbol])
    intl_closes = [float(d["close"]) for d in intl] if intl else []
    itrend, imas = intl_trend(intl_closes) if intl_closes else ("无数据", {})
    intl_last = intl[-1] if intl else None

    pos, avg, realized = compute_holding(trades_csv, symbol)

    return {
        "symbol": symbol, "disp": disp, "price": price, "levels": lv,
        "daily_k": dk[-6:], "self_ma": self_ma,
        "trend": trend_label(price, self_ma["MA5"], self_ma["MA10"]) if price else "无数据",
        "intl_trend": itrend, "intl_mas": imas, "intl_last": intl_last,
        "pos": pos, "avg": avg, "realized": realized,
    }


def render(session: str, analyses: List[Dict[str, Any]], now: str) -> str:
    title = "盘前" if session == "pre" else "收盘"
    lines = [f"## 贵金属{title}日报  `{now}`", ""]
    for a in analyses:
        p = a["price"]
        lines.append(f"### {a['disp']}")
        if p is None:
            lines.append("- 暂无数据\n")
            continue
        sm = a["self_ma"]
        sm_str = "  ".join(f"{k}={v:.2f}" for k, v in sm.items() if v is not None) or "数据积累中"
        lines.append(f"- 现价(融通金): **{p:.2f}**　自建{sm_str}")
        lines.append(f"- 短期趋势: {a['trend']}　国际盘: {a['intl_trend']}")
        if a["pos"] > 0:
            unreal = (p - a["avg"]) * a["pos"]
            landed = (p - PLATINUM_SELL_COST - a["avg"]) * a["pos"]
            lines.append(f"- 持仓: {a['pos']:.0f}g @ {a['avg']:.2f}　"
                         f"浮亏(账面) {unreal:+.0f} / 落袋 {landed:+.0f}　已实现 {a['realized']:+.0f}")
            lines.append(f"- 策略: {signal_for_holding(p, a['levels'], a['pos'], a['avg'])}")
        else:
            lines.append(f"- 策略: {signal_for_watch(p, a['levels'])}")
        lines.append("")
    lines.append("> 自动生成 · 融通金价为交易基准，国际盘仅供趋势参考 · 非投资建议")
    return "\n".join(lines)


def push_pushplus(token: str, topic: str, title: str, content: str) -> None:
    payload = {"token": token, "title": title, "content": content, "template": "markdown"}
    if topic:
        payload["topic"] = topic
    r = requests.post("http://www.pushplus.plus/send", json=payload, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise RuntimeError(f"PushPlus 失败: {data}")


def run(config_path: str, session: str, push: bool) -> str:
    config = load_config(config_path)
    price_dir = os.path.join(ROOT, config.get("price_log", {}).get("dir", "records/prices"))
    trades_csv = os.path.join(ROOT, "records/trades.csv")

    symbols = config.get("symbols", {})
    analyses = []
    for name, cfg in symbols.items():
        lv = {k: float(v) for k, v in cfg["levels"].items()}
        analyses.append(analyze(name, cfg.get("display_name", name), lv, price_dir, trades_csv))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report = render(session, analyses, now)

    # 本地存档
    rep_dir = os.path.join(ROOT, "records/reports")
    os.makedirs(rep_dir, exist_ok=True)
    fname = f"{datetime.now().strftime('%Y-%m-%d')}_{session}.md"
    with open(os.path.join(rep_dir, fname), "w", encoding="utf-8") as f:
        f.write(report)

    if push:
        pp = config.get("pushplus", {})
        token = pp.get("token", "").strip()
        if token:
            title = f"贵金属{'盘前' if session == 'pre' else '收盘'}日报 {datetime.now().strftime('%m-%d')}"
            push_pushplus(token, pp.get("topic", ""), title, report)

    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="每日贵金属分析报告")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--session", choices=["pre", "post"], default="post")
    ap.add_argument("--push", action="store_true", help="推送 PushPlus")
    args = ap.parse_args()
    print(run(args.config, args.session, args.push))


if __name__ == "__main__":
    main()
