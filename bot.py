import os
import requests
import feedparser
from google import genai

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Поиск свежих новостей FC 27 через Google News RSS
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

            news.append(
                {
                    "title": title,
                    "link": link,
                    "source": source,
                }
            )

    return news[:10]


def main():
    news = get_news()

    if not news:
        send_telegram(
            "🤖 LilsNews\n\n"
            "Проверил источники FC 27, но новых материалов не нашёл."
        )
        return

    news_text = ""

    for item in news:
        news_text += (
            f"ЗАГОЛОВОК: {item['title']}\n"
            f"ИСТОЧНИК: {item['source']}\n"
            f"ССЫЛКА: {item['link']}\n\n"
        )

    prompt = f"""
Ты редактор Telegram-канала LilsNews, посвящённого EA SPORTS FC 27.

Ниже находятся свежие материалы, найденные в новостной ленте:

{news_text}

Твоя задача:

1. Выбери только действительно важные новости для игроков EA SPORTS FC 27.
2. Не пиши посты про обычные статьи, рекламу или незначительные материалы.
3. Не придумывай никаких фактов.
4. Если информация является слухом или неподтверждённой информацией, обязательно укажи это.
5. Если есть несколько материалов об одном событии, объедини их в одну новость.
6. Пиши на русском языке.
7. Стиль — короткий, современный Telegram-канал про FC 27.
8. Используй эмодзи, но без перебора.
9. Заголовок должен сразу объяснять, что произошло.
10. В конце обязательно дай ссылку на источник.

Формат:

🔥 ЗАГОЛОВОК

Короткое объяснение новости в 2–5 предложениях.

📌 Главное:
• пункт
• пункт
• пункт

🔗 Источник: ссылка

Если среди материалов нет действительно важной новости, напиши строго:

NO_NEWS
"""

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
    )

    post = response.text.strip()

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
