"""
vk_agent.py — Бесплатный AI-агент в VK (с памятью контекста и картинками)

Запуск: python3 vk_agent.py
Остановка: python3 vk_agent.py stop

Использует GitHub Models (бесплатно) для ответов на сообщения.
Помнит последние 20 сообщений в каждом диалоге.
Ищет картинки через Яндекс и публикует посты с картинками.
Данные хранятся в Supabase PostgreSQL.
"""

import os
import sys
import re
import time
import logging
import signal
import requests
import psycopg2
from datetime import datetime
import pytz

MOSCOW_TZ = pytz.timezone('Europe/Moscow')
from urllib.parse import quote_plus

import vk_api
from vk_api.utils import get_random_id
from dotenv import load_dotenv
from news_search import search_all_groups, filter_by_engagement, download_image

# Загружаем .env
load_dotenv()
BOSSYOKI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'BossYoki')
bossyoki_env = os.path.join(BOSSYOKI_DIR, '.env')
if os.path.exists(bossyoki_env):
    load_dotenv(bossyoki_env)

# === Настройки ===

VK_TOKEN = os.getenv('VK_TOKEN')
VK_TOKEN_PHOTOS = os.getenv('VK_TOKEN_PHOTOS')
VK_USER_ID = int(os.getenv('VK_USER_ID', '114439622'))
VK_GROUP_ID = int(os.getenv('VK_GROUP_ID', '0'))
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(DIR, 'agent.log')
PID_FILE = os.path.join(DIR, 'agent.pid')
IMAGES_DIR = os.path.join(DIR, 'images')
POLL_INTERVAL = 3
MAX_HISTORY = 20

# GitHub Models API (бесплатно)
GITHUB_MODELS_API = "https://models.inference.ai.azure.com/chat/completions"
MODEL = "gpt-4o-mini"

# Яндекс Картинки (без API-ключей)
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

SYSTEM_PROMPT = (
    "Ты полезный AI-ассистент. Отвечай кратко и по делу на русском языке. "
    "У тебя есть память предыдущих сообщений — используй её для контекста. "
    "Если просят написать текст — пиши сразу, без лишних объяснений."
)

# === Логирование ===

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('agent')


# === БД (Supabase PostgreSQL) ===

def get_db():
    """Подключение к Supabase"""
    return psycopg2.connect(SUPABASE_KEY)

def get_user_history(user_id):
    """Получает историю конкретного пользователя из БД"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        SELECT role, message FROM agent_conversations
        WHERE user_id = %s ORDER BY id DESC LIMIT %s
    ''', (user_id, MAX_HISTORY))
    rows = cur.fetchall()
    conn.close()
    # Возвращаем в хронологическом порядке
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]

def add_to_history(user_id, role, text):
    """Добавляет сообщение в историю в БД"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO agent_conversations (user_id, role, message)
        VALUES (%s, %s, %s)
    ''', (user_id, role, text))
    conn.commit()
    conn.close()

def is_post_published(vk_url):
    """Проверяет, был ли пост уже опубликован"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT id FROM published_posts WHERE vk_url = %s', (vk_url,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def save_post(user_id, text, image_url, vk_url):
    """Сохраняет опубликованный пост в БД"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO published_posts (user_id, text, image_url, vk_url)
        VALUES (%s, %s, %s, %s)
    ''', (user_id, text, image_url, vk_url))
    conn.commit()
    conn.close()


# === AI через GitHub Models (с памятью) ===

def ask_ai(text, user_id):
    """Отправляет запрос в GitHub Models с контекстом диалога"""
    if not GITHUB_TOKEN:
        return "❌ Нет GitHub токена"

    # Загружаем историю из БД
    user_history = get_user_history(user_id)

    # Формируем сообщения с историей
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(user_history)
    messages.append({"role": "user", "content": text})

    try:
        r = requests.post(
            GITHUB_MODELS_API,
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL,
                "messages": messages,
                "max_tokens": 1000,
                "temperature": 0.7
            },
            timeout=30
        )

        data = r.json()
        if "choices" in data and len(data["choices"]) > 0:
            reply = data["choices"][0]["message"]["content"]
            # Сохраняем в историю в БД
            add_to_history(user_id, "user", text)
            add_to_history(user_id, "assistant", reply)
            return reply
        elif "error" in data:
            log.error(f"GitHub Models error: {data['error']}")
            return f"❌ Ошибка AI: {data['error'].get('message', 'неизвестно')}"
        else:
            return "❌ Неожиданный ответ от AI"

    except Exception as e:
        log.error(f"AI error: {e}")
        return f"❌ Ошибка соединения с AI"


