#!/usr/bin/env python3
import argparse
import csv
import os
from datetime import datetime

CSV_HEADER = [
    "date",
    "symbol",
    "action",
    "price_per_g",
    "weight_g",
    "fee_per_g",
    "all_in_price_per_g",
    "cash_flow",
    "pnl_realized",
    "position_g",
    "avg_cost_per_g",
]


def ensure_csv(path: str) -> None:
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADER)


def append_trade(path: str, date: str, symbol: str, action: str, price: float, weight: float, fee: float) -> None:
    all_in = price + fee if action == "buy" else price - fee
    cash_flow = -(all_in * weight) if action == "buy" else (all_in * weight)

    row = [
        date,
        symbol,
        action,
        f"{price:.2f}",
        f"{weight:.2f}",
        f"{fee:.2f}",
        f"{all_in:.2f}",
        f"{cash_flow:.2f}",
        "0.00",
        "",
        "",
    ]

    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(row)


def main() -> None:
    p = argparse.ArgumentParser(description="追加一笔贵金属交易到台账")
    p.add_argument("--csv", default="records/trades.csv")
    p.add_argument("--symbol", default="platinum", help="品种: platinum / gold / silver")
    p.add_argument("--action", required=True, choices=["buy", "sell"], help="buy 或 sell")
    p.add_argument("--price", required=True, type=float, help="单价（不含手续费）")
    p.add_argument("--weight", required=True, type=float, help="重量（克）")
    p.add_argument("--fee", default=1.0, type=float, help="每克手续费")
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="日期，默认今天")
    p.add_argument("--mark", type=float, default=None, help="可选：追加后按该价格估值（仅在 --symbol 指定时有意义）")
    args = p.parse_args()

    ensure_csv(args.csv)
    append_trade(args.csv, args.date, args.symbol, args.action, args.price, args.weight, args.fee)
    print(f"已记录: {args.date} [{args.symbol}] {args.action} {args.weight:.2f}g @ {args.price:.2f}, fee={args.fee:.2f}/g")

    cmd = f"python3 records/pnl_report.py --csv {args.csv} --symbol {args.symbol}"
    if args.mark is not None:
        cmd += f" --mark {args.mark}"
    os.system(cmd)


if __name__ == "__main__":
    main()
