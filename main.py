import paramiko
import pickle
import atexit
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
    MessageHandler,
)

servers = {}
thresholds = {}


def save_data():
    """
    Сохраняет данные о подключении к серверам и пороги в файлы.
    Обеспечивает сохранение данных серверов и порогов
    между перезапусками приложения, сериализуя их в файлы.
    """
    servers_to_save = {}
    for chat_id, server_data in servers.items():
        servers_to_save[chat_id] = {}
        for ip, ssh_data in server_data.items():
            username, password, ssh_client = ssh_data
            servers_to_save[chat_id][ip] = (username, password)

    with open("servers_data.pkl", "wb") as f:
        pickle.dump(servers_to_save, f)

    with open("thresholds_data.pkl", "wb") as f:
        pickle.dump(thresholds, f)


def load_data():
    """
    Загружает данные о подключении к серверам и пороги из файлов.

    Восстанавливает ранее сохраненные данные, включая учетные данные серверов
    и значения порогов, для использования в приложении.
    """
    global servers, thresholds
    try:
        with open("servers_data.pkl", "rb") as f:
            servers_data = pickle.load(f)
            for chat_id, server_data in servers_data.items():
                servers[chat_id] = {}
                for ip, (username, password) in server_data.items():
                    ssh_client = paramiko.SSHClient()
                    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    try:
                        ssh_client.connect(ip, username=username, password=password)
                        servers[chat_id][ip] = (username, password, ssh_client)
                    except Exception as e:
                        print(f"Не удалось подключиться к серверу {ip}: {e}")
            print(f"Загруженные серверы: {servers}")
    except FileNotFoundError:
        servers = {}

    try:
        with open("thresholds_data.pkl", "rb") as f:
            thresholds = pickle.load(f)
        print(f"Загруженные пороги: {thresholds}")
    except FileNotFoundError:
        thresholds = {}


def execute_ssh_command(ssh_client, command):
    """
    Выполняет SSH-команду на указанном сервере.

    Args:
        ssh_client (paramiko.SSHClient): SSH-клиент, подключенный к серверу.
        command (str): Команда для выполнения на сервере.

    Returns:
        str: Результат выполнения команды.
    """
    stdin, stdout, stderr = ssh_client.exec_command(command)
    return stdout.read().decode().strip()


def get_server_load(ssh_client):
    """
    Получает данные о нагрузке CPU и RAM на сервере.

    Args:
        ssh_client (paramiko.SSHClient): SSH-клиент, подключенный к серверу.

    Returns:
        tuple: Процент нагрузки процессора (float) и ОЗУ (float) в процентах.
    """
    cpu_output = execute_ssh_command(ssh_client, "top -bn1 | grep 'Cpu(s)'")
    ram_output = execute_ssh_command(
        ssh_client, "free | awk '/Mem:/ {print $3/$2 * 100.0}'"
    )

    # Извлекаем процент использования CPU
    cpu_usage = float(cpu_output.split()[1].replace("%", "")) if cpu_output else 0

    # Извлекаем процент использования RAM
    ram_usage = float(ram_output) if ram_output else 0

    return cpu_usage, ram_usage


