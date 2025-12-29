"""
Flask веб-приложение для Telegram Searcher
Графический интерфейс для управления поиском групп и каналов
"""

from flask import Flask, render_template, request, jsonify, send_file, session
from flask_session import Session
import os
import json
import asyncio
import threading
from datetime import datetime
from pathlib import Path
import importlib.util

# Импорт основного класса поисковика
from telegram_searcher import TelegramSearcher

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = 'flask_session'
app.config['UPLOAD_FOLDER'] = 'results'
Session(app)

# Глобальные переменные для хранения состояния
search_tasks = {}  # {session_id: {'status': 'running'/'completed'/'error', 'results': {...}}}
search_configs = {}  # {session_id: {'keywords': [], 'cities': [], 'delay': 5.0}}
search_stop_flags = {}  # {session_id: threading.Event()} - флаги для остановки поиска
config_file_lock = threading.Lock()  # Блокировка для безопасной записи в config.py

# Создаем папку для результатов
os.makedirs('results', exist_ok=True)
os.makedirs('templates', exist_ok=True)
os.makedirs('static', exist_ok=True)


def get_session_id():
    """Получить или создать session ID"""
    if 'session_id' not in session:
        session['session_id'] = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return session['session_id']


def save_config_to_file(keywords, cities, delay):
    """Сохранить конфигурацию в config.py"""
    # Используем блокировку для предотвращения одновременных записей
    with config_file_lock:
        try:
            import re
            import time
            config_path = 'config.py'
            
            # Небольшая задержка, чтобы избежать конфликтов при перезапуске Flask
            time.sleep(0.1)
            
            # Читаем текущий config.py (пробуем несколько раз, если файл пустой)
            content = None
            file_not_found = False
            for attempt in range(3):
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if content.strip() and 'KEYWORDS' in content:
                        break
                    elif attempt < 2:
                        app.logger.warning(f"⚠️ Попытка {attempt + 1}: config.py пустой, ждем...")
                        time.sleep(0.2)
                except FileNotFoundError:
                    file_not_found = True
                    if attempt < 2:
                        time.sleep(0.2)
                    break
            
            # Если файл не существует или пустой, создаем базовую структуру
            if file_not_found or not content or not content.strip() or 'KEYWORDS' not in content:
                if file_not_found:
                    app.logger.warning("⚠️ config.py не найден, создаем новый файл")
                else:
                    app.logger.warning("⚠️ config.py пустой или поврежден, восстанавливаем структуру")
                
                # Пытаемся сохранить API_ID и API_HASH из старого файла
                api_id = '27375139'
                api_hash = '66e1bc627b8dda02e2bb35ea44fde4cf'
                if content:
                    api_id_match = re.search(r'API_ID\s*=\s*(\d+)', content)
                    api_hash_match = re.search(r'API_HASH\s*=\s*["\']([^"\']+)["\']', content)
                    if api_id_match:
                        api_id = api_id_match.group(1)
                    if api_hash_match:
                        api_hash = api_hash_match.group(1)
                
                # Создаем новую структуру
                content = f'''"""
Файл конфигурации
"""

# Получите эти данные на https://my.telegram.org
API_ID = {api_id}
API_HASH = "{api_hash}"

# Ключевые слова для поиска
KEYWORDS = [
]

# Города для комбинаций с ключевыми словами
CITIES = [
]

# Максимальное количество результатов на одно ключевое слово
LIMIT_PER_KEYWORD = 50

# Использовать транслитерацию (русские -> английские буквы)
USE_TRANSLITERATION = True

# Использовать комбинации с городами
USE_CITY_COMBINATIONS = True

# Задержка между поисковыми запросами в секундах
SEARCH_DELAY = 5.0
'''
            
            # Формируем строку для KEYWORDS
            keywords_str = 'KEYWORDS = [\n'
            if keywords:
                for kw in keywords:
                    # Экранируем кавычки и обратные слеши
                    kw_escaped = kw.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
                    keywords_str += f"    '{kw_escaped}',\n"
            keywords_str += ']'
            
            # Формируем строку для CITIES
            cities_str = 'CITIES = [\n'
            if cities:
                for city in cities:
                    # Экранируем кавычки и обратные слеши
                    city_escaped = city.replace("\\", "\\\\").replace('"', '\\"')
                    cities_str += f'    "{city_escaped}",\n'
            cities_str += ']'
            
            # Заменяем KEYWORDS - используем нежадный квантификатор для многострочных списков
            # Паттерн ищет KEYWORDS = [ и все до первой закрывающей скобки ], включая переносы строк
            keywords_pattern = r'KEYWORDS\s*=\s*\[.*?\]'
            if re.search(keywords_pattern, content, flags=re.DOTALL):
                content = re.sub(keywords_pattern, keywords_str, content, flags=re.DOTALL)
                app.logger.info(f"✅ KEYWORDS заменен в config.py: {len(keywords)} слов")
            else:
                app.logger.warning("⚠️ Не удалось найти KEYWORDS в config.py для замены")
            
            # Заменяем CITIES - аналогично
            cities_pattern = r'CITIES\s*=\s*\[.*?\]'
            if re.search(cities_pattern, content, flags=re.DOTALL):
                content = re.sub(cities_pattern, cities_str, content, flags=re.DOTALL)
                app.logger.info(f"✅ CITIES заменен в config.py: {len(cities)} городов")
            else:
                app.logger.warning("⚠️ Не удалось найти CITIES в config.py для замены")
            
            # Заменяем SEARCH_DELAY
            delay_pattern = r'SEARCH_DELAY\s*=\s*[\d.]+'
            content = re.sub(delay_pattern, f'SEARCH_DELAY = {delay}', content)
            
            # Сохраняем файл
            try:
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                # Принудительно сбрасываем буфер
                import os
                os.fsync(f.fileno()) if hasattr(f, 'fileno') else None
            except Exception as e:
                app.logger.error(f"❌ Ошибка записи в config.py: {e}")
                return False
            
            # Проверяем, что сохранение прошло успешно (пробуем несколько раз)
            for check_attempt in range(3):
                try:
                    time.sleep(0.05)  # Небольшая задержка для файловой системы
                    with open(config_path, 'r', encoding='utf-8') as f:
                        saved_content = f.read()
                    
                    if not saved_content.strip():
                        if check_attempt < 2:
                            app.logger.warning(f"⚠️ Проверка {check_attempt + 1}: файл пустой, повторяем...")
                            continue
                        else:
                            app.logger.error("❌ Файл остался пустым после сохранения!")
                            return False
                    
                    # Проверяем наличие KEYWORDS в файле
                    if 'KEYWORDS' not in saved_content:
                        app.logger.error("❌ KEYWORDS не найден в сохраненном файле!")
                        return False
                    
                    # Проверяем наличие ключевых слов
                    if keywords:
                        found_count = 0
                        for kw in keywords:
                            # Ищем слово в файле (может быть с экранированием)
                            kw_escaped = kw.replace("'", "\\'")
                            if kw in saved_content or kw_escaped in saved_content:
                                found_count += 1
                        
                        if found_count == 0:
                            app.logger.error(f"❌ Ключевые слова не найдены в сохраненном файле! Ожидалось: {keywords}")
                            return False
                        elif found_count < len(keywords):
                            app.logger.warning(f"⚠️ Найдено только {found_count} из {len(keywords)} ключевых слов")
                    
                    app.logger.info(f"✅ config.py успешно сохранен. KEYWORDS: {len(keywords)}, CITIES: {len(cities)}")
                    return True
                    
                except Exception as e:
                    if check_attempt < 2:
                        app.logger.warning(f"⚠️ Ошибка проверки сохранения (попытка {check_attempt + 1}): {e}")
                        time.sleep(0.1)
                    else:
                        app.logger.error(f"❌ Ошибка проверки сохранения: {e}")
                        return False
            
            return False
        except Exception as e:
            app.logger.error(f"Ошибка сохранения config.py: {e}")
            import traceback
            traceback.print_exc()
            return False


