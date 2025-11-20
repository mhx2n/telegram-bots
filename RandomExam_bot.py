import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType
from aiogram.types import Message
from aiogram.filters import Command

# =======================
# কনফিগারেশন অংশ
# =======================

# 👉 তোমার বটের টোকেন এখানে বসাও
BOT_TOKEN = "8501149052:AAHYEaxjtfanY8qzj4nxeBEftdZ-iUZioF8"

# 👉 OWNER এর Telegram numeric user ID (নিজের ID বসাবে)
OWNER_ID = 8455496745  # এখানে নিজের আইডি বসাও

# ডাটাবেইজ ফাইলের নাম
DB_PATH = "quiz_links.db"

# =======================
# Logging সেটাপ
# =======================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


# =======================
# ডাটাবেইজ হেল্পার
# =======================

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    # admins টেবিল
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
    """)

    # quiz_links টেবিল: ফরোয়ার্ড থেকে জেনারেট করা সব লিংক
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quiz_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            channel_title TEXT,
            link TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # settings টেবিল (যদি ভবিষ্যতে টগল সিস্টেম দরকার হয়)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # শুরুতে OWNER কে admin করে রাখি (না থাকলে)
    cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (OWNER_ID,))

    conn.commit()
    conn.close()
    logger.info("Database initialized.")


def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def add_admin(user_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def remove_admin(user_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_all_admins():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM admins ORDER BY user_id")
    rows = cur.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def save_quiz_link(admin_id: int, channel_title: str | None, link: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO quiz_links (admin_id, channel_title, link, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (admin_id, channel_title, link, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def get_links_for_admin(admin_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, channel_title, link, created_at
        FROM quiz_links
        WHERE admin_id = ?
        ORDER BY id ASC
        """,
        (admin_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def clear_links_for_admin(admin_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM quiz_links WHERE admin_id = ?", (admin_id,))
    conn.commit()
    conn.close()


# =======================
# টেলিগ্রাম হেল্পার
# =======================

def build_tg_link_from_forward(message: Message) -> tuple[str | None, str | None]:
    """
    ফরোয়ার্ড করা channel / supergroup মেসেজ থেকে টেলিগ্রাম লিংক বানানোর চেষ্টা।
    রিটার্ন: (channel_title, link) অথবা (None, None)
    """
    fwd_chat = message.forward_from_chat
    fwd_mid = message.forward_from_message_id

    if not fwd_chat or not fwd_mid:
        return None, None

    channel_title = fwd_chat.title or fwd_chat.full_name or fwd_chat.username or "Unknown"

    # public channel হলে username পাওয়া যাবে
    if fwd_chat.username:
        link = f"https://t.me/{fwd_chat.username}/{fwd_mid}"
        return channel_title, link

    # private supergroup / channel হলে id সাধারণত -100xxxxxxxxxx এর মত
    if fwd_chat.type in (ChatType.SUPERGROUP, ChatType.CHANNEL):
        chat_id_str = str(fwd_chat.id)
        # -100 বাদ দিয়ে বাকিটা নিয়ে t.me/c/ আইডি বানানো
        if chat_id_str.startswith("-100"):
            internal_id = chat_id_str[4:]
            link = f"https://t.me/c/{internal_id}/{fwd_mid}"
            return channel_title, link

    # আর কিছু না পারলে None
    return channel_title, None


# =======================
# Bot + Dispatcher
# =======================

# parse_mode একদমই ব্যবহার করছি না, যেন entity error না আসে
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=None)
)
dp = Dispatcher()


# =======================
# কমন হেল্পার
# =======================

async def ensure_owner(message: Message) -> bool:
    if message.from_user is None:
        return False
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ এই কমান্ড শুধু OWNER ব্যবহার করতে পারবে।")
        return False
    return True


async def ensure_admin(message: Message) -> bool:
    if message.from_user is None:
        return False
    if not is_admin(message.from_user.id):
        await message.answer("❌ তুমি এই বটের এডমিন নও। OWNER আগে এডমিন করে দেবে।")
        return False
    return True


# =======================
# কমান্ড হ্যান্ডলার
# =======================

@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    text = (
        "🤖 Quiz Link Collector Bot\n\n"
        "এই বট দিয়ে তুমি চ্যানেল থেকে ফরোয়ার্ড করা কুইজ মেসেজের লিংক "
        "সিরিয়াল অনুযায়ী সেভ করে রাখতে পারবে।\n\n"
        "ব্যবহার:\n"
        "1) OWNER আগে তোমাকে admin করবে।\n"
        "2) এরপর বটের ইনবক্সে চ্যানেল থেকে কুইজ ফরোয়ার্ড করলে "
        "বট অটোমেটিক ঐ মেসেজের টেলিগ্রাম লিংক সেভ করবে।\n"
        "3) /my_links দিয়ে নিজের সব লিংক দেখতে পারবে।\n"
        "4) /clear_my_links দিয়ে নিজের সব লিংক মুছে ফেলতে পারবে।\n\n"
        "OWNER কমান্ড:\n"
        "/add_admin <user_id>\n"
        "/remove_admin <user_id>\n"
        "/admins\n"
    )
    await message.answer(text)


