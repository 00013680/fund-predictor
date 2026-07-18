#!/usr/bin/env python3
"""
基金预测 v4 — 日级别交易指导
功能：
1. 三层量化模型预测
2. 日级别交易策略（加仓/减持/持有/观望）
3. 预测跟踪记录
4. 精美HTML仪表盘
"""

import json, math, os, sys, time
from datetime import datetime, timedelta
from urllib.request import urlopen, Request

# ========== 交易记录（用户持仓） ==========
TRANSACTIONS = [
    {"code": "022831", "name": "华商电子C", "type": "买入", "amount": 3000, "date": "2026-07-15", "note": "首次买入"},
    {"code": "022831", "name": "华商电子C", "type": "清仓转出", "amount": 0, "date": "2026-07-17", "shares": 726.62, "note": "亏损约160元，转至嘉实农业C，周一确认份额"},
    {"code": "021753", "name": "南方电力C", "type": "买入", "amount": 2000, "date": "2026-07-16"},
    {"code": "014064", "name": "银华农业C", "type": "买入", "amount": 5000, "date": "2026-07-16"},
    {"code": "012087", "name": "博时健康C", "type": "买入", "amount": 3000, "date": "2026-07-16"},
    {"code": "017938", "name": "易方达医疗C", "type": "买入", "amount": 2000, "date": "2026-07-15"},
    {"code": "027034", "name": "中银AI C", "type": "买入", "amount": 3000, "date": "2026-07-15"},
    {"code": "015468", "name": "嘉实农业C", "type": "转入", "amount": 0, "date": "2026-07-17", "shares": 726.62, "note": "从华商电子C转入，周一确认份额"},
]

# ========== 配置 ==========
FUNDS = [
    {"code": "022831", "name": "华商电子C", "sector": "tech", "short": "电子"},
    {"code": "021753", "name": "南方电力C", "sector": "power", "short": "电力"},
    {"code": "014064", "name": "银华农业C", "sector": "agri", "short": "农业"},
    {"code": "012087", "name": "博时健康C", "sector": "med", "short": "健康"},
    {"code": "017938", "name": "易方达医疗C", "sector": "med", "short": "医疗"},
    {"code": "027034", "name": "中银AI C", "sector": "tech", "short": "AI"},
    {"code": "015468", "name": "嘉实农业C", "sector": "agri", "short": "农业2"},
]

SECTORS = {
    "tech":  {"name": "科技", "index": "sh000032"},
    "power": {"name": "电力", "index": "sz399808"},
    "agri":  {"name": "农业", "index": "sh000949"},
    "med":   {"name": "医药", "index": "sz399989"},
}
MARKET_INDICES = {"上证指数": "sh000001", "创业板指": "sz399006"}

BEST_PARAMS = {
    "022831": (20, 0.01), "021753": (20, 0.01), "014064": (26, 0.01),
    "012087": (26, 0.01), "017938": (30, 0.01), "027034": (36, 0.10),
    "015468": (30, 1.00),
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PREDICTIONS_FILE = os.path.join(SCRIPT_DIR, "fund_predictions.json")
# ===== 部署配置 =====
# 本地运行时输出到桌面，PythonAnywhere运行时输出到网站目录，GitHub Actions输出到output/
_DESKTOP = os.path.expanduser("~/Desktop/基金预测.html")
_WEBSITE = os.path.expanduser("~/mysite/基金预测.html")
_GITHUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "基金预测.html")
# 优先级：output/ > mysite/ > Desktop
if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")):
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
        return [{"date": item["day"], "close": float(item["close"])} for item in json.loads(text)]
    except: return []

def daily_to_weekly(data, key="nav"):
    if not data: return []
    weekly, cur_week, prev = [], None, None
    for item in data:
        d = datetime.strptime(item["date"], "%Y-%m-%d")
        w = d.isocalendar()[:2]
        if w != cur_week:
            if cur_week is not None and prev: weekly.append(prev)
            cur_week = w
        prev = item
    if prev: weekly.append(prev)
    return weekly