# === Отправка в VK ===

def send_vk(api, user_id, text):
    """Отправляет сообщение в VK"""
    try:
        api.messages.send(
            user_id=user_id,
            message=text,
            random_id=get_random_id()
        )
        log.info(f"→ {user_id}: {text[:80]}")
    except Exception as e:
        log.error(f"Ошибка отправки: {e}")


# === Посты на стену ===

def post_to_wall(api, text):
    """Публикует пост на стене группы"""
    try:
        result = api.wall.post(
            owner_id=-VK_GROUP_ID,
            message=text,
            from_group=1
        )
        post_id = result['post_id']
        url = f"https://vk.com/wall-{VK_GROUP_ID}_{post_id}"
        log.info(f"Опубликован пост: {url}")
        return url
    except Exception as e:
        log.error(f"Ошибка публикации: {e}")
        return None


# === Поиск картинок через Яндекс ===

def search_image(query):
    """Ищет картинку через Яндекс Картинки и скачивает"""
    try:
        url = f"https://yandex.ru/images/search?text={quote_plus(query)}"
        resp = requests.get(url, headers=SEARCH_HEADERS, timeout=15)
        html = resp.text

        # Извлекаем URL картинок (прямые ссылки на изображения)
        raw_urls = re.findall(
            r'https?://[^\s"<>\\]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s"<>\\]*)?',
            html, re.IGNORECASE
        )

        # Чистим от HTML-мусора
        clean_urls = []
        for u in raw_urls:
            u = u.split('&quot;')[0].split('&amp;')[0].split('&#')[0]
            u = u.rstrip('/.,;:')
            if 'yastatic.net' in u:
                continue
            if len(u) > 30:
                clean_urls.append(u)

        unique = list(dict.fromkeys(clean_urls))
        if not unique:
            return None

        # Скачиваем первую подходящую
        os.makedirs(IMAGES_DIR, exist_ok=True)
        for img_url in unique[:8]:
            try:
                r = requests.get(img_url, headers=SEARCH_HEADERS, timeout=8)
                ct = r.headers.get('content-type', '')
                size = len(r.content)
                if r.status_code == 200 and size > 10000 and 'image' in ct:
                    ext = 'jpg'
                    if 'png' in ct:
                        ext = 'png'
                    elif 'webp' in ct:
                        ext = 'webp'
                    img_path = os.path.join(IMAGES_DIR, f"post_{int(time.time())}.{ext}")
                    with open(img_path, 'wb') as f:
                        f.write(r.content)
                    log.info(f"Картинка скачана: {img_path}")
                    return img_path
            except:
                continue

        return None
    except Exception as e:
        log.error(f"Ошибка поиска картинки: {e}")
        return None


# === Загрузка фото в VK ===

def upload_photo_to_vk(img_path):
    """Загружает фото в VK и возвращает attachment строку"""
    if not VK_TOKEN_PHOTOS:
        log.error("Нет VK_TOKEN_PHOTOS для загрузки фото")
        return None

    try:
        # 1. Получаем сервер для загрузки
        r = requests.get('https://api.vk.com/method/photos.getWallUploadServer', params={
            'owner_id': f'-{VK_GROUP_ID}',
            'access_token': VK_TOKEN_PHOTOS,
            'v': '5.131'
        })
        upload_server = r.json()['response']

        # 2. Загружаем фото
        with open(img_path, 'rb') as f:
            response = requests.post(upload_server['upload_url'], files={'photo': f}, timeout=30)
        upload_data = response.json()

        # 3. Сохраняем фото
        r = requests.get('https://api.vk.com/method/photos.saveWallPhoto', params={
            'owner_id': f'-{VK_GROUP_ID}',
            'server': upload_data['server'],
            'photo': upload_data['photo'],
            'hash': upload_data['hash'],
            'access_token': VK_TOKEN_PHOTOS,
            'v': '5.131'
        })
        saved = r.json()['response']
        attachment = f"photo{saved[0]['owner_id']}_{saved[0]['id']}"
        
        log.info(f"Фото загружено: {attachment}")
        return attachment
    except Exception as e:
        log.error(f"Ошибка загрузки фото: {e}")
        return None


