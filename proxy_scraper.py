# proxy_scraper.py
import requests
from bs4 import BeautifulSoup
import redis
import schedule
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor

# Koneksi Redis
redis_client = redis.Redis(host='redis.railway.internal', port=6379, db=0, decode_responses=True)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
]

def get_headers(site):
    """Menghasilkan headers dinamis berdasarkan situs"""
    referers = {
        "free-proxy-list": "https://free-proxy-list.net/",
        "proxylist": "https://proxylist.geonode.com/",
        "google": "https://google.com/",
        "proxyscrape": "https://api.proxyscrape.com/"
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

# Konfigurasi logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def test_proxy(proxy, timeout=5):
    test_url = "http://www.google.com"
    proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    try:
        response = requests.get(test_url, proxies=proxies, headers=get_headers("google"), timeout=timeout)
        return response.status_code == 200
    except:
        return False

def fetch_geonode_proxies():
    url = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&country=ID"
    try:
        response = requests.get(url, headers=get_headers("proxylist"), timeout=10)
        data = response.json()["data"]
        return [f"{item['ip']}:{item['port']}" for item in data if item["country"] == "ID"]
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari Geonode: {e}")
        return []

def fetch_free_proxy_list():
    url = "https://free-proxy-list.net/"
    try:
        response = requests.get(url, headers=get_headers("free-proxy-list"), timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        proxies = []
        for row in table.find("tbody").find_all("tr"):
            cols = row.find_all("td")
            if cols[3].text.strip() == "Indonesia":
                proxies.append(f"{cols[0].text.strip()}:{cols[1].text.strip()}")
        return proxies
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari Free Proxy List: {e}")
        return []

def fetch_proxyscrape_proxies():
    url = "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=id"
    try:
        response = requests.get(url, headers=get_headers("proxyscrape"), timeout=10)
        return response.text.splitlines()
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari Proxyscrape: {e}")
        return []

def scrape_and_store_proxies():
    logger.info("🔄 Scraping dan memvalidasi proxy...")
    # Ambil proxy baru
    all_proxies = list(set(fetch_geonode_proxies() + fetch_free_proxy_list() + fetch_proxyscrape_proxies()))
    logger.info(f"Total proxy sebelum validasi: {len(all_proxies)}")

    # Validasi proxy secara paralel
    with ThreadPoolExecutor(max_workers=10) as executor:
        valid_proxies = [proxy for proxy, is_valid in zip(all_proxies, executor.map(test_proxy, all_proxies)) if is_valid]
    
    # Update Redis
    redis_client.delete("proxy_list")
    if valid_proxies:
        redis_client.rpush("proxy_list", *valid_proxies)
        logger.info(f"✅ Proxy valid tersimpan di Redis: {len(valid_proxies)}")
    else:
        logger.warning("⚠️ Tidak ada proxy valid ditemukan")

def main():
    logger.info("🚀 Proxy Scraper Started...")
    scrape_and_store_proxies()  # Jalankan sekali saat start
    schedule.every(30).minutes.do(scrape_and_store_proxies)
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()