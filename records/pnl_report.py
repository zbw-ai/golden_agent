#!/usr/bin/env python3
import argparse
import csv
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class State:
    position_g: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    trade_rows: List[List[str]] = field(default_factory=list)


def parse_float(v: str) -> float:
    return float(v.strip())


def print_table(headers, data_rows) -> None:
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in data_rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def process_row(s: State, r: Dict[str, str]) -> Tuple[State, float]:
    action = r["action"].strip().lower()
    price = parse_float(r["price_per_g"])
    weight = parse_float(r["weight_g"])
    fee = parse_float(r["fee_per_g"])
    realized_this = 0.0

    if action == "buy":
        all_in = price + fee
        total_cost_before = s.avg_cost * s.position_g
        total_cost_after = total_cost_before + all_in * weight
        s.position_g += weight
        s.avg_cost = total_cost_after / s.position_g if s.position_g > 0 else 0.0

    elif action == "sell":
        if weight > s.position_g + 1e-6:
            raise ValueError(f"卖出超过持仓: sell={weight}, pos={s.position_g}")
        net_sell = price - fee
        realized_this = (net_sell - s.avg_cost) * weight
        s.realized_pnl += realized_this
        s.position_g -= weight
        if s.position_g <= 1e-6:
            s.position_g = 0.0
            s.avg_cost = 0.0
    else:
        raise ValueError(f"未知 action: {action}")

    return s, realized_this


def run(csv_path: str, mark_price: Optional[float], symbol_filter: Optional[str]) -> None:
    with open(csv_path, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    states: Dict[str, State] = {}

    for r in rows:
        sym = r.get("symbol", "platinum").strip() or "platinum"
        if symbol_filter and sym != symbol_filter:
            continue

        s = states.setdefault(sym, State())
        s, realized_this = process_row(s, r)

        action = r["action"].strip().lower()
        price = parse_float(r["price_per_g"])
        weight = parse_float(r["weight_g"])
        fee = parse_float(r["fee_per_g"])

        s.trade_rows.append([
            r["date"],
            sym,
            "买入" if action == "buy" else "卖出",
            f"{price:.2f}",
            f"{weight:.2f}",
            f"{fee:.2f}",
            f"{s.position_g:.2f}",
            f"{s.avg_cost:.2f}",
            f"{realized_this:.2f}",
        ])

    if not states:
        print("(无匹配交易记录)")
        return

    for sym, s in states.items():
        print(f"=== [{sym}] 每笔交易 ===")
        print_table(
            ["日期", "品种", "方向", "成交价", "克数", "手续费", "持仓", "均价", "本笔盈亏"],
            s.trade_rows,
        )
        print()

        unrealized = 0.0
        if mark_price is not None and s.position_g > 0:
            unrealized = (mark_price - s.avg_cost) * s.position_g

        total = s.realized_pnl + unrealized

        print(f"=== [{sym}] 总体盈亏 ===")
        summary_rows = [
            ["交易笔数", f"{len(s.trade_rows)}"],
            ["当前持仓", f"{s.position_g:.2f} g"],
            ["持仓均价(含手续费)", f"{s.avg_cost:.2f} 元/克"],
            ["已实现盈亏", f"{s.realized_pnl:.2f} 元"],
        ]
        if mark_price is not None:
            summary_rows.extend([
                ["估值单价", f"{mark_price:.2f} 元/克"],
                ["未实现盈亏", f"{unrealized:.2f} 元"],
            ])
        summary_rows.append(["总盈亏", f"{total:.2f} 元"])
        print_table(["项目", "数值"], summary_rows)
        print()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="贵金属交易盈亏汇总（按品种分桶）")
    p.add_argument("--csv", default="records/trades.csv")
    p.add_argument("--symbol", default=None, help="仅汇总该品种；不填则按品种全部输出")
    p.add_argument("--mark", type=float, default=None, help="按该价格估算未实现盈亏（建议配合 --symbol 使用）")
    args = p.parse_args()
    run(args.csv, args.mark, args.symbol)
