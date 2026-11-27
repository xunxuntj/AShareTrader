# AShareTrader

A 股跨资产 ETF 量化投研、防未来函数回测与全云端信号推送系统（**AKShare + Baostock 双源互备** + **Supabase 免费版** + **GitHub Actions 定时任务** + **飞书 Bot 卡片** + **Streamlit Community Cloud 仪表盘**）。

## 快速开始（本地开发与研究）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps

# 1. 运行单元测试
.\.venv\Scripts\python.exe -m pytest tests/ -v

# 2. 同步历史行情（默认未配置 Supabase 时自动使用本地 data/ SQLite+Parquet）
.\.venv\Scripts\python.exe -m ashare_trader.cli --local sync-data --start 2016-01-01

# 3. 运行多策略同口径回测
.\.venv\Scripts\python.exe -m ashare_trader.cli --local backtest --start 2018-01-01

# 4. 启动本地 Streamlit 仪表盘
.\.venv\Scripts\streamlit.exe run app.py
```

## 全云端零运维部署（不依赖本地电脑）

### 1. 初始化 Supabase（免费版）
1. 在 [Supabase](https://supabase.com/) 创建免费项目。
2. 进入 **SQL Editor**，粘贴并执行 [`sql/schema.sql`](sql/schema.sql)。
3. 在 **Project Settings -> API** 获取 `Project URL`（作为 `SUPABASE_URL`）和 `service_role` 秘钥（作为 `SUPABASE_KEY`）。

### 2. 配置飞书自定义机器人
1. 在飞书群聊点击 **设置 -> 群机器人 -> 添加自定义机器人**。
2. 勾选 **签名校验**，复制 Webhook 地址（`FEISHU_WEBHOOK_URL`）与签名密钥（`FEISHU_SECRET`）。

### 3. 配置 GitHub Actions 定时任务
将本项目推送至你的 GitHub 私有仓库，在 **Settings -> Secrets and variables -> Actions** 添加 4 个 Secrets：
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `FEISHU_WEBHOOK_URL`
- `FEISHU_SECRET`

工作流 `.github/workflows/daily_signal.yml` 将在每个交易日自动执行：
- **14:35（北京时间）**：拉取盘中实时快照、计算尾盘调仓与止损信号并推送飞书卡片。
- **16:30（北京时间）**：覆写正式收盘日线、更新回测快照（静默归档，遇异常才告警）。

### 4. 部署 Streamlit Community Cloud 仪表盘
1. 登录 [Streamlit Community Cloud](https://share.streamlit.io/)，绑定上述 GitHub 仓库，主入口选择 `app.py`。
2. 在 **Advanced Settings -> Secrets** 中填入 TOML 格式配置：
   ```toml
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "eyJ..."
   FEISHU_WEBHOOK_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
   FEISHU_SECRET = "..."
   APP_PASSWORD = "your_write_password"
   ```
3. 点击 Deploy 即可获得全天候线上信号、回测、账本与数据健康监控看板。
