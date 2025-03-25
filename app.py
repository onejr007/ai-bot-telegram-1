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
from collections import Counter
from requests_html import AsyncHTMLSession
from statistics import mean, median

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
    """Melatih model Markov dari history chat"""
    try:
        with open("history_chat.txt", "r", encoding="utf-8") as f:
            text_data = f.read().strip()
        
        if not text_data or len(text_data.split()) < 5:
            logging.warning("⚠️ Tidak cukup data untuk model Markov! Menggunakan dataset default.")
            text_data = "Selamat datang di bot prediksi teks. Silakan ketik sesuatu."

        return markovify.Text(text_data, state_size=2)
    
    except FileNotFoundError:
        logging.error("❌ File history_chat.txt tidak ditemukan. Menggunakan model default.")
        text_data = "Selamat datang di bot prediksi teks. Silakan ketik sesuatu."
        return markovify.Text(text_data, state_size=2)

def predict_markov(query):
    """Memprediksi teks berikutnya menggunakan Markov Chain"""
    try:
        model = train_markov()
        prediction = model.make_sentence(tries=10)  # Coba prediksi hingga 10 kali
        
        if not prediction:
            logging.warning(f"⚠️ Tidak ada prediksi Markov untuk: {query}")
            return ""
        
        return prediction
    
    except Exception as e:
        logging.error(f"❌ Gagal memprediksi dengan Markov: {e}")
        return ""

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
    return re.findall(r"Rp\s?[\d.,]+", text)

def clean_price_format(price_str):
    """Membersihkan harga dari format tidak valid dan angka tambahan setelah harga utama"""
    if not isinstance(price_str, str):
        return None  # Abaikan jika bukan string

    # Hapus "Rp" dan spasi awal
    price_cleaned = price_str.replace("Rp", "").strip()

    # Cari pola harga utama dengan format angka dan titik pemisah ribuan
    match = re.search(r"(\d{1,3}(?:\.\d{3})*)", price_cleaned)

    if not match:
        return None  # Abaikan jika tidak menemukan angka yang sesuai

    # Ambil hanya bagian harga utama
    price_main = match.group(1)

    # Hapus titik agar bisa dikonversi ke integer
    price_value = int(price_main.replace(".", ""))

    return price_value

