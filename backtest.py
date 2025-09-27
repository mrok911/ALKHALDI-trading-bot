import ccxt
import pandas as pd
import talib
import requests
from datetime import datetime

# ================== إعدادات ==================
EXCHANGE = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "future"}  # Binance Futures
})
TIMEFRAME = "1h"        # الفريم
LIMIT = 500             # عدد الشموع
TOP_N = 10              # عدد العملات المراد اختبارها
MIN_VOLUME_USD = 50_000 # أقل حجم تداول يومي بالدولار
RESULTS_FILE = "results.csv"

# ===== بيانات التليجرام =====
TELEGRAM_BOT_TOKEN = "8394018642:AAFcsChe34lYG4BGKnIX4mSwZF1lgcvsjD0"
TELEGRAM_CHAT_ID = "6373166854"

def send_msg(text):
    """إرسال رسالة للتليجرام"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print("Telegram Error:", e)

# ================== الدوال ==================
def fetch_symbols_top_n(n=TOP_N, min_vol_usd=MIN_VOLUME_USD):
    """جلب رموز Binance Futures الأكثر سيولة (USDT)"""
    print("جلب tickers ...")
    try:
        tickers = EXCHANGE.fetch_tickers()
    except Exception as e:
        print("خطأ بجلب tickers:", e)
        return []

    rows = []
    for sym, t in tickers.items():
        try:
            if not isinstance(sym, str):
                continue
            if not (sym.endswith("/USDT") or ":USDT" in sym):
                continue
            last = float(t.get("last", 0) or 0)
            quote_vol = t.get("quoteVolume") or 0
            base_vol = t.get("baseVolume") or 0
            vol_usd = float(quote_vol) if quote_vol else float(base_vol) * last
            if vol_usd >= min_vol_usd:
                rows.append((sym, vol_usd))
        except:
            continue

    rows_sorted = sorted(rows, key=lambda x: x[1], reverse=True)
    symbols = [r[0] for r in rows_sorted[:n]]
    print(f"اختيار {len(symbols)} زوج (Top by 24h volume).")
    return symbols

def fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT):
    """جلب بيانات OHLCV"""
    try:
        data = EXCHANGE.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df
    except Exception as e:
        print(f"⚠️ خطأ في {symbol}:", e)
        return None

def strategy_signals(df):
    """استراتيجية RSI + MACD"""
    close = df["close"].astype(float).values
    rsi = talib.RSI(close, timeperiod=14)
    macd, macdsignal, macdhist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)

    signal = []
    for i in range(len(close)):
        if rsi[i] < 30 and macd[i] > macdsignal[i]:
            signal.append("BUY")
        elif rsi[i] > 70 and macd[i] < macdsignal[i]:
            signal.append("SELL")
        else:
            signal.append("HOLD")
    df["signal"] = signal
    return df

def backtest(df):
    """باكتيست بسيط"""
    wins, losses = 0, 0
    prev = "HOLD"
    entry = 0
    for _, row in df.iterrows():
        sig = row["signal"]
        price = row["close"]
        if sig == "BUY" and prev != "BUY":
            entry = price
            prev = "BUY"
        elif sig == "SELL" and prev == "BUY":
            if price > entry:
                wins += 1
            else:
                losses += 1
            prev = "SELL"
        elif sig == "SELL" and prev != "SELL":
            entry = price
            prev = "SELL"
        elif sig == "BUY" and prev == "SELL":
            if price < entry:
                wins += 1
            else:
                losses += 1
            prev = "BUY"
    total = wins + losses
    win_rate = wins / total * 100 if total > 0 else 0
    return wins, losses, win_rate

# ================== MAIN ==================
if __name__ == "__main__":
    run_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print("بدء Backtest — time:", run_time)
    symbols = fetch_symbols_top_n()
    results = []
    for sym in symbols:
        df = fetch_ohlcv(sym)
        if df is None or df.empty:
            continue
        df = strategy_signals(df)
        wins, losses, wr = backtest(df)
        results.append({
            "time": run_time,
            "symbol": sym,
            "wins": wins,
            "losses": losses,
            "win_rate": wr
        })
    if results:
        df_results = pd.DataFrame(results)
        df_results = df_results.sort_values(by="win_rate", ascending=False)

        # === حساب الملخص العام ===
        total_wins = df_results["wins"].sum()
        total_losses = df_results["losses"].sum()
        total_trades = total_wins + total_losses
        avg_wr = df_results["win_rate"].mean()
        summary = {
            "time": run_time,
            "symbol": "SUMMARY",
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": avg_wr
        }
        df_results = pd.concat([df_results, pd.DataFrame([summary])], ignore_index=True)

        # عرض النتائج
        print("\n📊 نتائج Backtest:")
        print(df_results.to_string(index=False))

        # إضافة النتائج للملف (append)
        try:
            old = pd.read_csv(RESULTS_FILE)
            df_all = pd.concat([old, df_results], ignore_index=True)
        except FileNotFoundError:
            df_all = df_results

        df_all.to_csv(RESULTS_FILE, index=False, encoding="utf-8-sig")
        print(f"\n✅ تم حفظ النتائج وإضافتها في {RESULTS_FILE}")

        # ===== إرسال النتائج للتليجرام =====
        msg = "📊 <b>نتائج Backtest</b>\n\n"
        for _, row in df_results.iterrows():
            msg += f"{row['symbol']} | ✅ {row['wins']} ❌ {row['losses']} | WinRate: {row['win_rate']:.2f}%\n"
        send_msg(msg)

    else:
        print("❌ لم يتم توليد نتائج.")
        send_msg("❌ لم يتم توليد نتائج الباكتيست.")
