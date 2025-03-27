import aiohttp
from bs4 import BeautifulSoup
import redis
import random
import asyncio
import re
import os
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
    logger.info(f"🌐 Memulai scraping Tokopedia - URL awal: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("tokopedia"), timeout=aiohttp.ClientTimeout(total=15)) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"🔄 Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"💰 Harga mentah ditemukan: {raw_prices}")
                result = clean_and_validate_prices(raw_prices)
                logger.info(f"✅ Harga setelah validasi: {result}")
                return result
        except Exception as e:
            logger.error(f"❌ Gagal scraping Tokopedia: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_bukalapak_price(query):
    search_url = f"https://www.bukalapak.com/products?search[keywords]={query.replace(' ', '%20')}"
    logger.info(f"🌐 Memulai scraping Bukalapak - URL awal: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("bukalapak"), timeout=aiohttp.ClientTimeout(total=15)) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"🔄 Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"💰 Harga mentah ditemukan: {raw_prices}")
                result = clean_and_validate_prices(raw_prices)
                logger.info(f"✅ Harga setelah validasi: {result}")
                return result
        except Exception as e:
            logger.error(f"❌ Gagal scraping Bukalapak: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_shopee_price(query):
    search_url = f"https://shopee.co.id/search?keyword={query.replace(' ', '%20')}"
    logger.info(f"🌐 Memulai scraping Shopee - URL awal: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("shopee"), timeout=aiohttp.ClientTimeout(total=15)) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"🔄 Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"💰 Harga mentah ditemukan: {raw_prices}")
                result = clean_and_validate_prices(raw_prices)
                logger.info(f"✅ Harga setelah validasi: {result}")
                return result
        except Exception as e:
            logger.error(f"❌ Gagal scraping Shopee: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_blibli_price(query):
    search_url = f"https://www.blibli.com/cari/{query.replace(' ', '%20')}"
    logger.info(f"🌐 Memulai scraping Blibli - URL awal: {search_url}")
    proxy = get_valid_proxy()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                search_url,
                headers=get_headers("blibli"),
                proxy=f"http://{proxy}" if proxy else None,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"🔄 Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"💰 Harga mentah ditemukan: {raw_prices}")
                result = clean_and_validate_prices(raw_prices)
                logger.info(f"✅ Harga setelah validasi: {result}")
                return result
        except Exception as e:
            logger.error(f"❌ Gagal scraping Blibli{' dengan proxy ' + proxy if proxy else ''}: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_digimap_price(query):
    search_url = f"https://www.digimap.co.id/search?type=product&q={query.replace(' ', '+')}"
    logger.info(f"🌐 Memulai scraping Digimap - URL awal: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("digimap"), timeout=aiohttp.ClientTimeout(total=15)) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"🔄 Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = re.findall(r"Rp[\s]?\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"💰 Harga mentah ditemukan: {raw_prices}")
                result = clean_and_validate_prices(raw_prices)
                logger.info(f"✅ Harga setelah validasi: {result}")
                return result
        except Exception as e:
            logger.error(f"❌ Gagal scraping Digimap: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_price(query):
    logger.info(f"🔍 Mencari harga untuk: {query}")
    cached_answer = find_price_in_history(query)
    if cached_answer:
        min_max = cached_answer.split(" - ")
        avg = str(round((int(min_max[0].replace("Rp", "").replace(".", "")) + int(min_max[1].replace("Rp", "").replace(".", ""))) / 2))
        result = {"max": min_max[1].replace("Rp", ""), "min": min_max[0].replace("Rp", ""), "avg": f"{avg:,}".replace(",", ".")}
        logger.info(f"🔄 Menggunakan cache: {result}")
        return result

    tasks = [
        asyncio.wait_for(scrape_tokopedia_price(query), timeout=15),
        asyncio.wait_for(scrape_bukalapak_price(query), timeout=15),
        asyncio.wait_for(scrape_shopee_price(query), timeout=15),
        asyncio.wait_for(scrape_blibli_price(query), timeout=15),
        asyncio.wait_for(scrape_digimap_price(query), timeout=15),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    valid_results = [r for r in results if not isinstance(r, Exception) and r["avg"] != "0"]
    if not valid_results:
        logger.info(f"❌ Tidak ada hasil valid untuk {query}")
        return None
    
    avg_values = [int(r["avg"].replace(".", "")) for r in valid_results]
    min_avg = min(avg_values)
    max_avg = max(avg_values)
    avg_avg = round(sum(avg_values) / len(avg_values))
    
    result = {
        "max": f"{max_avg:,}".replace(",", "."),
        "min": f"{min_avg:,}".replace(",", "."),
        "avg": f"{avg_avg:,}".replace(",", ".")
    }
    save_price_history(query, f"Rp{result['min']} - Rp{result['max']}")
    logger.info(f"✅ Hasil akhir untuk {query}: {result}")
    return result