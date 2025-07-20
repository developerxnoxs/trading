import logging
import base64
import json
import re
import os
import requests
import mplfinance as mpf
import pandas as pd
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from pytz import timezone as tz
import nest_asyncio
import asyncio

nest_asyncio.apply()

# --- AMBIL DARI ENVIRONMENT ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

logging.basicConfig(level=logging.INFO)

def fetch_btc_kucoin(interval="15min", candle_limit=200):
    symbol = "BTC-USDT"
    interval_map = {
        "1min": 60, "3min": 180, "5min": 300, "15min": 900,
        "30min": 1800, "1hour": 3600, "2hour": 7200,
        "4hour": 14400, "6hour": 21600, "8hour": 28800,
        "12hour": 43200, "1day": 86400, "1week": 604800
    }
    if interval not in interval_map:
        print("❌ Interval tidak valid.")
        return []
    end_at = int(datetime.now(timezone.utc).timestamp())
    start_at = end_at - interval_map[interval] * candle_limit

    try:
        response = requests.get("https://api.kucoin.com/api/v1/market/candles", params={
            "symbol": symbol,
            "type": interval,
            "startAt": start_at,
            "endAt": end_at
        })
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "200000":
            return None
        return sorted(data["data"], key=lambda x: x[0])
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def generate_candlestick_chart(data, filename="chart.jpg", tf="15min"):
    if not data: return None
    ohlc = []
    for item in data:
        ts = datetime.fromtimestamp(int(item[0]), tz=tz("Asia/Jakarta"))
        ohlc.append([
            ts,
            float(item[1]), float(item[3]), float(item[4]),
            float(item[2]), float(item[5])
        ])
    df = pd.DataFrame(ohlc, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    df.set_index("Date", inplace=True)

    mc = mpf.make_marketcolors(up='green', down='red', wick='gray', volume='in')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', facecolor='white')

    mpf.plot(
        df, type='candle', volume=True, style=s,
        title=f"BTC/USDT ({tf} - KuCoin)", ylabel="USDT",
        ylabel_lower="Volume", savefig=dict(fname=filename, dpi=150),
        figratio=(16, 9), figscale=1.5, tight_layout=True
    )
    return filename

def analyze_image(image_path):
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": """Ini adalah chart BTCUSDT. Lakukan analisa teknikal:
- Sinyal saat ini (BUY/SELL)
- Entry ideal
- Take Profit & Stop Loss yang ideal
- Pola candlestick penting
- Kesimpulan dalam 1 kalimat"""},
                {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}
            ]
        }]
    }
    headers = {"Content-Type": "application/json"}
    res = requests.post(url, headers=headers, data=json.dumps(payload))
    if res.status_code == 200:
        return res.json()["candidates"][0]["content"]["parts"][0]["text"]
    return f"❌ Gagal menganalisis gambar ({res.status_code})"

def format_reply(text):
    text = re.sub(r"[*_`]", "", text).strip().lower()
    output, seen = {}, set()
    for line in text.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip(), val.strip()
        if not val: continue
        if "sinyal" in key and "sinyal" not in seen:
            output["sinyal"] = f"🔁 Sinyal: {val}\n"; seen.add("sinyal")
        elif any(k in key for k in ["entry", "masuk", "ideal"]) and "entry" not in seen:
            output["entry"] = f"🎯 Entry: {val}\n"; seen.add("entry")
        elif "tp" in key or "take profit" in key and "tp" not in seen:
            output["tp"] = f"🎯 TP: {val}\n"; seen.add("tp")
        elif "sl" in key or "stop loss" in key and "sl" not in seen:
            output["sl"] = f"🛑 SL: {val}\n"; seen.add("sl")
        elif any(k in key for k in ["pola", "engulf", "pinbar", "doji", "double"]) and "pola" not in seen:
            output["pola"] = f"🕯️ {line}\n"; seen.add("pola")
        elif "kesimpulan" in key and "kesimpulan" not in seen:
            output["kesimpulan"] = f"🧠 Kesimpulan: {val}\n"; seen.add("kesimpulan")
    return "\n".join(output[k] for k in ["sinyal", "entry", "tp", "sl", "pola", "kesimpulan"] if k in output) or "⚠️ Gagal membaca analisa."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🕒 1m", callback_data='1min'),
         InlineKeyboardButton("🕒 5m", callback_data='5min'),
         InlineKeyboardButton("🕒 15m", callback_data='15min')],
        [InlineKeyboardButton("⏳ 1h", callback_data='1hour'),
         InlineKeyboardButton("🕰️ 4h", callback_data='4hour'),
         InlineKeyboardButton("📆 1d", callback_data='1day')],
    ]
    await update.message.reply_text("Pilih timeframe untuk analisa BTC/USDT:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    interval = query.data
    await query.edit_message_text(f"📥 Mengambil data BTC/USDT ({interval})...")

    data = fetch_btc_kucoin(interval)
    if not data or len(data) < 10:
        await query.message.reply_text("❌ Data terlalu sedikit.")
        return

    filename = f"chart_{interval}.jpg"
    path = generate_candlestick_chart(data, filename, interval)
    await query.message.reply_photo(photo=open(path, "rb"), caption="📊 Menganalisis chart...")

    result = analyze_image(path)
    formatted = format_reply(result)
    await query.message.reply_text(f"📈 Hasil Analisa BTC/USDT ({interval}):\n\n{formatted}")

    keyboard = [
        [InlineKeyboardButton("🕒 1m", callback_data='1min'),
         InlineKeyboardButton("🕒 5m", callback_data='5min'),
         InlineKeyboardButton("🕒 15m", callback_data='15min')],
        [InlineKeyboardButton("⏳ 1h", callback_data='1hour'),
         InlineKeyboardButton("🕰️ 4h", callback_data='4hour'),
         InlineKeyboardButton("📆 1d", callback_data='1day')],
    ]
    await query.message.reply_text("🔁 Pilih timeframe lain untuk analisa:", reply_markup=InlineKeyboardMarkup(keyboard))

# === MAIN FUNCTION ===
async def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("✅ Bot Telegram aktif...")

if __name__ == "__main__":
    asyncio.run(main())

      
