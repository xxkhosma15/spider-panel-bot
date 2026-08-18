"""
Admin-only Telegram bot for Spider Panel — expanded version.

Covers:
  • Users (list / create / view / config / QR / delete / toggle / reset traffic)
  • Inbounds (list / create quick / delete / generate Reality keys)
  • Server stats
  • Cloudflare Worker (status / sync / sync-source)
  • Scanner (view saved CF & Railway IPs)
  • Groups (list / create / delete)
  • Change panel password

Setup:
  1. pip install -r requirements.txt
  2. cp .env.example .env  → fill values
  3. python bot.py
"""

from __future__ import annotations

import html
import io
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from panel_client import (
    PanelClient,
    PanelError,
    extract_config_uris,
    obj_id,
    obj_label,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])
PANEL_URL = os.environ["PANEL_URL"]
PANEL_PASSWORD = os.environ["PANEL_ADMIN_PASSWORD"]

panel = PanelClient(PANEL_URL, PANEL_PASSWORD)

# Conversation states
(
    ASK_USERNAME,
    ASK_INBOUNDS,
    ASK_LIMIT,
    ASK_EXPIRE,
    CONFIRM_CREATE,
    # inbound create
    ASK_IB_NAME,
    ASK_IB_PROTO,
    # group create
    ASK_GRP_NAME,
    # change password
    ASK_OLD_PASS,
    ASK_NEW_PASS,
) = range(10)

PAGE_SIZE = 8


# ── access control ──────────────────────────────────────────────────────────


def admin_only(handler):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id != ADMIN_CHAT_ID:
            if update.message:
                await update.message.reply_text("⛔️ این ربات خصوصیه.")
            return ConversationHandler.END
        return await handler(update, context)

    return wrapped


# ── main menu ───────────────────────────────────────────────────────────────


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 کاربران", callback_data="menu:users")],
            [InlineKeyboardButton("📥 اینباندها", callback_data="menu:inbounds")],
            [InlineKeyboardButton("📊 آمار سرور", callback_data="stats:show")],
            [InlineKeyboardButton("☁️ Worker", callback_data="menu:worker")],
            [InlineKeyboardButton("🔍 اسکنر IP", callback_data="menu:scanner")],
            [InlineKeyboardButton("📁 گروه‌ها", callback_data="menu:groups")],
            [InlineKeyboardButton("⚙️ تغییر پسورد پنل", callback_data="settings:pass")],
        ]
    )


@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "به ربات مدیریت <b>Spider Panel</b> خوش اومدی 🕷️\nیکی از گزینه‌ها رو انتخاب کن:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


async def show_main_menu(query):
    await query.edit_message_text(
        "یکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu_keyboard(),
    )


# ── users ───────────────────────────────────────────────────────────────────


def users_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 لیست کاربران", callback_data="users:list:0")],
            [InlineKeyboardButton("➕ ساخت کاربر جدید", callback_data="user:new")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu:main")],
        ]
    )


async def render_user_list(query, page: int):
    try:
        users = await panel.list_users()
    except PanelError as e:
        await query.edit_message_text(f"خطا در گرفتن لیست کاربران:\n{e.detail}")
        return

    if not users:
        await query.edit_message_text(
            "هیچ کاربری هنوز ثبت نشده.", reply_markup=users_menu_keyboard()
        )
        return

    start = page * PAGE_SIZE
    chunk = users[start : start + PAGE_SIZE]

    buttons = []
    for u in chunk:
        uid = obj_id(u, "id", "user_id", "_id", "uuid", "username")
        label = obj_label(u, "username", "name", "id", default=str(uid))
        status = u.get("status", "active") if isinstance(u, dict) else "active"
        mark = "🟢" if status == "active" else "🔴"
        buttons.append(
            [InlineKeyboardButton(f"{mark} {label}", callback_data=f"user:view:{uid}")]
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"users:list:{page-1}"))
    if start + PAGE_SIZE < len(users):
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"users:list:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🏠 منوی کاربران", callback_data="menu:users")])
    await query.edit_message_text(
        f"👥 کاربران ({len(users)} نفر) — صفحه {page + 1}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def user_detail_keyboard(user_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📄 کانفیگ", callback_data=f"user:config:{user_id}"),
                InlineKeyboardButton("🔳 QR", callback_data=f"user:qr:{user_id}"),
            ],
            [
                InlineKeyboardButton("🔄 فعال/غیرفعال", callback_data=f"user:toggle:{user_id}"),
                InlineKeyboardButton("♻️ ریست ترافیک", callback_data=f"user:reset:{user_id}"),
            ],
            [InlineKeyboardButton("🗑 حذف", callback_data=f"user:delete_confirm:{user_id}")],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="users:list:0")],
        ]
    )


