"""
content_generator.py — Генерация контента для постов
"""
import re
import os
import time
import requests
from urllib.parse import quote_plus

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Ключевые слова для поиска тем (туризм)
DEFAULT_KEYWORDS = [
    "горящие туры", "путешествия", "отдых", "туризм",
    "самолёт", "отель", "пляж", "море"
]


def generate_post_text(ask_ai_func, topic, style="blogger"):
    """Генерирует текст поста через AI"""
    prompts = {
        "blogger": (
            f"Напиши короткий привлекательный пост для VK группы про туризм на тему: {topic}. "
            "Тон: живой, эмоциональный, с эмодзи. 3-5 предложений. Без хэштегов."
        ),
        "entrepreneur": (
            f"Напиши деловую рекомендацию для VK группы про туризм на тему: {topic}. "
            "Тон: профессиональный, но доступный. Что делать бизнесу сейчас. 3-4 предложения."
        )
    }
    prompt = prompts.get(style, prompts["blogger"])
    return ask_ai_func(prompt, 0)


def search_image_yandex(query):
    """Ищет картинку через Яндекс Картинки"""
    try:
        url = f"https://yandex.ru/images/search?text={quote_plus(query)}"
        resp = requests.get(url, headers=SEARCH_HEADERS, timeout=15)
        html = resp.text

        raw_urls = re.findall(
            r'https?://[^\s"<>\\]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s"<>\\]*)?',
            html, re.IGNORECASE
        )

        clean_urls = []
        for u in raw_urls:
            u = u.split('&quot;')[0].split('&amp;')[0].split('&#')[0].rstrip('/.,;:')
            if 'yastatic.net' not in u and len(u) > 30:
                clean_urls.append(u)

        unique = list(dict.fromkeys(clean_urls))
        if not unique:
            return None

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
                    img_path = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        'images', f"post_{int(time.time())}.{ext}"
                    )
                    os.makedirs(os.path.dirname(img_path), exist_ok=True)
                    with open(img_path, 'wb') as f:
                        f.write(r.content)
                    return img_path
            except Exception:
                continue

        return None
    except Exception:
        return None


def format_topics(topics):
    """Форматирует список тем для отправки пользователю"""
    if not topics:
        return "Не удалось найти актуальные темы."

    result = "☀️ Нашёл для тебя темы:\n\n"
    for i, topic in enumerate(topics[:3], 1):
        text = topic.get('text', 'Без текста')
        if len(text) > 80:
            text = text[:80] + "..."
        likes = topic.get('likes', 0)
        group = topic.get('group_name', '')
        has_image = "📷" if topic.get('image_url') else ""
        source = f" | Источник: {group}" if group else ""
        result += f"{i}️⃣ {text}\n   ❤️ {likes} лайков{source} {has_image}\n\n"
    result += "Напиши цифру (1, 2 или 3), и я подготовлю пост."
    return result


def format_recommendation(trends):
    """Форматирует рекомендацию для предпринимателя"""
    if not trends:
        return "Не удалось проанализировать тренды."

    top = trends[0] if trends else {}
    return (
        f"💼 Для твоего бизнеса сейчас актуально:\n\n"
        f"📊 Активно обсуждают: {top.get('text', 'тему')[:60]}...\n"
        f"❤️ Вовлечённость: {top.get('likes', 0)} лайков\n\n"
        f"Что предлагаю:\n"
        f"1️⃣ Сделать пост по этой теме\n"
        f"2️⃣ Опубликовать опрос\n"
        f"3️⃣ Пропустить и ждать следующего\n\n"
        f"Напиши номер."
    )
