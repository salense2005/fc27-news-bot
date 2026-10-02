import os
import time
import requests
import feedparser
from google import genai

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=EA%20FC%2027&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20SPORTS%20FC%2027&hl=en-US&gl=US&ceid=US:en",
]

client = genai.Client(api_key=GEMINI_API_KEY)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )


def get_news():
    news = []
    seen_links = set()

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:10]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()

            if not title or not link:
                continue

            if link in seen_links:
                continue

            seen_links.add(link)

            source = ""

            if hasattr(entry, "source"):
                source = entry.source.get("title", "")

            news.append({
                "title": title,
                "link": link,
                "source": source
            })

    return news[:10]


def generate_post(news):
    news_text = ""

    for item in news:
        news_text += (
            f"ЗАГОЛОВОК: {item['title']}\n"
            f"ИСТОЧНИК: {item['source']}\n"
            f"ССЫЛКА: {item['link']}\n\n"
        )

    prompt = f"""
Ты редактор Telegram-канала LilsNews про EA SPORTS FC 27.

Вот свежие найденные материалы:

{news_text}

Выбери только действительно важные новости для игроков FC 27.

Правила:
- Пиши на русском языке.
- Не выдумывай факты.
- Не выдавай слухи за подтвержденную информацию.
- Если это слух — прямо напиши «слух» или «неподтвержденная информация».
- Не пиши про обычные незначительные статьи.
- Если несколько источников сообщают об одном событии — объедини их.
- Используй умеренное количество эмодзи.
- Пост должен быть коротким и удобным для Telegram.

Формат:

🔥 ЗАГОЛОВОК

Кратко объясни, что произошло.

📌 Главное:
• пункт
• пункт
• пункт

🔗 Источник: ссылка

Если нет действительно важной новости, ответь только:

NO_NEWS
"""

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
            )

            return response.text.strip()

        except Exception as error:
            print(f"Gemini error, attempt {attempt + 1}: {error}")

            if attempt < 2:
                time.sleep(10)
            else:
                return None


def main():
    news = get_news()

    if not news:
        send_telegram(
            "🤖 LilsNews\n\n"
            "Проверил источники FC 27, но новостей пока не нашёл."
        )
        return

    post = generate_post(news)

    if post is None:
        send_telegram(
            "⚠️ LilsNews\n\n"
            "Новости найдены, но Gemini временно не смог обработать запрос. "
            "Попробую снова при следующем запуске."
        )
        return

    if post == "NO_NEWS":
        send_telegram(
            "🤖 LilsNews\n\n"
            "Новых действительно важных новостей FC 27 пока нет."
        )
        return

    send_telegram(
        "📰 LILSNEWS — НОВАЯ НОВОСТЬ\n\n"
        + post
    )


if __name__ == "__main__":
    main()
