import requests
from bs4 import BeautifulSoup
import redis
import random
import logging
from concurrent.futures import ThreadPoolExecutor
import os

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
        "free-proxy-list": "https://free-proxy-list.net",
        "proxylist": "https://proxylist.geonode.com",
        "google": "https://google.com",
        "proxyscrape": "https://api.proxyscrape.com",
        "blibli": "https://www.blibli.com",
        "openproxy": "https://openproxy.space",
        "proxy-list": "https://www.proxy-list.download",
        "httpbin": "http://httpbin.org",
        "proxynova": "https://www.proxynova.com",
        "sslproxies": "https://www.sslproxies.org"
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

def test_proxy(proxy, timeout=3):
    test_url = "http://httpbin.org/ip"
    proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    try:
        response = requests.get(test_url, proxies=proxies, headers=get_headers("httpbin"), timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        logger.debug(f"Proxy {proxy} gagal: {e}")
        return False

def fetch_openproxyspace_proxies():
    url = "https://openproxy.space/list/http"
    try:
        response = requests.get(url, headers=get_headers("openproxy"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        proxies = [item.text.strip() for item in soup.select(".list .item") if ":" in item.text.strip()]
        return proxies
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari OpenProxySpace: {e}")
        return []

def fetch_proxy_list_download():
    url = "https://www.proxy-list.download/api/v1/get?type=http&country=ID"
    try:
        response = requests.get(url, headers=get_headers("proxy-list"), timeout=10)
        response.raise_for_status()
        return [p for p in response.text.splitlines() if p.strip()]
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari Proxy-List.download: {e}")
        return []

def fetch_geonode_proxies():
    url = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&country=ID"
    try:
        response = requests.get(url, headers=get_headers("proxylist"), timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data.get("data"):
            logger.warning("⚠️ Tidak ada data proxy dari Geonode")
            return []
        return [f"{item['ip']}:{item['port']}" for item in data["data"] if item.get("country") == "ID"]
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari Geonode: {e}")
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
        return proxies
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari Free Proxy List: {e}")
        return []

def fetch_proxyscrape_proxies():
    url = "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=id"
    try:
        response = requests.get(url, headers=get_headers("proxyscrape"), timeout=10)
        response.raise_for_status()
        return [p for p in response.text.splitlines() if p.strip()]
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari Proxyscrape: {e}")
        return []

def fetch_proxynova_proxies():
    url = "https://www.proxynova.com/proxy-server-list/country-id/"
    try:
        response = requests.get(url, headers=get_headers("proxynova"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        proxies = []
        table = soup.find("table", {"id": "tbl_proxy_list"})
        if not table:
            logger.warning("⚠️ Tidak ada tabel proxy di ProxyNova")
            return []
        for row in table.find("tbody").find_all("tr"):
            cols = row.find_all("td")
            if len(cols) >= 2:
                ip = cols[0].text.strip()
                port = cols[1].text.strip()
                if ip and port:
                    proxies.append(f"{ip}:{port}")
        return proxies
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari ProxyNova: {e}")
        return []

def fetch_sslproxies_proxies():
    url = "https://www.sslproxies.org/"
    try:
        response = requests.get(url, headers=get_headers("sslproxies"), timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        proxies = []
        table = soup.find("table", {"class": "table"})
        if not table:
            logger.warning("⚠️ Tidak ada tabel proxy di SSL Proxies")
            return []
        for row in table.find("tbody").find_all("tr"):
            cols = row.find_all("td")
            if len(cols) > 3 and cols[3].text.strip() == "ID":
                proxies.append(f"{cols[0].text.strip()}:{cols[1].text.strip()}")
        return proxies
    except Exception as e:
        logger.error(f"❌ Gagal mengambil proxy dari SSL Proxies: {e}")
        return []

def scrape_and_store_proxies():
    logger.info("🔄 Scraping dan memvalidasi proxy...")
    
    geonode_proxies = fetch_geonode_proxies()
    logger.info(f"📊 Jumlah proxy dari Geonode: {len(geonode_proxies)}")
    
    free_proxy_list_proxies = fetch_free_proxy_list()
    logger.info(f"📊 Jumlah proxy dari Free Proxy List: {len(free_proxy_list_proxies)}")
    
    proxyscrape_proxies = fetch_proxyscrape_proxies()
    logger.info(f"📊 Jumlah proxy dari Proxyscrape: {len(proxyscrape_proxies)}")
    
    openproxy_proxies = fetch_openproxyspace_proxies()
    logger.info(f"📊 Jumlah proxy dari OpenProxy: {len(openproxy_proxies)}")
    
    proxy_list_download_proxies = fetch_proxy_list_download()
    logger.info(f"📊 Jumlah proxy dari Proxy-List.download: {len(proxy_list_download_proxies)}")
    
    proxynova_proxies = fetch_proxynova_proxies()
    logger.info(f"📊 Jumlah proxy dari ProxyNova: {len(proxynova_proxies)}")

    sslproxies_proxies = fetch_sslproxies_proxies()
    logger.info(f"📊 Jumlah proxy dari SSLProxies: {len(sslproxies_proxies)}")
    
    all_proxies = list(set(
        geonode_proxies + free_proxy_list_proxies + proxyscrape_proxies +
        openproxy_proxies + proxy_list_download_proxies + proxynova_proxies +
        sslproxies_proxies
    ))
    logger.info(f"Total proxy sebelum validasi: {len(all_proxies)}")

    with ThreadPoolExecutor(max_workers=20) as executor:
        valid_proxies = [proxy for proxy, is_valid in zip(all_proxies, executor.map(test_proxy, all_proxies)) if is_valid]
    
    try:
        if not check_redis_connection():
            logger.warning("⚠️ Tidak bisa menyimpan proxy karena Redis tidak tersedia")
            return
        
        existing_proxies = redis_client.lrange("proxy_list", 0, -1)
        new_proxies = [p for p in valid_proxies if p not in existing_proxies]
        if new_proxies:
            redis_client.rpush("proxy_list", *new_proxies)
            logger.info(f"✅ Menambahkan {len(new_proxies)} proxy valid ke Redis. Total proxy sekarang: {redis_client.llen('proxy_list')}")
        else:
            logger.info("ℹ️ Tidak ada proxy baru untuk ditambahkan")
    except redis.RedisError as e:
        logger.error(f"❌ Gagal menyimpan proxy ke Redis: {e}")

def check_redis_connection():
    try:
        redis_client.ping()
        return True
    except redis.RedisError as e:
        logger.error(f"❌ Gagal terhubung ke Redis: {e}")
        return False