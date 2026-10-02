import os
import json
import time
import re
import html
import threading
from urllib.parse import quote_plus, urlparse, urljoin

import requests
import feedparser
from bs4 import BeautifulSoup
from google import genai


# ============================================================
# LILSNEWS — EA SPORTS FC 27 TELEGRAM NEWS BOT
#
# VERSION:
#   Claude realtime bot
#   + automatic source-image extraction
#
# ОСНОВНАЯ ЛОГИКА НЕ МЕНЯЕТСЯ:
#
#   1) Google News RSS
#   2) реальная статья
#   3) Gemini
#   4) готовый пост
#   5) Telegram
#   6) память
#   7) постоянный мониторинг
#
# ДОБАВЛЕНО:
#
#   - поиск главной фотографии статьи
#   - og:image
#   - twitter:image
#   - JSON-LD image
#   - fallback на изображения внутри article/main
#   - скачивание фотографии
#   - отправка фотографии в Telegram
#
# Если фото не найдено/недоступно:
#   пост всё равно публикуется обычным текстом.
#
# Required environment variables:
#   TELEGRAM_TOKEN
#   TELEGRAM_CHAT_ID
#   GEMINI_API_KEY
# ============================================================


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]


MEMORY_FILE = "published_news.json"

# Проверка новостей каждые 2 минуты.
CHECK_INTERVAL = 2 * 60

MAX_RSS_ITEMS_PER_QUERY = 12
MAX_CANDIDATES = 20
MAX_ARTICLES_TO_FETCH = 12
MAX_ARTICLE_CHARS = 16000
WORKER_COUNT = 4


# ------------------------------------------------------------
# IMAGE SETTINGS
# ------------------------------------------------------------

# Максимальный размер картинки, которую пытаемся скачать.
# Telegram позволяет отправлять фотографии до 10 MB.
MAX_IMAGE_BYTES = 9 * 1024 * 1024

# Таймаут скачивания изображения.
IMAGE_TIMEOUT = 20

# Минимальная длина URL картинки.
MIN_IMAGE_URL_LENGTH = 10


# ------------------------------------------------------------
# Telegram caption limit
# ------------------------------------------------------------

# Telegram ограничивает caption фотографии.
# Чтобы не обрезать полноценный пост, используем небольшой запас.
TELEGRAM_CAPTION_LIMIT = 1000


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# HTTP
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
}


# ============================================================
# SEARCH QUERIES
# ============================================================

SEARCH_QUERIES = [
    "EA FC 27 SBC",
    "EA FC 27 Ultimate Team cards",
    "EA FC 27 players leaked",
    "EA FC 27 ratings",
    "EA FC 27 promo",
    "EA FC 27 meta",
    "EA FC 27 tactics",
    "EA FC 27 gameplay",
    "EA FC 27 patch",
    "EA FC 27 objective",
    "EA FC 27 evolution",
    "EA FC 27 pro players",
]


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    """
    Обычная отправка текста.
    """

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

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

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data}"
        )


def send_telegram_photo(
    image_bytes,
    caption=""
):
    """
    Отправляет фотографию в Telegram.

    Используем multipart upload, а не image URL.
    Это надёжнее, потому что Telegram не должен
    самостоятельно скачивать картинку с сайта.
    """

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendPhoto"
    )

    files = {
        "photo": (
            "lilsnews.jpg",
            image_bytes,
            "image/jpeg",
        )
    }

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
    }

    if caption:
        data["caption"] = caption

    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=60,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram sendPhoto error: {result}"
        )

    return result


def test_telegram():
    """
    Тест Telegram.

    Важно:
    здесь оставляем обычное текстовое тестовое сообщение,
    чтобы запуск бота не зависел от поиска фотографий.
    """

    try:

        send_telegram(
            "🤖 LilsNews запущен.\n\n"
            "Мониторинг EA FC 27 активен."
        )

        print(
            "Telegram test message sent successfully."
        )

        return True

    except Exception as error:

        print(
            f"Telegram test failed: {error}"
        )

        return False


# ============================================================
# MEMORY
# ============================================================

def load_memory():

    if not os.path.exists(
        MEMORY_FILE
    ):
        return []

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

            return (
                data
                if isinstance(data, list)
                else []
            )

    except Exception as error:

        print(
            f"Memory error: {error}"
        )

        return []


def save_memory(memory):

    with open(
        MEMORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            memory[-300:],
            file,
            ensure_ascii=False,
            indent=2
        )


def memory_has_item(
    memory,
    link="",
    title=""
):

    link = (
        link or ""
    ).strip().lower()

    title = re.sub(
        r"\s+",
        " ",
        title or ""
    ).strip().lower()

    for old in memory:

        old_link = (
            old.get("link")
            or ""
        ).strip().lower()

        old_title = re.sub(
            r"\s+",
            " ",
            old.get("title") or ""
        ).strip().lower()

        if (
            link
            and old_link
            and link == old_link
        ):
            return True

        if (
            title
            and old_title
            and title == old_title
        ):
            return True

    return False


