# app.py
from multiprocessing import Process
import proxy_scraper
import asyncio
import os
import logging
import uuid
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext
from telegram.error import BadRequest
import markovify
from price_scraper import scrape_price
from utils import load_chat_history, save_chat_history, normalize_price_query, logger

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def train_markov():
    try:
        with open("history_chat.txt", "r", encoding="utf-8") as f:
            text_data = f.read().strip()
        if not text_data or len(text_data.split()) < 5:
            text_data = "Selamat datang di bot prediksi teks. Silakan ketik sesuatu."
        return markovify.Text(text_data, state_size=2)
    except FileNotFoundError:
        text_data = "Selamat datang di bot prediksi teks. Silakan ketik sesuatu."
        return markovify.Text(text_data, state_size=2)

def predict_markov(query):
    try:
        model = train_markov()
        prediction = model.make_sentence(tries=10)
        return prediction or ""
    except Exception as e:
        logger.error(f"❌ Gagal memprediksi Markov: {e}")
        return ""

def add_to_history(text):
    save_chat_history(text)

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Gunakan inline mode dengan '@NamaBot <kata>' untuk prediksi teks atau kirim pertanyaan harga dalam chat.")

async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    if not query:
        return
    add_to_history(query)
    markov_result = predict_markov(query)
    predictions = [f"{query} {markov_result.split()[1]}" if len(markov_result.split()) > 1 else ""]
    predictions = list(set(predictions))[:3]
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
    dots = ["🔍 Mencari harga.", "🔍 Mencari harga..", "🔍 Mencari harga..."]
    current_text = None
    idx = 0
    while not stop_event.is_set():
        new_text = dots[idx % 3]
        if new_text != current_text:
            try:
                await message.edit_text(new_text)
                current_text = new_text
            except BadRequest as e:
                if "Message is not modified" not in str(e):
                    logger.error(f"❌ Error saat mengedit pesan: {e}")
            except Exception as e:
                logger.error(f"❌ Error tak terduga: {e}")
        idx += 1
        await asyncio.sleep(0.5)

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.strip().lower()
    if is_price_question(text):
        message = await update.message.reply_text("🔍 Mencari harga.")
        stop_event = asyncio.Event()
        animation_task = asyncio.create_task(animate_search_message(message, stop_event))
        try:
            normalized_query = normalize_price_query(text)
            prices = await scrape_price(normalized_query)
            stop_event.set()
            await animation_task
            if prices:
                answer = f"Kisaran Harga: {min(prices)} - {max(prices)}"
            else:
                answer = "❌ Tidak dapat menemukan harga untuk produk tersebut."
            await message.edit_text(answer)
        except Exception as e:
            stop_event.set()
            await animation_task
            await message.edit_text(f"❌ Terjadi kesalahan: {e}")
    else:
        await update.message.reply_text("Ini bukan pertanyaan harga. Fitur lain segera ditambahkan!")

def is_price_question(text):
    price_keywords = ["harga", "berapa harga", "cari harga", "harga terbaru", "diskon", "best price", "murah", "mahal"]
    return any(keyword in text for keyword in price_keywords)

def main():
    if not TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN tidak ditemukan!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    proxy_process = Process(target=proxy_scraper.main)
    try:
        proxy_process.start()
        logger.info("🚀 Bot Telegram dan Proxy Scraper sedang berjalan...")
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ Gagal menjalankan bot atau proxy scraper: {e}")
    finally:
        if proxy_process.is_alive():
            proxy_process.terminate()
            logger.info("🛑 Proxy Scraper dihentikan.")

if __name__ == "__main__":
    main()