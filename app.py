import asyncio
import os
import logging
import uuid
import signal
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext
from telegram.error import BadRequest
import markovify
import aiohttp
from price_scraper import scrape_price
from utils import load_chat_history, save_chat_history, normalize_price_query, logger

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

async def fetch_google_suggestions(query):
    url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={query}&hl=id"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                data = await response.json()
                return data[1][:4] if len(data) > 1 else []  # Maksimal 4 saran dalam bahasa Indonesia
        except Exception as e:
            logger.error(f"❌ Gagal mengambil saran dari Google: {e}")
            return []

def train_markov(query):
    chat_history = load_chat_history()
    if not chat_history:
        logger.info("ℹ️ Chat history kosong, menggunakan fallback Google.")
        suggestions = asyncio.run(fetch_google_suggestions(query))
        text_data = "\n".join(suggestions) if suggestions else "Cek harga barang secara online."
    else:
        text_data = "\n".join(chat_history)
    return markovify.Text(text_data, state_size=1)

def predict_markov(query):
    try:
        model = train_markov(query)
        predictions = set()
        # Prediksi dari Markov
        for _ in range(10):  # Coba lebih banyak untuk variasi
            sentence = model.make_short_sentence(140, tries=10)
            if sentence and f"{query} {sentence}" not in predictions:
                predictions.add(f"{query} {sentence}")
                if len(predictions) == 4:
                    break
        
        # Jika kurang dari 4, tambahkan dari Google
        if len(predictions) < 4:
            google_preds = asyncio.run(fetch_google_suggestions(query))
            for pred in google_preds:
                if pred not in predictions and len(predictions) < 4:
                    predictions.add(pred)
                    save_chat_history(pred)  # Simpan ke Redis
        
        return list(predictions)[:4]
    except Exception as e:
        logger.error(f"❌ Gagal memprediksi Markov: {e}")
        return [f"{query} barang"]

def add_to_history(text):
    if len(text.split()) > 1:  # Simpan hanya kalimat lengkap
        save_chat_history(text)

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Gunakan inline mode '@NamaBot <kata>' untuk prediksi teks atau kirim pertanyaan harga di chat.")

async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    if not query:
        return
    add_to_history(query)
    predictions = predict_markov(query)
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