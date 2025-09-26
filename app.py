import os
import time
import sqlite3
import threading
import requests
import pandas as pd
import numpy as np
from binance.client import Client
import ta

# ==============================
# معلوماتك (تيليجرام)
# ==============================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8394018642:AAFcsChe34lYG4BGKnIX4mSwZF1lgcvsjD0")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6373166854")

# ==============================
# Binance Client بدون API (مشاهدة فقط)
# ==============================
client = Client("", "")

# ==============================
# SQLite
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
            status TEXT,
            tp1 REAL,
            tp2 REAL,
            tp3 REAL,
            sl REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==============================
# تيليجرام
# ==============================
def send_telegram_message(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print("Telegram error:", e)

# ==============================
# بيانات السوق
# ==============================
def fetch_klines(symbol="BTCUSDT", interval="1m", limit=200):
    try:
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
        ])
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["volume"] = df["volume"].astype(float)
        return df
    except Exception as e:
        print("Fetch error:", e)
        return None

# ==============================
# التحليل الفني
# ==============================
def analyze(df):
    try:
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        macd_ind = ta.trend.MACD(df["close"])
        df["macd"] = macd_ind.macd()
        df["signal"] = macd_ind.macd_signal()
        boll = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_high"] = boll.bollinger_hband()
        df["bb_low"] = boll.bollinger_lband()
        atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14)
        df["atr"] = atr.average_true_range()

        last = df.iloc[-1]

        # فلتر الحجم
        if last["volume"] < 100:
            return None

        # إشارات تداول
        if last["rsi"] < 30 and last["macd"] > last["signal"]:
            return "BUY", last
        elif last["rsi"] > 70 and last["macd"] < last["signal"]:
            return "SELL", last
        return None
    except Exception as e:
        print("Error analyze:", e)
        return None

# ==============================
# تنفيذ الصفقة
# ==============================
def execute_trade(symbol, side, last):
    entry = float(last["close"])
    atr = float(last["atr"])

    if side == "BUY":
        tp1, tp2, tp3 = entry + atr, entry + 2*atr, entry + 3*atr
        sl = entry - 1.5*atr
    else:
        tp1, tp2, tp3 = entry - atr, entry - 2*atr, entry - 3*atr
        sl = entry + 1.5*atr

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (symbol, side, entry_price, status, tp1, tp2, tp3, sl)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, side, entry, "OPEN", tp1, tp2, tp3, sl))
    conn.commit()
    conn.close()

    msg = f"""
🟢 *{symbol}* [{side}]
Entry: `{entry}`
TP1: `{tp1}`
TP2: `{tp2}`
TP3: `{tp3}`
SL: `{sl}`
"""
    send_telegram_message(msg)

# ==============================
# الحلقة الرئيسية
# ==============================
def loop():
    symbol = "BTCUSDT"
    while True:
        df = fetch_klines(symbol)
        if df is not None:
            result = analyze(df)
            if result:
                side, last = result
                execute_trade(symbol, side, last)
        time.sleep(60)

if __name__ == "__main__":
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    while True:
        time.sleep(1)
