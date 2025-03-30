import asyncio
import os
import logging
import signal
import time
import redis
import subprocess
from flask import Flask, render_template, jsonify, request
from logging.handlers import QueueHandler
from queue import Queue
from proxy_scraper import scrape_and_store_proxies
from chat_handler import run_telegram_bot, shutdown_telegram
from utils import logger

# Konfigurasi Redis
REDIS_HOST = os.getenv("REDIS_HOST", "redis.railway.internal")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db=0, decode_responses=True)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Setup logging dengan queue untuk frontend
log_queue = Queue()
log_handler = QueueHandler(log_queue)
logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# Buffer untuk menyimpan log terbaru
log_buffer = []

# Inisialisasi Flask
app = Flask(__name__)

# Fungsi untuk memproses log ke buffer
def process_logs():
    while not log_queue.empty():
        log_record = log_queue.get()
        log_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log_record.created))
        log_entry = f"{log_time} - {log_record.levelname} - {log_record.message}"
        log_buffer.append(log_entry)

# Endpoint Flask untuk dashboard
@app.route('/')
def dashboard():
    logger.info("ℹ️ Mengakses endpoint dashboard")
    return render_template('dashboard.html')

# API untuk data monitoring
@app.route('/api/monitoring', methods=['GET'])
def monitoring_data():
    process_logs()
    redis_status = "Connected" if check_redis_connection() else "Disconnected"
    proxy_count = redis_client.llen("proxy_list") or 0
    chat_history_count = redis_client.llen("chat_history") or 0
    price_history_count = redis_client.hlen("price_history") or 0
    
    return jsonify({
        "logs": log_buffer,
        "redis_status": redis_status,
        "proxy_count": proxy_count,
        "chat_history_count": chat_history_count,
        "price_history_count": price_history_count
    })

# API untuk membersihkan log
@app.route('/api/clear_logs', methods=['POST'])
def clear_logs():
    global log_buffer
    log_buffer = []
    logger.info("ℹ️ Log telah dibersihkan")
    return jsonify({"status": "success", "message": "Logs cleared"})

# CRUD untuk Proxy
@app.route('/api/proxies', methods=['GET'])
def get_proxies():
    proxies = redis_client.lrange("proxy_list", 0, -1)
    return jsonify({"proxies": proxies})

@app.route('/api/proxies', methods=['POST'])
def add_proxy():
    proxy = request.json.get('proxy')
    if proxy:
        redis_client.lpush("proxy_list", proxy)
        logger.info(f"ℹ️ Proxy {proxy} ditambahkan")
        return jsonify({"status": "success", "message": f"Proxy {proxy} added"})
    return jsonify({"status": "error", "message": "Proxy is required"}), 400

@app.route('/api/proxies', methods=['PUT'])
def update_proxy():
    old_proxy = request.json.get('old_proxy')
    new_proxy = request.json.get('new_proxy')
    if old_proxy and new_proxy and redis_client.lrem("proxy_list", 0, old_proxy) > 0:
        redis_client.lpush("proxy_list", new_proxy)
        logger.info(f"ℹ️ Proxy {old_proxy} diperbarui menjadi {new_proxy}")
        return jsonify({"status": "success", "message": f"Proxy updated to {new_proxy}"})
    return jsonify({"status": "error", "message": "Proxy not found or invalid data"}), 404

@app.route('/api/proxies', methods=['DELETE'])
def delete_proxy():
    proxy = request.json.get('proxy')
    if proxy and redis_client.lrem("proxy_list", 0, proxy) > 0:
        logger.info(f"ℹ️ Proxy {proxy} dihapus")
        return jsonify({"status": "success", "message": f"Proxy {proxy} deleted"})
    return jsonify({"status": "error", "message": "Proxy not found"}), 404

# CRUD untuk Chat History
@app.route('/api/chat_history', methods=['GET'])
def get_chat_history():
    chat_history = redis_client.lrange("chat_history", 0, -1)
    return jsonify({"chat_history": chat_history})

