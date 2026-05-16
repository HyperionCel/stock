import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, date
import uvicorn
import threading

from models import init_db, get_session, Trade, Setting
from stock_service import load_stock_db, search_stocks, get_realtime_price, get_highest_close_since, get_hkd_rate, get_currency

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    t = threading.Thread(target=lambda: load_stock_db(), daemon=True)
    t.start()
    yield


app = FastAPI(title="股票投资助手", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TradeCreate(BaseModel):
    stock_code: str
    stock_name: str
    sector: str = ""
    direction: str
    quantity: int
    price: float
    trade_date: str


class SettingsUpdate(BaseModel):
    cash: float = 0.0
    debt: float = 0.0


# ====== 前端 ======

@app.get("/", response_class=HTMLResponse)
def index():
    html_path = os.path.join(BASE_DIR, "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Stock Assistant</h1>")


@app.get("/manifest.json")
def manifest():
    return FileResponse(os.path.join(BASE_DIR, "static", "manifest.json"))


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(BASE_DIR, "static", "sw.js"))


# ====== 股票搜索 ======

@app.get("/api/stocks/search")
def api_search_stocks(q: str = ""):
    return {"results": search_stocks(q)}


# ====== 股票实时价格 ======

@app.get("/api/stocks/price/{code}")
def api_stock_price(code: str):
    price_data = get_realtime_price(code)
    if price_data is None:
        raise HTTPException(status_code=404, detail="获取价格失败")
    return price_data


# ====== 交易记录 ======

@app.post("/api/trades")
def create_trade(trade: TradeCreate):
    session = get_session()
    try:
        trade_date = datetime.strptime(trade.trade_date, "%Y-%m-%d").date()
        db_trade = Trade(
            stock_code=trade.stock_code, stock_name=trade.stock_name,
            sector=trade.sector, direction=trade.direction,
            quantity=trade.quantity, price=trade.price,
            trade_date=trade_date,
        )
        session.add(db_trade)
        session.commit()
        session.refresh(db_trade)
        return {"id": db_trade.id, "message": "交易记录已创建"}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@app.get("/api/trades")
def list_trades():
    session = get_session()
    try:
        trades = session.query(Trade).order_by(Trade.trade_date.desc(), Trade.id.desc()).all()
        return {"trades": [
            {
                "id": t.id, "stock_code": t.stock_code,
                "stock_name": t.stock_name, "sector": t.sector,
                "direction": t.direction, "quantity": t.quantity,
                "price": t.price,
                "trade_date": t.trade_date.strftime("%Y-%m-%d") if t.trade_date else "",
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
            }
            for t in trades
        ]}
    finally:
        session.close()


@app.delete("/api/trades/{trade_id}")
def delete_trade(trade_id: int):
    session = get_session()
    try:
        trade = session.query(Trade).filter(Trade.id == trade_id).first()
        if not trade:
            raise HTTPException(status_code=404, detail="交易记录不存在")
        session.delete(trade)
        session.commit()
        return {"message": "已删除"}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


# ====== 组合数据 ======

def calc_take_profit(highest_pnl_pct):
    if highest_pnl_pct < 20:
        return None
    elif highest_pnl_pct < 30:
        drawdown = 0.40
    elif highest_pnl_pct < 50:
        drawdown = 0.30
    elif highest_pnl_pct < 100:
        drawdown = 0.20
    else:
        drawdown = 0.15
    return 1 - drawdown


