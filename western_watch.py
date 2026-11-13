"""Западный радар для «Плати по всему миру».

Использование:
  python3 -I western_watch.py [часов=30]          # полный отчёт для Claude (печать)
  python3 -I western_watch.py --alert [часов=6]   # сторож: новые срочные события сразу в Telegram

Источники:
1. Страницы правил (кто и откуда может пользоваться ИИ-сервисами): сравнение текста с прошлым снимком.
2. Карты сайтов anthropic.com и support.claude.com: какие страницы появились/обновились
   (правки правил видны тут раньше, чем в СМИ).
3. Ленты: блоги ИИ-компаний, западные техСМИ, Google News по запросам, Hacker News.
Срочное (!!!) = изменилась страница правил, обновилась страница /legal или /news,
или заголовок про ИИ/платёжный сервис + Россия/санкции/регионы/баны.
Сбой источника печатается как «недоступен», а не как «пусто».
"""
import datetime as dt
import difflib
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tg_send  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15"}
MSK = dt.timezone(dt.timedelta(hours=3))
STATE = os.environ.get("RADAR_STATE", "/Users/Плати по миру/инфоповоды/запад/.state")

POLICY_PAGES = {
    "Anthropic: поддерживаемые страны": "https://www.anthropic.com/supported-countries",
    "Anthropic: политика использования (AUP)": "https://www.anthropic.com/legal/aup",
    "Anthropic: условия для пользователей": "https://www.anthropic.com/legal/consumer-terms",
    "Anthropic: условия для бизнеса": "https://www.anthropic.com/legal/commercial-terms",
    "Claude: где доступен (справка)": "https://support.anthropic.com/en/articles/8461763-where-can-i-access-claude",
    "OpenAI: поддерживаемые страны API": "https://platform.openai.com/docs/supported-countries",
    "Gemini: где доступен": "https://support.google.com/gemini/answer/13575153?hl=en",
}

SITEMAPS = {
    "anthropic.com": ("https://www.anthropic.com/sitemap.xml", None),
    "support.claude.com (en)": ("https://support.claude.com/sitemap.xml", "/en/"),
}
SITEMAP_HOT_URL = re.compile(r"/legal|supported|countr|region|availab|\bban|suspend|polic|terms|restrict|"
                             r"sanction|acceptable-use|eligib|where-can", re.I)

FEEDS = {
    "OpenAI News": "https://openai.com/news/rss.xml",
    "Google AI Blog": "https://blog.google/technology/ai/rss/",
    "Anthropic Status": "https://status.anthropic.com/history.rss",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "Wired AI": "https://www.wired.com/feed/tag/ai/latest/rss",
    "Engadget": "https://www.engadget.com/rss.xml",
    "9to5Mac": "https://9to5mac.com/feed/",
}

GNEWS_QUERIES = [
    "Anthropic Russia", "Claude Russia", "Anthropic restricted regions", "Anthropic terms update",
    "Claude accounts banned", "OpenAI Russia", "ChatGPT Russia", "OpenAI supported countries",
    "ChatGPT accounts banned", "Gemini Russia", "AI companies Russia users",
    "Apple Russia App Store", "Google Play Russia", "Visa Mastercard Russia",
    "Steam Russia", "Spotify Russia", "YouTube Russia",
    "ChatGPT price", "Claude subscription price", "Midjourney OR Cursor OR Perplexity pricing",
]

# Поездки: русскоязычный Google News (такие новости раньше выходят в российских СМИ и у АТОР)
GNEWS_RU_QUERIES = [
    "безвизовый режим для россиян", "визы для россиян", "въезд для россиян",
    "правила въезда для россиян", "электронная виза для россиян", "новые рейсы из России",
    "туристы из России новые правила", "оплата картами за границей россиянам",
]
TRAVEL = re.compile(r"виз|въезд|рейс|авиа|турист|туризм|границ|отдых|аэропорт|загранпаспорт|за границ|перелёт|перелет|отел", re.I)
TRAVEL_HOT = re.compile(r"безвиз|отмен\w* виз|без виз|упрост\w*|электронн\w* виз|e-?visa|открыл\w*|возобнов\w*|"
                        r"запрет\w* въезд|не пуска|перестал\w* (?:пускать|выдавать)|новые правила въезда|прямы\w* рейс", re.I)

