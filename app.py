import json
import os
import markovify
import requests
import asyncio
import logging
import nest_asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from bs4 import BeautifulSoup
from difflib import get_close_matches

# Terapkan nest_asyncio agar tidak terjadi error event loop
nest_asyncio.apply()

# Logging untuk debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# File tempat menyimpan history chat dan data harga
CHAT_HISTORY_FILE = "chat_history.json"
PRICE_HISTORY_FILE = "price_history.json"

# Fungsi untuk memuat data dari JSON
def load_data(file):
    if not os.path.exists(file):
        return {}
    with open(file, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

# Fungsi untuk menyimpan data ke JSON
def save_data(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# Fungsi untuk menambahkan teks baru ke history chat
def add_to_history(text):
    data = load_data(CHAT_HISTORY_FILE)
    words = text.split()
    for i in range(len(words) - 1):
        key = words[i]
        next_word = words[i + 1]
        if key not in data:
            data[key] = []
        if next_word not in data[key]:
            data[key].append(next_word)
    save_data(CHAT_HISTORY_FILE, data)

# Fungsi untuk memprediksi kata berikutnya
def predict_text(text):
    data = load_data(CHAT_HISTORY_FILE)
    words = text.split()
    last_word = words[-1] if words else ""
    
    predictions = data.get(last_word, [])
    return predictions[:3] if predictions else []

# Fungsi untuk mencari harga melalui scraping
def scrape_price(query):
    try:
        search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}+harga"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(search_url, headers=headers)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            prices = []
            for span in soup.find_all("span"):
                text = span.get_text()
                if "Rp" in text:
                    text = text.replace("Rp", "").replace(".", "").strip()
                    if text.isdigit():
                        prices.append(int(text))

            if len(prices) >= 2:
                return f"Kisaran Harga: Rp {min(prices):,} ~ Rp {max(prices):,}"
            elif prices:
                return f"Perkiraan Harga: Rp {prices[0]:,}"
    
    except Exception as e:
        logger.error(f"Error saat scraping harga: {e}")

    return "Maaf, saya tidak menemukan harga yang relevan."

# Fungsi untuk mencari harga di dataset jika sudah pernah dicari sebelumnya
def check_price_history(query):
    price_data = load_data(PRICE_HISTORY_FILE)

    # Coba cari pertanyaan yang mirip dalam dataset
    closest_matches = get_close_matches(query, price_data.keys(), n=1, cutoff=0.7)

    if closest_matches:
        return price_data[closest_matches[0]]
    return None

# Fungsi untuk menyimpan hasil harga ke dalam dataset
def save_price_history(query, price):
    price_data = load_data(PRICE_HISTORY_FILE)
    price_data[query] = price
    save_data(PRICE_HISTORY_FILE, price_data)

# Fungsi untuk menangani perintah /start
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Halo! Ketik teks untuk mendapatkan prediksi atau tanyakan harga barang.")

# Fungsi untuk menangani pesan teks
async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.lower()

    # Simpan ke history untuk meningkatkan prediksi AI
    add_to_history(text)

    # Jika pertanyaan terkait harga
    if "harga" in text or "berapa harga" in text:
        # Cek apakah harga sudah ada dalam dataset
        existing_price = check_price_history(text)
        if existing_price:
            await update.message.reply_text(f"🔍 Hasil dari database:\n{existing_price}")
            return
        
        # Jika belum ada, lakukan scraping
        await update.message.reply_text("Tunggu sebentar, saya sedang mencarikan data yang cocok untuk Anda...")
        price_info = scrape_price(text)

        # Simpan hasilnya ke dataset
        save_price_history(text, price_info)
        await update.message.reply_text(price_info)
    else:
        # Prediksi teks
        predictions = predict_text(text)
        if predictions:
            response = "Prediksi selanjutnya:\n" + "\n".join(f"{i+1}. {text} {p}" for i, p in enumerate(predictions))
            await update.message.reply_text(response)

# Konfigurasi bot Telegram
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
app = Application.builder().token(TOKEN).build()

# Menambahkan handler
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Fungsi utama untuk menjalankan bot
async def main():
    logger.info("🚀 Memulai bot...")
    await app.bot.delete_webhook()
    await app.run_polling()

# Jalankan bot
if __name__ == "__main__":
    asyncio.run(main())
