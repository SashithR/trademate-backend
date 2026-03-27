import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

os.environ["HTTPX_FORCE_IPV4"] = "1"
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8222011135:AAFHHiBFsE0J85TYAgNRmy9x-FHfKGgfrG0")
API_BASE = "http://127.0.0.1:8000"

# --------- Local cache (optional) ---------
user_shop = {}  # telegram_user_id -> shop_id (cache for faster use)

# Track low stock items to avoid repeating alerts
last_low_set = {}  # shop_id -> set(product_id)

# ===== SALE (multi-item cart) =====
sale_state = {}
sale_selected = {}
sale_search_results = {}
sale_cart = {}
sale_edit_index = {}

# ===== PURCHASE EXISTING =====
purchase_state = {}
purchase_selected = {}
purchase_search_results = {}
purchase_qty = {}
purchase_cost = {}
purchase_sell = {}
purchase_alert = {}

# ===== ADD NEW PRODUCT =====
newp_state = {}
newp_name = {}
newp_unit = {}
newp_qty = {}
newp_cost = {}
newp_sell = {}
newp_alert = {}

# ===== CASH OUT =====
cashout_state = {}   # user_id -> WAIT_REASON | WAIT_AMOUNT
cashout_reason = {}  # user_id -> str | None


def clear_sale(user_id: int):
    sale_state.pop(user_id, None)
    sale_selected.pop(user_id, None)
    sale_search_results.pop(user_id, None)
    sale_edit_index.pop(user_id, None)

def clear_sale_cart(user_id: int):
    sale_cart.pop(user_id, None)

def clear_purchase(user_id: int):
    purchase_state.pop(user_id, None)
    purchase_selected.pop(user_id, None)
    purchase_search_results.pop(user_id, None)
    purchase_qty.pop(user_id, None)
    purchase_cost.pop(user_id, None)
    purchase_sell.pop(user_id, None)
    purchase_alert.pop(user_id, None)

def clear_new_product(user_id: int):
    newp_state.pop(user_id, None)
    newp_name.pop(user_id, None)
    newp_unit.pop(user_id, None)
    newp_qty.pop(user_id, None)
    newp_cost.pop(user_id, None)
    newp_sell.pop(user_id, None)
    newp_alert.pop(user_id, None)

def clear_cashout(user_id: int):
    cashout_state.pop(user_id, None)
    cashout_reason.pop(user_id, None)

def ensure_cart(user_id: int):
    if user_id not in sale_cart:
        sale_cart[user_id] = []

def get_cart_total(user_id: int) -> float:
    cart = sale_cart.get(user_id, [])
    return sum(float(it["qty"]) * float(it["unit_price"]) for it in cart)

def format_cart_message(user_id: int, added_line: str = "") -> str:
    cart = sale_cart.get(user_id, [])
    total = get_cart_total(user_id)

    lines = []
    if added_line:
        lines.append("Added ")
        lines.append(added_line)
        lines.append("")

    lines.append("Cart")
    if not cart:
        lines.append("(empty)")
    else:
        show = cart[:7]
        for idx, it in enumerate(show, start=1):
            lines.append(f"{idx}) {it['name']} × {it['qty']}")
        if len(cart) > 7:
            lines.append(f"+{len(cart) - 7} more items")

    lines.append("")
    lines.append(f"Total: {total}")
    return "\n".join(lines)

def main_menu_markup() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("New Sale-විකුනුම්", callback_data="SALE")],
        [InlineKeyboardButton("Purchase Product-භාණ්ඩ ගැනීම.", callback_data="PURCHASE_EXISTING")],
        [InlineKeyboardButton("Add New Product-නව භාණ්ඩ ගැනීම.", callback_data="PURCHASE_NEW")],
        [InlineKeyboardButton("Low Stock-හිග තොග", callback_data="LOW_STOCK")],
        [InlineKeyboardButton("Take Cash Out-මුදල් ඉවත් කිරීම", callback_data="CASH_OUT")],
        [InlineKeyboardButton("Daily Summary-දවසේ සාරාංශය", callback_data="SUMMARY_DAILY")],
        [InlineKeyboardButton("Weekly Summary-සතියේ සාරාංශය", callback_data="SUMMARY_WEEKLY")],
        [InlineKeyboardButton("Monthly Summary-මාසික සරාංශය", callback_data="SUMMARY_MONTHLY")],
    ]
    return InlineKeyboardMarkup(keyboard)

def cancel_only_markup(tag: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data=f"{tag}_CANCEL")]])

def back_cancel_markup(back_cb: str, cancel_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Back-ආපසු", callback_data=back_cb)],
        [InlineKeyboardButton("Cancel-අවලංගු කරන්න", callback_data=cancel_cb)],
    ])

def sale_action_buttons() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Add More-තව එක් කරන්න", callback_data="SALE_ADD_MORE")],
        [InlineKeyboardButton("Edit Cart-වෙනස් කරන්න", callback_data="SALE_EDIT")],
        [InlineKeyboardButton("Finish Sale-අවසන් කරන්න", callback_data="SALE_FINISH")],
        [InlineKeyboardButton("Cancel-අවලංගු කරන්න", callback_data="SALE_CANCEL")],
    ]
    return InlineKeyboardMarkup(keyboard)

