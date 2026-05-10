import telebot
import os
import time
import logging
import json
from datetime import datetime, timedelta
from telebot import types
from collections import defaultdict
import psycopg2
from psycopg2.extras import DictCursor, Json
import threading

# ====================== CONFIG ======================
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    TOKEN = "8493101678:AAFP8SkvoSux8nRs0Op6NuoCMOiX9oDkF5A"   # Remove after testing

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required!")

# ================== SETTINGS ==================
CHANNEL_ID = "-1001775169065"
CHANNEL_INVITE_LINK = "https://t.me/+NzZ2mbPo9_02MDk8"
ADMIN_ID = 8258407224
VIP_USERNAME = "Antonio_Gomez_01"

bot = telebot.TeleBot(TOKEN)

# ====================== LOGGING ======================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ====================== DATABASE ======================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor, connect_timeout=10)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            joined_channel BOOLEAN DEFAULT FALSE,
            invites INTEGER DEFAULT 0,
            valid_invites INTEGER DEFAULT 0,
            referred_by BIGINT,
            last_referral_date TIMESTAMP,
            access_granted_date TIMESTAMP
        );""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS today_free_games (
            id SERIAL PRIMARY KEY, media TEXT, media_type TEXT, text TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS free_games_archive (
            id SERIAL PRIMARY KEY, 
            day DATE UNIQUE, 
            posts JSONB, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS won_tickets (
            id SERIAL PRIMARY KEY,
            media TEXT,
            media_type TEXT,
            text TEXT,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );""")
        
        conn.commit()
        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.error(f"DB Init Error: {e}")
    finally:
        cur.close()
        conn.close()

def load_data():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users")
        users_data = {row['user_id']: dict(row) for row in cur.fetchall()}

        cur.execute("SELECT media, media_type, text FROM today_free_games ORDER BY id")
        today_free_games = [dict(row) for row in cur.fetchall()]

        cur.execute("SELECT day, posts FROM free_games_archive ORDER BY day DESC")
        free_games_posts = [(row['day'], row['posts']) for row in cur.fetchall()]

        cur.execute("""
            SELECT id, media, media_type, text 
            FROM won_tickets 
            WHERE expires_at > NOW() 
            ORDER BY created_at DESC
        """)
        won_tickets = [dict(row) for row in cur.fetchall()]

        logger.info(f"✅ Loaded {len(free_games_posts)} archived days and {len(today_free_games)} today's games")
        return users_data, today_free_games, free_games_posts, won_tickets
    finally:
        cur.close()
        conn.close()

def save_user(user_id, data):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO users (user_id, joined_channel, invites, valid_invites, referred_by, last_referral_date, access_granted_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                joined_channel = EXCLUDED.joined_channel,
                invites = EXCLUDED.invites,
                valid_invites = EXCLUDED.valid_invites,
                referred_by = EXCLUDED.referred_by,
                last_referral_date = EXCLUDED.last_referral_date,
                access_granted_date = EXCLUDED.access_granted_date
        """, (user_id, data.get("joined_channel"), data.get("invites", 0),
              data.get("valid_invites", 0), data.get("referred_by"),
              data.get("last_referral_date"), data.get("access_granted_date")))
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ====================== INITIALIZE ======================
init_db()
users_data, today_free_games, free_games_posts, won_tickets = load_data()
last_daily_reset = datetime.now().date()

bot_me = bot.get_me()
BOT_USERNAME = bot_me.username if bot_me else None

# ====================== HELPERS ======================
def get_user_referrals(user_id):
    if user_id in users_data:
        return users_data[user_id].get("valid_invites", 0)
    return 0

def is_member_of_channel(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# ====================== PER COMMAND ANTI-SPAM ======================
COMMAND_COOLDOWNS = {
    "today_games": 8,
    "previous_games": 12,
    "won_tickets": 8,
    "leaderboard": 15,
    "vip": 5,
    "check_channel": 5
}

last_command_time = defaultdict(lambda: defaultdict(lambda: datetime.min))

def anti_spam_per_command(user_id, command_key):
    now = datetime.now()
    cooldown = COMMAND_COOLDOWNS.get(command_key, 5)
    if (now - last_command_time[user_id][command_key]) < timedelta(seconds=cooldown):
        return False
    last_command_time[user_id][command_key] = now
    return True

def clean_expired_won_tickets():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM won_tickets WHERE expires_at <= NOW()")
        conn.commit()
    finally:
        cur.close()
        conn.close()

def daily_reset_check(manual=False):
    global last_daily_reset, today_free_games, free_games_posts

    now = datetime.now()
    today = now.date()

    if not manual and today <= last_daily_reset:
        return

    archived = False
    if today_free_games:
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM free_games_archive WHERE day = %s", (last_daily_reset,))
            cur.execute("INSERT INTO free_games_archive (day, posts) VALUES (%s, %s)", 
                       (last_daily_reset, Json(today_free_games)))
            conn.commit()
            free_games_posts.insert(0, (last_daily_reset, today_free_games[:]))
            archived = True
            
            msg = f"✅ Daily reset completed.\n📦 Archived {len(today_free_games)} free games for {last_daily_reset}"
            if manual:
                msg = "🔄 Manual archive triggered!\n" + msg
            try:
                bot.send_message(ADMIN_ID, msg)
            except:
                pass
        except Exception as e:
            logger.error(f"❌ Archive error: {e}")
            try:
                bot.send_message(ADMIN_ID, f"⚠️ Failed to archive:\n{str(e)[:250]}")
            except:
                pass
        finally:
            cur.close()
            conn.close()

    today_free_games.clear()
    last_daily_reset = today

# Background thread
def background_daily_checker():
    while True:
        try:
            daily_reset_check()
        except Exception as e:
            logger.error(f"Background reset error: {e}")
        time.sleep(300)

reset_thread = threading.Thread(target=background_daily_checker, daemon=True)
reset_thread.start()
logger.info("🕒 Background daily reset checker started")

def notify_all_users_about_new_game():
    if not today_free_games:
        return
    notified = 0
    skipped = 0
    for user_id in list(users_data.keys()):
        if user_id == ADMIN_ID:
            continue
        try:
            first_name = "there"
            try:
                chat = bot.get_chat(user_id)
                first_name = chat.first_name or "there"
            except:
                pass
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎮 Today's Free Games", callback_data="open_today_games"))
            bot.send_message(
                user_id,
                f"Hello 👋 {first_name},\n\nA Free games has just been posted.\nClick on **Today's Free Games** button to see it 🤝",
                parse_mode="Markdown",
                reply_markup=markup
            )
            notified += 1
            time.sleep(0.15)
        except Exception as e:
            skipped += 1
            logger.warning(f"Could not notify user {user_id}: {str(e)[:100]}")
    logger.info(f"📢 New game notification sent to {notified} users (skipped {skipped})")
    try:
        bot.send_message(
            ADMIN_ID,
            f"✅ New game posted successfully!\n📢 Notification sent to **{notified}** users\n⚠️ Skipped: {skipped}",
            parse_mode="Markdown"
        )
    except:
        pass

def get_persistent_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2, is_persistent=True)
    markup.add("🎮 Today's Free Games", "📜 Previous Free Games")
    markup.add("🏆 Referral Leaderboard", "✅ Won Tickets")
    markup.add("💎 VIP Service 💯")
    markup.add("🔄 Manual Archive")  # Admin only button
    return markup

# ====================== SEND FUNCTIONS WITH DELETE (Newest at bottom) ======================
def send_today_games_with_delete(chat_id):
    if not today_free_games:
        bot.send_message(chat_id, "No free games today yet.")
        return
    for i in range(len(today_free_games)-1, -1, -1):  # newest at bottom
        post = today_free_games[i]
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🗑 Delete This Post", callback_data=f"del_today_{i}"))
        try:
            if post.get("media_type") == "photo":
                bot.send_photo(chat_id, post["media"], caption=post.get("text"), reply_markup=markup)
            elif post.get("media_type") == "video":
                bot.send_video(chat_id, post["media"], caption=post.get("text"), reply_markup=markup)
            else:
                bot.send_message(chat_id, post.get("text"), reply_markup=markup)
        except:
            pass

def send_won_tickets_with_delete(chat_id):
    if not won_tickets:
        bot.send_message(chat_id, "No winning tickets yet.")
        return
    for i in range(len(won_tickets)-1, -1, -1):  # newest at bottom
        post = won_tickets[i]
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🗑 Delete This Ticket", callback_data=f"del_win_{i}"))
        try:
            if post.get("media_type") == "photo":
                bot.send_photo(chat_id, post["media"], caption=post.get("text", "Winning Ticket"), reply_markup=markup)
            elif post.get("media_type") == "video":
                bot.send_video(chat_id, post["media"], caption=post.get("text", "Winning Ticket"), reply_markup=markup)
            else:
                bot.send_message(chat_id, post.get("text", "Winning Ticket"), reply_markup=markup)
        except:
            pass

def send_previous_games_with_delete(chat_id):
    if not free_games_posts:
        bot.send_message(chat_id, "No previous games yet.")
        return
    bot.send_message(chat_id, f"📜 Previous Free Games ({len(free_games_posts)} days)")
    for day_idx, (day, posts) in enumerate(free_games_posts[:5]):
        bot.send_message(chat_id, f"📅 {day} ({len(posts)} posts)")
        for i in range(len(posts)-1, -1, -1):  # newest at bottom
            post = posts[i]
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🗑 Delete This Post", callback_data=f"del_arch_{day_idx}_{i}"))
            try:
                if post.get("media_type") == "photo":
                    bot.send_photo(chat_id, post["media"], caption=post.get("text"), reply_markup=markup)
                elif post.get("media_type") == "video":
                    bot.send_video(chat_id, post["media"], caption=post.get("text"), reply_markup=markup)
                else:
                    bot.send_message(chat_id, post.get("text"), reply_markup=markup)
            except:
                pass

# ====================== CHANNEL HANDLER ======================
@bot.chat_member_handler()
def handle_channel_update(update):
    try:
        if str(update.chat.id) != CHANNEL_ID:
            return
        user = update.new_chat_member.user
        user_id = user.id
        status = update.new_chat_member.status

        if user_id not in users_data:
            users_data[user_id] = {
                "joined_channel": False,
                "invites": 0,
                "valid_invites": 0,
                "referred_by": None,
                "last_referral_date": None,
                "access_granted_date": None
            }

        data = users_data[user_id]

        if status in ["member", "administrator", "creator"]:
            was_joined = data.get("joined_channel", False)
            data["joined_channel"] = True
            if not was_joined and data.get("referred_by"):
                referrer_id = data["referred_by"]
                if referrer_id in users_data:
                    ref_data = users_data[referrer_id]
                    ref_data["valid_invites"] = ref_data.get("valid_invites", 0) + 1
                    ref_data["last_referral_date"] = datetime.now()
                    save_user(referrer_id, ref_data)
                    bot.send_message(referrer_id, f"🎉 New valid referral! Total: {ref_data['valid_invites']}/5")
            save_user(user_id, data)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ I Have Joined", callback_data="check_channel"))
            bot.send_message(user_id, f"Hello 👋 {user.first_name}, you have been Approved!\n\nClick 👉 I have Joined ☝️", reply_markup=markup)

        elif status in ["left", "kicked"]:
            data["joined_channel"] = False
            if data.get("referred_by"):
                referrer_id = data["referred_by"]
                if referrer_id in users_data:
                    ref_data = users_data[referrer_id]
                    old_count = ref_data.get("valid_invites", 0)
                    new_count = max(0, old_count - 1)
                    ref_data["valid_invites"] = new_count
                    save_user(referrer_id, ref_data)
                    needed = max(5 - new_count, 0)
                    bot.send_message(
                        referrer_id,
                        f"❌ One of your referrals has left the channel.\n\n"
                        f"You now have **{new_count}/5** valid invites.\n"
                        f"You need **{needed}** more friend{'' if needed == 1 else 's'} for full access.",
                        parse_mode="Markdown"
                    )
            save_user(user_id, data)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Join Channel Again", url=CHANNEL_INVITE_LINK))
            bot.send_message(
                user_id, 
                "❌ You left the channel. Your access has been revoked.\n\n"
                "Please rejoin the channel to continue.",
                reply_markup=markup
            )
    except Exception as e:
        logger.error(f"Channel handler error: {e}")

# ====================== COMMANDS ======================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    daily_reset_check()
    access = check_access(user_id)

    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref"):
        try:
            referrer_id = int(args[1][3:])
            if referrer_id != user_id and referrer_id in users_data:
                if users_data[user_id].get("referred_by") is None:
                    users_data[user_id]["referred_by"] = referrer_id
                    save_user(user_id, users_data[user_id])
                    bot.send_message(referrer_id, f"📩 Someone used your referral link! They must join the channel for it to count.")
        except:
            pass

    if access == "channel":
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton("✅ Join Private Channel", url=CHANNEL_INVITE_LINK))
        markup.add(types.InlineKeyboardButton("🔄 I Have Joined", callback_data="check_channel"))
        bot.send_message(message.chat.id, "👋 Welcome!\nYou must join our private channel first.", reply_markup=markup)
    else:
        bot.send_message(message.chat.id, "✅ Welcome back! Use the menu below.", reply_markup=get_persistent_keyboard())

