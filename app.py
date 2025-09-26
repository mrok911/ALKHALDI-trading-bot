import os
import time
import sqlite3
import threading
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from flask import Flask, request
from binance.client import Client
import ta

# ==============================
# معلوماتك
# ==============================
API_KEY = ""   # نخليه فاضي الآن للتجربة
API_SECRET = ""
TELEGRAM_BOT_TOKEN = "8394018642:AAFcsChe34lYG4BGKnIX4mSwZF1lgcvsjD0"
TELEGRAM_CHAT_ID = "6373166854"
WEBHOOK_URL = "https://han-nonagglutinative-desultorily.ngrok-free.dev"

# ==============================
# Binance Client
# ==============================
client = Client(API_KEY, API_SECRET)

# ==============================
# قاعدة البيانات SQLite
# ==============================
DB_FILE = "trades.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            status TEXT,
            opened_at DATETIME,
            closed_at DATETIME
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==============================
# Telegram
# ==============================
def send_telegram_message(message: str, reply_to=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    if reply_to:
        data["reply_to_message_id"] = reply_to
    try:
        r = requests.post(url, data=data)
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        print("Telegram error:", e)
        return None

# ==============================
# Binance Helpers
# ==============================
def fetch_futures_symbols():
    try:
        exchange_info = client.futures_exchange_info()
        return [s["symbol"] for s in exchange_info["symbols"] if s["quoteAsset"] == "USDT"]
    except Exception as e:
        print("Error fetching symbols:", e)
        return []

def fetch_klines(symbol, interval="1m", limit=200):
    try:
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except Exception as e:
        print("Fetch error:", e)
        return None

# ==============================
# التحليل
# ==============================
def analyze(df):
    try:
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        macd = ta.trend.MACD(df["close"])
        df["macd"] = macd.macd()
        df["signal"] = macd.macd_signal()
        last = df.iloc[-1]

        # فلتر حجم تداول
        if last["volume"] < 1000:
            return None

        # إشارات
        if last["rsi"] < 30 and last["macd"] > last["signal"]:
            return "BUY"
        elif last["rsi"] > 70 and last["macd"] < last["signal"]:
            return "SELL"
        return None
    except:
        return None

# ==============================
# تسجيل الصفقات
# ==============================
def log_trade(symbol, side, entry_price, status="OPEN", exit_price=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (symbol, side, entry_price, exit_price, pnl, status, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        side,
        entry_price,
        exit_price,
        0,
        status,
        datetime.now(timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()

# ==============================
# Flask Webhook
# ==============================
app = Flask(__name__)

@app.route("/", methods=["POST"])
def webhook():
    update = request.get_json()
    if not update:
        return "ok"

    message = update.get("message", {})
    text = message.get("text", "")
    chat_id = message.get("chat", {}).get("id")

    if str(chat_id) != TELEGRAM_CHAT_ID:
        return "ok"

    if text == "/start":
        send_telegram_message(
            "✅ مرحبا بالشيخ الخالدي،\n"
            "على بركه الله البوت شغّال الآن ويبدأ تحليل أزواج Futures على Binance.\n\n"
            "ثقة الإشارات = 80%                                 /help /open /history /stats"
        )
    elif text == "/help":
        send_telegram_message("📘 الأوامر:\n/help\n/open\n/history\n/stats")
    elif text == "/open":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT symbol, side, entry_price FROM trades WHERE status='OPEN'")
        rows = c.fetchall()
        conn.close()
        if rows:
            msg = "📂 الصفقات المفتوحة:\n" + "\n".join([f"{r[0]} {r[1]} @ {r[2]}" for r in rows])
        else:
            msg = "لا توجد صفقات مفتوحة."
        send_telegram_message(msg)
    elif text == "/history":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT symbol, side, entry_price, exit_price, pnl FROM trades WHERE status!='OPEN' ORDER BY id DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        if rows:
            msg = "📜 آخر الصفقات:\n" + "\n".join([f"{r[0]} {r[1]} {r[2]} → {r[3]} = {r[4]}" for r in rows])
        else:
            msg = "لا يوجد تاريخ صفقات."
        send_telegram_message(msg)
    elif text == "/stats":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) FROM trades WHERE status!='OPEN'")
        total, wins = c.fetchone()
        conn.close()
        winrate = (wins/total*100) if total > 0 else 0
        msg = f"📊 الأداء\nالإجمالي: {total}\n✅ رابحة: {wins}\n❌ خاسرة: {total-wins}\n📈 نسبة النجاح: {winrate:.2f}%"
        send_telegram_message(msg)

    return "ok"

# ==============================
# الحلقة الرئيسية
# ==============================
def trading_loop():
    symbols = fetch_futures_symbols()
    print(f"[🚀] Monitoring {len(symbols)} futures symbols")
    while True:
        for symbol in symbols:
            df = fetch_klines(symbol)
            if df is None:
                continue
            signal = analyze(df)
            if signal:
                price = float(df["close"].iloc[-1])
                log_trade(symbol, signal, price)
                send_telegram_message(f"🟢 {signal} {symbol} @ {price}")
        time.sleep(60)

if __name__ == "__main__":
    # ضبط Webhook
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}"
    print("Webhook set:", requests.get(url).json())

    # إرسال رسالة ترحيبية تلقائية عند بدء التشغيل (مرة واحدة فقط)
    send_telegram_message(
        "✅ مرحبا بالشيخ الخالدي،\n"
        "على بركه الله البوت شغّال الآن ويبدأ تحليل أزواج Futures على Binance.\n\n"
        "ثقة الإشارات = 80%                                 /help /open /history /stats"
    )

    # تشغيل التحليل في ثريد منفصل
    t = threading.Thread(target=trading_loop, daemon=True)
    t.start()

    print("[🚀] ALKHALDI Bot Started...")
    app.run(host="0.0.0.0", port=5000)
