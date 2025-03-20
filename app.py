import json
import os
import markovify
import requests
import uuid
import logging
import asyncio
import re
import nest_asyncio
from bs4 import BeautifulSoup
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, InlineQueryHandler, CallbackContext

# Terapkan nest_asyncio agar tidak terjadi error event loop
nest_asyncio.apply()

# Logging untuk debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# File tempat menyimpan history chat dan harga
CHAT_HISTORY_FILE = "chat_history.json"
PRICE_DATA_FILE = "price_data.json"

# Fungsi untuk memuat data dari JSON
def load_data(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError:
            return []

# Fungsi untuk menyimpan data ke JSON
def save_data(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

# Fungsi untuk melatih model Markov
def train_markov():
    data = load_data(CHAT_HISTORY_FILE)
    if len(data) < 3:
        return None  # Jangan buat model jika dataset terlalu kecil
    text_data = " ".join(data)
    return markovify.Text(text_data, state_size=2)

# Fungsi untuk memprediksi kata berikutnya menggunakan Markov
def predict_markov(text):
    model = train_markov()
    if model:
        words = text.split()
        if len(words) < 1:
            return []
        next_words = []
        for _ in range(10):  # Coba beberapa kali untuk mendapatkan prediksi unik
            sentence = model.make_sentence_with_start(words[-1], strict=False)
            if sentence:
                tokens = sentence.split()
                if len(tokens) > 1:
                    next_word = tokens[1]  # Ambil kata kedua
                    if next_word not in next_words:
                        next_words.append(next_word)
            if len(next_words) >= 3:
                break
        return next_words
    return []

# Fungsi untuk menambahkan teks baru ke history chat
def add_to_history(text):
    data = load_data(CHAT_HISTORY_FILE)
    if text not in data:
        data.append(text)
        save_data(CHAT_HISTORY_FILE, data)

# Fungsi untuk mencari prediksi dari Google Search (Scraping)
def predict_google(text):
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={text}"
        response = requests.get(url)
        if response.status_code == 200:
            suggestions = response.json()[1]
            next_words = []
            for suggestion in suggestions:
                tokens = suggestion.split()
                if len(tokens) > 1:
                    next_word = tokens[1]  # Ambil kata kedua
                    if next_word not in next_words:
                        next_words.append(next_word)
                if len(next_words) >= 3:
                    break
            return next_words
    except:
        return []
    return []

# Fungsi untuk scraping harga dari berbagai situs
def scrape_price(query):
    search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}+harga"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(search_url, headers=headers)

    if response.status_code != 200:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    prices = []
    
    # Cari harga dalam hasil pencarian
    for span in soup.find_all("span"):
        match = re.search(r"Rp\s?([\d,.]+)", span.text)
        if match:
            price = match.group(1).replace(".", "").replace(",", "")
            prices.append(int(price))

    if len(prices) >= 2:
        return f"Kisaran Harga: Rp {min(prices):,} ~ Rp {max(prices):,}".replace(",", ".")
    elif prices:
        return f"Perkiraan Harga: Rp {prices[0]:,}".replace(",", ".")
    
    return "Tidak ditemukan harga yang valid."

# Fungsi untuk menangani pertanyaan harga
async def handle_price(update: Update, query: str):
    await update.message.reply_text("🔍 Tunggu Sebentar, saya sedang mencarikan data yang cocok untuk Anda...")

    # Cek apakah harga sudah ada dalam database JSON
    price_data = load_data(PRICE_DATA_FILE)
    for entry in price_data:
        if entry["question"] == query:
            await update.message.reply_text(entry["answer"])
            return

    # Jika belum ada, lakukan scraping
    price_result = scrape_price(query)
    if price_result:
        # Simpan hasil ke JSON untuk pertanyaan serupa di masa depan
        price_data.append({"question": query, "answer": price_result})
        save_data(PRICE_DATA_FILE, price_data)
        await update.message.reply_text(price_result)
    else:
        await update.message.reply_text("Maaf, saya tidak menemukan harga untuk permintaan Anda.")

# Fungsi untuk menangani perintah `/start`
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Halo! Gunakan inline mode dengan '@NamaBot <kata>' untuk mendapatkan prediksi teks.")

# Fungsi untuk menangani inline query (@bot <kata>)
async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query
    if not query:
        return

    # Simpan ke history agar AI belajar
    add_to_history(query)

    # Prediksi menggunakan Markov
    markov_results = predict_markov(query)

    # Prediksi menggunakan Google
    google_results = predict_google(query)

    # Gabungkan hasil dari kedua metode
    combined_results = list(set(markov_results + google_results))[:3]

    # Jika tidak ada prediksi yang ditemukan
    if not combined_results:
        combined_results = ["Tidak ada prediksi yang tersedia."]

    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=prediction,
            input_message_content=InputTextMessageContent(prediction),
        )
        for prediction in combined_results
    ]

    await update.inline_query.answer(results)

# Fungsi untuk menangani pesan teks
async def handle_message(update: Update, context: CallbackContext):
    user_text = update.message.text.lower()

    # Cek apakah pertanyaan berhubungan dengan harga
    if "harga" in user_text or "berapa" in user_text:
        await handle_price(update, user_text)
    else:
        await update.message.reply_text("Saya hanya bisa memprediksi teks dan mencari harga.")

# Konfigurasi bot Telegram
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = f"https://{os.getenv('RAILWAY_STATIC_URL')}/webhook"

app = Application.builder().token(TOKEN).build()

# Menambahkan handler
app.add_handler(CommandHandler("start", start))
app.add_handler(InlineQueryHandler(inline_query))
app.add_handler(CommandHandler("harga", handle_price))
app.add_handler(CommandHandler("cari", handle_price))
app.add_handler(CommandHandler("search", handle_price))
app.add_handler(CommandHandler("price", handle_price))
app.add_handler(CommandHandler("getprice", handle_price))
app.add_handler(CommandHandler("getharga", handle_price))
app.add_handler(CommandHandler("lookup", handle_price))
app.add_handler(CommandHandler("findprice", handle_price))
app.add_handler(CommandHandler("pricecheck", handle_price))
app.add_handler(CommandHandler("checkprice", handle_price))

# Fungsi utama untuk menjalankan bot
async def main():
    logger.info("🚀 Menghapus webhook sebelum memulai polling...")
    await app.bot.delete_webhook()
    logger.info("✅ Webhook dihapus, memulai polling...")

    loop = asyncio.get_running_loop()
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
