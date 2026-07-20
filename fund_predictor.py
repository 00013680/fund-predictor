#!/usr/bin/env python3
"""
基金预测 v6 — 日级别在线学习（3日平滑目标）
功能：
1. 三层量化模型预测（日级别）
2. 在线学习：每有新净值数据，微调个体模型（3步SGD）
3. 技术指标特征（RSI, MACD, 均线乖离等）
4. 日级别交易策略（买入/持有/减持/观望）
5. 模型权重持久化（跨运行保留）
6. 精美HTML仪表盘
"""

import json, math, os, sqlite3, sys, time
from datetime import datetime, timedelta
from urllib.request import urlopen, Request

# ========== 交易记录（用户持仓） ==========
TRANSACTIONS = [
    # 买入
    {"code": "017938", "name": "易方达医疗C", "type": "买入", "amount": 2000, "date": "2026-07-15"},
    {"code": "022831", "name": "华商电子C", "type": "买入", "amount": 3000, "date": "2026-07-15"},
    {"code": "012087", "name": "博时健康C", "type": "买入", "amount": 3000, "date": "2026-07-16"},
    {"code": "014064", "name": "银华农业C", "type": "买入", "amount": 5000, "date": "2026-07-16"},
    {"code": "021753", "name": "南方电力C", "type": "买入", "amount": 2000, "date": "2026-07-16"},
    {"code": "017175", "name": "天弘绿色电力C", "type": "买入", "amount": 1000, "date": "2026-07-20"},
    # 卖出
    {"code": "012087", "name": "博时健康C", "type": "卖出", "shares": 4526.25, "date": "2026-07-17"},
    {"code": "027034", "name": "中银AI C", "type": "卖出", "shares": 2596.95, "date": "2026-07-17"},
    # 转换
    {"code": "022831", "name": "华商电子C", "type": "转出", "target": "015468", "target_name": "嘉实农业C", "shares": 726.62, "date": "2026-07-17"},
    {"code": "017938", "name": "易方达医疗C", "type": "转出", "target": "021753", "target_name": "南方电力C", "shares": 2724.05, "date": "2026-07-17"},
    {"code": "014064", "name": "银华农业C", "type": "转出", "target": "021753", "target_name": "南方电力C", "shares": 1421.10, "date": "2026-07-17"},
]

# ========== 配置 ==========
FUNDS = [
    # ── 现有持仓 ──
    {"code": "021753", "name": "南方电力C", "sector": "power", "short": "电力"},
    {"code": "014064", "name": "银华农业C", "sector": "agri", "short": "农业"},
    {"code": "017938", "name": "易方达医疗C", "sector": "med", "short": "医疗"},
    {"code": "015468", "name": "嘉实农业C", "sector": "agri", "short": "农业2"},
    {"code": "008886", "name": "富国军工C", "sector": "mil", "short": "军工"},
    {"code": "013596", "name": "招商煤炭C", "sector": "coal", "short": "煤炭"},
    # ── 新增 watchlist ──
    {"code": "017175", "name": "天弘绿色电力C", "sector": "green_power", "short": "绿电"},
    {"code": "012414", "name": "招商中证白酒C", "sector": "baijiu", "short": "白酒"},
    {"code": "014674", "name": "富国港通互联网C", "sector": "hk_internet", "short": "港股互联"},
    {"code": "008888", "name": "华夏半导体芯片C", "sector": "chip", "short": "半导体"},
    {"code": "012651", "name": "博时半导体C", "sector": "chip", "short": "半导体2"},
    {"code": "013014", "name": "华夏新能源车C", "sector": "new_energy", "short": "新能源车"},
]

SECTORS = {
    "power":       {"name": "电力", "index": "sz399808"},
    "agri":        {"name": "农业", "index": "sh000949"},
    "med":         {"name": "医药", "index": "sz399989"},
    "mil":         {"name": "军工", "index": "sz399959"},
    "coal":        {"name": "煤炭", "index": "sz399998"},
    "green_power": {"name": "绿色电力", "index": "sz399438"},
    "baijiu":      {"name": "白酒", "index": "sz399997"},
    "hk_internet": {"name": "港股互联", "index": "sh000001"},
    "chip":        {"name": "半导体芯片", "index": "sz980017"},
    "new_energy":  {"name": "新能源车", "index": "sz399976"},
}
MARKET_INDICES = {"上证指数": "sh000001", "创业板指": "sz399006"}

