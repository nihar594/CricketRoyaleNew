"""
Gems economy: /gems balance, /give transfers, /flip coin-flip gambling,
plus the match-reward message sent right after Player of the Match at
the end of every match.
"""

from __future__ import annotations

import asyncio
import io
import random

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import config
from utils.scoreboard import escape_md
from utils.userstats_card import _composite_theme_sync


async def gems_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username, user.first_name)
    gems = await db.get_gems(user.id)

    display_name = f"@{user.username}" if user.username else escape_md(user.first_name)
    text = (
        "💎 *Your Balance*\n\n"
        f"👤 User: {display_name}\n"
        f"🆔 ID: {user.id}\n\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 *Total Gems:* {gems}\n"
        "━━━━━━━━━━━━━━━\n\n"
        "🔥 Keep playing matches to earn more gems!\n"
        "🏆 Win POTM & boost your rewards!\n"
        "🛒 Spend them in /shop — titles & perks!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def give_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Gems to another player. Either reply to their message:
        /give 5000
    or tag them directly:
        /give 5000 @username
    (tagging only works if that person has used the bot before, since
    Telegram doesn't resolve @usernames to an ID for us)."""
    sender = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(sender.id, sender.username, sender.first_name)

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "• Reply to their message: /give 5000\n"
            "• Or tag them: /give 5000 @username"
        )
        return

    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a whole number, e.g. /give 500 @username")
        return

    if amount <= 0:
        await update.message.reply_text("Amount must be greater than 0.")
        return

    recipient_id = None
    recipient_username = None
    recipient_first_name = None
    recipient_name = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target = update.message.reply_to_message.from_user
        recipient_id = target.id
        recipient_username = target.username
        recipient_first_name = target.first_name
        recipient_name = f"@{target.username}" if target.username else escape_md(target.first_name)
    elif len(args) >= 2:
        username_arg = args[1]
        row = await db.get_user_by_username(username_arg)
        if row is None:
            await update.message.reply_text(
                f"Couldn't find {username_arg} -- they need to have used the bot at least once "
                "(e.g. /start) before you can send them Gems this way. You can also reply "
                "directly to their message with /give <amount> instead."
            )
            return
        recipient_id = row["user_id"]
        recipient_username = row["username"]
        recipient_first_name = row["first_name"]
        recipient_name = f"@{row['username']}" if row["username"] else escape_md(row["first_name"])
    else:
        await update.message.reply_text(
            "Tell me who to send Gems to -- reply to their message with /give <amount>, "
            "or tag them: /give <amount> @username"
        )
        return

    if recipient_id == sender.id:
        await update.message.reply_text("You can't send Gems to yourself!")
        return

    sender_gems = await db.get_gems(sender.id)
    if sender_gems < amount and sender.id not in config.OWNER_IDS:
        await update.message.reply_text(f"You don't have enough Gems for that. Your balance: {sender_gems} 💎")
        return

    await db.add_gems(sender.id, -amount, sender.username, sender.first_name)
    await db.add_gems(recipient_id, amount, recipient_username, recipient_first_name)

    sender_name = f"@{sender.username}" if sender.username else escape_md(sender.first_name)
    text = (
        "🎁 *Gems Sent!*\n\n"
        f"👤 From: {sender_name}\n"
        f"👤 To: {recipient_name}\n"
        f"💰 Amount: {amount} 💎\n\n"
        "✅ Transfer complete!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# SQLite's INTEGER column (and the sqlite3/aiosqlite driver underneath
# it) tops out at a signed 64-bit integer -- anything above this raises
# an OverflowError rather than storing an inflated/wrong value, so this
# is the real ceiling even though it's smaller than a literal
# 99,999,999,999,999,999,999 would be.
MAX_GIFT_AMOUNT = 9_223_372_036_854_775_807  # 2^63 - 1, SQLite's max INTEGER


async def gift_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only: /gift <amount> -- reply to someone's message to mint
    that many Gems straight into their balance, no cap on the sender's
    own balance since nothing is deducted from it (unlike /give, this
    creates Gems rather than transferring them)."""
    sender = update.effective_user
    if sender.id not in config.OWNER_IDS:
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
        await update.message.reply_text(
            "Reply to the player's message with /gift <amount> to send them Gems."
        )
        return

    if not context.args:
        await update.message.reply_text(f"Usage: reply with /gift <amount> (max {MAX_GIFT_AMOUNT})")
        return

    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a whole number, e.g. /gift 5000")
        return

    if amount <= 0:
        await update.message.reply_text("Amount must be greater than 0.")
        return
    if amount > MAX_GIFT_AMOUNT:
        await update.message.reply_text(f"Amount can't exceed {MAX_GIFT_AMOUNT}.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == sender.id:
        await update.message.reply_text("You can't gift Gems to yourself!")
        return

    db = context.application.bot_data["db"]
    current_balance = await db.get_gems(target.id)
    if current_balance + amount > MAX_GIFT_AMOUNT:
        await update.message.reply_text(
            f"That would push their balance past the maximum Gems a balance can hold "
            f"({MAX_GIFT_AMOUNT}). Their current balance is {current_balance} 💎 -- "
            f"try a smaller amount."
        )
        return

    await db.add_gems(target.id, amount, target.username, target.first_name)

    recipient_name = f"@{target.username}" if target.username else escape_md(target.first_name)
    new_balance = await db.get_gems(target.id)
    text = (
        "👑 *Owner Gift!*\n\n"
        f"👤 To: {recipient_name}\n"
        f"💎 Amount: {amount} 💎\n"
        f"💰 New balance: {new_balance} 💎\n\n"
        "✅ Gems minted and delivered!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def flip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Coin-flip gambling: /flip h <amount> or /flip t <amount>.
    The house keeps a slight edge -- config.FLIP_WIN_CHANCE (45%) chance
    to win, 55% to lose."""
    user = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username, user.first_name)

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /flip <h|t> <amount>\nExample: /flip h 200  (h = heads, t = tails)"
        )
        return

    choice_raw = args[0].lower()
    if choice_raw in ("h", "heads"):
        user_choice = "heads"
    elif choice_raw in ("t", "tails"):
        user_choice = "tails"
    else:
        await update.message.reply_text("Choose h (heads) or t (tails). Example: /flip h 200")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a whole number, e.g. /flip h 200")
        return

    if amount <= 0:
        await update.message.reply_text("Bet amount must be greater than 0.")
        return

    balance = await db.get_gems(user.id)
    if balance < amount:
        await update.message.reply_text(f"You don't have enough Gems for that bet. Your balance: {balance} 💎")
        return

    user_wins = random.random() < config.FLIP_WIN_CHANCE
    result = user_choice if user_wins else ("tails" if user_choice == "heads" else "heads")

    if user_wins:
        await db.add_gems(user.id, amount, user.username, user.first_name)
        new_balance = balance + amount
        text = (
            f"🎲 Coin Flipped: {result.upper()} 🪙\n"
            "✅ *You Won!*\n\n"
            f"💎 Bet: {amount}\n"
            f"🏆 Won: +{amount}\n"
            f"💰 Total Gems: {new_balance}"
        )
    else:
        await db.add_gems(user.id, -amount, user.username, user.first_name)
        new_balance = balance - amount
        text = (
            f"🎲 Coin Flipped: {result.upper()} 🪙\n"
            "❌ *You Lost!*\n\n"
            f"💎 Bet: {amount}\n"
            f"💸 Lost: -{amount}\n"
            f"💰 Total Gems: {new_balance}"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def dice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dice gambling: /dice <amount>. Rolls 1-6 -- 4/5/6 is a win, 1/2/3
    is a loss. Overall win probability is config.DICE_WIN_CHANCE (45%),
    same house edge as /flip; which face shows is picked to match
    whichever outcome was already decided."""
    user = update.effective_user
    db = context.application.bot_data["db"]
    await db.upsert_user(user.id, user.username, user.first_name)

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /dice <amount>\nExample: /dice 200")
        return

    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a whole number, e.g. /dice 200")
        return

    if amount <= 0:
        await update.message.reply_text("Bet amount must be greater than 0.")
        return

    balance = await db.get_gems(user.id)
    if balance < amount:
        await update.message.reply_text(f"You don't have enough Gems for that bet. Your balance: {balance} 💎")
        return

    user_wins = random.random() < config.DICE_WIN_CHANCE
    roll = random.choice([4, 5, 6]) if user_wins else random.choice([1, 2, 3])

    if user_wins:
        await db.add_gems(user.id, amount, user.username, user.first_name)
        text = f"🎲 Dice Rolled: {roll}\n🏆 *You Won!*\n💎 +{amount} Gems"
    else:
        await db.add_gems(user.id, -amount, user.username, user.first_name)
        text = f"🎲 Dice Rolled: {roll}\n❌ *You Lost!*\n💎 -{amount} Gems"

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def send_match_rewards(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    winner_name: str,
    potm_name: str,
    winner_coins: int,
    potm_coins: int,
) -> None:
    """Sent as its own message right after the Player of the Match
    announcement -- does not touch/replace that message."""
    text = (
        "🏁 *Match Completed!*\n\n"
        f"🏆 *Winner:* {winner_name}\n"
        f"🎯 *Player of the Match:* {potm_name}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "💰 *Match Rewards*\n\n"
        f"🥇 Winner Reward: +{winner_coins} Coins\n"
        f"🌟 POTM Bonus: +{potm_coins} Coins\n\n"
        "━━━━━━━━━━━━━━━\n\n"
        "💎 Keep playing to earn more rewards!\n"
        "🔥 Next match awaits… are you ready?"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)


# --------------------------------------------------------------------------
# Gems Shop: spend Gems on cosmetic titles and the Life Saver perk.
# --------------------------------------------------------------------------
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from utils.keyboards import shop_menu_keyboard
    db = context.application.bot_data["db"]
    gems = await db.get_gems(update.effective_user.id)
    text = (
        "🛒 *GEMS SHOP* 🛒\n"
        "━━━━━━━━━━━━━━━\n\n"
        f"💰 Your balance: *{gems}* 💎\n\n"
        "🏷️ *Titles* — permanent, shows next to your name in /teams, "
        "/score and /userstats\n"
        "🛡️ *Life Saver* — survive one dismissal automatically, next "
        "match you're out in\n\n"
        "What would you like to browse?"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_menu_keyboard())


async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    db = context.application.bot_data["db"]
    data = query.data

    from utils.keyboards import shop_menu_keyboard, shop_titles_keyboard, shop_lifesaver_keyboard, shop_themes_keyboard

    if data == "shop:menu":
        await query.answer()
        gems = await db.get_gems(user.id)
        text = (
            "🛒 *GEMS SHOP* 🛒\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"💰 Your balance: *{gems}* 💎\n\n"
            "What would you like to browse?"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_menu_keyboard())
        return

    if data == "shop:titles":
        await query.answer()
        owned = await db.get_owned_titles(user.id)
        active = await db.get_active_title(user.id)
        gems = await db.get_gems(user.id)
        text = (
            "🏷️ *TITLES* 🏷️\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"💰 Your balance: *{gems}* 💎\n\n"
            "Buy a title once, then equip it to show it next to your name "
            "in /teams, /score and /userstats."
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_titles_keyboard(owned, active)
        )
        return

    if data == "shop:lifesaver":
        await query.answer()
        stock = await db.get_life_saver_stock(user.id)
        gems = await db.get_gems(user.id)
        text = (
            "🛡️ *LIFE SAVER* 🛡️\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"💰 Your balance: *{gems}* 💎\n"
            f"📦 You currently own: *{stock}*\n\n"
            "The instant you'd be given out, one Life Saver is used "
            "automatically instead -- you stay not out and the match "
            "carries on. Stacks: buy as many as you like."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_lifesaver_keyboard())
        return

    if data.startswith("shop:buy:"):
        title_key = data.split(":")[2]
        from config import SHOP_TITLES
        entry = next((t for t in SHOP_TITLES if t[0] == title_key), None)
        if entry is None:
            await query.answer("Unknown title.", show_alert=True)
            return
        _, label, price = entry
        result = await db.buy_title(user.id, title_key, price)
        if result == "already_owned":
            await query.answer("You already own this title!", show_alert=True)
        elif result == "insufficient_gems":
            await query.answer("Not enough Gems for that title!", show_alert=True)
        else:
            await query.answer(f"Bought {label}! 🎉")
        owned = await db.get_owned_titles(user.id)
        active = await db.get_active_title(user.id)
        gems = await db.get_gems(user.id)
        text = (
            "🏷️ *TITLES* 🏷️\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"💰 Your balance: *{gems}* 💎"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_titles_keyboard(owned, active)
        )
        return

    if data.startswith("shop:equip:"):
        title_key = data.split(":", 2)[2]
        title_key = None if title_key == "none" else title_key
        ok = await db.equip_title(user.id, title_key)
        if not ok:
            await query.answer("You don't own that title.", show_alert=True)
        else:
            await query.answer("Unequipped." if title_key is None else "Title equipped! 👑")
        owned = await db.get_owned_titles(user.id)
        active = await db.get_active_title(user.id)
        gems = await db.get_gems(user.id)
        text = (
            "🏷️ *TITLES* 🏷️\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"💰 Your balance: *{gems}* 💎"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_titles_keyboard(owned, active)
        )
        return

    if data == "shop:buylifesaver":
        from config import LIFE_SAVER_PRICE
        result = await db.buy_life_saver(user.id, LIFE_SAVER_PRICE)
        if result == "insufficient_gems":
            await query.answer("Not enough Gems for a Life Saver!", show_alert=True)
        else:
            await query.answer("Life Saver purchased! 🛡️")
        stock = await db.get_life_saver_stock(user.id)
        gems = await db.get_gems(user.id)
        text = (
            "🛡️ *LIFE SAVER* 🛡️\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"💰 Your balance: *{gems}* 💎\n"
            f"📦 You currently own: *{stock}*"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_lifesaver_keyboard())
        return

    if data.startswith("shop:themes:"):
        await query.answer()
        page = int(data.split(":")[2])
        owned = await db.get_owned_themes(user.id)
        active = await db.get_active_theme(user.id)
        gems = await db.get_gems(user.id)
        text = (
            "🎨 *USERSTATS THEMES* 🎨\n"
            "━━━━━━━━━━━━━━━\n\n"
            f"💰 Your balance: *{gems}* 💎\n\n"
            "Buy a background theme once, then equip it to change how your "
            "/userstats card looks. Tap 👁️ Preview on any theme to see a "
            "demo with your own stats first."
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_themes_keyboard(owned, active, page)
        )
        return

    if data.startswith("shop:previewtheme:"):
        parts = data.split(":")
        theme_key = parts[2]
        from config import USERSTATS_THEMES
        entry = next((t for t in USERSTATS_THEMES if t[0] == theme_key), None)
        if entry is None:
            await query.answer("Unknown theme.", show_alert=True)
            return
        _, label, price, filename = entry
        await query.answer("Rendering preview... 🎨")

        row = await db.get_user_stats(user.id)
        if row is None or row["matches_played"] == 0:
            # Brand new / never-played accounts would otherwise preview
            # as an all-zero card, which doesn't show off the theme at
            # all -- use representative demo numbers instead just for
            # this preview.
            row = {
                "matches_played": 42, "matches_won": 25, "runs_scored": 1587, "balls_faced": 1120,
                "times_out": 30, "runs_conceded": 980, "balls_bowled": 720,
                "wickets_taken": 34, "best_bowling": "4/18", "centuries": 2,
                "fifties": 9, "motm_awards": 6, "highest_score": 108,
            }

        image_bytes = await asyncio.to_thread(_composite_theme_sync, None, row, filename)
        owned = await db.get_owned_themes(user.id)
        is_owned = theme_key in owned
        caption = (
            f"👁️ *Preview: {label}*\n"
            + ("This is one you already own -- equip it from the Themes menu!"
               if is_owned else f"Price: *{price}* 💎 — buy it from the Themes menu to equip it.")
        )
        if image_bytes:
            await query.message.reply_photo(
                photo=io.BytesIO(image_bytes), caption=caption, parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.message.reply_text(
                f"Couldn't render a preview for {label} right now.", parse_mode=ParseMode.MARKDOWN
            )
        return

    if data.startswith("shop:buytheme:"):
        theme_key = data.split(":")[2]
        from config import USERSTATS_THEMES
        entry = next((t for t in USERSTATS_THEMES if t[0] == theme_key), None)
        if entry is None:
            await query.answer("Unknown theme.", show_alert=True)
            return
        _, label, price, _filename = entry
        result = await db.buy_theme(user.id, theme_key, price)
        if result == "already_owned":
            await query.answer("You already own this theme!", show_alert=True)
        elif result == "insufficient_gems":
            await query.answer("Not enough Gems for that theme!", show_alert=True)
        else:
            await query.answer(f"Bought {label}! 🎉")
        owned = await db.get_owned_themes(user.id)
        active = await db.get_active_theme(user.id)
        gems = await db.get_gems(user.id)
        page = next((i // 5 for i, t in enumerate(USERSTATS_THEMES) if t[0] == theme_key), 0)
        text = f"🎨 *USERSTATS THEMES* 🎨\n━━━━━━━━━━━━━━━\n\n💰 Your balance: *{gems}* 💎"
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_themes_keyboard(owned, active, page)
        )
        return

    if data.startswith("shop:equiptheme:"):
        theme_key = data.split(":", 2)[2]
        theme_key = None if theme_key == "none" else theme_key
        ok = await db.equip_theme(user.id, theme_key)
        if not ok:
            await query.answer("You don't own that theme.", show_alert=True)
        else:
            await query.answer("Reset to default." if theme_key is None else "Theme equipped! 🎨")
        owned = await db.get_owned_themes(user.id)
        active = await db.get_active_theme(user.id)
        gems = await db.get_gems(user.id)
        from config import USERSTATS_THEMES
        page = next((i // 5 for i, t in enumerate(USERSTATS_THEMES) if t[0] == theme_key), 0) if theme_key else 0
        text = f"🎨 *USERSTATS THEMES* 🎨\n━━━━━━━━━━━━━━━\n\n💰 Your balance: *{gems}* 💎"
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=shop_themes_keyboard(owned, active, page)
        )
        return
