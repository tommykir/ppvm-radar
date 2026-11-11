"""Отправка сообщений в Telegram через бота.

Настройки в файле (по умолчанию «/Users/Плати по миру/.env.telegram»):
  TG_BOT_TOKEN=123456:ABC...
  TG_CHAT_ID=...          # появится сам после `setup`

Использование:
  python3 -I tg_send.py setup              # написать боту /start (или добавить его в чат), потом запустить
  python3 -I tg_send.py send "текст"        # отправить текст (HTML-разметка Telegram)
  python3 -I tg_send.py file путь.md        # отправить содержимое файла (режет на части по 4000 симв.)
Токен никогда не печатается.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

ENV = os.environ.get("TG_ENV_FILE", "/Users/Плати по миру/.env.telegram")


def load():
    cfg = {k: os.environ[k] for k in ("TG_BOT_TOKEN", "TG_CHAT_ID") if os.environ.get(k)}
    if cfg.get("TG_BOT_TOKEN") and cfg.get("TG_CHAT_ID"):
        return cfg
    if os.path.exists(ENV):
        for line in open(ENV, encoding="utf-8"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    return cfg


def api(token, method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}")


def send(text, cfg=None, html=True):
    cfg = cfg or load()
    token, chat = cfg.get("TG_BOT_TOKEN"), cfg.get("TG_CHAT_ID")
    if not token or not chat:
        print("Telegram не настроен: нет TG_BOT_TOKEN или TG_CHAT_ID в", ENV)
        return False
    ok = True
    parts = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for p in parts:
        params = {"chat_id": chat, "text": p, "disable_web_page_preview": "true"}
        if html:
            params["parse_mode"] = "HTML"
        r = api(token, "sendMessage", **params)
        if not r.get("ok") and html:  # разметка сломалась — отправляем как есть
            params.pop("parse_mode")
            r = api(token, "sendMessage", **params)
        if not r.get("ok"):
            print("Telegram: ошибка отправки:", r.get("description"))
            ok = False
    return ok


def setup():
    cfg = load()
    token = cfg.get("TG_BOT_TOKEN")
    if not token:
        print("Сначала впишите TG_BOT_TOKEN=… в", ENV)
        return
    r = api(token, "getUpdates")
    chats = {}
    for u in r.get("result", []):
        m = u.get("message") or u.get("my_chat_member") or u.get("channel_post") or {}
        c = m.get("chat")
        if c:
            chats[c["id"]] = c.get("title") or c.get("username") or c.get("first_name") or ""
    if not chats:
        print("Бот пока не видит ни одного чата: напишите ему /start или добавьте в группу и напишите там что-нибудь.")
        return
    chat_id, name = list(chats.items())[-1]
    lines = [l for l in open(ENV, encoding="utf-8") if not l.startswith("TG_CHAT_ID")]
    lines.append(f"TG_CHAT_ID={chat_id}\n")
    open(ENV, "w", encoding="utf-8").writelines(lines)
    print(f"Чат сохранён: {name} ({chat_id}). Все найденные: {chats}")
    send("Западный радар подключён. Сюда будут приходить срочные алерты и разборы.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "send":
        sys.exit(0 if send(" ".join(sys.argv[2:])) else 1)
    elif cmd == "file":
        sys.exit(0 if send(open(sys.argv[2], encoding="utf-8").read(), html=False) else 1)
    else:
        print(__doc__)