async def render_user_detail(query, user_id):
    try:
        u = await panel.get_user(user_id)
    except PanelError as e:
        await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    if isinstance(u, dict) and isinstance(u.get("user"), dict):
        u = u["user"]
    if not isinstance(u, dict):
        u = {}

    username = u.get("username", user_id)
    status = u.get("status", "active")
    status_icon = "🟢 فعال" if status == "active" else "🔴 غیرفعال"

    used = u.get("traffic_used_bytes") or u.get("used_bytes") or 0
    limit = u.get("traffic_limit_bytes") or u.get("limit_bytes") or 0
    try:
        used_gb = round(int(used) / (1024**3), 2)
        limit_gb = round(int(limit) / (1024**3), 2) if limit else "∞"
    except Exception:
        used_gb, limit_gb = "?", "?"

    lines = [
        f"👤 <b>{html.escape(str(username))}</b>",
        f"• وضعیت: {status_icon}",
        f"• ترافیک: {used_gb} / {limit_gb} GB",
    ]
    for key in ("expire_at", "expire", "proxy_ip_enabled", "proxy_country", "custom_ip_type"):
        if key in u and u[key] not in (None, "", False):
            lines.append(f"• {key}: {html.escape(str(u[key]))}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=user_detail_keyboard(user_id),
    )


# ── inbounds ────────────────────────────────────────────────────────────────


def inbounds_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 لیست اینباندها", callback_data="inbounds:list")],
            [InlineKeyboardButton("➕ ساخت اینباند سریع", callback_data="inbound:new")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu:main")],
        ]
    )


async def render_inbound_list(query):
    try:
        inbounds = await panel.list_inbounds()
    except PanelError as e:
        await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    if not inbounds:
        await query.edit_message_text(
            "هیچ اینباندی تعریف نشده.", reply_markup=inbounds_menu_keyboard()
        )
        return

    buttons = []
    for ib in inbounds:
        ib_id = obj_id(ib, "id", "inbound_id", "_id")
        name = obj_label(ib, "name", "remark", "tag", default=str(ib_id))
        proto = (ib.get("protocol") or "?") if isinstance(ib, dict) else "?"
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{name} ({proto})",
                    callback_data=f"inbound:view:{ib_id}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton("🏠 منوی اینباندها", callback_data="menu:inbounds")])
    await query.edit_message_text(
        f"📥 اینباندها ({len(inbounds)})",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def inbound_detail_keyboard(inbound_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔑 تولید کلید Reality",
                    callback_data=f"inbound:genkeys:{inbound_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🆔 Short ID جدید",
                    callback_data=f"inbound:gensid:{inbound_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑 حذف اینباند",
                    callback_data=f"inbound:delete_confirm:{inbound_id}",
                )
            ],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="inbounds:list")],
        ]
    )