def calc_returns(weekly, key="nav"):
    return [{"date": weekly[i]["date"], "return": (weekly[i][key]-weekly[i-1][key])/weekly[i-1][key], "value": weekly[i][key]}
            for i in range(1, len(weekly)) if weekly[i-1][key] > 0]

# ========== 特征工程 ==========
def build_features(fund_w, sector_w, market_w_list, lookback=4):
    features, targets = [], []
    fund_rets = [w["return"] for w in fund_w]
    for i in range(lookback, len(fund_w)):
        feat = {}
        for w in [1, 2, 3, 4]:
            feat[f"fund_ret_{w}w"] = sum(fund_rets[i-j] for j in range(w)) if i >= w else 0
        if i >= 4:
            r4 = fund_rets[i-3:i+1]; m4 = sum(r4)/4
            feat["fund_vol_4w"] = math.sqrt(sum((r-m4)**2 for r in r4)/4)
        else: feat["fund_vol_4w"] = 0
        feat["fund_accel"] = fund_rets[i] - fund_rets[i-1] if i >= 1 else 0
        if sector_w and i < len(sector_w):
            s_rets = [w["return"] for w in sector_w]
            for w in [1, 2, 4]:
                feat[f"sector_ret_{w}w"] = sum(s_rets[i-j] for j in range(w)) if i >= w and i < len(s_rets) else 0
        else:
            for w in [1, 2, 4]: feat[f"sector_ret_{w}w"] = 0
        m_avg = []
        for mw in market_w_list:
            if mw and i < len(mw): m_avg.append(mw[i]["return"])
        feat["market_ret_1w"] = sum(m_avg)/len(m_avg) if m_avg else 0
        for w in [2, 4]:
            mv = []
            for mw in market_w_list:
                if mw and i >= w and i < len(mw): mv.append(sum(mw[i-j]["return"] for j in range(w)))
            feat[f"market_ret_{w}w"] = sum(mv)/len(mv) if mv else 0
        feat["sector_market_spread"] = feat["sector_ret_1w"] - feat["market_ret_1w"]
        feat["fund_sector_spread"] = fund_rets[i] - feat["sector_ret_1w"]
        features.append(feat)
        if i + 1 < len(fund_w): targets.append({"return": fund_w[i+1]["return"], "date": fund_w[i+1]["date"]})
        else: targets.append(None)
    return features, targets

# ========== 模型 ==========
class RidgeModel:
    def __init__(self, alpha=0.1, lr=0.01, n_epochs=200):
        self.alpha = alpha; self.lr = lr; self.n_epochs = n_epochs
        self.weights = None; self.bias = 0; self.feature_names = None; self.trained = False
    def fit(self, X, y):
        if not X or len(X) < 5: return
        self.feature_names = sorted(X[0].keys())
        n, d = len(X), len(self.feature_names)
        Xm = [[x.get(f, 0) for f in self.feature_names] for x in X]
        self.weights = [0.0] * d; self.bias = sum(y) / n
        for _ in range(self.n_epochs):
            gw = [0.0] * d; gb = 0.0
            for i in range(n):
                pred = self.bias + sum(self.weights[j] * Xm[i][j] for j in range(d))
                err = pred - y[i]
                for j in range(d): gw[j] += 2 * err * Xm[i][j] / n
                gb += 2 * err / n
            for j in range(d):
                gw[j] += 2 * self.alpha * self.weights[j] / n
                self.weights[j] -= self.lr * gw[j]
            self.bias -= self.lr * gb
        self.trained = True
    def predict(self, x):
        if not self.trained: return 0
        return self.bias + sum(self.weights[j] * x.get(f, 0) for j, f in enumerate(self.feature_names))

