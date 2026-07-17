import asyncio
import json
import os
import time
from datetime import datetime
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

# ---------- КОНФИГУРАЦИЯ ----------
proxy_url = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
if proxy_url:
    http_request = HTTPXRequest(
        proxy=proxy_url,
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
    )
else:
    http_request = None
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в переменных окружения")

BASE_URL = "https://majestic.battlefy.com/algs/algs-season-6/lineups/event/6a46b91bb8a9a90012342648/region/europe-middle-east-and-africa"
LIMIT = 20
CHECK_INTERVAL = 60
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
ALL_TEAMS_FILE = os.path.join(DATA_DIR, "all_teams.json")
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "subscribers.json")
TARGET_COUNTRIES = {"KZ", "UA"}
# ----------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://majestic.battlefy.com/",
    "Origin": "https://majestic.battlefy.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# ---------- РАБОТА С ФАЙЛАМИ ----------
def load_subscribers():
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_subscribers(subscribers_set):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(subscribers_set), f, indent=2)

def load_all_teams():
    if os.path.exists(ALL_TEAMS_FILE):
        try:
            with open(ALL_TEAMS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {item["_id"]: item for item in data}
        except Exception as e:
            print(f"[{datetime.now()}] Ошибка загрузки файла: {e}")
            return {}
    return {}

def save_all_teams(teams_dict):
    teams_list = list(teams_dict.values())
    with open(ALL_TEAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(teams_list, f, indent=2, ensure_ascii=False)

def fetch_page(page=0):
    params = {"search": "", "page": page, "limit": LIMIT}
    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", []), data.get("totalPages", 1)
    except Exception as e:
        print(f"[{datetime.now()}] Ошибка при запросе страницы {page}: {e}")
        return None, 0

def get_all_teams():
    all_teams = []
    page = 0
    while True:
        results, total_pages = fetch_page(page)
        if results is None:
            break
        print(f"[{datetime.now()}] Страница {page} из {total_pages}, команд: {len(results)}")
        for item in results:
            _id = item.get("_id")
            team_name = item.get("teamName")
            country = item.get("country")
            members = item.get("members", [])
            player_names = [m.get("inGameName") for m in members if m.get("inGameName")]
            if _id and team_name and country:
                all_teams.append({
                    "_id": _id,
                    "teamName": team_name,
                    "country": country,
                    "players": player_names
                })
        if page >= total_pages - 1:
            break
        page += 1
    return all_teams

# ---------- ФОНОВЫЙ МОНИТОРИНГ ----------
async def monitor_loop(application: Application):
    print(f"[{datetime.now()}] Мониторинг запущен. Проверка каждые {CHECK_INTERVAL} сек.")
    known_teams = load_all_teams()
    if len(known_teams) == 0:
        print("Первый запуск: заполняю базу всех команд (без уведомлений)...")
        current_teams = get_all_teams()
        if current_teams:
            for team in current_teams:
                known_teams[team["_id"]] = team
            save_all_teams(known_teams)
            print(f"✅ База заполнена: {len(known_teams)} команд сохранено.")
        else:
            print("❌ Не удалось получить команды при первом запуске.")
    else:
        print(f"Загружено ранее известных команд: {len(known_teams)}")

    while True:
        current_teams = get_all_teams()
        if not current_teams:
            print(f"[{datetime.now()}] Не удалось получить список. Повтор через {CHECK_INTERVAL} сек.")
            await asyncio.sleep(CHECK_INTERVAL)
            continue

        new_teams = []
        changed_teams = []

        for team in current_teams:
            _id = team["_id"]
            if _id not in known_teams:
                new_teams.append(team)
                known_teams[_id] = team
            else:
                old_players = set(known_teams[_id].get("players", []))
                new_players = set(team.get("players", []))
                if old_players != new_players:
                    changed_teams.append({
                        "team": team,
                        "old_players": old_players,
                        "new_players": new_players
                    })
                    known_teams[_id]["players"] = team["players"]

        if new_teams or changed_teams:
            save_all_teams(known_teams)
            print(f"[{datetime.now()}] База обновлена. Всего сохранено: {len(known_teams)}")

        subscribers = load_subscribers()
        if not subscribers:
            if new_teams or changed_teams:
                print("[{datetime.now()}] Нет подписчиков, уведомления не отправлены.")
        else:
            # Новые команды (только целевые страны)
            target_new = [t for t in new_teams if t["country"].upper() in TARGET_COUNTRIES]
            for team in target_new:
                msg = f"⚡️ Новая команда ALGS!\n\n🏷 {team['teamName']} ({team['country']})"
                if team.get("players"):
                    msg += f"\n👥 Игроки: {', '.join(team['players'])}"
                for chat_id in subscribers:
                    try:
                        await application.bot.send_message(chat_id=chat_id, text=msg)
                    except Exception as e:
                        print(f"[{datetime.now()}] Ошибка отправки пользователю {chat_id}: {e}")

            # Изменения состава (только целевые страны)
            for item in changed_teams:
                team = item["team"]
                if team["country"].upper() not in TARGET_COUNTRIES:
                    continue
                old = ', '.join(item["old_players"]) if item["old_players"] else "—"
                new = ', '.join(item["new_players"]) if item["new_players"] else "—"
                msg = f"🔄 Изменение состава команды!\n\n🏷 {team['teamName']} ({team['country']})\n\nБыло: {old}\nСтало: {new}"
                for chat_id in subscribers:
                    try:
                        await application.bot.send_message(chat_id=chat_id, text=msg)
                    except Exception as e:
                        print(f"[{datetime.now()}] Ошибка отправки пользователю {chat_id}: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

# ---------- КОМАНДЫ БОТА ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для отслеживания команд ALGS из Казахстана (KZ) и Украины (UA).\n\n"
        "Доступные команды:\n"
        "/subscribe – подписаться на уведомления\n"
        "/unsubscribe – отписаться\n"
        "/list – показать все текущие команды из KZ/UA\n"
        "/status – проверить статус подписки\n"
        "/help – эта справка"
    )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subscribers = load_subscribers()
    if chat_id in subscribers:
        await update.message.reply_text("Вы уже подписаны на уведомления.")
    else:
        subscribers.add(chat_id)
        save_subscribers(subscribers)
        await update.message.reply_text("✅ Вы подписались на уведомления о новых командах и изменениях состава.")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subscribers = load_subscribers()
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)
        await update.message.reply_text("❌ Вы отписались от уведомлений.")
    else:
        await update.message.reply_text("Вы и так не подписаны.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    subscribers = load_subscribers()
    if chat_id in subscribers:
        await update.message.reply_text("✅ Вы подписаны на уведомления.")
    else:
        await update.message.reply_text("❌ Вы не подписаны на уведомления. Используйте /subscribe.")

async def list_teams(update: Update, context: ContextTypes.DEFAULT_TYPE):
    known_teams = load_all_teams()
    target_teams = [t for t in known_teams.values() if t["country"].upper() in TARGET_COUNTRIES]
    if not target_teams:
        await update.message.reply_text("На данный момент нет зарегистрированных команд из KZ или UA.")
        return
    target_teams.sort(key=lambda x: x["teamName"])
    lines = []
    for idx, team in enumerate(target_teams, 1):
        players = ", ".join(team.get("players", [])) or "—"
        lines.append(f"{idx}. {team['teamName']} ({team['country']}) — {players}")
    msg = "📋 Команды из KZ/UA:\n\n" + "\n".join(lines)
    if len(msg) > 4000:
        for i in range(0, len(msg), 4000):
            await update.message.reply_text(msg[i:i+4000])
    else:
        await update.message.reply_text(msg)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

def main():
    if http_request:
        application = Application.builder().token(BOT_TOKEN).request(http_request).build()
    else:
        application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("list", list_teams))
    application.add_handler(CommandHandler("help", help_command))

    loop = asyncio.get_event_loop()
    loop.create_task(monitor_loop(application))

    print(f"[{datetime.now()}] Бот запущен. Нажмите Ctrl+C для остановки.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()