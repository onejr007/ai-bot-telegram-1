# proxy_scraper.py
import requests
from bs4 import BeautifulSoup
import redis
import schedule
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor
import os

# Koneksi Redis dengan autentikasi (gunakan variabel lingkungan)
REDIS_HOST = os.getenv("REDIS_HOST", "redis.railway.internal")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")  # Tambahkan password dari Railway
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
    referers = {
        "free-proxy-list": "https://free-proxy-list.net/",
        "proxylist": "https://proxylist.geonode.com/",
        "google": "https://google.com/",
        "proxyscrape": "https://api.proxyscrape.com/"
    }
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": referers.get(site, "https://google.com/"),
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
    }

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def test_proxy(proxy, timeout=5):
    test_url = "http://www.google.com"
    proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    try:
        response = requests.get(test_url, proxies=proxies, headers=get_headers("google"), timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        logger.debug(f"Proxy {proxy} gagal: {e}")
        return False

def fetch_geonode_proxies():
    url = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&country=ID"
    try:
        response = requests.get(url, headers=get_headers("proxylist"), timeout=10)
        response.raise_for_status()  # Raise exception untuk status kode bukan 200
        data = response.json()
        if not data.get("data"):
            logger.warning("⚠️ Tidak ada data proxy dari Geonode")
            return []
        proxies = [f"{item['ip']}:{item['port']}" for item in data["data"] if item.get("country") == "ID"]
        logger.debug(f"Geonode proxies: {len(proxies)} ditemukan")
        return proxies
    except requests.RequestException as e:
        logger.error(f"❌ Gagal mengambil proxy dari Geonode: {e}")
        return []
    except ValueError as e:
        logger.error(f"❌ Gagal parsing JSON dari Geonode: {e}")
        return []

def fetch_free_proxy_list():
    url = "https://free-proxy-list.net/"
    try:
        response = requests.get(url, headers=get_headers("free-proxy-list"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if not table:
            logger.warning("⚠️ Tidak ada tabel proxy di Free Proxy List")
            return []
        proxies = []
        for row in table.find("tbody").find_all("tr"):
            cols = row.find_all("td")
            if len(cols) > 3 and cols[3].text.strip() == "Indonesia":
                proxies.append(f"{cols[0].text.strip()}:{cols[1].text.strip()}")
        logger.debug(f"Free Proxy List proxies: {len(proxies)} ditemukan")
        return proxies
    except requests.RequestException as e:
        logger.error(f"❌ Gagal mengambil proxy dari Free Proxy List: {e}")
        return []
    except AttributeError as e:
        logger.error(f"❌ Gagal parsing HTML dari Free Proxy List: {e}")
        return []

def fetch_proxyscrape_proxies():
    url = "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=id"
    try:
        response = requests.get(url, headers=get_headers("proxyscrape"), timeout=10)
        response.raise_for_status()
        proxies = response.text.splitlines()
        logger.debug(f"Proxyscrape proxies: {len(proxies)} ditemukan")
        return [p for p in proxies if p.strip()]  # Hilangkan baris kosong
    except requests.RequestException as e:
        logger.error(f"❌ Gagal mengambil proxy dari Proxyscrape: {e}")
        return []

def scrape_and_store_proxies():
    logger.info("🔄 Scraping dan memvalidasi proxy...")
    all_proxies = list(set(fetch_geonode_proxies() + fetch_free_proxy_list() + fetch_proxyscrape_proxies()))
    logger.info(f"Total proxy sebelum validasi: {len(all_proxies)}")

    with ThreadPoolExecutor(max_workers=10) as executor:
        valid_proxies = [proxy for proxy, is_valid in zip(all_proxies, executor.map(test_proxy, all_proxies)) if is_valid]
    
    try:
        redis_client.delete("proxy_list")
        if valid_proxies:
            redis_client.rpush("proxy_list", *valid_proxies)
            logger.info(f"✅ Proxy valid tersimpan di Redis: {len(valid_proxies)}")
        else:
            logger.warning("⚠️ Tidak ada proxy valid ditemukan")
    except redis.RedisError as e:
        logger.error(f"❌ Gagal menyimpan proxy ke Redis: {e}")

# proxy_scraper.py (lanjutan)
def main():
    logger.info("🚀 Proxy Scraper Started...")
    scrape_and_store_proxies()  # Jalankan sekali saat start
    schedule.every(30).minutes.do(scrape_and_store_proxies)
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Error dalam loop utama: {e}")
            time.sleep(60)  # Tunggu lebih lama jika error

if __name__ == "__main__":
    main()