# ========== 三层预测 ==========
def predict_fund(all_fund_data, fund_code, sector):
    params = BEST_PARAMS.get(fund_code, (36, 0.1))
    train_window, alpha = params
    fund_feats = all_fund_data[fund_code]["features"]
    fund_tgts = all_fund_data[fund_code]["targets"]
    n = len(fund_feats)
    if n < 10: return 0, 0, 0, 0
    last_feat = fund_feats[-1]
    train_start = max(0, n - train_window)

    gx, gy = [], []
    for fc, fd in all_fund_data.items():
        for j in range(train_start, n):
            if j < len(fd["features"]) and fd["targets"][j] is not None:
                gx.append(fd["features"][j]); gy.append(fd["targets"][j]["return"])
    gm = RidgeModel(alpha=alpha, lr=0.01, n_epochs=200)
    if len(gx) >= 10: gm.fit(gx, gy)
    g_pred = gm.predict(last_feat)

    sx, sy = [], []
    for sf in [f for f in FUNDS if f["sector"] == sector]:
        if sf["code"] in all_fund_data:
            fd = all_fund_data[sf["code"]]
            for j in range(train_start, n):
                if j < len(fd["features"]) and fd["targets"][j] is not None:
                    sx.append(fd["features"][j]); sy.append(fd["targets"][j]["return"])
    sm = RidgeModel(alpha=alpha, lr=0.01, n_epochs=200)
    if len(sx) >= 5: sm.fit(sx, sy)
    s_pred = sm.predict(last_feat)

    ind_w = min(train_window, 26); ind_start = max(0, n - ind_w)
    ix, iy = [], []
    for j in range(ind_start, n):
        if j < len(fund_feats) and fund_tgts[j] is not None:
            ix.append(fund_feats[j]); iy.append(fund_tgts[j]["return"])
    im = RidgeModel(alpha=alpha*0.5, lr=0.01, n_epochs=200)
    if len(ix) >= 5: im.fit(ix, iy)
    i_pred = im.predict(last_feat)

    ni = len(ix)
    if ni >= 15: final = 0.25*g_pred + 0.25*s_pred + 0.5*i_pred
    elif ni >= 5: final = 0.35*g_pred + 0.35*s_pred + 0.3*i_pred
    else: final = 0.5*g_pred + 0.5*s_pred

    return final, g_pred, s_pred, i_pred

