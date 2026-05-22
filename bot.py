#!/usr/bin/env python3
"""
ربات تلگرام نمونه — نمایش اطلاعات اشتراک 3x-ui از روی لینک ساب.

کاربر لینک ساب می‌فرستد؛ ربات پنل را از panels.json پیدا می‌کند
و از API پنل اطلاعات را می‌خواند.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from xui_api import (
    PanelConfig,
    XuiPanel,
    load_panels,
    parse_sub_link,
    resolve_panel,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("subbot")

PANELS_FILE = os.getenv("PANELS_FILE", "panels.json")


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None or not str(val).strip():
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# پیش‌فرض false — پنل‌های IP معمولاً گواهی منقضی/خودامضا دارند
VERIFY_SSL = _env_bool("VERIFY_SSL", default=False)

if not VERIFY_SSL:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _fmt_bytes(n: int) -> str:
    n = max(0, int(n))
    units = ("B", "KB", "MB", "GB", "TB")
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.2f} {u}" if u != "B" else f"{int(v)} B"
        v /= 1024
    return f"{n} B"


def _fmt_expiry(expiry_ms: int, pending_days: int | None) -> str:
    if expiry_ms == 0:
        return "نامحدود"
    if expiry_ms < 0:
        d = pending_days if pending_days is not None else int(abs(expiry_ms) // (86400 * 1000))
        return f"از اولین اتصال — {d} روز"
    dt = datetime.fromtimestamp(expiry_ms / 1000.0, tz=timezone.utc)
    local = dt.astimezone()
    remain = dt - datetime.now(timezone.utc)
    days_left = max(0, remain.days)
    return f"{local.strftime('%Y-%m-%d %H:%M')} ({days_left} روز مانده)"


def format_subscription_message(info) -> str:
    used = info.up + info.down
    limit = info.total_limit
    if limit > 0:
        pct = min(100.0, (used / limit) * 100.0)
        traffic_line = (
            f"📊 حجم: {_fmt_bytes(used)} / {_fmt_bytes(limit)} ({pct:.1f}%)"
        )
    else:
        traffic_line = f"📊 مصرف: {_fmt_bytes(used)} (نامحدود)"

    status = "✅ فعال" if info.enabled else "❌ غیرفعال"
    lines = [
        "📋 <b>اطلاعات اشتراک</b>",
        "",
        f"🖥 پنل: <b>{info.panel_name}</b>",
        f"📧 ایمیل: <code>{info.email or '—'}</code>",
        f"🔗 ساب: <code>{info.sub_id}</code>",
        f"⚡ وضعیت: {status}",
        traffic_line,
        f"⬆️ آپلود: {_fmt_bytes(info.up)}",
        f"⬇️ دانلود: {_fmt_bytes(info.down)}",
        f"📅 انقضا: {_fmt_expiry(info.expiry_ms, info.pending_days)}",
    ]
    if info.inbound_id:
        proto = f" ({info.protocol})" if info.protocol else ""
        lines.append(f"📡 اینباند: #{info.inbound_id}{proto}")
    return "\n".join(lines)


def get_panel_client(panel_cfg: PanelConfig) -> XuiPanel:
    return XuiPanel(panel_cfg, verify_ssl=VERIFY_SSL)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "سلام 👋\n\n"
        "لینک اشتراک (subscription) خود را بفرستید.\n\n"
        "مثال:\n"
        "<code>https://206.71.158.69:2096/sub/a09sdzfhq22n0lor</code>",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "فقط لینک ساب را ارسال کنید.\n"
        "ربات پنل را از فایل panels.json پیدا می‌کند و "
        "اطلاعات را از API پنل 3x-ui می‌خواند.",
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return

    panels: dict = context.application.bot_data.get("panels") or {}
    if not panels:
        await update.message.reply_text("❌ فایل panels.json خالی است یا لود نشده.")
        return

    wait = await update.message.reply_text("⏳ در حال بررسی اشتراک…")

    try:
        sub_base, sub_id = parse_sub_link(text)
        panel_cfg = resolve_panel(panels, sub_base)
        sub_url = f"{panel_cfg.sub_base.rstrip('/')}/{sub_id}"
        client = get_panel_client(panel_cfg)
        info = client.get_subscription_info(sub_id, sub_url)
        msg = format_subscription_message(info)
        await wait.edit_text(msg, parse_mode="HTML")
    except LookupError as exc:
        await wait.edit_text(f"❌ {exc}")
    except ValueError as exc:
        await wait.edit_text(f"❌ {exc}")
    except Exception as exc:
        log.exception("subscription lookup failed")
        await wait.edit_text(f"❌ خطا: {exc}")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN در .env تنظیم نشده است.")

    if not os.path.isfile(PANELS_FILE):
        raise SystemExit(
            f"فایل {PANELS_FILE} وجود ندارد. "
            f"از panels.json.example کپی بگیرید: cp panels.json.example panels.json"
        )

    panels = load_panels(PANELS_FILE)
    log.info("Loaded %d panel(s) from %s", len(panels), PANELS_FILE)
    log.info("VERIFY_SSL=%s (برای پنل IP معمولاً false)", VERIFY_SSL)

    app = Application.builder().token(token).build()
    app.bot_data["panels"] = panels

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
