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
        "lazada": "https://www.lazada.co.id/",
        "blibli": "https://www.blibli.com/",
        "samsung": "https://www.samsung.com/id/",
        "shopee": "https://shopee.co.id/",
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

def get_valid_proxy(max_retries=3):
    for attempt in range(max_retries):
        proxy = redis_client.lpop("proxy_list")
        if not proxy:
            logger.warning("⚠️ Tidak ada proxy tersedia di Redis.")
            return None
        redis_client.rpush("proxy_list", proxy)
        logger.info(f"ℹ️ Menggunakan proxy {proxy} pada percobaan {attempt + 1}.")
        return proxy
    logger.warning(f"⚠️ Tidak ada proxy valid setelah {max_retries} percobaan.")
    return None

def calculate_iqr_range(prices):
    if not prices or len(prices) < 4:
        return None, None
    sorted_prices = sorted(prices)
    n = len(sorted_prices)
    q1_idx = n // 4
    q3_idx = (3 * n) // 4
    q1 = sorted_prices[q1_idx]
    q3 = sorted_prices[q3_idx]
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr
    return max(0, lower_bound), upper_bound

def clean_and_validate_prices(raw_prices, site):
    cleaned_prices = []
    for price in raw_prices:
        price_text = price.get_text(strip=True) if hasattr(price, "get_text") else str(price)
        match = re.search(r"Rp\s*(\d+(?:[.,]\d{3})*)", price_text)
        if match:
            price_cleaned = re.sub(r"[^\d]", "", match.group(1))
            try:
                num = int(price_cleaned)
                if num >= 1000000:  # Harga minimal 1 juta untuk flagship
                    cleaned_prices.append(num)
            except ValueError:
                continue
    logger.info(f"{site}: Harga integer setelah pembersihan: {cleaned_prices}")
    
    if not cleaned_prices:
        logger.info(f"{site}: Tidak ada harga valid ditemukan")
        return {"max": "0", "min": "0", "avg": "0"}
    
    lower_bound, upper_bound = calculate_iqr_range(cleaned_prices)
    if lower_bound is None:
        valid_prices = cleaned_prices
        logger.info(f"{site}: Data kurang dari 4, menggunakan semua harga: {valid_prices}")
    else:
        valid_prices = [p for p in cleaned_prices if lower_bound <= p <= upper_bound]
        logger.info(f"{site}: Harga rasional (rentang {lower_bound:,}-{upper_bound:,}): {valid_prices}")
    
    if not valid_prices:
        logger.info(f"{site}: Tidak ada harga rasional ditemukan")
        return {"max": "0", "min": "0", "avg": "0"}
    
    sorted_prices = sorted(valid_prices)
    min_price = sorted_prices[0]
    max_price = sorted_prices[-1]
    avg_price = round(sum(sorted_prices) / len(sorted_prices))
    
    # Validasi min tidak terlalu jauh dari avg (minimal 50% dari avg)
    if min_price < avg_price * 0.5:
        min_price = round(avg_price * 0.5)
        logger.info(f"{site}: Harga min disesuaikan ke {min_price:,} (50% dari avg)")
    
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

