import os
import sqlite3
import requests
from flask import Flask, request
from binance.client import Client
import pandas as pd
import threading
import time

# ================== CONFIG ==================
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8394018642:AAFcsChe34lYG4BGKnIX4mSwZF1lgcvsjD0")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6373166854")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://han-nonagglutinative-desultorily.ngrok-free.dev")

app = Flask(__name__)
client = Client(API_KEY, API_SECRET)

DB_FILE = "trades.db"

# ================== DB INIT ==================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        side TEXT,
        entry_price REAL,
        exit_price REAL,
        pnl REAL,
        status TEXT,
        opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP,
        telegram_msg_id INTEGER,
        result TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# ================== TELEGRAM ==================
def send_telegram_message(text, reply_to=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    r = requests.post(url, json=payload).json()
    return r.get("result", {}).get("message_id")

def edit_telegram_message(message_id, new_text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "Markdown",
    }
    requests.post(url, json=payload)

# ================== INDICATORS ==================
def fetch_klines(symbol, interval, limit=200):
    try:
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'timestamp','o','h','l','c','v','c1','c2','c3','c4','c5','c6'
        ])
        df['c'] = df['c'].astype(float)
        return df
    except Exception as e:
        print(f"[fetch_klines] error {e}")
        return None

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line

def compute_bollinger(series, period=20, std=2):
    mid = series.rolling(period).mean()
    stddev = series.rolling(period).std()
    upper = mid + std * stddev
    lower = mid - std * stddev
    return upper, mid, lower

# ================== SIGNALS ==================
def generate_signal(symbol):
    timeframes = ["1m","5m","15m","1h","4h"]
    signals = []

    for tf in timeframes:
        df = fetch_klines(symbol, tf)
        if df is None or len(df) < 20:
            continue
        df['rsi'] = compute_rsi(df['c'])
        df['macd'], df['signal'] = compute_macd(df['c'])
        df['upper'], df['middle'], df['lower'] = compute_bollinger(df['c'])
        last = df.iloc[-1]

        if any(pd.isna([last['rsi'], last['macd'], last['signal'], last['upper'], last['lower']])):
            continue

        if last['rsi'] > 70 and last['macd'] < last['signal'] and last['c'] >= last['upper']:
            signals.append((tf, "SELL", last['c'], last['upper'], last['lower']))
        elif last['rsi'] < 30 and last['macd'] > last['signal'] and last['c'] <= last['lower']:
            signals.append((tf, "BUY", last['c'], last['upper'], last['lower']))

    if not signals:
        return None

    sides = [s[1] for s in signals]
    side = sides[-1] if len(set(sides)) > 1 else sides[0]

    entry = signals[-1][2]
    upper = signals[-1][3]
    lower = signals[-1][4]

    atr = (upper - lower) / 2
    if atr <= 0 or entry is None:
        return None

    if side == "BUY":
        tp1, tp2, tp3 = entry + atr*0.5, entry + atr*1.0, entry + atr*1.5
        sl = entry - atr*0.5
    else:
        tp1, tp2, tp3 = entry - atr*0.5, entry - atr*1.0, entry - atr*1.5
        sl = entry + atr*0.5

    return {
        "symbol": symbol,
        "side": side,
        "entry": float(entry),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "sl": float(sl),
        "timeframes": "/".join([s[0] for s in signals]),
        "confidence": 100 if len(signals) > 1 else 80
    }

# ================== MONITOR ==================
def monitor():
    while True:
        try:
            symbols = [s['symbol'] for s in client.futures_exchange_info()['symbols'] if s['quoteAsset']=="USDT"]
            print(f"[🚀] scanning {len(symbols)} symbols (limit 200)")
            for symbol in symbols[:200]:
                signal = generate_signal(symbol)
                if signal:
                    send_trade_signal(signal)
            time.sleep(10)
        except Exception as e:
            print("[monitor] error", e)
            time.sleep(5)

def send_trade_signal(sig):
    link = f"[{sig['symbol']}](https://www.binance.com/en/futures/{sig['symbol']})"
    text = f"{'🟢' if sig['side']=='BUY' else '🔴'} {link} { 'صاعد' if sig['side']=='BUY' else 'هابط'}\n"
    text += f"⏱️ {sig['timeframes']}    دخول: {sig['entry']:.6f}\n\n"
    text += f"TP1: {sig['tp1']:.6f}\nTP2: {sig['tp2']:.6f}\nTP3: {sig['tp3']:.6f}\n\n"
    text += f"SL: {sig['sl']:.6f}    الثقة: {sig['confidence']}%"

    msg_id = send_telegram_message(text)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO trades (symbol, side, entry_price, status, telegram_msg_id) VALUES (?,?,?,?,?)",
              (sig['symbol'], sig['side'], sig['entry'], 'open', msg_id))
    conn.commit()
    conn.close()

# ================== COMMANDS ==================
def handle_command(text):
    if text == "/help":
        return (
            "🛠️ الأوامر المتاحة:\n"
            "/help - عرض المساعدة\n"
            "/open - الصفقات المفتوحة\n"
            "/history - آخر 10 صفقات مغلقة\n"
            "/stats - أداء البوت"
        )
    elif text == "/open":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT symbol, side, entry_price, opened_at FROM trades WHERE status='open'")
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "لا يوجد صفقات مفتوحة حالياً."
        return "\n".join([f"🔓 {r[0]} {r[1]} دخول {r[2]} وقت {r[3]}" for r in rows])
    elif text == "/history":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT symbol, side, entry_price, exit_price, result FROM trades WHERE status='closed' ORDER BY closed_at DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        if not rows:
            return "لا يوجد صفقات سابقة."
        return "\n".join([f"{r[0]} {r[1]} دخول {r[2]} خروج {r[3]} ➝ {r[4]}" for r in rows])
    elif text == "/stats":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM trades")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE status='open'")
        open_trades = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE result='TP1'")
        tp1 = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE result='TP2'")
        tp2 = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE result='TP3'")
        tp3 = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE result='SL'")
        sl = c.fetchone()[0]
        conn.close()

        def pct(x): return f"{(x/total*100):.1f}%" if total else "0%"
        return (
            f"📊 الأداء\n"
            f"إجمالي الصفقات: {total}       🔓 OPEN: {open_trades} ({pct(open_trades)})\n\n"
            f"🎯 TP1: {tp1} ({pct(tp1)})\n"
            f"🎯 TP2: {tp2} ({pct(tp2)})\n"
            f"🏆 TP3: {tp3} ({pct(tp3)})\n"
            f"❌ SL: {sl} ({pct(sl)})"
        )
    return None

# ================== WEBHOOK ==================
@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json()
    if "message" in update:
        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "")
        print(f"[Telegram] Received: {text}")
        reply = handle_command(text)
        if reply:
            send_telegram_message(reply)
    return {"ok": True}

def set_webhook():
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    requests.post(url, json={"url": f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"})
    print("[Webhook] setWebhook called")

# ================== START ==================
if __name__ == "__main__":
    set_webhook()
    send_telegram_message(
        "✅ مرحبا بالشيخ الخالدي،\n"
        "على بركه الله البوت شغّال الآن ويبدأ تحليل أزواج Futures على Binance.\n\n"
        "ثقة الإشارات = 80%                                 /help /open /history /stats"
    )
    print("[🚀] ALKHALDI Bot Started...")
    threading.Thread(target=monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
