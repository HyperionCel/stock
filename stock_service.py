import pandas as pd
import requests
import json
import os
from pypinyin import lazy_pinyin, Style
from datetime import datetime, timedelta, date
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
A_CACHE_FILE = os.path.join(BASE_DIR, "a_stocks_cache.csv")
HK_CACHE_FILE = os.path.join(BASE_DIR, "hk_stocks_cache.csv")
_stock_db = None
_last_refresh = None


def _generate_pinyin(text):
    try:
        initials = lazy_pinyin(text, style=Style.FIRST_LETTER)
        full = lazy_pinyin(text, style=Style.NORMAL)
        return "".join(initials).lower(), "".join(full).lower()
    except Exception:
        return "", ""


def _get_market(raw_code):
    code = str(raw_code).strip()
    if re.match(r"^\d{5}$", code):
        return "hk"
    c = code.zfill(6)
    if c.startswith(("0", "3", "6")):
        return "a"
    return "hk"


def _load_csv(filepath, label):
    """从 CSV 缓存文件加载股票列表"""
    if not os.path.exists(filepath):
        print(f"{label}缓存不存在: {filepath}")
        return []
    try:
        df = pd.read_csv(filepath, dtype={"code": str})
        df = df.fillna("")
        records = df.to_dict(orient="records")
        print(f"{label}列表(缓存): {len(records)}条")
        return records
    except Exception as e:
        print(f"{label}缓存读取失败: {e}")
        return []


def load_stock_db():
    global _stock_db, _last_refresh
    now = datetime.now()
    if _last_refresh and (now - _last_refresh).total_seconds() < 3600:
        return True

    try:
        records = []
        records.extend(_load_csv(A_CACHE_FILE, "A股"))
        records.extend(_load_csv(HK_CACHE_FILE, "港股"))
        if records:
            _stock_db = pd.DataFrame(records)
            _last_refresh = now
        print(f"股票数据库加载完成, 共{len(records)}条")
        return True
    except Exception as e:
        print(f"加载股票数据库失败: {e}")
        return False


def search_stocks(query, limit=20):
    if _stock_db is None:
        load_stock_db()

    db = _stock_db
    if db is None or db.empty:
        return []

    q = query.strip().lower()
    if not q:
        return []

    code_match = db[db["code"].astype(str).str.startswith(q, na=False)]
    name_match = db[db["name"].astype(str).str.contains(q, case=False, na=False)]
    pinyin_match = db[db["pinyin"].astype(str).str.contains(q, case=False, na=False)] if "pinyin" in db.columns else pd.DataFrame()
    pinyin_full_match = db[db["pinyin_full"].astype(str).str.contains(q, case=False, na=False)] if "pinyin_full" in db.columns else pd.DataFrame()

    result = pd.concat([code_match, name_match, pinyin_match, pinyin_full_match])
    result = result.drop_duplicates(subset=["code"])
    result = result.head(limit)

    return json.loads(result.to_json(orient="records"))


def _sina_prefix(raw_code):
    code = str(raw_code).strip()
    market = _get_market(code)
    if market == "hk":
        return "hk" + code.zfill(5)
    c = code.zfill(6)
    if c.startswith(("6", "9")):
        return "sh" + c
    return "sz" + c


def _parse_sina_price(code):
    try:
        prefix = _sina_prefix(code)
        url = f"http://hq.sinajs.cn/list={prefix}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        r = requests.get(url, headers=headers, timeout=10)
        text = r.text.strip()
        if "hq_str_" not in text:
            return None

        data = text.split('"')[1].split(",")
        market = _get_market(code)

        if market == "hk":
            if len(data) < 15:
                return None
            name = data[1]
            open_p = float(data[2]) if data[2] else 0
            pre_close = float(data[3]) if data[3] else 0
            high = float(data[4]) if data[4] else 0
            low = float(data[5]) if data[5] else 0
            price = float(data[6]) if data[6] else 0
            change_pct = float(data[8]) if data[8] else 0
        else:
            if len(data) < 32:
                return None
            name = data[0]
            open_p = float(data[1]) if data[1] else 0
            pre_close = float(data[2]) if data[2] else 0
            price = float(data[3]) if data[3] else 0
            high = float(data[4]) if data[4] else 0
            low = float(data[5]) if data[5] else 0
            change_pct = round((price - pre_close) / pre_close * 100, 2) if pre_close else 0

        return {
            "code": code, "name": name, "price": price,
            "change_pct": change_pct, "high": high,
            "low": low, "open": open_p, "pre_close": pre_close,
        }
    except Exception:
        return None


def get_realtime_price(stock_code):
    raw = str(stock_code).strip()
    market = _get_market(raw)
    if market == "hk":
        code = raw.zfill(5)
    else:
        code = raw.zfill(6)
    return _parse_sina_price(code)


def get_historical_data(stock_code, start_date=None):
    raw = str(stock_code).strip()
    market = _get_market(raw)

    if market != "a":
        return []

    try:
        prefix = _sina_prefix(raw)
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}&scale=240&datalen=500"
        r = requests.get(url, timeout=8)
        if not r.text or not r.text.strip():
            return []
        data = json.loads(r.text)
        if not data:
            return []
        records = []
        for item in data:
            d = item.get("day", "")
            if start_date and d < start_date:
                continue
            records.append({
                "date": d,
                "close": float(item.get("close", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "open": float(item.get("open", 0)),
                "volume": float(item.get("volume", 0)),
            })
        return records
    except Exception as e:
        print(f"获取历史数据失败 {stock_code}: {e}")
    return []


def get_highest_close_since(stock_code, since_date):
    if isinstance(since_date, date):
        since_date = since_date.strftime("%Y-%m-%d")
    hist = get_historical_data(stock_code, start_date=since_date)
    if hist:
        closes = [h["close"] for h in hist]
        return max(closes) if closes else None
    return None


_HKD_RATE = None
_HKD_RATE_TIME = None


def get_hkd_rate():
    global _HKD_RATE, _HKD_RATE_TIME
    now = datetime.now()
    if _HKD_RATE_TIME and (now - _HKD_RATE_TIME).total_seconds() < 3600:
        return _HKD_RATE
    try:
        url = "http://hq.sinajs.cn/list=fx_shkdcny"
        headers = {"Referer": "https://finance.sina.com.cn"}
        r = requests.get(url, headers=headers, timeout=5)
        data = r.text.split('"')[1].split(",")
        rate = float(data[1]) if len(data) > 1 and data[1] else 0.87
        _HKD_RATE = rate
        _HKD_RATE_TIME = now
        return rate
    except Exception:
        return _HKD_RATE or 0.87


def get_currency(stock_code):
    m = _get_market(str(stock_code).strip())
    return "HKD" if m == "hk" else "CNY"
