import aiohttp
from bs4 import BeautifulSoup
import redis
import random
import asyncio
import re
import os
import logging
from collections import Counter
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

def clean_and_validate_prices(raw_prices, site):
    cleaned_prices = []
    for price in raw_prices:
        price_text = price.get_text(strip=True) if hasattr(price, "get_text") else str(price)
        match = re.search(r"Rp\s*(\d+(?:[.,]\d{3})*)", price_text)
        if match:
            price_cleaned = re.sub(r"[^\d]", "", match.group(1))
            try:
                num = int(price_cleaned)
                if 1000 <= num <= 100_000_000:
                    cleaned_prices.append(num)
            except ValueError:
                continue
    logger.info(f"{site}: Harga integer setelah pembersihan: {cleaned_prices}")
    
    if not cleaned_prices:
        logger.info(f"{site}: Tidak ada harga valid ditemukan")
        return {"max": "0", "min": "0", "avg": "0"}
    
    price_counts = Counter(cleaned_prices)
    most_common_price = price_counts.most_common(1)[0][0]
    rational_range = (most_common_price * 0.7, most_common_price * 1.3)
    
    valid_prices = [p for p in cleaned_prices if rational_range[0] <= p <= rational_range[1]]
    logger.info(f"{site}: Harga rasional (rentang {rational_range[0]:,}-{rational_range[1]:,}): {valid_prices}")
    
    if not valid_prices:
        logger.info(f"{site}: Tidak ada harga rasional ditemukan")
        return {"max": "0", "min": "0", "avg": "0"}
    
    sorted_prices = sorted(valid_prices)
    min_price = sorted_prices[0]
    max_price = sorted_prices[-1]
    avg_price = round(sum(sorted_prices) / len(sorted_prices))
    
    result = {
        "max": "{:,.0f}".format(max_price).replace(",", "."),
        "min": "{:,.0f}".format(min_price).replace(",", "."),
        "avg": "{:,.0f}".format(avg_price).replace(",", ".")
    }
    logger.info(f"{site}: Harga setelah validasi: {result}")
    return result

async def scrape_tokopedia_price(query):
    search_url = f"https://www.tokopedia.com/search?st=product&q={query.replace(' ', '+')}"
    logger.info(f"Tokopedia: Memulai scraping - URL awal: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("tokopedia"), timeout=aiohttp.ClientTimeout(total=15)) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"Tokopedia: Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"Tokopedia: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                return clean_and_validate_prices(raw_prices, "Tokopedia")
        except Exception as e:
            logger.error(f"Tokopedia: Gagal scraping: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_bukalapak_price(query):
    search_url = f"https://www.bukalapak.com/products?search[keywords]={query.replace(' ', '%20')}"
    logger.info(f"Bukalapak: Memulai scraping - URL awal: {search_url}")
    proxy = get_valid_proxy()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                search_url,
                headers=get_headers("bukalapak"),
                proxy=f"http://{proxy}" if proxy else None,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"Bukalapak: Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".bl-product-card__price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"Bukalapak: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                return clean_and_validate_prices(raw_prices, "Bukalapak")
        except Exception as e:
            logger.error(f"Bukalapak: Gagal scraping{' dengan proxy ' + proxy if proxy else ''}: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_shopee_price(query):
    search_url = f"https://shopee.co.id/search?keyword={query.replace(' ', '%20')}"
    logger.info(f"Shopee: Memulai scraping - URL awal: {search_url}")
    proxy = get_valid_proxy()
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(
                search_url,
                headers=get_headers("shopee"),
                proxy=f"http://{proxy}" if proxy else None,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"Shopee: Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".shopee-search-item-result__item .price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"Shopee: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                return clean_and_validate_prices(raw_prices, "Shopee")
        except Exception as e:
            logger.error(f"Shopee: Gagal scraping{' dengan proxy ' + proxy if proxy else ''}: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_blibli_price(query):
    search_url = f"https://www.blibli.com/cari/{query.replace(' ', '%20')}"
    logger.info(f"Blibli: Memulai scraping - URL awal: {search_url}")
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
                    logger.info(f"Blibli: Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".product__price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"Blibli: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                return clean_and_validate_prices(raw_prices, "Blibli")
        except Exception as e:
            logger.error(f"Blibli: Gagal scraping{' dengan proxy ' + proxy if proxy else ''}: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_digimap_price(query):
    search_url = f"https://www.digimap.co.id/search?type=product&q={query.replace(' ', '+')}"
    logger.info(f"Digimap: Memulai scraping - URL awal: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("digimap"), timeout=aiohttp.ClientTimeout(total=15)) as response:
                redirected_url = str(response.url)
                if redirected_url != search_url:
                    logger.info(f"Digimap: Redirected ke: {redirected_url}")
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"Digimap: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                return clean_and_validate_prices(raw_prices, "Digimap")
        except Exception as e:
            logger.error(f"Digimap: Gagal scraping: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_price(query):
    logger.info(f"🔍 Mencari harga untuk: {query}")
    cached_answer = find_price_in_history(query)
    if cached_answer:
        min_max = cached_answer.split(" - ")
        avg = round((int(min_max[0].replace("Rp", "").replace(".", "")) + int(min_max[1].replace("Rp", "").replace(".", ""))) / 2)
        result = {
            "max": "{:,.0f}".format(int(min_max[1].replace("Rp", "").replace(".", ""))).replace(",", "."),
            "min": "{:,.0f}".format(int(min_max[0].replace("Rp", "").replace(".", ""))).replace(",", "."),
            "avg": "{:,.0f}".format(avg).replace(",", ".")
        }
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
    
    all_prices = []
    for i, result in enumerate(results):
        if not isinstance(result, Exception) and result["avg"] != "0":
            site = ["Tokopedia", "Bukalapak", "Shopee", "Blibli", "Digimap"][i]
            prices = [int(result["min"].replace(".", "")), int(result["max"].replace(".", "")), int(result["avg"].replace(".", ""))]
            all_prices.extend(prices)
    
    if not all_prices:
        logger.info(f"❌ Tidak ada hasil valid untuk {query} dari semua situs")
        return None
    
    price_counts = Counter(all_prices)
    most_common_price = price_counts.most_common(1)[0][0]
    rational_range = (most_common_price * 0.7, most_common_price * 1.3)
    valid_prices = [p for p in all_prices if rational_range[0] <= p <= rational_range[1]]
    
    if not valid_prices:
        logger.info(f"❌ Tidak ada harga rasional untuk {query} dari semua situs")
        return None
    
    min_avg = min(valid_prices)
    max_avg = max(valid_prices)
    avg_avg = round(sum(valid_prices) / len(valid_prices))
    
    result = {
        "max": "{:,.0f}".format(max_avg).replace(",", "."),
        "min": "{:,.0f}".format(min_avg).replace(",", "."),
        "avg": "{:,.0f}".format(avg_avg).replace(",", ".")
    }
    save_price_history(query, f"Rp{result['min']} - Rp{result['max']}")
    logger.info(f"✅ Hasil akhir untuk {query}: {result}")
    return result