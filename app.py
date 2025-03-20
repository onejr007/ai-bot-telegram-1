import json
import os
import markovify
import requests
import uuid
import logging
import asyncio
import nest_asyncio
from bs4 import BeautifulSoup
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import Application, CommandHandler, InlineQueryHandler, CallbackContext

# Terapkan nest_asyncio agar tidak error event loop
nest_asyncio.apply()

# Logging untuk debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# File JSON
CHAT_HISTORY_FILE = "chat_history.json"
PRICE_HISTORY_FILE = "price_history.json"

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

# Fungsi untuk prediksi teks Markov
def predict_markov(text):
    model = train_markov()
    if model:
        try:
            sentence = model.make_sentence_with_start(text, strict=False)
            return sentence if sentence else None
        except markovify.text.ParamError:
            return None
    return None

# Fungsi untuk menambahkan teks ke history
def add_to_history(text):
    data = load_data(CHAT_HISTORY_FILE)
    if text not in data:
        data.append(text)
        save_data(CHAT_HISTORY_FILE, data)

# Fungsi untuk mencari prediksi dari Google Auto Suggest
def predict_google(text):
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={text}"
        response = requests.get(url)
        if response.status_code == 200:
            suggestions = response.json()[1]
            return suggestions[:3] if suggestions else []
    except:
        return []
    return []

# Fungsi untuk scraping harga dari berbagai sumber
def scrape_price(query):
    search_url = f"https://www.google.com/search?q={query}+harga"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(search_url, headers=headers)
    
    if response.status_code != 200:
        return None
    
    soup = BeautifulSoup(response.text, "html.parser")
    prices = []

    for result in soup.find_all("div", class_="BNeawe iBp4i AP7Wnd"):
        price_text = result.text
        if "Rp" in price_text:
            prices.append(price_text)

    return prices[:5] if prices else None

# Fungsi untuk menyimpan jawaban harga di dataset
def save_price_data(question, answer):
    data = load_data(PRICE_HISTORY_FILE)
    data.append({"question": question, "answer": answer})
    save_data(PRICE_HISTORY_FILE, data)

# Fungsi untuk mencari jawaban harga di dataset
def find_price_in_history(question):
    data = load_data(PRICE_HISTORY_FILE)
    for entry in data:
        if question.lower() in entry["question"].lower():
            return entry["answer"]
    return None

# Fungsi untuk menangani perintah `/start`
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Halo! Gunakan inline mode dengan '@NamaBot <kata>' untuk mendapatkan prediksi teks atau bertanya harga.")

# Fungsi untuk menangani inline query (@bot <kata>)
async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    if not query:
        return
    
    # Simpan history agar bot belajar
    add_to_history(query)

    # Cek apakah ini pertanyaan harga
    if "harga" in query.lower():
        cached_answer = find_price_in_history(query)
        if cached_answer:
            answer = cached_answer
        else:
            await update.inline_query.answer([
                InlineQueryResultArticle(
                    id=str(uuid.uuid4()),
                    title="🔍 Mencari harga...",
                    input_message_content=InputTextMessageContent("Tunggu Sebentar, saya sedang mencarikan data yang cocok untuk Anda."),
                )
            ])
            prices = scrape_price(query)
            if prices:
                min_price = min(prices)
                max_price = max(prices)
                answer = f"Kisaran Harga: {min_price} ~ {max_price}"
                save_price_data(query, answer)
            else:
                answer = "Maaf, saya tidak menemukan harga untuk barang ini."

        results = [
            InlineQueryResultArticle(
                id=str(uuid.uuid4()),
                title=f"💰 {answer}",
                input_message_content=InputTextMessageContent(answer),
            )
        ]
        await update.inline_query.answer(results)
        return

    # Prediksi teks dengan berbagai metode
    markov_result = predict_markov(query)
    google_results = predict_google(query)
    
    predictions = []
    if markov_result:
        words = markov_result.split(" ")
        if len(words) > 1:
            predictions.append(f"{query} {words[1]}")
    predictions.extend(google_results)
    
    # Maksimal 3 prediksi unik
    predictions = list(set(predictions))[:3]
    
    # Buat hasil inline query
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=pred,
            input_message_content=InputTextMessageContent(pred),
        )
        for pred in predictions
    ]

    if results:
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
    
    loop = asyncio.get_running_loop()
    await app.run_polling()

# Jalankan bot
if __name__ == "__main__":
    asyncio.run(main())