# ============================================================
# TEXT / HTML
# ============================================================

def clean_text(text):

    if not text:
        return ""

    text = html.unescape(
        str(text)
    )

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def is_fc27_title(title):

    low = title.lower()

    # Никогда не смешиваем старые игры.
    if any(
        x in low
        for x in [
            "fc 26",
            "fc26",
            "fc 25",
            "fc25",
            "fifa 26",
            "fifa 25",
        ]
    ):
        return False

    return (
        "fc 27" in low
        or "fc27" in low
    )


# ============================================================
# GOOGLE NEWS RSS
# ============================================================

def make_rss_url(query):

    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def get_news():

    news = []
    seen = set()

    for query in SEARCH_QUERIES:

        try:

            feed = feedparser.parse(
                make_rss_url(query)
            )

            for entry in feed.entries[
                :MAX_RSS_ITEMS_PER_QUERY
            ]:

                title = clean_text(
                    entry.get(
                        "title",
                        ""
                    )
                )

                link = (
                    entry.get(
                        "link",
                        ""
                    )
                    or ""
                ).strip()

                summary = clean_text(
                    entry.get(
                        "summary",
                        ""
                    )
                    or entry.get(
                        "description",
                        ""
                    )
                )

                if (
                    not title
                    or not link
                ):
                    continue

                if not is_fc27_title(
                    title
                ):
                    continue

                # Google News может вернуть одну
                # и ту же историю через разные запросы.
                normalized = re.sub(
                    r"[^a-z0-9]+",
                    "",
                    title.lower()
                )

                if normalized in seen:
                    continue

                seen.add(normalized)

                news.append({
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published": entry.get(
                        "published",
                        ""
                    ),
                })

        except Exception as error:

            print(
                f"RSS error for "
                f"'{query}': {error}"
            )

    return news


# ============================================================
# PRIORITY
# ============================================================

def calculate_priority(title):

    low = title.lower()

    points = {
        "sbc": 180,
        "new sbc": 70,

        "card": 110,
        "cards": 110,

        "player": 80,
        "players": 80,

        "rating": 80,
        "ratings": 80,

        "meta": 140,

        "tactic": 120,
        "tactics": 120,

        "formation": 105,

        "gameplay": 100,

        "pro player": 110,

        "vejrgang": 130,

        "promo": 80,

        "team 2": 75,
        "team 1": 75,

        "leak": 65,
        "leaked": 65,

        "patch": 110,
        "update": 60,

        "objective": 90,

        "evolution": 90,

        "upgrade": 65,

        "ultimate team": 40,
    }

    return sum(
        value
        for key, value in points.items()
        if key in low
    )


def select_best_news(
    news,
    memory
):

    fresh = []

    for item in news:

        if memory_has_item(
            memory,
            item["link"],
            item["title"]
        ):
            continue

        item["priority"] = (
            calculate_priority(
                item["title"]
            )
        )

        fresh.append(item)

    fresh.sort(
        key=lambda x: x["priority"],
        reverse=True
    )

    return fresh[
        :MAX_CANDIDATES
    ]


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def extract_article_text(soup):

    # --------------------------------------------------------
    # 1) JSON-LD articleBody
    # --------------------------------------------------------

    bodies = []

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        try:

            raw = (
                script.string
                or script.get_text()
            )

            data = json.loads(raw)

            objects = []

            if isinstance(
                data,
                list
            ):
                objects = data

            elif isinstance(
                data,
                dict
            ):

                graph = data.get(
                    "@graph"
                )

                if isinstance(
                    graph,
                    list
                ):
                    objects = graph
                else:
                    objects = [data]

            for obj in objects:

                if not isinstance(
                    obj,
                    dict
                ):
                    continue

                body = obj.get(
                    "articleBody"
                )

                if body:

                    body = clean_text(
                        body
                    )

                    if len(body) >= 300:
                        bodies.append(
                            body
                        )

        except Exception:
            pass

    if bodies:
        return max(
            bodies,
            key=len
        )


    # --------------------------------------------------------
    # 2) Common article containers
    # --------------------------------------------------------

    selectors = [
        "article",
        "[itemprop='articleBody']",
        ".article-body",
        ".article-content",
        ".article__body",
        ".article__content",
        ".post-content",
        ".post__content",
        ".entry-content",
        ".story-body",
        ".story-content",
        ".articleBody",
        ".articleText",
        ".article-text",
        ".content-body",
        "main",
    ]

    candidates = []

    for selector in selectors:

        try:

            elements = soup.select(
                selector
            )

        except Exception:

            continue

        for element in elements:

            copy = BeautifulSoup(
                str(element),
                "html.parser"
            )

            for bad in copy.select(
                "script,style,noscript,"
                "nav,footer,header,"
                ".advertisement,.ads,"
                ".social,.comments,.comment"
            ):

                bad.decompose()

            text = clean_text(
                copy.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 300:
                candidates.append(
                    text
                )

    if candidates:

        return max(
            candidates,
            key=len
        )


    # --------------------------------------------------------
    # 3) Paragraph fallback
    # --------------------------------------------------------

    paragraphs = []

    for p in soup.find_all("p"):

        text = clean_text(
            p.get_text(
                " ",
                strip=True
            )
        )

        if len(text) >= 40:
            paragraphs.append(
                text
            )

    if paragraphs:

        text = "\n".join(
            paragraphs
        )

        if len(text) >= 300:
            return text


    # --------------------------------------------------------
    # 4) Meta description fallback
    # --------------------------------------------------------

    for attrs in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ]:

        tag = soup.find(
            "meta",
            attrs=attrs
        )

        if (
            tag
            and tag.get("content")
        ):

            return clean_text(
                tag["content"]
            )

    return ""


