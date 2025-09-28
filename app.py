import os
import logging
import threading
import time
import math
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue
from binance.client import Client as BinanceClient
from binance.client import Client # استيراد بديل لاستخدامه في الثوابت
from ratelimiter import RateLimiter

# --- 1. الإعدادات والثوابت الأساسية (جلب من البيئة مع قيم افتراضية) ---

# سيستخدم البوت قيم Railway (os.getenv) أولاً، ثم التوكنات المذكورة هنا كقيمة احتياطية.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8394018642:AAFcsChe34lYG4BGKnIX4mSwZF1lgcvsjD0")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6373166854")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")      
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET") 
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8000)) 

# ثوابت البوت والمنطق
FULL_SCAN_INTERVAL = 60 
CONFIDENCE_BASE = 80 
TAKE_PROFIT_LEVELS = [1.02, 1.05, 1.10] 
STOP_LOSS_PERCENT = 0.98 
TIME_LIMIT_SECONDS = 3600 * 24 

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- التحقق الحاسم من المتغيرات وتهيئة العميل ---
logger.info("--- Starting Critical Environment Check ---")

# 1. التحقق من اكتمال المتغيرات (خاصة مفاتيح Binance والـ Webhook)
if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BINANCE_API_KEY, BINANCE_API_SECRET, WEBHOOK_URL]):
    # نستخدم logging.critical لإظهار الخطأ بوضوح في سجلات Railway
    logger.critical("FATAL ERROR: One or more critical environment variables (Tokens/Keys/Webhook) are MISSING. Bot cannot start.")
    logger.critical(f"BINANCE_API_KEY present: {bool(BINANCE_API_KEY)}")
    logger.critical(f"WEBHOOK_URL present: {bool(WEBHOOK_URL)}")
    exit(1)

# 2. تهيئة عميل Binance بأكثر طريقة استقراراً
try:
    BINANCE_CLIENT = BinanceClient(BINANCE_API_KEY, BINANCE_API_SECRET)
    # محاولة بسيطة للتحقق من الاتصال (للكشف المبكر عن خطأ المفاتيح)
    BINANCE_CLIENT.ping()
    logger.info("Binance Client initialized and connectivity checked successfully.")
except Exception as e:
    logger.critical(f"FATAL ERROR: Failed to initialize Binance Client. Keys are likely incorrect or have permission issues. Error: {e}")
    exit(1)

# تهيئة Rate Limiter
RATE_LIMITER = RateLimiter(max_calls=1200, period=60) 

# قائمة لتتبع الصفقات النشطة محليًا
active_trades = {} 

logger.info("--- Environment Check Complete. Starting Bot Services. ---")


# --- 2. دوال مساعدة لـ Binance ---

@RATE_LIMITER
def get_klines_data(symbol, interval=Client.KLINE_INTERVAL_4HOUR, limit=100):
    """جلب بيانات الشموع من Binance مع التحكم في معدل الطلبات."""
    try:
        klines = BINANCE_CLIENT.get_klines(symbol=symbol, interval=interval, limit=limit)
        return klines
    except Exception as e:
        logger.error(f"Error fetching klines for {symbol}: {e}")
        return None

# --- 3. دالة فحص السوق (تقوم بالجدولة الأوتوماتيكية) ---

def scan_market_for_signals(context: ContextTypes.DEFAULT_TYPE):
    """الدالة التي يتم جدولتها لفحص السوق وإطلاق الإشارات."""
    logger.info("Starting automated market scan...")
    
    try:
        exchange_info = BINANCE_CLIENT.futures_exchange_info()
        symbols = [s['symbol'] for s in exchange_info['symbols'] if s['status'] == 'TRADING']
    except Exception as e:
        logger.error(f"Failed to fetch exchange info: {e}")
        return
        
    new_signals = []

    for symbol in symbols:
        data = get_klines_data(symbol)
        if data and len(data) > 50:
            current_close = float(data[-1][4])
            avg_close = sum([float(k[4]) for k in data]) / len(data)
            
            if current_close > avg_close * 1.01 and symbol not in active_trades: 
                new_signals.append(symbol)
    
    if new_signals:
        for symbol in new_signals:
            send_signal_and_start_tracker(context.application, symbol)

    logger.info(f"Market scan finished. Found {len(new_signals)} new signals.")