async def monitor_load(context):
    """
    Периодически проверяет нагрузку на сервера и отправляет предупреждения, если пороги превышены.

    Args:
        context (telegram.ext.CallbackContext): Контекст, передаваемый планировщиком.
    """
    for chat_id, server_data in servers.items():
        for ip, (_, _, ssh_client) in server_data.items():
            try:
                cpu_usage, ram_usage = get_server_load(ssh_client)

                # Получаем текущие пороги для CPU и RAM
                cpu_threshold, ram_threshold = thresholds.get(ip, (80, 80))
                print(
                    f"Сервер {ip}: CPU {cpu_usage}%, RAM {ram_usage}%, пороги: CPU {cpu_threshold}%, RAM {ram_threshold}%"
                )

                # Проверяем, превышает ли нагрузка порог
                if cpu_usage > cpu_threshold or ram_usage > ram_threshold:
                    print(
                        f"Порог превышен на сервере {ip}: CPU {cpu_usage}%, RAM {ram_usage}%"
                    )
                    await context.bot.send_message(
                        chat_id,
                        f"Сервер {ip} превышает пороги!\nCPU: {cpu_usage}%\nRAM: {ram_usage:.2f}%",
                    )

            except Exception as e:
                print(f"Ошибка при мониторинге сервера {ip}: {e}")
                await context.bot.send_message(
                    chat_id, f"Ошибка при мониторинге сервера {ip}: {e}"
                )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает команду /start для начала взаимодействия с ботом.

    Args:
        update (telegram.Update): Входящее обновление от пользователя.
        context (telegram.ext.CallbackContext): Контекст обновления.
    """
    message = update.message if update.message else update.callback_query.message
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton("Новое подключение", callback_data="new_connection")],
    ]

    server_list = servers.get(chat_id, {})
    if server_list:
        for ip in server_list:
            keyboard.append([InlineKeyboardButton(ip, callback_data=f"server_{ip}")])
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Нет подключенных серверов", callback_data="no_servers"
                )
            ]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text("Выберите сервер:", reply_markup=reply_markup)


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает взаимодействия с кнопками в интерфейсе бота.

    Args:
        update (telegram.Update): Входящее обновление от пользователя.
        context (telegram.ext.CallbackContext): Контекст обновления.
    """
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data

    if data == "new_connection":
        await query.message.reply_text(
            "Введите IP, имя пользователя и пароль через пробел:"
        )
        await query.message.delete()
        return

    if data.startswith("server_"):
        ip = data.split("_")[1]

        # Получаем текущие пороги для CPU и RAM
        cpu_threshold, ram_threshold = thresholds.get(ip, (80, 80))

        keyboard = [
            [InlineKeyboardButton("Текущая нагрузка", callback_data=f"load_{ip}")],
            [
                InlineKeyboardButton(
                    f"Порог CPU: {cpu_threshold}%", callback_data=f"thresholds_cpu_{ip}"
                )
            ],
            [
                InlineKeyboardButton(
                    f"Порог RAM: {ram_threshold}%", callback_data=f"thresholds_ram_{ip}"
                )
            ],
            [InlineKeyboardButton("Удалить сервер", callback_data=f"delete_{ip}")],
            [InlineKeyboardButton("Назад", callback_data="back")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(
            f"Меню сервера {ip}: ", reply_markup=reply_markup
        )
        await query.message.delete()

    elif data.startswith("delete_"):
        ip = data.split("_")[1]
        if ip in servers.get(chat_id, {}):
            ssh_client = servers[chat_id].pop(ip)[2]
            ssh_client.close()
            await query.message.reply_text(f"Сервер {ip} удален из списка мониторинга.")
            await query.message.delete()
            await start(update, context)

    elif data.startswith("load_"):
        ip = data.split("_")[1]

        _, _, ssh_client = servers[chat_id][ip]
        try:
            cpu_usage, ram_usage = get_server_load(ssh_client)
            await query.message.reply_text(
                f"Текущая нагрузка сервера {ip}:\nCPU: {cpu_usage}%\nRAM: {ram_usage:.2f}%"
            )
        except Exception as e:
            await query.message.reply_text(
                f"Ошибка при получении нагрузки сервера {ip}: {e}"
            )

        finally:
            await query.message.delete()
            await start(update, context)

    elif data.startswith("thresholds_"):
        _, resource, ip = data.split("_")
        if resource == "cpu":
            keyboard = [
                [
                    InlineKeyboardButton(f"{value}%", callback_data=f"cpu_{ip}_{value}")
                    for value in [20, 40, 60, 80, 90]
                ],
            ]
            await query.message.reply_text(
                "Выберите порог для CPU:", reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await query.message.delete()
        elif resource == "ram":
            keyboard = [
                [
                    InlineKeyboardButton(f"{value}%", callback_data=f"ram_{ip}_{value}")
                    for value in [20, 40, 60, 80, 90]
                ],
            ]
            await query.message.reply_text(
                "Выберите порог для RAM:", reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await query.message.delete()

    elif data.startswith("cpu_"):
        _, ip, value = data.split("_")
        value = int(value)
        thresholds[ip] = (value, thresholds.get(ip, (80, 80))[1])
        await query.message.reply_text(f"Пороги для CPU для сервера {ip} установлены!")
        await query.message.delete()
        await start(update, context)

    elif data.startswith("ram_"):
        _, ip, value = data.split("_")
        value = int(value)
        thresholds[ip] = (thresholds.get(ip, (80, 80))[0], value)
        await query.message.reply_text(f"Пороги для RAM для сервера {ip} установлены!")
        await query.message.delete()
        await start(update, context)

    elif data == "back":
        await query.message.delete()
        await start(update, context)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает текстовые сообщения от пользователей.

    Args:
        update (telegram.Update): Входящее обновление от пользователя.
        context (telegram.ext.CallbackContext): Контекст обновления.
    """
    chat_id = update.effective_chat.id
    try:
        if len(update.message.text.split()) != 3:
            await update.message.reply_text(
                "Введите IP, имя пользователя и пароль через пробел."
            )
            return

        ip, username, password = update.message.text.split()
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(ip, username=username, password=password)

        servers.setdefault(chat_id, {})[ip] = (username, password, ssh_client)
        await update.message.reply_text(f"Сервер {ip} успешно добавлен!")
        await start(update, context)
    except paramiko.ssh_exception.AuthenticationException:
        await update.message.reply_text("Ошибка: неверное имя пользователя или пароль.")
    except paramiko.ssh_exception.SSHException as e:
        await update.message.reply_text(f"Ошибка SSH: {e}")
    except Exception as e:
        await update.message.reply_text(f"Непредвиденная ошибка: {e}.")


def main():
    """
    Запускает бота.

    Инициализирует бота, загружает данные, регистрирует обработчики команд и сообщений,
    а также запускает периодические задачи. Запускает режим опроса для обработки обновлений.
    """
    load_data()
    atexit.register(save_data)
    app = (
        ApplicationBuilder()
        .token("7748161779:AAFdTGK1YHUXe_Ol8ie1thJiToDN0Bzv2Cs")
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.job_queue.run_repeating(monitor_load, interval=60, first=10)
    app.run_polling()


if __name__ == "__main__":
    main()