async def render_inbound_detail(query, inbound_id):
    try:
        inbounds = await panel.list_inbounds()
    except PanelError as e:
        await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    ib = None
    for item in inbounds:
        if str(obj_id(item, "id", "inbound_id", "_id")) == str(inbound_id):
            ib = item
            break

    if not ib or not isinstance(ib, dict):
        await query.edit_message_text("اینباند پیدا نشد.", reply_markup=inbounds_menu_keyboard())
        return

    name = ib.get("name") or inbound_id
    lines = [
        f"📥 <b>{html.escape(str(name))}</b>",
        f"• protocol: {ib.get('protocol', '?')}",
        f"• network: {ib.get('network', '?')}",
        f"• security: {ib.get('security', '?')}",
        f"• port: {ib.get('port', '?')}",
    ]
    if ib.get("domain"):
        lines.append(f"• domain: {html.escape(str(ib['domain']))}")
    if ib.get("sni"):
        lines.append(f"• sni: {html.escape(str(ib['sni']))}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=inbound_detail_keyboard(inbound_id),
    )


# ── groups ──────────────────────────────────────────────────────────────────


def groups_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 لیست گروه‌ها", callback_data="groups:list")],
            [InlineKeyboardButton("➕ ساخت گروه", callback_data="group:new")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu:main")],
        ]
    )


async def render_group_list(query):
    try:
        groups = await panel.list_groups()
    except PanelError as e:
        await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    if not groups:
        await query.edit_message_text(
            "هیچ گروهی وجود ندارد.", reply_markup=groups_menu_keyboard()
        )
        return

    buttons = []
    for g in groups:
        gid = obj_id(g, "group_id", "id", "_id")
        name = obj_label(g, "name", default=str(gid))
        count = g.get("user_count", len(g.get("user_ids") or [])) if isinstance(g, dict) else 0
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{name} ({count} نفر)",
                    callback_data=f"group:view:{gid}",
                )
            ]
        )
    buttons.append([InlineKeyboardButton("🏠 منوی گروه‌ها", callback_data="menu:groups")])
    await query.edit_message_text(
        f"📁 گروه‌ها ({len(groups)})",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def group_detail_keyboard(group_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑 حذف گروه",
                    callback_data=f"group:delete_confirm:{group_id}",
                )
            ],
            [InlineKeyboardButton("⬅️ بازگشت", callback_data="groups:list")],
        ]
    )


# ── scanner ─────────────────────────────────────────────────────────────────


def scanner_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("☁️ Cloudflare IPs", callback_data="scanner:cf")],
            [InlineKeyboardButton("🚂 Railway IPs", callback_data="scanner:railway")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu:main")],
        ]
    )


# ── worker ──────────────────────────────────────────────────────────────────


def worker_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📡 وضعیت Worker", callback_data="worker:status")],
            [InlineKeyboardButton("🔄 Sync کاربران", callback_data="worker:sync")],
            [InlineKeyboardButton("🌍 Sync Source پروکسی‌ها", callback_data="worker:syncsrc")],
            [InlineKeyboardButton("📍 لوکیشن‌ها", callback_data="worker:locations")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu:main")],
        ]
    )


# ── callback router ─────────────────────────────────────────────────────────