# ============================================================
# IMAGE EXTRACTION
# ============================================================

def normalize_image_url(
    image_url,
    page_url
):
    """
    Делает URL изображения абсолютным.

    Например:
        /images/photo.jpg
    превращается в:
        https://site.com/images/photo.jpg
    """

    if not image_url:
        return ""

    image_url = (
        str(image_url)
        .strip()
    )

    if not image_url:
        return ""

    # Иногда в атрибуте бывает несколько URL.
    if "," in image_url:
        image_url = (
            image_url
            .split(",")[0]
            .strip()
        )

    # Убираем возможные кавычки.
    image_url = image_url.strip(
        "\"'"
    )

    if (
        image_url.startswith(
            "//"
        )
    ):
        parsed = urlparse(
            page_url
        )

        image_url = (
            parsed.scheme
            + ":"
            + image_url
        )

    elif not image_url.startswith(
        (
            "http://",
            "https://",
        )
    ):

        image_url = urljoin(
            page_url,
            image_url
        )

    if not image_url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        return ""

    return image_url


def extract_image_from_jsonld(
    soup,
    page_url
):
    """
    Ищет image в JSON-LD.

    Поддерживает варианты:
        "image": "..."
        "image": ["..."]
        "image": {"url": "..."}
    """

    candidates = []

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        try:

            raw = (
                script.string
                or script.get_text()
            )

            data = json.loads(raw)

            objects = []

            if isinstance(
                data,
                list
            ):

                objects = data

            elif isinstance(
                data,
                dict
            ):

                graph = data.get(
                    "@graph"
                )

                if isinstance(
                    graph,
                    list
                ):
                    objects = graph
                else:
                    objects = [data]

            for obj in objects:

                if not isinstance(
                    obj,
                    dict
                ):
                    continue

                image = obj.get(
                    "image"
                )

                if isinstance(
                    image,
                    str
                ):

                    candidates.append(
                        image
                    )

                elif isinstance(
                    image,
                    list
                ):

                    for item in image:

                        if isinstance(
                            item,
                            str
                        ):
                            candidates.append(
                                item
                            )

                        elif isinstance(
                            item,
                            dict
                        ):

                            url = item.get(
                                "url"
                            )

                            if url:
                                candidates.append(
                                    url
                                )

                elif isinstance(
                    image,
                    dict
                ):

                    url = image.get(
                        "url"
                    )

                    if url:
                        candidates.append(
                            url
                        )

        except Exception:
            pass

    for candidate in candidates:

        normalized = normalize_image_url(
            candidate,
            page_url
        )

        if normalized:
            return normalized

    return ""


def image_url_looks_bad(
    image_url
):
    """
    Отбрасываем очевидные служебные картинки.
    """

    low = image_url.lower()

    bad_words = [
        "favicon",
        "logo",
        "avatar",
        "icon",
        "sprite",
        "tracking",
        "pixel",
        "placeholder",
        "blank.gif",
        "spacer.gif",
    ]

    return any(
        word in low
        for word in bad_words
    )