@bot.message_handler(commands=['post'], func=lambda m: m.from_user.id == ADMIN_ID)
def post_free_games(message):
    try:
        text = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
        media = None
        media_type = None
        if message.reply_to_message:
            replied = message.reply_to_message
            if replied.photo:
                media = replied.photo[-1].file_id
                media_type = "photo"
            elif replied.video:
                media = replied.video.file_id
                media_type = "video"
        elif message.photo:
            media = message.photo[-1].file_id
            media_type = "photo"
        elif message.video:
            media = message.video.file_id
            media_type = "video"

        if not media:
            bot.reply_to(message, "❌ **How to post:**\n1. Send a photo or video\n2. Reply with `/post Your caption`")
            return

        today_free_games.append({"media": media, "media_type": media_type, "text": text})
        bot.reply_to(message, f"✅ Added **{media_type}** to Today's Free Games! Total: {len(today_free_games)}")
        notify_all_users_about_new_game()
    except Exception as e:
        logger.error(f"Post error: {e}")
        bot.reply_to(message, "❌ Something went wrong. Try again.")

@bot.message_handler(commands=['win'], func=lambda m: m.from_user.id == ADMIN_ID)
def post_won_ticket(message):
    try:
        text = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else "Winning Ticket"
        media = None
        media_type = None
        if message.reply_to_message:
            replied = message.reply_to_message
            if replied.photo:
                media = replied.photo[-1].file_id
                media_type = "photo"
            elif replied.video:
                media = replied.video.file_id
                media_type = "video"
        elif message.photo:
            media = message.photo[-1].file_id
            media_type = "photo"
        elif message.video:
            media = message.video.file_id
            media_type = "video"

        if not media:
            bot.reply_to(message, "❌ **How to post winning ticket:**\n1. Send a photo or video\n2. Reply with `/win Your caption`")
            return

        expires_at = datetime.now() + timedelta(days=30)
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO won_tickets (media, media_type, text, expires_at) VALUES (%s, %s, %s, %s)",
            (media, media_type, text, expires_at)
        )
        conn.commit()
        cur.close()
        conn.close()

        won_tickets.insert(0, {"media": media, "media_type": media_type, "text": text})
        bot.reply_to(message, f"✅ Winning ticket added successfully! Total active: {len(won_tickets)}")
    except Exception as e:
        logger.error(f"Win command error: {e}")
        bot.reply_to(message, "❌ Something went wrong while posting the winning ticket.")