@dp.message(Command("add_admin"))
async def cmd_add_admin(message: Message):
    if not await ensure_owner(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("ব্যবহার: /add_admin <user_id>")
        return

    try:
        uid = int(parts[1].strip())
    except ValueError:
        await message.answer("user_id অবশ্যই সংখ্যা হতে হবে।")
        return

    add_admin(uid)
    await message.answer(f"✅ {uid} এখন থেকে এই বটের admin।")


@dp.message(Command("remove_admin"))
async def cmd_remove_admin(message: Message):
    if not await ensure_owner(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("ব্যবহার: /remove_admin <user_id>")
        return

    try:
        uid = int(parts[1].strip())
    except ValueError:
        await message.answer("user_id অবশ্যই সংখ্যা হতে হবে।")
        return

    if uid == OWNER_ID:
        await message.answer("OWNER কে remove করা যাবে না।")
        return

    remove_admin(uid)
    await message.answer(f"🗑 {uid} এখন আর admin নয়।")


@dp.message(Command("admins"))
async def cmd_admins(message: Message):
    if not await ensure_owner(message):
        return

    admins = get_all_admins()
    if not admins:
        await message.answer("এখনো কোনো admin নেই।")
        return

    lines = ["👑 OWNER: {}".format(OWNER_ID), "", "🧑‍💻 Admin list:"]
    for uid in admins:
        mark = " (OWNER)" if uid == OWNER_ID else ""
        lines.append(f"- {uid}{mark}")
    await message.answer("\n".join(lines))


@dp.message(Command("my_links"))
async def cmd_my_links(message: Message):
    if not await ensure_admin(message):
        return

    uid = message.from_user.id
    rows = get_links_for_admin(uid)
    if not rows:
        await message.answer("তোমার জন্য এখনো কোনো কুইজ লিংক সেভ করা নেই।")
        return

    lines = [f"📚 তোমার মোট কুইজ লিংক: {len(rows)}", ""]
    for i, row in enumerate(rows, start=1):
        title = row["channel_title"] or "Unknown Channel"
        link = row["link"]
        lines.append(f"{i}. {title} → {link}")

    # যদি খুব বড় হয়, টেলিগ্রাম মেসেজ লিমিটের জন্য ভাগ করে পাঠানো
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3500:
            await message.answer(chunk)
            chunk = line + "\n"
        else:
            chunk += line + "\n"
    if chunk:
        await message.answer(chunk)


@dp.message(Command("clear_my_links"))
async def cmd_clear_my_links(message: Message):
    if not await ensure_admin(message):
        return

    uid = message.from_user.id
    clear_links_for_admin(uid)
    await message.answer("🧹 তোমার সব সেভ করা কুইজ লিংক মুছে ফেলা হয়েছে।")


# =======================
# ফরোয়ার্ড করা মেসেজ হ্যান্ডলার
# =======================

@dp.message(F.chat.type == ChatType.PRIVATE)
async def handle_forwarded_quiz(message: Message):
    """
    বটের ইনবক্সে যেকোনো মেসেজ আসলেই এখানে আসবে।
    কিন্তু আমরা কেবল admin + forwarded + channel/supergroup এর মেসেজ নেবো।
    """
    if message.from_user is None:
        return

    # admin না হলে কনফিউজড না করতে, কিছুই বললাম না
    if not is_admin(message.from_user.id):
        return

    # ফরোয়ার্ড করা কিনা চেক
    if not message.forward_from_chat or not message.forward_from_message_id:
        # ফরোয়ার্ড না হলে হালকা feedback
        await message.answer(
            "এটা ফরোয়ার্ড করা মেসেজ না।\n"
            "অনুগ্রহ করে চ্যানেল থেকে কুইজ/মেসেজ ফরোয়ার্ড করে পাঠাও।"
        )
        return

    channel_title, link = build_tg_link_from_forward(message)
    if link is None:
        await message.answer(
            "এই ফরোয়ার্ড থেকে সরাসরি লিংক তৈরি করা যায়নি।\n"
            "সম্ভবত চ্যানেল/গ্রুপের প্রাইভেসি সেটিংসের জন্য এমন হচ্ছে।"
        )
        return

    # সেভ করো
    admin_id = message.from_user.id
    save_quiz_link(admin_id, channel_title, link)

    # আবার সব লিংক নিয়ে সিরিয়াল লিস্ট বানাও
    rows = get_links_for_admin(admin_id)
    total = len(rows)

    lines = [
        #"✅ নতুন কুইজ লিংক সেভ হয়েছে!",
        #"",
        #f"চ্যানেল: {channel_title}",
        #f"লিংক: {link}",
        #"",
        f"Total number of questions: {total}",
        "",
        "📚 Here are the sources of all questions in proper sequence."
        "If you are unsure about any topic, feel free to review it from here.\n\n",
    ]
    for i, row in enumerate(rows, start=1):
        t = row["channel_title"] or "Unknown Channel"
        l = row["link"]
        lines.append(f"{i}. {t} → {l}")

    # বড় হলে ভাগ ভাগ করে পাঠাই
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 3500:
            await message.answer(chunk)
            chunk = line + "\n"
        else:
            chunk += line + "\n"

    if chunk:
        await message.answer(chunk)


# =======================
# main
# =======================

async def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("BOT_TOKEN আগে কোডের উপরে সেট করে নাও।")

    init_db()
    logger.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt.")