def determine_minimum_price(prices):
    """Menentukan harga terendah yang masuk akal berdasarkan distribusi data"""
    if not prices:
        return 0  # Jika tidak ada harga, tidak ada batas minimum

    sorted_prices = sorted(prices)
    q1 = median(sorted_prices[:len(sorted_prices)//2])  # Kuartil pertama (Q1)
    
    return max(q1 // 2, 50000)  # Minimal 50rb untuk menghindari harga aneh

def remove_outliers(prices):
    """Menghapus outlier menggunakan metode interquartile range (IQR)"""
    if len(prices) < 4:  
        return prices  # Tidak cukup data untuk menghitung IQR, gunakan semua harga

    prices.sort()  # Pastikan data terurut sebelum median dihitung

    q1 = median(prices[:len(prices)//2])  # Kuartil pertama (Q1)
    q3 = median(prices[len(prices)//2:])  # Kuartil ketiga (Q3)
    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    filtered_prices = [p for p in prices if lower_bound <= p <= upper_bound]

    return filtered_prices if filtered_prices else prices  # Jika semua harga dianggap outlier, pakai semua harga awal

async def scrape_tokopedia_price(query):
    """Scraping harga dari Tokopedia dan merata-ratakan harga valid"""
    search_url = f"https://www.tokopedia.com/search?st=product&q={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS)
    logging.info(f"Link Tokped : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Tokopedia untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    # Ambil semua teks dari halaman
    all_text = soup.get_text()

    # Cari semua harga yang valid menggunakan regex
    raw_prices = extract_prices(all_text)

    logging.info(f"🔍 Harga mentah ditemukan di Tokopedia untuk '{query}': {raw_prices}")

    valid_prices = []
    invalid_prices = []

    for price in raw_prices:
        price_cleaned = clean_price_format(price)

        if price_cleaned is not None:
            valid_prices.append(price_cleaned)
        else:
            invalid_prices.append(price)

    logging.info(f"✅ Harga valid setelah cleaning: {valid_prices}")
    logging.info(f"⚠️ Harga tidak valid (diabaikan): {invalid_prices}")

    # **Menentukan batas harga minimum berdasarkan data yang ditemukan**
    min_valid_price = determine_minimum_price(valid_prices)
    logging.info(f"📉 Harga terendah yang masuk akal berdasarkan data: {min_valid_price}")

    valid_prices = [p for p in valid_prices if p >= min_valid_price]

    # **Filter harga berdasarkan distribusi data (IQR)**
    filtered_prices = remove_outliers(valid_prices)
    
    logging.info(f"✅ Harga setelah filter outlier: {filtered_prices}")

    # **Rata-rata harga setelah filtering**
    if filtered_prices:
        avg_price = round(mean(filtered_prices))

        logging.info(f"✅ Harga rata-rata di Tokopedia untuk '{query}': Rp{avg_price:,}")
        return [f"Rp{avg_price:,}".replace(",", ".")]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Tokopedia")
    return []

async def scrape_shopee_price(query):
    """Scraping harga dari Shopee menggunakan requests tanpa browser"""
    query = query.replace(" ", "%20")
    search_url = f"https://shopee.co.id/search?keyword={query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://shopee.co.id/"
    }

    response = requests.get(search_url, headers=headers)
    if response.status_code != 200:
        logging.warning(f"⚠️ Gagal mengakses Shopee (status {response.status_code})")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Ambil harga produk pertama yang ditemukan
    raw_prices = soup.find_all("span", string=re.compile(r"Rp"))

    prices = []
    for price in raw_prices:
        price_text = re.sub(r"[^\d]", "", price.get_text())
        if price_text.isdigit():
            prices.append(int(price_text))

    if prices:
        best_price = min(prices)
        logging.info(f"✅ Harga termurah di Shopee untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga untuk '{query}' di Shopee")
    return []

async def scrape_bukalapak_price(query):
    """Scraping harga dari Bukalapak menggunakan JSON API."""
    query = normalize_price_query(query)
    search_url = f"https://api.bukalapak.com/multistrategy-products?keywords={query.replace(' ', '+')}&limit=10"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logging.info(f"Link Bukalapak : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Bukalapak untuk '{query}'")
        return []

    try:
        data = response.json()
        products = data.get("data", {}).get("products", [])
        raw_prices = [int(product["price"]) for product in products]
    except Exception as e:
        logging.error(f"⚠️ Gagal memproses JSON Bukalapak: {e}")
        return []

    logging.info(f"🔍 Harga mentah ditemukan di Bukalapak untuk '{query}': {raw_prices}")

    valid_prices = [p for p in raw_prices if 500000 <= p <= 50000000]
    invalid_prices = list(set(raw_prices) - set(valid_prices))

    logging.info(f"✅ Harga valid setelah filtering: {valid_prices}")
    logging.info(f"⚠️ Harga tidak valid (diabaikan): {invalid_prices}")

    if valid_prices:
        best_price = min(valid_prices)
        logging.info(f"✅ Harga termurah di Bukalapak untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Bukalapak")
    return []

async def scrape_blibli_price(query):
    """Scraping harga dari Blibli menggunakan JSON API."""
    query = normalize_price_query(query)
    search_url = f"https://www.blibli.com/backend/search/products?searchTerm={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logging.info(f"Link Blibli : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Blibli untuk '{query}'")
        return []

    try:
        data = response.json()
        products = data.get("data", {}).get("products", [])
        raw_prices = [int(product["price"]["value"]) for product in products]
    except Exception as e:
        logging.error(f"⚠️ Gagal memproses JSON Blibli: {e}")
        return []

    logging.info(f"🔍 Harga mentah ditemukan di Blibli untuk '{query}': {raw_prices}")

    valid_prices = [p for p in raw_prices if 500000 <= p <= 50000000]

    if valid_prices:
        best_price = min(valid_prices)
        logging.info(f"✅ Harga termurah di Blibli untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Blibli")
    return []

async def scrape_digimap_price(query):
    """Scraping harga dari Digimap menggunakan HTML parsing."""
    query = normalize_price_query(query)
    search_url = f"https://www.digimap.co.id/search?type=product&q={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logging.info(f"Link Digimap : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Digimap untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    price_elements = soup.select("span.money")

    raw_prices = [price.get_text() for price in price_elements]
    logging.info(f"🔍 Harga mentah ditemukan di Digimap untuk '{query}': {raw_prices}")

    valid_prices = [int(re.sub(r"[^\d]", "", price)) for price in raw_prices if 500000 <= int(re.sub(r"[^\d]", "", price)) <= 50000000]

    if valid_prices:
        best_price = min(valid_prices)
        logging.info(f"✅ Harga termurah di Digimap untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Digimap")
    return []

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

def merge_prediction(query, prediction):
    """Menggabungkan query dengan prediksi tanpa duplikasi kata."""
    query_words = query.split()
    pred_words = prediction.split()

    # Jika prediksi mengandung query di awal, ambil bagian tambahan saja
    if prediction.startswith(query):
        return prediction  # Gunakan prediksi langsung tanpa menambahkan query lagi
    
    # Jika query mengandung bagian awal dari prediksi, hilangkan bagian yang berulang
    for i in range(len(query_words)):
        if query_words[i:] == pred_words[:len(query_words[i:])]:
            return " ".join(query_words[:i] + pred_words)

    # Jika tidak ada matching, gunakan prediksi secara langsung
    return prediction

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
            input_message_content=InputTextMessageContent(merge_prediction(query, pred)),  
        )
        for pred in predictions
    ]

    if results:
        await update.inline_query.answer(results, cache_time=1)

def normalize_price_query(text):
    """Bersihkan query agar hanya berisi kata kunci produk yang valid"""
    text = text.lower().strip()

    # 1️⃣ Hapus kata seperti "harga", "cek harga", "berapa harga", dll.
    text = re.sub(r"\b(harga|cek harga|berapa harga|berapa sih|berapa si)\b", "", text).strip()

    # 2️⃣ Hapus simbol atau karakter yang tidak diperlukan
    text = re.sub(r"[^\w\s]", "", text)  # Menghapus tanda baca seperti "?", "!", ",", dll.

    # 3️⃣ Hilangkan spasi berlebih dan kata duplikat
    words = text.split()
    text = " ".join(sorted(set(words), key=words.index))

    # 4️⃣ Koreksi kesalahan umum (misalnya "ip" -> "iphone")
    text = re.sub(r"\b(ipun|ipin|ipon|ip)(?:\s+(\d+))?\b", r"iphone \2", text).strip()

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