@admin_only
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    # ── menus ──
    if data == "menu:main":
        await show_main_menu(query)
        return
    if data == "menu:users":
        await query.edit_message_text("بخش کاربران:", reply_markup=users_menu_keyboard())
        return
    if data == "menu:inbounds":
        await query.edit_message_text("بخش اینباندها:", reply_markup=inbounds_menu_keyboard())
        return
    if data == "menu:groups":
        await query.edit_message_text("بخش گروه‌ها:", reply_markup=groups_menu_keyboard())
        return
    if data == "menu:scanner":
        await query.edit_message_text(
            "اسکنر IP (فقط مشاهده لیست ذخیره‌شده):\n"
            "اسکن واقعی از مرورگر پنل انجام می‌شود.",
            reply_markup=scanner_menu_keyboard(),
        )
        return
    if data == "menu:worker":
        await query.edit_message_text("Cloudflare Worker:", reply_markup=worker_menu_keyboard())
        return

    # ── users ──
    if data.startswith("users:list:"):
        page = int(data.split(":")[2])
        await render_user_list(query, page)
        return

    if data.startswith("user:view:"):
        await render_user_detail(query, data.split(":", 2)[2])
        return

    if data.startswith("user:config:"):
        user_id = data.split(":", 2)[2]
        try:
            u = await panel.get_user(user_id)
        except PanelError as e:
            await query.message.reply_text(f"خطا:\n{e.detail}")
            return
        if isinstance(u, dict) and isinstance(u.get("user"), dict):
            u = u["user"]
        username = u.get("username", user_id) if isinstance(u, dict) else user_id

        configs: list[str] = []
        try:
            sub_data = await panel.get_sub_data(username)
            configs = extract_config_uris(sub_data)
        except PanelError:
            pass
        if not configs:
            try:
                cfg = await panel.get_user_config(user_id)
                configs = extract_config_uris(cfg)
            except PanelError as e:
                await query.message.reply_text(f"خطا در گرفتن کانفیگ:\n{e.detail}")
                return
        if not configs:
            await query.message.reply_text("هیچ کانفیگی پیدا نشد.")
            return

        await query.message.reply_text(f"📄 {len(configs)} کانفیگ پیدا شد:")
        for idx, uri in enumerate(configs, start=1):
            await query.message.reply_text(
                f"<b>{idx}.</b>\n<code>{html.escape(uri)}</code>",
                parse_mode=ParseMode.HTML,
            )
        sub_url = panel.sub_page_url(username)
        await query.message.reply_text(
            f"🔗 صفحه ساب:\n{sub_url}\n\n"
            "⚠️ این لینک صفحه وب است، نه subscription خام."
        )
        return

    if data.startswith("user:qr:"):
        user_id = data.split(":", 2)[2]
        try:
            qr_bytes = await panel.get_user_qr(user_id)
        except PanelError as e:
            await query.message.reply_text(f"خطا:\n{e.detail}")
            return
        await query.message.reply_photo(photo=io.BytesIO(qr_bytes))
        return

    if data.startswith("user:toggle:"):
        user_id = data.split(":", 2)[2]
        try:
            res = await panel.toggle_user(user_id)
            status = res.get("status", "?")
            await query.answer(f"وضعیت → {status}", show_alert=True)
            await render_user_detail(query, user_id)
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    if data.startswith("user:reset:"):
        user_id = data.split(":", 2)[2]
        try:
            await panel.reset_user_traffic(user_id)
            await query.answer("ترافیک ریست شد ✅", show_alert=True)
            await render_user_detail(query, user_id)
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    if data.startswith("user:delete_confirm:"):
        user_id = data.split(":", 2)[2]
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"user:delete:{user_id}"),
                    InlineKeyboardButton("❌ انصراف", callback_data=f"user:view:{user_id}"),
                ]
            ]
        )
        await query.edit_message_text("مطمئنی می‌خوای این کاربر رو حذف کنی؟", reply_markup=kb)
        return

    if data.startswith("user:delete:"):
        user_id = data.split(":", 2)[2]
        try:
            await panel.delete_user(user_id)
        except PanelError as e:
            await query.edit_message_text(f"خطا در حذف:\n{e.detail}")
            return
        await query.edit_message_text("✅ کاربر حذف شد.", reply_markup=users_menu_keyboard())
        return

    # ── inbounds ──
    if data == "inbounds:list":
        await render_inbound_list(query)
        return

    if data.startswith("inbound:view:"):
        await render_inbound_detail(query, data.split(":", 2)[2])
        return

    if data.startswith("inbound:genkeys:"):
        inbound_id = data.split(":", 2)[2]
        try:
            res = await panel.generate_reality_keys(inbound_id)
            await query.answer("کلیدهای Reality جدید ساخته شد ✅", show_alert=True)
            await render_inbound_detail(query, inbound_id)
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    if data.startswith("inbound:gensid:"):
        inbound_id = data.split(":", 2)[2]
        try:
            await panel.generate_short_id(inbound_id)
            await query.answer("Short ID جدید ساخته شد ✅", show_alert=True)
            await render_inbound_detail(query, inbound_id)
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
        return

    if data.startswith("inbound:delete_confirm:"):
        inbound_id = data.split(":", 2)[2]
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ حذف", callback_data=f"inbound:delete:{inbound_id}"),
                    InlineKeyboardButton("❌ انصراف", callback_data=f"inbound:view:{inbound_id}"),
                ]
            ]
        )
        await query.edit_message_text("اینباند حذف بشه؟", reply_markup=kb)
        return

    if data.startswith("inbound:delete:"):
        inbound_id = data.split(":", 2)[2]
        try:
            await panel.delete_inbound(inbound_id)
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
            return
        await query.edit_message_text("✅ اینباند حذف شد.", reply_markup=inbounds_menu_keyboard())
        return

    # ── groups ──
    if data == "groups:list":
        await render_group_list(query)
        return

    if data.startswith("group:view:"):
        gid = data.split(":", 2)[2]
        try:
            groups = await panel.list_groups()
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
            return
        g = next(
            (x for x in groups if str(obj_id(x, "group_id", "id")) == str(gid)),
            None,
        )
        if not g:
            await query.edit_message_text("گروه پیدا نشد.")
            return
        name = g.get("name", gid)
        count = g.get("user_count", len(g.get("user_ids") or []))
        text = (
            f"📁 <b>{html.escape(str(name))}</b>\n"
            f"• تعداد کاربران: {count}\n"
            f"• ترافیک: {g.get('traffic_limit', 0)}\n"
            f"• روز اعتبار: {g.get('expire_days', 0)}"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=group_detail_keyboard(gid)
        )
        return

    if data.startswith("group:delete_confirm:"):
        gid = data.split(":", 2)[2]
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ حذف", callback_data=f"group:delete:{gid}"),
                    InlineKeyboardButton("❌ انصراف", callback_data=f"group:view:{gid}"),
                ]
            ]
        )
        await query.edit_message_text("گروه حذف بشه؟", reply_markup=kb)
        return

    if data.startswith("group:delete:"):
        gid = data.split(":", 2)[2]
        try:
            await panel.delete_group(gid)
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
            return
        await query.edit_message_text("✅ گروه حذف شد.", reply_markup=groups_menu_keyboard())
        return

    # ── scanner ──
    if data.startswith("scanner:"):
        ctype = data.split(":", 1)[1]
        if ctype not in ("cf", "railway"):
            return
        try:
            res = await panel.scanner_ips(ctype)
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
            return
        ips = res.get("ips") or []
        if not ips:
            text = f"هیچ IP ذخیره‌شده‌ای برای <b>{ctype}</b> نیست."
        else:
            lines = [f"🔍 IPs ({ctype}) — {len(ips)} مورد:"]
            for ip in ips:
                lines.append(f"• <code>{html.escape(str(ip))}</code>")
            text = "\n".join(lines)
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=scanner_menu_keyboard()
        )
        return

    # ── worker ──
    if data == "worker:status":
        try:
            res = await panel.worker_status()
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
            return
        connected = res.get("connected") or res.get("ok")
        domain = res.get("worker_domain") or res.get("domain") or "—"
        name = res.get("worker_name") or "—"
        text = (
            f"☁️ <b>Worker Status</b>\n"
            f"• متصل: {'✅ بله' if connected else '❌ خیر'}\n"
            f"• نام: {html.escape(str(name))}\n"
            f"• دامنه: <code>{html.escape(str(domain))}</code>"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=worker_menu_keyboard()
        )
        return

    if data == "worker:sync":
        try:
            await panel.worker_sync()
            await query.answer("Sync انجام شد ✅", show_alert=True)
        except PanelError as e:
            await query.answer(f"خطا: {e.detail[:100]}", show_alert=True)
        return

    if data == "worker:syncsrc":
        try:
            await panel.worker_sync_source()
            await query.answer("Source پروکسی‌ها آپدیت شد ✅", show_alert=True)
        except PanelError as e:
            await query.answer(f"خطا: {e.detail[:100]}", show_alert=True)
        return

    if data == "worker:locations":
        try:
            res = await panel.worker_locations()
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
            return
        # response shape may vary
        locs = res.get("locations") or res.get("proxies") or res
        if isinstance(locs, dict):
            lines = ["📍 لوکیشن‌های Worker:"]
            for code, val in list(locs.items())[:30]:
                lines.append(f"• <code>{html.escape(str(code))}</code>: {html.escape(str(val)[:60])}")
            text = "\n".join(lines) if len(lines) > 1 else "لوکیشنی پیدا نشد."
        else:
            text = f"<pre>{html.escape(str(locs)[:1500])}</pre>"
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=worker_menu_keyboard()
        )
        return

    # ── stats ──
    if data == "stats:show":
        try:
            s = await panel.server_stats()
        except PanelError as e:
            await query.edit_message_text(f"خطا:\n{e.detail}")
            return
        # flexible formatting
        def g(*keys, default="—"):
            for k in keys:
                if k in s and s[k] is not None:
                    return s[k]
            return default

        text = (
            "📊 <b>آمار سرور</b>\n"
            f"• CPU: {g('cpu', 'cpu_percent')}%\n"
            f"• RAM: {g('ram', 'memory', 'mem_percent')}%\n"
            f"• Disk: {g('disk', 'disk_percent')}%\n"
            f"• Uptime: {g('uptime', 'uptime_sec')}\n"
            f"• Connections: {g('connections', 'active_connections')}\n"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
        )
        return