def run_search_async(session_id, keywords, cities, delay, api_id, api_hash):
    """Асинхронный запуск поиска в отдельном потоке"""
    try:
        app.logger.info(f"🚀 Запуск поиска: keywords={keywords}, cities={cities}, delay={delay}")
        
        # Проверяем входные данные
        if not keywords:
            search_tasks[session_id]['status'] = 'error'
            search_tasks[session_id]['message'] = 'Ошибка: не указаны ключевые слова'
            app.logger.error("❌ Ошибка: не указаны ключевые слова")
            return
        
        # Создаем флаг остановки
        stop_event = threading.Event()
        search_stop_flags[session_id] = stop_event
        
        # Создаем новый event loop для этого потока
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Обновляем статус
        search_tasks[session_id]['status'] = 'running'
        search_tasks[session_id]['message'] = 'Поиск запущен...'
        
        # Создаем экземпляр поисковика
        app.logger.info("📱 Создание экземпляра TelegramSearcher...")
        searcher = TelegramSearcher(api_id, api_hash, search_delay=delay)
        
        # Генерируем поисковые запросы
        app.logger.info(f"🔍 Генерация поисковых запросов из {len(keywords)} ключевых слов и {len(cities) if cities else 0} городов...")
        search_queries = TelegramSearcher.generate_search_queries(keywords, cities)
        app.logger.info(f"✅ Сгенерировано {len(search_queries)} поисковых запросов")
        
        # Запускаем поиск используя оригинальный метод класса
        async def search():
            try:
                await searcher.connect()
                
                # Используем оригинальный метод поиска
                search_tasks[session_id]['message'] = f'Поиск по {len(search_queries)} запросам...'
                
                # Модифицируем метод поиска для поддержки остановки
                # Создаем обертку, которая проверяет флаг остановки
                all_groups = []
                all_channels = []
                seen_ids = set()
                
                for i, keyword in enumerate(search_queries):
                    # Проверяем флаг остановки перед каждым запросом
                    if stop_event.is_set():
                        break
                    
                    try:
                        from telethon.tl.functions.contacts import SearchRequest
                        from telethon.tl.types import Channel, Chat
                        
                        results = await searcher.client(SearchRequest(
                            q=keyword,
                            limit=50
                        ))
                        
                        # Проверяем, есть ли результаты
                        found_in_query = 0
                        if hasattr(results, 'chats') and results.chats:
                            for result in results.chats:
                                if stop_event.is_set():
                                    break
                                
                                from telethon.tl.types import Channel, Chat
                                if not isinstance(result, (Channel, Chat)):
                                    continue
                                
                                entity_id = result.id
                                if entity_id in seen_ids:
                                    continue
                                seen_ids.add(entity_id)
                                
                                members_count = await searcher._get_members_count(result)
                                
                                entity_info = {
                                    'id': entity_id,
                                    'title': result.title,
                                    'username': getattr(result, 'username', None),
                                    'members_count': members_count,
                                    'keyword': keyword
                                }
                                
                                if isinstance(result, Channel):
                                    if result.broadcast:
                                        all_channels.append(entity_info)
                                        searcher.current_results['channels'].append(entity_info)
                                    else:
                                        all_groups.append(entity_info)
                                        searcher.current_results['groups'].append(entity_info)
                                elif isinstance(result, Chat):
                                    all_groups.append(entity_info)
                                    searcher.current_results['groups'].append(entity_info)
                                
                                found_in_query += 1
                        
                        # Обновляем прогресс с информацией о найденных
                        progress = (i + 1) / len(search_queries) * 100
                        found_total = len(all_groups) + len(all_channels)
                        if found_in_query > 0:
                            search_tasks[session_id]['message'] = f'Поиск... {i+1}/{len(search_queries)} ({progress:.1f}%) | Найдено: {found_total} | В этом запросе: {found_in_query}'
                        else:
                            search_tasks[session_id]['message'] = f'Поиск... {i+1}/{len(search_queries)} ({progress:.1f}%) | Найдено: {found_total}'
                        
                        # Обновляем прогресс
                        progress = (i + 1) / len(search_queries) * 100
                        found_total = len(all_groups) + len(all_channels)
                        search_tasks[session_id]['message'] = f'Поиск... {i+1}/{len(search_queries)} ({progress:.1f}%) | Найдено: {found_total}'
                        
                        # Задержка
                        if delay > 0 and not stop_event.is_set():
                            await asyncio.sleep(delay)
                            
                    except Exception as e:
                        error_msg = str(e)
                        # Обрабатываем flood wait
                        if "wait of" in error_msg and "seconds" in error_msg:
                            try:
                                wait_seconds = int(error_msg.split("wait of")[1].split("seconds")[0].strip())
                                wait_hours = wait_seconds / 3600
                                if wait_seconds > 3600:
                                    search_tasks[session_id]['message'] = f'⚠️ Flood wait: пропускаю "{keyword}" (ожидание ~{wait_hours:.1f}ч)'
                                    continue
                            except:
                                pass
                        
                        # Логируем другие ошибки, но продолжаем
                        if not stop_event.is_set():
                            # Не показываем каждую ошибку, только обновляем прогресс
                            continue
                        else:
                            break
                
                # Проверяем, был ли остановлен поиск
                if stop_event.is_set():
                    saved_results = searcher.current_results
                    if saved_results['groups'] or saved_results['channels']:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        groups_file = f'results/telegram_groups_stopped_{timestamp}.xlsx'
                        channels_file = f'results/telegram_channels_stopped_{timestamp}.xlsx'
                        
                        searcher.save_to_excel(
                            saved_results['groups'],
                            saved_results['channels'],
                            groups_file,
                            channels_file
                        )
                        
                        search_tasks[session_id]['status'] = 'stopped'
                        search_tasks[session_id]['message'] = f'Поиск остановлен. Найдено: {len(saved_results["groups"])} групп, {len(saved_results["channels"])} каналов'
                        search_tasks[session_id]['results'] = {
                            'groups_file': groups_file,
                            'channels_file': channels_file,
                            'groups_count': len(saved_results['groups']),
                            'channels_count': len(saved_results['channels'])
                        }
                    else:
                        search_tasks[session_id]['status'] = 'stopped'
                        search_tasks[session_id]['message'] = 'Поиск остановлен. Результаты не найдены'
                else:
                    # Поиск завершен успешно
                    results = {'groups': all_groups, 'channels': all_channels}
                    
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    groups_file = f'results/telegram_groups_{timestamp}.xlsx'
                    channels_file = f'results/telegram_channels_{timestamp}.xlsx'
                    
                    searcher.save_to_excel(
                        results['groups'],
                        results['channels'],
                        groups_file,
                        channels_file
                    )
                    
                    search_tasks[session_id]['status'] = 'completed'
                    search_tasks[session_id]['message'] = f'Поиск завершен! Найдено: {len(results["groups"])} групп, {len(results["channels"])} каналов'
                    search_tasks[session_id]['results'] = {
                        'groups_file': groups_file,
                        'channels_file': channels_file,
                        'groups_count': len(results['groups']),
                        'channels_count': len(results['channels'])
                    }
                
                await searcher.disconnect()
                
            except KeyboardInterrupt:
                # Сохраняем результаты при прерывании
                saved_results = searcher.current_results
                if saved_results['groups'] or saved_results['channels']:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    groups_file = f'results/telegram_groups_interrupted_{timestamp}.xlsx'
                    channels_file = f'results/telegram_channels_interrupted_{timestamp}.xlsx'
                    
                    searcher.save_to_excel(
                        saved_results['groups'],
                        saved_results['channels'],
                        groups_file,
                        channels_file
                    )
                    
                    search_tasks[session_id]['status'] = 'interrupted'
                    search_tasks[session_id]['message'] = 'Поиск прерван, результаты сохранены'
                    search_tasks[session_id]['results'] = {
                        'groups_file': groups_file,
                        'channels_file': channels_file,
                        'groups_count': len(saved_results['groups']),
                        'channels_count': len(saved_results['channels'])
                    }
                
                await searcher.disconnect()
                
            except Exception as e:
                error_msg = f'Ошибка: {str(e)}'
                app.logger.error(f"❌ Ошибка в поиске: {e}")
                import traceback
                app.logger.error(traceback.format_exc())
                search_tasks[session_id]['status'] = 'error'
                search_tasks[session_id]['message'] = error_msg
                try:
                    await searcher.disconnect()
                except:
                    pass
        
        app.logger.info("🔄 Запуск event loop...")
        loop.run_until_complete(search())
        loop.close()
        app.logger.info("✅ Event loop завершен")
        
    except Exception as e:
        error_msg = f'Ошибка запуска: {str(e)}'
        app.logger.error(f"❌ Критическая ошибка запуска: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        search_tasks[session_id]['status'] = 'error'
        search_tasks[session_id]['message'] = error_msg


@app.route('/')
def index():
    """Главная страница"""
    session_id = get_session_id()
    
    # Загружаем данные из config.py
    try:
        import config as app_config
        keywords = getattr(app_config, 'KEYWORDS', [])
        cities = getattr(app_config, 'CITIES', [])
        delay = getattr(app_config, 'SEARCH_DELAY', 5.0)
    except:
        keywords = []
        cities = []
        delay = 5.0
    
    # Инициализируем конфигурацию для сессии из config.py
    if session_id not in search_configs:
        search_configs[session_id] = {
            'keywords': keywords,
            'cities': cities,
            'delay': delay
        }
    else:
        # Обновляем из config.py если там есть изменения
        search_configs[session_id]['keywords'] = keywords
        search_configs[session_id]['cities'] = cities
        search_configs[session_id]['delay'] = delay
    
    # Инициализируем задачу поиска
    if session_id not in search_tasks:
        search_tasks[session_id] = {
            'status': 'idle',
            'message': 'Готов к запуску',
            'results': None
        }
    
    config = search_configs[session_id]
    return render_template('index.html', 
                         keywords=config['keywords'],
                         cities=config['cities'],
                         delay=config['delay'])


@app.route('/api/add_keyword', methods=['POST'])
def add_keyword():
    """Добавить ключевое слово"""
    session_id = get_session_id()
    data = request.json
    keyword = data.get('keyword', '').strip()
    
    app.logger.info(f"➕ Добавление ключевого слова: '{keyword}' для сессии {session_id}")
    
    if not keyword:
        return jsonify({'success': False, 'message': 'Ключевое слово не может быть пустым'})
    
    # Если сессия не найдена, загружаем данные из config.py
    if session_id not in search_configs:
        app.logger.info(f"📝 Сессия не найдена, загружаем данные из config.py")
        try:
            import config as app_config
            keywords = getattr(app_config, 'KEYWORDS', [])
            cities = getattr(app_config, 'CITIES', [])
            delay = getattr(app_config, 'SEARCH_DELAY', 5.0)
            search_configs[session_id] = {
                'keywords': keywords.copy(),
                'cities': cities.copy(),
                'delay': delay
            }
            app.logger.info(f"✅ Данные загружены из config.py: {len(keywords)} ключевых слов, {len(cities)} городов")
        except Exception as e:
            app.logger.error(f"❌ Ошибка загрузки из config.py: {e}")
            search_configs[session_id] = {'keywords': [], 'cities': [], 'delay': 5.0}
    
    if keyword not in search_configs[session_id]['keywords']:
        search_configs[session_id]['keywords'].append(keyword)
        app.logger.info(f"✅ Ключевое слово добавлено. Всего: {len(search_configs[session_id]['keywords'])}")
        app.logger.info(f"📋 Текущие ключевые слова: {search_configs[session_id]['keywords']}")
        
        # Сохраняем в config.py
        save_result = save_config_to_file(
            search_configs[session_id]['keywords'],
            search_configs[session_id]['cities'],
            search_configs[session_id]['delay']
        )
        if save_result:
            app.logger.info("💾 config.py успешно обновлен")
            # Проверяем, что данные действительно сохранились
            try:
                import config as test_config
                saved_keywords = getattr(test_config, 'KEYWORDS', [])
                app.logger.info(f"🔍 Проверка сохранения: в config.py найдено {len(saved_keywords)} ключевых слов")
                if len(saved_keywords) != len(search_configs[session_id]['keywords']):
                    app.logger.warning(f"⚠️ Несоответствие: в памяти {len(search_configs[session_id]['keywords'])}, в файле {len(saved_keywords)}")
            except Exception as e:
                app.logger.error(f"❌ Ошибка при проверке сохранения: {e}")
        else:
            app.logger.error("❌ Не удалось сохранить в config.py!")
        
        return jsonify({'success': True, 'keywords': search_configs[session_id]['keywords']})
    else:
        app.logger.warning(f"⚠️ Ключевое слово '{keyword}' уже существует")
        return jsonify({'success': False, 'message': 'Ключевое слово уже добавлено'})


@app.route('/api/remove_keyword', methods=['POST'])
def remove_keyword():
    """Удалить ключевое слово"""
    session_id = get_session_id()
    data = request.json
    keyword = data.get('keyword', '').strip()
    
    app.logger.info(f"➖ Удаление ключевого слова: '{keyword}' для сессии {session_id}")
    
    # Если сессия не найдена, загружаем данные из config.py
    if session_id not in search_configs:
        app.logger.info(f"📝 Сессия не найдена, загружаем данные из config.py")
        try:
            import config as app_config
            keywords = getattr(app_config, 'KEYWORDS', [])
            cities = getattr(app_config, 'CITIES', [])
            delay = getattr(app_config, 'SEARCH_DELAY', 5.0)
            search_configs[session_id] = {
                'keywords': keywords.copy(),
                'cities': cities.copy(),
                'delay': delay
            }
            app.logger.info(f"✅ Данные загружены из config.py: {len(keywords)} ключевых слов, {len(cities)} городов")
        except Exception as e:
            app.logger.error(f"❌ Ошибка загрузки из config.py: {e}")
            search_configs[session_id] = {'keywords': [], 'cities': [], 'delay': 5.0}
    
    app.logger.info(f"📋 Текущие ключевые слова: {search_configs[session_id]['keywords']}")
    
    if keyword in search_configs[session_id]['keywords']:
        search_configs[session_id]['keywords'].remove(keyword)
        app.logger.info(f"✅ Ключевое слово удалено. Осталось: {len(search_configs[session_id]['keywords'])}")
        
        # Сохраняем в config.py
        if save_config_to_file(
            search_configs[session_id]['keywords'],
            search_configs[session_id]['cities'],
            search_configs[session_id]['delay']
        ):
            app.logger.info("💾 config.py обновлен")
        else:
            app.logger.warning("⚠️ Не удалось сохранить в config.py")
        
        return jsonify({'success': True, 'keywords': search_configs[session_id]['keywords']})
    else:
        app.logger.warning(f"⚠️ Ключевое слово '{keyword}' не найдено в списке")
        return jsonify({'success': False, 'message': f'Ключевое слово "{keyword}" не найдено'})


@app.route('/api/add_city', methods=['POST'])
def add_city():
    """Добавить город"""
    session_id = get_session_id()
    data = request.json
    city = data.get('city', '').strip()
    
    app.logger.info(f"➕ Добавление города: '{city}' для сессии {session_id}")
    
    if not city:
        return jsonify({'success': False, 'message': 'Город не может быть пустым'})
    
    # Если сессия не найдена, загружаем данные из config.py
    if session_id not in search_configs:
        app.logger.info(f"📝 Сессия не найдена, загружаем данные из config.py")
        try:
            import config as app_config
            keywords = getattr(app_config, 'KEYWORDS', [])
            cities = getattr(app_config, 'CITIES', [])
            delay = getattr(app_config, 'SEARCH_DELAY', 5.0)
            search_configs[session_id] = {
                'keywords': keywords.copy(),
                'cities': cities.copy(),
                'delay': delay
            }
            app.logger.info(f"✅ Данные загружены из config.py: {len(keywords)} ключевых слов, {len(cities)} городов")
        except Exception as e:
            app.logger.error(f"❌ Ошибка загрузки из config.py: {e}")
            search_configs[session_id] = {'keywords': [], 'cities': [], 'delay': 5.0}
    
    if city not in search_configs[session_id]['cities']:
        search_configs[session_id]['cities'].append(city)
        app.logger.info(f"✅ Город добавлен. Всего: {len(search_configs[session_id]['cities'])}")
        app.logger.info(f"📋 Текущие города: {search_configs[session_id]['cities']}")
        app.logger.info(f"📋 Текущие ключевые слова: {search_configs[session_id]['keywords']}")
        
        # Сохраняем в config.py (с текущими ключевыми словами!)
        save_result = save_config_to_file(
            search_configs[session_id]['keywords'],
            search_configs[session_id]['cities'],
            search_configs[session_id]['delay']
        )
        if save_result:
            app.logger.info("💾 config.py успешно обновлен")
        else:
            app.logger.error("❌ Не удалось сохранить в config.py!")
        
        return jsonify({'success': True, 'cities': search_configs[session_id]['cities']})
    else:
        app.logger.warning(f"⚠️ Город '{city}' уже существует")
        return jsonify({'success': False, 'message': 'Город уже добавлен'})


@app.route('/api/remove_city', methods=['POST'])
def remove_city():
    """Удалить город"""
    session_id = get_session_id()
    data = request.json
    city = data.get('city', '').strip()
    
    app.logger.info(f"➖ Удаление города: '{city}' для сессии {session_id}")
    
    # Если сессия не найдена, загружаем данные из config.py
    if session_id not in search_configs:
        app.logger.info(f"📝 Сессия не найдена, загружаем данные из config.py")
        try:
            import config as app_config
            keywords = getattr(app_config, 'KEYWORDS', [])
            cities = getattr(app_config, 'CITIES', [])
            delay = getattr(app_config, 'SEARCH_DELAY', 5.0)
            search_configs[session_id] = {
                'keywords': keywords.copy(),
                'cities': cities.copy(),
                'delay': delay
            }
            app.logger.info(f"✅ Данные загружены из config.py: {len(keywords)} ключевых слов, {len(cities)} городов")
        except Exception as e:
            app.logger.error(f"❌ Ошибка загрузки из config.py: {e}")
            search_configs[session_id] = {'keywords': [], 'cities': [], 'delay': 5.0}
    
    app.logger.info(f"📋 Текущие города: {search_configs[session_id]['cities']}")
    
    if city in search_configs[session_id]['cities']:
        search_configs[session_id]['cities'].remove(city)
        app.logger.info(f"✅ Город удален. Осталось: {len(search_configs[session_id]['cities'])}")
        
        # Сохраняем в config.py
        if save_config_to_file(
            search_configs[session_id]['keywords'],
            search_configs[session_id]['cities'],
            search_configs[session_id]['delay']
        ):
            app.logger.info("💾 config.py обновлен")
        else:
            app.logger.warning("⚠️ Не удалось сохранить в config.py")
        
        return jsonify({'success': True, 'cities': search_configs[session_id]['cities']})
    else:
        app.logger.warning(f"⚠️ Город '{city}' не найден в списке")
        return jsonify({'success': False, 'message': f'Город "{city}" не найден'})


@app.route('/api/set_delay', methods=['POST'])
def set_delay():
    """Установить задержку"""
    session_id = get_session_id()
    data = request.json
    delay = float(data.get('delay', 5.0))
    
    app.logger.info(f"⏱️ Установка задержки: {delay} секунд для сессии {session_id}")
    
    if delay < 0:
        return jsonify({'success': False, 'message': 'Задержка не может быть отрицательной'})
    
    # Если сессия не найдена, загружаем данные из config.py
    if session_id not in search_configs:
        app.logger.info(f"📝 Сессия не найдена, загружаем данные из config.py")
        try:
            import config as app_config
            keywords = getattr(app_config, 'KEYWORDS', [])
            cities = getattr(app_config, 'CITIES', [])
            current_delay = getattr(app_config, 'SEARCH_DELAY', 5.0)
            search_configs[session_id] = {
                'keywords': keywords.copy(),
                'cities': cities.copy(),
                'delay': current_delay
            }
            app.logger.info(f"✅ Данные загружены из config.py: {len(keywords)} ключевых слов, {len(cities)} городов")
        except Exception as e:
            app.logger.error(f"❌ Ошибка загрузки из config.py: {e}")
            search_configs[session_id] = {'keywords': [], 'cities': [], 'delay': 5.0}
    
    search_configs[session_id]['delay'] = delay
    app.logger.info(f"📋 Текущие ключевые слова: {search_configs[session_id]['keywords']}")
    app.logger.info(f"📋 Текущие города: {search_configs[session_id]['cities']}")
    
    # Сохраняем в config.py (с текущими ключевыми словами и городами!)
    save_result = save_config_to_file(
        search_configs[session_id]['keywords'],
        search_configs[session_id]['cities'],
        delay
    )
    if save_result:
        app.logger.info("💾 config.py успешно обновлен")
    else:
        app.logger.error("❌ Не удалось сохранить в config.py!")
    
    return jsonify({'success': True, 'delay': delay})


@app.route('/api/start_search', methods=['POST'])
def start_search():
    """Запустить поиск"""
    session_id = get_session_id()
    app.logger.info(f"🔍 Запрос на запуск поиска от сессии {session_id}")
    
    # Если сессия не найдена, загружаем данные из config.py
    if session_id not in search_configs:
        app.logger.info(f"📝 Сессия не найдена, загружаем данные из config.py")
        try:
            import config as app_config
            keywords = getattr(app_config, 'KEYWORDS', [])
            cities = getattr(app_config, 'CITIES', [])
            delay = getattr(app_config, 'SEARCH_DELAY', 5.0)
            search_configs[session_id] = {
                'keywords': keywords.copy(),
                'cities': cities.copy(),
                'delay': delay
            }
            app.logger.info(f"✅ Данные загружены из config.py: {len(keywords)} ключевых слов, {len(cities)} городов")
        except Exception as e:
            app.logger.error(f"❌ Ошибка загрузки из config.py: {e}")
            search_configs[session_id] = {'keywords': [], 'cities': [], 'delay': 5.0}
    
    config = search_configs[session_id]
    app.logger.info(f"📋 Конфигурация: keywords={len(config.get('keywords', []))}, cities={len(config.get('cities', []))}, delay={config.get('delay', 5.0)}")
    
    if not config.get('keywords'):
        app.logger.warning("⚠️ Ключевые слова не указаны")
        return jsonify({'success': False, 'message': 'Добавьте хотя бы одно ключевое слово'})
    
    # Проверяем API credentials
    try:
        import config as app_config
        api_id = app_config.API_ID
        api_hash = app_config.API_HASH
        app.logger.info("✅ API credentials загружены")
    except Exception as e:
        app.logger.error(f"❌ Ошибка загрузки API credentials: {e}")
        return jsonify({'success': False, 'message': 'API_ID и API_HASH не настроены в config.py'})
    
    # Проверяем, не запущен ли уже поиск
    if session_id in search_tasks and search_tasks[session_id]['status'] == 'running':
        app.logger.warning("⚠️ Поиск уже запущен")
        return jsonify({'success': False, 'message': 'Поиск уже запущен'})
    
    # Инициализируем задачу
    search_tasks[session_id] = {
        'status': 'starting',
        'message': 'Запуск поиска...',
        'results': None
    }
    
    # Запускаем поиск в отдельном потоке
    app.logger.info("🚀 Создание потока для поиска...")
    thread = threading.Thread(
        target=run_search_async,
        args=(session_id, config['keywords'], config.get('cities', []), config.get('delay', 5.0), api_id, api_hash)
    )
    thread.daemon = True
    thread.start()
    app.logger.info("✅ Поток запущен")
    
    return jsonify({'success': True, 'message': 'Поиск запущен'})


@app.route('/api/stop_search', methods=['POST'])
def stop_search():
    """Остановить поиск"""
    session_id = get_session_id()
    
    if session_id in search_stop_flags:
        search_stop_flags[session_id].set()
        search_tasks[session_id]['message'] = 'Остановка поиска...'
        return jsonify({'success': True, 'message': 'Команда остановки отправлена'})
    
    return jsonify({'success': False, 'message': 'Поиск не запущен'})


@app.route('/api/status', methods=['GET'])
def get_status():
    """Получить статус поиска"""
    session_id = get_session_id()
    
    if session_id not in search_tasks:
        return jsonify({
            'status': 'idle',
            'message': 'Готов к запуску',
            'results': None
        })
    
    task = search_tasks[session_id]
    return jsonify({
        'status': task['status'],
        'message': task.get('message', ''),
        'results': task.get('results')
    })


@app.route('/api/download/<filename>')
def download_file(filename):
    """Скачать файл результата"""
    file_path = os.path.join('results', filename)
    
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return jsonify({'error': 'Файл не найден'}), 404


@app.route('/api/get_files', methods=['GET'])
def get_files():
    """Получить список доступных файлов результатов"""
    files = []
    results_dir = Path('results')
    
    if results_dir.exists():
        for file in results_dir.glob('*.xlsx'):
            files.append({
                'name': file.name,
                'size': file.stat().st_size,
                'modified': datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            })
    
    # Сортируем по дате изменения (новые первыми)
    files.sort(key=lambda x: x['modified'], reverse=True)
    
    return jsonify({'files': files})


if __name__ == '__main__':
    print("🚀 Запуск Flask приложения...")
    print("📱 Откройте в браузере: http://127.0.0.1:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)