def extract_article_image(
    soup,
    page_url
):
    """
    Главная функция поиска фотографии.

    Приоритет:

    1. og:image
    2. twitter:image
    3. JSON-LD image
    4. изображения внутри article/main
    """

    candidates = []


    # --------------------------------------------------------
    # 1) Open Graph
    # --------------------------------------------------------

    for attrs in [
        {
            "property": "og:image"
        },
        {
            "property": "og:image:url"
        },
        {
            "property": "og:image:secure_url"
        },
    ]:

        tag = soup.find(
            "meta",
            attrs=attrs
        )

        if tag:

            content = tag.get(
                "content"
            )

            if content:

                candidates.append(
                    (
                        100,
                        normalize_image_url(
                            content,
                            page_url
                        )
                    )
                )


    # --------------------------------------------------------
    # 2) Twitter
    # --------------------------------------------------------

    for attrs in [
        {
            "name": "twitter:image"
        },
        {
            "name": "twitter:image:src"
        },
    ]:

        tag = soup.find(
            "meta",
            attrs=attrs
        )

        if tag:

            content = tag.get(
                "content"
            )

            if content:

                candidates.append(
                    (
                        90,
                        normalize_image_url(
                            content,
                            page_url
                        )
                    )
                )


    # --------------------------------------------------------
    # 3) JSON-LD
    # --------------------------------------------------------

    jsonld_image = (
        extract_image_from_jsonld(
            soup,
            page_url
        )
    )

    if jsonld_image:

        candidates.append(
            (
                80,
                jsonld_image
            )
        )


    # --------------------------------------------------------
    # 4) Main article image
    # --------------------------------------------------------

    containers = []

    try:

        containers.extend(
            soup.select(
                "article"
            )
        )

        containers.extend(
            soup.select(
                "main"
            )
        )

    except Exception:
        pass


    for container in containers:

        try:

            images = container.find_all(
                "img"
            )

        except Exception:

            continue

        for image in images:

            # ------------------------------------------------
            # src / data-src / lazy loading
            # ------------------------------------------------

            src = (
                image.get("src")
                or image.get("data-src")
                or image.get(
                    "data-lazy-src"
                )
                or image.get(
                    "data-original"
                )
            )

            if not src:

                # Иногда URL лежит в srcset.
                srcset = image.get(
                    "srcset"
                )

                if srcset:

                    src = (
                        srcset
                        .split(",")[0]
                        .strip()
                        .split(" ")[0]
                    )

            normalized = (
                normalize_image_url(
                    src,
                    page_url
                )
            )

            if not normalized:
                continue

            if image_url_looks_bad(
                normalized
            ):
                continue

            score = 50

            # Большое изображение вероятнее является
            # главным изображением статьи.
            try:

                width = int(
                    image.get(
                        "width",
                        0
                    )
                    or 0
                )

                height = int(
                    image.get(
                        "height",
                        0
                    )
                    or 0
                )

                if width >= 500:
                    score += 15

                if height >= 300:
                    score += 15

                if width >= 800:
                    score += 10

            except Exception:
                pass

            candidates.append(
                (
                    score,
                    normalized
                )
            )


    # --------------------------------------------------------
    # 5) Все изображения страницы как последний fallback
    # --------------------------------------------------------

    if not candidates:

        try:

            for image in soup.find_all(
                "img"
            ):

                src = (
                    image.get("src")
                    or image.get("data-src")
                    or image.get(
                        "data-lazy-src"
                    )
                    or image.get(
                        "data-original"
                    )
                )

                normalized = (
                    normalize_image_url(
                        src,
                        page_url
                    )
                )

                if not normalized:
                    continue

                if image_url_looks_bad(
                    normalized
                ):
                    continue

                candidates.append(
                    (
                        10,
                        normalized
                    )
                )

        except Exception:
            pass


    # --------------------------------------------------------
    # Choose best candidate
    # --------------------------------------------------------

    cleaned = []

    seen = set()

    for score, image_url in candidates:

        if not image_url:
            continue

        if len(image_url) < MIN_IMAGE_URL_LENGTH:
            continue

        if image_url in seen:
            continue

        seen.add(
            image_url
        )

        cleaned.append(
            (
                score,
                image_url
            )
        )

    if not cleaned:
        return ""

    cleaned.sort(
        key=lambda x: x[0],
        reverse=True
    )

    best_score, best_url = (
        cleaned[0]
    )

    print(
        "IMAGE FOUND: "
        f"[score={best_score}] "
        f"{best_url}"
    )

    return best_url


# ============================================================
# DOWNLOAD IMAGE
# ============================================================

