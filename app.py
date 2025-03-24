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
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
]

HEADERS = {"User-Agent": random.choice(USER_AGENTS)}

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

async def scrape_tokopedia_price(query):
    """Scraping harga dari Tokopedia berdasarkan pencarian teks 'Rp', hanya ambil harga masuk akal"""
    search_url = f"https://www.tokopedia.com/search?st=product&q={query}"
    response = requests.get(search_url, headers=HEADERS)

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Tokopedia untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    # 1️⃣ Ambil semua teks dari halaman
    all_text = soup.get_text()

    # 2️⃣ Cari semua harga dengan format 'RpX.XXX.XXX' menggunakan regex
    prices = re.findall(r"Rp[\s]?[\d.,]+", all_text)

    # 3️⃣ Bersihkan format harga (hilangkan spasi, ubah koma menjadi titik)
    clean_prices = []
    for price in prices:
        price_cleaned = price.replace("Rp", "").replace(" ", "").replace(",", "").strip()
        try:
            price_int = int(price_cleaned)  # Konversi ke integer
            if 200000 <= price_int <= 50000000:  # Hanya ambil harga dalam rentang wajar
                clean_prices.append(price_int)
        except ValueError:
            continue

    # 4️⃣ Ambil harga termurah yang masuk akal
    if clean_prices:
        min_price = min(clean_prices)
        logging.info(f"✅ Harga termurah di Tokopedia untuk '{query}': Rp{min_price:,}")
        return [f"Rp{min_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Tokopedia")
    return []

async def scrape_shopee_price(query):
    """Scraping harga dari Shopee dengan fallback selector."""
    search_url = f"https://shopee.co.id/search?keyword={query}"
    response = requests.get(search_url, headers=HEADERS)
    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Shopee untuk '{query}'")
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()
    selectors = [
        "div.xrnzAF span.wNNZR",
        "div._1d9_77"  # alternatif jika struktur berubah
    ]
    for sel in selectors:
        for result in soup.select(sel):
            prices.update(extract_prices(result.get_text()))
    return list(prices)[:5]

async def scrape_bukalapak_price(query):
    """Scraping harga dari Bukalapak dengan fallback selector."""
    search_url = f"https://www.bukalapak.com/products?search%5Bkeywords%5D={query}"
    response = requests.get(search_url, headers=HEADERS)
    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Bukalapak untuk '{query}'")
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()
    selectors = [
        "span.amount",
        "span.product-price"  # alternatif jika tersedia
    ]
    for sel in selectors:
        for result in soup.select(sel):
            prices.update(extract_prices(result.get_text()))
    return list(prices)[:5]

async def scrape_blibli_price(query):
    """Scraping harga dari Blibli dengan fallback selector."""
    search_url = f"https://www.blibli.com/jual/{query.replace(' ', '-')}"
    response = requests.get(search_url, headers=HEADERS)
    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Blibli untuk '{query}'")
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()
    selectors = [
        "div.product__price",
        "span.product-price"  # alternatif
    ]
    for sel in selectors:
        for result in soup.select(sel):
            prices.update(extract_prices(result.get_text()))
    return list(prices)

async def scrape_digimap_price(query):
    """Scraping harga dari Digimap dengan fallback selector."""
    search_url = f"https://www.digimap.co.id/collections/{query.replace(' ', '-')}"
    response = requests.get(search_url, headers=HEADERS)
    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Digimap untuk '{query}'")
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    prices = set()
    selectors = [
        "span.money"
    ]
    for sel in selectors:
        for result in soup.select(sel):
            prices.update(extract_prices(result.get_text()))
    return list(prices)