# ====================== KEYBOARD HANDLER ======================
@bot.message_handler(content_types=['text'])
def handle_keyboard(message):
    user_id = message.from_user.id
    text = message.text.strip()
    daily_reset_check()
    clean_expired_won_tickets()

    if text == "🎮 Today's Free Games":
        if not anti_spam_per_command(user_id, "today_games"):
            bot.send_message(message.chat.id, "⏳ Please wait a few seconds before using this again.")
            return
        if user_id == ADMIN_ID:
            send_today_games_with_delete(message.chat.id)
        elif check_access(user_id) == "full":
            if today_free_games:
                bot.send_message(message.chat.id, f"🎮 **Today's Free Games** ({len(today_free_games)})", parse_mode="Markdown")
                for post in reversed(today_free_games):
                    try:
                        if post.get("media_type") == "photo":
                            bot.send_photo(message.chat.id, post["media"], caption=post.get("text"))
                        elif post.get("media_type") == "video":
                            bot.send_video(message.chat.id, post["media"], caption=post.get("text"))
                        time.sleep(0.5)
                    except:
                        pass
            else:
                bot.send_message(message.chat.id, "No free games today yet.")
        else:
            current_referrals = get_user_referrals(user_id)
            needed = max(5 - current_referrals, 1)
            message_text = (
                "❌ You don't have full access yet.\n\n"
                "You need **5 friends** to get access to Free Games.\n\n"
                f"👥 Friends referred so far: **{current_referrals}**\n"
                f"🔜 You still need: **{needed}** more friend{'' if needed == 1 else 's'}"
            )
            markup = types.InlineKeyboardMarkup(row_width=1)
            share_button = types.InlineKeyboardButton(
                text="🔗 Share with Friends",
                url=f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start=ref{user_id}&text=Join%20me%20and%20unlock%20Free%20Games%20together!%20%F0%9F%8E%AE"
            )
            markup.add(share_button)
            bot.send_message(message.chat.id, message_text, parse_mode="Markdown", reply_markup=markup)

    elif text == "✅ Won Tickets":
        if not anti_spam_per_command(user_id, "won_tickets"):
            bot.send_message(message.chat.id, "⏳ Please wait a few seconds before using this again.")
            return
        if user_id == ADMIN_ID:
            send_won_tickets_with_delete(message.chat.id)
        else:
            if won_tickets:
                bot.send_message(message.chat.id, f"✅ **Won Tickets** ({len(won_tickets)} active)", parse_mode="Markdown")
                for post in reversed(won_tickets):
                    try:
                        if post.get("media_type") == "photo":
                            bot.send_photo(message.chat.id, post["media"], caption=post.get("text", "Winning Ticket"))
                        elif post.get("media_type") == "video":
                            bot.send_video(message.chat.id, post["media"], caption=post.get("text", "Winning Ticket"))
                        time.sleep(0.5)
                    except:
                        pass
            else:
                bot.send_message(message.chat.id, "No winning tickets yet.")

    elif text == "📜 Previous Free Games":
        if not anti_spam_per_command(user_id, "previous_games"):
            bot.send_message(message.chat.id, "⏳ Please wait a few seconds before using this again.")
            return
        if user_id == ADMIN_ID:
            send_previous_games_with_delete(message.chat.id)
        else:
            if free_games_posts:
                bot.send_message(message.chat.id, f"📜 **Previous Free Games** ({len(free_games_posts)} days)", parse_mode="Markdown")
                for day, posts in free_games_posts[:10]:
                    bot.send_message(message.chat.id, f"📅 {day}")
                    for post in reversed(posts):
                        try:
                            if post.get("media_type") == "photo":
                                bot.send_photo(message.chat.id, post["media"], caption=post.get("text"))
                            elif post.get("media_type") == "video":
                                bot.send_video(message.chat.id, post["media"], caption=post.get("text"))
                            time.sleep(0.4)
                        except:
                            pass
            else:
                bot.send_message(message.chat.id, "No previous games yet.")

    elif text == "🏆 Referral Leaderboard":
        if not anti_spam_per_command(user_id, "leaderboard"):
            bot.send_message(message.chat.id, "⏳ Please wait a few seconds before using this again.")
            return
        sorted_users = sorted(users_data.items(), key=lambda x: x[1].get("valid_invites", 0), reverse=True)
        lb = "🏆 **Top Referrers**\n\n"
        for i, (uid, data) in enumerate(sorted_users[:15], 1):
            lb += f"{i}. User `{uid}` — **{data.get('valid_invites', 0)}** invites\n"
        bot.send_message(message.chat.id, lb, parse_mode="Markdown")

    elif text == "💎 VIP Service 💯":
        if not anti_spam_per_command(user_id, "vip"):
            bot.send_message(message.chat.id, "⏳ Please wait a few seconds before using this again.")
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{VIP_USERNAME}"))
        bot.send_message(message.chat.id, "💎 Want VIP Service?\nContact Admin for premium access.", reply_markup=markup)

    elif text == "🔄 Manual Archive" and user_id == ADMIN_ID:
        daily_reset_check(manual=True)
        bot.send_message(message.chat.id, "🔄 Manual archive executed successfully!")

