#!/usr/bin/env python3
import argparse
import json
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml


@dataclass
class TriggerEvent:
    key: str
    title: str
    description: str


@dataclass
class SymbolConfig:
    name: str
    display_name: str
    row_match: str
    levels: Optional[Dict[str, float]]


class PriceFetcher:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        mock_cfg = config.get("mock_mode", {})
        start = mock_cfg.get("start_price", 426.0)
        if isinstance(start, dict):
            self.mock_prices: Dict[str, float] = {k: float(v) for k, v in start.items()}
        else:
            self.mock_prices = {}
            self._mock_default = float(start)

    def get_all_prices(self, symbols: List[SymbolConfig]) -> Dict[str, Tuple[float, str]]:
        mock_cfg = self.config.get("mock_mode", {})
        if mock_cfg.get("enabled", False):
            volatility = float(mock_cfg.get("volatility", 1.2))
            result: Dict[str, Tuple[float, str]] = {}
            for sym in symbols:
                if sym.name not in self.mock_prices:
                    self.mock_prices[sym.name] = getattr(self, "_mock_default", 426.0)
                self.mock_prices[sym.name] += random.uniform(-volatility, volatility)
                self.mock_prices[sym.name] = round(self.mock_prices[sym.name], 2)
                result[sym.name] = (self.mock_prices[sym.name], "mock_mode")
            return result

        sources = self.config.get("price_sources", [])
        last_error: Optional[Exception] = None
        for source in sources:
            if not source.get("enabled", True):
                continue
            try:
                prices = self._fetch_from_source(source, symbols)
                src_name = source.get("name", "unknown_source")
                return {name: (p, src_name) for name, p in prices.items()}
            except Exception as exc:
                last_error = exc
                continue

        raise RuntimeError(f"所有数据源失败: {last_error}")

    def _fetch_from_source(self, source: Dict[str, Any], symbols: List[SymbolConfig]) -> Dict[str, float]:
        source_type = source.get("type", "json_api")
        if source_type == "rtj_quoteh5_playwright":
            return self._fetch_from_rtj_quoteh5(source, symbols)
        if len(symbols) > 1:
            raise ValueError(f"数据源 {source.get('name')} 仅支持单品种")
        sym = symbols[0]
        return {sym.name: self._fetch_from_json_api(source)}

    def _fetch_from_json_api(self, source: Dict[str, Any]) -> float:
        method = source.get("method", "GET").upper()
        url = source["url"]
        headers = source.get("headers", {})
        timeout = float(source.get("timeout_sec", 8))

        if method == "GET":
            resp = requests.get(url, headers=headers, timeout=timeout)
        else:
            resp = requests.request(method, url, headers=headers, timeout=timeout)

        resp.raise_for_status()

        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise ValueError(f"非 JSON 响应: {exc}")

        path = source.get("json_path")
        if path:
            value = self._extract_by_path(payload, path)
            return float(value)

        if isinstance(payload, dict):
            if "price" in payload:
                return float(payload["price"])
            if "data" in payload and isinstance(payload["data"], dict) and "price" in payload["data"]:
                return float(payload["data"]["price"])

        raise ValueError("无法从响应中提取 price")

    @staticmethod
    def _parse_first_float(text: str) -> float:
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if not m:
            raise ValueError(f"未识别到数值: {text}")
        return float(m.group(0))

    def _fetch_from_rtj_quoteh5(self, source: Dict[str, Any], symbols: List[SymbolConfig]) -> Dict[str, float]:
        from playwright.sync_api import sync_playwright

        url = source.get("url", "https://i.jzj9999.com/quoteh5/")
        timeout_ms = int(float(source.get("timeout_sec", 25)) * 1000)

        results: Dict[str, float] = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_selector("div.price-table-row", timeout=timeout_ms)

                rows = page.locator("div.price-table-row")
                n = rows.count()
                row_texts = [rows.nth(i).inner_text() for i in range(n)]

                for sym in symbols:
                    price = None
                    for i, txt in enumerate(row_texts):
                        if sym.row_match not in txt:
                            continue
                        cols = rows.nth(i).locator("div.el-col.el-col-6")
                        if cols.count() < 2:
                            continue
                        sale_text = cols.nth(1).inner_text().strip()
                        price = self._parse_first_float(sale_text)
                        break
                    if price is None:
                        raise ValueError(f"未在页面中找到 row_match={sym.row_match!r} ({sym.display_name})")
                    results[sym.name] = price
            finally:
                browser.close()
        return results

    @staticmethod
    def _extract_by_path(data: Any, path: str) -> Any:
        cur = data
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise ValueError(f"json_path 无效: {path}")
        return cur


