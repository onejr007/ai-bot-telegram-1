import json
import os
import markovify
import requests
import uuid
import logging
import asyncio
import nest_asyncio
import re
import random

from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, InlineQueryHandler, filters, CallbackContext
from telegram import InlineQueryResultArticle, InputTextMessageContent

nest_asyncio.apply()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
]

def get_headers():
    return {"User-Agent": random.choice(USER_AGENTS)}

HEADERS = get_headers()

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
        
        response = requests.get(url, headers=HEADERS)
        if response.status_code == 200:
            suggestions = response.json()[1]
            if suggestions:
                return suggestions[:3]  # Ambil maksimal 3 hasil
            
            # Jika tidak ada hasil, coba dengan bagian terakhir query
            short_text = " ".join(text.split()[-2:])
            logger.info(f"🔄 Coba query lebih pendek: {short_text}")
            
            response = requests.get(f"https://suggestqueries.google.com/complete/search?client=firefox&q={short_text}", headers=HEADERS)
            if response.status_code == 200:
                suggestions = response.json()[1]
                return suggestions[:3] if suggestions else []

    except Exception as e:
        logger.error(f"❌ Gagal mengambil prediksi Google: {e}")
    
    return []

def extract_prices(text):
    """Mengambil harga dari teks dengan format Rp."""
    return re.findall(r"Rp ?[\d.,]+", text)

