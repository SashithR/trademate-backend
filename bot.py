import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

os.environ["HTTPX_FORCE_IPV4"] = "1"

# ---------- CONFIG ----------
BOT_TOKEN = "8222011135:AAElS8HWUCDUdGzESoRZDo06yzMOWS7811Q"
API_BASE = "http://127.0.0.1:8000"

# ---------- IN-MEMORY STATE ----------
user_shop = {}               # telegram_user_id -> shop_id

# Sales state
sale_state = {}              # user_id -> WAIT_SEARCH | WAIT_QTY
sale_selected = {}           # user_id -> product dict
sale_search_results = {}     # user_id -> last search results list

# Purchase state
purchase_state = {}          # user_id -> WAIT_SEARCH | WAIT_QTY | WAIT_COST
purchase_selected = {}       # user_id -> product dict
purchase_search_results = {} # user_id -> last purchase search results list
purchase_qty = {}            # user_id -> qty float


# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("New Sale", callback_data="SALE")],
        [InlineKeyboardButton("New Purchase", callback_data="PURCHASE")],
        [InlineKeyboardButton("Today Summary", callback_data="SUMMARY")],
    ]
    await update.message.reply_text(
        "Trade Mate Menu\nChoose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /link 1")
        return

    try:
        shop_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Shop id must be a number. Example: /link 1")
        return

    user_shop[update.effective_user.id] = shop_id
    await update.message.reply_text(f"Shop linked successfully (shop_id={shop_id})")