def build_sale_menu_buttons(products):
    buttons = []
    for p in products:
        buttons.append([InlineKeyboardButton(f"{p['name']}", callback_data=f"SALE_PROD:{p['product_id']}")])
    buttons.append([InlineKeyboardButton("Search Product-සොයන්න", callback_data="SALE_SEARCH")])
    buttons.append([InlineKeyboardButton("Edit Cart-වෙනස් කරන්න", callback_data="SALE_EDIT")])
    buttons.append([InlineKeyboardButton("Finish Sale-අවසන් කරන්න", callback_data="SALE_FINISH")])
    buttons.append([InlineKeyboardButton("Cancel-අවලංගු කරන්න", callback_data="SALE_CANCEL")])
    return InlineKeyboardMarkup(buttons)

def build_sale_edit_menu(user_id: int) -> InlineKeyboardMarkup:
    cart = sale_cart.get(user_id, [])
    buttons = []

    if not cart:
        buttons.append([InlineKeyboardButton("Back-ආපසු", callback_data="SALE_BACK_TO_MENU")])
        buttons.append([InlineKeyboardButton("Cancel-අවලංගු කරන්න", callback_data="SALE_CANCEL")])
        return InlineKeyboardMarkup(buttons)

    for idx, it in enumerate(cart):
        title = f"{idx+1}) {it['name']} (qty {it['qty']})"
        buttons.append([InlineKeyboardButton(title, callback_data=f"SALE_EDIT_ITEM:{idx}")])

    buttons.append([InlineKeyboardButton("Back-ආපසු", callback_data="SALE_BACK_TO_MENU")])
    buttons.append([InlineKeyboardButton("Cancel-අවලංගු කරන්න", callback_data="SALE_CANCEL")])
    return InlineKeyboardMarkup(buttons)

def build_sale_item_actions(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Change Qty-ප්‍රමාණය වෙනස් කරන්න", callback_data=f"SALE_EDIT_QTY:{idx}")],
        [InlineKeyboardButton("Remove Item-අයිතම ඉවත් කරන්න", callback_data=f"SALE_REMOVE:{idx}")],
        [InlineKeyboardButton("Back-ආපසු", callback_data="SALE_EDIT")],
        [InlineKeyboardButton("Cancel-අවලංගු කරන්න", callback_data="SALE_CANCEL")],
    ])

def unit_buttons_markup() -> InlineKeyboardMarkup:
    unit_buttons = [
        [InlineKeyboardButton("kg", callback_data="NEWP_UNIT:kg"),
         InlineKeyboardButton("g", callback_data="NEWP_UNIT:g")],
        [InlineKeyboardButton("L", callback_data="NEWP_UNIT:L"),
         InlineKeyboardButton("ml", callback_data="NEWP_UNIT:ml")],
        [InlineKeyboardButton("Bottle", callback_data="NEWP_UNIT:Bottle"),
         InlineKeyboardButton("Packet", callback_data="NEWP_UNIT:Packet")],
        [InlineKeyboardButton("Piece", callback_data="NEWP_UNIT:Piece")],
        [InlineKeyboardButton("Back", callback_data="NEWP_BACK")],
        [InlineKeyboardButton("Cancel", callback_data="NEWP_CANCEL")],
    ]
    return InlineKeyboardMarkup(unit_buttons)

def _fetch_low_stock(shop_id: int):
    try:
        r = requests.get(f"{API_BASE}/stock/low", params={"shop_id": shop_id}, timeout=10)
    except Exception:
        return None, "Backend not reachable. Is FastAPI running?"

    if r.status_code != 200:
        return None, f"Error getting low stock: {r.text}"

    return (r.json() or []), None

def _format_low_stock_message(items, title: str = "⚠ Low Stock"):
    if not items:
        return "No low stock items -අඩු තොග නැත."

    lines = [title]
    for it in items[:15]:
        lines.append(f"- {it['name']} : {it['stock_qty']} (alert {it['alert_qty']})")
    if len(items) > 15:
        lines.append(f"+{len(items) - 15} more")
    return "\n".join(lines)

async def notify_new_low_stock(context: ContextTypes.DEFAULT_TYPE, chat_id: int, shop_id: int):
    items, err = _fetch_low_stock(shop_id)
    if err:
        return

    current_low = set(int(it["product_id"]) for it in items if "product_id" in it)
    prev_low = last_low_set.get(shop_id, set())
    newly_low = current_low - prev_low

    last_low_set[shop_id] = current_low

    if not newly_low:
        return

    show_items = [it for it in items if int(it["product_id"]) in newly_low]
    msg = _format_low_stock_message(show_items, title="⚠ Low Stock (New)")
    msg += "\n\nPress Low Stock in menu to view all current low stock items.\nඅවම තොග බලාගැනීමට මෙනුව භාවිතා කරන්න"
    await context.bot.send_message(chat_id=chat_id, text=msg)