# --- 4. دالة إرسال الإشارة وبدء التتبع ---

def send_signal_and_start_tracker(application, symbol):
    """يرسل الإشارة إلى Telegram ويطلق خيط التتبع للصفقة."""
    
    if symbol in active_trades:
        return

    try:
        ticker = BINANCE_CLIENT.futures_ticker(symbol=symbol)
        entry_price = float(ticker['lastPrice'])
    except Exception as e:
        logger.error(f"Failed to get ticker for {symbol}: {e}")
        return

    # تسجيل الصفقة محليًا
    trade_data = {
        'symbol': symbol,
        'entry_price': entry_price,
        'start_time': time.time(),
        'tp_levels_reached': [False] * len(TAKE_PROFIT_LEVELS),
        'status': 'ACTIVE'
    }
    active_trades[symbol] = trade_data
    
    # بناء رسالة الإشارة
    tp_message = "\n".join([f"TP{i+1}: {entry_price * tp:.4f} (+{round((tp-1)*100, 2)}%)" 
                            for i, tp in enumerate(TAKE_PROFIT_LEVELS)])
    
    message = (
        f"🚨 **إشارة تداول جديدة (Futures)** 🚨\n\n"
        f"**العملة:** `{symbol}`\n"
        f"**الثقة:** {CONFIDENCE_BASE}%\n"
        f"**سعر الدخول:** {entry_price:.4f}\n\n"
        f"**أهداف جني الأرباح:**\n{tp_message}\n\n"
        f"**وقف الخسارة (SL):** {entry_price * STOP_LOSS_PERCENT:.4f} (-2.00%)\n"
        f"**الحد الزمني:** 24 ساعة\n\n"
        f"البوت بدأ التتبع الآلي للصفقة الآن."
    )
    
    # إرسال الرسالة
    application.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    logger.info(f"Signal sent and tracker launched for {symbol} at {entry_price:.4f}")
    
    # إطلاق خيط منفصل لتتبع الصفقة
    tracker_thread = threading.Thread(target=trade_tracker, args=(application, symbol), daemon=True)
    tracker_thread.start()

# --- 5. خيط التتبع الآلي للصفقة (يعمل بشكل مستقل) ---

def trade_tracker(application, symbol):
    """خيط يعمل في الخلفية لتتبع صفقة معينة وتنفيذ SL/TP/Time-Stop."""
    
    trade = active_trades[symbol]
    entry_price = trade['entry_price']
    
    # حساب أهداف الإغلاق
    sl_price = entry_price * STOP_LOSS_PERCENT
    tp_prices = [entry_price * tp for tp in TAKE_PROFIT_LEVELS]
    
    logger.info(f"Tracker started for {symbol}. Entry: {entry_price:.4f}")

    while trade['status'] == 'ACTIVE':
        time.sleep(10) # فحص السعر كل 10 ثوانٍ

        try:
            current_price = float(BINANCE_CLIENT.futures_ticker(symbol=symbol)['lastPrice'])
        except Exception as e:
            logger.error(f"Tracker failed to fetch price for {symbol}: {e}")
            continue

        # 1. فحص وقف الخسارة (SL)
        if current_price <= sl_price:
            close_trade(application, symbol, current_price, "STOP_LOSS")
            return

        # 2. فحص جني الأرباح (TP)
        for i, tp_price in enumerate(tp_prices):
            if current_price >= tp_price and not trade['tp_levels_reached'][i]:
                trade['tp_levels_reached'][i] = True
                
                # إرسال إشعار بوصول الهدف
                application.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID, 
                    text=f"🟢 **تم تحقيق الهدف TP{i+1} للعملة {symbol}** عند سعر {current_price:.4f}!", 
                    parse_mode='Markdown'
                )
                logger.info(f"TP{i+1} hit for {symbol}.")
                
                # إذا تم الوصول لجميع الأهداف، يتم إغلاق الصفقة
                if all(trade['tp_levels_reached']):
                    close_trade(application, symbol, current_price, "TAKE_PROFIT_ALL")
                    return

        # 3. فحص الحد الزمني (Time-Stop)
        if time.time() - trade['start_time'] >= TIME_LIMIT_SECONDS:
            close_trade(application, symbol, current_price, "TIME_LIMIT")
            return
            
    logger.info(f"Tracker for {symbol} finished.")