def scrape_google_price(query):
    """Scraping harga dari Google Search dengan XPath yang lebih akurat."""
    search_url = f"https://www.google.com/search?q={query}+harga"
    response = requests.get(search_url, headers=HEADERS)

    if response.status_code != 200:
        logger.error(f"❌ Gagal mengambil data harga dari Google untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()

    # Ambil harga dari elemen hasil pencarian
    for result in soup.select("span[jsname='vWLAgc']"):
        prices.update(extract_prices(result.get_text()))

    return list(prices)[:5]

def scrape_tokopedia_price(query):
    """Scraping harga dari Tokopedia"""
    search_url = f"https://www.tokopedia.com/search?st=product&q={query}"
    response = requests.get(search_url, headers=HEADERS)

    if response.status_code != 200:
        logger.error(f"❌ Gagal mengambil data harga dari Tokopedia untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()

    for result in soup.select("div[data-testid='spnSRPProdPrice']"):
        prices.update(extract_prices(result.get_text()))

    return list(prices)[:5]

def scrape_shopee_price(query):
    """Scraping harga dari Shopee"""
    search_url = f"https://shopee.co.id/search?keyword={query}"
    response = requests.get(search_url, headers=HEADERS)

    if response.status_code != 200:
        logger.error(f"❌ Gagal mengambil data harga dari Shopee untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()

    for result in soup.select("div.xrnzAF span.wNNZR"):
        prices.update(extract_prices(result.get_text()))

    return list(prices)[:5]

def scrape_bukalapak_price(query):
    """Scraping harga dari Bukalapak"""
    search_url = f"https://www.bukalapak.com/products?search%5Bkeywords%5D={query}"
    response = requests.get(search_url, headers=HEADERS)

    if response.status_code != 200:
        logger.error(f"❌ Gagal mengambil data harga dari Bukalapak untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()

    for result in soup.select("span.amount"):
        prices.update(extract_prices(result.get_text()))

    return list(prices)[:5]

def scrape_blibli_price(query):
    search_url = f"https://www.blibli.com/jual/{query.replace(' ', '-')}"
    response = requests.get(search_url, headers=get_headers())
    soup = BeautifulSoup(response.text, "html.parser")

    prices = []
    for item in soup.select("div.product__price"):
        prices.append(item.get_text())

    return prices

def scrape_digimap_price(query):
    search_url = f"https://www.digimap.co.id/collections/{query.replace(' ', '-')}"
    response = requests.get(search_url, headers=get_headers())
    soup = BeautifulSoup(response.text, "html.parser")

    prices = []
    for item in soup.select("span.money"):
        prices.append(item.get_text())

    return prices

def scrape_price(query):
    """Menggabungkan semua sumber harga dari berbagai e-commerce"""
    logger.info(f"🔍 Mencari harga untuk: {query}")

    logger.info("🔄 Scraping harga dari Google...")
    google_prices = scrape_google_price(query)
    logger.info(f"✅ Hasil Google: {google_prices}")

    logger.info("🔄 Scraping harga dari Tokopedia...")
    tokopedia_prices = scrape_tokopedia_price(query)
    logger.info(f"✅ Hasil Tokopedia: {tokopedia_prices}")

    logger.info("🔄 Scraping harga dari Shopee...")
    shopee_prices = scrape_shopee_price(query)
    logger.info(f"✅ Hasil Shopee: {shopee_prices}")

    logger.info("🔄 Scraping harga dari Bukalapak...")
    bukalapak_prices = scrape_bukalapak_price(query)
    logger.info(f"✅ Hasil Bukalapak: {bukalapak_prices}")

    logger.info("🔄 Scraping harga dari Blibli...")
    blibli_prices = scrape_blibli_price(query)
    logger.info(f"✅ Hasil Blibli: {blibli_prices}")

    logger.info("🔄 Scraping harga dari Digimap...")
    digimap_prices = scrape_digimap_price(query)
    logger.info(f"✅ Hasil Blibli: {digimap_prices}")

    all_prices = google_prices + tokopedia_prices + shopee_prices + bukalapak_prices + blibli_prices + digimap_prices
    unique_prices = sorted(set(all_prices))  # Hilangkan duplikasi dan urutkan

    if not unique_prices:
        logger.warning(f"❌ Tidak menemukan harga untuk '{query}'")
    else:
        logger.info(f"📊 Harga ditemukan untuk '{query}': {unique_prices}")

    return unique_prices[:5] if unique_prices else None

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

    logger.info(f"📩 Inline Query Diterima: '{query}'")
    add_to_history(query)

    markov_result = predict_markov(query)
    google_results = predict_google(query)

    if not google_results:
        logger.warning(f"⚠️ Google tidak memberikan prediksi untuk: {query}")

    predictions = []

    if markov_result:
        words = markov_result.split(" ")
        if len(words) > 1:
            predictions.append(f"{query} {words[1]}")
    
    logger.info(f"🔎 Prediksi Markov: {markov_result}")

    predictions.extend(google_results)
    logger.info(f"🔎 Prediksi Google Ditambahkan: {google_results}")

    if not predictions:
        predictions.extend(get_similar_from_history(query))

    if not predictions:
        first_word = query.split(" ")[0]
        google_fallback = predict_google(first_word)
        predictions.extend(google_fallback[:2])

    predictions = list(set(predictions))[:3]  # Hilangkan duplikat dan batasi 3 hasil

    logger.info(f"📌 Final Predictions: {predictions}")
    
    results = [
        InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=pred,
            input_message_content=InputTextMessageContent(f"{query} {pred}"),
        )
        for pred in predictions
    ]

    if results:
        await update.inline_query.answer(results, cache_time=1)

def normalize_price_question(text):
    text = text.lower()
    
    # Hilangkan kata-kata tanya dan "harga" yang berulang
    text = re.sub(r"\b(harga|berapa|tolong cari|tolong carikan|mohon carikan)\b", "", text).strip()

    # Jika belum ada kata "harga" di depan, tambahkan
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
                answer = f"Kisaran Harga: {min_price} - {max_price}"
                save_price_data(normalized_question, answer)
            else:
                answer = "❌ Tidak dapat menemukan harga untuk produk tersebut."

        await update.message.reply_text(answer)
    else:
        await update.message.reply_text("Silakan ketik pertanyaan harga atau gunakan inline query untuk prediksi teks.")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    logger.info("🚀 Bot Telegram sedang berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