async def scrape_price(query):
    """Menggabungkan semua sumber harga dari berbagai e-commerce"""
    logging.info(f"🔍 Mencari harga untuk: {query}")

    logging.info("🔄 Scraping harga dari Tokopedia...")
    tokopedia_prices = await scrape_tokopedia_price(query)
    logging.info(f"✅ Hasil Tokopedia: {tokopedia_prices}")

    logging.info("🔄 Scraping harga dari Shopee...")
    shopee_prices = await scrape_shopee_price(query)
    logging.info(f"✅ Hasil Shopee: {shopee_prices}")

    logging.info("🔄 Scraping harga dari Bukalapak...")
    bukalapak_prices = await scrape_bukalapak_price(query)
    logging.info(f"✅ Hasil Bukalapak: {bukalapak_prices}")

    logging.info("🔄 Scraping harga dari Blibli...")
    blibli_prices = await scrape_blibli_price(query)
    logging.info(f"✅ Hasil Blibli: {blibli_prices}")

    logging.info("🔄 Scraping harga dari Digimap...")
    digimap_prices = await scrape_digimap_price(query)
    logging.info(f"✅ Hasil Digimap: {digimap_prices}")

    all_prices = tokopedia_prices + shopee_prices + bukalapak_prices + list(blibli_prices) + list(digimap_prices)
    unique_prices = sorted(set(all_prices))

    if not unique_prices:
        logging.warning(f"❌ Tidak menemukan harga untuk '{query}'")
    else:
        logging.info(f"📊 Harga ditemukan untuk '{query}': {unique_prices}")

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

def normalize_price_query(text):
    """Membersihkan query agar lebih cocok dengan format pencarian di e-commerce secara fleksibel"""
    text = text.lower().strip()

    # 1️⃣ Hapus kata pertama jika merupakan kata tanya umum
    text = re.sub(r"^(berapa|berapa harga|berapa sih|berapa si|cari|tolong|mohon|please|find me|how much|where to buy) ", "", text)

    # 2️⃣ Hapus kata-kata tambahan umum di tengah atau akhir kalimat
    text = re.sub(r"\b(harga|minta|bantu|carikan|tolong carikan|mohon carikan|info|tentang|list harga|daftar harga|diskon|discount|best price)\b", "", text).strip()

    # 3️⃣ Hapus simbol atau karakter yang tidak diperlukan
    text = re.sub(r"[^\w\s]", "", text)  # Menghapus tanda baca seperti "?", "!", ",", dll.

    # 4️⃣ Hilangkan spasi berlebih dan kata duplikat
    words = text.split()
    text = " ".join(sorted(set(words), key=words.index))

    # 5️⃣ Cegah query terlalu pendek (misalnya hanya "hp" atau "laptop")
    if text in ["hp", "laptop", "gpu", "pc", "handphone", "smartphone", "console"]:
        text += " terbaru"  # Ubah "hp" menjadi "hp terbaru" agar lebih masuk akal

    return text.strip()

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text.strip().lower()

    # Jika ini pertanyaan harga dalam chat (bukan inline query)
    if is_price_question(text):
        await update.message.reply_text("🔍 Mencari harga...")
        normalized_question = normalize_price_query(text)
        prices = await scrape_price(normalized_question)

        if prices:
            min_price = min(prices)
            max_price = max(prices)
            answer = f"Kisaran Harga: {min_price} - {max_price}"
        else:
            answer = "❌ Tidak dapat menemukan harga untuk produk tersebut."

        await update.message.reply_text(answer)
    else:
        await handle_general_question(update, text)  # Panggil fungsi lain untuk pertanyaan umum

async def handle_general_question(update, text):
    """Menangani pertanyaan yang tidak berkaitan dengan harga"""
    await update.message.reply_text("Saya mendeteksi ini bukan pertanyaan tentang harga. Fungsi lain akan segera ditambahkan!")

def is_price_question(text):
    """Deteksi apakah pertanyaan berkaitan dengan harga atau tidak"""
    text = text.lower().strip()
    
    # Kata kunci yang menunjukkan pertanyaan tentang harga
    price_keywords = [
        "harga", "berapa harga", "cari harga", "harga terbaru", "diskon", "best price",
        "murah", "mahal", "kisaran harga", "harga pasaran", "harga second", "harga bekas"
    ]

    # Jika salah satu kata kunci muncul dalam teks, berarti pertanyaan tentang harga
    return any(keyword in text for keyword in price_keywords)

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    logger.info("🚀 Bot Telegram sedang berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()
