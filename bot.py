import os
import time
import requests
import feedparser
from google import genai

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Источники поиска новостей
RSS_FEEDS = [
    "https://news.google.com/rss/search?q=EA%20FC%2027%20meta&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20Ultimate%20Team&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20SBC&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20players%20tactics&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20gameplay&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EA%20FC%2027%20patch&hl=en-US&gl=US&ceid=US:en",
]

client = genai.Client(api_key=GEMINI_API_KEY)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()


def get_news():
    news = []
    seen_links = set()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()

                if not title or not link:
                    continue

                if link in seen_links:
                    continue

                seen_links.add(link)

                news.append({
                    "title": title,
                    "link": link
                })

        except Exception as error:
            print(f"RSS error: {error}")

    return news[:40]


def generate_post(news):
    news_text = ""

    for index, item in enumerate(news, start=1):
        news_text += (
            f"{index}. {item['title']}\n"
            f"LINK: {item['link']}\n\n"
        )

    prompt = f"""
Ты — главный редактор Telegram-канала LilsNews про EA SPORTS FC 27.

Твоя задача — НЕ пересказывать все новости подряд.

Тебе нужно выбрать только такие новости, которые реально заинтересуют игрока FC 27
и заставят его открыть пост.

Вот найденные материалы:

{news_text}

========================
ПРИОРИТЕТ НОВОСТЕЙ
========================

1. META 🔥
- новые мета-игроки;
- сильные карты;
- игроки, которых начали массово использовать;
- новые сильные связки;
- изменения меты;
- сильные PlayStyles;
- сильные позиции;
- находки игроков.

2. ULTIMATE TEAM 💰
- новые SBC;
- новые промокарты;
- Objectives;
- Evolutions;
- новые награды;
- сильные и выгодные карты;
- важные изменения Ultimate Team.

3. GAMEPLAY 🎮
- изменения геймплея;
- новые механики;
- сильные удары;
- эффективные пасы;
- забегания;
- финты;
- изменения после патчей;
- вещи, которые реально влияют на игру.

4. ТАКТИКИ ⚡
- новые мета-тактики;
- формации;
- инструкции;
- тактики, которые используют сильные игроки;
- интересные тактические находки.

5. MARKET 📈
- важные изменения рынка;
- резкое падение/рост цен;
- карты, которые стали выгодными;
- важные события для рынка.

6. ОСТАЛЬНОЕ
Публиковать только если новость действительно очень важная
для обычного игрока FC 27.

========================
ЧТО ЗАПРЕЩЕНО
========================

НЕ публикуй:

- обычные мелкие патчноуты;
- незначительные исправления багов;
- рекламные статьи;
- длинные интервью;
- новости, которые интересны только разработчикам;
- очевидные вещи;
- повторяющиеся новости;
- длинные списки изменений;
- новости без пользы для игрока;
- обычные статьи ради количества постов.

Очень важно:

ЛУЧШЕ НЕ ОПУБЛИКОВАТЬ НОВОСТЬ,
ЧЕМ ОПУБЛИКОВАТЬ НЕИНТЕРЕСНУЮ НОВОСТЬ.

========================
СТИЛЬ
========================

Пост должен читаться за 5–10 секунд.

Максимум: 50–70 слов.

Заголовок — короткий и цепляющий.

Основной текст — максимум 2–4 коротких предложения.

Не используй длинные объяснения.

Не пересказывай всю статью.

Не добавляй ссылки.

НЕ указывай источник.

НЕ пиши:

"Источник:"
"Подробнее:"
"Читать далее:"

Не используй Markdown-ссылки.

Не добавляй огромные списки.

Используй 1–3 подходящих эмодзи.

========================
СЛУХИ
========================

Если новость является слухом или неподтвержденной информацией,
в начале поста обязательно поставь:

⚠️ СЛУХ

Не выдавай слух за подтвержденную информацию.

========================
ФОРМАТ
========================

🔥 Короткий цепляющий заголовок

2–4 коротких предложения с самой важной информацией.

Если действительно необходимо:

📌 Главное:
• один важный пункт
• второй важный пункт

Но используй список только если без него информация будет менее понятной.

========================
ВАЖНО
========================

Если среди найденных материалов НЕТ новости,
которая соответствует этим требованиям,
ответь строго:

NO_NEWS

Если есть несколько материалов об одном событии,
не создавай несколько постов.

Выбери только САМУЮ интересную и важную новость.

Не придумывай факты.
"""


    models = [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]

    for model in models:
        for attempt in range(3):
            try:
                print(f"Trying model: {model}, attempt: {attempt + 1}")

                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )

                if response and response.text:
                    return response.text.strip()

            except Exception as error:
                print(f"Gemini error: {error}")

                if attempt < 2:
                    time.sleep(10)

    return None


def main():
    print("LilsNews started")

    news = get_news()

    print(f"Found {len(news)} news items")

    if not news:
        send_telegram(
            "🤖 LilsNews\n\n"
            "Свежих новостей FC 27 пока не найдено."
        )
        return

    post = generate_post(news)

    if post is None:
        send_telegram(
            "⚠️ LilsNews\n\n"
            "Новости найдены, но Gemini временно недоступен."
        )
        return

    if post == "NO_NEWS":
        print("No important news found.")
        return

    send_telegram(
        "📰 LILSNEWS\n\n"
        + post
    )

    print("Post sent to Telegram")


if __name__ == "__main__":
    main()
