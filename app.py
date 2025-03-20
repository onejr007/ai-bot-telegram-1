import json
import os
import markovify
import requests
import uuid
import logging
import asyncio
import nest_asyncio
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, InlineQueryHandler, CallbackContext

# Terapkan nest_asyncio agar tidak terjadi error event loop
nest_asyncio.apply()

# Logging untuk debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# File tempat menyimpan history chat
CHAT_HISTORY_FILE = "chat_history.json"

# Fungsi untuk memuat data dari JSON
def load_data():
    if not os.path.exists(CHAT_HISTORY_FILE):
        return []
    with open(CHAT_HISTORY_FILE, "r", encoding="utf-8") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError:
            return []

# Fungsi untuk menyimpan data ke JSON
def save_data(data):
    with open(CHAT_HISTORY_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

# Fungsi untuk melatih model Markov
def train_markov():
    data = load_data()
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
            return sentence if sentence else "Saya belum bisa memprediksi lanjutannya."
        except markovify.text.ParamError:
            return "Belum ada cukup data untuk prediksi."
    return "Belum ada cukup data untuk prediksi."

# Fungsi untuk menambahkan teks baru ke history chat
def add_to_history(text):
    data = load_data()
    if text not in data:
        data.append(text)
        save_data(data)

# Fungsi untuk mencari prediksi dari Google Search (Scraping)
def predict_google(text):
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={text}"
        response = requests.get(url)
        if response.status_code == 200:
            suggestions = response.json()[1]
            return suggestions[:3] if suggestions else ["Tidak ada prediksi."]
    except:
        return ["Gagal mengambil prediksi dari Google."]
    return ["Tidak ada prediksi."]

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
    markov_result = predict_markov(query)

    # Prediksi menggunakan Google
    google_results = predict_google(query)
    google_text = "\n".join(google_results)

    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"Prediksi Markov: {markov_result}",
            input_message_content=InputTextMessageContent(markov_result),
        ),
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=f"Prediksi Google: {google_results[0] if google_results else 'Tidak ada prediksi'}",
            input_message_content=InputTextMessageContent(google_results[0] if google_results else "Tidak ada prediksi"),
        ),
    ]

    await update.inline_query.answer(results)

# Konfigurasi bot Telegram
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

app = Application.builder().token(TOKEN).build()

# Menambahkan handler
app.add_handler(CommandHandler("start", start))
app.add_handler(InlineQueryHandler(inline_query))

# Fungsi utama untuk menjalankan bot
async def main():
    logger.info("🚀 Menghapus webhook sebelum memulai polling...")
    await app.bot.delete_webhook()
    logger.info("✅ Webhook dihapus, memulai polling...")
    
    await app.run_polling()

# Jalankan bot dengan event loop yang sudah berjalan
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