# ========== 日级别交易策略 ==========
def generate_daily_advice(fund_data, weekly_prediction, today):
    """
    根据周预测和本周已实现收益，给出今天的操作建议
    
    逻辑：
    - 周初：看周预测方向，给大方向建议
    - 周中：对比已实现 vs 预期，判断进度
    - 周末：评估本周表现，预判下周
    """
    daily = fund_data["daily"]
    if len(daily) < 2:
        return {"action": "观望", "reason": "数据不足", "progress": 0}

    # 本周已实现收益
    last_nav = daily[-1]["nav"]
    last_date = daily[-1]["date"]

    # 找到本周一的净值
    today_dt = datetime.strptime(last_date, "%Y-%m-%d")
    weekday = today_dt.weekday()  # 0=Mon, 4=Fri

    # 找本周起始净值
    week_start_nav = None
    for i in range(len(daily)-1, -1, -1):
        d = datetime.strptime(daily[i]["date"], "%Y-%m-%d")
        if d.weekday() == 0:  # Monday
            week_start_nav = daily[i]["nav"]
            break
        elif d < today_dt - timedelta(days=weekday):
            week_start_nav = daily[i]["nav"]
            break

    if week_start_nav is None and len(daily) >= 2:
        week_start_nav = daily[-2]["nav"]

    if week_start_nav and week_start_nav > 0:
        week_actual = (last_nav - week_start_nav) / week_start_nav * 100
    else:
        week_actual = 0

    # 今日涨跌
    if len(daily) >= 2:
        today_change = (daily[-1]["nav"] - daily[-2]["nav"]) / daily[-2]["nav"] * 100
    else:
        today_change = 0

    pred_pct = weekly_prediction * 100

    # 预测进度（本周已实现占预测的比例）
    if abs(pred_pct) > 0.01:
        progress = week_actual / pred_pct * 100
    else:
        progress = 0

    # 生成建议
    if weekday == 0:  # 周一
        if pred_pct > 1:
            action, reason = "加仓", f"周预测+{pred_pct:.1f}%，周初建仓好时机"
        elif pred_pct < -1:
            action, reason = "减持", f"周预测{pred_pct:.1f}%，考虑减仓避险"
        else:
            action, reason = "持有", f"周预测{pred_pct:+.1f}%，波动不大先观望"
    elif weekday >= 4:  # 周五
        if progress > 80:
            action, reason = "持有", f"本周预测已兑现{progress:.0f}%，保持仓位"
        elif progress < -50:
            action, reason = "观望", f"预测方向与实际相反，等待下周"
        else:
            action, reason = "持有", "周五不做大调整，等下周"
    else:  # 周二~周四
        if pred_pct > 1:
            if progress < 30:
                if today_change < -0.5:
                    action, reason = "加仓", f"回调中，预测+{pred_pct:.1f}%但才涨了{week_actual:.1f}%，逢低加仓"
                else:
                    action, reason = "持有", f"预测+{pred_pct:.1f}%，进度{progress:.0f}%，继续持有"
            elif progress > 120:
                action, reason = "减仓", f"预测已超额兑现（{progress:.0f}%），考虑部分获利"
            else:
                action, reason = "持有", f"进度正常（{progress:.0f}%），继续持有"
        elif pred_pct < -1:
            if progress > 50:
                action, reason = "观望", f"已跌{abs(week_actual):.1f}%，接近预测跌幅，暂不操作"
            elif today_change < -1:
                action, reason = "观望", f"今日跌{today_change:.1f}%，恐慌中不追卖"
            else:
                action, reason = "减持", f"预测{pred_pct:.1f}%，考虑减仓"
        else:
            if abs(today_change) > 1.5:
                action, reason = "观望", f"今日波动大({today_change:+.1f}%)，但周预测震荡，不追涨杀跌"
            else:
                action, reason = "持有", "预测震荡，保持现有仓位"

    return {
        "action": action,
        "reason": reason,
        "week_actual": round(week_actual, 3),
        "today_change": round(today_change, 3),
        "progress": round(progress, 1),
        "weekday": weekday,
    }

# ========== 预测记录管理 ==========
def load_predictions():
    try:
        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"version": "1.0", "predictions": []}

