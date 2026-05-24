# ═══════════════════════════════════════════════════════════════
#  ☣️  БИО-ВОЙНЫ  —  Telegram Bot  (Render-ready, single file)
#  Стек: Python 3.11, aiogram 3.7, aiosqlite
# ═══════════════════════════════════════════════════════════════

import asyncio, logging, os, random, string, datetime, json
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)

from aiohttp import web

# ───────────────────────────────────────────────────────────────
#  КОНФИГ
# ───────────────────────────────────────────────────────────────

BOT_TOKEN      = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "866169035"))
PORT           = int(os.getenv("PORT", "8080"))
DB_PATH        = "biowar.db"

INFECT_COOLDOWN   = 3600   # 1 час между атаками
FEVER_HEAL_COST   = 50.0   # стоимость лечения горячки
FEVER_DURATION    = 3600   # базовая длительность горячки в секундах

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────────────
#  KEEP-ALIVE
# ───────────────────────────────────────────────────────────────

async def health(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Keep-alive сервер запущен на порту {PORT}")

# ───────────────────────────────────────────────────────────────
#  БАЗА ДАННЫХ
# ───────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id            INTEGER PRIMARY KEY,
                username           TEXT,
                full_name          TEXT,
                lab_name           TEXT    DEFAULT 'Лаборатория',
                lab_id             TEXT    UNIQUE,
                pathogen_name      TEXT    DEFAULT 'засекречено',
                infection          INTEGER DEFAULT 1,
                immunity           INTEGER DEFAULT 1,
                lethality          INTEGER DEFAULT 1,
                security           INTEGER DEFAULT 1,
                pathogens_ready    INTEGER DEFAULT 1,
                pathogens_max      INTEGER DEFAULT 1,
                scientist_level    INTEGER DEFAULT 1,
                bio_exp            INTEGER DEFAULT 0,
                bio_resource       REAL    DEFAULT 100.0,
                operations_success INTEGER DEFAULT 0,
                operations_total   INTEGER DEFAULT 0,
                prevented_success  INTEGER DEFAULT 0,
                prevented_total    INTEGER DEFAULT 0,
                infected_count     INTEGER DEFAULT 0,
                diseases_count     INTEGER DEFAULT 1,
                clan_id            INTEGER DEFAULT NULL,
                is_banned          INTEGER DEFAULT 0,
                event_immunity     INTEGER DEFAULT 0,
                last_attack_at     TIMESTAMP DEFAULT NULL,
                is_infected        INTEGER DEFAULT 0,
                fever_until        TIMESTAMP DEFAULT NULL,
                infected_until     TIMESTAMP DEFAULT NULL,
                infected_by        INTEGER DEFAULT NULL,
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Миграции для старых БД
        for col, definition in [
            ("last_attack_at",  "TIMESTAMP DEFAULT NULL"),
            ("is_infected",     "INTEGER DEFAULT 0"),
            ("fever_until",     "TIMESTAMP DEFAULT NULL"),
            ("infected_until",  "TIMESTAMP DEFAULT NULL"),
            ("infected_by",     "INTEGER DEFAULT NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE players ADD COLUMN {col} {definition}")
            except Exception:
                pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS clans (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT UNIQUE,
                tag           TEXT UNIQUE,
                leader_id     INTEGER,
                description   TEXT    DEFAULT '',
                members_count INTEGER DEFAULT 1,
                bio_resource  REAL    DEFAULT 0.0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS upgrade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, skill TEXT, amount INTEGER, cost REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT,
                title       TEXT,
                description TEXT,
                payload     TEXT    DEFAULT '{}',
                is_active   INTEGER DEFAULT 1,
                ends_at     TIMESTAMP,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attack_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                attacker_id INTEGER,
                target_id   INTEGER,
                success     INTEGER,
                atk_roll    INTEGER,
                def_roll    INTEGER,
                reward      REAL    DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# ── Игроки ─────────────────────────────────────────────────────

async def get_player(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM players WHERE user_id=?", (user_id,)) as c:
            r = await c.fetchone()
            return dict(r) if r else None

async def get_player_by_username(username: str) -> Optional[dict]:
    uname = username.lstrip("@").lower()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE LOWER(username)=?", (uname,)
        ) as c:
            r = await c.fetchone()
            return dict(r) if r else None

async def create_player(user_id, username, full_name):
    lab_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO players
            (user_id,username,full_name,lab_id,lab_name,pathogen_name)
            VALUES (?,?,?,?,?,?)
        """, (user_id, username, full_name, lab_id,
              f"Лаборатория #{lab_id[:4]}", "засекречено"))
        await db.commit()
    return await get_player(user_id)

async def get_or_create(user_id, username, full_name):
    p = await get_player(user_id)
    return p or await create_player(user_id, username, full_name)

async def update_player(user_id, **kw):
    if not kw: return
    fields = ", ".join(f"{k}=?" for k in kw)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE players SET {fields} WHERE user_id=?", [*kw.values(), user_id])
        await db.commit()

async def is_banned(uid):
    p = await get_player(uid)
    return bool(p and p["is_banned"])

async def is_admin(uid):
    if uid == SUPER_ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)) as c:
            return await c.fetchone() is not None

async def add_admin(uid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (uid,))
        await db.commit()

async def get_all_players():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT user_id,username,full_name,is_banned FROM players") as c:
            return [dict(r) for r in await c.fetchall()]

async def get_top_players(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM players WHERE is_banned=0 ORDER BY infected_count DESC LIMIT ?",
            (limit,)
        ) as c:
            return [dict(r) for r in await c.fetchall()]

# ── Кланы ──────────────────────────────────────────────────────

async def get_clan(clan_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clans WHERE id=?", (clan_id,)) as c:
            r = await c.fetchone()
            return dict(r) if r else None

async def get_clan_by_name(name: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clans WHERE LOWER(name)=?", (name.lower(),)) as c:
            r = await c.fetchone()
            return dict(r) if r else None

async def get_clan_by_tag(tag: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clans WHERE LOWER(tag)=?", (tag.lower(),)) as c:
            r = await c.fetchone()
            return dict(r) if r else None

async def create_clan(name: str, tag: str, leader_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO clans (name,tag,leader_id) VALUES (?,?,?)",
                (name, tag, leader_id)
            )
            await db.commit()
        except Exception:
            return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM clans WHERE leader_id=? ORDER BY id DESC LIMIT 1", (leader_id,)) as c:
            r = await c.fetchone()
            return dict(r) if r else None

# ── События ────────────────────────────────────────────────────

async def create_event(etype, title, description, payload, hours):
    ends_at = datetime.datetime.utcnow() + datetime.timedelta(hours=hours)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO events (type,title,description,payload,ends_at) VALUES (?,?,?,?,?)",
            (etype, title, description, payload, ends_at)
        )
        await db.commit()

async def deactivate_event(eid):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE events SET is_active=0 WHERE id=?", (eid,))
        await db.commit()

async def get_active_events():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM events WHERE is_active=1") as c:
            return [dict(r) for r in await c.fetchall()]

# ── Атаки ──────────────────────────────────────────────────────

async def log_attack(attacker_id, target_id, success, atk_roll, def_roll, reward=0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO attack_log (attacker_id,target_id,success,atk_roll,def_roll,reward) VALUES (?,?,?,?,?,?)",
            (attacker_id, target_id, success, atk_roll, def_roll, reward)
        )
        await db.commit()

# ───────────────────────────────────────────────────────────────
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ───────────────────────────────────────────────────────────────

def fever_active(p: dict) -> bool:
    """Проверяет, есть ли у игрока активная горячка."""
    if not p.get("fever_until"):
        return False
    try:
        fu = datetime.datetime.fromisoformat(str(p["fever_until"]))
        return datetime.datetime.utcnow() < fu
    except Exception:
        return False

def infected_active(p: dict) -> bool:
    """Проверяет, заражён ли игрок в данный момент."""
    if not p.get("is_infected"):
        return False
    if not p.get("infected_until"):
        return False
    try:
        iu = datetime.datetime.fromisoformat(str(p["infected_until"]))
        if datetime.datetime.utcnow() >= iu:
            return False
        return True
    except Exception:
        return False

def infect_chance(attacker: dict, target: dict) -> float:
    """
    Вычисляет шанс заражения (0.0 – 1.0).

    Логика:
      - atk = infection атакующего
      - def = immunity + security цели
      - Если def > atk в 2+ раза → шанс почти 0 (2–5%)
      - Если def чуть больше atk → малый шанс (10–25%)
      - Если примерно равны → 40–50%
      - Если atk намного больше → до 85%
    """
    atk = attacker["infection"]
    def_ = target["immunity"] + target["security"]
    if target.get("event_immunity"):
        return 0.01

    ratio = atk / max(def_, 1)

    if ratio >= 2.0:
        chance = 0.85
    elif ratio >= 1.5:
        chance = 0.65
    elif ratio >= 1.0:
        chance = 0.45
    elif ratio >= 0.75:
        chance = 0.25
    elif ratio >= 0.5:
        chance = 0.10
    else:
        chance = 0.03

    return chance

def fever_seconds(target: dict) -> int:
    """
    Длительность горячки в секундах.
    Летальность атакующего влияет на длительность заражения (макс 24 часа).
    Базовые 3600 сек + 1800 сек за каждый уровень летальности.
    """
    return min(FEVER_DURATION + target.get("lethality", 1) * 1800, 86400)

def infected_seconds(attacker: dict) -> int:
    """
    Длительность заражения в секундах на основе летальности атакующего.
    Макс 24 часа (86400 сек).
    """
    return min(3600 + attacker.get("lethality", 1) * 3600, 86400)

# ───────────────────────────────────────────────────────────────
#  ROUTER / FSM
# ───────────────────────────────────────────────────────────────

router = Router()

class S(StatesGroup):
    # Клан
    clan_name        = State()
    clan_tag         = State()
    # Админ: события
    event_hours      = State()
    event_bonus      = State()
    event_count      = State()
    # Прочее
    rename_lab       = State()
    rename_pathogen  = State()

# ── Клавиатуры ─────────────────────────────────────────────────

def kb_main():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧫 Лаборатория"), KeyboardButton(text="⚗️ Прокачка")],
            [KeyboardButton(text="☣️ Заразить"),    KeyboardButton(text="🏆 Рейтинг")],
            [KeyboardButton(text="👥 Клан"),        KeyboardButton(text="🛡 Защита")],
            [KeyboardButton(text="📋 Профиль"),     KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True
    )

def kb_cancel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def kb_clan_actions(player: dict):
    if player.get("clan_id"):
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Инфо о клане", callback_data="clan_info")],
            [InlineKeyboardButton(text="🚪 Покинуть клан",  callback_data="clan_leave")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать клан", callback_data="clan_create")],
        [InlineKeyboardButton(text="🔍 Найти клан",  callback_data="clan_search")],
    ])

def kb_fever(player: dict):
    cost = FEVER_HEAL_COST
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💊 Вылечить за {cost:.0f} Bio",
            callback_data="fever_heal"
        )],
        [InlineKeyboardButton(text="⏳ Ждать (бесплатно)", callback_data="fever_wait")],
    ])

def kb_upgrade():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🦠 Заразность +1",   callback_data="upgrade:infection")],
        [InlineKeyboardButton(text="🛡 Иммунитет +1",    callback_data="upgrade:immunity")],
        [InlineKeyboardButton(text="☠️ Летальность +1",  callback_data="upgrade:lethality")],
        [InlineKeyboardButton(text="🔒 Безопасность +1", callback_data="upgrade:security")],
    ])

# ───────────────────────────────────────────────────────────────
#  АПГРЕЙДЫ
# ───────────────────────────────────────────────────────────────

UPGRADE_COST_BASE = 30.0
UPGRADE_COST_MULT = 1.5

def upgrade_cost(current_level: int) -> float:
    return round(UPGRADE_COST_BASE * (UPGRADE_COST_MULT ** (current_level - 1)), 2)

UPGRADE_FIELDS = {
    "infection":  "🦠 Заразность",
    "immunity":   "🛡 Иммунитет",
    "lethality":  "☠️ Летальность",
    "security":   "🔒 Безопасность",
}

# ───────────────────────────────────────────────────────────────
#  ОБРАБОТЧИКИ — ОСНОВНЫЕ
# ───────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(msg: Message):
    if await is_banned(msg.from_user.id):
        return await msg.answer("🚫 Вы заблокированы.")
    p = await get_or_create(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    await msg.answer(
        f"☣️ <b>Добро пожаловать в БИО-ВОЙНЫ!</b>\n\n"
        f"Твоя лаборатория: <b>{p['lab_name']}</b>\n"
        f"Лаб ID: <code>{p['lab_id']}</code>\n\n"
        f"Развивай патоген, заражай соперников, захватывай мир!",
        reply_markup=kb_main()
    )

@router.message(F.text == "📋 Профиль")
async def cmd_profile(msg: Message):
    p = await get_or_create(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    if await is_banned(msg.from_user.id):
        return await msg.answer("🚫 Вы заблокированы.")

    has_fever    = fever_active(p)
    is_inf       = infected_active(p)
    fever_str    = ""
    infected_str = ""

    if has_fever:
        fu = datetime.datetime.fromisoformat(str(p["fever_until"]))
        rem = int((fu - datetime.datetime.utcnow()).total_seconds())
        h, m = divmod(rem // 60, 60)
        fever_str = f"\n🤒 <b>Горячка:</b> осталось {h}ч {m}мин"

    if is_inf:
        iu = datetime.datetime.fromisoformat(str(p["infected_until"]))
        rem = int((iu - datetime.datetime.utcnow()).total_seconds())
        h, m = divmod(rem // 60, 60)
        infected_str = f"\n☣️ <b>Заражён:</b> осталось {h}ч {m}мин"

    await msg.answer(
        f"👤 <b>{p['full_name']}</b>  (@{p['username'] or '—'})\n"
        f"🏭 Лаборатория: <b>{p['lab_name']}</b>\n"
        f"🔬 Патоген: <b>{p['pathogen_name']}</b>\n\n"
        f"🦠 Заразность:    <b>{p['infection']}</b>\n"
        f"🛡 Иммунитет:    <b>{p['immunity']}</b>\n"
        f"☠️ Летальность:  <b>{p['lethality']}</b>\n"
        f"🔒 Безопасность: <b>{p['security']}</b>\n\n"
        f"💰 Bio-ресурсы:  <b>{p['bio_resource']:.1f}</b>\n"
        f"🧪 Bio-опыт:     <b>{p['bio_exp']}</b>\n"
        f"🎯 Заражено:     <b>{p['infected_count']}</b>\n"
        f"📊 Атак: {p['operations_total']} (успех: {p['operations_success']})\n"
        f"{fever_str}{infected_str}"
    )

@router.message(F.text == "🧫 Лаборатория")
async def cmd_lab(msg: Message):
    p = await get_or_create(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    await msg.answer(
        f"🏭 <b>{p['lab_name']}</b>\n"
        f"🆔 Лаб ID: <code>{p['lab_id']}</code>\n\n"
        f"🔬 Патоген: <b>{p['pathogen_name']}</b>\n"
        f"🦠 Заразность:    <b>{p['infection']}</b>\n"
        f"🛡 Иммунитет:    <b>{p['immunity']}</b>\n"
        f"☠️ Летальность:  <b>{p['lethality']}</b>\n"
        f"🔒 Безопасность: <b>{p['security']}</b>\n"
        f"💰 Bio-ресурсы:  <b>{p['bio_resource']:.1f}</b>"
    )

@router.message(F.text == "⚗️ Прокачка")
async def cmd_upgrade_menu(msg: Message):
    p = await get_or_create(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    cost_inf = upgrade_cost(p["infection"])
    cost_imm = upgrade_cost(p["immunity"])
    cost_let = upgrade_cost(p["lethality"])
    cost_sec = upgrade_cost(p["security"])
    await msg.answer(
        f"⚗️ <b>Прокачка лаборатории</b>\n\n"
        f"💰 Bio-ресурсы: <b>{p['bio_resource']:.1f}</b>\n\n"
        f"🦠 Заразность ({p['infection']}) — <b>{cost_inf}</b> Bio\n"
        f"🛡 Иммунитет ({p['immunity']}) — <b>{cost_imm}</b> Bio\n"
        f"☠️ Летальность ({p['lethality']}) — <b>{cost_let}</b> Bio\n"
        f"🔒 Безопасность ({p['security']}) — <b>{cost_sec}</b> Bio",
        reply_markup=kb_upgrade()
    )

@router.callback_query(F.data.startswith("upgrade:"))
async def cb_upgrade(cb: CallbackQuery):
    uid  = cb.from_user.id
    if await is_banned(uid): return await cb.answer("🚫", show_alert=True)
    p    = await get_player(uid)
    skill = cb.data.split(":")[1]
    if skill not in UPGRADE_FIELDS:
        return await cb.answer("❌ Неизвестный навык", show_alert=True)

    current = p[skill]
    cost    = upgrade_cost(current)
    if p["bio_resource"] < cost:
        return await cb.answer(f"❌ Недостаточно Bio-ресурсов! Нужно {cost}", show_alert=True)

    await update_player(uid, **{skill: current + 1, "bio_resource": p["bio_resource"] - cost})
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO upgrade_log (user_id,skill,amount,cost) VALUES (?,?,?,?)",
            (uid, skill, 1, cost)
        )
        await db.commit()
    await cb.answer(f"✅ {UPGRADE_FIELDS[skill]} повышена до {current+1}!", show_alert=True)
    p2 = await get_player(uid)
    cost_inf = upgrade_cost(p2["infection"])
    cost_imm = upgrade_cost(p2["immunity"])
    cost_let = upgrade_cost(p2["lethality"])
    cost_sec = upgrade_cost(p2["security"])
    await cb.message.edit_text(
        f"⚗️ <b>Прокачка лаборатории</b>\n\n"
        f"💰 Bio-ресурсы: <b>{p2['bio_resource']:.1f}</b>\n\n"
        f"🦠 Заразность ({p2['infection']}) — <b>{cost_inf}</b> Bio\n"
        f"🛡 Иммунитет ({p2['immunity']}) — <b>{cost_imm}</b> Bio\n"
        f"☠️ Летальность ({p2['lethality']}) — <b>{cost_let}</b> Bio\n"
        f"🔒 Безопасность ({p2['security']}) — <b>{cost_sec}</b> Bio",
        reply_markup=kb_upgrade()
    )

# ───────────────────────────────────────────────────────────────
#  ЗАРАЖЕНИЕ
# ───────────────────────────────────────────────────────────────

async def _resolve_target(msg: Message) -> Optional[dict]:
    """
    Определяет цель атаки:
    1) Реплай на сообщение
    2) /заразить @username
    3) /заразить 123456789 (Telegram ID)
    """
    # 1. Реплай
    if msg.reply_to_message:
        ru = msg.reply_to_message.from_user
        return await get_player(ru.id)

    # 2. Аргумент команды
    parts = msg.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None

    arg = parts[1].strip()
    if arg.startswith("@"):
        return await get_player_by_username(arg)
    if arg.isdigit():
        return await get_player(int(arg))
    return None


@router.message(Command("заразить"))
@router.message(F.text.startswith("☣️ Заразить"))
async def cmd_infect(msg: Message):
    uid = msg.from_user.id
    if await is_banned(uid):
        return await msg.answer("🚫 Вы заблокированы.")

    attacker = await get_or_create(uid, msg.from_user.username, msg.from_user.full_name)

    # Проверяем горячку атакующего
    if fever_active(attacker):
        fu = datetime.datetime.fromisoformat(str(attacker["fever_until"]))
        rem = int((fu - datetime.datetime.utcnow()).total_seconds())
        h, m = divmod(rem // 60, 60)
        return await msg.answer(
            f"🤒 <b>У тебя горячка!</b>\n\n"
            f"Ты слишком слаб, чтобы заражать других.\n"
            f"Осталось: <b>{h}ч {m}мин</b>\n\n"
            f"Можно вылечиться за <b>{FEVER_HEAL_COST:.0f} Bio</b> или подождать.",
            reply_markup=kb_fever(attacker)
        )

    # Кулдаун
    if attacker.get("last_attack_at"):
        try:
            la = datetime.datetime.fromisoformat(str(attacker["last_attack_at"]))
            diff = (datetime.datetime.utcnow() - la).total_seconds()
            if diff < INFECT_COOLDOWN:
                rem = int(INFECT_COOLDOWN - diff)
                h, m = divmod(rem // 60, 60)
                return await msg.answer(f"⏳ Кулдаун атаки! Следующая через <b>{h}ч {m}мин</b>.")
        except Exception:
            pass

    # Если просто нажали кнопку без реплая и аргументов
    if msg.text.strip() == "☣️ Заразить" and not msg.reply_to_message:
        return await msg.answer(
            "☣️ <b>Как заразить?</b>\n\n"
            "• Ответь на сообщение жертвы командой /заразить\n"
            "• Или: /заразить @username\n"
            "• Или: /заразить 123456789"
        )

    target = await _resolve_target(msg)

    if not target:
        return await msg.answer(
            "❌ Цель не найдена!\n\n"
            "Используй:\n"
            "• /заразить @username\n"
            "• /заразить 123456789\n"
            "• Или ответь на сообщение жертвы"
        )

    if target["user_id"] == uid:
        return await msg.answer("🤦 Нельзя заражать самого себя!")

    if target["is_banned"]:
        return await msg.answer("❌ Этот игрок недоступен.")

    if target.get("event_immunity"):
        return await msg.answer("🛡 У цели <b>иммунитет события</b>! Атака невозможна.")

    if infected_active(target):
        iu = datetime.datetime.fromisoformat(str(target["infected_until"]))
        rem = int((iu - datetime.datetime.utcnow()).total_seconds())
        h, m = divmod(rem // 60, 60)
        return await msg.answer(
            f"☣️ <b>{target['full_name']}</b> уже заражён!\n"
            f"Заражение спадёт через <b>{h}ч {m}мин</b>."
        )

    # Вычисляем шанс
    chance  = infect_chance(attacker, target)
    atk_roll = random.random()
    success  = atk_roll < chance

    reward = 0.0
    now    = datetime.datetime.utcnow()

    await update_player(uid, last_attack_at=now.isoformat(),
                        operations_total=attacker["operations_total"] + 1)

    if success:
        # Длительность заражения (по летальности атакующего)
        inf_secs   = infected_seconds(attacker)
        fever_secs = fever_seconds(attacker)
        inf_until  = now + datetime.timedelta(seconds=inf_secs)
        fever_until = now + datetime.timedelta(seconds=fever_secs)

        reward = round(random.uniform(10, 30) + attacker["infection"] * 2, 2)

        await update_player(uid,
            bio_resource    = attacker["bio_resource"] + reward,
            bio_exp         = attacker["bio_exp"] + 10,
            infected_count  = attacker["infected_count"] + 1,
            operations_success = attacker["operations_success"] + 1,
        )
        await update_player(target["user_id"],
            is_infected     = 1,
            infected_until  = inf_until.isoformat(),
            fever_until     = fever_until.isoformat(),
            infected_by     = uid,
        )

        inf_h, inf_m = divmod(inf_secs // 60, 60)
        fev_h, fev_m = divmod(fever_secs // 60, 60)

        await msg.answer(
            f"☣️ <b>ЗАРАЖЕНИЕ УСПЕШНО!</b>\n\n"
            f"🎯 Жертва: <b>{target['full_name']}</b>\n"
            f"🦠 Заразность: {attacker['infection']} vs 🛡{target['immunity']}+🔒{target['security']}\n"
            f"🎲 Шанс: {chance*100:.0f}% | Бросок: {atk_roll*100:.0f}%\n\n"
            f"⏳ Длительность заражения: <b>{inf_h}ч {inf_m}мин</b>\n"
            f"🤒 Горячка у жертвы: <b>{fev_h}ч {fev_m}мин</b>\n"
            f"💰 Получено: +<b>{reward}</b> Bio"
        )

        # Уведомляем жертву
        try:
            bot: Bot = msg.bot
            await bot.send_message(
                target["user_id"],
                f"☣️ <b>ВАС ЗАРАЗИЛИ!</b>\n\n"
                f"Атаковал: <b>{attacker['full_name']}</b>\n"
                f"🤒 У вас горячка на <b>{fev_h}ч {fev_m}мин</b> — вы не можете заражать других!\n"
                f"⏳ Заражение спадёт через <b>{inf_h}ч {inf_m}мин</b>\n\n"
                f"💊 Вылечить горячку: /лечение",
                reply_markup=kb_fever(target)
            )
        except Exception:
            pass
    else:
        await update_player(target["user_id"],
            prevented_success = target["prevented_success"] + 1,
            prevented_total   = target["prevented_total"] + 1,
        )
        await msg.answer(
            f"🛡 <b>Атака отражена!</b>\n\n"
            f"🎯 Цель: <b>{target['full_name']}</b>\n"
            f"🦠 Заразность: {attacker['infection']} vs 🛡{target['immunity']}+🔒{target['security']}\n"
            f"🎲 Шанс: {chance*100:.0f}% | Бросок: {atk_roll*100:.0f}%\n\n"
            f"Прокачай <b>заразность</b> для следующей попытки!"
        )

    await log_attack(uid, target["user_id"], int(success),
                     int(atk_roll * 100), int(chance * 100), reward)

# ───────────────────────────────────────────────────────────────
#  ГОРЯЧКА — ЛЕЧЕНИЕ
# ───────────────────────────────────────────────────────────────

@router.message(Command("лечение"))
async def cmd_fever_info(msg: Message):
    uid = msg.from_user.id
    p   = await get_or_create(uid, msg.from_user.username, msg.from_user.full_name)

    if not fever_active(p):
        return await msg.answer("✅ У тебя нет горячки! Ты здоров.")

    fu  = datetime.datetime.fromisoformat(str(p["fever_until"]))
    rem = int((fu - datetime.datetime.utcnow()).total_seconds())
    h, m = divmod(rem // 60, 60)

    await msg.answer(
        f"🤒 <b>Горячка активна!</b>\n\n"
        f"Осталось: <b>{h}ч {m}мин</b>\n"
        f"💰 Вылечить за <b>{FEVER_HEAL_COST:.0f} Bio</b> прямо сейчас\n"
        f"⏳ Или просто подожди — горячка пройдёт сама.",
        reply_markup=kb_fever(p)
    )

@router.callback_query(F.data == "fever_heal")
async def cb_fever_heal(cb: CallbackQuery):
    uid = cb.from_user.id
    p   = await get_player(uid)

    if not fever_active(p):
        await cb.answer("✅ Горячки нет!", show_alert=True)
        return await cb.message.edit_text("✅ Ты уже здоров!")

    if p["bio_resource"] < FEVER_HEAL_COST:
        return await cb.answer(
            f"❌ Недостаточно Bio-ресурсов! Нужно {FEVER_HEAL_COST:.0f}",
            show_alert=True
        )

    await update_player(uid,
        bio_resource = p["bio_resource"] - FEVER_HEAL_COST,
        fever_until  = None,
    )
    await cb.answer("💊 Горячка вылечена!", show_alert=True)
    await cb.message.edit_text(
        f"✅ <b>Горячка вылечена!</b>\n\n"
        f"💰 Потрачено: <b>{FEVER_HEAL_COST:.0f}</b> Bio\n"
        f"Теперь ты снова можешь заражать других! ☣️"
    )

@router.callback_query(F.data == "fever_wait")
async def cb_fever_wait(cb: CallbackQuery):
    p  = await get_player(cb.from_user.id)
    if not fever_active(p):
        return await cb.answer("✅ Горячки уже нет!", show_alert=True)
    fu  = datetime.datetime.fromisoformat(str(p["fever_until"]))
    rem = int((fu - datetime.datetime.utcnow()).total_seconds())
    h, m = divmod(rem // 60, 60)
    await cb.answer(f"⏳ Горячка пройдёт через {h}ч {m}мин", show_alert=True)

# ───────────────────────────────────────────────────────────────
#  РЕЙТИНГ
# ───────────────────────────────────────────────────────────────

@router.message(F.text == "🏆 Рейтинг")
async def cmd_top(msg: Message):
    top = await get_top_players(10)
    if not top:
        return await msg.answer("Рейтинг пуст.")
    lines = ["🏆 <b>ТОП-10 по заражениям</b>\n"]
    medals = ["🥇","🥈","🥉"] + ["🔹"] * 7
    for i, p in enumerate(top):
        name = p["full_name"] or p["username"] or str(p["user_id"])
        lines.append(f"{medals[i]} {name} — <b>{p['infected_count']}</b> заражений")
    await msg.answer("\n".join(lines))

# ───────────────────────────────────────────────────────────────
#  КЛАНЫ
# ───────────────────────────────────────────────────────────────

@router.message(F.text == "👥 Клан")
@router.message(Command("клан"))
async def cmd_clan_menu(msg: Message):
    uid = msg.from_user.id
    if await is_banned(uid): return await msg.answer("🚫 Вы заблокированы.")
    p = await get_or_create(uid, msg.from_user.username, msg.from_user.full_name)

    if p.get("clan_id"):
        clan = await get_clan(p["clan_id"])
        if clan:
            return await msg.answer(
                f"👥 <b>Твой клан: [{clan['tag']}] {clan['name']}</b>\n\n"
                f"👤 Участников: <b>{clan['members_count']}</b>\n"
                f"💰 Bio клана: <b>{clan['bio_resource']:.1f}</b>\n"
                f"📝 {clan['description'] or '—'}",
                reply_markup=kb_clan_actions(p)
            )

    await msg.answer(
        "👥 <b>Кланы</b>\n\nТы не состоишь в клане.\nСоздай свой или вступи в существующий!",
        reply_markup=kb_clan_actions(p)
    )

# Команда /создатьклан
@router.message(Command("создатьклан"))
async def cmd_create_clan_command(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    if await is_banned(uid): return await msg.answer("🚫 Вы заблокированы.")
    p = await get_or_create(uid, msg.from_user.username, msg.from_user.full_name)
    if p.get("clan_id"):
        return await msg.answer("❌ Ты уже состоишь в клане! Сначала выйди из него.")
    await msg.answer(
        "🏗 <b>Создание клана</b>\n\nВведи <b>название</b> клана:",
        reply_markup=kb_cancel()
    )
    await state.set_state(S.clan_name)

# Кнопка «Создать клан»
@router.callback_query(F.data == "clan_create")
async def cb_clan_create(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    if await is_banned(uid): return await cb.answer("🚫", show_alert=True)
    p = await get_player(uid)
    if p and p.get("clan_id"):
        return await cb.answer("❌ Ты уже в клане!", show_alert=True)
    await cb.message.answer(
        "🏗 <b>Создание клана</b>\n\nВведи <b>название</b> клана:",
        reply_markup=kb_cancel()
    )
    await state.set_state(S.clan_name)
    await cb.answer()

@router.message(S.clan_name)
async def proc_clan_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    if len(name) < 2 or len(name) > 32:
        return await msg.answer("❌ Название должно быть от 2 до 32 символов.")
    if await get_clan_by_name(name):
        return await msg.answer("❌ Клан с таким названием уже существует!")
    await state.update_data(clan_name=name)
    await msg.answer(
        f"✅ Название: <b>{name}</b>\n\nТеперь введи <b>тег</b> клана (2–6 символов, например: BIO):",
        reply_markup=kb_cancel()
    )
    await state.set_state(S.clan_tag)

@router.message(S.clan_tag)
async def proc_clan_tag(msg: Message, state: FSMContext):
    tag = msg.text.strip().upper()
    if len(tag) < 2 or len(tag) > 6:
        return await msg.answer("❌ Тег должен быть от 2 до 6 символов.")
    if await get_clan_by_tag(tag):
        return await msg.answer("❌ Клан с таким тегом уже существует!")
    data = await state.get_data()
    name = data["clan_name"]
    uid  = msg.from_user.id

    clan = await create_clan(name, tag, uid)
    if not clan:
        await state.clear()
        return await msg.answer("❌ Ошибка создания клана. Попробуй другое название/тег.")

    await update_player(uid, clan_id=clan["id"])
    await state.clear()
    await msg.answer(
        f"🎉 <b>Клан создан!</b>\n\n"
        f"🏷 Название: <b>[{tag}] {name}</b>\n"
        f"👑 Лидер: ты\n\n"
        f"Поделись тегом клана, чтобы другие могли вступить!",
        reply_markup=kb_main()
    )

@router.callback_query(F.data == "clan_info")
async def cb_clan_info(cb: CallbackQuery):
    p = await get_player(cb.from_user.id)
    if not p or not p.get("clan_id"):
        return await cb.answer("❌ Ты не в клане", show_alert=True)
    clan = await get_clan(p["clan_id"])
    if not clan:
        return await cb.answer("❌ Клан не найден", show_alert=True)
    await cb.message.answer(
        f"👥 <b>[{clan['tag']}] {clan['name']}</b>\n\n"
        f"👤 Участников: <b>{clan['members_count']}</b>\n"
        f"💰 Bio клана: <b>{clan['bio_resource']:.1f}</b>\n"
        f"📝 {clan['description'] or '—'}"
    )
    await cb.answer()

@router.callback_query(F.data == "clan_leave")
async def cb_clan_leave(cb: CallbackQuery):
    uid = cb.from_user.id
    p   = await get_player(uid)
    if not p or not p.get("clan_id"):
        return await cb.answer("❌ Ты не в клане", show_alert=True)
    clan = await get_clan(p["clan_id"])
    await update_player(uid, clan_id=None)
    if clan:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE clans SET members_count=MAX(0,members_count-1) WHERE id=?",
                (clan["id"],)
            )
            await db.commit()
    await cb.answer("✅ Ты покинул клан", show_alert=True)
    await cb.message.edit_text("🚪 Ты вышел из клана.")

@router.callback_query(F.data == "clan_search")
async def cb_clan_search(cb: CallbackQuery):
    await cb.message.answer(
        "🔍 Для вступления в клан попроси его тег у участника.\n"
        "Команда: /вступитьвклан ТЕГ"
    )
    await cb.answer()

@router.message(Command("вступитьвклан"))
async def cmd_join_clan(msg: Message):
    uid  = msg.from_user.id
    p    = await get_or_create(uid, msg.from_user.username, msg.from_user.full_name)
    if p.get("clan_id"):
        return await msg.answer("❌ Ты уже в клане! Сначала выйди.")
    parts = msg.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return await msg.answer("❌ Укажи тег клана: /вступитьвклан ТЕГ")
    tag  = parts[1].strip().upper()
    clan = await get_clan_by_tag(tag)
    if not clan:
        return await msg.answer(f"❌ Клан [{tag}] не найден.")
    await update_player(uid, clan_id=clan["id"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE clans SET members_count=members_count+1 WHERE id=?", (clan["id"],)
        )
        await db.commit()
    await msg.answer(f"✅ Ты вступил в клан <b>[{clan['tag']}] {clan['name']}</b>!")

# ───────────────────────────────────────────────────────────────
#  ЗАЩИТА
# ───────────────────────────────────────────────────────────────

@router.message(F.text == "🛡 Защита")
async def cmd_defense(msg: Message):
    p = await get_or_create(msg.from_user.id, msg.from_user.username, msg.from_user.full_name)
    await msg.answer(
        f"🛡 <b>Защита лаборатории</b>\n\n"
        f"🛡 Иммунитет:    <b>{p['immunity']}</b>\n"
        f"🔒 Безопасность: <b>{p['security']}</b>\n"
        f"🔗 Суммарная защита: <b>{p['immunity'] + p['security']}</b>\n\n"
        f"Чем выше защита — тем сложнее тебя заразить.\n"
        f"Прокачивай через ⚗️ Прокачка!"
    )

# ───────────────────────────────────────────────────────────────
#  ПОМОЩЬ
# ───────────────────────────────────────────────────────────────

@router.message(F.text == "ℹ️ Помощь")
@router.message(Command("help"))
async def cmd_help(msg: Message):
    await msg.answer(
        "☣️ <b>БИО-ВОЙНЫ — Помощь</b>\n\n"
        "<b>Команды:</b>\n"
        "/заразить @username — заразить игрока по юзернейму\n"
        "/заразить 123456789 — заразить по Telegram ID\n"
        "  (или ответь на сообщение + /заразить)\n"
        "/лечение — вылечить горячку\n"
        "/создатьклан — создать клан\n"
        "/вступитьвклан ТЕГ — вступить в клан\n\n"
        "<b>Механика заражения:</b>\n"
        "🦠 Заразность атакующего vs 🛡Иммунитет + 🔒Безопасность цели\n"
        "Чем выше твоя заразность — тем больше шанс успеха\n"
        "☠️ Летальность влияет на длительность заражения (до 24ч)\n\n"
        "<b>Горячка:</b>\n"
        "После заражения у жертвы горячка — она не может атаковать!\n"
        "Лечение: /лечение (за Bio или подождать)\n\n"
        "⏳ Кулдаун атаки: 1 час"
    )

# ───────────────────────────────────────────────────────────────
#  ОТМЕНА
# ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("❌ Отменено.")
    await cb.answer()

# ───────────────────────────────────────────────────────────────
#  АДМИН-ПАНЕЛЬ
# ───────────────────────────────────────────────────────────────

EVENT_INFO = {
    "mutation": {
        "title": "🦠 Мутация",
        "desc":  "Всем игрокам повышена заразность на {bonus} ур. на {hours}ч",
        "broadcast": "🦠 <b>СОБЫТИЕ: МУТАЦИЯ!</b>\n\nВсем игрокам заразность +{bonus} на {hours} часов! Используй момент!"
    },
    "epidemic": {
        "title": "💀 Эпидемия",
        "desc":  "Все игроки получают Bio-ресурсы",
        "broadcast": "💀 <b>СОБЫТИЕ: ЭПИДЕМИЯ!</b>\n\nКаждый игрок получил +{bonus} Bio-ресурсов! Трать с умом! ({hours}ч)"
    },
    "quarantine": {
        "title": "🛡 Карантин",
        "desc":  "Карантин на {hours}ч",
        "broadcast": "🛡 <b>СОБЫТИЕ: КАРАНТИН!</b>\n\nВведён карантин на {hours} часов. Все атаки заблокированы!"
    },
    "biowar": {
        "title": "⚔️ Биовойна",
        "desc":  "Ускоренное получение Bio-опыта",
        "broadcast": "⚔️ <b>СОБЫТИЕ: БИОВОЙНА!</b>\n\nБонус Bio-опыта +{bonus}% на {hours} часов! Атакуй!"
    },
    "loot": {
        "title": "🎁 Трофеи",
        "desc":  "Случайные игроки получают Bio-ресурсы",
        "broadcast": "🎁 <b>СОБЫТИЕ: ТРОФЕИ!</b>\n\n{count} случайных игроков получат по {bonus} Bio! Может повезёт тебе?"
    },
}

@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if not await is_admin(msg.from_user.id):
        return await msg.answer("❌ Нет доступа.")
    await msg.answer(
        "🔧 <b>Админ-панель</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика",    callback_data="adm_stats")],
            [InlineKeyboardButton(text="📢 Рассылка",      callback_data="adm_broadcast")],
            [InlineKeyboardButton(text="🚫 Бан игрока",    callback_data="adm_ban")],
            [InlineKeyboardButton(text="✅ Разбан игрока",  callback_data="adm_unban")],
            [InlineKeyboardButton(text="☣️ События",       callback_data="adm_events")],
        ])
    )

@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id): return await cb.answer("❌", show_alert=True)
    players = await get_all_players()
    total   = len(players)
    banned  = sum(1 for p in players if p["is_banned"])
    await cb.message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👤 Игроков всего: <b>{total}</b>\n"
        f"🚫 Заблокировано: <b>{banned}</b>"
    )
    await cb.answer()

@router.callback_query(F.data == "adm_events")
async def cb_adm_events(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id): return await cb.answer("❌", show_alert=True)
    btns = [[InlineKeyboardButton(text=v["title"], callback_data=f"event_start:{k}")]
            for k, v in EVENT_INFO.items()]
    active = await get_active_events()
    if active:
        for ev in active:
            btns.append([InlineKeyboardButton(
                text=f"🛑 Стоп: {ev['title']}",
                callback_data=f"event_stop:{ev['id']}"
            )])
    await cb.message.answer("☣️ <b>События</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))
    await cb.answer()

@router.callback_query(F.data.startswith("event_stop:"))
async def cb_event_stop(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id): return await cb.answer("❌", show_alert=True)
    eid = int(cb.data.split(":")[1])
    await deactivate_event(eid)
    players = await get_all_players()
    for p in players:
        if p["is_banned"]: continue
        try:
            await cb.bot.send_message(p["user_id"], "☣️ <b>Событие завершено!</b>\n\nАдминистрация завершила активное событие.")
        except Exception: pass
        await asyncio.sleep(0.05)
    await cb.message.answer("✅ Событие остановлено.")
    await cb.answer()

@router.callback_query(F.data.startswith("event_start:"))
async def cb_event_start(cb: CallbackQuery, state: FSMContext):
    if not await is_admin(cb.from_user.id): return await cb.answer("❌", show_alert=True)
    etype = cb.data.split(":")[1]
    info  = EVENT_INFO[etype]
    await state.update_data(etype=etype)
    await cb.message.answer(
        f"☣️ <b>{info['title']}</b>\n\n⏱ На сколько часов? (1–72):",
        reply_markup=kb_cancel()
    )
    await state.set_state(S.event_hours)
    await cb.answer()

@router.message(S.event_hours)
async def proc_event_hours(msg: Message, state: FSMContext):
    if not await is_admin(msg.from_user.id): return
    try:
        hours = int(msg.text.strip())
        if not 1 <= hours <= 72: raise ValueError
    except ValueError:
        return await msg.answer("❌ Число от 1 до 72")
    await state.update_data(hours=hours)
    data  = await state.get_data()
    etype = data["etype"]
    prompts = {
        "mutation":   "🦠 На сколько уровней повысить заразность? (1–10):",
        "epidemic":   "💰 Сколько Bio-ресурсов выдать каждому? (напр. 50):",
        "quarantine": "🛡 Ввести иммунитет всем? (да/нет):",
        "biowar":     "⚔️ На сколько % повысить Bio-опыт? (напр. 50):",
        "loot":       "🎁 Сколько случайных игроков получат награду? (напр. 10):",
    }
    await msg.answer(prompts.get(etype, "Введи параметр:"))
    await state.set_state(S.event_bonus)

@router.message(S.event_bonus)
async def proc_event_bonus(msg: Message, state: FSMContext):
    if not await is_admin(msg.from_user.id): return
    data  = await state.get_data()
    etype = data["etype"]
    txt   = msg.text.strip().lower()
    if etype == "quarantine":
        bonus = 1 if txt in ("да","yes","1","+") else 0
        await state.update_data(bonus=bonus)
        await _launch_event(msg, state)
    elif etype == "loot":
        try:
            count = int(txt)
            if count < 1: raise ValueError
        except ValueError:
            return await msg.answer("❌ Положительное число")
        await state.update_data(bonus=0, loot_count=count)
        await msg.answer("💰 Сколько Bio получит каждый счастливчик?")
        await state.set_state(S.event_count)
    else:
        try:
            bonus = int(txt)
            if bonus < 0: raise ValueError
        except ValueError:
            return await msg.answer("❌ Положительное число")
        await state.update_data(bonus=bonus)
        await _launch_event(msg, state)

@router.message(S.event_count)
async def proc_event_count(msg: Message, state: FSMContext):
    if not await is_admin(msg.from_user.id): return
    try:
        amount = int(msg.text.strip())
        if amount < 1: raise ValueError
    except ValueError:
        return await msg.answer("❌ Положительное число")
    await state.update_data(loot_amount=amount)
    await _launch_event(msg, state)

async def _remove_immunity_after(delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE players SET event_immunity=0")
        await db.commit()
    logger.info("Иммунитет от карантина снят")

async def _launch_event(msg: Message, state: FSMContext):
    data    = await state.get_data()
    etype   = data["etype"]
    hours   = data["hours"]
    bonus   = data.get("bonus", 0)
    info    = EVENT_INFO[etype]
    bot: Bot = msg.bot

    players        = await get_all_players()
    active_players = [p for p in players if not p["is_banned"]]
    winners        = []
    broadcast_text = ""
    event_desc     = ""

    if etype == "mutation":
        event_desc     = info["desc"].format(bonus=bonus, hours=hours)
        broadcast_text = info["broadcast"].format(bonus=bonus, hours=hours)
        for p in active_players:
            fp = await get_player(p["user_id"])
            if fp: await update_player(p["user_id"], infection=fp["infection"] + bonus)

    elif etype == "epidemic":
        event_desc     = info["desc"]
        broadcast_text = info["broadcast"].format(bonus=bonus, hours=hours)
        for p in active_players:
            fp = await get_player(p["user_id"])
            if fp: await update_player(p["user_id"], bio_resource=fp["bio_resource"] + bonus)

    elif etype == "quarantine":
        event_desc     = info["desc"].format(hours=hours)
        broadcast_text = info["broadcast"].format(hours=hours)
        if bonus:
            for p in active_players:
                await update_player(p["user_id"], event_immunity=1)
            asyncio.create_task(_remove_immunity_after(hours * 3600))

    elif etype == "biowar":
        event_desc     = info["desc"]
        broadcast_text = info["broadcast"].format(bonus=bonus, hours=hours)
        exp_bonus = max(1, bonus // 10)
        for p in active_players:
            fp = await get_player(p["user_id"])
            if fp: await update_player(p["user_id"], bio_exp=fp["bio_exp"] + exp_bonus * 10)

    elif etype == "loot":
        count       = data.get("loot_count", 5)
        loot_amount = data.get("loot_amount", 100)
        winners     = random.sample(active_players, min(count, len(active_players)))
        winner_ids  = {w["user_id"] for w in winners}
        event_desc  = info["desc"]
        broadcast_text = info["broadcast"].format(count=len(winners), bonus=loot_amount)
        for p in active_players:
            fp = await get_player(p["user_id"])
            if fp and p["user_id"] in winner_ids:
                await update_player(p["user_id"], bio_resource=fp["bio_resource"] + loot_amount)

    await create_event(etype, info["title"], event_desc, "{}", hours)

    sent = 0
    winner_ids_set = {w["user_id"] for w in winners}
    status = await msg.answer(f"☣️ Запускаю рассылку... 0/{len(active_players)}")
    for i, p in enumerate(active_players):
        try:
            await bot.send_message(p["user_id"], broadcast_text)
            if etype == "loot" and p["user_id"] in winner_ids_set:
                la = data.get("loot_amount", 100)
                await bot.send_message(p["user_id"],
                    f"🎉 <b>ПОВЕЗЛО!</b> Ты получил <b>+{la}</b> Bio из заброшенного склада!")
            sent += 1
        except Exception: pass
        if (i+1) % 25 == 0:
            try: await status.edit_text(f"☣️ {i+1}/{len(active_players)}...")
            except Exception: pass
        await asyncio.sleep(0.05)

    await status.edit_text(
        f"✅ <b>Событие '{info['title']}' запущено!</b>\n\n"
        f"⏱ Длительность: <b>{hours}ч</b>\n"
        f"📢 Уведомлено: <b>{sent}</b> игроков"
    )
    await state.clear()

# ───────────────────────────────────────────────────────────────
#  ЗАПУСК
# ───────────────────────────────────────────────────────────────

async def main():
    await init_db()
    logger.info("БД инициализирована ✅")

    if SUPER_ADMIN_ID:
        await add_admin(SUPER_ADMIN_ID)
        logger.info(f"Супер-админ: {SUPER_ADMIN_ID}")

    await start_web()

    bot = Bot(token=BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Бот запущен ✅")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