# ---------- BUTTON HANDLER ----------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data
    shop_id = user_shop.get(user_id)

    needs_link = (
        data in ("SALE", "PURCHASE", "SUMMARY", "SALE_SEARCH")
        or data.startswith("SALE_PROD:")
        or data.startswith("PURCHASE_PROD:")
    )
    if needs_link and not shop_id:
        await query.message.reply_text("Link your shop first: /link 1")
        return

    # ---------- NEW SALE ----------
    if data == "SALE":
        # Clear purchase flow if switching
        purchase_state.pop(user_id, None)
        purchase_selected.pop(user_id, None)
        purchase_search_results.pop(user_id, None)
        purchase_qty.pop(user_id, None)

        r = requests.get(
            f"{API_BASE}/products/top",
            params={"shop_id": shop_id, "limit": 10},
            timeout=10,
        )
        if r.status_code != 200:
            await query.message.reply_text(f"Error getting products: {r.text}")
            return

        products = r.json() or []
        if not products:
            await query.message.reply_text("No products found for this shop.")
            return

        buttons = []
        for p in products:
            buttons.append([
                InlineKeyboardButton(p["name"], callback_data=f"SALE_PROD:{p['product_id']}")
            ])
        buttons.append([InlineKeyboardButton("Search Product", callback_data="SALE_SEARCH")])

        await query.message.reply_text(
            "New Sale\nSelect a product:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # ---------- SALE: search ----------
    if data == "SALE_SEARCH":
        sale_state[user_id] = "WAIT_SEARCH"
        await query.message.reply_text("Type product name to search (example: soap, rice, noodles):")
        return

    # ---------- SALE: product selected ----------
    if data.startswith("SALE_PROD:"):
        product_id = int(data.split(":")[1])

        chosen = None
        for p in sale_search_results.get(user_id, []):
            if int(p.get("product_id", -1)) == product_id:
                chosen = p
                break

        if not chosen:
            r = requests.get(
                f"{API_BASE}/products/top",
                params={"shop_id": shop_id, "limit": 30},
                timeout=10,
            )
            if r.status_code == 200:
                for p in (r.json() or []):
                    if int(p.get("product_id", -1)) == product_id:
                        chosen = p
                        break

        if not chosen:
            await query.message.reply_text("Product not found. Tap New Sale again.")
            return

        if "sell_price" not in chosen or chosen["sell_price"] is None:
            await query.message.reply_text("This product has no sell price set. Add it in the website first.")
            return

        sale_selected[user_id] = chosen
        sale_state[user_id] = "WAIT_QTY"

        await query.message.reply_text(
            f"Selected: {chosen['name']}\nEnter quantity (example: 1 or 0.5):"
        )
        return

    # ---------- NEW PURCHASE ----------
    if data == "PURCHASE":
        # Clear sale flow if switching
        sale_state.pop(user_id, None)
        sale_selected.pop(user_id, None)
        sale_search_results.pop(user_id, None)

        purchase_state[user_id] = "WAIT_SEARCH"
        await query.message.reply_text(
            "New Purchase\nType product name to search (example: rice, soap, oil):"
        )
        return

    # ---------- PURCHASE: product selected ----------
    if data.startswith("PURCHASE_PROD:"):
        product_id = int(data.split(":")[1])

        chosen = None
        for p in purchase_search_results.get(user_id, []):
            if int(p.get("product_id", -1)) == product_id:
                chosen = p
                break

        if not chosen:
            await query.message.reply_text("Product not found. Tap New Purchase again.")
            return

        purchase_selected[user_id] = chosen
        purchase_state[user_id] = "WAIT_QTY"

        await query.message.reply_text(
            f"Selected: {chosen['name']}\nEnter quantity purchased (example: 1 or 0.5):"
        )
        return

    # ---------- SUMMARY ----------
    if data == "SUMMARY":
        r = requests.get(
            f"{API_BASE}/summary/today",
            params={"shop_id": shop_id},
            timeout=10,
        )
        if r.status_code != 200:
            await query.message.reply_text(f"Error getting summary: {r.text}")
            return

        s = r.json()
        await query.message.reply_text(
            f"Today Summary\n"
            f"Sales: {s['sales_total']}\n"
            f"Purchases: {s['purchases_total']}\n"
            f"Net Cash: {s['net_cash']}"
        )
        return


# ---------- TEXT HANDLER ----------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    shop_id = user_shop.get(user_id)
    if not shop_id:
        return

    # ----- SALES: Search -----
    if sale_state.get(user_id) == "WAIT_SEARCH":
        r = requests.get(
            f"{API_BASE}/products/search",
            params={"shop_id": shop_id, "q": text, "limit": 10},
            timeout=10,
        )
        if r.status_code != 200:
            await update.message.reply_text(f"Search failed: {r.text}")
            return

        products = r.json() or []
        if not products:
            await update.message.reply_text("No matches. Try another name.")
            return

        sale_search_results[user_id] = products

        buttons = []
        for p in products:
            buttons.append([
                InlineKeyboardButton(p["name"], callback_data=f"SALE_PROD:{p['product_id']}")
            ])

        await update.message.reply_text(
            "Select a product:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # ----- SALES: Quantity -----
    if sale_state.get(user_id) == "WAIT_QTY":
        chosen = sale_selected.get(user_id)
        if not chosen:
            await update.message.reply_text("No product selected. Tap New Sale again.")
            sale_state.pop(user_id, None)
            return

        try:
            qty = float(text)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Enter a valid quantity (example: 1 or 0.5)")
            return

        payload = {
            "shop_id": shop_id,
            "items": [
                {
                    "product_id": int(chosen["product_id"]),
                    "qty": qty,
                    "unit_price": float(chosen["sell_price"]),
                }
            ],
        }

        r = requests.post(f"{API_BASE}/sales", json=payload, timeout=10)
        if r.status_code != 200:
            await update.message.reply_text(f"Sale failed: {r.text}")
            return

        res = r.json()
        total = res.get("total_amount", "OK")

        await update.message.reply_text(
            f"Sale recorded ✅\n"
            f"Product: {chosen['name']}\n"
            f"Qty: {qty}\n"
            f"Total: {total}"
        )

        sale_state.pop(user_id, None)
        sale_selected.pop(user_id, None)
        sale_search_results.pop(user_id, None)
        return

    # ----- PURCHASE: Search -----
    if purchase_state.get(user_id) == "WAIT_SEARCH":
        r = requests.get(
            f"{API_BASE}/products/search",
            params={"shop_id": shop_id, "q": text, "limit": 10},
            timeout=10,
        )
        if r.status_code != 200:
            await update.message.reply_text(f"Search failed: {r.text}")
            return

        products = r.json() or []
        if not products:
            await update.message.reply_text("No matches. Try another name.")
            return

        purchase_search_results[user_id] = products

        buttons = []
        for p in products:
            buttons.append([
                InlineKeyboardButton(p["name"], callback_data=f"PURCHASE_PROD:{p['product_id']}")
            ])

        await update.message.reply_text(
            "Select a product to purchase:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    # ----- PURCHASE: Quantity -----
    if purchase_state.get(user_id) == "WAIT_QTY":
        chosen = purchase_selected.get(user_id)
        if not chosen:
            await update.message.reply_text("No product selected. Tap New Purchase again.")
            purchase_state.pop(user_id, None)
            return

        try:
            qty = float(text)
            if qty <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Enter a valid quantity (example: 1 or 0.5)")
            return

        purchase_qty[user_id] = qty
        purchase_state[user_id] = "WAIT_COST"

        await update.message.reply_text(
            f"Qty: {qty}\nNow enter cost price per unit (example: 180):"
        )
        return

    # ----- PURCHASE: Cost price + save -----
    if purchase_state.get(user_id) == "WAIT_COST":
        chosen = purchase_selected.get(user_id)
        qty = purchase_qty.get(user_id)

        if not chosen or qty is None:
            await update.message.reply_text("Purchase state lost. Tap New Purchase again.")
            purchase_state.pop(user_id, None)
            purchase_selected.pop(user_id, None)
            purchase_search_results.pop(user_id, None)
            purchase_qty.pop(user_id, None)
            return

        try:
            cost = float(text)
            if cost < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Enter a valid cost price (example: 180)")
            return

        payload = {
            "shop_id": shop_id,
            "items": [
                {
                    "product_id": int(chosen["product_id"]),
                    "qty": float(qty),
                    "unit_price": float(cost),
                }
            ],
        }

        r = requests.post(f"{API_BASE}/purchases", json=payload, timeout=10)
        if r.status_code != 200:
            await update.message.reply_text(f"Purchase failed: {r.text}")
            return

        res = r.json()
        total = res.get("total_amount", "OK")

        await update.message.reply_text(
            f"Purchase recorded ✅\n"
            f"Product: {chosen['name']}\n"
            f"Qty: {qty}\n"
            f"Cost price: {cost}\n"
            f"Total: {total}"
        )

        purchase_state.pop(user_id, None)
        purchase_selected.pop(user_id, None)
        purchase_search_results.pop(user_id, None)
        purchase_qty.pop(user_id, None)
        return


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("link", link))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
