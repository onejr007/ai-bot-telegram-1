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

# Fungsi untuk memprediksi teks menggunakan Markov (Pastikan menghasilkan 2 kata)
def predict_markov(text):
    model = train_markov()
    if model:
        try:
            sentence = model.make_sentence_with_start(text, strict=False)
            if sentence:
                words = sentence.split()
                if len(words) > 1:
                    return f"{text} {words[1]}"
        except markovify.text.ParamError:
            return None
    return None

# Fungsi untuk mencari prediksi dari Google Search (Scraping)
def predict_google(text):
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={text}"
        response = requests.get(url)
        if response.status_code == 200:
            suggestions = response.json()[1]
            results = [s for s in suggestions if len(s.split()) > 1]  # Pastikan minimal 2 kata
            return results[:3]
    except:
        return []
    return []

# Fungsi untuk prediksi sederhana dari history chat
def predict_from_history(text):
    history = load_data()
    results = []
    for sentence in history:
        words = sentence.split()
        if len(words) > 1 and words[0] == text:
            results.append(f"{text} {words[1]}")
    return results[:3]

# Fungsi untuk menambahkan teks baru ke history chat
def add_to_history(text):
    data = load_data()
    if text not in data:
        data.append(text)
        save_data(data)

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

    # Menggabungkan prediksi dari berbagai metode
    predictions = set()

    # Tambahkan prediksi dari Markov
    markov_prediction = predict_markov(query)
    if markov_prediction:
        predictions.add(markov_prediction)

    # Tambahkan prediksi dari Google
    google_predictions = predict_google(query)
    predictions.update(google_predictions)

    # Tambahkan prediksi dari history chat
    history_predictions = predict_from_history(query)
    predictions.update(history_predictions)

    # Pastikan setiap hasil minimal memiliki 2 kata
    final_predictions = [pred for pred in predictions if len(pred.split()) > 1][:3]

    # Jika tidak ada hasil prediksi, berikan fallback
    if not final_predictions:
        final_predictions = ["Tidak ada prediksi tersedia."]

    # Buat hasil inline query
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=pred,
            input_message_content=InputTextMessageContent(pred),
        )
        for pred in final_predictions
    ]

    await update.inline_query.answer(results)

# Konfigurasi bot Telegram
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = f"https://{os.getenv('RAILWAY_STATIC_URL')}/webhook"

app = Application.builder().token(TOKEN).build()

# Menambahkan handler
app.add_handler(CommandHandler("start", start))
app.add_handler(InlineQueryHandler(inline_query))

# Fungsi utama untuk menjalankan bot
async def main():
    logger.info("🚀 Menghapus webhook sebelum memulai polling...")
    await app.bot.delete_webhook()
    logger.info("✅ Webhook dihapus, memulai polling...")
    
    # Pastikan menggunakan event loop yang sudah berjalan
    loop = asyncio.get_running_loop()
    await app.run_polling()

# Jalankan bot
if __name__ == "__main__":
    asyncio.run(main())