def _get_shop_for_telegram_user(telegram_user_id: str):
    try:
        r = requests.get(f"{API_BASE}/telegram/shop", params={"telegram_user_id": telegram_user_id}, timeout=10)
    except Exception:
        return None, "Backend not reachable. Is FastAPI running?"

    if r.status_code != 200:
        return None, f"Error: {r.text}"

    data = r.json() or {}
    if not data.get("linked"):
        return None, None

    return int(data["shop_id"]), None

def _consume_link_token(link_token: str, telegram_user_id: str, chat_id: int):
    try:
        r = requests.post(
            f"{API_BASE}/telegram/consume-link-token",
            json={
                "link_token": link_token,
                "telegram_user_id": str(telegram_user_id),
                "chat_id": str(chat_id),
            },
            timeout=10,
        )
    except Exception:
        return None, "Backend not reachable. Is FastAPI running?"

    if r.status_code != 200:
        return None, r.text

    data = r.json() or {}
    if not data.get("ok"):
        return None, "Link failed"

    return int(data["shop_id"]), None


# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    args = context.args or []
    if args:
        link_token = args[0].strip()
        shop_id, err = _consume_link_token(link_token, str(user_id), chat_id)
        if err or not shop_id:
            await update.message.reply_text(
                "I could not link your Telegram to the website account.\n"
                "Please try the website button again.\nසම්බන්ද කිරීමට නොහැකී, නැවත බටනය ඔබා උත්සහ කරන්න"
            )
            return

        user_shop[user_id] = shop_id
        await update.message.reply_text(
            f" Connected to your shop (shop_id={shop_id})\n\nTrade Mate Menu\nChoose an option:\nTrade Mate මෙනුව, අවශ්‍යතාවය තෝරන්න.",
            reply_markup=main_menu_markup(),
        )
        return

    if user_id not in user_shop:
        shop_id, err = _get_shop_for_telegram_user(str(user_id))
        if err:
            await update.message.reply_text(err)
            return
        if not shop_id:
            await update.message.reply_text(
                "Your Telegram is not connected to a shop yet.\n\n"
                "Go to the Trade Mate website dashboard and click “ Telegram Bot ”.\n"
                "Then come back here and press /start again.\nබොට් සම්බන්ද වී නැත, නැවත වෙබ් පිටුවට ගොස් බටනය මගින් උත්සහ කරන්න"
            )
            return
        user_shop[user_id] = shop_id

    await update.message.reply_text(
        "Trade Mate Menu\nChoose an option:\nTrade Mate මෙනුව, අවශ්‍යතාවය තෝරන්න.",
        reply_markup=main_menu_markup(),
    )