# ====================== CALLBACKS ======================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data

    if data == "check_channel":
        if not anti_spam_per_command(user_id, "check_channel"):
            bot.answer_callback_query(call.id, "⏳ Please wait before trying again.", show_alert=True)
            return
        if is_member_of_channel(user_id):
            users_data[user_id]["joined_channel"] = True
            save_user(user_id, users_data[user_id])
            bot.send_message(call.message.chat.id, "✅ Access granted!", reply_markup=get_persistent_keyboard())
        else:
            bot.answer_callback_query(call.id, "❌ You have not joined yet.", show_alert=True)

    elif data == "open_today_games":
        bot.answer_callback_query(call.id)
        temp_message = types.Message(
            message_id=0,
            from_user=call.from_user,
            chat=call.message.chat,
            date=datetime.now(),
            content_type='text',
            options={},
            json_string=""
        )
        temp_message.text = "🎮 Today's Free Games"
        handle_keyboard(temp_message)

    # Delete handlers
    elif data.startswith("del_today_") and user_id == ADMIN_ID:
        try:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(today_free_games):
                today_free_games.pop(idx)
                bot.answer_callback_query(call.id, "🗑 Post deleted from Today's Games", show_alert=True)
                send_today_games_with_delete(call.message.chat.id)
        except:
            bot.answer_callback_query(call.id, "Error deleting post", show_alert=True)

    elif data.startswith("del_win_") and user_id == ADMIN_ID:
        try:
            idx = int(data.split("_")[-1])
            if 0 <= idx < len(won_tickets):
                ticket = won_tickets.pop(idx)
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("DELETE FROM won_tickets WHERE media = %s AND media_type = %s AND text = %s",
                           (ticket.get("media"), ticket.get("media_type"), ticket.get("text")))
                conn.commit()
                cur.close()
                conn.close()
                bot.answer_callback_query(call.id, "🗑 Won ticket deleted", show_alert=True)
                send_won_tickets_with_delete(call.message.chat.id)
        except:
            bot.answer_callback_query(call.id, "Error deleting ticket", show_alert=True)

    elif data.startswith("del_arch_") and user_id == ADMIN_ID:
        try:
            _, day_idx, post_idx = data.split("_")
            day_idx = int(day_idx)
            post_idx = int(post_idx)
            if 0 <= day_idx < len(free_games_posts):
                day, posts = free_games_posts[day_idx]
                if 0 <= post_idx < len(posts):
                    posts.pop(post_idx)
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("UPDATE free_games_archive SET posts = %s WHERE day = %s", (Json(posts), day))
                    conn.commit()
                    cur.close()
                    conn.close()
                    bot.answer_callback_query(call.id, f"🗑 Deleted from archive", show_alert=True)
                    send_previous_games_with_delete(call.message.chat.id)
        except:
            bot.answer_callback_query(call.id, "Error deleting from archive", show_alert=True)