def post_to_wall_with_image(api, text, img_path):
    """Публикует пост с картинкой на стене группы"""
    try:
        # Загружаем фото
        attachment = upload_photo_to_vk(img_path)
        if not attachment:
            # Если фото не загрузилось — публикуем без картинки
            return post_to_wall(api, text)

        # Публикуем пост с картинкой
        result = api.wall.post(
            owner_id=-VK_GROUP_ID,
            message=text,
            attachments=attachment,
            from_group=1
        )
        post_id = result['post_id']
        url = f"https://vk.com/wall-{VK_GROUP_ID}_{post_id}"
        log.info(f"Опубликован пост с картинкой: {url}")
        return url
    except Exception as e:
        log.error(f"Ошибка публикации: {e}")
        return None


# === Обработка команд ===

def count_today_posts():
    """Считает количество постов, опубликованных сегодня"""
    conn = get_db()
    cur = conn.cursor()
    now = datetime.now(MOSCOW_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cur.execute(
        'SELECT COUNT(*) FROM published_posts WHERE created_at >= %s',
        (today_start,)
    )
    count = cur.fetchone()[0]
    conn.close()
    return count


# Шаблоны ответов (без AI, 0 токенов)
GREETINGS = ["привет", "здравствуй", "добрый день", "добрый вечер", "доброе утро",
             "хай", "хей", "йо", "приветик", "здарова"]
GREETING_RESPONSES = [
    "Привет! Чем могу помочь?",
    "Йо! Напиши 'помощь' чтобы увидеть команды.",
    "Привет! Хочешь пост? Напиши 'найди посты'.",
]

# Состояние: ожидание выбора темы (1/2/3)
pending_topics = []


def handle_command(text, api):
    """Обрабатывает служебные команды (с шаблонами без AI)"""
    t = text.lower().strip()

    # === Шаблоны без AI (0 токенов) ===

    # Приветствия
    if any(t.startswith(g) for g in GREETINGS):
        import random
        return random.choice(GREETING_RESPONSES)

    if t in ["помощь", "help", "команды"]:
        return (
            "Команды:\n"
            "найди посты — поиск тем из VK-групп (3 варианта)\n"
            "1 / 2 / 3 — выбрать тему из списка\n"
            "пост [текст] — опубликовать пост\n"
            "пост с картинкой [тема] — пост + картинка\n"
            "мои посты — список опубликованных\n"
            "сколько постов — статистика за сегодня\n"
            "помощь — этот список"
        )

    if t in ["статус", "сколько постов"]:
        count = count_today_posts()
        if count == 0:
            return "Сегодня пока нет постов."
        return f"Сегодня опубликовано: {count} пост(ов)."

    # Поиск постов из VK-групп
    if t in ["найди посты", "найди темы", "поищи посты", "что по трендам"]:
        global pending_topics
        all_posts = search_all_groups(count_per_group=10)
        filtered = filter_by_engagement(all_posts, min_likes=5, max_age_hours=72)
        top3 = filtered[:3]
        if not top3:
            return "Свежих постов с лайками пока нет. Попробуй позже."
        pending_topics = top3
        result = "Нашёл 3 варианта:\n\n"
        for i, topic in enumerate(top3, 1):
            text_preview = topic['text'][:100] + "..." if len(topic['text']) > 100 else topic['text']
            likes = topic.get('likes', 0)
            group = topic.get('group_name', '')
            result += f"{i}. {text_preview}\n   Лайков: {likes} | {group}\n\n"
        result += "Напиши 1, 2 или 3 чтобы опубликовать"
        return result

    # Выбор темы из списка (1/2/3)
    if t in ['1', '2', '3'] and pending_topics:
        choice = int(t) - 1
        if choice < len(pending_topics):
            topic = pending_topics[choice]
            topic_text = topic.get('text', '')[:200]
            group_name = topic.get('group_name', '')
            source_label = f"\n\nИсточник: {group_name}" if group_name else ""

            # Генерируем пост через AI
            post_text = ask_ai(
                f"Напиши короткий привлекательный пост для VK на основе этого текста:\n\n{topic_text}",
                0
            )
            post_text += source_label

            # Пытаемся взять картинку из оригинального поста
            img_path = None
            image_url = topic.get('image_url')
            if image_url:
                img_path = download_image(image_url)

            if img_path:
                url = post_to_wall_with_image(api, post_text, img_path)
                try:
                    os.remove(img_path)
                except:
                    pass
            else:
                url = post_to_wall(api, post_text)

            pending_topics = []
            if url:
                save_post(0, post_text, image_url, url)
                return f"Опубликовано!\n{url}"
            else:
                return "Не удалось опубликовать пост"
        else:
            pending_topics = []
            return "Неверный номер. Напиши 'найди посты' заново."

    # Если ввели число, но нет списка — сбрасываем
    if t in ['1', '2', '3'] and not pending_topics:
        return "Список пуст. Напиши 'найди посты' чтобы начать."

    # Показать опубликованные посты
    if t in ["мои посты", "посты", "список постов"]:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT text, vk_url, created_at FROM published_posts ORDER BY id DESC LIMIT 5')
        posts = cur.fetchall()
        conn.close()
        if not posts:
            return "Пока нет опубликованных постов."
        result = "Последние посты:\n\n"
        for i, (text, url, dt) in enumerate(posts, 1):
            short = text[:80] + "..." if len(text) > 80 else text
            result += f"{i}. {short}\n   {url}\n\n"
        return result

    # Пост с картинкой — разные варианты написания
    keywords_with_image = ["пост с картинкой", "пост с фото", "пост с изображением",
                           "сделай пост с картинкой", "напиши пост с картинкой",
                           "опубликуй пост с картинкой", "выложи пост с картинкой"]
    for kw in keywords_with_image:
        if kw in t:
            # Извлекаем тему после ключевого слова
            idx = t.find(kw) + len(kw)
            query = text[idx:].strip().strip(":").strip()
            if not query:
                return "Укажи тему. Пример: пост с картинкой горящие туры Турция"
            
            # Генерируем текст через AI
            post_text = ask_ai(f"Напиши короткий привлекательный пост для ВКонтакте на тему: {query}", 0)
            
            # Ищем картинку
            img_path = search_image(query)
            if img_path:
                url = post_to_wall_with_image(api, post_text, img_path)
            else:
                url = post_to_wall(api, post_text)
            
            if url:
                save_post(0, post_text, img_path, url)
                return f"✅ Пост опубликован!\n{url}"
            else:
                return "❌ Не удалось опубликовать пост"

    # Обычный пост (без картинки) — разные варианты
    post_prefixes = ["пост ", "сделай пост ", "напиши пост ", "опубликуй пост ", "выложи пост "]
    post_prefix = None
    for prefix in post_prefixes:
        if t.startswith(prefix):
            post_prefix = prefix
            break
    
    if post_prefix:
        post_text = text[len(post_prefix):].strip()
        if not post_text:
            return "Укажи текст поста. Пример: пост Привет мир!"
        
        # Если текст короткий — генерируем через AI
        if len(post_text) < 20:
            post_text = ask_ai(f"Напиши интересный пост для VK на тему: {post_text}", 0)
        
        url = post_to_wall(api, post_text)
        if url:
            save_post(0, post_text, None, url)
            return f"✅ Пост опубликован!\n{url}"
        else:
            return "❌ Не удалось опубликовать пост"

    return None


# === Основной цикл ===

def main():
    # PID файл
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    log.info(f"AI-агент запущен. Модель: {MODEL}")
    log.info(f"Группа: {VK_GROUP_ID}")
    log.info(f"PID: {os.getpid()}")

    # Хранилище обработанных message_id
    processed = set()

    while True:
        try:
            # Подключение к VK
            vk_session = vk_api.VkApi(token=VK_TOKEN)
            api = vk_session.get_api()

            # Получаем последние беседы
            conversations = api.messages.getConversations(
                offset=0,
                count=20
            )

            for item in conversations['items']:
                msg = item['last_message']
                msg_id = msg['id']
                user_id = msg['from_id']
                text = msg.get('text', '').strip()

                # Пропускаем уже обработанные
                if msg_id in processed:
                    continue

                # Пропускаем сообщения от самого себя
                if user_id == -int(VK_GROUP_ID):
                    processed.add(msg_id)
                    continue

                # Пропускаем пустые
                if not text:
                    processed.add(msg_id)
                    continue

                log.info(f"← {user_id}: {text[:80]}")

                # Обработка команд
                reply = handle_command(text, api)

                # Если не команда — отправляем в AI с контекстом
                if not reply:
                    reply = ask_ai(text, user_id)

                # Отправляем ответ
                send_vk(api, user_id, reply)
                processed.add(msg_id)

            # Ограничиваем размер хранилища
            if len(processed) > 1000:
                processed = set(list(processed)[-500:])

        except Exception as e:
            log.error(f"Ошибка: {e}")

        time.sleep(POLL_INTERVAL)


# === Остановка ===

def stop():
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Остановлен (PID {pid})")
        os.remove(PID_FILE)
    else:
        print("Агент не запущен")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        stop()
        sys.exit(0)

    def handle_signal(sig, frame):
        log.info("Остановлен.")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    main()
