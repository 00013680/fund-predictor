#!/usr/bin/env python3
"""
基金市场数据爬虫 — 每5分钟抓取A股指数/板块/基金估值，收盘后抓净值
数据存SQLite，零token消耗
"""
import json, os, re, sqlite3, sys, time, traceback
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError

# ========== 配置 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "market_data.db")
LOG_FILE = os.path.join(SCRIPT_DIR, "scraper.log")

# 持仓基金
FUNDS = [
    {"code": "021753", "name": "南方电力C", "sector": "power"},
    {"code": "014064", "name": "银华农业C", "sector": "agri"},
    {"code": "017938", "name": "易方达医疗C", "sector": "med"},
    {"code": "015468", "name": "嘉实农业C", "sector": "agri"},
]

# A股指数
MARKET_INDICES = {
    "上证指数": "sh000001",
    "深证成指": "sz399001",
    "创业板指": "sz399006",
}

# 板块指数
SECTOR_INDICES = {
    "电力": "sz399808",
    "农业": "sh000949",
    "医药": "sz399989",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}

# ========== 数据库 ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 实时行情快照
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        type TEXT NOT NULL,  -- index/sector/fund_est/fund_nav
        code TEXT NOT NULL,
        name TEXT,
        price REAL,
        change_pct REAL,
        volume REAL,
        extra TEXT,
        UNIQUE(ts, type, code)
    )""")
    # 新闻/事件
    c.execute("""CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        source TEXT,
        title TEXT,
        summary TEXT,
        impact TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_snap_type ON snapshots(type)")
    conn.commit()
    return conn

# ========== 抓取函数 ==========
def fetch_url(url, timeout=15):
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def fetch_sina_realtime(symbol):
    """新浪实时行情: 返回 (价格, 涨跌幅%, 成交量万)"""
    try:
        url = f"https://hq.sinajs.cn/list={symbol}"
        text = fetch_url(url)
        # 格式: var hq_str_sh000001="上证指数,3261.557,..."
        match = re.search(r'"(.+)"', text)
        if not match:
            return None
        fields = match.group(1).split(",")
        if len(fields) < 9:
            return None
        name = fields[0]
        price = float(fields[3])
        prev_close = float(fields[2])
        change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
        volume = float(fields[8]) / 10000  # 转万吨
        return {"name": name, "price": price, "change_pct": round(change_pct, 2), "volume": volume}
    except Exception as e:
        log(f"fetch_sina_realtime({symbol}) error: {e}")
        return None

def fetch_fund_est(fund_code):
    """基金实时估值（盘中）"""
    try:
        url = f"https://fundgz.1234567.com.cn/js/{fund_code}.js"
        text = fetch_url(url)
        # 格式: jsonpgz({"fundcode":"...","gsz":"1.2345","gszzl":"-0.12",...})
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
    except Exception as e:
        log(f"fetch_fund_est({fund_code}) error: {e}")
        return None

def fetch_fund_nav(fund_code, days=1):
    """基金历史净值"""
    try:
        url = (f"https://api.fund.eastmoney.com/f10/lsjz?callback=jQuery"
               f"&fundCode={fund_code}&pageIndex=1&pageSize={days}"
               f"&startDate=&endDate=")
        text = fetch_url(url)
        match = re.search(r'\((.+)\)', text)
        if not match:
            return None
        data = json.loads(match.group(1))
        records = data.get("Data", {}).get("LSJZList", [])
        results = []
        for r in records[:days]:
            results.append({
                "date": r.get("FSRQ", ""),
                "nav": float(r.get("DWJZ", 0)),
                "acc_nav": float(r.get("LJJZ", 0)),
                "change_pct": float(r.get("JZZZL", 0)) if r.get("JZZZL") else 0,
            })
        return results
    except Exception as e:
        log(f"fetch_fund_nav({fund_code}) error: {e}")
        return None

def fetch_market_news():
    """抓取东方财富快讯"""
    try:
        url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_home_channel&column=350&order=1&needInteractData=0&page_index=1&page_size=5"
        text = fetch_url(url)
        data = json.loads(text)
        news_list = data.get("data", {}).get("list", [])
        results = []
        for n in news_list[:5]:
            results.append({
                "title": n.get("title", ""),
                "summary": n.get("digest", "")[:200],
                "time": n.get("showTime", ""),
                "source": "东方财富",
            })
        return results
    except Exception as e:
        log(f"fetch_market_news error: {e}")
        return []

# ========== 存储 ==========
def save_snapshot(conn, type, code, name, price, change_pct, volume=0, extra=""):
    try:
        conn.execute(
            "INSERT OR IGNORE INTO snapshots(ts,type,code,name,price,change_pct,volume,extra) VALUES(?,?,?,?,?,?,?,?)",
            (now_str(), type, code, name, price, change_pct, volume, extra)
        )
        conn.commit()
    except Exception as e:
        log(f"save_snapshot error: {e}")

def save_news(conn, articles):
    for a in articles:
        try:
            conn.execute(
                "INSERT INTO news(ts,source,title,summary,impact) VALUES(?,?,?,?,?)",
                (now_str(), a.get("source",""), a.get("title",""), a.get("summary",""), "")
            )
        except:
            pass
    conn.commit()

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg):
    line = f"[{now_str()}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass

# ========== 主逻辑 ==========
def is_trading_hours():
    """判断是否在交易时间（周一到周五 9:15-15:05）"""
    now = datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.time()
    from datetime import time as dt_time
    return dt_time(9, 15) <= t <= dt_time(15, 5)