# ── create user conversation ────────────────────────────────────────────────


@admin_only
async def new_user_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_user"] = {}
    await query.edit_message_text("نام کاربری جدید رو بفرست:")
    return ASK_USERNAME


async def new_user_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_user"]["username"] = update.message.text.strip()
    try:
        inbounds = await panel.list_inbounds()
    except PanelError as e:
        await update.message.reply_text(f"خطا در گرفتن اینباندها:\n{e.detail}")
        return ConversationHandler.END

    if not inbounds:
        await update.message.reply_text(
            "هیچ اینباندی توی پنل تعریف نشده. اول از پنل یا بخش اینباندها یه اینباند بساز."
        )
        return ConversationHandler.END

    choices = {}
    for ib in inbounds:
        ib_id = obj_id(ib, "id", "inbound_id", "_id", "tag", "remark")
        label = obj_label(ib, "name", "remark", "tag", "id", default=str(ib_id))
        choices[str(ib_id)] = label
    context.user_data["new_user"]["_inbound_choices"] = choices
    context.user_data["new_user"]["inbound_ids"] = []

    await update.message.reply_text(
        "اینباند(ها) رو انتخاب کن (چندتا هم میشه)، بعد «تایید» رو بزن:",
        reply_markup=inbound_choice_keyboard(context),
    )
    return ASK_INBOUNDS


