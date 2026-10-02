import os
import json
import time
import re
import html
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MEMORY_FILE = "published_news.json"
client = genai.Client(api_key=GEMINI_API_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

SEARCH_QUERIES = [
    "EA FC 27 SBC", "EA FC 27 Ultimate Team cards",
    "EA FC 27 players leaked", "EA FC 27 ratings",
    "EA FC 27 promo", "EA FC 27 meta",
    "EA FC 27 tactics", "EA FC 27 gameplay",
    "EA FC 27 patch", "EA FC 27 objective",
    "EA FC 27 evolution", "EA FC 27 pro players",
]

def make_rss_url(query):
    return "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")

def test_telegram():
    try:
        send_telegram("🤖 LilsNews запущен.\n\nМониторинг EA FC 27 активен.")
        print("Telegram test message sent successfully.")
        return True
    except Exception as e:
        print(f"Telegram test failed: {e}")
        return False

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"Memory error: {e}")
        return []

def save_memory(memory):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory[-200:], f, ensure_ascii=False, indent=2)

def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(text)).strip()

def extract_article_text(soup):
    scripts = soup.find_all("script", type="application/ld+json")
    bodies = []
    for script in scripts:
        try:
            data = json.loads(script.string or script.get_text())
            objects = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
            for obj in objects:
                if isinstance(obj, dict) and obj.get("articleBody"):
                    bodies.append(clean_text(obj["articleBody"]))
        except Exception:
            pass
    if bodies:
        best = max(bodies, key=len)
        if len(best) >= 500:
            return best

    selectors = [
        "article", "[itemprop='articleBody']", ".article-body",
        ".article-content", ".article__body", ".article__content",
        ".post-content", ".post__content", ".entry-content",
        ".story-body", ".story-content", ".articleBody",
        ".articleText", ".article-text", ".content-body", "main"
    ]
    candidates = []
    for selector in selectors:
        try:
            elements = soup.select(selector)
        except Exception:
            continue
        for element in elements:
            for bad in element.select("script,style,noscript,nav,footer,header,.advertisement,.ads,.social,.comments,.comment"):
                bad.decompose()
            text = clean_text(element.get_text(" ", strip=True))
            if len(text) >= 300:
                candidates.append(text)
    if candidates:
        return max(candidates, key=len)

    paragraphs = []
    for p in soup.find_all("p"):
        text = clean_text(p.get_text(" ", strip=True))
        if len(text) >= 40:
            paragraphs.append(text)
    if paragraphs:
        text = "\n".join(paragraphs)
        if len(text) >= 300:
            return text

    for attr in [{"name": "description"}, {"property": "og:description"}, {"name": "twitter:description"}]:
        tag = soup.find("meta", attrs=attr)
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return ""