@app.route('/api/chat_history', methods=['POST'])
def add_chat_history():
    entry = request.json.get('entry')
    if entry:
        redis_client.lpush("chat_history", entry)
        logger.info(f"ℹ️ Chat history {entry} ditambahkan")
        return jsonify({"status": "success", "message": f"Chat history {entry} added"})
    return jsonify({"status": "error", "message": "Entry is required"}), 400

@app.route('/api/chat_history', methods=['PUT'])
def update_chat_history():
    old_entry = request.json.get('old_entry')
    new_entry = request.json.get('new_entry')
    if old_entry and new_entry and redis_client.lrem("chat_history", 0, old_entry) > 0:
        redis_client.lpush("chat_history", new_entry)
        logger.info(f"ℹ️ Chat history {old_entry} diperbarui menjadi {new_entry}")
        return jsonify({"status": "success", "message": f"Chat history updated to {new_entry}"})
    return jsonify({"status": "error", "message": "Entry not found or invalid data"}), 404

@app.route('/api/chat_history', methods=['DELETE'])
def delete_chat_history():
    entry = request.json.get('entry')
    if entry and redis_client.lrem("chat_history", 0, entry) > 0:
        logger.info(f"ℹ️ Chat history {entry} dihapus")
        return jsonify({"status": "success", "message": f"Chat history {entry} deleted"})
    return jsonify({"status": "error", "message": "Entry not found"}), 404

# CRUD untuk Price History
@app.route('/api/price_history', methods=['GET'])
def get_price_history():
    price_history = redis_client.hgetall("price_history")
    return jsonify({"price_history": price_history})

@app.route('/api/price_history', methods=['POST'])
def add_price_history():
    key = request.json.get('key')
    value = request.json.get('value')
    if key and value:
        redis_client.hset("price_history", key, json.dumps(value))
        logger.info(f"ℹ️ Price history {key} ditambahkan")
        return jsonify({"status": "success", "message": f"Price history {key} added"})
    return jsonify({"status": "error", "message": "Key and value are required"}), 400

@app.route('/api/price_history', methods=['PUT'])
def update_price_history():
    key = request.json.get('key')
    value = request.json.get('value')
    if key and value and redis_client.hexists("price_history", key):
        redis_client.hset("price_history", key, json.dumps(value))
        logger.info(f"ℹ️ Price history {key} diperbarui")
        return jsonify({"status": "success", "message": f"Price history {key} updated"})
    return jsonify({"status": "error", "message": "Key not found or invalid data"}), 404

@app.route('/api/price_history', methods=['DELETE'])
def delete_price_history():
    key = request.json.get('key')
    if key and redis_client.hdel("price_history", key) > 0:
        logger.info(f"ℹ️ Price history {key} dihapus")
        return jsonify({"status": "success", "message": f"Price history {key} deleted"})
    return jsonify({"status": "error", "message": "Key not found"}), 404

def check_redis_connection():
    try:
        redis_client.ping()
        return True
    except redis.RedisError:
        return False

async def run_proxy_scraper_periodically():
    while True:
        try:
            logger.info("🚀 Memulai scraping proxy...")
            await scrape_and_store_proxies()
            logger.info("✅ Proxy scraping selesai untuk iterasi ini")
        except Exception as e:
            logger.error(f"❌ Gagal menjalankan proxy scraper: {e}")
        await asyncio.sleep(5 * 60)

async def run_flask():
    port = int(os.getenv("PORT", 8080))
    logger.info(f"🚀 Menjalankan Flask server dengan Gunicorn pada port {port}...")
    gunicorn_process = subprocess.Popen([
        "gunicorn",
        "--bind", f"0.0.0.0:{port}",
        "--workers", "2",
        "app:app"
    ])
    await asyncio.sleep(1)
    if gunicorn_process.poll() is not None:
        logger.error("❌ Gunicorn gagal dimulai")
    else:
        logger.info("✅ Gunicorn berjalan")

async def main():
    telegram_app = await run_telegram_bot(TOKEN)
    
    tasks = [
        run_flask(),
        run_proxy_scraper_periodically(),
    ]

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown_telegram(telegram_app)))

    logger.info("🚀 Aplikasi utama sedang berjalan...")
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())