#!/bin/bash
# Скрипт для запуска Flask сервера

cd /home/nikolai/PycharmProjects/telegram_searcher

# Проверяем, не запущен ли уже сервер
if pgrep -f "python.*app.py" > /dev/null; then
    echo "Сервер уже запущен"
    exit 0
fi

# Запускаем сервер в фоне
nohup python3 app.py > /tmp/flask_output.log 2>&1 &

echo "Сервер запущен"
echo "Логи: tail -f /tmp/flask_output.log"
echo "Остановить: pkill -f 'python.*app.py'"