# ---------- BUTTON HANDLER ----------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data

    shop_id = user_shop.get(user_id)
    if not shop_id:
        await query.message.reply_text(
            "Your Telegram is not connected to a shop yet.\n\n"
            "Go to the Trade Mate website dashboard and click “Your Telegram Bot Shop”.\n"
            "Then come back here and press /start again.\nබොට් සම්බනද වී නැත, නැවත වෙබ් පිටුවට ගොස් බටනය මගින් උත්සහ කරන්න"
        )
        return

    if data == "LOW_STOCK":
        items, err = _fetch_low_stock(shop_id)
        if err:
            await query.message.reply_text(err, reply_markup=main_menu_markup())
            return

        msg = _format_low_stock_message(items, title="⚠ Low Stock (Current)")
        await query.message.reply_text(msg, reply_markup=main_menu_markup())
        return

    # =========================
    # CASH OUT FLOW
    # =========================
    if data == "CASH_OUT":
        clear_sale(user_id)
        clear_sale_cart(user_id)
        clear_purchase(user_id)
        clear_new_product(user_id)
        clear_cashout(user_id)

        cashout_state[user_id] = "WAIT_REASON"
        await query.message.reply_text(
            "Take Cash Out\nEnter reason or type 0 to skip:\nමුදල් ගැනීමට හේතුව:",
            reply_markup=back_cancel_markup("CASHOUT_BACK_MENU", "CASHOUT_CANCEL"),
        )
        return

    if data == "CASHOUT_BACK_MENU":
        clear_cashout(user_id)
        await query.message.reply_text("Back to menu:", reply_markup=main_menu_markup())
        return

    if data == "CASHOUT_BACK_REASON":
        cashout_state[user_id] = "WAIT_REASON"
        await query.message.reply_text(
            "Enter reason or type 0 to skip:\nමුදල් ගැනීමට හේතුව:",
            reply_markup=back_cancel_markup("CASHOUT_BACK_MENU", "CASHOUT_CANCEL"),
        )
        return

    if data == "CASHOUT_CANCEL":
        clear_cashout(user_id)
        await query.message.reply_text("Cash out cancelled", reply_markup=main_menu_markup())
        return

    if data in ("SUMMARY_DAILY", "SUMMARY_WEEKLY", "SUMMARY_MONTHLY"):
        period = "daily"
        if data == "SUMMARY_WEEKLY":
            period = "weekly"
        elif data == "SUMMARY_MONTHLY":
            period = "monthly"

        try:
            r = requests.get(
                f"{API_BASE}/reports/summary",
                params={"shop_id": shop_id, "period": period},
                timeout=10
            )
        except Exception:
            await query.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await query.message.reply_text(f"Error getting summary: {r.text}")
            return

        s = r.json()
        await query.message.reply_text(
            f"{period.title()} Summary\n"
            f"Sales: {s['sales_total']}\n"
            f"Purchases: {s['purchases_total']}\n"
            f"Cash Out: {s['cash_out_total']}\n"
            f"Profit: {s['profit']}\n"
            f"Net Cash: {s['net_cash']}\n"
            f"From: {s['start_date']} To: {s['end_date']}",
            reply_markup=main_menu_markup(),
        )
        return

    # =========================
    # SALE FLOW
    # =========================
    if data == "SALE":
        clear_purchase(user_id)
        clear_new_product(user_id)
        clear_cashout(user_id)
        clear_sale(user_id)
        ensure_cart(user_id)

        try:
            r = requests.get(f"{API_BASE}/products/top", params={"shop_id": shop_id, "limit": 10}, timeout=10)
        except Exception:
            await query.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await query.message.reply_text(f"Error getting products: {r.text}")
            return

        products = r.json() or []
        if not products:
            await query.message.reply_text("No products found for this shop.\nමේ භාණ්ඩය මෙම වෙළදසල තුල සොයා ගැනීමට නොහැකි.")
            return

        await query.message.reply_text("New Sale\nSelect a product:\nභාණ්ඩය තෝරන්න", reply_markup=build_sale_menu_buttons(products))
        return

    if data == "SALE_SEARCH":
        sale_state[user_id] = "WAIT_SEARCH"
        await query.message.reply_text(
            "Type product name to search:\nසෙවීමට භාණ්ඩයේ නම ඉදිරිපත් කරන්න.",
            reply_markup=back_cancel_markup("SALE_BACK_TO_MENU", "SALE_CANCEL"),
        )
        return

    if data == "SALE_ADD_MORE" or data == "SALE_BACK_TO_MENU":
        clear_sale(user_id)
        ensure_cart(user_id)

        try:
            r = requests.get(f"{API_BASE}/products/top", params={"shop_id": shop_id, "limit": 10}, timeout=10)
        except Exception:
            await query.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await query.message.reply_text(f"Error getting products: {r.text}")
            return

        products = r.json() or []
        if not products:
            await query.message.reply_text("No products found for this shop.\nමේ භාණ්ඩය මෙම වෙළදසල තුල සොයා ගැනීමට නොහැකි.")
            return

        await query.message.reply_text("Select next product:\nඊලග භාණ්ඩය තෝරන්න", reply_markup=build_sale_menu_buttons(products))
        return

    if data == "SALE_CANCEL":
        clear_sale(user_id)
        clear_sale_cart(user_id)
        await query.message.reply_text("Sale cancelled ", reply_markup=main_menu_markup())
        return

    if data == "SALE_FINISH":
        cart = sale_cart.get(user_id, [])
        if not cart:
            await query.message.reply_text("Cart is empty. Add products first(Press above menu).\nසෙවීමට භාණ්ඩයේ නම ඉදිරිපත් කරන්න.(උඩ මෙනුව භාවිතා කරන්න)")
            return

        payload = {
            "shop_id": shop_id,
            "items": [{"product_id": int(it["product_id"]), "qty": float(it["qty"]), "unit_price": float(it["unit_price"])} for it in cart],
        }

        try:
            r = requests.post(f"{API_BASE}/sales", json=payload, timeout=10)
        except Exception:
            await query.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await query.message.reply_text(f"Finish sale failed: {r.text}")
            return

        total = r.json().get("total_amount", "OK")
        item_count = len(cart)

        clear_sale(user_id)
        clear_sale_cart(user_id)

        await query.message.reply_text(
            f"Sale recorded \nItems: {item_count}\nTotal: {total}",
            reply_markup=main_menu_markup(),
        )

        await notify_new_low_stock(context, chat_id, shop_id)
        return

    if data == "SALE_EDIT":
        ensure_cart(user_id)
        await query.message.reply_text("Edit Cart\nChoose an item:\nකූඩයේ භාණ්ඩ වෙනස් කරන්න,භාණ්ඩය තෝරන්න", reply_markup=build_sale_edit_menu(user_id))
        return

    if data.startswith("SALE_EDIT_ITEM:"):
        idx = int(data.split(":")[1])
        cart = sale_cart.get(user_id, [])
        if idx < 0 or idx >= len(cart):
            await query.message.reply_text("Invalid item.\nවැරදී")
            return
        it = cart[idx]
        await query.message.reply_text(
            f"Item: {it['name']}\nCurrent qty: {it['qty']}",
            reply_markup=build_sale_item_actions(idx),
        )
        return

    if data.startswith("SALE_REMOVE:"):
        idx = int(data.split(":")[1])
        cart = sale_cart.get(user_id, [])
        if idx < 0 or idx >= len(cart):
            await query.message.reply_text("Invalid item.\nවැරදී")
            return

        removed = cart.pop(idx)
        msg = format_cart_message(user_id, added_line=f"Removed \n- {removed['name']}")
        await query.message.reply_text(msg, reply_markup=sale_action_buttons())
        return

    if data.startswith("SALE_EDIT_QTY:"):
        idx = int(data.split(":")[1])
        cart = sale_cart.get(user_id, [])
        if idx < 0 or idx >= len(cart):
            await query.message.reply_text("Invalid item.\nවැරදී")
            return

        sale_state[user_id] = "WAIT_EDIT_QTY"
        sale_edit_index[user_id] = idx
        it = cart[idx]

        await query.message.reply_text(
            f"Change Qty\nItem: ප්‍රමාණය වෙනස් කරන්න\n{it['name']}\nEnter new quantity (example: 1 or 0.5):\nනව ප්‍රමාණ එකතු කරන්න",
            reply_markup=back_cancel_markup("SALE_EDIT", "SALE_CANCEL"),
        )
        return

    if data.startswith("SALE_PROD:"):
        product_id = int(data.split(":")[1])

        chosen = None
        for p in sale_search_results.get(user_id, []):
            if int(p.get("product_id", -1)) == product_id:
                chosen = p
                break

        if not chosen:
            try:
                r = requests.get(f"{API_BASE}/products/top", params={"shop_id": shop_id, "limit": 30}, timeout=10)
            except Exception:
                await query.message.reply_text("Backend not reachable. Is FastAPI running?")
                return

            if r.status_code == 200:
                for p in (r.json() or []):
                    if int(p.get("product_id", -1)) == product_id:
                        chosen = p
                        break

        if not chosen:
            await query.message.reply_text("Product not found. Tap New Sale again.\nභාණ්ඩය සොයා ගත නොහැක. නැවත උත්සාහ කරන්න.")
            return

        if chosen.get("sell_price") is None:
            await query.message.reply_text("This product has no sell price set. Add it first.\nවිකුනුම් මිලක් යොදා නැත. එය එක් කරන්න.")
            return

        sale_selected[user_id] = chosen
        sale_state[user_id] = "WAIT_QTY"

        await query.message.reply_text(
            f"Selected: {chosen['name']}\nEnter quantity (example: 1 or 0.5):\nප්‍රමාණය ඇතුලත් කරන්න",
            reply_markup=back_cancel_markup("SALE_BACK_TO_MENU", "SALE_CANCEL"),
        )
        return

    # =========================
    # PURCHASE EXISTING FLOW.
    # =========================
    if data == "PURCHASE_EXISTING":
        clear_sale(user_id)
        clear_sale_cart(user_id)
        clear_new_product(user_id)
        clear_cashout(user_id)
        clear_purchase(user_id)

        purchase_state[user_id] = "WAIT_SEARCH"
        await query.message.reply_text(
            "Purchase Product\nType product name to search:\nසෙවීමට භාණ්ඩයේ නම ඇතුලත් කරන්න.",
            reply_markup=back_cancel_markup("PUR_BACK_MENU", "PUR_CANCEL"),
        )
        return

    if data == "PUR_BACK_MENU":
        clear_purchase(user_id)
        await query.message.reply_text("Back to menu:\nනැවත මෙනුවට", reply_markup=main_menu_markup())
        return

    if data == "PUR_CANCEL":
        clear_purchase(user_id)
        await query.message.reply_text("Purchase cancelled", reply_markup=main_menu_markup())
        return

    if data.startswith("PURCHASE_PROD:"):
        product_id = int(data.split(":")[1])

        chosen = None
        for p in purchase_search_results.get(user_id, []):
            if int(p.get("product_id", -1)) == product_id:
                chosen = p
                break

        if not chosen:
            await query.message.reply_text("Product not found. Tap Purchase Product again.\nභාණ්ඩය සොයාගත නොහැක.නැවත මිලදී ගැනීම ඇතුලත් කරන්න")
            return

        purchase_selected[user_id] = chosen
        purchase_state[user_id] = "WAIT_QTY"
        await query.message.reply_text(
            f"Selected: {chosen['name']}\nEnter quantity purchased (example: 1 or 0.5):\nමිලදී ගන්න ප්‍රමාණය ඇතුලත් කරන්න.",
            reply_markup=back_cancel_markup("PUR_BACK_TO_RESULTS", "PUR_CANCEL"),
        )
        return

    if data == "PUR_BACK_TO_RESULTS":
        purchase_state[user_id] = "WAIT_SEARCH"
        await query.message.reply_text(
            "Type product name to search again:\nනැවත සෙවීමට භාණ්ඩයේ නම ‍යොදන්න",
            reply_markup=back_cancel_markup("PUR_BACK_MENU", "PUR_CANCEL"),
        )
        return

    # =========================
    # ADD NEW PRODUCT FLOW
    # =========================
    if data == "PURCHASE_NEW":
        clear_sale(user_id)
        clear_sale_cart(user_id)
        clear_purchase(user_id)
        clear_cashout(user_id)
        clear_new_product(user_id)

        newp_state[user_id] = "WAIT_NEW_NAME"
        await query.message.reply_text(
            "Add New Product\nEnter product name:\nනව තොග මිලදී ගැනීමට භාණ්ඩයේ නම ඇතුලත් කරන්න:",
            reply_markup=cancel_only_markup("NEWP"),
        )
        return

    if data == "NEWP_CANCEL":
        clear_new_product(user_id)
        await query.message.reply_text("New product cancelled", reply_markup=main_menu_markup())
        return

    if data == "NEWP_BACK":
        step = newp_state.get(user_id)
        if step == "WAIT_NEW_UNIT":
            newp_state[user_id] = "WAIT_NEW_NAME"
            await query.message.reply_text("Enter product name:\nභාණ්ඩයේ නම ඇතුලත් කරන්න:", reply_markup=cancel_only_markup("NEWP"))
            return
        if step == "WAIT_NEW_QTY":
            newp_state[user_id] = "WAIT_NEW_UNIT"
            await query.message.reply_text("Select unit:\nඒකකය තෝරන්න:", reply_markup=unit_buttons_markup())
            return
        if step == "WAIT_NEW_COST":
            newp_state[user_id] = "WAIT_NEW_QTY"
            await query.message.reply_text(
                "Enter purchase quantity (example: 5 or 0.5):\nමිලදී ගන්න ප්‍රමාණය ඇතුලත් කරන්න.",
                reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
            )
            return
        if step == "WAIT_NEW_SELL":
            newp_state[user_id] = "WAIT_NEW_COST"
            await query.message.reply_text(
                "Enter cost price per unit (example: 180):\nභාණ්ඩයේ පිරිවැය ඇතුලත් කරන්න:",
                reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
            )
            return
        if step == "WAIT_NEW_ALERT":
            newp_state[user_id] = "WAIT_NEW_SELL"
            await query.message.reply_text(
                "Enter selling price per unit (example: 250):\nභාණ්ඩයේ විකුනුම් මිල ඇතුලත් කරන්න.:",
                reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
            )
            return

        clear_new_product(user_id)
        await query.message.reply_text("Back to menu:\nමෙනුවට යාම:", reply_markup=main_menu_markup())
        return

    if data.startswith("NEWP_UNIT:"):
        unit = data.split(":", 1)[1]
        newp_unit[user_id] = unit
        newp_state[user_id] = "WAIT_NEW_QTY"
        await query.message.reply_text(
            f"Unit: {unit}\nEnter purchase quantity (example: 5 or 0.5):\nමිලදී ගන්නා ඒකක ගනන ඇතුලත් කරන්න.",
            reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
        )
        return


