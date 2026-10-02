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
        },
        timeout=30,
    )


def get_news():
    news = []
    seen = set()

    for url in RSS_FEEDS:
        feed = feedparser.parse(url)

        for entry in feed.entries[:10]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()

            if title and link and link not in seen:
                seen.add(link)
                news.append(f"{title}\n{link}")

    return news[:10]


def main():
    news = get_news()

    if not news:
        send_telegram("🤖 LilsNews\n\nНовости FC 27 не найдены.")
        return

    news_text = "\n\n".join(news)

    prompt = f"""
Ты редактор Telegram-канала LilsNews про EA SPORTS FC 27.

Свежие новости:

{news_text}

Выбери самую важную новость.

Напиши короткий пост на русском языке.

Формат:

🔥 Заголовок

2-4 предложения с сутью новости.

📌 Главное:
• важный пункт
• важный пункт

🔗 Источник: ссылка

Не выдумывай информацию.
Если это слух — напиши, что это слух.

Если ничего важного нет, напиши NO_NEWS.
"""

    # Пробуем несколько моделей по очереди.
    models = [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]

    response = None

    for model in models:
        for attempt in range(2):
            try:
                print(f"Trying model: {model}")

                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )

                if response and response.text:
                    break

            except Exception as error:
                print(f"Error with {model}: {error}")

                if attempt == 0:
                    time.sleep(5)

        if response and response.text:
            break

    if not response or not response.text:
        send_telegram(
            "⚠️ LilsNews\n\n"
            "Новости найдены, но ни одна доступная Gemini-модель "
            "не смогла обработать запрос."
        )
        return

    post = response.text.strip()

    if post == "NO_NEWS":
        send_telegram(
            "🤖 LilsNews\n\n"
            "Новых важных новостей FC 27 пока нет."
        )
        return

    send_telegram(
        "📰 LILSNEWS\n\n" + post
    )


if __name__ == "__main__":
    main()