def inbound_choice_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    data = context.user_data["new_user"]
    selected = set(data["inbound_ids"])
    rows = []
    for ib_id, label in data["_inbound_choices"].items():
        mark = "✅ " if ib_id in selected else ""
        rows.append(
            [InlineKeyboardButton(f"{mark}{label}", callback_data=f"inbound_toggle:{ib_id}")]
        )
    rows.append([InlineKeyboardButton("تایید ➡️", callback_data="inbound_done")])
    return InlineKeyboardMarkup(rows)


async def toggle_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ib_id = query.data.split(":", 1)[1]
    ids = context.user_data["new_user"]["inbound_ids"]
    if ib_id in ids:
        ids.remove(ib_id)
    else:
        ids.append(ib_id)
    await query.edit_message_reply_markup(reply_markup=inbound_choice_keyboard(context))
    return ASK_INBOUNDS


async def inbound_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data["new_user"]["inbound_ids"]:
        await query.answer("حداقل یه اینباند انتخاب کن", show_alert=True)
        return ASK_INBOUNDS
    await query.edit_message_text("سقف ترافیک به گیگابایت رو بفرست (فقط عدد، برای نامحدود 0):")
    return ASK_LIMIT


async def new_user_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.replace(".", "", 1).isdigit():
        await update.message.reply_text("لطفاً فقط عدد بفرست (مثلاً 30 یا 0 برای نامحدود).")
        return ASK_LIMIT
    context.user_data["new_user"]["traffic_limit_gb"] = float(text)
    await update.message.reply_text("مدت اعتبار به روز رو بفرست (فقط عدد):")
    return ASK_EXPIRE