class PushPlusNotifier:
    def __init__(self, token: str, topic: str = ""):
        self.token = token
        self.topic = topic

    def send(self, title: str, content: str) -> None:
        url = "http://www.pushplus.plus/send"
        payload = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": "markdown",
        }
        if self.topic:
            payload["topic"] = self.topic

        resp = requests.post(url, json=payload, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200:
            raise RuntimeError(f"PushPlus 发送失败: {data}")


class StrategyEngine:
    def __init__(self, display_name: str, levels: Dict[str, float]):
        self.display_name = display_name
        self.levels = levels
        self.prev_price: Optional[float] = None

    def evaluate(self, price: float) -> List[TriggerEvent]:
        events: List[TriggerEvent] = []
        p0 = self.prev_price
        self.prev_price = price

        if p0 is None:
            return events

        if p0 > self.levels["buy_1_lte"] >= price:
            events.append(TriggerEvent(
                key="buy_1_lte",
                title=f"{self.display_name}：第一买点/警报位",
                description=f"价格下破至 <= {self.levels['buy_1_lte']}，当前 {price:.2f} 元/克。",
            ))

        if p0 > self.levels["buy_2_lte"] >= price:
            events.append(TriggerEvent(
                key="buy_2_lte",
                title=f"{self.display_name}：第二买点/深度位",
                description=f"价格下破至 <= {self.levels['buy_2_lte']}，当前 {price:.2f} 元/克。",
            ))

        if p0 < self.levels["breakout_gte"] <= price:
            events.append(TriggerEvent(
                key="breakout_gte",
                title=f"{self.display_name}：突破/反弹起步",
                description=f"价格上破至 >= {self.levels['breakout_gte']}，当前 {price:.2f} 元/克。",
            ))

        if p0 < self.levels["take_profit_watch_gte"] <= price:
            events.append(TriggerEvent(
                key="take_profit_watch_gte",
                title=f"{self.display_name}：第一减仓位",
                description=f"价格上破至 >= {self.levels['take_profit_watch_gte']}，当前 {price:.2f} 元/克。",
            ))

        if p0 < self.levels["take_profit_main_gte"] <= price:
            events.append(TriggerEvent(
                key="take_profit_main_gte",
                title=f"{self.display_name}：主清仓/解套位",
                description=f"价格上破至 >= {self.levels['take_profit_main_gte']}，当前 {price:.2f} 元/克。",
            ))

        return events


class AlertDeduper:
    def __init__(self, cooldown_sec: int):
        self.cooldown_sec = cooldown_sec
        self.last_sent: Dict[str, float] = {}

    def should_send(self, event_key: str, now_ts: float) -> bool:
        t = self.last_sent.get(event_key)
        if t is None or now_ts - t >= self.cooldown_sec:
            self.last_sent[event_key] = now_ts
            return True
        return False


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_symbols(config: Dict[str, Any]) -> List[SymbolConfig]:
    required = ["buy_1_lte", "buy_2_lte", "breakout_gte", "take_profit_watch_gte", "take_profit_main_gte"]

    symbols_cfg = config.get("symbols")
    if symbols_cfg:
        result: List[SymbolConfig] = []
        for name, cfg in symbols_cfg.items():
            levels = cfg.get("levels")
            if levels:
                for k in required:
                    if k not in levels:
                        raise ValueError(f"symbols.{name}.levels 缺少 {k}")
                levels = {k: float(levels[k]) for k in required}
            else:
                levels = None
            result.append(SymbolConfig(
                name=name,
                display_name=cfg.get("display_name", name),
                row_match=cfg.get("row_match", ""),
                levels=levels,
            ))
        return result

    # Legacy single-symbol fallback
    levels = config.get("levels", {})
    for k in required:
        if k not in levels:
            raise ValueError(f"缺少 levels.{k}")
        levels[k] = float(levels[k])
    return [SymbolConfig(
        name="platinum",
        display_name=config.get("symbol", "融通金铂金"),
        row_match="铂 金",
        levels=levels,
    )]


def log_price(log_dir: str, symbol_name: str, price: float, source: str) -> None:
    os.makedirs(log_dir, exist_ok=True)
    now = datetime.now()
    fpath = os.path.join(log_dir, f"{symbol_name}_{now.strftime('%Y-%m-%d')}.csv")
    write_header = not os.path.exists(fpath)
    with open(fpath, "a", encoding="utf-8") as f:
        if write_header:
            f.write("timestamp,price,source\n")
        f.write(f"{now.strftime('%Y-%m-%d %H:%M:%S')},{price:.2f},{source}\n")


def format_msg(display_name: str, event: TriggerEvent, price: float, source: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"## {event.title}\n\n"
        f"- 品种: {display_name}\n"
        f"- 当前价: **{price:.2f} 元/克**\n"
        f"- 信号: {event.description}\n"
        f"- 数据源: `{source}`\n"
        f"- 时间: `{now}`\n"
    )


def _maybe_run_report(config_path: str, report_times: Dict[str, str], done: Dict[str, str]) -> None:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    cur = now.strftime("%H:%M")
    for session, sched in report_times.items():
        if done.get(session) == today:
            continue
        # 到点（含错过补发：当前时间 >= 计划时间且当天未发）
        if cur >= sched:
            try:
                import importlib.util
                _dr_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "records", "daily_report.py")
                _spec = importlib.util.spec_from_file_location("daily_report", _dr_path)
                daily_report = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(daily_report)
                daily_report.run(config_path, session, push=True)
                done[session] = today
                print(f"[{now.strftime('%H:%M:%S')}] [REPORT] {session} 日报已生成并推送")
            except Exception as exc:
                done[session] = today  # 失败也标记，避免循环重试刷屏
                print(f"[{now.strftime('%H:%M:%S')}] [REPORT_ERROR] {session}: {exc}")


