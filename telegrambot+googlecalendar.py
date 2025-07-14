import logging
import os
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters
)
from telegram_calendar import DetailedTelegramCalendar, LSTEP

from google.oauth2 import service_account
from googleapiclient.discovery import build

# Настрой логирование
logging.basicConfig(level=logging.INFO)

# === НАСТРОЙ СВОИ ПАРАМЕТРЫ ===
TELEGRAM_TOKEN = 'ТВОЙ_TELEGRAM_BOT_TOKEN'
AUTHORIZED_USER_ID = 123456789  # твой Telegram ID
CALENDAR_ID = 'primary'  # или твой id календаря
CREDENTIALS_JSON = 'credentials.json'

# Авторизация Google
SCOPES = ['https://www.googleapis.com/auth/calendar']
creds = service_account.Credentials.from_service_account_file(
    CREDENTIALS_JSON, scopes=SCOPES
)
service = build('calendar', 'v3', credentials=creds)


# === Декоратор, чтобы только ты пользовался ===
def restricted(func):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != AUTHORIZED_USER_ID:
            await update.message.reply_text("⛔️ У тебя нет доступа.")
            return
        return await func(update, context)
    return wrapped


# === Команда /start ===
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["📅 Создать событие"],
        ["🗑 Удалить событие"],
        ["📂 Показать события"]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Привет! 📌 Я управляю твоим Google Календарём.\nЧто делаем?",
        reply_markup=reply_markup
    )


# === Создать событие (выбор даты) ===
@restricted
async def create_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    calendar, step = DetailedTelegramCalendar().build()
    await update.message.reply_text(
        f"📅 Выбери {LSTEP[step]}",
        reply_markup=calendar
    )


# === Обработка Inline-календаря ===
@restricted
async def calendar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    result, key, step = DetailedTelegramCalendar().process(query.data)
    if not result and key:
        await query.edit_message_text(
            f"📅 Выбери {LSTEP[step]}",
            reply_markup=key
        )
    elif result:
        context.user_data['selected_date'] = result
        await query.edit_message_text(
            f"📅 Дата выбрана: {result}\n⌚ Введи время начала (ЧЧ:ММ):"
        )


# === Обработка сообщений ===
@restricted
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    # Если ждём время
    if 'selected_date' in context.user_data and ':' in text:
        try:
            selected_date = context.user_data['selected_date']
            selected_time = datetime.strptime(text.strip(), "%H:%M").time()
            start_dt = datetime.combine(selected_date, selected_time)
            end_dt = start_dt + timedelta(hours=1)  # по умолчанию 1 час

            event = {
                'summary': 'Моё событие',
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Europe/Moscow'},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Europe/Moscow'},
            }
            service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

            await update.message.reply_text(
                f"✅ Событие создано: {start_dt.strftime('%Y-%m-%d %H:%M')}"
            )
            context.user_data.pop('selected_date')

        except ValueError:
            await update.message.reply_text("❌ Формат времени неверный. Попробуй ЧЧ:ММ.")
        return

    # Стандартные кнопки
    if text == "📅 Создать событие":
        await create_event(update, context)
    elif text == "🗑 Удалить событие":
        await delete_event(update, context)
    elif text == "📂 Показать события":
        await show_events(update, context)
    else:
        await update.message.reply_text("🤖 Я тебя не понял. Используй меню.")


# === Удалить события ===
@restricted
async def delete_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=datetime.utcnow().isoformat() + 'Z',
        maxResults=5,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    if not events:
        await update.message.reply_text("📭 Нет событий для удаления.")
        return

    msg = "🗑 Найдены события:\n"
    for i, event in enumerate(events, start=1):
        start = event['start'].get('dateTime', event['start'].get('date'))
        msg += f"{i}. {event['summary']} — {start}\n"

    msg += "\nВведи номер события для удаления:"
    context.user_data['events_to_delete'] = events
    await update.message.reply_text(msg)


# === Подтверждение удаления ===
@restricted
async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if 'events_to_delete' in context.user_data and text.isdigit():
        idx = int(text) - 1
        events = context.user_data['events_to_delete']
        if 0 <= idx < len(events):
            event_id = events[idx]['id']
            service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
            await update.message.reply_text("✅ Событие удалено.")
            context.user_data.pop('events_to_delete')
            return
        else:
            await update.message.reply_text("❌ Неверный номер.")
    elif 'selected_date' not in context.user_data:
        await update.message.reply_text("🤖 Я тебя не понял.")


# === Показать события ===
@restricted
async def show_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=datetime.utcnow().isoformat() + 'Z',
        maxResults=5,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    if not events:
        await update.message.reply_text("📭 Нет ближайших событий.")
        return

    msg = "📌 Ближайшие события:\n"
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        msg += f"- {event['summary']} — {start}\n"

    await update.message.reply_text(msg)


# === Запуск ===
if __name__ == '__main__':
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(calendar_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), confirm_delete))

    app.run_polling()
