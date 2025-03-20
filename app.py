import json
import os
import markovify
import requests
import uuid
import logging
import asyncio
import re
from bs4 import BeautifulSoup
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, InlineQueryHandler, MessageHandler, filters, CallbackContext

# Logging untuk debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# File tempat menyimpan history chat dan data harga
CHAT_HISTORY_FILE = "chat_history.json"
DATA_HARGA_FILE = "harga_data.json"

# Fungsi untuk memuat data dari JSON
def load_data(file_path):
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError:
            return []

# Fungsi untuk menyimpan data ke JSON
def save_data(file_path, data):
    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

# Fungsi untuk melatih model Markov
def train_markov():
    data = load_data(CHAT_HISTORY_FILE)
    if len(data) < 3:
        return None  # Jangan buat model jika dataset terlalu kecil
    text_data = " ".join(data)
    return markovify.Text(text_data, state_size=2)

# Fungsi untuk memprediksi teks menggunakan Markov
def predict_markov(text):
    model = train_markov()
    if model:
        try:
            sentence = model.make_sentence_with_start(text, strict=False)
            if sentence:
                return sentence.split()[:2]  # Ambil 2 kata pertama
        except markovify.text.ParamError:
            pass
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
            return [s.split()[:2] for s in suggestions][:3]  # Ambil 2 kata pertama
    except:
        return []
    return []

# Fungsi untuk mencari harga barang dari e-commerce
def scrape_harga(query):
    headers = {"User-Agent": "Mozilla/5.0"}
    search_url = f"https://www.google.com/search?q=harga+{query}"
    
    try:
        response = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(response.text, "html.parser")
        prices = []

        # Cari elemen harga dalam hasil pencarian Google
        for price_tag in soup.find_all(string=re.compile(r"Rp\s?\d[\d\.\,]+")):
            price = re.sub(r"[^\d]", "", price_tag)  # Bersihkan format harga
            prices.append(int(price))

        if prices:
            return min(prices), max(prices)  # Rentang harga termurah - termahal
    except:
        return None, None
    return None, None

# Fungsi untuk mencari harga barang berdasarkan pertanyaan user
async def handle_harga(update: Update, context: CallbackContext):
    query = update.message.text.lower()
    
    # Cek apakah pertanyaan sudah ada di dataset harga
    harga_data = load_data(DATA_HARGA_FILE)
    for item in harga_data:
        if query in item["pertanyaan"]:
            await update.message.reply_text(f"Kisaran Harga: Rp {item['harga_min']} - Rp {item['harga_max']}")
            return

    # Beri tahu user bahwa pencarian sedang dilakukan
    await update.message.reply_text("Tunggu sebentar, saya sedang mencarikan data yang cocok untuk Anda...")

    # Scraping harga
    harga_min, harga_max = scrape_harga(query)

    if harga_min and harga_max:
        # Simpan ke dataset harga
        harga_data.append({"pertanyaan": query, "harga_min": harga_min, "harga_max": harga_max})
        save_data(DATA_HARGA_FILE, harga_data)

        await update.message.reply_text(f"Kisaran Harga: Rp {harga_min} - Rp {harga_max}")
    else:
        await update.message.reply_text("Maaf, saya tidak menemukan informasi harga yang sesuai.")

# Fungsi untuk menangani inline query (@bot <kata>)
async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    if not query:
        return

    add_to_history(query)

    # Prediksi teks menggunakan berbagai metode
    markov_results = predict_markov(query)
    google_results = predict_google(query)

    # Gabungkan semua hasil prediksi (tanpa label metode)
    combined_results = list(set(markov_results + google_results))[:3]  # Maksimal 3 prediksi unik

    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"{' '.join(pred)}",
            input_message_content=InputTextMessageContent(' '.join(pred)),
        ) for pred in combined_results if pred
    ]

    if results:
        await update.inline_query.answer(results)

# Fungsi untuk menangani perintah `/start`
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Halo! Kirimkan pertanyaan harga atau gunakan inline mode dengan '@NamaBot <kata>' untuk mendapatkan prediksi teks.")

# Konfigurasi bot Telegram
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
app = Application.builder().token(TOKEN).build()

# Menambahkan handler
app.add_handler(CommandHandler("start", start))
app.add_handler(InlineQueryHandler(inline_query))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_harga))  # Menjawab harga dari chat biasa

# Fungsi utama untuk menjalankan bot
async def main():
    logger.info("🚀 Menghapus webhook sebelum memulai polling...")
    await app.bot.delete_webhook()
    logger.info("✅ Webhook dihapus, memulai polling...")

    await app.run_polling()

# Jalankan bot
if __name__ == "__main__":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Jika tidak ada event loop, jalankan `asyncio.run()`
        asyncio.run(main())
    else:
        # Jika event loop sudah berjalan (Railway), jalankan `main()` langsung
        asyncio.create_task(main())