async def scrape_lazada_price(query, retries=3):
    search_url = f"https://www.lazada.co.id/catalog/?q={query.replace(' ', '+')}"
    logger.info(f"Lazada: Memulai scraping - URL awal: {search_url}")
    for attempt in range(retries):
        proxy = get_valid_proxy()
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    search_url,
                    headers=get_headers("lazada"),
                    proxy=f"http://{proxy}" if proxy else None,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    redirected_url = str(response.url)
                    if redirected_url != search_url:
                        logger.info(f"Lazada: Redirected ke: {redirected_url}")
                    text = await response.text()
                    soup = BeautifulSoup(text, "html.parser")
                    raw_prices = soup.select(".price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                    logger.info(f"Lazada: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                    return clean_and_validate_prices(raw_prices, "Lazada")
            except Exception as e:
                logger.error(f"Lazada: Gagal scraping{' dengan proxy ' + proxy if proxy else ''} pada percobaan {attempt + 1}: {e}")
                if proxy:
                    logger.info(f"🗑️ Proxy {proxy} gagal, dihapus dari Redis.")
                    redis_client.lrem("proxy_list", 0, proxy)
    logger.error(f"Lazada: Gagal setelah {retries} percobaan.")
    return {"max": "0", "min": "0", "avg": "0"}

async def scrape_blibli_price(query, retries=3):
    search_url = f"https://www.blibli.com/cari/{query.replace(' ', '%20')}"
    logger.info(f"Blibli: Memulai scraping - URL awal: {search_url}")
    for attempt in range(retries):
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
                logger.error(f"Blibli: Gagal scraping{' dengan proxy ' + proxy if proxy else ''} pada percobaan {attempt + 1}: {e}")
                if proxy:
                    logger.info(f"🗑️ Proxy {proxy} gagal, dihapus dari Redis.")
                    redis_client.lrem("proxy_list", 0, proxy)
    logger.error(f"Blibli: Gagal setelah {retries} percobaan.")
    return {"max": "0", "min": "0", "avg": "0"}

async def scrape_samsung_price(query):
    # Langsung ke halaman produk S23 Ultra
    search_url = "https://www.samsung.com/id/smartphones/galaxy-s23-ultra/buy/"
    logger.info(f"Samsung: Memulai scraping - URL awal: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("samsung"), timeout=aiohttp.ClientTimeout(total=15)) as response:
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                logger.info(f"Samsung: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                return clean_and_validate_prices(raw_prices, "Samsung")
        except Exception as e:
            logger.error(f"Samsung: Gagal scraping: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_shopee_price(query, retries=3):
    search_url = f"https://shopee.co.id/search?keyword={query.replace(' ', '%20')}"
    logger.info(f"Shopee: Memulai scraping - URL awal: {search_url}")
    for attempt in range(retries):
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
                    raw_prices = soup.select(".price") or re.findall(r"Rp\s*\d+(?:[.,]\d+)*", soup.get_text())
                    logger.info(f"Shopee: Harga mentah ditemukan: {[p.get_text(strip=True) if hasattr(p, 'get_text') else p for p in raw_prices]}")
                    return clean_and_validate_prices(raw_prices, "Shopee")
            except Exception as e:
                logger.error(f"Shopee: Gagal scraping{' dengan proxy ' + proxy if proxy else ''} pada percobaan {attempt + 1}: {e}")
                if proxy:
                    logger.info(f"🗑️ Proxy {proxy} gagal, dihapus dari Redis.")
                    redis_client.lrem("proxy_list", 0, proxy)
    logger.error(f"Shopee: Gagal setelah {retries} percobaan.")
    return {"max": "0", "min": "0", "avg": "0"}

def round_to_nearest_hundred_thousand(value):
    return round(value / 100000) * 100000

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
        asyncio.wait_for(scrape_lazada_price(query), timeout=15),
        asyncio.wait_for(scrape_blibli_price(query), timeout=15),
        asyncio.wait_for(scrape_samsung_price(query), timeout=15),
        asyncio.wait_for(scrape_shopee_price(query), timeout=15),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_valid_prices = []
    for i, result in enumerate(results):
        if not isinstance(result, Exception) and result["avg"] != "0":
            site = ["Tokopedia", "Lazada", "Blibli", "Samsung", "Shopee"][i]
            prices = [
                int(result["min"].replace(".", "")),
                int(result["max"].replace(".", "")),
                int(result["avg"].replace(".", ""))
            ]
            all_valid_prices.extend(prices)
            logger.info(f"{site}: Menambahkan harga valid ke hasil akhir: {prices}")
    
    if not all_valid_prices:
        logger.info(f"❌ Tidak ada hasil valid untuk {query} dari semua situs")
        return None
    
    min_price = round_to_nearest_hundred_thousand(min(all_valid_prices))
    max_price = round_to_nearest_hundred_thousand(max(all_valid_prices))
    avg_price = round_to_nearest_hundred_thousand(sum(all_valid_prices) / len(all_valid_prices))
    
    # Validasi min tidak terlalu jauh dari avg
    if min_price < avg_price * 0.5:
        min_price = round(avg_price * 0.5)
        logger.info(f"Hasil akhir: Harga min disesuaikan ke {min_price:,} (50% dari avg)")
    
    result = {
        "max": "{:,.0f}".format(max_price).replace(",", "."),
        "min": "{:,.0f}".format(min_price).replace(",", "."),
        "avg": "{:,.0f}".format(avg_price).replace(",", ".")
    }
    save_price_history(query, f"Rp{result['min']} - Rp{result['max']}")
    logger.info(f"✅ Hasil akhir untuk {query}: {result}")
    return result