def close_trade(application, symbol, final_price, reason):
    """دالة لإنهاء الصفقة وإرسال تقرير الإغلاق."""
    
    if symbol not in active_trades or active_trades[symbol]['status'] != 'ACTIVE':
        return 

    trade = active_trades[symbol]
    trade['status'] = 'CLOSED' 
    
    entry_price = trade['entry_price']
    pnl_percent = ((final_price / entry_price) - 1) * 100
    pnl_sign = "✅" if pnl_percent >= 0 else "❌"
    
    reason_map = {
        "STOP_LOSS": "بسبب وصول وقف الخسارة (SL)",
        "TAKE_PROFIT_ALL": "بسبب تحقيق جميع أهداف جني الأرباح (TP)",
        "TIME_LIMIT": "بسبب انتهاء الحد الزمني (24 ساعة)"
    }
    
    message = (
        f"{pnl_sign} **إغلاق صفقة {symbol}** {pnl_sign}\n\n"
        f"**سبب الإغلاق:** {reason_map.get(reason, 'غير معروف')}\n"
        f"**سعر الدخول:** {entry_price:.4f}\n"
        f"**سعر الإغلاق:** {final_price:.4f}\n"
        f"**الربح/الخسارة:** {pnl_percent:+.2f}%"
    )

    application.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
    logger.info(f"Trade {symbol} closed. Reason: {reason}. PnL: {pnl_percent:+.2f}%")
    
    # إزالة الصفقة من القائمة النشطة
    del active_trades[symbol]


# --- 6. دوال أوامر Telegram الأساسية ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرد على أمر /start ويؤكد عمل البوت."""
    
    message = (
        "✅ **مرحبا بالشيخ الخالدي**، على بركه الله البوت شغّال الآن.\n"
        f"يبدأ تحليل أزواج Futures على Binance. ثقة الإشارات = {CONFIDENCE_BASE}%"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض حالة البوت الحالية والصفقات النشطة."""
    
    if not active_trades:
        status_message = "البوت نشط. لا توجد صفقات مفتوحة حاليًا."
    else:
        details = "\n".join([f"• {sym} (Entry: {trade['entry_price']:.4f}, TP Reached: {sum(trade['tp_levels_reached'])}/{len(TAKE_PROFIT_LEVELS)})" 
                             for sym, trade in active_trades.items()])
        status_message = f"البوت نشط. الصفقات المفتوحة:\n\n{details}"

    await update.message.reply_text(status_message)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """يسجل الأخطاء التي تسببها التحديثات."""
    logger.error("Update '%s' caused error '%s'", update, context.error)

# --- 7. دالة التشغيل الرئيسية (مفتاح الأتمتة) ---

def run_app():
    """الدالة التي تقوم بتشغيل البوت، مع التبديل بين وضع Webhook و Polling."""
    
    # تهيئة Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # إعداد جدولة المهام (Job Queue)
    application.job_queue.run_repeating(scan_market_for_signals, 
                                        interval=FULL_SCAN_INTERVAL, 
                                        first=10) 
    
    # إضافة المعالجات (Handlers)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_error_handler(error_handler)

    # تحديد وضع التشغيل (للتشغيل الدائم على Railway)
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        
        logger.info(f"Starting in WEBHOOK mode on port {PORT}. URL: {webhook_url}")
        
        # تعيين Webhook تلقائياً
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN,
            webhook_url=webhook_url
        )
    else:
        # وضع Polling (للاختبار المحلي فقط)
        logger.warning("WEBHOOK_URL not set. Running in Polling mode (Only for local testing, will stop when closed).")
        application.run_polling(poll_interval=1.0)

if __name__ == '__main__':
    run_app()
