import aiohttp
from bs4 import BeautifulSoup
import redis
import random
import logging
import asyncio
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
        "hide-my-ip": "https://www.hide-my-ip.com",
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

async def test_proxy(proxy, timeout=3):
    test_url = "http://httpbin.org/ip"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(test_url, proxy=f"http://{proxy}", headers=get_headers("httpbin"), timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                return response.status == 200
        except Exception as e:
            logger.debug(f"Proxy {proxy} gagal: {e}")
            return False

async def fetch_hide_my_ip_proxies():
    url = "https://www.hide-my-ip.com/proxylist.shtml"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("hide-my-ip"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
                proxies = []
                for row in soup.select("table tr")[1:]:  # Skip header
                    cols = row.find_all("td")
                    if len(cols) > 1:
                        ip = cols[0].text.strip()
                        port = cols[1].text.strip()
                        if ip and port and ":" not in ip:
                            proxies.append(f"{ip}:{port}")
                return proxies
        except Exception as e:
            logger.error(f"❌ Gagal mengambil proxy dari Hide My IP: {e}")
            return []

async def fetch_proxy_list_download():
    url = "https://www.proxy-list.download/api/v1/get?type=http&country=ID"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("proxy-list"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                text = await response.text()
                return [p for p in text.splitlines() if p.strip()]
        except Exception as e:
            logger.error(f"❌ Gagal mengambil proxy dari Proxy-List.download: {e}")
            return []

async def fetch_geonode_proxies():
    url = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&country=ID"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("proxylist"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                data = await response.json()
                if not data.get("data"):
                    logger.warning("⚠️ Tidak ada data proxy dari Geonode")
                    return []
                return [f"{item['ip']}:{item['port']}" for item in data["data"] if item.get("country") == "ID"]
        except Exception as e:
            logger.error(f"❌ Gagal mengambil proxy dari Geonode: {e}")
            return []

async def fetch_free_proxy_list():
    url = "https://free-proxy-list.net/"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("free-proxy-list"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
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

async def fetch_proxyscrape_proxies():
    url = "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&protocol=http&timeout=10000&country=id"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("proxyscrape"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                text = await response.text()
                return [p for p in text.splitlines() if p.strip()]
        except Exception as e:
            logger.error(f"❌ Gagal mengambil proxy dari Proxyscrape: {e}")
            return []

async def fetch_proxynova_proxies():
    url = "https://www.proxynova.com/proxy-server-list/country-id/"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("proxynova"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
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

async def fetch_sslproxies_proxies():
    url = "https://www.sslproxies.org/"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=get_headers("sslproxies"), timeout=aiohttp.ClientTimeout(total=10)) as response:
                response.raise_for_status()
                text = await response.text()
                soup = BeautifulSoup(text, "html.parser")
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

async def scrape_and_store_proxies():
    logger.info("🔄 Scraping dan memvalidasi proxy...")
    
    tasks = [
        fetch_geonode_proxies(),
        fetch_free_proxy_list(),
        fetch_proxyscrape_proxies(),
        fetch_hide_my_ip_proxies(),
        fetch_proxy_list_download(),
        fetch_proxynova_proxies(),
        fetch_sslproxies_proxies()
    ]
    results = await asyncio.gather(*tasks)
    
    all_proxies = list(set(sum(results, [])))
    logger.info(f"Total proxy sebelum validasi: {len(all_proxies)}")

    test_tasks = [test_proxy(proxy) for proxy in all_proxies]
    valid_results = await asyncio.gather(*test_tasks)
    valid_proxies = [proxy for proxy, is_valid in zip(all_proxies, valid_results) if is_valid]
    
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