async def new_user_expire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("لطفاً فقط عدد روز بفرست.")
        return ASK_EXPIRE
    context.user_data["new_user"]["expire_days"] = int(text)

    d = context.user_data["new_user"]
    choices = d["_inbound_choices"]
    inbound_labels = ", ".join(choices[i] for i in d["inbound_ids"])
    summary = (
        f"نام کاربری: {d['username']}\n"
        f"اینباندها: {inbound_labels}\n"
        f"ترافیک: {d['traffic_limit_gb']} GB\n"
        f"اعتبار: {d['expire_days']} روز\n\n"
        "ساخته بشه؟"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ بله", callback_data="user:create_confirm"),
                InlineKeyboardButton("❌ انصراف", callback_data="menu:main"),
            ]
        ]
    )
    await update.message.reply_text(summary, reply_markup=kb)
    return CONFIRM_CREATE


async def create_user_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    d = context.user_data.get("new_user", {})

    payload = {
        "username": d["username"],
        "inbound_ids": [int(i) if str(i).isdigit() else i for i in d["inbound_ids"]],
        "traffic_limit_gb": d["traffic_limit_gb"],
        "expire_days": d["expire_days"],
    }

    try:
        created = await panel.create_user(payload)
    except PanelError as e:
        await query.edit_message_text(f"❌ ساخت کاربر با خطا مواجه شد:\n{e.detail}")
        return ConversationHandler.END

    context.user_data.pop("new_user", None)
    uname = created.get("username", d["username"]) if isinstance(created, dict) else d["username"]
    await query.edit_message_text(
        f"✅ کاربر ساخته شد: {uname}",
        reply_markup=users_menu_keyboard(),
    )
    return ConversationHandler.END


# ── create inbound conversation ─────────────────────────────────────────────


@admin_only
async def new_inbound_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_inbound"] = {}
    await query.edit_message_text("نام اینباند رو بفرست:")
    return ASK_IB_NAME


async def new_inbound_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_inbound"]["name"] = update.message.text.strip()[:60]
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("VLESS + WS + TLS", callback_data="ibproto:vless_ws")],
            [InlineKeyboardButton("VLESS Reality", callback_data="ibproto:reality")],
            [InlineKeyboardButton("Worker (Cloudflare)", callback_data="ibproto:worker")],
            [InlineKeyboardButton("❌ انصراف", callback_data="menu:inbounds")],
        ]
    )
    await update.message.reply_text("پروتکل رو انتخاب کن:", reply_markup=kb)
    return ASK_IB_PROTO


async def new_inbound_proto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]
    d = context.user_data["new_inbound"]

    if choice == "vless_ws":
        payload = {
            "name": d["name"],
            "protocol": "vless",
            "network": "ws",
            "security": "tls",
        }
    elif choice == "reality":
        payload = {
            "name": d["name"],
            "protocol": "reality",
            "network": "tcp",
            "security": "reality",
        }
    elif choice == "worker":
        payload = {
            "name": d["name"],
            "protocol": "worker",
        }
    else:
        await query.edit_message_text("انتخاب نامعتبر.")
        return ConversationHandler.END

    try:
        created = await panel.create_inbound(payload)
    except PanelError as e:
        await query.edit_message_text(f"❌ خطا در ساخت اینباند:\n{e.detail}")
        return ConversationHandler.END

    context.user_data.pop("new_inbound", None)
    name = created.get("name", d["name"]) if isinstance(created, dict) else d["name"]
    await query.edit_message_text(
        f"✅ اینباند ساخته شد: {name}",
        reply_markup=inbounds_menu_keyboard(),
    )
    return ConversationHandler.END