# ====================== ACCESS CHECK ======================
def check_access(user_id):
    if user_id == ADMIN_ID:
        return "full"
    if user_id not in users_data:
        users_data[user_id] = {
            "joined_channel": False, 
            "invites": 0, 
            "valid_invites": 0,
            "referred_by": None, 
            "last_referral_date": None, 
            "access_granted_date": None
        }
        save_user(user_id, users_data[user_id])

    data = users_data[user_id]
    if not data.get("joined_channel"):
        return "channel"

    if data.get("access_granted_date"):
        if (datetime.now() - data["access_granted_date"]) <= timedelta(days=7):
            return "full"
        else:
            data["valid_invites"] = 0
            data["access_granted_date"] = None
            save_user(user_id, data)

    if data.get("valid_invites", 0) >= 5:
        data["access_granted_date"] = datetime.now()
        save_user(user_id, data)
        return "full"
    return "invites"

# ====================== BOT START ======================
if __name__ == "__main__":
    logger.info("🤖 Bot starting with full delete support + newest at bottom...")
    try:
        bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook cleared successfully")
        
        bot.infinity_polling(none_stop=True, 
                            allowed_updates=['message', 'callback_query', 'chat_member'],
                            timeout=30,
                            long_polling_timeout=30)
    except Exception as e:
        logger.error(f"Critical error: {e}")
