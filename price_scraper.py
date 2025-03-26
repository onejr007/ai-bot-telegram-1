# price_scraper.py
import requests
from bs4 import BeautifulSoup
import redis
import random
import asyncio
import re
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import logging
from utils import clean_price_format, get_min_reasonable_price, normalize_price_query, mean, save_price_history, find_price_in_history

# Koneksi Redis dengan autentikasi
REDIS_HOST = os.getenv("REDIS_HOST", "redis.railway.internal")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    password=REDIS_PASSWORD,
    db=0,
    decode_responses=True
)

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def get_valid_proxy():
    proxy = redis_client.lpop("proxy_list")
    if proxy:
        redis_client.rpush("proxy_list", proxy)
        return proxy
    return None

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

async def scrape_tokopedia_price(query):
    search_url = f"https://www.tokopedia.com/search?st=product&q={query.replace(' ', '+')}"
    try:
        response = requests.get(search_url, headers=get_headers("tokopedia"), timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        raw_prices = soup.get_text().re.findall(r"Rp[\s]?[\d.,]+")
        valid_prices = [clean_price_format(price) for price in raw_prices if clean_price_format(price)]
        if not valid_prices:
            return []
        min_reasonable = get_min_reasonable_price(valid_prices)
        filtered_prices = [p for p in valid_prices if p >= min_reasonable]
        if not filtered_prices:
            return []
        avg_price = round(mean(filtered_prices))
        return [f"Rp{avg_price:,}".replace(",", ".")]
    except Exception as e:
        logger.error(f"❌ Gagal scraping Tokopedia: {e}")
        return []

async def scrape_priceza_price(query):
    search_url = f"https://www.priceza.co.id/s/priceza-search/?search={query.replace(' ', '+')}"
    try:
        response = requests.get(search_url, headers=get_headers("priceza"), timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        raw_prices = soup.get_text().re.findall(r"Rp[\s]?[\d,.]+")
        valid_prices = [clean_price_format(price) for price in raw_prices if clean_price_format(price)]
        if not valid_prices:
            return []
        min_reasonable = get_min_reasonable_price(valid_prices)
        filtered_prices = [p for p in valid_prices if p >= min_reasonable]
        if not filtered_prices:
            return []
        avg_price = round(mean(filtered_prices))
        return [f"Rp{avg_price:,}".replace(",", ".")]
    except Exception as e:
        logger.error(f"❌ Gagal scraping Priceza: {e}")
        return []

async def scrape_bukalapak_price(query):
    search_url = f"https://www.bukalapak.com/products?search[keywords]={query.replace(' ', '%20')}"
    try:
        response = requests.get(search_url, headers=get_headers("bukalapak"), timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        raw_prices = soup.get_text().re.findall(r"Rp[\s]?[\d,.]+")
        valid_prices = [clean_price_format(price) for price in raw_prices if clean_price_format(price)]
        if not valid_prices:
            return []
        min_reasonable = get_min_reasonable_price(valid_prices)
        filtered_prices = [p for p in valid_prices if p >= min_reasonable]
        if not filtered_prices:
            return []
        avg_price = round(mean(filtered_prices))
        return [f"Rp{avg_price:,}".replace(",", ".")]
    except Exception as e:
        logger.error(f"❌ Gagal scraping Bukalapak: {e}")
        return []

async def try_scrape_blibli(search_url, use_proxy=False, proxy=None):
    chrome_options = get_chrome_options(headless=True, proxy=proxy if use_proxy else None)
    chrome_options.add_argument("--page-load-strategy=eager")
    driver = None
    try:
        service = Service(executable_path=os.getenv("CHROMEDRIVER_BIN", "/usr/bin/chromedriver"))
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        driver.set_page_load_timeout(8)
        driver.get(search_url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "blu-product-card__price-final")))
        if "challenge" in driver.current_url:
            return None
        price_elements = driver.find_elements(By.CLASS_NAME, "blu-product-card__price-final")
        raw_prices = [elem.text.strip() for elem in price_elements if elem.text.strip()]
        valid_prices = [clean_price_format(f"Rp{price}") for price in raw_prices if clean_price_format(f"Rp{price}")]
        if not valid_prices:
            return []
        min_reasonable = get_min_reasonable_price(valid_prices)
        filtered_prices = [p for p in valid_prices if p >= min_reasonable]
        if not filtered_prices:
            return []
        avg_price = round(mean(filtered_prices))
        return [f"Rp{avg_price:,}".replace(",", ".")]
    except Exception as e:
        logger.error(f"❌ Gagal scraping Blibli{' dengan proxy ' + proxy if use_proxy else ''}: {e}")
        return None if "challenge" in str(e).lower() else []
    finally:
        if driver:
            driver.quit()

async def scrape_blibli_price(query):
    search_url = f"https://www.blibli.com/cari/{query.replace(' ', '%20')}"
    logger.info(f"🔄 Scraping Blibli untuk '{query}'...")
    result = await try_scrape_blibli(search_url, use_proxy=False)
    if result:
        return result
    logger.info("⚠️ Gagal tanpa proxy, mencoba dengan proxy...")
    while True:
        proxy = get_valid_proxy()
        if not proxy:
            logger.error("❌ Tidak ada proxy valid di Redis")
            break
        result = await try_scrape_blibli(search_url, use_proxy=True, proxy=proxy)
        if result:
            return result
        logger.info(f"Proxy {proxy} gagal, mencoba proxy berikutnya...")
    return []

async def scrape_digimap_price(query):
    query = normalize_price_query(query)
    search_url = f"https://www.digimap.co.id/search?type=product&q={query.replace(' ', '+')}"
    try:
        response = requests.get(search_url, headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        raw_prices = [price.get_text() for price in soup.select("span.money")]
        valid_prices = [int(re.sub(r"[^\d]", "", price)) for price in raw_prices if 500000 <= int(re.sub(r"[^\d]", "", price)) <= 50000000]
        if valid_prices:
            best_price = min(valid_prices)
            return [f"Rp{best_price:,}"]
        return []
    except Exception as e:
        logger.error(f"❌ Gagal scraping Digimap: {e}")
        return []

async def scrape_price(query):
    logger.info(f"🔍 Mencari harga untuk: {query}")
    cached_answer = find_price_in_history(query)
    if cached_answer:
        return cached_answer.split(" - ") if " - " in cached_answer else [cached_answer]

    tasks = [
        scrape_tokopedia_price(query),
        scrape_priceza_price(query),
        scrape_bukalapak_price(query),
        scrape_blibli_price(query),
        scrape_digimap_price(query),
    ]
    try:
        results = await asyncio.gather(*tasks)
        all_prices = [price for sublist in results for price in sublist]
        unique_prices = sorted(set(all_prices))
        if unique_prices:
            save_price_history(query, f"{min(unique_prices)} - {max(unique_prices)}")
        return unique_prices[:5] if unique_prices else None
    except Exception as e:
        logger.error(f"❌ Gagal menjalankan scraping: {e}")
        return None