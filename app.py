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
    return re.findall(r"Rp ?[\d.,]+", text)

async def scrape_tokopedia_price(query):
    """Scraping harga dari Tokopedia berdasarkan teks 'Rp', hanya ambil harga valid"""
    query = normalize_price_query(query)  # Gunakan query yang sudah diperbaiki
    search_url = f"https://www.tokopedia.com/search?st=product&q={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS)
    logging.info(f"Link Tokped : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Tokopedia untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    # 1️⃣ Ambil semua teks dari halaman
    all_text = soup.get_text()

    # 2️⃣ Cari semua harga yang valid menggunakan regex
    raw_prices = re.findall(r"Rp[\s]?[\d.,]+", all_text)

    logging.info(f"🔍 Harga mentah ditemukan di Tokopedia untuk '{query}': {raw_prices}")

    # 3️⃣ Bersihkan harga yang salah dan pastikan format benar
    valid_prices = []
    invalid_prices = []

    for price in raw_prices:
        price_cleaned = re.sub(r"[^\d.]", "", price.replace("Rp", "").strip())

        # Ambil hanya angka sebelum titik terakhir untuk menghindari angka tambahan
        if '.' in price_cleaned:
            parts = price_cleaned.split('.')
            if len(parts[-1]) > 3:  # Jika bagian terakhir lebih dari 3 digit, hapus bagian terakhir
                price_cleaned = '.'.join(parts[:-1])

        try:
            price_int = int(price_cleaned.replace(".", ""))
            if 5000000 <= price_int <= 100000000:  # Pastikan harga masuk akal untuk HP flagship
                valid_prices.append(price_int)
            else:
                invalid_prices.append(price_int)
        except ValueError:
            invalid_prices.append(price_cleaned)

    logging.info(f"✅ Harga valid setelah filtering: {valid_prices}")
    logging.info(f"⚠️ Harga tidak valid (diabaikan): {invalid_prices}")

    # 4️⃣ **Gunakan harga termurah yang masuk akal daripada harga paling umum**
    if valid_prices:
        best_price = min(valid_prices)  # Gunakan harga termurah untuk produk flagship

        logging.info(f"✅ Harga termurah di Tokopedia untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}".replace(",", ".")]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Tokopedia")
    return []

async def scrape_shopee_price(query):
    """Scraping harga dari Shopee menggunakan JSON extraction."""
    query = normalize_price_query(query)
    search_url = f"https://shopee.co.id/search?keyword={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logging.info(f"Link Shopee : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Shopee untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    # 1️⃣ Ambil JSON Shopee jika tersedia
    script_tag = soup.find("script", string=re.compile("window.__PRELOADED_STATE__"))
    raw_prices = []
    
    if script_tag:
        json_text = re.search(r"window.__PRELOADED_STATE__\s*=\s*({.*?});", script_tag.string)
        if json_text:
            try:
                json_data = json.loads(json_text.group(1))
                items = json_data.get("listingReducer", {}).get("items", [])
                raw_prices = [int(item["price"]) for item in items if "price" in item]
            except Exception as e:
                logging.error(f"⚠️ Gagal memproses JSON Shopee: {e}")

    logging.info(f"🔍 Harga mentah ditemukan di Shopee untuk '{query}': {raw_prices}")

    # 3️⃣ Filter harga valid
    valid_prices = []
    invalid_prices = []

    for price in raw_prices:
        if 500000 <= price <= 50000000:
            valid_prices.append(price)
        else:
            invalid_prices.append(price)

    logging.info(f"✅ Harga valid setelah filtering: {valid_prices}")
    logging.info(f"⚠️ Harga tidak valid (diabaikan): {invalid_prices}")

    if valid_prices:
        best_price = min(valid_prices)
        logging.info(f"✅ Harga termurah di Shopee untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Shopee")
    return []

async def scrape_bukalapak_price(query):
    """Scraping harga dari Bukalapak menggunakan JSON parsing."""
    query = normalize_price_query(query)
    search_url = f"https://www.bukalapak.com/products?search%5Bkeywords%5D={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logging.info(f"Link Bukalapak : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Bukalapak untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")

    # 1️⃣ Ambil JSON Bukalapak
    script_tag = soup.find("script", string=re.compile('"price":'))
    raw_prices = []

    if script_tag:
        json_text = re.search(r"({.*})", script_tag.string)
        if json_text:
            try:
                json_data = json.loads(json_text.group(1))
                raw_prices = [int(product["price"]) for product in json_data["data"]["products"]]
            except Exception as e:
                logging.error(f"⚠️ Gagal memproses JSON Bukalapak: {e}")

    logging.info(f"🔍 Harga mentah ditemukan di Bukalapak untuk '{query}': {raw_prices}")

    # 3️⃣ Filter harga valid
    valid_prices = []
    invalid_prices = []

    for price in raw_prices:
        if 500000 <= price <= 50000000:
            valid_prices.append(price)
        else:
            invalid_prices.append(price)

    logging.info(f"✅ Harga valid setelah filtering: {valid_prices}")
    logging.info(f"⚠️ Harga tidak valid (diabaikan): {invalid_prices}")

    if valid_prices:
        best_price = min(valid_prices)
        logging.info(f"✅ Harga termurah di Bukalapak untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Bukalapak")
    return []

async def scrape_blibli_price(query):
    """Scraping harga dari Blibli berbasis HTML parsing."""
    query = normalize_price_query(query)
    search_url = f"https://www.blibli.com/search?s={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logging.info(f"Link Blibli : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Blibli untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    all_text = soup.get_text()

    # 1️⃣ Cari semua harga dengan regex
    raw_prices = re.findall(r"Rp[\s]?[\d.,]+", all_text)
    logging.info(f"🔍 Harga mentah ditemukan di Blibli untuk '{query}': {raw_prices}")

    # 3️⃣ Filter harga valid
    valid_prices = []
    invalid_prices = []

    for price in raw_prices:
        price_cleaned = re.sub(r"[^\d]", "", price.replace("Rp", "").strip())
        try:
            price_int = int(price_cleaned)
            if 500000 <= price_int <= 50000000:
                valid_prices.append(price_int)
            else:
                invalid_prices.append(price_int)
        except ValueError:
            invalid_prices.append(price_cleaned)

    logging.info(f"✅ Harga valid setelah filtering: {valid_prices}")
    logging.info(f"⚠️ Harga tidak valid (diabaikan): {invalid_prices}")

    if valid_prices:
        best_price = min(valid_prices)
        logging.info(f"✅ Harga termurah di Blibli untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logging.warning(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Blibli")
    return []

async def scrape_digimap_price(query):
    """Scraping harga dari Digimap berbasis HTML parsing."""
    query = normalize_price_query(query)
    search_url = f"https://www.digimap.co.id/search?type=product&q={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logging.info(f"Link Digimap : '{search_url}'")

    if response.status_code != 200:
        logging.error(f"❌ Gagal mengambil data harga dari Digimap untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    all_text = soup.get_text()

    # 1️⃣ Cari semua harga dengan regex
    raw_prices = re.findall(r"Rp[\s]?[\d.,]+", all_text)
    logging.info(f"🔍 Harga mentah ditemukan di Digimap untuk '{query}': {raw_prices}")

    # 3️⃣ Filter harga valid
    valid_prices = []
    invalid_prices = []

    for price in raw_prices:
        price_cleaned = re.sub(r"[^\d]", "", price.replace("Rp", "").strip())
        try:
            price_int = int(price_cleaned)
            if 5000000 <= price_int <= 100000000:  # Batas harga lebih tinggi
                valid_prices.append(price_int)
            else:
                invalid_prices.append(price_int)
        except ValueError:
            invalid_prices.append(price_cleaned)

    logging.info(f"✅ Harga valid setelah filtering: {valid_prices}")
    logging.info(f"⚠️ Harga tidak valid (diabaikan): {invalid_prices}")

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