@app.get("/api/portfolio")
def get_portfolio():
    session = get_session()
    try:
        trades = session.query(Trade).order_by(Trade.trade_date.asc()).all()
        cash_setting = session.query(Setting).filter(Setting.key == "cash").first()
        debt_setting = session.query(Setting).filter(Setting.key == "debt").first()
        cash = cash_setting.value if cash_setting else 0.0
        debt = debt_setting.value if debt_setting else 0.0
        session.close()

        hkd_rate = get_hkd_rate()

        positions = {}
        for t in trades:
            key = t.stock_code
            if key not in positions:
                positions[key] = {
                    "stock_code": t.stock_code, "stock_name": t.stock_name,
                    "sector": t.sector or "未分类",
                    "hold_qty": 0, "hold_cost": 0.0,
                    "first_buy_date": None,
                }
            p = positions[key]
            if t.direction == "买入":
                p["hold_qty"] += t.quantity
                p["hold_cost"] += t.quantity * t.price
                if p["first_buy_date"] is None or t.trade_date < p["first_buy_date"]:
                    p["first_buy_date"] = t.trade_date
            else:
                # 卖出：按卖出金额减少持仓成本（含已实现盈亏）
                sell_amt = t.quantity * t.price
                avg = p["hold_cost"] / p["hold_qty"] if p["hold_qty"] > 0 else 0
                p["hold_qty"] -= t.quantity
                p["hold_cost"] -= sell_amt

        position_list = []
        total_market_value_rmb = 0.0

        for code, p in positions.items():
            try:
                net_shares = p["hold_qty"]
                if net_shares <= 0:
                    continue
                avg_cost = round(p["hold_cost"] / p["hold_qty"], 4) if p["hold_qty"] > 0 else 0

                price_data = get_realtime_price(code)
                if price_data is None:
                    continue

                currency = get_currency(code)
                cp = price_data["price"]
                mkt_val = round(net_shares * cp, 2)
                # 港股换算为人民币
                mkt_val_rmb = round(mkt_val * hkd_rate, 2) if currency == "HKD" else mkt_val
                pnl_amt = round((cp - avg_cost) * net_shares, 2)
                pnl_amt_rmb = round(pnl_amt * hkd_rate, 2) if currency == "HKD" else pnl_amt
                pnl_pct = round((cp - avg_cost) / avg_cost * 100, 2) if avg_cost > 0 else 0

                highest_close = None
                highest_pnl_pct = 0.0
                take_profit_price = None
                stop_loss_price = None

                if p["first_buy_date"]:
                    try:
                        highest_close = get_highest_close_since(code, p["first_buy_date"])
                    except Exception:
                        pass

                if highest_close:
                    highest_pnl_pct = round((highest_close - avg_cost) / avg_cost * 100, 2)
                    tp_ratio = calc_take_profit(highest_pnl_pct)
                    if tp_ratio is not None:
                        take_profit_price = round(avg_cost + (highest_close - avg_cost) * tp_ratio, 2)
                    stop_loss_price = round(avg_cost * 0.93, 2)

                position_list.append({
                    "stock_code": code, "stock_name": p["stock_name"],
                    "sector": p["sector"],
                    "currency": currency,
                    "first_buy_date": p["first_buy_date"].strftime("%Y-%m-%d") if p["first_buy_date"] else "",
                    "avg_cost": round(avg_cost, 2), "shares": net_shares,
                    "current_price": cp, "market_value": mkt_val,
                    "market_value_rmb": mkt_val_rmb,
                    "pnl_amount": round(pnl_amt, 2), "pnl_amount_rmb": pnl_amt_rmb,
                    "pnl_pct": pnl_pct,
                    "highest_close": round(highest_close, 2) if highest_close else None,
                    "highest_pnl_pct": highest_pnl_pct,
                    "take_profit_price": take_profit_price,
                    "stop_loss_price": stop_loss_price,
                })
                total_market_value_rmb += mkt_val_rmb
            except Exception:
                continue

        account_total_rmb = round(total_market_value_rmb + cash - debt, 2)

        # 按板块分组，每个板块包含个股详情和小计
        sector_map = {}
        for pos in position_list:
            sec = pos["sector"]
            pos["pct_of_total"] = round(pos["market_value_rmb"] / account_total_rmb * 100, 2) if account_total_rmb > 0 else 0
            if sec not in sector_map:
                sector_map[sec] = {"sector": sec, "positions": [], "sector_value_rmb": 0.0}
            sector_map[sec]["positions"].append(pos)
            sector_map[sec]["sector_value_rmb"] += pos["market_value_rmb"]

        # 按板块市值占比降序排列
        sector_list = []
        for sec, data in sector_map.items():
            sector_list.append({
                "sector": sec,
                "count": len(data["positions"]),
                "sector_value_rmb": round(data["sector_value_rmb"], 2),
                "sector_pct": round(data["sector_value_rmb"] / account_total_rmb * 100, 2) if account_total_rmb > 0 else 0,
                "positions": sorted(data["positions"], key=lambda x: x["market_value_rmb"], reverse=True),
            })
        sector_list.sort(key=lambda x: x["sector_pct"], reverse=True)

        return {
            "positions": position_list,
            "total_stock_value_rmb": round(total_market_value_rmb, 2),
            "cash": cash, "debt": debt,
            "account_total_rmb": account_total_rmb,
            "sector_summary": sector_list,
            "hkd_rate": hkd_rate,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            session.close()
        except:
            pass


# ====== 设置 ======

@app.post("/api/settings")
def update_settings(settings: SettingsUpdate):
    session = get_session()
    try:
        for key, val in [("cash", settings.cash), ("debt", settings.debt)]:
            existing = session.query(Setting).filter(Setting.key == key).first()
            if existing:
                existing.value = val
            else:
                session.add(Setting(key=key, value=val))
        session.commit()
        return {"message": "设置已更新"}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@app.get("/api/settings")
def get_settings():
    session = get_session()
    try:
        cash_row = session.query(Setting).filter(Setting.key == "cash").first()
        debt_row = session.query(Setting).filter(Setting.key == "debt").first()
        return {"cash": cash_row.value if cash_row else 0.0, "debt": debt_row.value if debt_row else 0.0}
    finally:
        session.close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else "8000"))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
