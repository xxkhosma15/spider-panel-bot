"""
Admin-only Telegram bot for Spider Panel.

Lets you (and only you — checked via ADMIN_CHAT_ID) manage panel users
from Telegram: list, create, delete, and fetch config/QR.

Setup:
    1. pip install -r requirements.txt
    2. Copy .env.example to .env and fill in the values
    3. python bot.py

See README.md for details and for how to extend this with more panel
features (inbounds, worker, server stats, etc).
"""

from __future__ import annotations

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

from panel_client import PanelClient, PanelError

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

# Conversation states for the "create user" flow
ASK_USERNAME, ASK_INBOUNDS, ASK_LIMIT, ASK_EXPIRE, CONFIRM_CREATE = range(5)


# ---------------------------------------------------------------------
# access control
# ---------------------------------------------------------------------


def admin_only(handler):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id != ADMIN_CHAT_ID:
            if update.message:
                await update.message.reply_text("⛔️ این ربات خصوصیه.")
            return ConversationHandler.END
        return await handler(update, context)

    return wrapped


# ---------------------------------------------------------------------
# main menu
# ---------------------------------------------------------------------


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 لیست کاربران", callback_data="users:list:0")],
            [InlineKeyboardButton("➕ ساخت کاربر جدید", callback_data="user:new")],
        ]
    )


@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "به ربات مدیریت Spider Panel خوش اومدی 🕷️\nیکی از گزینه‌ها رو انتخاب کن:",
        reply_markup=main_menu_keyboard(),
    )


async def show_main_menu(query):
    await query.edit_message_text(
        "یکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu_keyboard()
    )


# ---------------------------------------------------------------------
# user list / detail
# ---------------------------------------------------------------------

PAGE_SIZE = 8


async def render_user_list(query, page: int):
    try:
        users = await panel.list_users()
    except PanelError as e:
        await query.edit_message_text(f"خطا در گرفتن لیست کاربران:\n{e.detail}")
        return

    if not users:
        await query.edit_message_text(
            "هیچ کاربری هنوز ثبت نشده.", reply_markup=main_menu_keyboard()
        )
        return

    start = page * PAGE_SIZE
    chunk = users[start : start + PAGE_SIZE]

    buttons = [
        [
            InlineKeyboardButton(
                f"{u.get('username', u.get('id'))}",
                callback_data=f"user:view:{u.get('id')}",
            )
        ]
        for u in chunk
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"users:list:{page-1}"))
    if start + PAGE_SIZE < len(users):
        nav.append(InlineKeyboardButton("بعدی ➡️", callback_data=f"users:list:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="menu:main")])

    await query.edit_message_text(
        f"👥 کاربران ({len(users)} نفر) — صفحه {page+1}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def user_detail_keyboard(user_id) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 کانفیگ", callback_data=f"user:config:{user_id}")],
            [InlineKeyboardButton("🔳 QR", callback_data=f"user:qr:{user_id}")],
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

    lines = [f"👤 <b>{u.get('username', user_id)}</b>"]
    for key in ("expire", "limit_bytes", "traffic_limit_gb", "proxy_ip_enabled", "proxy_country"):
        if key in u:
            lines.append(f"• {key}: {u[key]}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=user_detail_keyboard(user_id),
    )


# ---------------------------------------------------------------------
# callback query router
# ---------------------------------------------------------------------


@admin_only
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu:main":
        await show_main_menu(query)
        return

    if data.startswith("users:list:"):
        page = int(data.split(":")[2])
        await render_user_list(query, page)
        return

    if data.startswith("user:view:"):
        user_id = data.split(":", 2)[2]
        await render_user_detail(query, user_id)
        return

    if data.startswith("user:config:"):
        user_id = data.split(":", 2)[2]
        try:
            cfg = await panel.get_user_config(user_id)
        except PanelError as e:
            await query.message.reply_text(f"خطا:\n{e.detail}")
            return
        text = cfg if isinstance(cfg, str) else str(cfg)
        # send as a code block, chunked if long
        for i in range(0, len(text), 3500):
            await query.message.reply_text(
                f"<code>{text[i:i+3500]}</code>", parse_mode=ParseMode.HTML
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
        await query.edit_message_text("✅ کاربر حذف شد.", reply_markup=main_menu_keyboard())
        return


# ---------------------------------------------------------------------
# create-user conversation
# ---------------------------------------------------------------------


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
        await update.message.reply_text("هیچ اینباندی توی پنل تعریف نشده. اول از پنل یه اینباند بساز.")
        return ConversationHandler.END

    context.user_data["new_user"]["_inbound_choices"] = {
        str(ib.get("id")): ib.get("remark", ib.get("id")) for ib in inbounds
    }
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
        rows.append([InlineKeyboardButton(f"{mark}{label}", callback_data=f"inbound_toggle:{ib_id}")])
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

    # NOTE: adjust these field names if your panel fork expects different
    # ones — these follow the naming used in the panel's README/features.
    payload = {
        "username": d["username"],
        "inbound_ids": [int(i) if i.isdigit() else i for i in d["inbound_ids"]],
        "traffic_limit_gb": d["traffic_limit_gb"],
        "expire_days": d["expire_days"],
    }

    try:
        created = await panel.create_user(payload)
    except PanelError as e:
        await query.edit_message_text(f"❌ ساخت کاربر با خطا مواجه شد:\n{e.detail}")
        return ConversationHandler.END

    context.user_data.pop("new_user", None)
    await query.edit_message_text(
        f"✅ کاربر ساخته شد: {created.get('username', d['username'])}",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_user", None)
    await update.message.reply_text("لغو شد.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END


# ---------------------------------------------------------------------
# app wiring
# ---------------------------------------------------------------------


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(new_user_entry, pattern="^user:new$")],
        states={
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_user_username)],
            ASK_INBOUNDS: [
                CallbackQueryHandler(toggle_inbound, pattern="^inbound_toggle:"),
                CallbackQueryHandler(inbound_done, pattern="^inbound_done$"),
            ],
            ASK_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_user_limit)],
            ASK_EXPIRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_user_expire)],
            CONFIRM_CREATE: [CallbackQueryHandler(create_user_confirmed, pattern="^user:create_confirm$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    app.add_handler(conv)

    # general callback router (list/view/config/qr/delete/menu) — must be
    # added after the conversation handler so it doesn't swallow its callbacks
    app.add_handler(CallbackQueryHandler(on_callback))

    return app


def main():
    app = build_app()
    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
