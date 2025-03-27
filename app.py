import asyncio
import os
import logging
import uuid
import signal
import json
import random
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext
from telegram.error import BadRequest
import aiohttp
from price_scraper import scrape_price
from utils import load_chat_history, save_chat_history, normalize_price_query, logger

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def get_headers(site):
    referers = {
        "tokopedia": "https://www.tokopedia.com/",
        "lazada": "https://www.lazada.co.id/",
        "blibli": "https://www.blibli.com/",
        "samsung": "https://www.samsung.com/id/",
        "shopee": "https://shopee.co.id/",
        "google": "https://www.google.com/",
        "bing": "https://www.bing.com/"
    }
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referers.get(site, "https://www.google.com/"),
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
    }

async def fetch_google_suggestions(query):
    url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={query}&hl=id"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("google"), timeout=aiohttp.ClientTimeout(total=5)) as response:
                # Ambil teks mentah karena MIME type bukan application/json
                text = await response.text()
                # Parse JSON secara manual
                data = json.loads(text)
                suggestions = data[1][:6] if len(data) > 1 else []
                logger.info(f"ℹ️ Saran dari Google: {suggestions}")
                return suggestions
        except Exception as e:
            logger.error(f"❌ Gagal mengambil saran dari Google: {e}")
            return []

async def fetch_bing_suggestions(query):
    url = f"https://api.bing.com/qsonhs.aspx?type=cb&q={query}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("bing"), timeout=aiohttp.ClientTimeout(total=5)) as response:
                # Ambil teks mentah karena MIME type bukan application/json
                text = await response.text()
                # Parse JSON secara manual
                data = json.loads(text)
                suggestions = [item["q"] for item in data["AS"]["Results"][0]["Suggests"]][:6] if "AS" in data else []
                logger.info(f"ℹ️ Saran dari Bing: {suggestions}")
                return suggestions
        except Exception as e:
            logger.error(f"❌ Gagal mengambil saran dari Bing: {e}")
            return []

async def predict_markov(query):
    try:
        predictions = set()
        chat_history = load_chat_history()
        query_words = query.split()

        # 1. Prioritaskan Redis
        if chat_history:
            logger.info(f"ℹ️ Mengambil prediksi dari Redis untuk '{query}'")
            for entry in chat_history:
                if entry.startswith(query) and entry != query and len(predictions) < 4:
                    predictions.add(entry)

        # 2. Lengkapi dari Google dan Bing jika kurang dari 4
        if len(predictions) < 4:
            logger.info(f"ℹ️ Prediksi dari Redis kurang dari 4, melengkapi dari search engine.")
            google_preds = await fetch_google_suggestions(query)
            bing_preds = await fetch_bing_suggestions(query)
            all_preds = google_preds + bing_preds
            
            # Filter dan tambahkan prediksi yang relevan
            for pred in all_preds:
                if (pred.startswith(query) and 
                    pred != query and 
                    pred not in predictions and 
                    len(pred.split()) > len(query_words) and
                    len(predictions) < 4):
                    predictions.add(pred)
                    save_chat_history(pred)

        # 3. Fallback akhir hanya 2 opsi: second dan baru
        if len(predictions) < 4:
            logger.info(f"ℹ️ Prediksi masih kurang, menambahkan fallback akhir (second/baru).")
            fallback_preds = [
                f"{query} second",
                f"{query} baru"
            ]
            for pred in fallback_preds:
                if pred != query and pred not in predictions and len(predictions) < 4:
                    predictions.add(pred)
                    save_chat_history(pred)

        return list(predictions)[:4]
    except Exception as e:
        logger.error(f"❌ Gagal memprediksi: {e}")
        return [f"{query} second", f"{query} baru"]

def add_to_history(text):
    if len(text.split()) > 1:
        save_chat_history(text)
        logger.info(f"📌 Menambahkan '{text}' ke chat history di Redis")

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Gunakan inline mode '@NamaBot <kata>' untuk prediksi teks atau kirim pertanyaan harga di chat.")

async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    if not query:
        return
    add_to_history(query)
    predictions = await predict_markov(query)
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=pred,
            input_message_content=InputTextMessageContent(pred),
        ) for pred in predictions if pred
    ]
    if results:
        await update.inline_query.answer(results, cache_time=1)

async def animate_search_message(message, stop_event):
    dots = ["🔍 Mencari harga", "🔍 Mencari harga.", "🔍 Mencari harga..", "🔍 Mencari harga..."]
    idx = 0
    start_time = asyncio.get_event_loop().time()
    while not stop_event.is_set():
        try:
            elapsed = asyncio.get_event_loop().time() - start_time
            if 60 <= elapsed < 61:
                await message.reply_text("Mohon tunggu, Bot masih berjalan")
            await message.edit_text(dots[idx % 4])
            idx += 1
            await asyncio.sleep(1)
        except BadRequest as e:
            logger.debug(f"ℹ️ Pesan tidak dimodifikasi atau sudah dihapus: {e}")
            break
        except Exception as e:
            logger.error(f"❌ Error tak terduga di animasi: {e}")
            break

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.strip().lower()
    if is_price_question(text):
        message = await update.message.reply_text("🔍 Mencari harga")
        stop_event = asyncio.Event()
        animation_task = asyncio.create_task(animate_search_message(message, stop_event))
        try:
            normalized_query = normalize_price_query(text)
            add_to_history(f"harga {normalized_query}")
            prices = await asyncio.wait_for(scrape_price(normalized_query), timeout=180)
            stop_event.set()
            await animation_task
            if prices and prices["avg"] != "0":
                answer = f"Kisaran Harga:\nMin: Rp{prices['min']}\nMax: Rp{prices['max']}\nRata-rata: Rp{prices['avg']}"
            else:
                answer = f"❌ Tidak dapat menemukan harga untuk '{normalized_query}'."
            await message.edit_text(answer)
        except asyncio.TimeoutError:
            stop_event.set()
            await animation_task
            await message.edit_text(f"❌ Bot tidak bisa menemukan harga dari barang '{normalized_query}' dalam 3 menit.")
        except Exception as e:
            stop_event.set()
            await animation_task
            await message.edit_text(f"❌ Terjadi kesalahan: {e}")
    else:
        await update.message.reply_text("Ini bukan pertanyaan harga. Fitur lain segera ditambahkan!")

def is_price_question(text):
    price_keywords = ["harga", "berapa harga", "cari harga", "harga terbaru", "diskon", "best price", "murah", "mahal"]
    return any(keyword in text for keyword in price_keywords)

async def run_proxy_scraper_periodically():
    while True:
        try:
            logger.info("🚀 Memulai scraping proxy...")
            from proxy_scraper import scrape_and_store_proxies
            task = asyncio.create_task(scrape_and_store_proxies())
            await task
            logger.info("✅ Proxy scraping selesai untuk iterasi ini")
        except Exception as e:
            logger.error(f"❌ Gagal menjalankan proxy scraper: {e}")
        await asyncio.sleep(5 * 60)

async def shutdown(application):
    logger.info("🛑 Memulai proses shutdown bot...")
    await application.stop()
    await application.shutdown()
    logger.info("✅ Shutdown bot selesai.")

async def main():
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN tidak ditemukan!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    asyncio.create_task(run_proxy_scraper_periodically())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(app)))

    logger.info("🚀 Bot Telegram sedang berjalan...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())