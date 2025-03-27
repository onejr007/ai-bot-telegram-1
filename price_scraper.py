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
from selenium.common.exceptions import TimeoutException, WebDriverException
import logging
from utils import normalize_price_query, save_price_history, find_price_in_history

REDIS_HOST = os.getenv("REDIS_HOST", "redis.railway.internal")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db=0, decode_responses=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def get_headers(site):
    referers = {
        "tokopedia": "https://www.tokopedia.com/",
        "shopee": "https://shopee.co.id/",
        "lazada": "https://www.lazada.co.id/",
        "bukalapak": "https://www.bukalapak.com/",
        "blibli": "https://www.blibli.com/",
        "digimap": "https://www.digimap.co.id/",
    }
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referers.get(site, "https://www.google.com/"),
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
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
    options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.binary_location = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    if proxy:
        options.add_argument(f"--proxy-server={proxy}")
    return options

def clean_and_validate_prices(raw_prices):
    cleaned_prices = []
    for price in raw_prices:
        price_cleaned = re.sub(r"[^\d.,]", "", price.replace("Rp", "").strip())
        price_cleaned = price_cleaned.replace(",", ".")
        match = re.search(r"(\d+(?:\.\d+)*)", price_cleaned)
        if match:
            num = match.group().replace(".", "")
            try:
                cleaned_prices.append(int(num))
            except ValueError:
                continue
    
    valid_prices = [p for p in cleaned_prices if 1000 <= p <= 1_000_000_000]
    if not valid_prices:
        return {"max": "0", "min": "0", "avg": "0"}
    
    sorted_prices = sorted(valid_prices)
    min_price = sorted_prices[0]
    max_price = sorted_prices[-1]
    avg_price = round(sum(sorted_prices) / len(sorted_prices))
    
    return {
        "max": f"{max_price:,}".replace(",", "."),
        "min": f"{min_price:,}".replace(",", "."),
        "avg": f"{avg_price:,}".replace(",", ".")
    }

async def scrape_tokopedia_price(query):
    search_url = f"https://www.tokopedia.com/search?st=product&q={query.replace(' ', '+')}"
    try:
        response = requests.get(search_url, headers=get_headers("tokopedia"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
        return clean_and_validate_prices(raw_prices)
    except Exception as e:
        logger.error(f"❌ Gagal scraping Tokopedia: {e}")
        return {"max": "0", "min": "0", "avg": "0"}

async def scrape_bukalapak_price(query):
    search_url = f"https://www.bukalapak.com/products?search[keywords]={query.replace(' ', '%20')}"
    try:
        response = requests.get(search_url, headers=get_headers("bukalapak"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
        return clean_and_validate_prices(raw_prices)
    except Exception as e:
        logger.error(f"❌ Gagal scraping Bukalapak: {e}")
        return {"max": "0", "min": "0", "avg": "0"}

async def scrape_shopee_price(query):
    search_url = f"https://shopee.co.id/search?keyword={query.replace(' ', '%20')}"
    chrome_options = get_chrome_options(headless=True)
    driver = None
    try:
        service = Service(executable_path=os.getenv("CHROMEDRIVER_BIN", "/usr/bin/chromedriver"))
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(20)
        driver.get(search_url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        soup = BeautifulSoup(driver.page_source, "html.parser")
        raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
        return clean_and_validate_prices(raw_prices)
    except (TimeoutException, WebDriverException) as e:
        logger.error(f"❌ Gagal scraping Shopee (timeout atau driver error): {e}")
        return {"max": "0", "min": "0", "avg": "0"}
    except Exception as e:
        logger.error(f"❌ Gagal scraping Shopee: {e}")
        return {"max": "0", "min": "0", "avg": "0"}
    finally:
        if driver:
            driver.quit()

async def try_scrape_blibli(search_url, use_proxy=False, proxy=None, retries=2):
    chrome_options = get_chrome_options(headless=True, proxy=proxy if use_proxy else None)
    driver = None
    attempt = 0
    while attempt < retries:
        try:
            service = Service(executable_path=os.getenv("CHROMEDRIVER_BIN", "/usr/bin/chromedriver"))
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.set_page_load_timeout(20)
            driver.get(search_url)
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            soup = BeautifulSoup(driver.page_source, "html.parser")
            raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
            return clean_and_validate_prices(raw_prices)
        except (TimeoutException, WebDriverException) as e:
            attempt += 1
            logger.error(f"❌ Gagal scraping Blibli (percobaan {attempt}/{retries}): {e}")
            if attempt < retries:
                await asyncio.sleep(2)  # Tunggu sebelum retry
        except Exception as e:
            logger.error(f"❌ Gagal scraping Blibli{' dengan proxy ' + proxy if use_proxy else ''}: {e}")
            break
        finally:
            if driver:
                driver.quit()
    return {"max": "0", "min": "0", "avg": "0"}

async def scrape_blibli_price(query):
    search_url = f"https://www.blibli.com/cari/{query.replace(' ', '%20')}"
    result = await try_scrape_blibli(search_url, use_proxy=False)
    if result["avg"] != "0":
        return result
    logger.info("⚠️ Gagal tanpa proxy, mencoba dengan proxy...")
    while True:
        proxy = get_valid_proxy()
        if not proxy:
            logger.error("❌ Tidak ada proxy valid di Redis")
            break
        result = await try_scrape_blibli(search_url, use_proxy=True, proxy=proxy)
        if result["avg"] != "0":
            return result
        logger.info(f"Proxy {proxy} gagal, mencoba proxy berikutnya...")
    return {"max": "0", "min": "0", "avg": "0"}

async def scrape_digimap_price(query):
    search_url = f"https://www.digimap.co.id/search?type=product&q={query.replace(' ', '+')}"
    try:
        response = requests.get(search_url, headers=get_headers("digimap"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
        return clean_and_validate_prices(raw_prices)
    except Exception as e:
        logger.error(f"❌ Gagal scraping Digimap: {e}")
        return {"max": "0", "min": "0", "avg": "0"}

async def scrape_price(query):
    logger.info(f"🔍 Mencari harga untuk: {query}")
    cached_answer = find_price_in_history(query)
    if cached_answer:
        min_max = cached_answer.split(" - ")
        avg = str(round((int(min_max[0].replace("Rp", "").replace(".", "")) + int(min_max[1].replace("Rp", "").replace(".", ""))) / 2))
        return {"max": min_max[1].replace("Rp", ""), "min": min_max[0].replace("Rp", ""), "avg": f"{avg:,}".replace(",", ".")}

    tasks = [
        scrape_tokopedia_price(query),
        scrape_bukalapak_price(query),
        scrape_shopee_price(query),
        scrape_blibli_price(query),
        scrape_digimap_price(query),
    ]
    results = await asyncio.gather(*tasks)
    all_prices = []
    for result in results:
        if result["avg"] != "0":
            all_prices.extend([int(result["min"].replace(".", "")), int(result["max"].replace(".", "")), int(result["avg"].replace(".", ""))])
    
    if not all_prices:
        return None
    
    sorted_prices = sorted(all_prices)
    min_price = sorted_prices[0]
    max_price = sorted_prices[-1]
    avg_price = round(sum(sorted_prices) / len(sorted_prices))
    
    result = {
        "max": f"{max_price:,}".replace(",", "."),
        "min": f"{min_price:,}".replace(",", "."),
        "avg": f"{avg_price:,}".replace(",", ".")
    }
    save_price_history(query, f"Rp{result['min']} - Rp{result['max']}")
    return result