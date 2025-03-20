import json
import os
import markovify
import requests
import uuid
import logging
import asyncio
import nest_asyncio
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext
from telegram import InlineQueryResultArticle, InputTextMessageContent

nest_asyncio.apply()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHAT_HISTORY_FILE = "chat_history.json"
PRICE_HISTORY_FILE = "price_history.json"

def load_data(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError:
            return []

def save_data(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)

def train_markov():
    data = load_data(CHAT_HISTORY_FILE)
    if len(data) < 3:
        return None
    text_data = " ".join(data)
    return markovify.Text(text_data, state_size=2)

def predict_markov(text):
    model = train_markov()
    if model:
        try:
            sentence = model.make_sentence_with_start(text, strict=False)
            return sentence if sentence else None
        except markovify.text.ParamError:
            return None
    return None

def add_to_history(text):
    data = load_data(CHAT_HISTORY_FILE)
    if text not in data:
        data.append(text)
        save_data(CHAT_HISTORY_FILE, data)

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

def save_price_data(question, answer):
    data = load_data(PRICE_HISTORY_FILE)
    data.append({"question": question, "answer": answer})
    save_data(PRICE_HISTORY_FILE, data)

def find_price_in_history(question):
    data = load_data(PRICE_HISTORY_FILE)
    for entry in data:
        if question.lower() in entry["question"].lower():
            return entry["answer"]
    return None

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Gunakan inline mode dengan '@NamaBot <kata>' untuk prediksi teks atau kirim pertanyaan harga dalam chat.")

async def inline_query(update: Update, context: CallbackContext):
    query = update.inline_query.query.strip()
    if not query:
        return

    add_to_history(query)

    markov_result = predict_markov(query)
    google_results = predict_google(query)
    
    predictions = []
    if markov_result:
        words = markov_result.split(" ")
        if len(words) > 1:
            predictions.append(f"{query} {words[1]}")
    predictions.extend(google_results)

    predictions = list(set(predictions))[:3]

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

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.strip().lower()

    # Jika ini pertanyaan harga dalam chat (bukan inline query)
    if text.startswith("harga "):
        await update.message.reply_text("🔍 Mencari harga...")

        cached_answer = find_price_in_history(text)
        if cached_answer:
            answer = cached_answer
        else:
            prices = scrape_price(text)
            if prices:
                min_price = min(prices)
                max_price = max(prices)
                answer = f"Kisaran Harga: {min_price} ~ {max_price}"
                save_price_data(text, answer)
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
