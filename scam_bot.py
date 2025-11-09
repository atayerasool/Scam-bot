
import os, json, sqlite3
from datetime import datetime
from dotenv import load_dotenv
import telebot
from telebot import types

# ==================== LOAD CONFIG ====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in (os.getenv("ADMIN_IDS") or "").split(",") if x.strip()]
if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN missing in .env")

bot = telebot.TeleBot(BOT_TOKEN)
DB = "scammers.db"

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS scammers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, tg_id TEXT, username TEXT,
        description TEXT, proofs TEXT,
        verified INTEGER DEFAULT 0, added_by INTEGER,
        created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter INTEGER, suspect TEXT,
        description TEXT, proofs TEXT,
        processed INTEGER DEFAULT 0, created_at TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT, first_name TEXT, last_name TEXT, added_at TEXT
    )""")
    conn.commit()
    conn.close()

def save_user(user):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?)",
                (user.id, user.username or "", user.first_name or "", user.last_name or "", datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

# ==================== UTILITIES ====================
def format_scammer(row):
    id_, name, tg_id, username, desc, proofs, verified, added_by, created = row
    tag = "✅ Verified" if verified else "⚠️ Unverified"
    txt = f"📛 Name: {name}\n🆔 ID: {tg_id}\n👤 Username: @{username or 'N/A'}\n{tag}\n📅 Added: {created}\n\n📝 {desc}"
    return txt

def main_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🔎 Search Scammer", "📝 Report Scammer")
    kb.row("📞 Contact Admin")
    return kb

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("➕ Add Scammer", "📋 View Reports")
    kb.row("📣 Broadcast", "🏠 Back")
    return kb

# ==================== COMMANDS ====================
@bot.message_handler(commands=["start"])
def start(m):
    save_user(m.from_user)
    bot.send_message(m.chat.id, "Welcome! Use menu to search or report scammers.", reply_markup=main_menu())

@bot.message_handler(commands=["admin"])
def admin_panel(m):
    if m.from_user.id not in ADMIN_IDS:
        return bot.reply_to(m, "⛔ You are not an admin.")
    bot.send_message(m.chat.id, "Welcome to Admin Panel 👮‍♂️", reply_markup=admin_menu())

# ==================== SEARCH ====================
@bot.message_handler(func=lambda m: m.text == "🔎 Search Scammer")
def search_prompt(m):
    msg = bot.send_message(m.chat.id, "Enter Telegram ID, @username, or name to search:")
    bot.register_next_step_handler(msg, search_scammer)

def search_scammer(m):
    q = m.text.strip().lstrip("@")
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT * FROM scammers WHERE tg_id=? OR username=? OR name LIKE ?", (q, q, f"%{q}%"))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        bot.send_message(m.chat.id, "✅ No record found. This user appears safe.", reply_markup=main_menu())
        return
    for r in rows:
        bot.send_message(m.chat.id, format_scammer(r))
        for p in json.loads(r[5] or "[]"):
            if p["type"] == "photo":
                bot.send_photo(m.chat.id, p["file_id"])
            elif p["type"] == "video":
                bot.send_video(m.chat.id, p["file_id"])
    bot.send_message(m.chat.id, "Search complete.", reply_markup=main_menu())

# ==================== REPORT ====================
report_flow = {}

@bot.message_handler(func=lambda m: m.text == "📝 Report Scammer")
def report_start(m):
    report_flow[m.chat.id] = {"step": 1, "data": {"proofs": []}}
    bot.send_message(m.chat.id, "Enter scammer ID or @username:")

@bot.message_handler(func=lambda m: m.chat.id in report_flow, content_types=["text", "photo", "video"])
def report_steps(m):
    flow = report_flow[m.chat.id]
    step = flow["step"]

    if step == 1:
        flow["data"]["suspect"] = m.text
        flow["step"] = 2
        bot.send_message(m.chat.id, "Describe what happened:")
    elif step == 2:
        flow["data"]["description"] = m.text
        flow["step"] = 3
        bot.send_message(m.chat.id, "Send photos/videos as proof (one by one). Send /done when finished.")
    elif step == 3:
        if m.text == "/done":
            data = flow["data"]
            conn = sqlite3.connect(DB)
            cur = conn.cursor()
            cur.execute("INSERT INTO reports (reporter,suspect,description,proofs,created_at) VALUES (?,?,?,?,?)",
                        (m.from_user.id, data["suspect"], data["description"], json.dumps(data["proofs"]),
                         datetime.utcnow().isoformat()))
            conn.commit(); conn.close()
            bot.send_message(m.chat.id, "✅ Report submitted to admins.", reply_markup=main_menu())
            for admin in ADMIN_IDS:
                bot.send_message(admin, f"📢 New report from @{m.from_user.username}\nScammer: {data['suspect']}\nDesc: {data['description']}")
            report_flow.pop(m.chat.id)
        elif m.content_type == "photo":
            flow["data"]["proofs"].append({"type": "photo", "file_id": m.photo[-1].file_id})
            bot.reply_to(m, "📸 Photo saved. Send more or /done")
        elif m.content_type == "video":
            flow["data"]["proofs"].append({"type": "video", "file_id": m.video.file_id})
            bot.reply_to(m, "🎥 Video saved. Send more or /done")

# ==================== ADMIN FUNCTIONS ====================
add_flow = {}

@bot.message_handler(func=lambda m: m.text == "➕ Add Scammer" and m.from_user.id in ADMIN_IDS)
def add_start(m):
    add_flow[m.chat.id] = {"step": 1, "data": {"proofs": []}}
    bot.send_message(m.chat.id, "Enter scammer name:")

@bot.message_handler(func=lambda m: m.chat.id in add_flow, content_types=["text", "photo", "video"])
def add_steps(m):
    flow = add_flow[m.chat.id]
    step = flow["step"]

    if step == 1:
        flow["data"]["name"] = m.text
        flow["step"] = 2
        bot.send_message(m.chat.id, "Enter Telegram ID or @username:")
    elif step == 2:
        flow["data"]["tg_id"] = m.text
        flow["data"]["username"] = m.text.lstrip("@")
        flow["step"] = 3
        bot.send_message(m.chat.id, "Enter description:")
    elif step == 3:
        flow["data"]["description"] = m.text
        flow["step"] = 4
        bot.send_message(m.chat.id, "Send photo/video proofs. Send /done when finished.")
    elif step == 4:
        if m.text == "/done":
            flow["data"]["verified"] = True
            conn = sqlite3.connect(DB)
            cur = conn.cursor()
            cur.execute("""INSERT INTO scammers
                (name,tg_id,username,description,proofs,verified,added_by,created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (flow["data"]["name"], flow["data"]["tg_id"], flow["data"]["username"],
                 flow["data"]["description"], json.dumps(flow["data"]["proofs"]), 1, m.from_user.id,
                 datetime.utcnow().isoformat()))
            conn.commit(); conn.close()
            bot.send_message(m.chat.id, "✅ Scammer added successfully!", reply_markup=admin_menu())
            add_flow.pop(m.chat.id)
        elif m.content_type == "photo":
            flow["data"]["proofs"].append({"type": "photo", "file_id": m.photo[-1].file_id})
            bot.reply_to(m, "📸 Photo saved. Send more or /done")
        elif m.content_type == "video":
            flow["data"]["proofs"].append({"type": "video", "file_id": m.video.file_id})
            bot.reply_to(m, "🎥 Video saved. Send more or /done")