BRAND = re.compile(r"anthropic|claude|openai|chatgpt|gpt-\d|gemini|google play|app store|apple|steam|spotify|"
                   r"netflix|youtube|visa\b|mastercard|paypal|midjourney|cursor|perplexity|grok|xai|"
                   r"playstation|xbox|microsoft|copilot|google", re.I)
RISK = re.compile(r"\brussia|\brussian|росси|sanction|unsupported|supported countr|restricted (?:region|countr)|"
                  r"export control|\b(?:ban|bans|banned|banning|block|blocks|blocked|suspend\w*|restrict\w*|cut off)\b"
                  r"[^.;|]{0,60}\b(?:account|user|countr|region|compan|developer)|"
                  r"\b(?:account|user|countr|region|compan|developer)\w*\b[^.;|]{0,60}\b(?:ban|banned|block|blocked|suspend\w*|restrict\w*|cut off)\b", re.I)
LAUNCH = re.compile(r"\blaunch|\brelease|introduc|unveil|announc|rolls? out|now available|new model|"
                    r"\bprice|\bpricing|\bplans?\b|subscription|free tier|\bfree\b|cheaper|raises? prices|"
                    r"\b(?:gpt|claude|gemini|grok|llama|sora|veo|o\d)[- ]?\d", re.I)
AIBRAND = re.compile(r"anthropic|claude|openai|chatgpt|gpt|gemini|grok|xai|midjourney|perplexity|cursor|"
                     r"sora|veo|copilot|deepseek|llama|meta ai|mistral|suno|runway|elevenlabs|notebooklm", re.I)
OFFICIAL = {"OpenAI News", "Google AI Blog"}
MAJOR = {"TechCrunch AI", "The Verge", "Ars Technica", "Wired AI", "Engadget", "9to5Mac"}
MONEY = re.compile(r"\bprice|\bpricing|\bplans?\b|subscription|\bpayment|paywall|free tier|tariff|\$\d", re.I)


def fetch(url, timeout=30, hops=5, *, with_url=False):
    for _ in range(hops):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "ignore")
                return (text, r.geturl()) if with_url else text
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location")
            if e.code in (307, 308) and loc:
                url = urllib.parse.urljoin(url, loc)
                continue
            raise
    raise RuntimeError("слишком много редиректов")


def page_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div|tr|section)>", "\n", raw)
    raw = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [re.sub(r"\s+", " ", l).strip() for l in raw.split("\n")]
    # служебный мусор (id, токены, счётчики) меняется при каждой загрузке — выбрасываем
    lines = [l for l in lines if len(l) > 2 and not re.fullmatch(r"[\d\W_]+", l)
             and not re.search(r"\b[0-9a-f]{16,}\b|\b\d{12,}\b", l, re.I)]
    return "\n".join(lines)


def parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def ev(level, kind, title, link, date=None, detail="", src=""):
    return {"level": level, "kind": kind, "title": title, "link": link, "date": date,
            "detail": detail, "src": src, "id": hashlib.sha1(f"{kind}|{link}|{title}|{detail[:200]}".encode()).hexdigest()}


# ---------- 1. Страницы правил ----------
def policies(errors):
    out = []
    os.makedirs(STATE, exist_ok=True)
    for name, url in POLICY_PAGES.items():
        key = hashlib.sha1(url.encode()).hexdigest()[:12]
        path = os.path.join(STATE, key + ".txt")
        try:
            text = page_text(fetch(url))
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        if len(text) < 300:
            errors.append(f"{name}: страница почти пустая ({len(text)} симв.)")
            continue
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if old is None:
            out.append(ev(0, "policy", f"{name}: первый снимок сохранён", url))
        elif old != text:
            diff = [l for l in difflib.unified_diff(old.split("\n"), text.split("\n"), lineterm="", n=0)
                    if l[:1] in "+-" and not l.startswith(("+++", "---"))]
            if diff:
                stamp = dt.datetime.now(MSK).strftime("%Y-%m-%d_%H%M")
                with open(os.path.join(STATE, f"{key}.{stamp}.prev.txt"), "w", encoding="utf-8") as f:
                    f.write(old)
                out.append(ev(2, "policy", f"Изменилась страница: {name} ({len(diff)} строк)", url,
                              dt.datetime.now(dt.timezone.utc), "\n".join(l[:300] for l in diff[:30])))
        else:
            out.append(ev(0, "policy", f"{name}: без изменений", url))
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return out


