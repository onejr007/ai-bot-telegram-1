import os
import json
import markovify
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, CallbackContext, CommandHandler

# Ambil token dari Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Pastikan token tersedia
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN tidak ditemukan di environment variables!")

# Path ke dataset JSON
DATA_FILE = "chat_history.json"

# Fungsi untuk memuat atau membuat dataset JSON
def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump([], f)
    with open(DATA_FILE, "r") as f:
        return json.load(f)

# Fungsi untuk menyimpan chat ke dataset JSON
def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Fungsi untuk melatih model Markov dari dataset
def train_markov():
    data = load_data()
    text_data = " ".join(data)
    if text_data:
        return markovify.Text(text_data, state_size=2)
    return None

# Fungsi untuk mendapatkan prediksi teks dengan Markov
def predict_markov(text):
    model = train_markov()
    if model:
        sentence = model.make_sentence_with_start(text, strict=False)
        return sentence if sentence else "Saya tidak bisa memprediksi lanjutannya."
    return "Belum ada cukup data untuk prediksi."

# Fungsi untuk scraping Google Search untuk prediksi
def predict_google(query):
    headers = {"User-Agent": "Mozilla/5.0"}
    search_url = f"https://www.google.com/search?q={query}"
    response = requests.get(search_url, headers=headers)

    if response.status_code == 200:
        soup = BeautifulSoup(response.text, "html.parser")
        results = soup.find_all("h3")
        if results:
            return results[0].text
    return "Tidak ada hasil dari Google."

# Fungsi utama untuk menangani pesan
async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text
    chat_id = update.message.chat_id

    # Simpan chat ke JSON untuk training Markov
    data = load_data()
    data.append(text)
    save_data(data)

    # Jika pesan diawali "Coba ", prediksi teks
    if text.lower().startswith("coba "):
        input_text = text[5:].strip()
        markov_result = predict_markov(input_text)
        google_result = predict_google(input_text)

        reply = f"**Prediksi Markov:** {markov_result}\n**Prediksi Google:** {google_result}"
        await update.message.reply_text(reply)
    else:
        await update.message.reply_text("Ketik 'Coba <kata>' untuk mendapatkan prediksi teks.")

# Fungsi untuk perintah `/start`
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Halo! Ketik 'Coba <kata>' untuk mendapatkan prediksi teks.")

# Konfigurasi bot Telegram
app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# Tambahkan handler untuk pesan dan perintah
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Jalankan bot
if __name__ == "__main__":
    print("Bot Telegram sedang berjalan...")
    app.run_polling()