def is_after_close():
    """收盘后（15:05-15:30）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    from datetime import time as dt_time
    return dt_time(15, 5) <= t <= dt_time(15, 30)

def run_realtime_scrape():
    """盘中实时抓取：指数+板块+基金估值"""
    conn = init_db()
    log("=== 实时抓取开始 ===")

    # A股指数
    for name, symbol in MARKET_INDICES.items():
        data = fetch_sina_realtime(symbol)
        if data:
            save_snapshot(conn, "index", symbol, name, data["price"], data["change_pct"], data["volume"])
            log(f"  {name}: {data['price']} ({data['change_pct']:+.2f}%)")
        time.sleep(0.3)

    # 板块指数
    for name, symbol in SECTOR_INDICES.items():
        data = fetch_sina_realtime(symbol)
        if data:
            save_snapshot(conn, "sector", symbol, name, data["price"], data["change_pct"], data["volume"])
            log(f"  {name}: {data['price']} ({data['change_pct']:+.2f}%)")
        time.sleep(0.3)

    # 基金估值
    for fund in FUNDS:
        data = fetch_fund_est(fund["code"])
        if data:
            save_snapshot(conn, "fund_est", fund["code"], fund["name"], data["nav"], data["change_pct"], extra=data.get("time",""))
            log(f"  {fund['name']}: 估值{data['nav']:.4f} ({data['change_pct']:+.2f}%)")
        time.sleep(0.3)

    # 快讯
    news = fetch_market_news()
    if news:
        save_news(conn, news)
        log(f"  新闻: {len(news)}条")

    conn.close()
    log("=== 实时抓取完成 ===")

def run_nav_scrape():
    """收盘后抓取基金实际净值"""
    conn = init_db()
    log("=== 净值抓取开始 ===")

    for fund in FUNDS:
        records = fetch_fund_nav(fund["code"], days=3)
        if records:
            latest = records[0]
            save_snapshot(conn, "fund_nav", fund["code"], fund["name"],
                         latest["nav"], latest["change_pct"],
                         extra=f"acc_nav={latest['acc_nav']},date={latest['date']}")
            log(f"  {fund['name']}: 净值{latest['nav']:.4f} ({latest['change_pct']:+.2f}%) [{latest['date']}]")
        time.sleep(0.5)

    conn.close()
    log("=== 净值抓取完成 ===")

def get_latest_summary():
    """获取最新数据摘要（供AI分析用）"""
    conn = init_db()
    c = conn.cursor()

    result = {"ts": now_str(), "indices": {}, "sectors": {}, "funds": {}, "news": []}

    # 最新指数
    for name, symbol in MARKET_INDICES.items():
        c.execute("SELECT price, change_pct FROM snapshots WHERE type='index' AND code=? ORDER BY ts DESC LIMIT 1", (symbol,))
        row = c.fetchone()
        if row:
            result["indices"][name] = {"price": row[0], "change_pct": row[1]}

    # 最新板块
    for name, symbol in SECTOR_INDICES.items():
        c.execute("SELECT price, change_pct FROM snapshots WHERE type='sector' AND code=? ORDER BY ts DESC  LIMIT 1", (symbol,))
        row = c.fetchone()
        if row:
            result["sectors"][name] = {"price": row[0], "change_pct": row[1]}

    # 最新基金数据
    for fund in FUNDS:
        fund_data = {}
        # 估值
        c.execute("SELECT price, change_pct, ts FROM snapshots WHERE type='fund_est' AND code=? ORDER BY ts DESC LIMIT 1", (fund["code"],))
        row = c.fetchone()
        if row:
            fund_data["est_nav"] = row[0]
            fund_data["est_change"] = row[1]
            fund_data["est_time"] = row[2]
        # 净值
        c.execute("SELECT price, change_pct, extra FROM snapshots WHERE type='fund_nav' AND code=? ORDER BY ts DESC LIMIT 1", (fund["code"],))
        row = c.fetchone()
        if row:
            fund_data["nav"] = row[0]
            fund_data["nav_change"] = row[1]
            fund_data["nav_info"] = row[2]
        result["funds"][fund["name"]] = fund_data

    # 最新新闻
    c.execute("SELECT title, summary, ts FROM news ORDER BY ts DESC LIMIT 5")
    for row in c.fetchall():
        result["news"].append({"title": row[0], "summary": row[1], "ts": row[2]})

    conn.close()
    return result

# ========== 数据清理 ==========
def cleanup_old_data(conn, keep_days=30):
    """清理超过keep_days天的旧数据，防止数据库无限增长"""
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")
    c = conn.cursor()
    c.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
    deleted_snap = c.rowcount
    c.execute("DELETE FROM news WHERE ts < ?", (cutoff,))
    deleted_news = c.rowcount
    conn.commit()
    if deleted_snap or deleted_news:
        log(f"  清理: 删{deleted_snap}条快照, {deleted_news}条新闻 (保留{keep_days}天)")
    # 压缩数据库
    conn.execute("VACUUM")
    conn.commit()

# ========== 入口 ==========
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"

    if mode == "realtime":
        run_realtime_scrape()
    elif mode == "nav":
        run_nav_scrape()
    elif mode == "summary":
        summary = get_latest_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif mode == "auto":
        # 自动判断：盘中抓实时，收盘后抓净值
        conn = init_db()
        cleanup_old_data(conn)
        conn.close()
        if is_trading_hours():
            run_realtime_scrape()
        elif is_after_close():
            run_nav_scrape()
        else:
            log("非交易时间，跳过")
    else:
        print("用法: python market_scraper.py [realtime|nav|summary|auto]")