# ---------- TEXT HANDLER ----------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    shop_id = user_shop.get(user_id)
    if not shop_id:
        return

    # CASH OUT
    if cashout_state.get(user_id) == "WAIT_REASON":
        if text == "0":
            cashout_reason[user_id] = None
        else:
            cashout_reason[user_id] = text

        cashout_state[user_id] = "WAIT_AMOUNT"
        await update.message.reply_text(
            "Enter cash out amount:\nඉවතට ගන්නා මුදල් ප්‍රමාණය.",
            reply_markup=back_cancel_markup("CASHOUT_BACK_REASON", "CASHOUT_CANCEL"),
        )
        return

    if cashout_state.get(user_id) == "WAIT_AMOUNT":
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid amount greater than 0.වලන්ගු ප්‍රමානයක් ලබා දෙන්න",
                reply_markup=back_cancel_markup("CASHOUT_BACK_REASON", "CASHOUT_CANCEL"),
            )
            return

        payload = {
            "shop_id": shop_id,
            "amount": float(amount),
            "note": cashout_reason.get(user_id),
        }

        try:
            r = requests.post(f"{API_BASE}/cash-out", json=payload, timeout=10)
        except Exception:
            await update.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await update.message.reply_text(f"Cash out failed: {r.text}")
            return

        note = cashout_reason.get(user_id)
        msg = f"Cash out recorded \nAmount: {amount}"
        if note:
            msg += f"\nReason: {note}"

        clear_cashout(user_id)
        await update.message.reply_text(msg, reply_markup=main_menu_markup())
        return

    # SALE SEARCH
    if sale_state.get(user_id) == "WAIT_SEARCH":
        try:
            r = requests.get(f"{API_BASE}/products/search", params={"shop_id": shop_id, "q": text, "limit": 10}, timeout=10)
        except Exception:
            await update.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await update.message.reply_text(f"Search failed: {r.text}")
            return

        products = r.json() or []
        if not products:
            await update.message.reply_text(
                "No matches. Try another name.\nගැලපීම් නැත.වෙනත් නමක් උත්සහ කරන්න",
                reply_markup=back_cancel_markup("SALE_BACK_TO_MENU", "SALE_CANCEL"),
            )
            return

        sale_search_results[user_id] = products
        buttons = [[InlineKeyboardButton(p["name"], callback_data=f"SALE_PROD:{p['product_id']}")] for p in products]
        buttons.append([InlineKeyboardButton("Back", callback_data="SALE_BACK_TO_MENU")])
        buttons.append([InlineKeyboardButton("Cancel", callback_data="SALE_CANCEL")])

        await update.message.reply_text("Select a product:\nභාණ්ඩය තෝරන්න", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if sale_state.get(user_id) == "WAIT_QTY":
        chosen = sale_selected.get(user_id)
        if not chosen:
            await update.message.reply_text("No product selected. Tap New Sale again.\nභාණ්ඩ තෝරා නැත.නව විකුනුමක් කරන්න")
            clear_sale(user_id)
            return

        try:
            qty = float(text)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid quantity (example: 1 or 0.5)\nවලන්ගු ප්‍රමාණයක් ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("SALE_BACK_TO_MENU", "SALE_CANCEL"),
            )
            return

        ensure_cart(user_id)
        cart = sale_cart[user_id]

        merged = False
        for it in cart:
            if int(it["product_id"]) == int(chosen["product_id"]):
                it["qty"] = float(it["qty"]) + qty
                merged = True
                break

        if not merged:
            cart.append({
                "product_id": int(chosen["product_id"]),
                "name": chosen["name"],
                "qty": qty,
                "unit_price": float(chosen["sell_price"]),
            })

        line_total = qty * float(chosen["sell_price"])
        added_line = f"- {chosen['name']} × {qty} = {line_total}"

        sale_selected.pop(user_id, None)
        sale_state.pop(user_id, None)

        msg = format_cart_message(user_id, added_line=added_line)
        await update.message.reply_text(msg, reply_markup=sale_action_buttons())
        return

    if sale_state.get(user_id) == "WAIT_EDIT_QTY":
        idx = sale_edit_index.get(user_id)
        cart = sale_cart.get(user_id, [])
        if idx is None or idx < 0 or idx >= len(cart):
            clear_sale(user_id)
            await update.message.reply_text("Edit state lost. Open Edit Cart again.\n උත්සහය අසාර්තකයි,නැවත කූඩය වෙනස් කිරීමට උත්සහ කරන්න.")
            return

        try:
            new_qty = float(text)
            if new_qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid quantity (example: 1 or 0.5)\nවලන්ගු ප්‍රමාණයක් ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("SALE_EDIT", "SALE_CANCEL"),
            )
            return

        cart[idx]["qty"] = new_qty
        clear_sale(user_id)

        msg = format_cart_message(user_id, added_line=f"Updated \n- {cart[idx]['name']} qty = {new_qty}")
        await update.message.reply_text(msg, reply_markup=sale_action_buttons())
        return

    # PURCHASE SEARCH
    if purchase_state.get(user_id) == "WAIT_SEARCH":
        try:
            r = requests.get(f"{API_BASE}/products/search", params={"shop_id": shop_id, "q": text, "limit": 10}, timeout=10)
        except Exception:
            await update.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await update.message.reply_text(f"Search failed: {r.text}")
            return

        products = r.json() or []
        if not products:
            await update.message.reply_text(
                "No matches. Try another name.\nභාණ්ඩය සොයාගත නොහැක.නැවත ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("PUR_BACK_MENU", "PUR_CANCEL"),
            )
            return

        purchase_search_results[user_id] = products
        buttons = [[InlineKeyboardButton(p["name"], callback_data=f"PURCHASE_PROD:{p['product_id']}")] for p in products]
        buttons.append([InlineKeyboardButton("Back", callback_data="PUR_BACK_MENU")])
        buttons.append([InlineKeyboardButton("Cancel", callback_data="PUR_CANCEL")])

        await update.message.reply_text("Select a product to purchase:\nමිලදී ගැනීමට භාණ්ඩයේ නම ඇතුලත් කරන්න", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if purchase_state.get(user_id) == "WAIT_QTY":
        try:
            qty = float(text)
            if qty <= 0:
                 raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid quantity (example: 1 or 0.5)\nවලන්ගු ප්‍රමාණයක් ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("PUR_BACK_TO_RESULTS", "PUR_CANCEL"),
        )
            return

        chosen = purchase_selected.get(user_id)
        if not chosen:
            await update.message.reply_text("Purchase state lost. Tap Purchase Product again.\nමිලදී ගැනීම නැවත උත්සහකරන්න")
            clear_purchase(user_id)
            return

        payload = {
            "shop_id": shop_id,
            "items": [{
            "product_id": int(chosen["product_id"]),
            "qty": float(qty),
            "unit_price": float(chosen["cost_price"]),
            "sell_price": float(chosen["sell_price"]),
            "alert_qty": float(chosen.get("alert_qty", 0)),
            }],
        }

        try:
            r = requests.post(f"{API_BASE}/purchases", json=payload, timeout=10)
        except Exception:
            await update.message.reply_text("Backend not reachable. Is FastAPI running?")
            return

        if r.status_code != 200:
            await update.message.reply_text(f"Purchase failed: {r.text}")
            return

        total = r.json().get("total_amount", "OK")
        await update.message.reply_text(
            f"Purchase recorded \nProduct: {chosen['name']}\nQty added: {qty}\nTotal cost: {total}",
            reply_markup=main_menu_markup(),
        )
        clear_purchase(user_id)
        await notify_new_low_stock(context, chat_id, shop_id)
        return


    # ADD NEW PRODUCT
    if newp_state.get(user_id) == "WAIT_NEW_NAME":
        if len(text) < 2:
            await update.message.reply_text("Enter a valid product name:\nවලන්ගු භාණ්ඩ නමක් යොදන්න", reply_markup=cancel_only_markup("NEWP"))
            return

        newp_name[user_id] = text
        newp_state[user_id] = "WAIT_NEW_UNIT"
        await update.message.reply_text("Select unit:", reply_markup=unit_buttons_markup())
        return

    if newp_state.get(user_id) == "WAIT_NEW_QTY":
        try:
            qty = float(text)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid quantity (example: 5 or 0.5)\nවලන්ගු ප්‍රමාණයක් ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
            )
            return

        newp_qty[user_id] = qty
        newp_state[user_id] = "WAIT_NEW_COST"
        await update.message.reply_text(
            "Enter cost price per unit (example: 180):\nඒකකයක පිරිවැය ඇතුලත් කරන්න",
            reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
        )
        return

    if newp_state.get(user_id) == "WAIT_NEW_COST":
        try:
            cost = float(text)
            if cost < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid cost price (example: 180)\nවලන්ගු පිරිවැයක් ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
            )
            return

        newp_cost[user_id] = cost
        newp_state[user_id] = "WAIT_NEW_SELL"
        await update.message.reply_text(
            "Enter selling price per unit (example: 250):\nඒකකයක විකුනුම් මිල ඇතුලත් කරන්න",
            reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
        )
        return

    if newp_state.get(user_id) == "WAIT_NEW_SELL":
        name = newp_name.get(user_id)
        unit = newp_unit.get(user_id)
        qty = newp_qty.get(user_id)
        cost = newp_cost.get(user_id)

        if not name or not unit or qty is None or cost is None:
            await update.message.reply_text("New product state lost. Tap Add New Product again.\nඋත්සහය අසාර්තකයි, නැවත භාණ්ඩ එක් කිරීමට උත්සහ කරන්න. ")
            clear_new_product(user_id)
            return

        try:
            sell = float(text)
            if sell < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid selling price (example: 250)\nවලන්ගු මිලක් ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
            )
            return

        newp_sell[user_id] = sell
        newp_state[user_id] = "WAIT_NEW_ALERT"
        await update.message.reply_text(
            "Enter stock alert quantity (example: 5). Use 0 to disable:\nඅවම තොග මට්ටමක් ඇතුලත් කරන්න",
            reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
        )
        return

    if newp_state.get(user_id) == "WAIT_NEW_ALERT":
        name = newp_name.get(user_id)
        unit = newp_unit.get(user_id)
        qty = newp_qty.get(user_id)
        cost = newp_cost.get(user_id)
        sell = newp_sell.get(user_id)

        if not name or not unit or qty is None or cost is None or sell is None:
            await update.message.reply_text("New product state lost. Tap Add New Product again.\nඋත්සහය අසාර්තකයි, නැවත භාණ්ඩ එක් කිරීමට උත්සහ කරන්න. ")
            clear_new_product(user_id)
            return

        try:
            alert = float(text)
            if alert < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Enter a valid alert quantity (example: 5). Use 0 to disable.\nවලන්ගු අවම තොග මට්ටමක් ඇතුලත් කරන්න",
                reply_markup=back_cancel_markup("NEWP_BACK", "NEWP_CANCEL"),
            )
            return

        try:
            pr = requests.post(
                f"{API_BASE}/products",
                json={
                    "shop_id": shop_id,
                    "name": name,
                    "unit": unit,
                    "sell_price": float(sell),
                    "cost_price": float(cost),
                    "stock_qty": 0,
                    "alert_qty": float(alert),
                },
                timeout=10,
            )
        except Exception:
            await update.message.reply_text("Backend not reachable. Is FastAPI running?")
            clear_new_product(user_id)
            return

        if pr.status_code != 200:
            await update.message.reply_text(f"Create product failed: {pr.text}")
            clear_new_product(user_id)
            return

        new_product_id = pr.json().get("id")
        if not new_product_id:
            await update.message.reply_text("Create product failed: missing id")
            clear_new_product(user_id)
            return

        try:
            r = requests.post(
                f"{API_BASE}/purchases",
                json={
                    "shop_id": shop_id,
                    "items": [{
                        "product_id": int(new_product_id),
                        "qty": float(qty),
                        "unit_price": float(cost),
                        "sell_price": float(sell),
                        "alert_qty": float(alert),
                    }],
                },
                timeout=10,
            )
        except Exception:
            await update.message.reply_text("Backend not reachable. Is FastAPI running?")
            clear_new_product(user_id)
            return

        if r.status_code != 200:
            await update.message.reply_text(f"Purchase failed: {r.text}")
            clear_new_product(user_id)
            return

        total = r.json().get("total_amount", "OK")
        await update.message.reply_text(
            f"Product added + Purchase recorded \nProduct: {name}\nUnit: {unit}\nQty: {qty}\nCost/unit: {cost}\nSell/unit: {sell}\nAlert qty: {alert}\nTotal cost: {total}",
            reply_markup=main_menu_markup(),
        )
        clear_new_product(user_id)
        await notify_new_low_stock(context, chat_id, shop_id)
        return


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logging.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()