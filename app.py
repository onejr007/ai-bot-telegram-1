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

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def get_headers(site):
    """Menghasilkan headers dinamis berdasarkan situs"""
    referers = {
        "tokopedia": "https://www.tokopedia.com/",
        "shopee": "https://shopee.co.id/",
        "lazada": "https://www.lazada.co.id/",
        "priceza": "https://www.priceza.co.id/",
        "bukalapak": "https://www.bukalapak.com/",
        "blibli": "https://www.blibli.com/",
        "free-proxy": "https://free-proxy-list.net/",
        "proxyscrape": "https://api.proxyscrape.com/",
    }
    
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referers.get(site, "https://google.com/"),  # Default ke Google jika tidak dikenal
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }

HEADERS = {"User-Agent": random.choice(USER_AGENTS)}

def fetch_proxies_from_free_proxy_list():
    """Mengambil proxy dari free-proxy-list.net"""
    url = "https://free-proxy-list.net/"
    try:
        response = requests.get(url, headers=get_headers("free-proxy"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        proxies = []
        for row in soup.select("#list tbody tr")[:10]:  # Ambil 10 proxy pertama
            cols = row.find_all("td")
            ip = cols[0].text
            port = cols[1].text
            proxies.append(f"{ip}:{port}")
        logger.info(f"Berhasil mengambil {len(proxies)} proxy dari free-proxy-list.net")
        return proxies
    except Exception as e:
        logger.error(f"Gagal mengambil proxy dari free-proxy-list.net: {str(e)}")
        return []

def fetch_proxies_from_proxyscrape():
    """Mengambil proxy dari proxyscrape.com"""
    url = "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=all"
    try:
        response = requests.get(url, headers=get_headers("proxyscrape"), timeout=10)
        response.raise_for_status()
        proxies = response.text.splitlines()[:10]  # Ambil 10 proxy pertama
        logger.info(f"Berhasil mengambil {len(proxies)} proxy dari proxyscrape.com")
        return proxies
    except Exception as e:
        logger.error(f"Gagal mengambil proxy dari proxyscrape.com: {str(e)}")
        return []

def get_free_proxies():
    """Mengambil proxy dari beberapa sumber dengan backup"""
    proxies = fetch_proxies_from_free_proxy_list()
    if not proxies:  # Jika gagal, coba sumber lain
        logger.warning("Sumber pertama gagal, mencoba sumber cadangan...")
        proxies = fetch_proxies_from_proxyscrape()
    return proxies if proxies else ["103.147.76.136:3128"]  # Proxy default jika semua gagal

def get_chrome_options(headless=True, proxy=None):
    options = Options()
    if headless:
        options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    user_agent = random.choice(USER_AGENTS)
    options.add_argument(f"user-agent={user_agent}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    return options

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
            logger.info("⚠️ Tidak cukup data untuk model Markov! Menggunakan dataset default.")
            text_data = "Selamat datang di bot prediksi teks. Silakan ketik sesuatu."

        return markovify.Text(text_data, state_size=2)
    
    except FileNotFoundError:
        logger.info("❌ File history_chat.txt tidak ditemukan. Menggunakan model default.")
        text_data = "Selamat datang di bot prediksi teks. Silakan ketik sesuatu."
        return markovify.Text(text_data, state_size=2)

def predict_markov(query):
    """Memprediksi teks berikutnya menggunakan Markov Chain"""
    try:
        model = train_markov()
        prediction = model.make_sentence(tries=10)  # Coba prediksi hingga 10 kali
        
        if not prediction:
            logger.info(f"⚠️ Tidak ada prediksi Markov untuk: {query}")
            return ""
        
        return prediction
    
    except Exception as e:
        logger.info(f"❌ Gagal memprediksi dengan Markov: {e}")
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

def clean_price_format(price_str):
    """Membersihkan harga dari format tidak valid dan angka tambahan setelah harga utama."""
    if not isinstance(price_str, str):
        return None

    # Hapus "Rp" dan spasi awal
    price_cleaned = price_str.replace("Rp", "").strip()

    # Cari angka harga utama
    match = re.search(r"(\d{1,3}(?:\.\d{3})*)", price_cleaned)

    if not match:
        return None  # Abaikan jika tidak menemukan angka yang sesuai

    # Ambil bagian harga utama dan konversi ke integer
    price_main = match.group(1).replace(".", "")

    return int(price_main)

def get_min_reasonable_price(prices):
    """Menentukan batas harga minimum yang masuk akal."""
    if len(prices) < 4:
        return min(prices) if prices else 0  # Jika data terlalu sedikit, pakai harga terendah yang tersedia

    sorted_prices = sorted(prices)
    q1 = median(sorted_prices[:len(sorted_prices)//2])  # Kuartil pertama (Q1)
    median_price = median(sorted_prices)  # Median harga

    # Harga minimum setidaknya setengah dari median
    min_reasonable = max(q1, median_price / 2)

    # Jika batas masih terlalu rendah, gunakan median sebagai batas bawah
    if min_reasonable < median_price / 2:
        min_reasonable = median_price / 2

    return round(min_reasonable)

async def scrape_tokopedia_price(query):
    """Scraping harga dari Tokopedia dan merata-ratakan harga valid."""
    search_url = f"https://www.tokopedia.com/search?st=product&q={query.replace(' ', '+')}"
    logger.info(f"🔄 Scraping harga dari Tokopedia untuk '{query}'...")
    logger.info(f"URL: {search_url}")
        
    try:
        response = requests.get(search_url, headers=get_headers("tokopedia"), timeout=10, allow_redirects=True)
        if response.status_code != 200:
            logger.info(f"❌ Gagal mengambil data harga dari Tokopedia untuk '{query}'")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        all_text = soup.get_text()

        raw_prices = re.findall(r"Rp[\s]?[\d.,]+", all_text)
        logger.info(f"🔍 Harga mentah ditemukan di Tokopedia untuk '{query}': {raw_prices}")

        valid_prices = [clean_price_format(price) for price in raw_prices if clean_price_format(price) is not None]
        logger.info(f"✅ Harga valid setelah cleaning: {valid_prices}")

        if not valid_prices:
            logger.info(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Tokopedia")
            return []

        # Tentukan batas harga terendah yang masuk akal
        min_reasonable_price = get_min_reasonable_price(valid_prices)
        logger.info(f"📉 Harga terendah yang masuk akal berdasarkan data: {min_reasonable_price}")

        # Filter harga yang lebih rendah dari batas masuk akal
        filtered_prices = [p for p in valid_prices if p >= min_reasonable_price]
        logger.info(f"✅ Harga setelah filter: {filtered_prices}")

        if not filtered_prices:
            logger.info(f"❌ Tidak ada harga yang masuk akal setelah filtering untuk '{query}'")
            return []

        avg_price = round(mean(filtered_prices))
        logger.info(f"✅ Harga rata-rata di Tokopedia untuk '{query}': Rp{avg_price:,}")
        
        return [f"Rp{avg_price:,}".replace(",", ".")]
    
    except Exception as e:
        logger.info(f"❌ Gagal scraping Tokopedia untuk '{query}': {str(e)}")
        return []

async def scrape_priceza_price(query):
    """Scraping harga dari Priceza dengan mencari teks yang diawali 'Rp'."""
    search_url = f"https://www.priceza.co.id/s/priceza-search/?search={query.replace(' ', '+')}"
    logger.info(f"🔄 Scraping harga dari Priceza untuk '{query}'...")
    logger.info(f"URL: {search_url}")

    try:
        response = requests.get(search_url, headers=get_headers("priceza"), timeout=10, allow_redirects=True)
        if response.status_code != 200:
            logging.error(f"❌ Gagal mengakses Priceza (status {response.status_code})")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        logger.info(f"URL setelah redirect (jika ada): {response.url}")

        # Cari teks yang diawali "Rp" menggunakan regex
        all_text = soup.get_text()
        raw_prices = re.findall(r"Rp[\s]?[\d,.]+", all_text)
        logger.info(f"🔍 Harga mentah ditemukan di Priceza untuk '{query}': {raw_prices}")

        if not raw_prices:
            logger.info(f"⚠️ Tidak menemukan teks harga dengan 'Rp' untuk '{query}'")
            return []

        # Bersihkan harga
        valid_prices = [clean_price_format(price) for price in raw_prices if clean_price_format(price) is not None]
        logger.info(f"✅ Harga valid setelah cleaning: {valid_prices}")

        if not valid_prices:
            logger.info(f"❌ Tidak ada harga valid setelah cleaning untuk '{query}'")
            return []

        # Tentukan batas harga terendah yang masuk akal
        min_reasonable_price = get_min_reasonable_price(valid_prices)
        logger.info(f"📉 Harga terendah yang masuk akal: {min_reasonable_price}")

        # Filter harga
        filtered_prices = [p for p in valid_prices if p >= min_reasonable_price]
        logger.info(f"✅ Harga setelah filter: {filtered_prices}")

        if not filtered_prices:
            logger.info(f"❌ Tidak ada harga yang masuk akal setelah filtering")
            return []

        # Hitung rata-rata
        avg_price = round(mean(filtered_prices))
        logger.info(f"✅ Harga rata-rata di Priceza: Rp{avg_price:,}")

        return [f"Rp{avg_price:,}".replace(",", ".")]

    except Exception as e:
        logger.info(f"❌ Gagal scraping Priceza: {str(e)}")
        return []
    
async def scrape_bukalapak_price(query):
    """Scraping harga dari Bukalapak dengan mencari teks yang diawali 'Rp'."""
    search_url = f"https://www.bukalapak.com/products?search[keywords]={query.replace(' ', '%20')}"

    logger.info(f"🔄 Scraping harga dari Bukalapak untuk '{query}'...")
    logger.info(f"URL awal: {search_url}")

    try:
        response = requests.get(search_url, headers=get_headers("bukalapak"), timeout=10, allow_redirects=True)
        if response.status_code != 200:
            logger.info(f"❌ Gagal mengakses Bukalapak (status {response.status_code})")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        logger.info(f"URL setelah redirect (jika ada): {response.url}")

        # Cari teks yang diawali "Rp" menggunakan regex
        all_text = soup.get_text()
        raw_prices = re.findall(r"Rp[\s]?[\d,.]+", all_text)
        logger.info(f"🔍 Harga mentah ditemukan di Bukalapak untuk '{query}': {raw_prices}")

        if not raw_prices:
            logger.info(f"⚠️ Tidak menemukan teks harga dengan 'Rp' untuk '{query}'")
            return []

        # Bersihkan harga
        valid_prices = [clean_price_format(price) for price in raw_prices if clean_price_format(price) is not None]
        logger.info(f"✅ Harga valid setelah cleaning: {valid_prices}")

        if not valid_prices:
            logger.info(f"❌ Tidak ada harga valid setelah cleaning untuk '{query}'")
            return []

        # Tentukan batas harga terendah yang masuk akal
        min_reasonable_price = get_min_reasonable_price(valid_prices)
        logger.info(f"📉 Harga terendah yang masuk akal: {min_reasonable_price}")

        # Filter harga
        filtered_prices = [p for p in valid_prices if p >= min_reasonable_price]
        logger.info(f"✅ Harga setelah filter: {filtered_prices}")

        if not filtered_prices:
            logger.info(f"❌ Tidak ada harga yang masuk akal setelah filtering")
            return []

        # Hitung rata-rata
        avg_price = round(mean(filtered_prices))
        logger.info(f"✅ Harga rata-rata di Bukalapak: Rp{avg_price:,}")

        return [f"Rp{avg_price:,}".replace(",", ".")]

    except Exception as e:
        logger.info(f"❌ Gagal scraping Bukalapak: {str(e)}")
        return []

async def scrape_blibli_price(query):
    """Scraping harga dari Blibli dengan failover proxy"""
    search_url = f"https://www.blibli.com/cari/{query.replace(' ', '%20')}"
    proxies = get_free_proxies()  # Ambil daftar proxy
    logger.info(f"Daftar proxy yang akan digunakan: {proxies}")

    for proxy in proxies:
        logger.info(f"Mencoba proxy: {proxy}")
        chrome_options = get_chrome_options(headless=True, proxy=proxy)
        driver = None
        try:
            service = Service(executable_path=os.getenv("CHROMEDRIVER_BIN", "/usr/bin/chromedriver"))
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            driver.set_page_load_timeout(15)  # Timeout 15 detik
            driver.get(search_url)
            await asyncio.sleep(5)
            current_url = driver.current_url
            logger.info(f"URL setelah memuat: {current_url}")

            if "challenge" in current_url:
                logger.warning(f"⚠️ Terdeteksi halaman challenge dengan proxy {proxy}")
                continue  # Coba proxy berikutnya

            soup = BeautifulSoup(driver.page_source, "html.parser")
            all_text = soup.get_text()
            raw_prices = re.findall(r"Rp[\s]?[\d,.]+", all_text)
            logger.info(f"🔍 Harga mentah ditemukan: {raw_prices}")

            if not raw_prices:
                logger.warning(f"⚠️ Tidak menemukan teks harga dengan 'Rp' menggunakan proxy {proxy}")
                continue

            valid_prices = [clean_price_format(price) for price in raw_prices if clean_price_format(price) is not None]
            if not valid_prices:
                logger.warning(f"❌ Tidak ada harga valid dengan proxy {proxy}")
                continue

            min_reasonable_price = get_min_reasonable_price(valid_prices)
            filtered_prices = [p for p in valid_prices if p >= min_reasonable_price]
            if not filtered_prices:
                logger.warning(f"❌ Tidak ada harga yang masuk akal dengan proxy {proxy}")
                continue

            avg_price = round(mean(filtered_prices))
            logger.info(f"✅ Harga rata-rata: Rp{avg_price:,}")
            return [f"Rp{avg_price:,}".replace(",", ".")]

        except Exception as e:
            logger.error(f"❌ Gagal dengan proxy {proxy}: {str(e)}")
            continue  # Coba proxy berikutnya
        finally:
            if driver is not None:
                driver.quit()

    logger.error("❌ Semua proxy gagal atau terdeteksi challenge")
    return []

async def scrape_digimap_price(query):
    """Scraping harga dari Digimap menggunakan HTML parsing."""
    query = normalize_price_query(query)
    search_url = f"https://www.digimap.co.id/search?type=product&q={query.replace(' ', '+')}"
    response = requests.get(search_url, headers=HEADERS, timeout=10)
    logger.info(f"Link Digimap : '{search_url}'")

    if response.status_code != 200:
        logger.info(f"❌ Gagal mengambil data harga dari Digimap untuk '{query}'")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    price_elements = soup.select("span.money")

    raw_prices = [price.get_text() for price in price_elements]
    logger.info(f"🔍 Harga mentah ditemukan di Digimap untuk '{query}': {raw_prices}")

    valid_prices = [int(re.sub(r"[^\d]", "", price)) for price in raw_prices if 500000 <= int(re.sub(r"[^\d]", "", price)) <= 50000000]

    if valid_prices:
        best_price = min(valid_prices)
        logger.info(f"✅ Harga termurah di Digimap untuk '{query}': Rp{best_price:,}")
        return [f"Rp{best_price:,}"]

    logger.info(f"❌ Tidak menemukan harga yang masuk akal untuk '{query}' di Digimap")
    return []

async def scrape_price(query):
    """Menggabungkan semua sumber harga dari berbagai e-commerce"""
    logger.info(f"🔍 Mencari harga untuk: {query}")

    logger.info("🔄 Scraping harga dari Tokopedia...")
    tokopedia_prices = await scrape_tokopedia_price(query)
    logger.info(f"✅ Hasil Tokopedia: {tokopedia_prices}")

    logger.info("🔄 Scraping harga dari Priceza...")
    priceza_prices = await scrape_priceza_price(query)
    logger.info(f"✅ Hasil Priceza: {priceza_prices}")

    logger.info("🔄 Scraping harga dari Bukalapak...")
    bukalapak_prices = await scrape_bukalapak_price(query)
    logger.info(f"✅ Hasil Bukalapak: {bukalapak_prices}")

    logger.info("🔄 Scraping harga dari Blibli...")
    blibli_prices = await scrape_blibli_price(query)
    logger.info(f"✅ Hasil Blibli: {blibli_prices}")

    logger.info("🔄 Scraping harga dari Digimap...")
    digimap_prices = await scrape_digimap_price(query)
    logger.info(f"✅ Hasil Digimap: {digimap_prices}")

    all_prices = tokopedia_prices + priceza_prices + bukalapak_prices + list(blibli_prices) + list(digimap_prices)
    unique_prices = sorted(set(all_prices))

    if not unique_prices:
        logger.info(f"❌ Tidak menemukan harga untuk '{query}'")
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
    # Pastikan token ada
    if not TOKEN:
        logger.info("❌ TELEGRAM_BOT_TOKEN tidak ditemukan di environment variables!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    logger.info("🚀 Bot Telegram sedang berjalan...")
    app.run_polling()

if __name__ == "__main__":
    main()