def download_image(
    image_url,
    page_url=""
):
    """
    Скачивает картинку с сайта.

    Возвращает bytes либо None.

    Если первая попытка не удалась,
    пробуем ещё раз с Referer.
    """

    if not image_url:

        return None


    headers = dict(
        HEADERS
    )

    headers["Accept"] = (
        "image/avif,image/webp,"
        "image/apng,image/svg+xml,"
        "image/*,*/*;q=0.8"
    )

    if page_url:

        headers["Referer"] = (
            page_url
        )


    try:

        print(
            "Downloading image: "
            f"{image_url}"
        )

        response = requests.get(
            image_url,
            headers=headers,
            timeout=IMAGE_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )

        print(
            "Image HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "Image download failed: "
                f"HTTP {response.status_code}"
            )

            return None


        content_type = (
            response.headers
            .get(
                "content-type",
                ""
            )
            .lower()
        )


        # Если сервер не указал content-type,
        # всё равно разрешаем загрузку,
        # если URL выглядит как изображение.
        valid_content = (
            content_type.startswith(
                "image/"
            )
            or any(
                extension in image_url.lower()
                for extension in [
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".gif",
                ]
            )
        )

        if not valid_content:

            print(
                "Not an image content type: "
                f"{content_type}"
            )

            return None


        content_length = response.headers.get(
            "content-length"
        )

        if content_length:

            try:

                if (
                    int(content_length)
                    > MAX_IMAGE_BYTES
                ):

                    print(
                        "Image too large."
                    )

                    return None

            except Exception:
                pass


        chunks = []
        total = 0

        for chunk in response.iter_content(
            chunk_size=64 * 1024
        ):

            if not chunk:
                continue

            total += len(
                chunk
            )

            if total > MAX_IMAGE_BYTES:

                print(
                    "Image exceeded size limit."
                )

                return None

            chunks.append(
                chunk
            )


        image_bytes = b"".join(
            chunks
        )


        if not image_bytes:

            print(
                "Downloaded image is empty."
            )

            return None


        print(
            "Image downloaded successfully: "
            f"{len(image_bytes)} bytes"
        )

        return image_bytes


    except Exception as error:

        print(
            f"Image download error: "
            f"{error}"
        )

        return None


# ============================================================
# ARTICLE FETCH
# ============================================================

def decode_google_news_url(url):
    """
    Google News redirect decoding.

    Failure is NOT fatal.
    """

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            allow_redirects=True,
        )

        final_url = (
            response.url
            or ""
        )

        if (
            final_url
            and
            "news.google.com/rss/articles/"
            not in final_url
        ):

            return final_url

    except Exception as error:

        print(
            f"Google News decode error: "
            f"{error}"
        )

    return url


def fetch_article(item):
    """
    Получает:

        title
        url
        text
        image_url
        source_type
        priority

    Даже если текст статьи не удалось получить,
    RSS fallback всё равно возвращается.

    Изображение ищется независимо от того,
    удалось ли извлечь полный текст.
    """

    source_url = item["link"]

    rss_title = item["title"]

    rss_summary = item.get(
        "summary",
        ""
    )

    print(
        f"Fetching: {source_url}"
    )


    # --------------------------------------------------------
    # Resolve Google News URL
    # --------------------------------------------------------

    real_url = decode_google_news_url(
        source_url
    )

    if real_url != source_url:

        print(
            f"REAL ARTICLE URL: "
            f"{real_url}"
        )


    # --------------------------------------------------------
    # Try real article
    # --------------------------------------------------------

    try:

        response = requests.get(
            real_url,
            headers=HEADERS,
            timeout=25,
            allow_redirects=True,
        )

        print(
            f"Final URL: "
            f"{response.url}"
        )

        print(
            f"HTTP status: "
            f"{response.status_code}"
        )


        if response.status_code == 200:

            content_type = (
                response.headers
                .get(
                    "content-type",
                    ""
                )
                .lower()
            )


            if "text/html" in content_type:

                soup = BeautifulSoup(
                    response.text,
                    "html.parser"
                )


                # ------------------------------------------------
                # IMAGE
                # ------------------------------------------------

                image_url = (
                    extract_article_image(
                        soup,
                        response.url
                    )
                )


                # ------------------------------------------------
                # TEXT
                # ------------------------------------------------

                title = (
                    clean_text(
                        soup.title.get_text()
                    )
                    if soup.title
                    else rss_title
                )

                text = extract_article_text(
                    soup
                )


                if len(text) >= 500:

                    print(
                        "Full article extracted: "
                        f"{len(text)} characters"
                    )

                    return {
                        "title": rss_title,
                        "url": response.url,
                        "text": text[
                            :MAX_ARTICLE_CHARS
                        ],
                        "image_url": image_url,
                        "source_type": "full_article",
                        "priority": item.get(
                            "priority",
                            0
                        ),
                    }


                print(
                    "Article text too short "
                    f"({len(text)} chars). "
                    "Using RSS fallback."
                )


                # Даже если текст короткий,
                # найденное изображение сохраняем.
                if image_url:

                    print(
                        "Image found despite "
                        "RSS text fallback."
                    )

                    fallback = clean_text(
                        rss_summary
                    )

                    if len(fallback) < 20:

                        fallback = (
                            "No usable RSS "
                            "summary was provided."
                        )

                    return {
                        "title": rss_title,
                        "url": response.url,
                        "text": fallback[
                            :5000
                        ],
                        "image_url": image_url,
                        "source_type":
                            "rss_fallback",
                        "priority": item.get(
                            "priority",
                            0
                        ),
                    }


    except Exception as error:

        print(
            f"Article fetch error: "
            f"{error}"
        )


    # --------------------------------------------------------
    # RSS FALLBACK
    # --------------------------------------------------------

    fallback = clean_text(
        rss_summary
    )


    if len(fallback) < 20:

        fallback = (
            "No usable RSS "
            "summary was provided."
        )


    print(
        "Using RSS fallback: "
        "title + "
        f"{len(fallback)} "
        "summary characters"
    )


    return {
        "title": rss_title,
        "url": (
            real_url
            if real_url != source_url
            else source_url
        ),
        "text": fallback[
            :5000
        ],
        "image_url": "",
        "source_type":
            "rss_fallback",
        "priority": item.get(
            "priority",
            0
        ),
    }


