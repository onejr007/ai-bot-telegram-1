import json
import os
import markovify
import requests
import uuid
import logging
import asyncio
import nest_asyncio
import re

from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext
from telegram import InlineQueryResultArticle, InputTextMessageContent

nest_asyncio.apply()

# Konfigurasi logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CHAT_HISTORY_FILE = "chat_history.json"
PRICE_HISTORY_FILE = "price_history.json"

def load_data(filename):
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        logger.error(f"❌ Gagal membaca {filename}, format JSON mungkin rusak.")
        return []

def save_data(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

def train_markov():
    data = load_data(CHAT_HISTORY_FILE)
    if len(data) < 3:
        logger.warning("⚠️ Data chat terlalu sedikit untuk melatih Markov.")
        return None
    text_data = " ".join(data)
    return markovify.Text(text_data, state_size=2)

def predict_markov(text):
    model = train_markov()
    if model:
        try:
            sentence = model.make_sentence_with_start(text, strict=False)
            if sentence:
                return sentence
        except markovify.text.ParamError:
            logger.warning(f"⚠️ Markov gagal membuat prediksi untuk: {text}")
    return None

def add_to_history(text):
    data = load_data(CHAT_HISTORY_FILE)
    if text not in data:
        data.append(text)
        save_data(CHAT_HISTORY_FILE, data)
        logger.info(f"📌 Menambahkan '{text}' ke history chat.")

def predict_google(text):
    try:
        url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={text}"
        response = requests.get(url)
        if response.status_code == 200:
            suggestions = response.json()[1]
            logger.info(f"🔍 Prediksi Google untuk '{text}': {suggestions}")
            return suggestions[:3] if suggestions else []
    except Exception as e:
        logger.error(f"❌ Error saat mengambil prediksi dari Google: {e}")
    return []

def scrape_price(query):
    search_url = f"https://www.google.com/search?q={query}+harga"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(search_url, headers=headers)

    if response.status_code != 200:
        logger.error(f"❌ Gagal mengambil data harga untuk '{query}'")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    prices = []

    for result in soup.find_all("div", class_="BNeawe iBp4i AP7Wnd"):
        price_text = result.text
        if "Rp" in price_text:
            prices.append(price_text)

    logger.info(f"📊 Harga ditemukan untuk '{query}': {prices}")
    return prices[:5] if prices else None

def save_price_data(question, answer):
    data = load_data(PRICE_HISTORY_FILE)
    data.append({"question": question, "answer": answer})
    save_data(PRICE_HISTORY_FILE, data)
    logger.info(f"💾 Menyimpan harga: {question} -> {answer}")

def find_price_in_history(question):
    data = load_data(PRICE_HISTORY_FILE)
    for entry in data:
        if question.lower() in entry["question"].lower():
            logger.info(f"🔄 Menggunakan harga dari history untuk '{question}'")
            return entry["answer"]
    return None

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Gunakan inline mode dengan '@NamaBot <kata>' untuk prediksi teks atau kirim pertanyaan harga dalam chat.")

def get_similar_from_history(text, max_results=3):
    """
    Mencari teks yang mirip dalam history chat jika Markov dan Google gagal.
    """
    data = load_data(CHAT_HISTORY_FILE)
    similar = [item for item in data if text in item and item != text]
    
    if similar:
        logger.info(f"🔄 Menggunakan teks yang mirip dari history chat untuk '{text}': {similar[:max_results]}")
    
    return similar[:max_results]

async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    if not query:
        return

    add_to_history(query)

    # Prediksi menggunakan berbagai metode
    markov_result = predict_markov(query)
    google_results = predict_google(query)
    
    predictions = []

    # Jika Markov berhasil, gunakan hasilnya
    if markov_result:
        words = markov_result.split(" ")
        if len(words) > 1:
            predictions.append(f"{query} {words[1]}")
    
    # Jika Google memberikan hasil, tambahkan ke prediksi
    predictions.extend(google_results)

    # Jika Markov & Google gagal, coba cari di history chat
    if not predictions:
        predictions.extend(get_similar_from_history(query))

    # Jika masih kosong, coba cari prediksi dari kata pertama query
    if not predictions:
        first_word = query.split(" ")[0]
        google_fallback = predict_google(first_word)
        predictions.extend(google_fallback[:2])

    # Hapus duplikat & ambil maksimal 3 prediksi
    predictions = list(set(predictions))[:3]

    logger.info(f"📌 Prediksi akhir untuk '{query}': {predictions}")

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

def normalize_price_question(text):
    """
    Menyederhanakan pertanyaan harga agar formatnya sama
    """
    text = text.lower()
    
    # Hilangkan kata-kata tanya yang tidak mempengaruhi pencarian
    text = re.sub(r"\b(coba carikan|berapa|tolong cari|tolong carikan|mohon carikan)\b", "", text).strip()
    
    # Pastikan format baku menggunakan "harga <produk>"
    if not text.startswith("harga "):
        text = "harga " + text

    return text

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.strip().lower()

    # Jika ini pertanyaan harga dalam chat (bukan inline query)
    if text.startswith("harga ") or any(x in text for x in ["berapa harga", "coba carikan harga"]):
        await update.message.reply_text("🔍 Mencari harga...")

        # Normalisasi pertanyaan
        normalized_question = normalize_price_question(text)

        # Cek apakah harga sudah pernah dicari sebelumnya
        cached_answer = find_price_in_history(normalized_question)
        if cached_answer:
            answer = cached_answer
        else:
            prices = scrape_price(normalized_question)
            if prices:
                min_price = min(prices)
                max_price = max(prices)
                answer = f"Kisaran Harga: {min_price} ~ {max_price}"
                save_price_data(normalized_question, answer)  # Simpan dengan pertanyaan yang sudah dinormalisasi
            else:
                answer = "Maaf, saya tidak menemukan harga untuk barang ini."

        await update.message.reply_text(answer)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(InlineQueryHandler(inline_query))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

async def main():
    logger.info("🚀 Menghapus webhook sebelum memulai polling...")
    await app.bot.delete_webhook()
    logger.info("✅ Webhook dihapus, memulai polling...")
    
    loop = asyncio.get_running_loop()
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