# ---------- 2. Карты сайтов ----------
def sitemaps(since, errors):
    out = []
    for name, (url, must) in SITEMAPS.items():
        try:
            x = fetch(url, timeout=60)
        except Exception as e:
            errors.append(f"sitemap {name}: {e}")
            continue
        for loc, mod in re.findall(r"<loc>([^<]+)</loc>\s*<lastmod>([^<]+)", x):
            if must and must not in loc:
                continue
            d = parse_date(mod)
            if not d or d < since:
                continue
            level = 2 if SITEMAP_HOT_URL.search(loc) or \
                ("anthropic.com" in loc and re.search(r"/news/|/claude-", loc)) else 1
            out.append(ev(level, "sitemap", f"Обновлена страница {name}: {urllib.parse.unquote(loc).split('/')[-1] or loc}",
                          loc, d, src=f"lastmod {mod}"))
    return out


# ---------- 3. Ленты ----------
def feed_items(xml):
    root = ET.fromstring(xml.encode("utf-8"))
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] not in ("item", "entry"):
            continue
        f = {c.tag.split("}")[-1]: c for c in el}
        title = (f["title"].text or "").strip() if "title" in f else ""
        link = (f["link"].text or f["link"].get("href") or "").strip() if "link" in f else ""
        date = None
        for k in ("pubDate", "published", "updated", "date"):
            if k in f and f[k].text:
                date = parse_date(f[k].text)
                if date:
                    break
        src = f["source"].text.strip() if "source" in f and f["source"].text else ""
        out.append((html.unescape(re.sub(r"<[^>]+>", "", title)), link, date, src))
    return out


def near(title, a, b, dist=90):
    return any(abs(x.start() - y.start()) <= dist for x in a.finditer(title) for y in b.finditer(title))


def level_for(title, need_brand=True):
    title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title)  # хвост « - Издание» у Google News
    if need_brand and not BRAND.search(title):
        return -1
    if RISK.search(title) and (near(title, BRAND, RISK) or not need_brand):
        return 2
    if MONEY.search(title) or BRAND.search(title):
        return 1
    return 0


def fetch_many(urls):
    """Параллельная загрузка: {url: (текст | None, ошибка | None)}."""
    from concurrent.futures import ThreadPoolExecutor

    def one(u):
        try:
            return u, fetch(u), None
        except Exception as e:
            return u, None, e
    with ThreadPoolExecutor(max_workers=12) as pool:
        return {u: (t, e) for u, t, e in pool.map(one, urls)}


def feeds(since, errors):
    out = []
    gn_urls = {q: "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"}) for q in GNEWS_QUERIES}
    got = fetch_many(list(FEEDS.values()) + list(gn_urls.values()))
    for name, url in FEEDS.items():
        try:
            raw, err = got[url]
            if err:
                raise err
            for t, link, d, src in feed_items(raw):
                if d and d >= since:
                    lvl = level_for(t, need_brand=name in ("The Verge", "Engadget", "Ars Technica", "9to5Mac"))
                    if name in OFFICIAL and lvl == 0 and AIBRAND.search(t):
                        lvl = 1
                    if name == "Anthropic Status":
                        lvl = 0
                    elif lvl == 1 and AIBRAND.search(t) and (name in OFFICIAL or (name in MAJOR and near(t, AIBRAND, LAUNCH, 70))):
                        lvl = 2  # официальный анонс или релиз/цены у крупного издания
                    if lvl >= 0:
                        out.append(ev(lvl, name, t, link, d))
        except Exception as e:
            errors.append(f"{name}: {e}")
    seen = set()
    for q in GNEWS_QUERIES:
        url = gn_urls[q]
        try:
            raw, err = got[url]
            if err:
                raise err
            for t, link, d, src in feed_items(raw):
                key = re.sub(r"\W+", "", t.lower())[:80]
                if not d or d < since or key in seen:
                    continue
                seen.add(key)
                lvl = level_for(t)
                if lvl >= 1:
                    out.append(ev(lvl, "Google News", t, link, d, src=f"запрос «{q}»"))
        except Exception as e:
            errors.append(f"Google News «{q}»: {e}")
    ru_urls = {q: "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:1d", "hl": "ru", "gl": "RU", "ceid": "RU:ru"}) for q in GNEWS_RU_QUERIES}
    got_ru = fetch_many(list(ru_urls.values()))
    for q, url in ru_urls.items():
        raw, err = got_ru[url]
        if err:
            errors.append(f"Google News RU «{q}»: {err}")
            continue
        try:
            for t, link, d, src in feed_items(raw):
                key = re.sub(r"\W+", "", t.lower())[:80]
                if not d or d < since or key in seen:
                    continue
                seen.add(key)
                core = re.sub(r"\s+-\s+[^-]{2,60}$", "", t)
                if TRAVEL.search(core):
                    lvl = 2 if TRAVEL_HOT.search(core) else 1
                    out.append(ev(lvl, "Google News RU", t, link, d, src=f"поездки, запрос «{q}»"))
        except Exception as e:
            errors.append(f"Google News RU «{q}»: {e}")
    try:
        data = json.loads(fetch("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60"))
        for h in data.get("hits", []):
            d = dt.datetime.fromtimestamp(h["created_at_i"], dt.timezone.utc)
            lvl = level_for(h.get("title") or "")
            if d >= since and lvl >= 1:
                out.append(ev(lvl, "Hacker News", h["title"],
                              f"https://news.ycombinator.com/item?id={h['objectID']}", d,
                              src=h.get("url") or ""))
    except Exception as e:
        errors.append(f"Hacker News: {e}")
    return out