BEST_PARAMS = {
    "021753": (20, 0.01), "014064": (26, 0.01),
    "017938": (30, 0.01), "015468": (30, 1.00),
    "008886": (30, 0.01), "013596": (30, 0.01),
    # 新增 watchlist 默认参数
    "017175": (30, 0.01), "012414": (30, 0.01),
    "014674": (30, 0.01), "008888": (30, 0.01),
    "012651": (30, 0.01), "013014": (30, 0.01),
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PREDICTIONS_FILE = os.path.join(SCRIPT_DIR, "fund_predictions.json")
MODEL_DIR = os.path.join(SCRIPT_DIR, "model_weights")
# ===== 部署配置 =====
_DESKTOP = os.path.expanduser("~/Desktop/基金预测.html")
_WEBSITE = os.path.expanduser("~/mysite/基金预测.html")
_GITHUB = os.path.join(SCRIPT_DIR, "output", "index.html")
if os.path.isdir(os.path.join(SCRIPT_DIR, "output")):
    OUTPUT_FILE = _GITHUB
elif os.path.isdir(os.path.expanduser("~/mysite")):
    OUTPUT_FILE = _WEBSITE
else:
    OUTPUT_FILE = _DESKTOP

# ========== 数据抓取 ==========
def fetch_fund_nav_all(fund_code):
    all_records = []
    page = 1
    while page <= 200:
        url = f"https://api.fund.eastmoney.com/f10/lsjz?callback=jQuery&fundCode={fund_code}&pageIndex={page}&pageSize=20&startDate=&endDate="
        headers = {"Referer": "https://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=20) as resp:
                text = resp.read().decode("utf-8")
            if "(" not in text: break
            data = json.loads(text[text.index("(")+1:text.rindex(")")])
            if not isinstance(data, dict): break
            lsjz = data.get("Data")
            if not isinstance(lsjz, dict): break
            records = lsjz.get("LSJZList", [])
            if not records: break
            before = len(all_records)
            for r in records:
                try: all_records.append({"date": r["FSRQ"], "nav": float(r["DWJZ"])})
                except: continue
            if len(all_records) >= data.get("TotalCount", 0) or len(all_records) == before: break
            page += 1; time.sleep(0.15)
        except: break
    all_records.sort(key=lambda x: x["date"])
    return all_records

def fetch_sina_kline(symbol, days=999):
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
    headers = {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
        return [{"date": item["day"], "close": float(item["close"]), "volume": float(item.get("volume", 0))} for item in json.loads(text)]
    except: return []

def calc_returns(data, key="nav"):
    """从净值/价格序列计算收益率"""
    return [{"date": data[i]["date"], "return": (data[i][key]-data[i-1][key])/data[i-1][key], "value": data[i][key]}
            for i in range(1, len(data)) if data[i-1][key] > 0]

# ========== 北向资金（akshare封装，失败则静默跳过） ==========
HAS_AKSHARE = False
try:
    os.environ["TQDM_DISABLE"] = "1"  # 关掉akshare的进度条
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    pass

def fetch_northbound_flow(days=500):
    """获取北向资金历史日度净流入（沪+深合计），失败返回空字典"""
    if not HAS_AKSHARE:
        return {}
    try:
        sh = ak.stock_hsgt_hist_em(symbol="沪股通")
        sz = ak.stock_hsgt_hist_em(symbol="深股通")
        result = {}
        for df, label in [(sh, "sh"), (sz, "sz")]:
            for _, row in df.iterrows():
                d = row["日期"]
                val = row.get("当日成交净买额", None)
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    continue
                if d not in result:
                    result[d] = 0.0
                result[d] += val  # 沪+深=北向合计净流入（亿元）
        return result
    except Exception:
        return {}

# ========== 基金持仓匹配（十大重仓股→加权个股行情） ==========
HOLDINGS_CACHE_FILE = os.path.join(SCRIPT_DIR, "fund_holdings_cache.json")
STOCK_CACHE_DIR = os.path.join(SCRIPT_DIR, "stock_cache")
HOLDINGS_EXPIRE_DAYS = 7  # 持仓一周一更（季报实际更新频率更低）

def stock_code_to_sina(code):
    """股票代码转新浪格式"""
    code = str(code).strip()
    return f"sh{code}" if code.startswith("6") else f"sz{code}"

def fetch_fund_holdings(fund_code, year=""):
    """从天天基金获取基金季报十大重仓股列表"""
    if not HAS_AKSHARE:
        return []
    url = f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={fund_code}&topline=10000&year={year}&month=&rt=0.913877"
    headers = {"Referer": "https://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8")
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start < 0 or brace_end < 0:
            return []
        obj_str = text[brace_start:brace_end+1]
        cpos = obj_str.find('content:"')
        if cpos < 0:
            return []
        val_start = cpos + len('content:"')
        last_brace = obj_str.rfind("}")
        second_last = obj_str.rfind('"', 0, last_brace-1)
        if second_last < val_start:
            return []
        content = obj_str[val_start:second_last]

        # 解析HTML表格中的持仓数据
        import re
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", content, re.DOTALL)
        holdings = []
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(cells) >= 4:
                code_cell = re.sub(r"<[^>]+>", "", cells[1]).strip()
                name_cell = re.sub(r"<[^>]+>", "", cells[2]).strip()
                weight_pct = None
                for cell in cells[3:]:
                    cleaned = re.sub(r"<[^>]+>", "", cell).strip()
                    if "%" in cleaned:
                        try:
                            weight_pct = float(cleaned.replace("%", "").strip())
                        except:
                            pass
                        break
                if code_cell and name_cell and weight_pct is not None:
                    holdings.append({"code": code_cell, "name": name_cell, "weight": weight_pct})
        return holdings
    except Exception:
        return []

def load_cached_holdings():
    """加载缓存的持仓数据"""
    try:
        with open(HOLDINGS_CACHE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_cached_holdings(data):
    """保存持仓缓存"""
    os.makedirs(os.path.dirname(HOLDINGS_CACHE_FILE), exist_ok=True)
    with open(HOLDINGS_CACHE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_all_holdings(fund_codes):
    """批量获取并缓存所有基金的持仓
    策略：先读缓存，7天外的重新拉取
    """
    cache = load_cached_holdings()
    now_ts = time.time()
    updated = False
    for fc in fund_codes:
        # 检查缓存是否过期
        cached = cache.get(fc, {})
        cached_ts = cached.get("fetch_time", 0)
        if cached_ts > 0 and (now_ts - cached_ts) < HOLDINGS_EXPIRE_DAYS * 86400:
            continue  # 缓存未过期
        # 拉取最新持仓（尝试近3年）
        holdings = []
        for y in ["2026", "2025", "2024", "2023"]:
            holdings = fetch_fund_holdings(fc, y)
            if holdings:
                break
            time.sleep(0.2)
        if holdings:
            cache[fc] = {"holdings": holdings, "fetch_time": now_ts}
            updated = True
            print(f"    持仓 {fc}: {len(holdings)}只重仓股")
        else:
            print(f"    持仓 {fc}: 未拉到数据（用板块指数代替）")
        time.sleep(0.3)
    if updated:
        save_cached_holdings(cache)
    return cache

def get_stock_daily_returns(stock_code, days=999):
    """获取个股日收益率序列"""
    symbol = stock_code_to_sina(stock_code)
    data = fetch_sina_kline(symbol, days)
    if not data:
        return []
    return calc_returns(data, "close")
def calc_sma(prices, period):
    """简单移动平均"""
    sma = []
    for i in range(len(prices)):
        if i < period - 1:
            sma.append(None)
        else:
            sma.append(sum(prices[i-period+1:i+1]) / period)
    return sma

def calc_ema(values, period):
    """指数移动平均"""
    if not values: return []
    multiplier = 2 / (period + 1)
    ema = [values[0]]
    for v in values[1:]:
        ema.append((v - ema[-1]) * multiplier + ema[-1])
    return ema

def calc_rsi(returns, period=14):
    """相对强弱指标 RSI"""
    gains = [max(r, 0) for r in returns]
    losses = [max(-r, 0) for r in returns]
    rsi = [None] * period
    if len(returns) <= period: return rsi
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(returns)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        if avg_loss == 0:
            rsi.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100 - 100 / (1 + rs))
    return rsi

def calc_macd(returns, fast=12, slow=26, signal=9):
    """MACD 指标"""
    prices = [100.0]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(prices))]
    signal_line = calc_ema(macd_line, signal)
    return macd_line, signal_line

# ========== 日级别特征工程 ==========
def build_features_daily(daily_returns, sector_returns, market_returns_list,
                         sector_vol_by_date=None, market_vol_by_date=None,
                         northbound_by_date=None, holding_daily_returns=None,
                         lookback=5):
    """
    生成日级别特征：
      - 基础：滞后收益 + 波动率 + 加速度
      - 技术：RSI + MACD + 均线乖离
      - 量能：板块/大盘成交量比 + VPT量价趋势
      - 资金流向：北向资金净流入 + 板块VPT
      - 日历：星期效应 + 月初月末 + 季末
    """
    fund_rets = [r["return"] for r in daily_returns]
    n = len(fund_rets)
    if n < lookback + 1:
        return [], []

    # 技术指标
    rsi_values = calc_rsi(fund_rets, 14)
    macd_line, macd_signal = calc_macd(fund_rets, 12, 26, 9)
    prices = [100.0]
    for r in fund_rets:
        prices.append(prices[-1] * (1 + r))
    prices = prices[1:]  # 对齐长度
    ma5 = calc_sma(prices, 5)
    ma20 = calc_sma(prices, 20)

    features, targets = [], []
    for i in range(lookback, n):
        feat = {}

        # ——— 基础特征：滞后收益 ———
        for w in [1, 2, 3, 5]:
            feat[f"fund_ret_{w}d"] = sum(fund_rets[i-j] for j in range(w)) if i >= w else 0

        # ——— 波动率 ———
        for vol_days in [5, 10]:
            if i >= vol_days:
                rv = fund_rets[i-vol_days+1:i+1]
                mv = sum(rv) / vol_days
                feat[f"fund_vol_{vol_days}d"] = math.sqrt(sum((r-mv)**2 for r in rv) / vol_days)
            else:
                feat[f"fund_vol_{vol_days}d"] = 0

        # ——— 加速度 ———
        feat["fund_accel"] = fund_rets[i] - fund_rets[i-1] if i >= 1 else 0

        # ——— RSI / MACD / 均线乖离 ———
        feat["fund_rsi_14"] = rsi_values[i] if i < len(rsi_values) and rsi_values[i] is not None else 50
        if i < len(macd_line) and i < len(macd_signal):
            feat["fund_macd"] = macd_line[i]
            feat["fund_macd_signal"] = macd_signal[i]
        else:
            feat["fund_macd"] = feat["fund_macd_signal"] = 0
        feat["fund_ma5_div"] = (prices[i] / ma5[i] - 1) * 100 if i < len(ma5) and ma5[i] else 0
        feat["fund_ma20_div"] = (prices[i] / ma20[i] - 1) * 100 if i < len(ma20) and ma20[i] else 0

        # ——— 板块收益率 ———
        if sector_returns and i < len(sector_returns):
            s_rets = [r["return"] for r in sector_returns]
            for w in [1, 5]:
                feat[f"sector_ret_{w}d"] = sum(s_rets[i-j] for j in range(w)) if i >= w and i < len(s_rets) else 0
        else:
            for w in [1, 5]: feat[f"sector_ret_{w}d"] = 0

        # ——— 大盘收益率 ———
        m_avg = []
        for mw in market_returns_list:
            if mw and i < len(mw):
                m_avg.append(mw[i]["return"])
        feat["market_ret_1d"] = sum(m_avg) / len(m_avg) if m_avg else 0
        for w in [2, 5]:
            mv = []
            for mw in market_returns_list:
                if mw and i >= w and i < len(mw):
                    mv.append(sum(mw[i-j]["return"] for j in range(w)))
            feat[f"market_ret_{w}d"] = sum(mv) / len(mv) if mv else 0

        # ——— 价差 ———
        feat["sector_market_spread"] = feat.get("sector_ret_1d", 0) - feat["market_ret_1d"]
        feat["fund_sector_spread"] = fund_rets[i] - feat.get("sector_ret_1d", 0)

        # ——— ✨ 量能指标（板块/大盘成交量） ———
        current_date = daily_returns[i]["date"]

        if sector_vol_by_date:
            sv = sector_vol_by_date.get(current_date, 0)
            sv_list = []
            for k in range(max(0, i-20), i+1):
                d = daily_returns[k]["date"]
                sv_list.append(sector_vol_by_date.get(d, 0))
            avg_sv = sum(sv_list) / len(sv_list) if sv_list else 1
            feat["sector_vol_ratio"] = sv / avg_sv if avg_sv > 0 else 1.0

            # 近5天板块成交量放大天数占比
            sv_up = 0
            for k in range(max(0, i-4), i+1):
                d_cur = daily_returns[k]["date"]
                d_prev = daily_returns[max(0, k-1)]["date"]
                v_cur = sector_vol_by_date.get(d_cur, 0)
                v_prev = sector_vol_by_date.get(d_prev, 0)
                if v_cur > v_prev and v_prev > 0:
                    sv_up += 1
            feat["sector_vol_trend"] = sv_up / 5.0
        else:
            feat["sector_vol_ratio"] = 1.0
            feat["sector_vol_trend"] = 0.5

        if market_vol_by_date:
            mv = market_vol_by_date.get(current_date, 0)
            mv_list = []
            for k in range(max(0, i-20), i+1):
                d = daily_returns[k]["date"]
                mv_list.append(market_vol_by_date.get(d, 0))
            avg_mv = sum(mv_list) / len(mv_list) if mv_list else 1
            feat["market_vol_ratio"] = mv / avg_mv if avg_mv > 0 else 1.0
        else:
            feat["market_vol_ratio"] = 1.0

        # ——— ✨✨ 北向资金 + VPT量价趋势（资金流向代理） ———
        # 北向资金（真实数据，最近几天可能NaN/缺失）
        nb = 0.0
        if northbound_by_date and current_date in northbound_by_date:
            nb = northbound_by_date[current_date]
        feat["northbound_net"] = nb / 100.0
        # 北向5日累计
        nb_5d = 0.0
        if northbound_by_date:
            for k in range(max(0, i-4), i+1):
                d = daily_returns[k]["date"]
                nb_5d += northbound_by_date.get(d, 0)
        feat["northbound_5d"] = nb_5d / 100.0

        # VPT量价趋势（补充信号）
        feat["sector_vpt"] = feat.get("sector_vol_ratio", 1.0) * feat.get("sector_ret_1d", 0)
        feat["market_vpt"] = feat.get("market_vol_ratio", 1.0) * feat["market_ret_1d"]
        feat["vpt_divergence"] = abs(feat["sector_vpt"] - feat["market_vpt"])

        # ——— ✨ 日历效应 ———
        dt = datetime.strptime(current_date, "%Y-%m-%d")
        wd = dt.weekday()
        for d_idx, d_name in enumerate(["mon", "tue", "wed", "thu"]):
            feat[f"is_{d_name}"] = 1.0 if wd == d_idx else 0.0
        # Friday = 全0基线

        dom = dt.day
        feat["month_start"] = 1.0 if dom <= 3 else 0.0
        feat["month_end"] = 1.0 if dom >= 28 else 0.0
        feat["is_quarter_end"] = 1.0 if dt.month in [3, 6, 9, 12] and dom >= 25 else 0.0

        # ——— ✨✨ 多项式特征扩展（交互项 + 平方） ———
        feat["poly_rsi_sq"] = feat["fund_rsi_14"] ** 2
        feat["poly_vol_sq"] = feat["fund_vol_5d"] ** 2
        feat["poly_ret1_ret5"] = feat["fund_ret_1d"] * feat["fund_ret_5d"]
        feat["poly_rsi_vol"] = feat["fund_rsi_14"] * feat["fund_vol_5d"]
        feat["poly_ma5_ma20"] = feat["fund_ma5_div"] * feat["fund_ma20_div"]
        feat["poly_sector_fund"] = feat.get("sector_ret_1d", 0) * feat["fund_ret_1d"]
        feat["poly_market_sector"] = feat["market_ret_1d"] * feat.get("sector_ret_1d", 0)
        feat["poly_vol_ret"] = feat.get("sector_vol_ratio", 1) * feat.get("sector_ret_1d", 0)

        # ——— ✨✨✨ 持仓匹配（十大重仓股加权收益） ———
        if holding_daily_returns:
            if not hasattr(build_features_daily, "_hdr_cache") or build_features_daily._hdr_cache is not holding_daily_returns:
                build_features_daily._hdr_cache = holding_daily_returns
                build_features_daily._hdr_dict = {h["date"]: h for h in holding_daily_returns if h.get("date")}
            hr_dict = build_features_daily._hdr_dict
            hr = hr_dict.get(current_date, None)
            if hr is not None:
                feat["holding_ret_1d"] = hr.get("wtd_return", 0)
                hr_5d = 0.0
                for k in range(max(0, i-4), i+1):
                    d = daily_returns[k]["date"]
                    hr_item = hr_dict.get(d)
                    if hr_item is not None:
                        hr_5d += hr_item.get("wtd_return", 0)
                feat["holding_ret_5d"] = hr_5d
                feat["holding_vs_sector"] = feat["holding_ret_1d"] - feat.get("sector_ret_1d", 0)
                feat["holding_vs_fund"] = feat["holding_ret_1d"] - fund_rets[i]
            else:
                feat["holding_ret_1d"] = 0
                feat["holding_ret_5d"] = 0
                feat["holding_vs_sector"] = 0
                feat["holding_vs_fund"] = 0
        else:
            feat["holding_ret_1d"] = 0
            feat["holding_ret_5d"] = 0
            feat["holding_vs_sector"] = 0
            feat["holding_vs_fund"] = 0

        features.append(feat)
        if i + 1 < n:
            if i + 3 < n:
                target_val = sum(fund_rets[i+1:i+4]) / 3.0
            else:
                target_val = fund_rets[i+1]  # 最后2条fallback到1日
            targets.append({"return": target_val, "date": daily_returns[i+1]["date"]})
        else:
            targets.append(None)

    return features, targets

# ========== 模型（v3：标准化 + 学习率衰减 + 在线学习） ==========
class RidgeModel:
    def __init__(self, alpha=0.1, lr=0.01, n_epochs=500):
        self.alpha = alpha; self.lr = lr; self.n_epochs = n_epochs
        self.weights = None; self.bias = 0; self.feature_names = None; self.trained = False
        self.feat_mean = {}; self.feat_std = {}

    def _standardize(self, row):
        out = {}
        for f in self.feature_names:
            v = row.get(f, 0)
            m = self.feat_mean.get(f, 0)
            s = self.feat_std.get(f, 1)
            out[f] = (v - m) / s if s > 1e-10 else 0
        return out

    def fit(self, X, y):
        if not X or len(X) < 5: return
        self.feature_names = sorted(X[0].keys())
        n, d = len(X), len(self.feature_names)
        X_raw = [[x.get(f, 0) for f in self.feature_names] for x in X]
        for j, f in enumerate(self.feature_names):
            vals = [X_raw[i][j] for i in range(n)]
            self.feat_mean[f] = sum(vals) / n
            var = sum((v - self.feat_mean[f])**2 for v in vals) / n
            self.feat_std[f] = math.sqrt(var) if var > 1e-10 else 1
        Xm = X_raw
        for i in range(n):
            for j, f in enumerate(self.feature_names):
                Xm[i][j] = (Xm[i][j] - self.feat_mean[f]) / self.feat_std[f]
        self.weights = [0.0] * d; self.bias = sum(y) / n
        for ep in range(self.n_epochs):
            lr_adj = self.lr / (1 + ep / 100)
            gw = [0.0] * d; gb = 0.0
            for i in range(n):
                pred = self.bias + sum(self.weights[j] * Xm[i][j] for j in range(d))
                err = pred - y[i]
                for j in range(d): gw[j] += 2 * err * Xm[i][j] / n
                gb += 2 * err / n
            for j in range(d):
                gw[j] += 2 * self.alpha * self.weights[j] / n
                self.weights[j] -= lr_adj * gw[j]
            self.bias -= lr_adj * gb
        self.trained = True

    def partial_fit(self, X, y, n_epochs=5):
        """在线更新：用新数据做少量梯度下降步，保留原有标准化参数"""
        if not self.trained or not X or len(X) < 1:
            return self.fit(X, y)
        n = len(X)
        d = len(self.weights)
        # 用已有统计量标准化新数据
        Xm = [[x.get(f, 0) for f in self.feature_names] for x in X]
        for i in range(n):
            for j, f in enumerate(self.feature_names):
                s = self.feat_std.get(f, 1)
                Xm[i][j] = (Xm[i][j] - self.feat_mean.get(f, 0)) / s if s > 1e-10 else 0
        for ep in range(n_epochs):
            lr_adj = self.lr / (1 + ep)
            gw = [0.0] * d; gb = 0.0
            for i in range(n):
                pred = self.bias + sum(self.weights[j] * Xm[i][j] for j in range(d))
                err = pred - y[i]
                for j in range(d): gw[j] += 2 * err * Xm[i][j] / n
                gb += 2 * err / n
            for j in range(d):
                gw[j] += self.alpha * self.weights[j]
                self.weights[j] -= lr_adj * gw[j]
            self.bias -= lr_adj * gb

    def predict(self, x):
        if not self.trained: return 0
        xn = self._standardize(x)
        return self.bias + sum(self.weights[j] * xn.get(f, 0) for j, f in enumerate(self.feature_names))

# ========== 模型持久化 ==========
def save_model_state(model, path, last_date=""):
    """保存模型权重到JSON"""
    state = {
        "version": 5, "last_date": last_date,
        "alpha": model.alpha, "lr": model.lr, "n_epochs": model.n_epochs,
        "weights": model.weights, "bias": model.bias,
        "feature_names": model.feature_names,
        "feat_mean": model.feat_mean, "feat_std": model.feat_std,
        "trained": model.trained,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_model_state(model, path):
    """从JSON加载模型权重，返回最后训练日期"""
    try:
        with open(path, "r") as f:
            state = json.load(f)
        if state.get("version") != 5: return None
        model.alpha = state["alpha"]
        model.lr = state["lr"]
        model.n_epochs = state["n_epochs"]
        model.weights = state["weights"]
        model.bias = state["bias"]
        model.feature_names = state["feature_names"]
        model.feat_mean = state["feat_mean"]
        model.feat_std = state["feat_std"]
        model.trained = state["trained"]
        return state.get("last_date", "")
    except:
        return None

# ========== 日级别在线预测（三层集成） ==========
def predict_daily(all_fund_data, fund_code, sector, model_dir):
    """三层预测 + 个体模型在线校准"""
    params = BEST_PARAMS.get(fund_code, (36, 0.1))
    alpha = params[1]
    fund_feats = all_fund_data[fund_code]["features"]
    fund_tgts = all_fund_data[fund_code]["targets"]
    n = len(fund_feats)
    if n < 10: return 0, 0, 0, 0
    last_feat = fund_feats[-1]

    # ── 个体模型（在线学习） ──
    indiv_path = os.path.join(model_dir, f"{fund_code}_indiv.json")
    indiv = RidgeModel(alpha=alpha*0.5, lr=0.01, n_epochs=5)
    last_date = load_model_state(indiv, indiv_path)

    if not last_date or not indiv.trained:
        # 首次运行：全量训练
        ix, iy = [], []
        for j in range(n - 1):
            if fund_tgts[j] is not None:
                ix.append(fund_feats[j]); iy.append(fund_tgts[j]["return"])
        if len(ix) >= 20:
            indiv = RidgeModel(alpha=alpha*0.5, lr=0.01, n_epochs=500)
            indiv.fit(ix, iy)
            new_last_date = fund_tgts[n-2]["date"] if n >= 2 else ""
        else:
            indiv = RidgeModel(alpha=alpha*0.5, lr=0.01, n_epochs=200)
            if len(ix) >= 5:
                indiv.fit(ix, iy)
            new_last_date = ""
    else:
        # 在线更新：找上次训练后的新数据
        new_X, new_y = [], []
        for j in range(n - 1):
            if fund_tgts[j] is not None and fund_tgts[j]["date"] > last_date:
                new_X.append(fund_feats[j]); new_y.append(fund_tgts[j]["return"])
        if new_X:
            indiv.partial_fit(new_X, new_y, n_epochs=5)
            print(f"    {fund_code} 在线学习: +{len(new_X)}条 [{new_X[0]['date']} → {new_X[-1]['date']}]")
        new_last_date = fund_tgts[n-2]["date"] if n >= 2 else ""
        if not new_last_date:
            new_last_date = last_date

    # 保存更新后的模型
    save_model_state(indiv, indiv_path, new_last_date)

    # ── 只用个体模型（三层集成已验证不如单层） ──
    i_pred = indiv.predict(last_feat)
    final = i_pred

    return final, final, final, final

# ========== 自适应阈值 + 置信度评分 ==========
def calc_adaptive_threshold(daily_returns, base_threshold=0.15, window=20):
    """根据近期波动率动态调整阈值
    波动大→阈值放宽（避免噪声交易）
    波动小→阈值收窄（抓住小趋势）
    """
    if len(daily_returns) < window:
        return base_threshold
    recent_rets = [r["return"] * 100 for r in daily_returns[-window:]]  # 转%
    avg_vol = sum(abs(r) for r in recent_rets) / window
    # 基准波动率 1.0% = 阈值 0.15%，线性缩放
    dynamic = base_threshold * (avg_vol / 1.0)
    return max(0.08, min(0.40, dynamic))  # 限制在 0.08%~0.40%

def calc_confidence(pred_pct, threshold, recent_accuracy, today_vol_pct):
    """计算预测置信度 0-100
    因素：预测幅度 / 阈值 + 历史准确率 + 今日波动修正
    """
    # 1. 信号强度：预测值超出阈值的倍数
    if abs(pred_pct) < threshold:
        signal_score = abs(pred_pct) / threshold * 30  # <阈值时最多30分
    else:
        signal_score = min(40, 30 + (abs(pred_pct) - threshold) / threshold * 10)  # 超阈值累加

    # 2. 历史准确率（0-40分）
    if recent_accuracy > 0:
        acc_score = max(0, min(40, (recent_accuracy - 50) * 0.8))  # 50%=0分，100%=40分
    else:
        acc_score = 20  # 无历史数据取中值

    # 3. 今日波动修正：波动太大扣分（最多扣20）
    if today_vol_pct > 3.0:
        vol_penalty = min(20, today_vol_pct * 3)
    else:
        vol_penalty = 0

    raw = signal_score + acc_score - vol_penalty
    return max(0, min(100, raw))


# ========== 日级别交易策略 ==========
def generate_daily_advice(fund_data, daily_prediction, today, recent_accuracy=None,
                           daily_returns=None, threshold=None, confidence=None):
    """根据明日预测给出今日操作建议（支持自适应阈值+置信度）"""
    daily = fund_data["daily"]
    if len(daily) < 2:
        return {"action": "观望", "reason": "数据不足", "today_change": 0, "pred_pct": 0}

    today_change = (daily[-1]["nav"] - daily[-2]["nav"]) / daily[-2]["nav"] * 100
    pred_pct = daily_prediction * 100

    # 自适应阈值
    if threshold is None and daily_returns:
        threshold = calc_adaptive_threshold(daily_returns)
    if threshold is None:
        threshold = 0.15

    # 置信度（如果外部没传，自行简单估算）
    if confidence is None:
        acc = recent_accuracy if recent_accuracy else 60
        confidence = calc_confidence(pred_pct, threshold, acc, abs(today_change))

    # 判断：低于阈值+低置信→持有/观望；超阈值+高置信→操作
    if abs(pred_pct) < threshold * 0.5:
        # 预测方向极弱
        if abs(today_change) > 2.0:
            action, reason = "观望", f"今日波动大({today_change:+.1f}%)，预测方向不明，多看少动"
        else:
            action, reason = "持有", f"预测{pred_pct:+.2f}%，波动不大继续持有"
    elif pred_pct > threshold:
        if today_change < -0.5 and confidence > 55:
            action, reason = "加仓", f"自信度{confidence}%·预测明日+{pred_pct:.2f}%，今日回调{today_change:+.1f}%，可逢低布局"
        elif confidence > 50:
            action, reason = "买入", f"自信度{confidence}%·预测明日上涨{pred_pct:.2f}%，可适当加仓"
        else:
            action, reason = "持有", f"自信度{confidence}%·预测+{pred_pct:.2f}%但信心不足，继续持有"
    elif pred_pct < -threshold:
        if today_change > 0.5 and confidence > 55:
            action, reason = "减持", f"自信度{confidence}%·预测明日下跌{pred_pct:.2f}%，今日反弹{today_change:+.1f}%可减仓"
        elif confidence > 50:
            action, reason = "观望", f"自信度{confidence}%·预测明日下跌{pred_pct:.2f}%，暂不操作"
        else:
            action, reason = "观望", f"自信度{confidence}%·预测-{abs(pred_pct):.2f}%但信心不足，多看少动"
    else:
        if abs(today_change) > 2.0:
            action, reason = "观望", f"今日波动大({today_change:+.1f}%)，明日方向不明"
        else:
            action, reason = "持有", f"预测{pred_pct:+.2f}%，波动不大继续持有"

    return {
        "action": action,
        "reason": reason,
        "today_change": round(today_change, 3),
        "pred_pct": round(pred_pct, 3),
        "confidence": round(confidence),
        "threshold": round(threshold, 3),
    }

# ========== 预测记录管理 ==========
def load_predictions():
    try:
        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"version": "1.0", "predictions": []}

def save_daily_prediction(pred_data):
    data = load_predictions()
    data["predictions"].append(pred_data)
    data["predictions"] = data["predictions"][-365:]  # 保留近1年
    with open(PREDICTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ========== 在线准确率回测 ==========
def calc_accuracy_daily(fund_code, sector, all_fund_data):
    """在线学习回测：用前60天训练，然后在后续每天做 predict→update→compare"""
    params = BEST_PARAMS.get(fund_code, (36, 0.1))
    alpha = params[1]
    ff = all_fund_data[fund_code]["features"]
    ft = all_fund_data[fund_code]["targets"]
    n = len(ff)
    if n < 80: return 0  # 日频至少需要80个样本

    correct = total = 0
    sq_err_sum = 0.0
    test_start = max(60, n - 60)

    # 初始训练
    model = RidgeModel(alpha=alpha*0.5, lr=0.01, n_epochs=500)
    train_X = ff[:test_start]
    train_y = [t["return"] for t in ft[:test_start] if t is not None]
    if len(train_X) >= 30:
        model.fit(train_X, train_y)
    else:
        return 0

    # 在线回测
    for wi in range(test_start, n - 1):
        if ft[wi] is None: continue
        pred = model.predict(ff[wi])
        actual = ft[wi]["return"]
        if (pred > 0 and actual > 0) or (pred < 0 and actual < 0):
            correct += 1
        sq_err_sum += (pred - actual) ** 2
        total += 1
        # 在线更新：预测完立即学
        model.partial_fit([ff[wi]], [actual], n_epochs=5)

    rmse = math.sqrt(sq_err_sum / total) if total > 0 else 0
    if rmse > 0.001:
        print(f"    {fund_code} RMSE={rmse:.4f} 方向准确率={correct}/{total}")
    return round(correct/total*100, 1) if total > 0 else 0

def calc_accuracy_ensemble(fund_code, sector, all_fund_data):
    """在线回测三层集成准确率（个体在线学习 + 全局/板块XGBoost静态评估）"""
    params = BEST_PARAMS.get(fund_code, (36, 0.1))
    alpha = params[1]
    ff = all_fund_data[fund_code]["features"]
    ft = all_fund_data[fund_code]["targets"]
    n = len(ff)
    if n < 80: return 0, 0

    test_start = max(60, n - 60)

    # ── 全局模型（训练集上训练一次） ──
    MAX_HISTORY = 250
    gx, gy = [], []
    for fc, fd in all_fund_data.items():
        nf = len(fd["targets"])
        if nf < 5: continue
        start = max(0, nf - MAX_HISTORY - 1)
        for j in range(start, min(test_start, nf)):
            if fd["targets"][j] is not None:
                gx.append(fd["features"][j]); gy.append(fd["targets"][j]["return"])
    gm = RidgeModel(alpha=alpha, lr=0.01, n_epochs=200)
    if len(gx) >= 10: gm.fit(gx, gy)

    # ── 板块模型 ──
    sx, sy = [], []
    for sf in [f for f in FUNDS if f["sector"] == sector]:
        if sf["code"] in all_fund_data:
            fd = all_fund_data[sf["code"]]
            nf = len(fd["targets"])
            if nf < 5: continue
            start = max(0, nf - MAX_HISTORY - 1)
            for j in range(start, min(test_start, nf)):
                if fd["targets"][j] is not None:
                    sx.append(fd["features"][j]); sy.append(fd["targets"][j]["return"])
    sm = RidgeModel(alpha=alpha, lr=0.01, n_epochs=200)
    if len(sx) >= 5: sm.fit(sx, sy)

    # ── 个体模型在线学习回测 ──
    model = RidgeModel(alpha=alpha*0.5, lr=0.01, n_epochs=500)
    train_X = ff[:test_start]
    train_y = [t["return"] for t in ft[:test_start] if t is not None]
    if len(train_X) >= 30:
        model.fit(train_X, train_y)
    else:
        return 0, 0

    correct_indiv = 0; correct_ens = 0; total = 0

    for wi in range(test_start, n - 1):
        if ft[wi] is None: continue
        actual = ft[wi]["return"]

        i_pred = model.predict(ff[wi])
        g_pred = gm.predict(ff[wi]) if gm.trained else 0
        s_pred = sm.predict(ff[wi]) if sm.trained else 0

        ng, ns, ni = len(gx), len(sx), len(model.feature_names) if model.feature_names else 1
        total_dc = ng + ns + ni
        w_g = ng / total_dc if total_dc > 0 else 1/3
        w_s = ns / total_dc if total_dc > 0 else 1/3
        w_i = ni / total_dc if total_dc > 0 else 1/3

        # 个体近期准确率调权
        perf_window = min(60, wi - test_start)
        if perf_window >= 15 and model.trained:
            ic = 0; it = 0
            for t in range(max(test_start, wi - perf_window), wi):
                if ft[t] is None: continue
                pv = model.predict(ff[t])
                av = ft[t]["return"]
                if (pv > 0 and av > 0) or (pv < 0 and av < 0):
                    ic += 1
                it += 1
            i_acc = ic / max(it, 1)
            if i_acc < 0.35 and it >= 10:
                pen = 0.6
                shift = w_i * pen
                w_i *= (1 - pen)
                w_g += shift * 0.6; w_s += shift * 0.4
            elif i_acc < 0.45 and it >= 10:
                pen = 0.3
                shift = w_i * pen
                w_i *= (1 - pen)
                w_g += shift * 0.6; w_s += shift * 0.4

        ens_pred = w_g * g_pred + w_s * s_pred + w_i * i_pred
        total += 1

        if (ens_pred > 0 and actual > 0) or (ens_pred < 0 and actual < 0):
            correct_ens += 1
        if (i_pred > 0 and actual > 0) or (i_pred < 0 and actual < 0):
            correct_indiv += 1

        model.partial_fit([ff[wi]], [actual], n_epochs=5)

    acc_ens = round(correct_ens/total*100, 1) if total > 0 else 0
    acc_indiv = round(correct_indiv/total*100, 1) if total > 0 else 0
    print(f"    {fund_code} 🔗 集成={correct_ens}/{total}({acc_ens}%) | 个体={correct_indiv}/{total}({acc_indiv}%)")
    return acc_ens, acc_indiv

# ========== HTML生成 ==========
def generate_html(results, update_time):
    """生成仪表盘式HTML（日预测版）"""
    now = datetime.now()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today_name = weekday_names[now.weekday()]

    # 统计
    bull = sum(1 for r in results if r["final_pred"] > 0.003)
    bear = sum(1 for r in results if r["final_pred"] < -0.003)
    flat = len(results) - bull - bear

    add_count = sum(1 for r in results if r.get("advice", {}).get("action") in ["加仓", "买入"])
    reduce_count = sum(1 for r in results if r.get("advice", {}).get("action") in ["减持", "减仓"])
    hold_count = len(results) - add_count - reduce_count

    # 总表
    table_rows = ""
    for r in results:
        pred_pct = r["final_pred"] * 100
        pred_color = "#e74c3c" if pred_pct < 0 else "#27ae60"
        pred_arrow = "📈" if pred_pct > 0.15 else ("📉" if pred_pct < -0.15 else "➡️")
        advice = r.get("advice", {})
        action = advice.get("action", "—")
        reason = advice.get("reason", "")
        today_chg = advice.get("today_change", 0)
        accuracy = r.get("accuracy", 0)
        action_color = {"加仓": "#27ae60", "买入": "#27ae60", "减持": "#e74c3c", "减仓": "#e74c3c", "持有": "#3498db", "观望": "#95a5a6"}.get(action, "#333")
        action_icon = {"加仓": "🟢", "买入": "🟢", "减持": "🔴", "减仓": "🔴", "持有": "🔵", "观望": "⚪"}.get(action, "⚪")
        today_color = "#e74c3c" if today_chg < 0 else "#27ae60"
        acc_color = "#27ae60" if accuracy >= 55 else ("#e67e22" if accuracy >= 50 else "#e74c3c")
        pnl = r.get("pnl")
        pnl_pct = r.get("pnl_pct")
        if pnl is not None and pnl_pct is not None:
            pnl_cls = "pnl-pos" if pnl >= 0 else "pnl-neg"
            pnl_html = f'<span class="{pnl_cls}">{pnl:+.0f}元 ({pnl_pct:+.1f}%)</span>'
        else:
            pnl_html = '<span style="color:#999">—</span>'

        table_rows += f"""
        <tr>
            <td class="fund-name-cell">{r['name']}</td>
            <td class="sector-cell">{SECTORS.get(r['sector'], {}).get('name', '')}</td>
            <td style="color:{today_color};font-weight:600">{today_chg:+.2f}%</td>
            <td style="color:{pred_color};font-weight:600">{pred_arrow} {pred_pct:+.2f}%</td>
            <td style="color:{acc_color}">{accuracy}%</td>
            <td>{pnl_html}</td>
            <td style="color:{action_color};font-weight:700">{action_icon} {action}</td>
            <td class="reason-cell">{reason}</td>
        </tr>"""

    detail_cards = ""
    charts_data = {}
    colors = ["#3498db", "#e74c3c", "#27ae60", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22"]

    for idx, r in enumerate(results):
        c = colors[idx % len(colors)]
        hist = r["history"]
        all_dates = [h["date"] for h in hist]
        all_navs = [h["nav"] for h in hist]

        fund_txns = [t for t in TRANSACTIONS if t["code"] == r["code"]]
        txn_markers = []
        for t in fund_txns:
            nav_at_date = None
            for h in hist:
                if h["date"] >= t["date"]:
                    nav_at_date = h["nav"]; break
            if nav_at_date:
                txn_markers.append({"date": t["date"], "type": t["type"], "amount": t.get("amount", 0), "nav": nav_at_date, "note": t.get("note", "")})

        # 预测未来1个交易日
        pred_dates, pred_navs = [], []
        if all_navs:
            last_nav = all_navs[-1]
            last_date = datetime.strptime(all_dates[-1], "%Y-%m-%d")
            next_date = last_date + timedelta(days=1)
            while next_date.weekday() >= 5:
                next_date += timedelta(days=1)
            pred_nav = last_nav * (1 + r["final_pred"])
            pred_dates.append(next_date.strftime("%Y-%m-%d"))
            pred_navs.append(round(pred_nav, 4))

        buy_navs = [t["nav"] for t in txn_markers if t["type"] in ("买入", "转入")]
        avg_buy_nav = sum(buy_navs) / len(buy_navs) if buy_navs else None

        charts_data[idx] = {
            "dates": all_dates, "navs": all_navs, "color": c,
            "txns": txn_markers, "pred_dates": pred_dates, "pred_navs": pred_navs,
            "pred_pct": r["final_pred"] * 100, "buy_price": avg_buy_nav
        }

        pred_pct = r["final_pred"] * 100
        action = r.get("advice", {}).get("action", "—")
        reason = r.get("advice", {}).get("reason", "")
        action_color = {"加仓": "#27ae60", "买入": "#27ae60", "减持": "#e74c3c", "减仓": "#e74c3c", "持有": "#3498db", "观望": "#95a5a6"}.get(action, "#333")

        nav_now = all_navs[-1] if all_navs else 0
        periods = {"1月": 22, "3月": 66, "6月": 132, "1年": 252}
        perf_html = ""
        for label, days in periods.items():
            if len(all_navs) > days:
                pct = (nav_now - all_navs[-days]) / all_navs[-days] * 100
                pc = "#e74c3c" if pct < 0 else "#27ae60"
                perf_html += f'<span class="perf-tag" style="color:{pc}">{label} {pct:+.2f}%</span>'

        # 我的盈亏
        pnl = r.get("pnl")
        pnl_pct = r.get("pnl_pct")
        cost = r.get("cost")
        if pnl is not None:
            pnl_c = "#e74c3c" if pnl >= 0 else "#27ae60"
            pnl_html_card = f'<div style="font-size:13px;padding:4px 10px;color:{pnl_c}">💰 我的盈亏: {pnl:+.0f}元 ({pnl_pct:+.1f}%) · 投入{cost:.0f}元</div>'
        else:
            pnl_html_card = ""

        detail_cards += f"""
        <div class="detail-card">
            <div class="dc-header">
                <span class="dc-name" style="border-left:4px solid {c}">{r['name']}</span>
                <span class="dc-nav">最新 {all_navs[-1]:.4f}</span>
                <span class="dc-pred tip" data-tip="未来3个交易日的平均收益率" style="color:{'#e74c3c' if pred_pct<0 else '#27ae60'}">3日趋势 {pred_pct:+.2f}%</span>
                <span class="dc-action" style="color:{action_color};background:{action_color}15;padding:2px 10px;border-radius:12px;font-weight:600">{action}</span>
            </div>
            <div class="dc-perf">{perf_html}</div>
            {pnl_html_card}
            <div class="dc-periods">
                <button class="period-btn" data-idx="{idx}" data-days="5" onclick="switchPeriod(this)">近1周</button>
                <button class="period-btn" data-idx="{idx}" data-days="22" onclick="switchPeriod(this)">近1月</button>
                <button class="period-btn" data-idx="{idx}" data-days="66" onclick="switchPeriod(this)">近3月</button>
                <button class="period-btn active" data-idx="{idx}" data-days="132" onclick="switchPeriod(this)">近6月</button>
                <button class="period-btn" data-idx="{idx}" data-days="252" onclick="switchPeriod(this)">近1年</button>
                <button class="period-btn" data-idx="{idx}" data-days="9999" onclick="switchPeriod(this)">全部</button>
            </div>
            <div class="dc-chart"><canvas id="ch{idx}"></canvas></div>
            <div class="dc-reason">{reason}</div>
            <div class="dc-layers">
                <span>个体模型 {r['individual_pred']*100:+.2f}%</span>
                <span style="color:#888">个体在线校准 ✓ | 北向资金 ✓ | 3日平滑 ✓</span>
            </div>
        </div>"""

    if add_count > reduce_count + 2:
        overall = "📈 偏进攻"
        overall_desc = f"多数基金看涨（{bull}只），可以适当加仓"
        overall_color = "#27ae60"
    elif reduce_count > add_count + 2:
        overall = "📉 偏防守"
        overall_desc = f"多数基金看跌（{bear}只），建议减仓或观望"
        overall_color = "#e74c3c"
    else:
        overall = "⚖️ 均衡"
        overall_desc = f"涨跌分化（涨{bull}/跌{bear}/平{flat}），精选个股"
        overall_color = "#f39c12"

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<script src="chart.min.js"></script>
<title>基金预测 v6 — {today_name}</title>

<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#333;padding:12px;max-width:1200px;margin:0 auto}}
.header{{text-align:center;padding:20px 0 12px}}.header h1{{font-size:22px;color:#1a1a2e;margin-bottom:4px}}.header .sub{{color:#888;font-size:12px}}
.dashboard{{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-bottom:16px}}
.dash-card{{background:white;border-radius:10px;padding:14px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.06)}}
.dash-card .label{{font-size:11px;color:#999;margin-bottom:4px}}.dash-card .value{{font-size:22px;font-weight:700}}
.table-wrap{{background:white;border-radius:10px;padding:14px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);overflow-x:auto}}
.table-wrap h2{{font-size:16px;margin-bottom:12px;color:#1a1a2e}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#f8f9fa;padding:8px 10px;text-align:left;font-weight:600;color:#555;border-bottom:2px solid #e9ecef;white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid #f0f0f0;white-space:nowrap}}
tr:hover{{background:#f8f9fb}}
.fund-name-cell{{font-weight:600;color:#1a1a2e;min-width:100px}}
.sector-cell{{color:#888;font-size:12px}}
.reason-cell{{font-size:12px;color:#666;max-width:250px;white-space:normal}}
.overall{{background:white;border-radius:10px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);border-left:4px solid {overall_color}}}
.overall h3{{color:{overall_color};font-size:18px;margin-bottom:4px}}.overall p{{color:#666;font-size:13px}}
.detail-card{{background:white;border-radius:12px;padding:16px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.04)}}
.dc-header{{display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}}
.dc-name{{font-weight:700;padding-left:10px;font-size:15px;color:#1a1a2e}}
.dc-nav{{font-size:13px;color:#666;margin-left:auto}}
.dc-pred{{font-weight:700;font-size:15px}}
.dc-perf{{display:flex;gap:12px;padding:4px 10px 8px;flex-wrap:wrap}}
.perf-tag{{font-size:12px;font-weight:600}}
.dc-periods{{display:flex;gap:0;margin-bottom:10px;padding:0 4px}}
.period-btn{{border:1px solid #e8e8e8;background:#f8f8f8;color:#666;font-size:12px;padding:5px 14px;cursor:pointer;transition:all 0.2s;white-space:nowrap}}
.period-btn:first-child{{border-radius:6px 0 0 6px}}
.period-btn:last-child{{border-radius:0 6px 6px 0}}
.period-btn:not(:last-child){{border-right:none}}
.period-btn:hover{{background:#eef4ff;color:#3498db}}
.period-btn.active{{background:#3498db;color:white;border-color:#3498db}}
.dc-chart{{height:200px;margin-bottom:8px;position:relative}}
.dc-reason{{font-size:12px;color:#888;margin-bottom:6px;padding-left:10px;font-style:italic}}
.dc-layers{{display:flex;gap:16px;font-size:11px;color:#aaa;padding-left:10px}}
.disclaimer{{text-align:center;padding:16px;color:#aaa;font-size:11px}}
</style></head><body>

<div class="header">
    <h1>📊 基金预测仪表盘</h1>
    <div class="sub">{update_time} · {today_name} · 日频v6 · 3日平滑目标+多项式特征+量能+日历+性能加权</div>
</div>

<div class="dashboard">
    <div class="dash-card"><div class="label">📈 看涨</div><div class="value" style="color:#27ae60">{bull}</div></div>
    <div class="dash-card"><div class="label">📉 看跌</div><div class="value" style="color:#e74c3c">{bear}</div></div>
    <div class="dash-card"><div class="label">➡️ 震荡</div><div class="value" style="color:#f39c12">{flat}</div></div>
    <div class="dash-card"><div class="label">🟢 买入/加仓</div><div class="value" style="color:#27ae60">{add_count}</div></div>
    <div class="dash-card"><div class="label">🔴 减持</div><div class="value" style="color:#e74c3c">{reduce_count}</div></div>
    <div class="dash-card"><div class="label">⏰ 时间</div><div class="value" style="color:#3498db;font-size:16px">{today_name}</div></div>
</div>

<div class="overall">
    <h3>{overall}</h3>
    <p>{overall_desc}</p>
</div>

<div class="table-wrap">
    <h2>📋 交易指导总表（明日预测）</h2>
    <table>
        <thead>
            <tr>
                <th>基金</th><th>板块</th><th>今日涨跌</th><th>明日预测</th><th>历史准确率</th><th>操作建议</th><th>原因</th>
            </tr>
        </thead>
        <tbody>{table_rows}</tbody>
    </table>
</div>

<h2 style="font-size:16px;margin:16px 0 10px;padding-left:4px">📈 详细走势</h2>
{detail_cards}

<div class="disclaimer">⚠️ 预测仅供参考，不构成投资建议 · 3日平滑目标 + 多项式特征 · 性能加权集成 + 滑动窗口 · 个体在线校准</div>
<script>
const FUND_DATA = {json.dumps(charts_data)};
const charts = {{}};

function getDaysRange(dates, days) {{
    if (days >= 9999) return {{startIdx: 0}};
    const now = new Date(dates[dates.length-1]);
    const cutoff = new Date(now);
    cutoff.setDate(cutoff.getDate() - days);
    const cutoffStr = cutoff.toISOString().split('T')[0];
    let startIdx = 0;
    for (let i = 0; i < dates.length; i++) {{
        if (dates[i] >= cutoffStr) {{ startIdx = i; break; }}
    }}
    return {{startIdx: startIdx}};
}}

function createChart(idx, days) {{
    const fd = FUND_DATA[idx];
    const range = getDaysRange(fd.dates, days || 132);
    const slicedDates = fd.dates.slice(range.startIdx);
    const slicedNavs = fd.navs.slice(range.startIdx);

    const predDates = fd.pred_dates || [];
    const predNavs = fd.pred_navs || [];

    const allLabels = slicedDates.concat(predDates);
    const histData = slicedNavs.concat(Array(predDates.length).fill(null));
    const predLine = Array(slicedDates.length - 1).fill(null).concat([slicedNavs[slicedNavs.length-1]]).concat(predNavs);

    const firstNav = slicedNavs[0];
    const lastNav = slicedNavs[slicedNavs.length-1];
    const changePct = ((lastNav - firstNav) / firstNav * 100);
    const lineColor = changePct >= 0 ? '#e74c3c' : '#27ae60';

    const txns = (fd.txns || []).filter(t => t.date >= slicedDates[0]);
    const buyPoints = [];
    const sellPoints = [];
    txns.forEach(t => {{
        const i = slicedDates.indexOf(t.date);
        if (i >= 0) {{
            if (t.type === '买入' || t.type === '转入') {{
                buyPoints.push({{x: t.date, y: t.nav, amount: t.amount, note: t.note}});
            }} else {{
                sellPoints.push({{x: t.date, y: t.nav, amount: t.amount, note: t.note}});
            }}
        }}
    }});

    const ctx = document.getElementById('ch' + idx).getContext('2d');
    if (charts[idx]) charts[idx].destroy();

    const datasets = [
        {{
            label: '历史净值',
            data: histData,
            borderColor: lineColor,
            borderWidth: 2,
            pointRadius: function(ctx) {{
                const d = allLabels[ctx.dataIndex];
                if (buyPoints.find(b => b.x === d) || sellPoints.find(s => s.x === d)) return 6;
                return 0;
            }},
            pointBackgroundColor: function(ctx) {{
                const d = allLabels[ctx.dataIndex];
                if (buyPoints.find(b => b.x === d)) return '#e74c3c';
                if (sellPoints.find(s => s.x === d)) return '#27ae60';
                return lineColor;
            }},
            pointBorderColor: function(ctx) {{
                const d = allLabels[ctx.dataIndex];
                if (buyPoints.find(b => b.x === d) || sellPoints.find(s => s.x === d)) return 'white';
                return 'transparent';
            }},
            pointBorderWidth: function(ctx) {{
                const d = allLabels[ctx.dataIndex];
                if (buyPoints.find(b => b.x === d) || sellPoints.find(s => s.x === d)) return 2;
                return 0;
            }},
            pointHoverRadius: 5,
            fill: true,
            backgroundColor: changePct >= 0 ? 'rgba(231,76,60,0.05)' : 'rgba(39,174,96,0.05)',
            tension: 0.3,
            spanGaps: false
        }},
        {{
            label: '明日预测',
            data: predLine,
            borderColor: '#f39c12',
            borderWidth: 2,
            borderDash: [6, 3],
            pointRadius: function(ctx) {{
                return ctx.dataIndex >= slicedDates.length ? 4 : 0;
            }},
            pointBackgroundColor: '#f39c12',
            pointBorderColor: 'white',
            pointBorderWidth: 2,
            pointHoverRadius: 5,
            fill: false,
            tension: 0.3,
            spanGaps: false
        }},
        {{
            label: '买入价',
            data: allLabels.map(() => fd.buy_price),
            borderColor: '#ff6b35',
            borderWidth: 1.5,
            borderDash: [8, 4],
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: false,
            tension: 0,
            spanGaps: false
        }}
    ];

    if (!fd.buy_price) datasets.pop();

    charts[idx] = new Chart(ctx, {{
        type: 'line',
        data: {{ labels: allLabels, datasets: datasets }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {{ mode: 'index', intersect: false }},
            plugins: {{
                legend: {{
                    display: true,
                    position: 'top',
                    align: 'end',
                    labels: {{
                        boxWidth: 12, padding: 8, font: {{ size: 10 }},
                        usePointStyle: true,
                        filter: function(item) {{ return item.text === '明日预测' || item.text === '买入价'; }}
                    }}
                }},
                tooltip: {{
                    backgroundColor: 'rgba(0,0,0,0.85)',
                    titleFont: {{ size: 11 }},
                    bodyFont: {{ size: 12 }},
                    padding: 10,
                    callbacks: {{
                        title: function(items) {{
                            const d = items[0].label;
                            const buy = buyPoints.find(b => b.x === d);
                            const sell = sellPoints.find(s => s.x === d);
                            let title = d;
                            if (buy) title += '  🔴 买入' + (buy.amount ? ' ¥' + buy.amount : '');
                            if (sell) title += '  🟢 ' + (sell.note || '卖出');
                            return title;
                        }},
                        label: function(ctx) {{
                            if (ctx.raw === null) return null;
                            let label = ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(4);
                            if (ctx.dataset.label === '买入价' && fd.buy_price) {{
                                const cur = slicedNavs[slicedNavs.length-1];
                                const diff = ((cur - fd.buy_price) / fd.buy_price * 100).toFixed(2);
                                label += ' (当前' + (diff >= 0 ? '+' : '') + diff + '%)';
                            }}
                            return label;
                        }}
                    }}
                }}
            }},
            scales: {{
                x: {{
                    grid: {{ display: false }},
                    ticks: {{ maxTicksLimit: 6, font: {{ size: 10 }}, maxRotation: 0 }}
                }},
                y: {{
                    grid: {{ color: 'rgba(0,0,0,0.04)' }},
                    ticks: {{ font: {{ size: 10 }}, callback: v => v.toFixed(4) }}
                }}
            }}
        }}
    }});
}}

function switchPeriod(btn) {{
    const idx = parseInt(btn.dataset.idx);
    const days = parseInt(btn.dataset.days);
    btn.parentElement.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    createChart(idx, days);
}}

document.addEventListener('DOMContentLoaded', function() {{
    for (let idx in FUND_DATA) {{
        createChart(idx, 132);
    }}
}});
</script></body></html>"""
    return html


# ========== 收盘学习 ==========
def close_learn():
    """收盘后：拉实际净值→对比预测→在线学习→输出总结"""
    now = datetime.now()
    update_time = now.strftime("%Y-%m-%d %H:%M")
    print(f"[{update_time}] 基金预测 v6 — 收盘学习")

    os.makedirs(MODEL_DIR, exist_ok=True)

    # 1. 拉今日实际净值
    print("  拉取今日净值...")
    today_navs = {}
    for fund in FUNDS:
        fc = fund["code"]
        records = fetch_fund_nav_all(fc)
        if records and len(records) >= 2:
            today_navs[fc] = records
            print(f"    {fund['name']}: {records[-1]['nav']:.4f} ({records[-1]['date']})")
        time.sleep(0.2)

    # 2. 读早上预测
    morning = load_morning_prediction()
    if not morning:
        print("  ⚠️ 今天没有早上预测记录，跳过对比")
        # 即使没有预测，也可以做学习
    morning_funds = morning.get("funds", {}) if morning else {}

    # 3. 拉板块和大盘数据（用于特征计算）
    print("  拉取大盘/板块数据...")
    market_daily = []
    for name, code in MARKET_INDICES.items():
        data = fetch_sina_kline(code, 999)
        if data:
            market_daily.append(calc_returns(data, "close"))
        time.sleep(0.2)

    sector_daily = {}
    sector_kline = {}
    for sc, si in SECTORS.items():
        data = fetch_sina_kline(si["index"], 999)
        if data:
            sector_daily[sc] = calc_returns(data, "close")
            sector_kline[sc] = data
        time.sleep(0.2)

    # 4. 对比+学习
    print("\n" + "=" * 60)
    print("  📊 收盘对比 & 在线学习")
    print("=" * 60)

    results = []
    for fund in FUNDS:
        fc = fund["code"]
        if fc not in today_navs:
            continue

        records = today_navs[fc]
        daily_returns = calc_returns(records, "nav")
        if len(daily_returns) < 30:
            continue

        # 计算今日实际收益率
        today_return = daily_returns[-1]["return"]
        today_pct = today_return * 100
        today_date = daily_returns[-1]["date"]

        # 加载模型
        params = BEST_PARAMS.get(fc, (36, 0.1))
        alpha = params[1]
        indiv_path = os.path.join(MODEL_DIR, f"{fc}_indiv.json")
        model = RidgeModel(alpha=alpha*0.5, lr=0.01, n_epochs=5)
        last_date = load_model_state(model, indiv_path)

        # 构建今天的特征（用最新一条）
        sector_vol_by_date = {}
        if fund["sector"] in sector_kline:
            for d in sector_kline[fund["sector"]]:
                sector_vol_by_date[d["date"]] = d["volume"]

        market_vol_by_date = {}
        market_kline_list = []
        for name, code in MARKET_INDICES.items():
            data = fetch_sina_kline(code, 999)
            if data:
                market_kline_list.append(data)
        for mk in market_kline_list:
            for d in mk:
                market_vol_by_date[d["date"]] = market_vol_by_date.get(d["date"], 0) + d["volume"]
        for k in market_vol_by_date:
            market_vol_by_date[k] /= len(market_kline_list) if market_kline_list else 1

        northbound_by_date = fetch_northbound_flow()

        features, targets = build_features_daily(
            daily_returns,
            sector_daily.get(fund["sector"], []),
            market_daily,
            sector_vol_by_date=sector_vol_by_date,
            market_vol_by_date=market_vol_by_date,
            northbound_by_date=northbound_by_date,
        )

        if not features or len(features) < 2:
            continue

        # 用倒数第二条特征预测（对应今天）
        last_feat = features[-2] if len(features) >= 2 else features[-1]
        pred = model.predict(last_feat) if model.trained else 0
        pred_pct = pred * 100

        # 方向是否正确
        direction_ok = (pred > 0 and today_return > 0) or (pred < 0 and today_return < 0)
        deviation = today_pct - pred_pct

        # 在线学习：用今天的实际结果更新模型
        if model.trained and len(features) >= 2:
            model.partial_fit([features[-2]], [today_return], n_epochs=5)
            save_model_state(model, indiv_path, today_date)
            learned = True
        else:
            learned = False

        # 我的盈亏
        pnl, pnl_pct_val, cost = calc_my_return(fc, records)

        icon = "✓" if direction_ok else "✗"
        learn_icon = "🧠" if learned else "⚪"
        results.append({
            "name": fund["name"], "code": fc,
            "pred_pct": pred_pct, "actual_pct": today_pct,
            "deviation": deviation, "direction_ok": direction_ok,
            "learned": learned, "pnl": pnl, "pnl_pct": pnl_pct_val, "cost": cost,
        })

    # 输出表格
    print(f"\n  {'基金':<14} {'预测':>7} {'实际':>7} {'偏差':>7} {'方向':>4} {'学习':>4} {'我的盈亏':>12}")
    print(f"  {'─'*14} {'─'*7} {'─'*7} {'─'*7} {'─'*4} {'─'*4} {'─'*12}")

    for r in results:
        pnl_str = f"{r['pnl']:+.0f}元" if r['pnl'] is not None else "—"
        print(f"  {r['name']:<12} {r['pred_pct']:>+6.2f}% {r['actual_pct']:>+6.2f}% "
              f"{r['deviation']:>+6.2f}% {'✓' if r['direction_ok'] else '✗':>4} "
              f"{'🧠' if r['learned'] else '—':>4} {pnl_str:>12}")

    # 统计
    correct = sum(1 for r in results if r["direction_ok"])
    total = len(results)
    learned_count = sum(1 for r in results if r["learned"])
    print(f"\n  方向准确: {correct}/{total} ({correct/total*100:.0f}%)" if total else "  无数据")
    print(f"  模型已更新: {learned_count}只")

    # 保存收盘记录
    close_record = {
        "date": now.strftime("%Y-%m-%d"),
        "type": "close",
        "funds": {r["code"]: {
            "predicted": round(r["pred_pct"], 3),
            "actual": round(r["actual_pct"], 3),
            "deviation": round(r["deviation"], 3),
            "direction_ok": r["direction_ok"],
            "name": r["name"],
        } for r in results},
        "accuracy": round(correct/total*100, 1) if total else 0,
    }
    save_daily_prediction(close_record)
    print("  📝 已保存收盘记录（模型已在线学习更新）")

    # 总结
    total_pnl = sum(r["pnl"] for r in results if r["pnl"] is not None)
    total_cost = sum(r["cost"] for r in results if r["cost"] is not None)
    if total_cost > 0:
        total_pct = total_pnl / total_cost * 100
        print(f"\n  💰 总盈亏: {total_pnl:+.0f}元 ({total_pct:+.1f}%) · 总投入{total_cost:.0f}元")


# ========== 中午修正预测 ==========
SCRAPER_DB = os.path.join(SCRIPT_DIR, "market_data.db")

def fetch_fund_est(fund_code):
    """基金实时估值（盘中），用于中午模式回退"""
    url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js"
    headers = {"Referer": "https://fundf10.eastmoney.com/", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            text = resp.read().decode("utf-8")
        import re
        match = re.search(r'jsonpgz\((.+)\)', text)
        if not match:
            return None
        data = json.loads(match.group(1))
        return {
            "name": data.get("name", ""),
            "nav": float(data.get("gsz", 0)),
            "change_pct": float(data.get("gszzl", 0)),
            "time": data.get("gztime", ""),
        }
    except Exception:
        return None

def read_today_scraper_data():
    """读取爬虫今天存的盘中数据"""
    if not os.path.exists(SCRAPER_DB):
        return {}
    conn = sqlite3.connect(SCRAPER_DB)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    result = {"funds": {}, "indices": {}, "sectors": {}}
    c.execute("SELECT code, name, price, change_pct, ts FROM snapshots WHERE type='fund_est' AND ts LIKE ? ORDER BY ts DESC", (f"{today}%",))
    rows = c.fetchall()
    seen = set()
    for code, name, price, change_pct, ts in rows:
        if code not in seen:
            result["funds"][code] = {"name": name, "est_nav": price, "est_change": change_pct, "ts": ts}
            seen.add(code)
    c.execute("SELECT code, name, price, change_pct, ts FROM snapshots WHERE type='index' AND ts LIKE ? ORDER BY ts DESC", (f"{today}%",))
    rows = c.fetchall()
    seen = set()
    for code, name, price, change_pct, ts in rows:
        if code not in seen:
            result["indices"][name] = {"price": price, "change_pct": change_pct, "ts": ts}
            seen.add(code)
    c.execute("SELECT code, name, price, change_pct, ts FROM snapshots WHERE type='sector' AND ts LIKE ? ORDER BY ts DESC", (f"{today}%",))
    rows = c.fetchall()
    seen = set()
    for code, name, price, change_pct, ts in rows:
        if code not in seen:
            result["sectors"][name] = {"price": price, "change_pct": change_pct, "ts": ts}
            seen.add(code)
    conn.close()
    return result

def load_morning_prediction():
    """读取今天早上的预测"""
    data = load_predictions()
    today = datetime.now().strftime("%Y-%m-%d")
    for pred in reversed(data.get("predictions", [])):
        if pred.get("date") == today and pred.get("type") == "daily":
            return pred
    return None

def noon_predict():
    """中午修正预测：对比早盘实际走势，修正预测"""
    now = datetime.now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M')}] 基金预测 v6 — 中午修正")

    morning = load_morning_prediction()
    if not morning:
        print("  ⚠️ 今天没有早上预测记录")
        return

    scraper_data = read_today_scraper_data()
    funds_est = scraper_data.get("funds", {})
    indices = scraper_data.get("indices", {})
    sectors = scraper_data.get("sectors", {})

    if not funds_est and not indices:
        print("  ⚠️ 爬虫数据为空（market_data.db里今天没有数据）")
        print("  回退到API直接获取...")
        for fund in FUNDS:
            est = fetch_fund_est(fund["code"])
            if est:
                funds_est[fund["code"]] = {"name": fund["name"], "est_nav": est["nav"], "est_change": est["change_pct"], "ts": est.get("time", "")}

    # === 早盘总结 ===
    print("\n" + "=" * 55)
    print("  📊 早盘总结")
    print("=" * 55)

    print("\n  大盘指数:")
    for name, data in indices.items():
        pct = data["change_pct"]
        icon = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
        print(f"    {icon} {name}: {data['price']:.2f} ({pct:+.2f}%)")

    print("\n  板块:")
    for name, data in sectors.items():
        pct = data["change_pct"]
        icon = "🟢" if pct > 0 else ("🔴" if pct < 0 else "⚪")
        print(f"    {icon} {name}: {pct:+.2f}%")

    # === 对比修正 ===
    print("\n" + "=" * 55)
    print("  🔍 早盘对比 & 修正")
    print("=" * 55)

    morning_funds = morning.get("funds", {})
    results = []

    print(f"\n  {'基金':<14} {'早预测':>8} {'今实际':>8} {'偏差':>8} {'状态':<6}")
    print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8} {'─'*6}")

    for code, mpred in morning_funds.items():
        name = mpred.get("name", code)
        morning_pct = mpred.get("predicted", 0)
        actual = 0.0
        if code in funds_est:
            actual = funds_est[code].get("est_change", 0.0)

        deviation = actual - morning_pct
        direction_correct = (morning_pct > 0 and actual > 0) or (morning_pct < 0 and actual < 0) or (abs(morning_pct) < 0.05 and abs(actual) < 0.1)
        status = "✓" if direction_correct else "✗"

        if abs(deviation) > 1.0:
            adjust = "⚠️大幅偏离"
        elif abs(deviation) > 0.3:
            adjust = "🔄需修正"
        else:
            adjust = "✅一致"

        print(f"  {name:<12} {morning_pct:>+7.2f}% {actual:>+7.2f}% {deviation:>+7.2f}% {status} {adjust}")

        revised = morning_pct * 0.4 + actual * 0.6 if abs(deviation) > 0.3 else morning_pct
        results.append({
            "code": code, "name": name,
            "morning_pred": morning_pct, "actual_today": round(actual, 3),
            "deviation": round(deviation, 3), "revised": round(revised, 3),
            "direction_ok": direction_correct,
        })

    # === 明日修正预测 ===
    print(f"\n  {'基金':<14} {'早预测':>8} {'修正后':>8} {'变化':>8}")
    print(f"  {'─'*14} {'─'*8} {'─'*8} {'─'*8}")

    noon_record = {"date": now.strftime("%Y-%m-%d"), "type": "noon", "funds": {}}

    for r in results:
        change = r["revised"] - r["morning_pred"]
        icon = "📈" if change > 0.05 else ("📉" if change < -0.05 else "➡️")
        print(f"  {r['name']:<12} {r['morning_pred']:>+7.2f}% {r['revised']:>+7.2f}% {change:>+7.2f}% {icon}")
        noon_record["funds"][r["code"]] = {"predicted": r["revised"], "name": r["name"]}

    # 保存修正记录
    save_daily_prediction(noon_record)
    print("\n  📝 已保存中午修正记录")

    # 综合建议
    print("\n" + "=" * 55)
    print("  💡 下午操作建议")
    print("=" * 55)

    for r in results:
        act = "持有观望"
        if r["revised"] > 0.15 and not r["direction_ok"]:
            act = "考虑减仓（走势偏离预测）"
        elif r["revised"] > 0.15 and r["direction_ok"]:
            act = "继续持有"
        elif r["revised"] < -0.15 and r["actual_today"] < -1.0:
            act = "考虑止损（跌幅加大）"
        elif r["revised"] < -0.15:
            act = "暂不加仓，等明日"
        print(f"    {r['name']}: {act}")


# ========== 主程序 ==========
def main():
    now = datetime.now()
    update_time = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.weekday()

    print(f"[{update_time}] 基金预测 v6 — 日级别在线学习（3日平滑目标）")
    os.makedirs(MODEL_DIR, exist_ok=True)

    # 1. 大盘数据（日线）+ 成交量
    print("  大盘...", end=" ")
    market_daily = []
    market_kline_list = []
    for name, code in MARKET_INDICES.items():
        data = fetch_sina_kline(code, 999)
        if data:
            market_daily.append(calc_returns(data, "close"))
            market_kline_list.append(data)
        time.sleep(0.2)
    print("OK")

    # 大盘成交量字典 (date→平均成交量)
    market_vol_by_date = {}
    for mk in market_kline_list:
        for d in mk:
            market_vol_by_date[d["date"]] = market_vol_by_date.get(d["date"], 0) + d["volume"]
    for k in market_vol_by_date:
        market_vol_by_date[k] /= len(market_kline_list) if market_kline_list else 1

    # 2. 板块数据（日线）+ 成交量
    print("  板块...", end=" ")
    sector_daily = {}
    sector_kline = {}
    for sc, si in SECTORS.items():
        data = fetch_sina_kline(si["index"], 999)
        if data:
            sector_daily[sc] = calc_returns(data, "close")
            sector_kline[sc] = data
        time.sleep(0.2)
    print("OK")

    # 2.5 北向资金历史数据
    print("  北向资金...", end=" ")
    northbound_by_date = fetch_northbound_flow()
    nb_days = len(northbound_by_date)
    if nb_days > 0:
        nb_sorted = sorted(northbound_by_date.keys())
        print(f"{nb_days}天 [{nb_sorted[0]} → {nb_sorted[-1]}]")
    else:
        print("跳过")

    # 2.6 基金持仓匹配：十大重仓股→加权个股行情（静默模式，失败不阻塞）
    print("  持仓匹配...")
    holdings_cache = get_all_holdings([f["code"] for f in FUNDS])

    # 收集所有唯一股票代码
    all_stock_codes = set()
    for fc, hc in holdings_cache.items():
        for h in hc.get("holdings", []):
            all_stock_codes.add(h["code"])
    all_stock_codes.discard("")  # 去掉可能的空值

    # 批量拉取个股日线数据（带10秒整体超时保护）
    stock_returns = {}  # stock_code → [{"date": ..., "return": ...}, ...]
    if all_stock_codes:
        stock_kline_dir = os.path.join(SCRIPT_DIR, "stock_cache")
        os.makedirs(stock_kline_dir, exist_ok=True)
        fetched = 0
        for sc in sorted(all_stock_codes):
            # 个股日行情缓存文件（按股票代码单独存，避免大文件）
            cache_path = os.path.join(stock_kline_dir, f"{sc}.json")
            stock_data = None
            try:
                with open(cache_path, "r") as f:
                    cached = json.load(f)
                # 当天缓存有效
                if cached.get("date") == datetime.now().strftime("%Y-%m-%d"):
                    stock_data = cached["data"]
            except:
                pass
            if stock_data is None:
                stock_data = get_stock_daily_returns(sc, 999)
                if stock_data:
                    try:
                        with open(cache_path, "w") as f:
                            json.dump({"date": datetime.now().strftime("%Y-%m-%d"), "data": stock_data}, f)
                    except:
                        pass
                    time.sleep(0.15)
                    fetched += 1
            if stock_data:
                stock_returns[sc] = stock_data
        print(f"    个股行情: {len(stock_returns)}/{len(all_stock_codes)}只股票 ({fetched}新拉取)")
    else:
        print("    无持仓数据，跳过")

    # 计算每只基金的加权持仓收益率
    fund_holding_returns = {}  # fund_code → [{"date":..., "wtd_return":...}, ...]
    for fc in [f["code"] for f in FUNDS]:
        hc = holdings_cache.get(fc, {})
        holdings_list = hc.get("holdings", [])
        if not holdings_list or not stock_returns:
            fund_holding_returns[fc] = None
            continue

        # 计算每日加权收益率
        total_weight = sum(h["weight"] for h in holdings_list)
        if total_weight <= 0:
            fund_holding_returns[fc] = None
            continue

        # 找出所有股票中最短的日收益率序列长度
        seq_lens = []
        stock_rets = {}
        for h in holdings_list:
            rets = stock_returns.get(h["code"])
            if rets and len(rets) > 10:
                stock_rets[h["code"]] = rets
                seq_lens.append(len(rets))
        if not stock_rets:
            fund_holding_returns[fc] = None
            continue
        min_len = min(seq_lens)
        # 统一截取到最短序列
        wtd_returns = []
        for idx in range(min_len):
            wtd = 0.0
            wsum = 0.0
            di = None
            for h in holdings_list:
                rets = stock_rets.get(h["code"])
                if rets and idx < len(rets):
                    w = h["weight"]
                    wtd += w * rets[idx]["return"]
                    wsum += w
                    if di is None:
                        di = rets[idx]["date"]
            if wsum > 0 and di:
                wtd_returns.append({"date": di, "wtd_return": wtd / wsum})
        fund_holding_returns[fc] = wtd_returns
        print(f"    持仓加权收益率 {fc}: {len(wtd_returns)}天")

    # 3. 基金数据 + 日级别特征（含量能/VPT量价趋势/日历/持仓匹配）
    all_fund_data = {}
    for fund in FUNDS:
        fc = fund["code"]
        print(f"  {fund['name']}...", end=" ")
        daily_nav = fetch_fund_nav_all(fc)
        if not daily_nav:
            print("FAILED"); continue

        daily_returns = calc_returns(daily_nav, "nav")

        # 板块成交量字典 (date→成交量)
        sector_vol_by_date = {}
        if fund["sector"] in sector_kline:
            for d in sector_kline[fund["sector"]]:
                sector_vol_by_date[d["date"]] = d["volume"]

        # 特征工程（日级别）
        features, targets = build_features_daily(
            daily_returns,
            sector_daily.get(fund["sector"], []),
            market_daily,
            sector_vol_by_date=sector_vol_by_date,
            market_vol_by_date=market_vol_by_date,
            northbound_by_date=northbound_by_date,
            holding_daily_returns=fund_holding_returns.get(fc),
        )
        all_fund_data[fc] = {
            "features": features, "targets": targets,
            "daily": daily_nav, "returns": daily_returns
        }
        # 计算我的实际盈亏
        pnl, pnl_pct, cost = calc_my_return(fc, daily_nav)
        all_fund_data[fc]["pnl"] = pnl
        all_fund_data[fc]["pnl_pct"] = pnl_pct
        all_fund_data[fc]["cost"] = cost
        if pnl is not None:
            print(f"OK ({len(daily_nav)}d → {len(features)}特征, 盈亏{pnl:+.0f}元 {pnl_pct:+.1f}%)")
        else:
            print(f"OK ({len(daily_nav)}d → {len(features)}特征)")

    # 4. 预测 + 在线校准
    print("\n  预测 & 在线校准...")
    results = []
    for fund in FUNDS:
        fc = fund["code"]
        if fc not in all_fund_data:
            continue
        fd = all_fund_data[fc]

        final, g, s, i = predict_daily(all_fund_data, fc, fund["sector"], MODEL_DIR)
        accuracy = calc_accuracy_daily(fc, fund["sector"], all_fund_data)
        acc_ens, acc_indiv = calc_accuracy_ensemble(fc, fund["sector"], all_fund_data)
        advice = generate_daily_advice(fd, final, now,
                                        recent_accuracy=accuracy,
                                        daily_returns=fd.get("returns"))

        results.append({
            "code": fc, "name": fund["name"], "sector": fund["sector"],
            "history": fd["daily"],
            "final_pred": final, "global_pred": g, "sector_pred": s, "individual_pred": i,
            "accuracy": acc_ens, "accuracy_indiv": acc_indiv, "advice": advice,
            "pnl": fd.get("pnl"), "pnl_pct": fd.get("pnl_pct"), "cost": fd.get("cost"),
        })

        pct = final * 100
        a = advice["action"]
        icon = {"加仓": "🟢", "买入": "🟢", "减持": "🔴", "减仓": "🔴", "持有": "🔵", "观望": "⚪"}.get(a, "⚪")
        conf = advice.get("confidence", "?")
        thr = advice.get("threshold", 0.15)
        print(f"  {icon} {fund['name']}: {pct:+.2f}% | 自信度{conf}% | 阈值{thr:.2f}% | → {a}")

    # 5. 保存预测记录
    daily_record = {
        "date": now.strftime("%Y-%m-%d"),
        "type": "daily",
        "funds": {r["code"]: {"predicted": round(r["final_pred"]*100, 3), "name": r["name"]} for r in results}
    }
    save_daily_prediction(daily_record)
    print("  📝 已保存今日预测记录")

    # 6. 生成HTML
    html = generate_html(results, update_time)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    import shutil
    chart_src = os.path.join(SCRIPT_DIR, "chart.min.js")
    chart_dst = os.path.join(os.path.dirname(OUTPUT_FILE), "chart.min.js")
    if "mysite" in OUTPUT_FILE:
        chart_dst = os.path.join(os.path.expanduser("~/mysite"), "chart.min.js")
    if os.path.exists(chart_src) and chart_src != chart_dst:
        shutil.copy2(chart_src, chart_dst)
        print(f"  📦 chart.min.js -> {chart_dst}")

    print(f"\n✅ {OUTPUT_FILE}")


if __name__ == "__main__":
    if "--noon" in sys.argv:
        noon_predict()
    elif "--close" in sys.argv:
        close_learn()
    else:
        main()
