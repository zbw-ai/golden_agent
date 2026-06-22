# 贵金属波段提醒系统（微信版）

低频波段提醒工具，监控**融通金**的黄金 / 铂金 / 白银价格：

- 实时抓取融通金销售价（Playwright 渲染网页）
- 命中关键点位时推送微信通知（PushPlus）
- 每日自动生成分析报告（盘前 + 收盘），存档 + 推送
- 价格按日落盘，可合成自建日K
- **非自动交易，不下单**

## 1. 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## 2. 配置

仓库**只提供 `config.example.yaml` 模板，不含真实密钥**。你需要基于它创建自己的 `config.yaml`：

### 第 1 步：复制出自己的配置文件

```bash
cp config.example.yaml config.yaml
```

### 第 2 步：注册 PushPlus，获取 token

1. 打开 [https://www.pushplus.plus](https://www.pushplus.plus)，用微信扫码登录
2. 登录后在首页「一对一推送」处即可看到你的 **token**（一串字符）
3. 关注它的公众号，否则收不到推送

### 第 3 步：把 token 填进 config.yaml

编辑 `config.yaml`，把占位符换成你自己的 token：

```yaml
pushplus:
  token: "这里填你在 PushPlus 拿到的 token"   # 替换掉 YOUR_PUSHPLUS_TOKEN
  topic: ""                                  # 群组推送才需要，个人推送留空
```

### 第 4 步（可选）：调整点位和报告时间

- `symbols` 下各品种的 `levels`：改成你自己关注的买卖点位（元/克）
- `daily_report` 的 `pre_time` / `post_time`：盘前/收盘报告时间

> ⚠️ `config.yaml` 含你的个人密钥，已被 `.gitignore` 排除，**不会被上传到 GitHub**。永远不要把真实 token 提交到仓库。

## 3. 运行

```bash
python3 monitor.py --config config.yaml
```

常驻进程会：每 `poll_interval_sec` 抓一次价 → 命中点位推送 → 按日落盘 → 到点（默认 08:30 / 21:00）自动生成日报。

## 4. 配置说明（config.yaml）

```yaml
symbols:
  platinum:                    # 品种 key（也是日志文件前缀）
    display_name: "融通金铂金"
    row_match: "铂 金"          # 页面表格里用于定位该行的文字
    levels:
      buy_1_lte:  ...          # 跌破 → 买点/警报
      buy_2_lte:  ...          # 跌破 → 深度位
      breakout_gte: ...        # 突破 → 反弹/突破信号
      take_profit_watch_gte: ...  # 减仓位
      take_profit_main_gte:  ...  # 主减仓/解套位
```

## 5. 记账与盈亏（可选）

```bash
# 记一笔交易
python3 records/add_trade.py --symbol platinum --action buy --price 424.7 --weight 100 --fee 1

# 查盈亏（按品种）
python3 records/pnl_report.py --symbol platinum --mark 380
```

## 6. 每日报告

monitor 常驻进程会到点自动跑；也可手动生成：

```bash
python3 records/daily_report.py --session pre        # 盘前
python3 records/daily_report.py --session post --push # 收盘 + 推送微信
```

报告 = 融通金自建日K（点位基准）+ 新浪国际盘日K（趋势/长均线参考），规则化输出趋势与操作提示。

## 7. 数据源说明

- **价格基准**：融通金网页 `https://i.jzj9999.com/quoteh5/`（Playwright 渲染后读"销售价"）
- **趋势参考**：新浪财经国际期货日K（XAU/XAG/XPT，美元/盎司，与融通金有价差，仅看趋势方向）

## 8. 联调模式

没有真实接口时可先开 mock 验证推送链路：

```yaml
mock_mode:
  enabled: true
```

## 免责声明

本工具仅做价格提醒与数据分析，所有结论为规则/技术面自动生成，**不构成投资建议**。贵金属价格受宏观、地缘等多重因素影响，交易决策与风险自负。