def claude_announcements(since, errors):
    base = "https://claude.com"
    try:
        index = fetch(base + "/blog", timeout=10)
    except Exception as error:
        errors.append(f"Claude blog: {type(error).__name__}")
        return []
    paths = list(dict.fromkeys(re.findall(r'href="(/resources/articles/[^"?#]+)"', index)))
    pages = fetch_many([base + path for path in paths[:15]])
    events = []
    for url, (raw, error) in pages.items():
        if error:
            errors.append(f"{url}: {type(error).__name__}")
            continue
        published = re.search(r'<meta property="article:published_time" content="([^"]+)"', raw)
        title = re.search(r"<title>(.*?)</title>", raw, re.S)
        if not published or not title:
            continue
        stamp = published[1]
        date = parse_date(stamp)
        # The blog sometimes gives only a calendar day. Give those posts a
        # full day for discovery and let the stable event ID prevent repeats.
        if not date or date < since - (dt.timedelta(days=1) if len(stamp) == 10 else dt.timedelta()):
            continue
        events.append(ev(2, "Claude Blog", html.unescape(title[1]), url, date))
    return events


def collect(hours):
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    errors = []
    events = policies(errors) + sitemaps(since, errors) + feeds(since, errors) + claude_announcements(since, errors)
    return events, errors


def fmt_date(d):
    return d.astimezone(MSK).strftime("%d.%m %H:%M") if d else "дата ?"


def report(hours):
    events, errors = collect(hours)
    now = dt.datetime.now(MSK)
    print(f"Западный радар, {now:%d.%m.%Y %H:%M} МСК, окно {hours} ч.")
    print("Уровни: !!! срочно (правила/регионы/Россия/баны), * бренды и деньги.\n")
    groups = {}
    for e in events:
        groups.setdefault(e["kind"], []).append(e)
    order = ["policy", "sitemap"] + [k for k in groups if k not in ("policy", "sitemap")]
    for k in order:
        items = sorted(groups.get(k, []), key=lambda e: (-e["level"], -(e["date"].timestamp() if e["date"] else 0)))
        print(f"### {k}: {len(items)}")
        for e in items[:30]:
            mark = {2: "!!! ", 1: "* "}.get(e["level"], "")
            src = f" — {e['src']}" if e["src"] else ""
            print(f"  {mark}{fmt_date(e['date'])} МСК | {e['title'][:220]}{src} | {e['link']}")
            if e["detail"]:
                for l in e["detail"].split("\n"):
                    print("      " + l)
        print()
    print("### Недоступно:", "; ".join(errors) if errors else "всё прочитано")