# ============================================================
# PREPARE ARTICLES
# ============================================================

def prepare_articles(news):

    prepared = []

    for i, item in enumerate(
        news,
        1
    ):

        print(
            "--------------------------------"
        )

        print(
            f"Reading article "
            f"{i}/{len(news)}: "
            f"{item['title']}"
        )


        article = fetch_article(
            item
        )


        if article:

            prepared.append({

                "title":
                    article["title"],

                "url":
                    article["url"],

                "text":
                    article["text"],

                "image_url":
                    article.get(
                        "image_url",
                        ""
                    ),

                "priority":
                    article["priority"],

                "source_type":
                    article["source_type"],
            })


            print(
                "Article prepared "
                f"({article['source_type']})."
            )

            if article.get(
                "image_url"
            ):

                print(
                    "Article image: "
                    f"{article['image_url']}"
                )

            else:

                print(
                    "Article image: "
                    "NOT FOUND"
                )


        else:

            print(
                "Could not prepare article."
            )


    print(
        "--------------------------------"
    )

    print(
        "Prepared "
        f"{len(prepared)} "
        "usable news items"
    )

    return prepared


# ============================================================
# GEMINI
# ============================================================

def analyze_news(
    articles,
    memory
):
    """
    Turn ONE article into ONE detailed Telegram post.

    ЛОГИКА CLAUDE СОХРАНЕНА.

    Фотография сюда НЕ передаётся как материал
    для генерации/анализа.

    image_url используется только Telegram-слоем.
    """

    if not articles:
        return None


    article = articles[0]


    memory_text = "\n".join(
        f"TOPIC: "
        f"{x.get('topic','')}\n"
        f"TITLE: "
        f"{x.get('title','')}"
        for x in memory[-80:]
    )


    prompt = f"""
Ты главный редактор Telegram-канала LilsNews по EA SPORTS FC 27.

Обработай ЭТУ ОДНУ НОВОСТЬ и подготовь подробный пост для игрока EA FC 27 Ultimate Team.

ПРАВИЛА:

- Используй ТОЛЬКО факты из материала.
- НИЧЕГО не придумывай. Если в статье нет OVR, цены, требований SBC,
  PlayStyles, дат или других данных — НЕ ДОБАВЛЯЙ их от себя.
- Если материал неполный/RSS fallback, честно укажи, что подробности
  в источнике недоступны из полученного текста.
- Не повторяй тему, которая уже опубликована.
- Не делай рейтинг источников и не выдумывай мнение автора.
- Сохраняй конкретные цифры, имена, даты, названия карт/SBC и условия,
  если они есть в материале.
- Пост должен быть содержательным, обычно 120–300 слов, но не растягивай
  его, если в источнике мало информации.
- Пиши на русском.
- В конце обязательно добавь строку TOPIC: уникальная тема этой новости.

ФОРМАТ:

📰 LILSNEWS

[КАТЕГОРИЯ]

[ЗАГОЛОВОК]

[ПОДРОБНОСТИ: что произошло, какие игроки/карты/SBC, OVR, цены,
требования, даты, апгрейды, статы и т.д. — только если это есть в тексте]

Источник: {article['url']}

TOPIC: уникальная тема новости

УЖЕ ОПУБЛИКОВАНО:
{memory_text}

МАТЕРИАЛ:

ЗАГОЛОВОК:
{article['title']}

ТИП:
{article['source_type']}

ТЕКСТ:
{article['text']}
"""


    models = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
    ]


    for model in models:

        for attempt in range(2):

            try:

                print(
                    f"Calling Gemini: "
                    f"{model}, "
                    f"attempt {attempt + 1}"
                )


                response = (
                    client.models.generate_content(
                        model=model,
                        contents=prompt,
                    )
                )


                if (
                    response
                    and response.text
                ):

                    result = (
                        response.text
                        .strip()
                    )

                    print(
                        "Gemini response received: "
                        f"{article['title']}"
                    )

                    return result


            except Exception as error:

                print(
                    f"Gemini error "
                    f"({model}): "
                    f"{error}"
                )

                if attempt == 0:

                    time.sleep(3)


    return None


# ============================================================
# OUTPUT PARSING
# ============================================================