def decode_google_news_url(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        if r.url and "news.google.com/rss/articles/" not in r.url:
            return r.url
    except Exception as e:
        print(f"Google News decode error: {e}")
    return url

def fetch_article(url):
    try:
        print(f"Fetching: {url}")
        real_url = decode_google_news_url(url)
        if real_url != url:
            print(f"REAL ARTICLE URL: {real_url}")
        r = requests.get(real_url, headers=HEADERS, timeout=25, allow_redirects=True)
        print(f"Final URL: {r.url}")
        if r.status_code != 200:
            print(f"HTTP status: {r.status_code}")
            return None
        if "text/html" not in r.headers.get("content-type", "").lower():
            print("Not HTML")
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup.select("script,style,noscript,svg,iframe,nav,footer"):
            tag.decompose()
        title = clean_text(soup.title.get_text()) if soup.title else ""
        text = extract_article_text(soup)
        print(f"Extracted REAL article text: {len(text)} characters")
        if len(text) < 500:
            return None
        return {"title": title, "url": r.url, "text": text[:18000]}
    except Exception as e:
        print(f"Article fetch error: {e}")
        return None

def get_news():
    news, seen = [], set()
    for query in SEARCH_QUERIES:
        try:
            feed = feedparser.parse(make_rss_url(query))
            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link:
                    continue
                low = title.lower()
                if any(x in low for x in ["fc 26", "fc26", "fc 25", "fc25"]):
                    continue
                if "fc 27" not in low and "fc27" not in low:
                    continue
                normalized = re.sub(r"[\s\-_:]+", "", low)
                if normalized in seen:
                    continue
                seen.add(normalized)
                news.append({"title": title, "link": link, "summary": clean_text(entry.get("summary", ""))})
        except Exception as e:
            print(f"RSS error: {e}")
    return news

def calculate_priority(title):
    low = title.lower()
    points = {
        "sbc":150, "new sbc":50, "card":100, "cards":100,
        "player":80, "players":80, "rating":80, "ratings":80,
        "meta":120, "tactic":110, "tactics":110, "formation":100,
        "gameplay":90, "pro player":100, "vejrgang":120,
        "promo":70, "team 2":70, "team 1":70, "leak":60, "leaked":60,
        "patch":90, "update":50, "objective":80, "evolution":80, "upgrade":60
    }
    return sum(v for k, v in points.items() if k in low)

def select_best_news(news):
    for item in news:
        item["priority"] = calculate_priority(item["title"])
    return sorted(news, key=lambda x: x["priority"], reverse=True)[:15]

def prepare_articles(news):
    prepared = []
    for i, item in enumerate(news, 1):
        print("--------------------------------")
        print(f"Reading article {i}/{len(news)}: {item['title']}")
        article = fetch_article(item["link"])
        if article:
            prepared.append({
                "title": item["title"], "url": article["url"],
                "text": article["text"], "priority": item["priority"]
            })
            print("Article successfully prepared.")
        else:
            print("Could not read article.")
    print("--------------------------------")
    print(f"Successfully read {len(prepared)} articles")
    return prepared

def analyze_news(articles, memory):
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += f"\n===== МАТЕРИАЛ {i} =====\nЗАГОЛОВОК: {a['title']}\nТЕКСТ:\n{a['text']}\n"

    memory_text = ""
    for item in memory[-50:]:
        memory_text += f"TOPIC: {item.get('topic','')}\nPOST: {item.get('post','')}\n"

    prompt = f"""
Ты главный редактор Telegram-канала LilsNews по EA SPORTS FC 27.
Найди максимум ОДНУ реально новую конкретную новость для Ultimate Team.

Используй ТОЛЬКО факты из текстов ниже. Не придумывай игроков, рейтинги,
цены, SBC, тактики или характеристики. Не пиши новость только по заголовку.

Приоритет: SBC, конкретные карты, META/тактика, pro player, gameplay,
патч, promo, leak с конкретикой, objectives, evolutions.

Не повторяй информацию из памяти. Если новых конкретных данных нет,
ответь строго NO_NEWS.

ФОРМАТ:
POST:
📰 LILSNEWS

[КАТЕГОРИЯ]

[КОРОТКИЙ ЗАГОЛОВОК]

[КОНКРЕТНЫЕ ДАННЫЕ]

TOPIC: уникальная тема новости

Память:
{memory_text}

Материалы:
{articles_text}
"""

    models = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
    for model in models:
        for attempt in range(2):
            try:
                print(f"Trying model: {model}, attempt: {attempt + 1}")
                response = client.models.generate_content(model=model, contents=prompt)
                if response and response.text:
                    result = response.text.strip()
                    print("================================")
                    print("GEMINI RAW RESPONSE:")
                    print(result)
                    print("================================")
                    return result
            except Exception as e:
                print(f"Gemini error ({model}): {e}")
                if attempt == 0:
                    time.sleep(5)
    return None

def extract_topic(result):
    if not result:
        return "", ""
    result = result.strip()
    if result.upper() == "NO_NEWS":
        return "", ""
    result = re.sub(r"```(?:text|markdown)?", "", result, flags=re.I)
    result = result.replace("```", "").strip()
    result = re.sub(r"^\s*POST\s*:\s*", "", result, flags=re.I).strip()

    match = re.search(r"TOPIC\s*:\s*(.+)", result, flags=re.I)
    if match:
        topic = match.group(1).strip()
        post = re.sub(r"\n?\s*TOPIC\s*:\s*.+$", "", result, flags=re.I | re.S).strip()
    else:
        post = result
        lines = [x.strip() for x in post.splitlines() if x.strip()]
        topic = lines[0][:150] if lines else "FC 27 News"
        print("WARNING: Gemini did not return TOPIC; generated one automatically.")
    return post, topic

def is_duplicate(topic, post, memory):
    topic = topic.lower().strip()
    post = post.lower().strip()
    for old in memory:
        old_topic = old.get("topic", "").lower().strip()
        old_post = old.get("post", "").lower().strip()
        if old_topic and old_topic == topic:
            return True
        if old_post and len(post) > 50:
            old_words = set(re.findall(r"\b\w+\b", old_post))
            new_words = set(re.findall(r"\b\w+\b", post))
            if new_words and len(old_words & new_words) / len(new_words) > 0.85:
                return True
    return False

def main():
    print("================================")
    print("LilsNews started")
    print("================================")

    memory = load_memory()
    print(f"Memory: {len(memory)} events")

    # Сразу проверяем Telegram. Это сообщение должно прийти при запуске.
    if not test_telegram():
        print("Telegram connection failed. STOP.")
        return

    news = get_news()
    print(f"Found {len(news)} raw news items")
    if not news:
        return

    news = select_best_news(news)
    print(f"Selected {len(news)} high-priority items")
    for i, item in enumerate(news, 1):
        print(f"{i}. [{item['priority']}] {item['title']}")

    articles = prepare_articles(news)
    if not articles:
        print("Could not read any articles.")
        return

    result = analyze_news(articles, memory)
    if result is None:
        print("Gemini unavailable.")
        return

    if result.strip().upper() == "NO_NEWS":
        print("No new important news.")
        return

    post, topic = extract_topic(result)

    if not post:
        print("Empty post.")
        return

    if is_duplicate(topic, post, memory):
        print(f"Duplicate blocked: {topic}")
        return

    try:
        send_telegram(post)
        print("Telegram publication successful.")
    except Exception as e:
        print(f"Telegram publication failed: {e}")
        return

    memory.append({
        "topic": topic,
        "post": post,
        "timestamp": int(time.time())
    })
    save_memory(memory)

    print("================================")
    print(f"Published: {topic}")
    print("================================")

if __name__ == "__main__":
    main()
