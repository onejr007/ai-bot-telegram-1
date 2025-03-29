import aiohttp
from bs4 import BeautifulSoup
import redis
import random
import asyncio
import re
import os
import logging
import time
import json
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
        "blibli": "https://www.blibli.com/",
        "bukalapak": "https://www.bukalapak.com/",
        "carousell": "https://www.carousell.co.id/",
        "priceza": "https://www.priceza.co.id/",
        "google": "https://www.google.com/",
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
        score = int(redis_client.hget("proxy_scores", proxy) or 0)
        if score < -5:
            redis_client.lrem("proxy_list", 0, proxy)
            continue
        redis_client.rpush("proxy_list", proxy)
        logger.info(f"ℹ️ Menggunakan proxy {proxy} (skor: {score}) pada percobaan {attempt + 1}.")
        return proxy
    return None

def update_proxy_score(proxy, success):
    if proxy:
        score = int(redis_client.hget("proxy_scores", proxy) or 0)
        new_score = score + (1 if success else -1)
        redis_client.hset("proxy_scores", proxy, new_score)
        if new_score < -5:
            redis_client.lrem("proxy_list", 0, proxy)

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

def extract_prices_from_json_scripts(soup, site):
    prices = []
    for script in soup.find_all("script"):
        if script.string:
            try:
                # Cari JSON dalam script
                json_data = re.search(r'({.*})', script.string, re.DOTALL)
                if json_data:
                    data = json.loads(json_data.group(1))
                    # Cari harga dalam struktur JSON umum
                    for key in ["price", "harga", "amount", "value"]:
                        if isinstance(data, dict) and key in data:
                            price_str = str(data[key])
                            match = re.search(r"(\d+[\.,]\d+[\.,]?\d*)", price_str)
                            if match:
                                prices.append(int(re.sub(r"[^\d]", "", match.group(1))))
            except (json.JSONDecodeError, ValueError):
                continue
    logger.info(f"{site}: Harga dari JSON: {prices}")
    return prices

def clean_and_validate_prices(raw_prices, text, site, max_items=10):
    cleaned_prices = []
    
    # Metode 1: Angka dengan Rp di dekatnya
    for price in raw_prices[:max_items]:
        price_text = price.get_text(strip=True) if hasattr(price, "get_text") else str(price)
        # Dukungan "juta" atau "jt"
        if "juta" in price_text.lower() or "jt" in price_text.lower():
            match = re.search(r"(\d+[\.,]?\d*)\s*(juta|jt)", price_text, re.IGNORECASE)
            if match:
                num = float(match.group(1).replace(",", ".")) * 1000000
                cleaned_prices.append(int(num))
                continue
        # Rp diikuti angka dengan koma/titik
        match = re.search(r"Rp\s*(\d+[\.,]\d+[\.,]?\d*)", price_text)
        if match:
            price_cleaned = re.sub(r"[^\d]", "", match.group(1))
            try:
                num = int(price_cleaned)
                if num > 10000:
                    cleaned_prices.append(num)
            except ValueError:
                continue
    
    # Metode 2: Angka dengan koma/titik dari teks keseluruhan
    text_prices = re.findall(r"\b\d+[\.,]\d+[\.,]?\d*\b", text)
    for price_str in text_prices:
        price_cleaned = re.sub(r"[^\d]", "", price_str)
        try:
            num = int(price_cleaned)
            if num > 10000 and num not in cleaned_prices:  # Hindari duplikat
                cleaned_prices.append(num)
        except ValueError:
            continue
    
    # Tambahkan harga dari JSON jika ada
    soup = BeautifulSoup(text, "html.parser")
    json_prices = extract_prices_from_json_scripts(soup, site)
    cleaned_prices.extend([p for p in json_prices if p > 10000 and p not in cleaned_prices])
    
    logger.info(f"{site}: Harga integer setelah pembersihan: {cleaned_prices}")
    
    if not cleaned_prices:
        logger.info(f"{site}: Tidak ada harga valid ditemukan")
        return {"max": "0", "min": "0", "avg": "0"}
    
    lower_bound, upper_bound = calculate_iqr_range(cleaned_prices)
    valid_prices = cleaned_prices if lower_bound is None else [p for p in cleaned_prices if lower_bound <= p <= upper_bound]
    
    if not valid_prices:
        return {"max": "0", "min": "0", "avg": "0"}
    
    min_price = min(valid_prices)
    max_price = max(valid_prices)
    avg_price = round(sum(valid_prices) / len(valid_prices))
    
    result = {
        "max": "{:,.0f}".format(max_price).replace(",", "."),
        "min": "{:,.0f}".format(min_price).replace(",", "."),
        "avg": "{:,.0f}".format(avg_price).replace(",", ".")
    }
    logger.info(f"{site}: Harga setelah validasi: {result}")
    return result