# ── create group conversation ───────────────────────────────────────────────


@admin_only
async def new_group_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("نام گروه رو بفرست:")
    return ASK_GRP_NAME


async def new_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()[:60]
    try:
        created = await panel.create_group({"name": name})
    except PanelError as e:
        await update.message.reply_text(f"❌ خطا:\n{e.detail}")
        return ConversationHandler.END
    gname = created.get("name", name) if isinstance(created, dict) else name
    await update.message.reply_text(
        f"✅ گروه ساخته شد: {gname}",
        reply_markup=groups_menu_keyboard(),
    )
    return ConversationHandler.END


# ── change password conversation ────────────────────────────────────────────


@admin_only
async def change_pass_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("پسورد فعلی پنل رو بفرست:")
    return ASK_OLD_PASS


async def change_pass_old(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["old_pass"] = update.message.text.strip()
    await update.message.reply_text("پسورد جدید رو بفرست:")
    return ASK_NEW_PASS


async def change_pass_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old = context.user_data.get("old_pass", "")
    new = update.message.text.strip()
    if len(new) < 4:
        await update.message.reply_text("پسورد جدید خیلی کوتاهه. دوباره بفرست:")
        return ASK_NEW_PASS
    try:
        await panel.change_password(old, new)
    except PanelError as e:
        await update.message.reply_text(f"❌ خطا:\n{e.detail}")
        return ConversationHandler.END
    # update local client password so future requests work
    panel.admin_password = new
    context.user_data.pop("old_pass", None)
    await update.message.reply_text(
        "✅ پسورد پنل تغییر کرد.\nیادت نره مقدار PANEL_ADMIN_PASSWORD رو هم توی .env آپدیت کنی.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("لغو شد.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ── app wiring ──────────────────────────────────────────────────────────────


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"⚠️ خطای غیرمنتظره:\n<code>{html.escape(str(context.error))}</code>",
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        pass


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # create user
    conv_user = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_user_entry, pattern="^user:new$")],
        states={
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_user_username)],
            ASK_INBOUNDS: [
                CallbackQueryHandler(toggle_inbound, pattern="^inbound_toggle:"),
                CallbackQueryHandler(inbound_done, pattern="^inbound_done$"),
            ],
            ASK_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_user_limit)],
            ASK_EXPIRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_user_expire)],
            CONFIRM_CREATE: [
                CallbackQueryHandler(create_user_confirmed, pattern="^user:create_confirm$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )
    app.add_handler(conv_user)

    # create inbound
    conv_ib = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_inbound_entry, pattern="^inbound:new$")],
        states={
            ASK_IB_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_inbound_name)],
            ASK_IB_PROTO: [CallbackQueryHandler(new_inbound_proto, pattern="^ibproto:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )
    app.add_handler(conv_ib)

    # create group
    conv_grp = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_group_entry, pattern="^group:new$")],
        states={
            ASK_GRP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_group_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )
    app.add_handler(conv_grp)

    # change password
    conv_pass = ConversationHandler(
        entry_points=[CallbackQueryHandler(change_pass_entry, pattern="^settings:pass$")],
        states={
            ASK_OLD_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_pass_old)],
            ASK_NEW_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_pass_new)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )
    app.add_handler(conv_pass)

    # general callbacks (must be after conversations)
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)

    return app


def main():
    app = build_app()
    logger.info("Spider Panel Bot starting (expanded)...")
    app.run_polling()


if __name__ == "__main__":
    main()
