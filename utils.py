# utils.py
import redis
import re
from statistics import mean, median
import logging

redis_client = redis.Redis(host='redis.railway.internal', port=6379, db=0, decode_responses=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def load_chat_history():
    """Ambil semua chat history dari Redis list"""
    return redis_client.lrange("chat_history", 0, -1) or []

def save_chat_history(text):
    """Tambahkan teks ke chat history di Redis"""
    if text not in redis_client.lrange("chat_history", 0, -1):
        redis_client.rpush("chat_history", text)
        logger.info(f"📌 Menambahkan '{text}' ke chat history di Redis")

def load_price_history():
    """Ambil semua price history dari Redis hash"""
    return redis_client.hgetall("price_history") or {}

def save_price_history(question, answer):
    """Simpan pasangan question:answer ke price history di Redis"""
    redis_client.hset("price_history", question, answer)
    logger.info(f"💾 Menyimpan harga ke Redis: {question} -> {answer}")

def find_price_in_history(question):
    """Cari harga di history berdasarkan question"""
    history = load_price_history()
    for q, a in history.items():
        if question.lower() in q.lower():
            logger.info(f"🔄 Menggunakan harga dari history untuk '{question}'")
            return a
    return None

def extract_prices(text):
    return re.findall(r"Rp ?[\d.,]+", text)

def clean_price_format(price_str):
    if not isinstance(price_str, str):
        return None
    price_cleaned = price_str.replace("Rp", "").strip()
    match = re.search(r"(\d{1,3}(?:\.\d{3})*)", price_cleaned)
    if not match:
        return None
    return int(match.group(1).replace(".", ""))

def get_min_reasonable_price(prices):
    if len(prices) < 4:
        return min(prices) if prices else 0
    sorted_prices = sorted(prices)
    q1 = median(sorted_prices[:len(sorted_prices)//2])
    median_price = median(sorted_prices)
    min_reasonable = max(q1, median_price / 2)
    if min_reasonable < median_price / 2:
        min_reasonable = median_price / 2
    return round(min_reasonable)

def normalize_price_query(text):
    text = text.lower().strip()
    text = re.sub(r"\b(harga|cek harga|berapa harga|berapa sih|berapa si)\b", "", text).strip()
    text = re.sub(r"[^\w\s]", "", text)
    words = text.split()
    text = " ".join(sorted(set(words), key=words.index))
    text = re.sub(r"\b(ipun|ipin|ipon|ip)(?:\s+(\d+))?\b", r"iphone \2", text).strip()
    return text.strip()