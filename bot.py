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
    seen = set()

    for url in RSS_FEEDS:
        feed = feedparser.parse(url)

        for entry in feed.entries[:10]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()

            if title and link and link not in seen:
                seen.add(link)
                news.append({
                    "title": title,
                    "link": link
                })

    return news[:10]


def generate_post(news):
    news_text = "\n\n".join(
        f"ЗАГОЛОВОК: {item['title']}\nССЫЛКА: {item['link']}"
        for item in news
    )

    prompt = f"""
Ты редактор Telegram-канала LilsNews про EA SPORTS FC 27.

Вот свежие новости:

{news_text}

Выбери самые важные новости для игроков FC 27.

Напиши короткий пост на русском языке.

Правила:
- не выдумывай факты;
- слухи обязательно обозначай как слухи;
- не пиши незначительные новости;
- используй несколько эмодзи;
- текст должен быть коротким;
- в конце дай ссылку на источник.

Формат:

🔥 ЗАГОЛОВОК

Краткое описание новости.

📌 Главное:
• пункт
• пункт

🔗 Источник: ссылка

Если ничего важного нет, напиши:
NO_NEWS
"""

    models = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]

    for model in models:
        for attempt in range(3):
            try:
                print(f"Trying {model}, attempt {attempt + 1}")

                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )

                if response and response.text:
                    return response.text.strip()

            except Exception as error:
                print(f"{model} error: {error}")

                if attempt < 2:
                    time.sleep(10)

    return None


def main():
    news = get_news()

    if not news:
        send_telegram(
            "🤖 LilsNews\n\n"
            "Новости FC 27 не найдены."
        )
        return

    post = generate_post(news)

    if post is None:
        send_telegram(
            "⚠️ LilsNews\n\n"
            "Новости найдены, но Gemini сейчас недоступен."
        )
        return

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
