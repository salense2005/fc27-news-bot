import os
import requests
import feedparser
from google import genai

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

RSS_FEEDS = [
    "https://www.ea.com/games/ea-sports-fc/fc-27/news",
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


def main():
    news = []

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:5]:
            title = entry.get("title", "")
            link = entry.get("link", "")

            if title and link:
                news.append(f"{title}\n{link}")

    if not news:
        send_telegram("🤖 LilsNews запущен, но новых новостей пока не найдено.")
        return

    news_text = "\n\n".join(news[:5])

    prompt = f"""
Ты редактор Telegram-канала по EA SPORTS FC 27.

Проанализируй найденные новости:

{news_text}

Выбери только действительно важные новости для игроков FC 27.

Если новость важная — напиши короткий Telegram-пост на русском языке.

Стиль:
- коротко и понятно
- без лишней воды
- используй эмодзи
- важные моменты выделяй
- не выдумывай факты
- не выдавай слухи за подтвержденную информацию
- в конце укажи источник

Если среди новостей нет ничего действительно важного, ответь только:
NO_NEWS
"""

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )

    post = response.text.strip()

    if post != "NO_NEWS":
        send_telegram("📰 НОВОСТЬ FC 27\n\n" + post)


if __name__ == "__main__":
    main()