@bot.message_handler(func=lambda m: m.text == "📋 View Reports" and m.from_user.id in ADMIN_IDS)
def view_reports(m):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT id, reporter, suspect, description, proofs, created_at FROM reports WHERE processed=0")
    rows = cur.fetchall(); conn.close()
    if not rows:
        return bot.send_message(m.chat.id, "No pending reports.", reply_markup=admin_menu())
    for r in rows:
        rid, reporter, suspect, desc, proofs, created = r
        bot.send_message(m.chat.id, f"📨 Report ID {rid}\nFrom: {reporter}\nSuspect: {suspect}\nDesc: {desc}")
        for p in json.loads(proofs or "[]"):
            if p["type"] == "photo": bot.send_photo(m.chat.id, p["file_id"])
            elif p["type"] == "video": bot.send_video(m.chat.id, p["file_id"])

@bot.message_handler(func=lambda m: m.text == "📣 Broadcast" and m.from_user.id in ADMIN_IDS)
def broadcast(m):
    msg = bot.send_message(m.chat.id, "Send message to broadcast to all users:")
    bot.register_next_step_handler(msg, do_broadcast)

def do_broadcast(m):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall(); conn.close()
    sent = 0
    for (uid,) in users:
        try:
            bot.send_message(uid, f"📢 {m.text}")
            sent += 1
        except:
            pass
    bot.send_message(m.chat.id, f"✅ Broadcast sent to {sent} users.", reply_markup=admin_menu())

# ==================== RUN BOT ====================
if __name__ == "__main__":
    init_db()
    print("🤖 Bot is running...")
    bot.infinity_polling()
