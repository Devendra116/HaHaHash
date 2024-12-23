import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import sqlite3
import google.generativeai as genai
from instruction import system_instruction
from dotenv import load_dotenv
from colorama import Fore, Style
import os
from utils import verify_payment

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
CRYPTO_WALLET_ADDRESS = os.getenv("CRYPTO_WALLET_ADDRESS")

# Configure Google Gemini
genai.configure(api_key=API_KEY)
generation_config = {
  "temperature": 1.25,
  "top_p": 0.95,
  "top_k": 40,
  "max_output_tokens": 8192,
  "response_mime_type": "text/plain",
}

model = genai.GenerativeModel(
    model_name="gemini-2.0-flash-exp",
    system_instruction=system_instruction,
    generation_config=generation_config,
)

# SQLite Database Setup
conn = sqlite3.connect("user_data.db", check_same_thread=False)
cursor = conn.cursor()

# Logging function
def logger(level, message):
    levels = {
        "USER": Fore.GREEN,
        "BOT": Fore.CYAN,
        "DEBUG": Fore.YELLOW,
        "ERROR": Fore.RED
    }
    color = levels.get(level, Fore.WHITE)
    print(f"{color}[{level}] {message}{Style.RESET_ALL}")

# Table creation
def create_tables():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        telegram_id INTEGER UNIQUE,
        name TEXT,
        message_count INTEGER DEFAULT 0
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        role TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS wallets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        wallet_address TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        transaction_id TEXT UNIQUE,
        amount REAL,
        status TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    conn.commit()

create_tables()

# Helper functions
def execute_query(query, params=(), fetch=False):
    cursor.execute(query, params)
    if fetch:
        return cursor.fetchall()
    conn.commit()

def ensure_user(telegram_id, name):
    execute_query("INSERT OR IGNORE INTO users (telegram_id, name) VALUES (?, ?)", (telegram_id, name))

def get_user_id(telegram_id):
    result = execute_query("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,), fetch=True)
    return result[0][0] if result else None

def increment_message_count(user_id):
    execute_query("UPDATE users SET message_count = message_count + 1 WHERE id = ?", (user_id,))
    result = execute_query("SELECT message_count FROM users WHERE id = ?", (user_id,), fetch=True)
    return result[0][0] if result else 0

def store_message(user_id, message, role):
    execute_query("INSERT INTO messages (user_id, message, role) VALUES (?, ?, ?)", (user_id, message, role))

def get_user_history(user_id):
    return execute_query("SELECT message, role FROM messages WHERE user_id = ? ORDER BY timestamp", (user_id,), fetch=True)

def generate_response(history):
    try:
        full_prompt = "\n".join(f"{role.capitalize()}: {msg}" for msg, role in history)
        response = model.generate_content(full_prompt)
        result = json.loads(response.text.replace("```json", "").replace("```", "").strip())
        logger("DEBUG", f"Agent Plan: {json.dumps(result['plan'], indent=4)}")
        return result["message"]
    except Exception as e:
        logger("ERROR", f"Error in generate_response: {e}")
        return f"Error: {str(e)}"

# Telegram Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name)
    logger("USER", f"User {user.first_name} ({user.id}) started the bot.")
    await update.message.reply_text(f"Hey what would you like to be called and where are you from?")

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name)
    user_id = get_user_id(user.id)
    message_count = increment_message_count(user_id)

    if message_count > 20:
        result = execute_query("SELECT wallet_address FROM wallets WHERE user_id = ?", (user_id,), fetch=True)
        if not result:
            await update.message.reply_text("Provide your wallet address first. Use /wallet <wallet_address>.")
        else:
            await update.message.reply_text(
                f"Limit reached. Send 0.001 SOL to {CRYPTO_WALLET_ADDRESS} and verify using /paid."
            )
        return

    user_message = update.message.text
    logger("USER", f"User {user.first_name} ({user.id}): {user_message}")
    store_message(user_id, user_message, "user")
    history = get_user_history(user_id)
    bot_response = generate_response(history)
    logger("BOT", f"Bot: {bot_response}")
    store_message(user_id, bot_response, "bot")
    await update.message.reply_text(bot_response)

async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name)
    user_id = get_user_id(user.id)
    result = execute_query("SELECT wallet_address FROM wallets WHERE user_id = ?", (user_id,), fetch=True)

    if not result:
        await update.message.reply_text("Provide your wallet address first. Use /wallet <wallet_address>.")
        return

    wallet_address = result[0][0]
    txn_id = verify_payment(CRYPTO_WALLET_ADDRESS, wallet_address, 0.001)

    if txn_id:
        try:
            execute_query("""
            INSERT INTO payments (user_id, transaction_id, amount, status)
            VALUES (?, ?, ?, 'verified')""", (user_id, txn_id, 0.001))
            execute_query("UPDATE users SET message_count = 0 WHERE id = ?", (user_id,))
            logger("DEBUG", f"Payment verified for user {user.first_name} ({user.id}). Transaction ID: {txn_id}")
            await update.message.reply_text("Payment verified!")
        except Exception as e:
            logger("ERROR", f"Error in /paid: {e}")
            await update.message.reply_text("Payment already used!")
    else:
        logger("DEBUG", "Payment not found.")
        await update.message.reply_text("Payment not found.")

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, user.first_name)
    user_id = get_user_id(user.id)

    if not context.args:
        await update.message.reply_text(
            "Please provide your Solana wallet address in the following format:\n"
            "`/wallet <your_wallet_address>`", parse_mode="Markdown"
        )
        return

    wallet_address = context.args[0]

    if len(wallet_address) != 44 or not wallet_address.isalnum():
        await update.message.reply_text(
            "Invalid wallet address format. Please ensure it is a valid Solana wallet address."
        )
        return

    try:
        execute_query("""
            INSERT INTO wallets (user_id, wallet_address)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET wallet_address = excluded.wallet_address
        """, (user_id, wallet_address))
        logger("DEBUG", f"Wallet address saved for user {user.first_name} ({user.id}): {wallet_address}")
        await update.message.reply_text(
            f"Your wallet address has been saved successfully: `{wallet_address}`",
            parse_mode="Markdown"
        )
    except sqlite3.IntegrityError as e:
        logger("ERROR", f"Error saving wallet address: {e}")
        await update.message.reply_text(
            "There was an error saving your wallet address. Please try again."
        )

# Bot setup
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("paid", paid))
app.add_handler(CommandHandler("wallet", wallet))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

if __name__ == "__main__":
    logger("DEBUG", "Bot is starting...")
    app.run_polling()