def run(config_path: str) -> None:
    config = load_config(config_path)

    poll_interval = int(config.get("poll_interval_sec", 15))
    cooldown = int(config.get("cooldown_sec", 900))

    symbols = parse_symbols(config)
    if not symbols:
        raise ValueError("未配置任何 symbol")

    pp_cfg = config.get("pushplus", {})
    token = pp_cfg.get("token", "").strip()
    if not token:
        raise ValueError("请配置 pushplus.token")

    notifier = PushPlusNotifier(token=token, topic=pp_cfg.get("topic", ""))
    fetcher = PriceFetcher(config)
    engines: Dict[str, Optional[StrategyEngine]] = {}
    for s in symbols:
        engines[s.name] = StrategyEngine(s.display_name, s.levels) if s.levels else None
    deduper = AlertDeduper(cooldown)

    price_log_cfg = config.get("price_log", {})
    log_enabled = price_log_cfg.get("enabled", True)
    log_dir = price_log_cfg.get("dir", "records/prices")

    report_cfg = config.get("daily_report", {})
    report_enabled = report_cfg.get("enabled", False)
    report_times = {
        "pre": report_cfg.get("pre_time", "08:30"),
        "post": report_cfg.get("post_time", "21:00"),
    }
    report_done: Dict[str, str] = {}  # session -> 已生成的日期

    print(f"[START] 监控启动: {[s.display_name for s in symbols]}")
    print(f"[START] 轮询: {poll_interval}s 冷却: {cooldown}s")
    if log_enabled:
        print(f"[START] 价格日志目录: {log_dir}")
    if report_enabled:
        print(f"[START] 每日报告: 盘前 {report_times['pre']} / 收盘 {report_times['post']}")

    while True:
        if report_enabled:
            _maybe_run_report(config_path, report_times, report_done)
        try:
            prices = fetcher.get_all_prices(symbols)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            summary = "  ".join(f"{n}={p:.2f}" for n, (p, _) in prices.items())
            print(f"[{now}] {summary}")

            for sym in symbols:
                if sym.name not in prices:
                    continue
                price, source = prices[sym.name]

                if log_enabled:
                    try:
                        log_price(log_dir, sym.name, price, source)
                    except Exception as log_exc:
                        print(f"[{now}] [LOG_ERROR] {sym.name}: {log_exc}")

                engine = engines.get(sym.name)
                if engine is None:
                    continue
                events = engine.evaluate(price)
                ts = time.time()
                for e in events:
                    dedup_key = f"{sym.name}:{e.key}"
                    if deduper.should_send(dedup_key, ts):
                        content = format_msg(sym.display_name, e, price, source)
                        notifier.send(e.title, content)
                        print(f"[ALERT] {dedup_key} -> 已推送")
                    else:
                        print(f"[SKIP] {dedup_key} -> 冷却中")

        except Exception as exc:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now}] [ERROR] {exc}")

        time.sleep(poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="贵金属波段提醒工具（黄金/铂金/白银，微信 PushPlus）")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