def save_prediction(pred_data):
    data = load_predictions()
    data["predictions"].append(pred_data)
    # 只保留最近52周
    data["predictions"] = data["predictions"][-52:]
    with open(PREDICTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_current_week_prediction():
    """获取本周的预测记录"""
    data = load_predictions()
    today = datetime.now()
    this_week = today.isocalendar()[:2]
    for p in reversed(data["predictions"]):
        p_date = datetime.strptime(p["date"], "%Y-%m-%d")
        if p_date.isocalendar()[:2] == this_week:
            return p
    return None

# ========== 历史准确率 ==========
def calc_accuracy(fund_code, sector, all_fund_data):
    params = BEST_PARAMS.get(fund_code, (36, 0.1))
    tw, alpha = params
    ff = all_fund_data[fund_code]["features"]
    ft = all_fund_data[fund_code]["targets"]
    n = len(ff)
    if n < 35: return 0
    correct = total = 0
    for wi in range(max(30, n-20), n-1):
        if ft[wi] is None: continue
        ts = max(0, wi - tw)
        gx, gy = [], []
        for fc, fd in all_fund_data.items():
            for j in range(ts, wi):
                if j < len(fd["features"]) and fd["targets"][j] is not None:
                    gx.append(fd["features"][j]); gy.append(fd["targets"][j]["return"])
        m = RidgeModel(alpha=alpha, lr=0.01, n_epochs=200)
        if len(gx) >= 10: m.fit(gx, gy)
        pred = m.predict(ff[wi])
        actual = ft[wi]["return"]
        if (pred > 0 and actual > 0) or (pred < 0 and actual < 0): correct += 1
        total += 1
    return round(correct/total*100, 1) if total > 0 else 0

# ========== HTML生成 ==========
def generate_html(results, update_time, is_daily=False):
    """生成仪表盘式HTML"""

    now = datetime.now()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    today_name = weekday_names[now.weekday()]

    # 统计
    bull = sum(1 for r in results if r["final_pred"] > 0.01)
    bear = sum(1 for r in results if r["final_pred"] < -0.01)
    flat = len(results) - bull - bear

    # 建议统计
    add_count = sum(1 for r in results if r.get("advice", {}).get("action") in ["加仓"])
    reduce_count = sum(1 for r in results if r.get("advice", {}).get("action") in ["减持", "减仓"])
    hold_count = len(results) - add_count - reduce_count

    # 总表
    table_rows = ""
    for r in results:
        pred_pct = r["final_pred"] * 100
        pred_color = "#e74c3c" if pred_pct < 0 else "#27ae60"
        pred_arrow = "📈" if pred_pct > 1 else ("📉" if pred_pct < -1 else "➡️")

        advice = r.get("advice", {})
        action = advice.get("action", "—")
        reason = advice.get("reason", "")
        today_chg = advice.get("today_change", 0)
        week_act = advice.get("week_actual", 0)
        progress = advice.get("progress", 0)
        accuracy = r.get("accuracy", 0)

        action_color = {"加仓": "#27ae60", "减持": "#e74c3c", "减仓": "#e74c3c", "持有": "#3498db", "观望": "#95a5a6"}.get(action, "#333")
        action_icon = {"加仓": "🟢", "减持": "🔴", "减仓": "🔴", "持有": "🔵", "观望": "⚪"}.get(action, "⚪")

        today_color = "#e74c3c" if today_chg < 0 else "#27ae60"
        week_color = "#e74c3c" if week_act < 0 else "#27ae60"
        acc_color = "#27ae60" if accuracy >= 55 else ("#e67e22" if accuracy >= 50 else "#e74c3c")

        # 进度条
        prog_width = min(max(abs(progress), 0), 150)
        prog_color = "#27ae60" if 0 < progress < 120 else ("#e74c3c" if progress > 120 or progress < -50 else "#f39c12")

        table_rows += f"""
        <tr>
            <td class="fund-name-cell">{r['name']}</td>
            <td class="sector-cell">{SECTORS.get(r['sector'], {}).get('name', '')}</td>
            <td style="color:{today_color};font-weight:600">{today_chg:+.2f}%</td>
            <td style="color:{week_color};font-weight:600">{week_act:+.2f}%</td>
            <td style="color:{pred_color};font-weight:600">{pred_arrow} {pred_pct:+.2f}%</td>
            <td>
                <div class="progress-bar">
                    <div class="progress-fill" style="width:{prog_width}%;background:{prog_color}"></div>
                </div>
                <span class="progress-text">{progress:.0f}%</span>
            </td>
            <td style="color:{acc_color}">{accuracy}%</td>
            <td style="color:{action_color};font-weight:700">{action_icon} {action}</td>
            <td class="reason-cell">{reason}</td>
        </tr>"""

    # 详细图表 — 支付宝风格时间区间切换
    detail_cards = ""
    charts_data = {}
    colors = ["#3498db", "#e74c3c", "#27ae60", "#f39c12", "#9b59b6", "#1abc9c", "#e67e22"]

    for idx, r in enumerate(results):
        c = colors[idx % len(colors)]
        hist = r["history"]  # 全部历史数据
        all_dates = [h["date"] for h in hist]
        all_navs = [h["nav"] for h in hist]

        # 找到该基金的交易记录
        fund_txns = [t for t in TRANSACTIONS if t["code"] == r["code"]]
        txn_markers = []
        for t in fund_txns:
            # 找到最近的交易日期净值
            nav_at_date = None
            for h in hist:
                if h["date"] >= t["date"]:
                    nav_at_date = h["nav"]
                    break
            if nav_at_date:
                txn_markers.append({
                    "date": t["date"],
                    "type": t["type"],
                    "amount": t.get("amount", 0),
                    "nav": nav_at_date,
                    "note": t.get("note", "")
                })

        # 生成预测净值（未来5个交易日）
        pred_dates = []
        pred_navs = []
        if all_navs:
            last_nav = all_navs[-1]
            last_date = datetime.strptime(all_dates[-1], "%Y-%m-%d")
            daily_pred = r["final_pred"] / 5  # 周预测拆成5天
            nav = last_nav
            for d in range(1, 6):
                next_date = last_date + timedelta(days=d)
                # 跳过周末
                while next_date.weekday() >= 5:
                    next_date += timedelta(days=1)
                nav = nav * (1 + daily_pred)
                pred_dates.append(next_date.strftime("%Y-%m-%d"))
                pred_navs.append(round(nav, 4))

        # 计算加权平均买入价
        buy_navs = [t["nav"] for t in txn_markers if t["type"] in ("买入", "转入")]
        avg_buy_nav = sum(buy_navs) / len(buy_navs) if buy_navs else None

        charts_data[idx] = {
            "dates": all_dates, "navs": all_navs, "color": c,
            "txns": txn_markers,
            "pred_dates": pred_dates, "pred_navs": pred_navs,
            "pred_pct": r["final_pred"] * 100,
            "buy_price": avg_buy_nav
        }

        advice = r.get("advice", {})
        pred_pct = r["final_pred"] * 100
        action = advice.get("action", "—")
        reason = advice.get("reason", "")
        action_color = {"加仓": "#27ae60", "减持": "#e74c3c", "减仓": "#e74c3c", "持有": "#3498db", "观望": "#95a5a6"}.get(action, "#333")

        # 计算各时间段涨跌幅
        nav_now = all_navs[-1] if all_navs else 0
        periods = {"1月": 22, "3月": 66, "6月": 132, "1年": 252}
        perf_html = ""
        for label, days in periods.items():
            if len(all_navs) > days:
                pct = (nav_now - all_navs[-days]) / all_navs[-days] * 100
                pc = "#e74c3c" if pct < 0 else "#27ae60"
                perf_html += f'<span class="perf-tag" style="color:{pc}">{label} {pct:+.2f}%</span>'

        detail_cards += f"""
        <div class="detail-card">
            <div class="dc-header">
                <span class="dc-name" style="border-left:4px solid {c}">{r['name']}</span>
                <span class="dc-nav">最新 {all_navs[-1]:.4f}</span>
                <span class="dc-pred" style="color:{'#e74c3c' if pred_pct<0 else '#27ae60'}">预测 {pred_pct:+.2f}%</span>
                <span class="dc-action" style="color:{action_color};background:{action_color}15;padding:2px 10px;border-radius:12px;font-weight:600">{action}</span>
            </div>
            <div class="dc-perf">{perf_html}</div>
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
                <span>全局 {r['global_pred']*100:+.2f}%</span>
                <span>板块 {r['sector_pred']*100:+.2f}%</span>
                <span>个体 {r['individual_pred']*100:+.2f}%</span>
            </div>
        </div>"""

    # 整体建议
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
<title>基金预测 v4 — {today_name}</title>

<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f0f2f5;color:#333;padding:12px;max-width:1200px;margin:0 auto}}
.header{{text-align:center;padding:20px 0 12px}}.header h1{{font-size:22px;color:#1a1a2e;margin-bottom:4px}}.header .sub{{color:#888;font-size:12px}}

/* 仪表盘 */
.dashboard{{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:10px;margin-bottom:16px}}
.dash-card{{background:white;border-radius:10px;padding:14px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,0.06)}}
.dash-card .label{{font-size:11px;color:#999;margin-bottom:4px}}.dash-card .value{{font-size:22px;font-weight:700}}

/* 总表 */
.table-wrap{{background:white;border-radius:10px;padding:14px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);overflow-x:auto}}
.table-wrap h2{{font-size:16px;margin-bottom:12px;color:#1a1a2e}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#f8f9fa;padding:8px 10px;text-align:left;font-weight:600;color:#555;border-bottom:2px solid #e9ecef;white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid #f0f0f0;white-space:nowrap}}
tr:hover{{background:#f8f9fb}}
.fund-name-cell{{font-weight:600;color:#1a1a2e;min-width:100px}}
.sector-cell{{color:#888;font-size:12px}}
.reason-cell{{font-size:12px;color:#666;max-width:250px;white-space:normal}}
.progress-bar{{display:inline-block;width:60px;height:6px;background:#eee;border-radius:3px;vertical-align:middle}}
.progress-fill{{height:100%;border-radius:3px;transition:width 0.3s}}
.progress-text{{font-size:10px;color:#999;margin-left:4px}}

/* 整体建议 */
.overall{{background:white;border-radius:10px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);border-left:4px solid {overall_color}}}
.overall h3{{color:{overall_color};font-size:18px;margin-bottom:4px}}.overall p{{color:#666;font-size:13px}}

/* 详细卡片 — 支付宝风格 */
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
    <div class="sub">{update_time} · {today_name} · 三层模型v4 · 每日更新</div>
</div>

<div class="dashboard">
    <div class="dash-card"><div class="label">📈 看涨</div><div class="value" style="color:#27ae60">{bull}</div></div>
    <div class="dash-card"><div class="label">📉 看跌</div><div class="value" style="color:#e74c3c">{bear}</div></div>
    <div class="dash-card"><div class="label">➡️ 震荡</div><div class="value" style="color:#f39c12">{flat}</div></div>
    <div class="dash-card"><div class="label">🟢 加仓</div><div class="value" style="color:#27ae60">{add_count}</div></div>
    <div class="dash-card"><div class="label">🔴 减仓</div><div class="value" style="color:#e74c3c">{reduce_count}</div></div>
    <div class="dash-card"><div class="label">⏰ 时间</div><div class="value" style="color:#3498db;font-size:16px">{today_name}</div></div>
</div>

<div class="overall">
    <h3>{overall}</h3>
    <p>{overall_desc}</p>
</div>

<div class="table-wrap">
    <h2>📋 交易指导总表</h2>
    <table>
        <thead>
            <tr>
                <th>基金</th><th>板块</th><th>今日涨跌</th><th>本周累计</th><th>下周预测</th>
                <th>预测进度</th><th>历史准确率</th><th>操作建议</th><th>原因</th>
            </tr>
        </thead>
        <tbody>{table_rows}</tbody>
    </table>
</div>

<h2 style="font-size:16px;margin:16px 0 10px;padding-left:4px">📈 详细走势</h2>
{detail_cards}

<div class="disclaimer">⚠️ 预测仅供参考，不构成投资建议 · 模型：三层Ridge回归 + Grid Search最优参数 · AI情绪层待接入</div>
<script>
// 支付宝风格图表 — 时间区间切换 + 买卖标记 + 预测线
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

    // 预测数据
    const predDates = fd.pred_dates || [];
    const predNavs = fd.pred_navs || [];

    // 合并显示（历史+预测）
    const allLabels = slicedDates.concat(predDates);
    const histData = slicedNavs.concat(Array(predDates.length).fill(null));
    // 预测线从历史最后一个点开始
    const predLine = Array(slicedDates.length - 1).fill(null).concat([slicedNavs[slicedNavs.length-1]]).concat(predNavs);

    // 涨跌颜色
    const firstNav = slicedNavs[0];
    const lastNav = slicedNavs[slicedNavs.length-1];
    const changePct = ((lastNav - firstNav) / firstNav * 100);
    const lineColor = changePct >= 0 ? '#e74c3c' : '#27ae60';

    // 买卖标记
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
                // 买卖点加大
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
            label: '预测净值',
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

    // 买入价为空时移除该数据集
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
                        filter: function(item) {{ return item.text === '预测净值' || item.text === '买入价'; }}
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

// Init all charts with 6月 default
document.addEventListener('DOMContentLoaded', function() {{
    for (let idx in FUND_DATA) {{
        createChart(idx, 132);
    }}
}});
</script></body></html>"""
    return html


# ========== 主程序 ==========
def main():
    now = datetime.now()
    update_time = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.weekday()
    is_daily = True  # 现在默认日更

    print(f"[{update_time}] 基金预测 v4 — 日级别")

    # 抓取数据
    print("  大盘...", end=" ")
    market_weekly = []
    for name, code in MARKET_INDICES.items():
        data = fetch_sina_kline(code, 999)
        market_weekly.append(calc_returns(daily_to_weekly(data, "close"), "close"))
        time.sleep(0.2)
    print("OK")

    print("  板块...", end=" ")
    sector_weekly = {}
    for sc, si in SECTORS.items():
        data = fetch_sina_kline(si["index"], 999)
        sector_weekly[sc] = calc_returns(daily_to_weekly(data, "close"), "close")
        time.sleep(0.2)
    print("OK")

    all_fund_data = {}
    for fund in FUNDS:
        fc = fund["code"]
        print(f"  {fund['name']}...", end=" ")
        daily = fetch_fund_nav_all(fc)
        if not daily:
            print("FAILED")
            continue
        weekly = daily_to_weekly(daily)
        returns = calc_returns(weekly)
        features, targets = build_features(returns, sector_weekly.get(fund["sector"], []), market_weekly)
        all_fund_data[fc] = {"features": features, "targets": targets, "daily": daily, "returns": returns}
        print(f"OK ({len(daily)}d)")

    # 预测 + 交易建议
    print("\n  预测 & 交易建议...")
    results = []
    for fund in FUNDS:
        fc = fund["code"]
        if fc not in all_fund_data:
            continue
        fd = all_fund_data[fc]

        final, g, s, i = predict_fund(all_fund_data, fc, fund["sector"])
        accuracy = calc_accuracy(fc, fund["sector"], all_fund_data)
        advice = generate_daily_advice(fd, final, now)

        results.append({
            "code": fc, "name": fund["name"], "sector": fund["sector"],
            "history": fd["daily"],
            "final_pred": final, "global_pred": g, "sector_pred": s, "individual_pred": i,
            "accuracy": accuracy, "advice": advice,
        })

        pct = final * 100
        a = advice["action"]
        icon = {"加仓": "🟢", "减持": "🔴", "减仓": "🔴", "持有": "🔵", "观望": "⚪"}.get(a, "⚪")
        print(f"  {icon} {fund['name']}: 预测{pct:+.2f}% | 今日{advice['today_change']:+.2f}% | 本周{advice['week_actual']:+.2f}% | → {a} ({advice['reason']})")

    # 保存本周预测
    if weekday == 0 or get_current_week_prediction() is None:
        pred_record = {
            "date": now.strftime("%Y-%m-%d"),
            "funds": {r["code"]: {"predicted": round(r["final_pred"]*100, 3), "name": r["name"]} for r in results}
        }
        save_prediction(pred_record)
        print("  📝 已保存本周预测记录")

    # 生成HTML
    html = generate_html(results, update_time, is_daily)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    # Copy chart.min.js to same directory as HTML
    import shutil
    chart_src = os.path.join(SCRIPT_DIR, "chart.min.js")
    chart_dst = os.path.join(os.path.dirname(OUTPUT_FILE), "chart.min.js")
    # 如果输出到网站目录，也复制一份到那里
    if "mysite" in OUTPUT_FILE:
        chart_dst = os.path.join(os.path.expanduser("~/mysite"), "chart.min.js")
    if os.path.exists(chart_src) and chart_src != chart_dst:
        shutil.copy2(chart_src, chart_dst)
        print(f"  📦 chart.min.js -> {chart_dst}")

    print(f"\n✅ {OUTPUT_FILE}")


if __name__ == "__main__":
    main()