def extract_topic(result):

    if not result:
        return "", ""


    result = result.strip()


    if result.upper() == "NO_NEWS":

        return "", ""


    result = re.sub(
        r"```(?:text|markdown)?",
        "",
        result,
        flags=re.I,
    )

    result = result.replace(
        "```",
        ""
    ).strip()


    result = re.sub(
        r"^\s*POST\s*:\s*",
        "",
        result,
        flags=re.I,
    ).strip()


    match = re.search(
        r"TOPIC\s*:\s*(.+)",
        result,
        flags=re.I,
    )


    if match:

        topic = (
            match.group(1)
            .strip()
        )

        post = re.sub(
            r"\n?\s*TOPIC\s*:\s*.+$",
            "",
            result,
            flags=re.I | re.S,
        ).strip()


    else:

        post = result

        lines = [
            line.strip()
            for line in post.splitlines()
            if line.strip()
        ]

        topic = (
            lines[0][:150]
            if lines
            else "FC 27 News"
        )

        print(
            "WARNING: Gemini did not "
            "return TOPIC; generated "
            "one automatically."
        )


    return post, topic


# ============================================================
# DUPLICATE CHECK
# ============================================================

def is_duplicate(
    topic,
    post,
    memory
):

    topic_normalized = (
        topic
        .lower()
        .strip()
    )


    post_words = set(
        re.findall(
            r"\w+",
            post.lower()
        )
    )


    for old in memory:

        old_topic = (
            old.get(
                "topic",
                ""
            )
            .lower()
            .strip()
        )


        if (
            old_topic
            and
            old_topic
            == topic_normalized
        ):

            return True


        old_post = (
            old.get(
                "post",
                ""
            )
            .lower()
        )


        old_words = set(
            re.findall(
                r"\w+",
                old_post
            )
        )


        if (
            len(post_words) >= 12
            and
            len(old_words) >= 12
        ):

            intersection = (
                post_words
                & old_words
            )


            similarity = (
                len(intersection)
                /
                max(
                    len(post_words),
                    len(old_words)
                )
            )


            if similarity >= 0.75:

                return True


    return False


# ============================================================
# PUBLISH WITH IMAGE
# ============================================================

def publish_post(
    post,
    article
):
    """
    Основная новая функция.

    Порядок:

        1. Есть image_url?
        2. Скачиваем изображение.
        3. Если изображение успешно скачано:
             - короткий пост -> фото + caption
             - длинный пост -> фото + полный текст
        4. Если фото скачать нельзя:
             - обычный текстовый пост

    Главное:
        фотография НИКОГДА не должна ломать публикацию.
    """

    image_url = (
        article.get(
            "image_url",
            ""
        )
        or ""
    ).strip()

    article_url = (
        article.get(
            "url",
            ""
        )
        or ""
    )


    # --------------------------------------------------------
    # NO IMAGE
    # --------------------------------------------------------

    if not image_url:

        print(
            "No article image found. "
            "Publishing text only."
        )

        send_telegram(
            post
        )

        return "text"


    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    image_bytes = download_image(
        image_url,
        page_url=article_url
    )


    if not image_bytes:

        print(
            "Could not download article "
            "image. Publishing text only."
        )

        send_telegram(
            post
        )

        return "text"


    # --------------------------------------------------------
    # PHOTO + CAPTION
    # --------------------------------------------------------

    if len(post) <= TELEGRAM_CAPTION_LIMIT:

        print(
            "Publishing photo + post "
            "as one Telegram message."
        )

        send_telegram_photo(
            image_bytes,
            caption=post
        )

        return "photo_caption"


    # --------------------------------------------------------
    # LONG POST
    # --------------------------------------------------------

    print(
        "Post is too long for Telegram "
        "photo caption."
    )

    print(
        "Publishing photo first, "
        "then full text."
    )


    # Первое сообщение — фотография.
    send_telegram_photo(
        image_bytes
    )


    # Второе сообщение — полный пост.
    send_telegram(
        post
    )


    return "photo_plus_text"


# ============================================================
# REAL-TIME MONITORING
# ============================================================

memory_lock = threading.Lock()

inflight_links = set()

inflight_lock = threading.Lock()


# ============================================================
# PROCESS ONE NEWS
# ============================================================

