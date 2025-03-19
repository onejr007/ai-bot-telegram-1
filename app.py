import os
import json
import markovify
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext

# Token bot Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# File JSON untuk menyimpan history chat
HISTORY_FILE = "chat_history.json"

# Load atau buat file history JSON
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        history_data = json.load(f)
else:
    history_data = {"texts": []}

def save_to_history(new_text):
    """Simpan input user ke history JSON."""
    history_data["texts"].append(new_text)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history_data, f)

def load_markov_model():
    """Muat ulang model Markov dari history chat."""
    if history_data["texts"]:
        all_text = " ".join(history_data["texts"])
        return markovify.Text(all_text, state_size=2)
    return None

def generate_markov_prediction(seed_text):
    """Coba prediksi teks berdasarkan Markov Chain jika ada data."""
    markov_model = load_markov_model()
    if markov_model:
        try:
            sentence = markov_model.make_sentence_with_start(seed_text, strict=False)
            if sentence:
                return sentence[len(seed_text):]  # Ambil kata setelah seed_text
        except:
            return None
    return None

def scrape_google_prediction(seed_text):
    """Scraping Google Search untuk mencari prediksi teks berikutnya."""
    try:
        search_url = f"https://www.google.com/search?q={seed_text}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(search_url, headers=headers)
        soup = BeautifulSoup(response.text, "html.parser")

        # Ambil hasil dari Google Suggestion
        suggestions = [s.text for s in soup.find_all("span") if seed_text.lower() in s.text.lower()]
        
        if suggestions:
            return suggestions[0].replace(seed_text, "").strip()  # Ambil hasil pertama
    except:
        return None
    return None

def handle_message(update: Update, context: CallbackContext):
    """Menangani pesan user dan memberikan prediksi dari berbagai mode."""
    user_input = update.message.text.strip()
    
    if user_input.endswith(" "):  # Jika user mengetik lalu menekan spasi
        seed_text = user_input.strip()

        # Coba prediksi dengan Markov Chain
        prediction = generate_markov_prediction(seed_text)

        # Jika Markov gagal, coba scraping Google
        if not prediction:
            prediction = scrape_google_prediction(seed_text)

        # Jika masih gagal, gunakan fallback teks
        if not prediction:
            prediction = "tidak yakin, coba lanjutkan sendiri!"

        response = seed_text + prediction
        update.message.reply_text(response)

        # Simpan history chat agar bot bisa belajar
        save_to_history(seed_text)

def main():
    """Menjalankan bot Telegram."""
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Handler untuk menerima pesan teks
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    # Mulai bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
