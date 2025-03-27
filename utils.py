import redis
import re
from statistics import mean, median
import logging
import os

REDIS_HOST = os.getenv("REDIS_HOST", "redis.railway.internal")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db=0, decode_responses=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def load_chat_history():
    return redis_client.lrange("chat_history", 0, -1) or []

def save_chat_history(text):
    if not check_redis_connection():
        logger.warning(f"⚠️ Tidak bisa menyimpan '{text}' ke chat history karena Redis tidak tersedia")
        return
    if text not in redis_client.lrange("chat_history", 0, -1):
        redis_client.rpush("chat_history", text)
        logger.info(f"📌 Menambahkan '{text}' ke chat history di Redis")

def load_price_history():
    return redis_client.hgetall("price_history") or {}

def save_price_history(question, answer):
    redis_client.hset("price_history", question, answer)
    logger.info(f"💾 Menyimpan harga ke Redis: {question} -> {answer}")

def find_price_in_history(question):
    history = load_price_history()
    for q, a in history.items():
        if question.lower() in q.lower():
            logger.info(f"🔄 Menggunakan harga dari history untuk '{question}'")
            return a
    return None

def normalize_price_query(text):
    text = text.lower().strip()
    text = re.sub(r"\b(harga|cek harga|berapa harga|berapa sih|berapa si)\b", "", text).strip()
    text = re.sub(r"[^\w\s]", "", text)
    words = text.split()
    text = " ".join(sorted(set(words), key=words.index))
    text = re.sub(r"\b(ipun|ipin|ipon|ip)(?:\s+(\d+))?\b", r"iphone \2", text).strip()
    return text.strip()

def check_redis_connection():
    try:
        redis_client.ping()
        return True
    except redis.RedisError as e:
        logger.error(f"❌ Gagal terhubung ke Redis: {e}")
        return False