def fmt_raw(e):
    t = html.escape(e["title"][:250])
    src = f" ({html.escape(e['src'])})" if e["src"] else ""
    icon = "🔴" if e["kind"] == "policy" or RISK.search(e["title"]) else "🟡"
    out = f"{icon} <b>{html.escape(e['kind'])}</b> {fmt_date(e['date'])}{src}\n{t}\n{html.escape(e['link'])}"
    if e["detail"]:
        out += "\n<pre>" + html.escape(e["detail"][:900]) + "</pre>"
    return out


def fmt_triaged(e, t):
    icon = "🔴 <b>Срочно</b>" if t["level"] == "urgent" else "🟡 <b>На сегодня</b>"
    lines = [f"{icon} · {html.escape(e['kind'])}, {fmt_date(e['date'])} МСК",
             f"<b>{html.escape(t['what'])}</b>"]
    if t.get("russia"):
        lines.append(f"Для россиян: {html.escape(t['russia'])}")
    if t.get("why"):
        lines.append(f"Для нас: {html.escape(t['why'])}")
    if t.get("action"):
        lines.append(f"Что делать: {html.escape(t['action'])}")
    if t.get("hook"):
        lines.append(f"Хук: «{html.escape(t['hook'])}»")
    m = re.search(r"\s-\s([^-]{2,60})$", e["title"]) if e["kind"].startswith("Google News") else None
    name = m.group(1).strip() if m else urllib.parse.urlparse(e["link"]).netloc.replace("www.", "")
    lines.append(f'Источник: <a href="{html.escape(e["link"], quote=True)}">{html.escape(name)}</a>')
    return "\n".join(lines)


# Обычный сбой не меняет правила доступа для россиян. Служебные
# обновления по нему остаются в отчёте, без алертов в Telegram.
OUTAGE = re.compile(
    r"\boutages?\b|\b(?:service|site|claude|chatgpt|gemini|copilot) (?:is )?down\b|"
    r"\b(?:elevated|increased|high) error|\b(?:service disruption|degraded performance)\b|"
    r"\b(?:сбой|сбои|сбоя|массовые ошибки|перебои)\b", re.I
)


def article_key(url):
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.urlencode([
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
        if not k.startswith("utm_") and k not in {"fbclid", "gclid"}
    ])
    return urllib.parse.urlunsplit(("", parsed.netloc.lower().removeprefix("www."),
                                    parsed.path.rstrip("/"), query, ""))


