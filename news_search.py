"""
news_search.py — Поиск новостей и трендов через VK API (из групп)
"""
import os
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
from dotenv import load_dotenv


SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Группы VK для поиска новостей (ID групп)
VK_GROUPS = [
    55673231,       # mudrknig — мотивация
    130183532,      # club130183532
    174477870,      # vk.lfhk
    226696250,      # bazanova.travel
]


def get_user_api():
    """Получает API с USER token (для чтения чужих групп)"""
    import vk_api
    bossyoki_env = os.path.join(os.path.dirname(__file__), '..', 'BossYoki', '.env')
    if os.path.exists(bossyoki_env):
        load_dotenv(bossyoki_env)
    token = os.getenv('VK_TOKEN_PHOTOS')
    if token:
        session = vk_api.VkApi(token=token)
        return session.get_api()
    return None


def search_group_posts(group_id, count=10):
    """Берёт последние посты из группы VK"""
    api = get_user_api()
    if not api:
        return []
    try:
        result = api.wall.get(owner_id=-group_id, count=count, v='5.131')
        return result.get('items', [])
    except Exception:
        return []


def search_all_groups(count_per_group=10):
    """Берёт посты из всех групп"""
    all_posts = []
    for group_id in VK_GROUPS:
        posts = search_group_posts(group_id, count_per_group)
        # Добавляем имя группы к каждому посту
        for post in posts:
            post['_group_id'] = group_id
        all_posts.extend(posts)
    return all_posts


def get_group_name(group_id):
    """Получает название группы по ID"""
    api = get_user_api()
    if not api:
        return f"Группа {group_id}"
    try:
        result = api.groups.getById(group_id=group_id, v='5.131')
        return result[0].get('name', f"Группа {group_id}")
    except Exception:
        return f"Группа {group_id}"


def get_post_image(post):
    """Извлекает URL картинки из поста VK"""
    attachments = post.get('attachments', [])
    for att in attachments:
        if att.get('type') == 'photo':
            sizes = att['photo'].get('sizes', [])
            if sizes:
                # Берём последний (самый большой)
                return sizes[-1].get('url')
    return None


def download_image(url, save_dir='images'):
    """Скачивает картинку по URL"""
    try:
        resp = requests.get(url, headers=SEARCH_HEADERS, timeout=10)
        if resp.status_code == 200 and len(resp.content) > 5000:
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, f"post_{int(time.time())}.jpg")
            with open(path, 'wb') as f:
                f.write(resp.content)
            return path
    except Exception:
        pass
    return None


def filter_by_engagement(posts, min_likes=10, max_age_hours=48):
    """Фильтрует посты по вовлечённости и свежести"""
    now = time.time()
    max_age_sec = max_age_hours * 3600

    # Кэш имён групп
    group_names = {}

    filtered = []
    for post in posts:
        post_time = post.get('date', 0)
        if now - post_time > max_age_sec:
            continue

        likes = post.get('likes', {}).get('count', 0) if isinstance(post.get('likes'), dict) else post.get('likes', 0)
        if likes >= min_likes:
            group_id = post.get('_group_id', abs(post.get('owner_id', 0)))
            if group_id not in group_names:
                group_names[group_id] = get_group_name(group_id)

            filtered.append({
                'id': post.get('id'),
                'text': post.get('text', '')[:200],
                'likes': likes,
                'owner_id': post.get('owner_id'),
                'group_id': group_id,
                'group_name': group_names[group_id],
                'image_url': get_post_image(post)
            })
    return sorted(filtered, key=lambda x: x['likes'], reverse=True)


def fetch_page(url, max_length=2000):
    """Парсит текст страницы по URL"""
    try:
        resp = requests.get(url, headers=SEARCH_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[:max_length]
    except Exception:
        return None


def fetch_and_summarize(url, ask_ai_func):
    """Парсит страницу и кратко описывает через AI"""
    text = fetch_page(url)
    if not text:
        return None
    prompt = f"Кратко опиши суть этой статьи в 2-3 предложениях на русском:\n\n{text[:1500]}"
    return ask_ai_func(prompt, 0)