async def scrape_tokopedia_price(query):
    start_time = time.time()
    search_url = f"https://www.tokopedia.com/search?st=product&q={query.replace(' ', '+')}"
    logger.info(f"Tokopedia: Memulai scraping - URL: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("tokopedia"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                if "captcha" in str(response.url).lower() or response.status == 403:
                    logger.warning("Tokopedia: Terdeteksi CAPTCHA atau pemblokiran")
                    return {"max": "0", "min": "0", "avg": "0"}
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select("div[data-testid='lblProductPrice']")
                result = clean_and_validate_prices(raw_prices, text, "Tokopedia")
                logger.info(f"Tokopedia: Selesai dalam {time.time() - start_time:.2f} detik")
                return result
        except Exception as e:
            logger.error(f"Tokopedia: Gagal scraping: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_blibli_price(query, retries=3):
    start_time = time.time()
    search_url = f"https://www.blibli.com/cari/{query.replace(' ', '%20')}"
    logger.info(f"Blibli: Memulai scraping - URL: {search_url}")
    for attempt in range(retries + 1):
        proxy = get_valid_proxy() if attempt < retries else None
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    search_url,
                    headers=get_headers("blibli"),
                    proxy=f"http://{proxy}" if proxy else None,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if "captcha" in str(response.url).lower() or response.status == 403:
                        logger.warning("Blibli: Terdeteksi CAPTCHA atau pemblokiran")
                        continue
                    text = await response.text()
                    soup = BeautifulSoup(text, "html.parser")
                    raw_prices = soup.select(".price__value")
                    result = clean_and_validate_prices(raw_prices, text, "Blibli")
                    update_proxy_score(proxy, True)
                    logger.info(f"Blibli: Selesai dalam {time.time() - start_time:.2f} detik")
                    return result
            except Exception as e:
                logger.error(f"Blibli: Gagal scraping{' dengan proxy ' + proxy if proxy else ''} pada percobaan {attempt + 1}: {e}")
                update_proxy_score(proxy, False)
    return {"max": "0", "min": "0", "avg": "0"}

async def scrape_bukalapak_price(query, retries=3):
    start_time = time.time()
    search_url = f"https://www.bukalapak.com/products?search[keywords]={query.replace(' ', '+')}"
    logger.info(f"Bukalapak: Memulai scraping - URL: {search_url}")
    for attempt in range(retries + 1):
        proxy = get_valid_proxy() if attempt < retries else None
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    search_url,
                    headers=get_headers("bukalapak"),
                    proxy=f"http://{proxy}" if proxy else None,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if "captcha" in str(response.url).lower() or response.status == 403:
                        logger.warning("Bukalapak: Terdeteksi CAPTCHA atau pemblokiran")
                        continue
                    text = await response.text()
                    soup = BeautifulSoup(text, "html.parser")
                    raw_prices = soup.select(".bl-product-card__price")
                    result = clean_and_validate_prices(raw_prices, text, "Bukalapak")
                    update_proxy_score(proxy, True)
                    logger.info(f"Bukalapak: Selesai dalam {time.time() - start_time:.2f} detik")
                    return result
            except Exception as e:
                logger.error(f"Bukalapak: Gagal scraping{' dengan proxy ' + proxy if proxy else ''} pada percobaan {attempt + 1}: {e}")
                update_proxy_score(proxy, False)
    return {"max": "0", "min": "0", "avg": "0"}

async def scrape_carousell_price(query):
    start_time = time.time()
    search_url = f"https://www.carousell.co.id/search/{query.replace(' ', '%20')}"
    logger.info(f"Carousell: Memulai scraping - URL: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("carousell"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                if "captcha" in str(response.url).lower() or response.status == 403:
                    logger.warning("Carousell: Terdeteksi CAPTCHA atau pemblokiran")
                    return {"max": "0", "min": "0", "avg": "0"}
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".D_V_f")
                result = clean_and_validate_prices(raw_prices, text, "Carousell")
                logger.info(f"Carousell: Selesai dalam {time.time() - start_time:.2f} detik")
                return result
        except Exception as e:
            logger.error(f"Carousell: Gagal scraping: {e}")
            return {"max": "0", "min": "0", "avg": "0"}

async def scrape_priceza_price(query):
    start_time = time.time()
    search_url = f"https://www.priceza.co.id/s/harga/{query.replace(' ', '-')}"
    logger.info(f"Priceza: Memulai scraping (fallback) - URL: {search_url}")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(search_url, headers=get_headers("priceza"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                raw_prices = soup.select(".price-val")
                result = clean_and_validate_prices(raw_prices, text, "Priceza")
                logger.info(f"Priceza: Selesai dalam {time.time() - start_time:.2f} detik")
                return result
        except Exception as e:
            logger.error(f"Priceza: Gagal scraping: {e}")
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
        asyncio.wait_for(scrape_tokopedia_price(query), timeout=10),
        asyncio.wait_for(scrape_blibli_price(query), timeout=10),
        asyncio.wait_for(scrape_bukalapak_price(query), timeout=10),
        asyncio.wait_for(scrape_carousell_price(query), timeout=10),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_valid_prices = []
    for i, result in enumerate(results):
        if not isinstance(result, Exception) and result["avg"] != "0":
            site = ["Tokopedia", "Blibli", "Bukalapak", "Carousell"][i]
            prices = [
                int(result["min"].replace(".", "")),
                int(result["max"].replace(".", "")),
                int(result["avg"].replace(".", ""))
            ]
            all_valid_prices.extend(prices)
            logger.info(f"{site}: Menambahkan harga valid ke hasil akhir: {prices}")
    
    if not all_valid_prices:
        logger.info(f"❌ Tidak ada hasil valid dari situs utama, mencoba fallback ke Priceza")
        fallback_result = await scrape_priceza_price(query)
        if fallback_result["avg"] != "0":
            all_valid_prices = [
                int(fallback_result["min"].replace(".", "")),
                int(fallback_result["max"].replace(".", "")),
                int(fallback_result["avg"].replace(".", ""))
            ]
    
    if not all_valid_prices:
        logger.info(f"❌ Tidak ada hasil valid untuk {query} dari semua situs")
        return None
    
    min_price = round_to_nearest_hundred_thousand(min(all_valid_prices))
    max_price = round_to_nearest_hundred_thousand(max(all_valid_prices))
    avg_price = round_to_nearest_hundred_thousand(sum(all_valid_prices) / len(all_valid_prices))
    
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