def read_candidates(events, recent, unread=None):
    from concurrent.futures import ThreadPoolExecutor

    def read(event):
        if event["kind"] == "policy":
            return event, event["detail"], ""
        url = event["src"] if event["kind"] == "Hacker News" else event["link"]
        if OUTAGE.search(event["title"]):
            return event, "", "outage"
        if not url.startswith(("https://", "http://")):
            return event, "", "no article URL"
        try:
            from trafilatura import extract
            raw, resolved = fetch(url, timeout=10, with_url=True)
            host = urllib.parse.urlsplit(resolved).hostname
            if host in {"news.google.com", "news.ycombinator.com"}:
                return event, "", "aggregator did not redirect"
            body = extract(raw, url=resolved, include_comments=False) or ""
        except Exception as error:
            reason = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            return event, "", reason
        if len(body) < 200:
            return event, "", "article text unavailable"
        return {**event, "link": resolved}, body[:6500], article_key(resolved)

    links = {r.get("link") for r in recent}
    candidates, contexts = [], {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for event, body, key in pool.map(read, events):
            if not body:
                if unread is not None and key not in {"outage", "no article URL"}:
                    unread.append(event["id"])
                print(f"Radar [{key or 'empty'}]: {event['title'][:240]} | {event['link']}")
                continue
            if key and key in links:
                print(f"Radar [duplicate]: {event['title'][:240]} | {event['link']}")
                continue
            if key:
                links.add(key)
            candidates.append(event)
            contexts[event["id"]] = body
    return candidates, contexts


def alert(hours):
    import triage
    seen_path = os.path.join(STATE, "alerted.json")
    sent_path = os.path.join(STATE, "sent_recent.json")
    os.makedirs(STATE, exist_ok=True)
    # первый запуск (или первая версия с Claude): только запоминаем, чтобы не засыпать канал старым
    first = not os.path.exists(seen_path) or not os.path.exists(sent_path)
    seen = json.load(open(seen_path)) if os.path.exists(seen_path) else {}
    recent = json.load(open(sent_path)) if os.path.exists(sent_path) else []
    events, errors = collect(hours)
    # в Claude идут все новые находки с брендом/деньгами/правилами, а не только «срочные» по словам
    fresh = [e for e in events if e["level"] >= 1 and e["id"] not in seen]
    fresh.sort(key=lambda e: (-e["level"], -(e["date"].timestamp() if e["date"] else 0)))
    now = dt.datetime.now(MSK)
    for e in (fresh if first else fresh[:40]):
        seen[e["id"]] = now.isoformat()
    cutoff = (now - dt.timedelta(days=10)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    if first:
        json.dump(seen, open(seen_path, "w"))
        json.dump(recent, open(sent_path, "w"), ensure_ascii=False)
        print(f"Первый запуск: запомнил {len(fresh)} текущих находок, алерты пойдут с новых.")
        print("Событий всего:", len(events), "| недоступно:", "; ".join(errors) if errors else "ничего")
        return
    batch = fresh[:40]
    sent_n, verdict = 0, "нет новых"
    if batch:
        unread = []
        candidates, contexts = read_candidates(batch, recent, unread)
        for event_id in unread:
            seen.pop(event_id, None)
        result = triage.triage(candidates, contexts, [r["t"] for r in recent])
        if result is None:
            verdict = "разбор недоступен"
            chosen = []
            for event in candidates:
                seen.pop(event["id"], None)
        else:
            chosen = [e for e in candidates if result.get(e["id"], {}).get("level") in ("urgent", "today")]
            chosen.sort(key=lambda e: result[e["id"]]["level"] != "urgent")
            verdict = f"{triage.provider_name()}: в работу {len(chosen)} из {len(candidates)} прочитанных (всего {len(batch)})"
        msgs = [fmt_triaged(e, result[e["id"]]) for e in chosen]
        if msgs:
            text = "\n\n".join(msgs)
            if tg_send.send(text):
                sent_n = len(msgs)
                for e in chosen:
                    t = (result or {}).get(e["id"], {}).get("what") or e["title"]
                    recent.append({"t": t[:300], "at": now.isoformat(),
                                   "link": article_key(e["link"]) if e["kind"] != "policy" else ""})
                recent = recent[-50:]
            else:
                print("Не отправлено в Telegram; находки:")
                for e in chosen:
                    print(" ", e["title"], e["link"])
                for e in fresh:  # не запоминаем, чтобы повторить в следующий раз
                    seen.pop(e["id"], None)
    json.dump(seen, open(seen_path, "w"))
    json.dump(recent, open(sent_path, "w"), ensure_ascii=False)
    log = os.path.join(os.path.dirname(STATE), "alerts.log")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M} новых находок: {len(fresh)}; {verdict}; отправлено: {sent_n}; недоступно: {len(errors)}\n")
    print(f"Новых находок: {len(fresh)}; {verdict}; отправлено: {sent_n}.",
          "Недоступно:", "; ".join(errors) if errors else "ничего")


def test_claude(hours):
    """Прогнать через Claude находки за N часов и прислать отобранное с пометкой «тест»; память не трогает."""
    import triage
    events, errors = collect(hours)
    batch = sorted([e for e in events if e["level"] >= 1],
                   key=lambda e: (-e["level"], -(e["date"].timestamp() if e["date"] else 0)))[:40]
    batch, contexts = read_candidates(batch, [])
    result = triage.triage(batch, contexts, [])
    if result is None:
        print("Claude недоступен — проверьте секрет ANTHROPIC_API_KEY")
        sys.exit(1)
    chosen = [e for e in batch if result.get(e["id"], {}).get("level") in ("urgent", "today")]
    chosen.sort(key=lambda e: result[e["id"]]["level"] != "urgent")
    head = f"🧪 <b>Тест разбора Claude</b>: из {len(batch)} находок за {hours} ч в работу {len(chosen)}"
    tg_send.send("\n\n".join([head] + [fmt_triaged(e, result[e["id"]]) for e in chosen]))
    for e in batch:
        r = result.get(e["id"], {})
        print(f"{r.get('level', '?'):6} | {e['title'][:110]} | {r.get('what', '')[:120]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--test-claude":
        test_claude(int(args[1]) if len(args) > 1 else 12)
    elif args and args[0] == "--alert":
        alert(int(args[1]) if len(args) > 1 else 6)
    else:
        report(int(args[0]) if args else 30)