def process_one_news(item):

    """
    Fetch, analyze and publish one news item independently.
    """

    link = item.get(
        "link",
        ""
    )


    try:

        print(
            "--------------------------------"
        )

        print(
            f"WORKER: {item['title']}"
        )


        # ----------------------------------------------------
        # FETCH ARTICLE + IMAGE
        # ----------------------------------------------------

        article = fetch_article(
            item
        )


        if not article:

            print(
                "WORKER: article could "
                "not be prepared"
            )

            return


        # ----------------------------------------------------
        # MEMORY SNAPSHOT
        # ----------------------------------------------------

        with memory_lock:

            memory_snapshot = (
                load_memory()
            )


        # ----------------------------------------------------
        # GEMINI
        # ----------------------------------------------------

        result = analyze_news(
            [article],
            memory_snapshot
        )


        if result is None:

            print(
                "WORKER: Gemini unavailable "
                f"for: {item['title']}"
            )

            return


        if (
            result
            .strip()
            .upper()
            == "NO_NEWS"
        ):

            print(
                "WORKER: NO_NEWS: "
                f"{item['title']}"
            )

            return


        # ----------------------------------------------------
        # PARSE
        # ----------------------------------------------------

        post, topic = extract_topic(
            result
        )


        if not post:

            print(
                "WORKER: empty post"
            )

            return


        # ----------------------------------------------------
        # DUPLICATE + PUBLISH
        # ----------------------------------------------------

        with memory_lock:

            current_memory = (
                load_memory()
            )


            if is_duplicate(
                topic,
                post,
                current_memory
            ):

                print(
                    "WORKER: duplicate "
                    f"blocked: {topic}"
                )

                return


            # ------------------------------------------------
            # PUBLISH
            # ------------------------------------------------

            publication_type = (
                publish_post(
                    post,
                    article
                )
            )


            # ------------------------------------------------
            # MEMORY
            # ------------------------------------------------

            now = int(
                time.time()
            )


            current_memory.append({

                "topic":
                    topic,

                "post":
                    post,

                "title":
                    item["title"],

                "link":
                    article["url"],

                "image_url":
                    article.get(
                        "image_url",
                        ""
                    ),

                "publication_type":
                    publication_type,

                "timestamp":
                    now,
            })


            save_memory(
                current_memory
            )


        print(
            "WORKER: Telegram "
            "publication successful."
        )

        print(
            "WORKER: Publication type: "
            f"{publication_type}"
        )

        print(
            f"WORKER: Published: "
            f"{topic}"
        )


    except Exception as error:

        print(
            "WORKER ERROR for "
            f"'{item.get('title','')}': "
            f"{error}"
        )


    finally:

        with inflight_lock:

            inflight_links.discard(
                link
            )


# ============================================================
# SUBMIT NEW ITEMS
# ============================================================

def submit_new_items(
    executor
):
    """
    Find fresh items and immediately
    hand them to background workers.
    """

    news = get_news()

    print(
        f"Found {len(news)} raw news items"
    )


    if not news:

        return


    with memory_lock:

        memory = load_memory()


    fresh = []


    for item in news:

        link = item.get(
            "link",
            ""
        )


        if not link:

            continue


        if memory_has_item(
            memory,
            link,
            item.get(
                "title",
                ""
            )
        ):

            continue


        with inflight_lock:

            if link in inflight_links:

                continue


        item["priority"] = (
            calculate_priority(
                item["title"]
            )
        )


        fresh.append(
            item
        )


    fresh.sort(
        key=lambda x: x["priority"],
        reverse=True
    )


    fresh = fresh[
        :MAX_CANDIDATES
    ]


    # --------------------------------------------------------
    # Queue
    # --------------------------------------------------------

    queued = []


    for item in fresh:

        link = item["link"]


        with inflight_lock:

            if link in inflight_links:

                continue


            inflight_links.add(
                link
            )


            queued.append(
                item
            )


    print(
        f"Queued {len(queued)} "
        "new items for immediate processing"
    )


    for i, item in enumerate(
        queued,
        1
    ):

        print(
            f"{i}. "
            f"[{item['priority']}] "
            f"{item['title']}"
        )


        executor.submit(
            process_one_news,
            item
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "================================"
    )

    print(
        "LilsNews REAL-TIME "
        "monitor started"
    )

    print(
        "================================"
    )

    print(
        f"Check interval: "
        f"{CHECK_INTERVAL // 60} minutes"
    )

    print(
        f"Background workers: "
        f"{WORKER_COUNT}"
    )

    print(
        "Image mode: SOURCE IMAGES"
    )

    print(
        "================================"
    )


    memory = load_memory()


    print(
        f"Memory: "
        f"{len(memory)} events"
    )


    # --------------------------------------------------------
    # Telegram test
    # --------------------------------------------------------

    if not test_telegram():

        print(
            "Telegram connection "
            "failed. STOP."
        )

        return


    # --------------------------------------------------------
    # Thread pool
    # --------------------------------------------------------

    executor = ThreadPoolExecutor(
        max_workers=WORKER_COUNT
    )


    try:

        while True:

            try:

                submit_new_items(
                    executor
                )

            except Exception as error:

                print(
                    "Monitor cycle error: "
                    f"{error}"
                )


            print(
                "Next scan in "
                f"{CHECK_INTERVAL // 60} "
                "minutes...",
                flush=True
            )


            time.sleep(
                CHECK_INTERVAL
            )


    except KeyboardInterrupt:

        print(
            "LilsNews stopped by user."
        )


    finally:

        executor.shutdown(
            wait=False,
            cancel_futures=False
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
