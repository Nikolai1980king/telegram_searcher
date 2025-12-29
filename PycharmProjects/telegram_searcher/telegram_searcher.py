"""
Автоматический поиск групп и каналов в Telegram
Использует Telethon для работы с Telegram API
"""

import asyncio
from datetime import datetime
from typing import List, Dict, Set

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill


class TelegramSearcher:
    def __init__(self, api_id: int, api_hash: str, session_name: str = 'telegram_session', search_delay: float = 1.0):
        """
        Инициализация клиента Telegram
        
        Args:
            api_id: API ID из my.telegram.org
            api_hash: API Hash из my.telegram.org
            session_name: Имя файла сессии (для сохранения авторизации)
            search_delay: Задержка между поисковыми запросами в секундах (0 = без задержки)
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.search_delay = search_delay
        self.client = TelegramClient(session_name, api_id, api_hash)
        # Хранилище для результатов (для сохранения при прерывании)
        self.current_results = {'groups': [], 'channels': []}
    
    @staticmethod
    def transliterate(text: str) -> str:
        """
        Транслитерация русских букв в английские
        
        Args:
            text: Текст для транслитерации
            
        Returns:
            Транслитерированный текст
        """
        translit_map = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
            'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
            'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Yo',
            'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
            'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
            'Ф': 'F', 'Х': 'H', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sch',
            'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya'
        }
        
        result = []
        for char in text:
            result.append(translit_map.get(char, char))
        return ''.join(result)
    
    @staticmethod
    def generate_search_queries(keywords: List[str], cities: List[str] = None) -> List[str]:
        """
        Генерирует комбинации поисковых запросов:
        - Оригинальные ключевые слова
        - Транслитерированные ключевые слова
        - Ключевые слова + города
        - Транслитерированные ключевые слова + города
        
        Args:
            keywords: Список ключевых слов
            cities: Список городов (опционально)
            
        Returns:
            Список всех комбинаций для поиска
        """
        queries: Set[str] = set()
        
        # Добавляем оригинальные ключевые слова
        for keyword in keywords:
            if keyword.strip():
                queries.add(keyword.strip())
        
        # Добавляем транслитерированные ключевые слова
        for keyword in keywords:
            if keyword.strip():
                translit = TelegramSearcher.transliterate(keyword.strip())
                if translit != keyword.strip():  # Добавляем только если изменилось
                    queries.add(translit)
        
        # Добавляем комбинации с городами
        if cities:
            for keyword in keywords:
                if not keyword.strip():
                    continue
                
                for city in cities:
                    if not city.strip():
                        continue
                    
                    # Оригинальное слово + город
                    queries.add(f"{keyword.strip()} {city.strip()}")
                    queries.add(f"{city.strip()} {keyword.strip()}")
                    
                    # Транслит слова + город
                    translit_keyword = TelegramSearcher.transliterate(keyword.strip())
                    if translit_keyword != keyword.strip():
                        queries.add(f"{translit_keyword} {city.strip()}")
                        queries.add(f"{city.strip()} {translit_keyword}")
                    
                    # Слово + транслит города
                    translit_city = TelegramSearcher.transliterate(city.strip())
                    if translit_city != city.strip():
                        queries.add(f"{keyword.strip()} {translit_city}")
                        queries.add(f"{translit_city} {keyword.strip()}")
                    
                    # Транслит слова + транслит города
                    if translit_keyword != keyword.strip() and translit_city != city.strip():
                        queries.add(f"{translit_keyword} {translit_city}")
                        queries.add(f"{translit_city} {translit_keyword}")
        
        return sorted(list(queries))
        
    async def connect(self):
        """Подключение к Telegram"""
        await self.client.start()
        print("✅ Успешно подключено к Telegram")
    
    async def _get_members_count(self, entity) -> int:
        """
        Получает количество участников группы/канала
        
        Args:
            entity: Объект Channel или Chat
            
        Returns:
            Количество участников или 0 если не удалось получить
        """
        try:
            if isinstance(entity, Channel):
                # Для каналов и супергрупп используем GetFullChannelRequest
                try:
                    full_channel = await self.client(GetFullChannelRequest(entity))
                    if hasattr(full_channel, 'full_chat'):
                        count = getattr(full_channel.full_chat, 'participants_count', None)
                        if count is not None:
                            return count
                except Exception:
                    pass
                
                # Альтернативный способ - через get_entity
                try:
                    full_info = await self.client.get_entity(entity)
                    count = getattr(full_info, 'participants_count', None)
                    if count is not None:
                        return count
                except Exception:
                    pass
                
                # Если ничего не помогло, пробуем получить из самого объекта
                count = getattr(entity, 'participants_count', None)
                if count is not None:
                    return count
                    
            elif isinstance(entity, Chat):
                # Для обычных групп используем GetFullChatRequest
                try:
                    full_chat = await self.client(GetFullChatRequest(entity.id))
                    if hasattr(full_chat, 'full_chat'):
                        count = getattr(full_chat.full_chat, 'participants_count', None)
                        if count is not None:
                            return count
                except Exception:
                    pass
                
                # Альтернативный способ
                count = getattr(entity, 'participants_count', None)
                if count is not None:
                    return count
                    
        except Exception:
            pass
        
        return 0
        
    async def search_channels_and_groups(self, keywords: List[str], limit_per_keyword: int = 50) -> Dict:
        """
        Поиск групп и каналов по ключевым словам
        
        Args:
            keywords: Список ключевых слов для поиска
            limit_per_keyword: Максимальное количество результатов на одно ключевое слово
            
        Returns:
            Словарь с найденными группами и каналами
        """
        all_groups = []
        all_channels = []
        seen_ids = set()  # Для избежания дубликатов
        
        # Обновляем текущие результаты в классе (для возможности сохранения при прерывании)
        self.current_results = {'groups': [], 'channels': []}
        
        print(f"\n🔍 Начинаю поиск по {len(keywords)} ключевым словам...")
        
        for keyword in keywords:
            print(f"\n📝 Ищу: '{keyword}'...")
            try:
                # Метод 1: Поиск через глобальный поиск Telegram
                try:
                    # Используем SearchRequest для поиска контактов/чатов
                    results = await self.client(SearchRequest(
                        q=keyword,
                        limit=limit_per_keyword
                    ))
                    
                    # Обработка результатов
                    for result in results.chats:
                        if not isinstance(result, (Channel, Chat)):
                            continue
                        
                        # Проверяем на дубликаты
                        entity_id = result.id
                        if entity_id in seen_ids:
                            continue
                        seen_ids.add(entity_id)
                        
                        # Получаем количество участников
                        members_count = await self._get_members_count(result)
                        
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
                                self.current_results['channels'].append(entity_info)  # Сохраняем в классе
                                username_str = f" (@{entity_info['username']})" if entity_info['username'] else ""
                                members_str = f" [{members_count:,} подписчиков]" if members_count > 0 else ""
                                print(f"  📢 Канал: {result.title}{username_str}{members_str}")
                            else:
                                all_groups.append(entity_info)
                                self.current_results['groups'].append(entity_info)  # Сохраняем в классе
                                username_str = f" (@{entity_info['username']})" if entity_info['username'] else ""
                                members_str = f" [{members_count:,} участников]" if members_count > 0 else ""
                                print(f"  👥 Группа: {result.title}{username_str}{members_str}")
                        elif isinstance(result, Chat):
                            all_groups.append(entity_info)
                            self.current_results['groups'].append(entity_info)  # Сохраняем в классе
                            members_str = f" [{members_count:,} участников]" if members_count > 0 else ""
                            print(f"  👥 Группа: {result.title}{members_str}")
                            
                except Exception as e:
                    error_msg = str(e)
                    # Проверяем, это flood wait?
                    if "wait of" in error_msg and "seconds" in error_msg:
                        # Извлекаем время ожидания
                        try:
                            wait_seconds = int(error_msg.split("wait of")[1].split("seconds")[0].strip())
                            wait_minutes = wait_seconds / 60
                            wait_hours = wait_minutes / 60
                            print(f"  ⚠️ Flood Wait: Telegram требует подождать {wait_seconds} секунд (~{wait_minutes:.0f} минут, ~{wait_hours:.1f} часов)")
                            print(f"  💡 Рекомендация: Увеличьте SEARCH_DELAY в config.py до 2.0-3.0 секунд")
                            print(f"  💡 Или уменьшите количество городов/ключевых слов")
                            if wait_seconds > 3600:  # Больше часа
                                print(f"  ⏸️ Пропускаю этот запрос и продолжаю с другими...")
                                continue
                        except:
                            pass
                    else:
                        print(f"  ⚠️ Ошибка при поиске через SearchRequest: {e}")
                    
                    # Метод 2: Поиск по уже известным диалогам
                    print(f"  🔄 Пробую альтернативный метод поиска...")
                    try:
                        async for dialog in self.client.iter_dialogs(limit=200):
                            if not isinstance(dialog.entity, (Channel, Chat)):
                                continue
                            
                            title = dialog.entity.title.lower()
                            if keyword.lower() not in title:
                                continue
                            
                            entity_id = dialog.entity.id
                            if entity_id in seen_ids:
                                continue
                            seen_ids.add(entity_id)
                            
                            # Получаем количество участников
                            members_count = await self._get_members_count(dialog.entity)
                            
                            entity_info = {
                                'id': entity_id,
                                'title': dialog.entity.title,
                                'username': getattr(dialog.entity, 'username', None),
                                'members_count': members_count,
                                'keyword': keyword
                            }
                            
                            if isinstance(dialog.entity, Channel):
                                if dialog.entity.broadcast:
                                    all_channels.append(entity_info)
                                    self.current_results['channels'].append(entity_info)  # Сохраняем в классе
                                    username_str = f" (@{entity_info['username']})" if entity_info['username'] else ""
                                    members_str = f" [{members_count:,} подписчиков]" if members_count > 0 else ""
                                    print(f"  📢 Канал: {dialog.entity.title}{username_str}{members_str}")
                                else:
                                    all_groups.append(entity_info)
                                    self.current_results['groups'].append(entity_info)  # Сохраняем в классе
                                    username_str = f" (@{entity_info['username']})" if entity_info['username'] else ""
                                    members_str = f" [{members_count:,} участников]" if members_count > 0 else ""
                                    print(f"  👥 Группа: {dialog.entity.title}{username_str}{members_str}")
                            elif isinstance(dialog.entity, Chat):
                                all_groups.append(entity_info)
                                self.current_results['groups'].append(entity_info)  # Сохраняем в классе
                                members_str = f" [{members_count:,} участников]" if members_count > 0 else ""
                                print(f"  👥 Группа: {dialog.entity.title}{members_str}")
                    except Exception as e2:
                        print(f"  ❌ Ошибка при альтернативном поиске: {e2}")
                    
            except Exception as e:
                print(f"  ❌ Ошибка при поиске '{keyword}': {e}")
            
            # Небольшая задержка между запросами (чтобы не получить flood wait)
            # Можно уменьшить до 0.5-1 секунды, но есть риск получить flood wait
            delay = getattr(self, 'search_delay', 1.0)
            if delay > 0:
                await asyncio.sleep(delay)
        
        print(f"\n✅ Поиск завершен!")
        print(f"   Найдено групп: {len(all_groups)}")
        print(f"   Найдено каналов: {len(all_channels)}")
        
        # Обновляем финальные результаты в классе
        self.current_results = {
            'groups': all_groups,
            'channels': all_channels
        }
        
        return {
            'groups': all_groups,
            'channels': all_channels
        }
    
    def save_to_excel(self, groups: List[Dict], channels: List[Dict], 
                     groups_file: str = 'telegram_groups.xlsx',
                     channels_file: str = 'telegram_channels.xlsx'):
        """
        Сохранение результатов в Excel файлы
        
        Args:
            groups: Список найденных групп
            channels: Список найденных каналов
            groups_file: Имя файла для групп
            channels_file: Имя файла для каналов
        """
        # Сохранение групп
        if groups:
            wb_groups = Workbook()
            ws_groups = wb_groups.active
            ws_groups.title = "Telegram Groups"
            
            # Заголовки
            headers = ['ID', 'Название', 'Username', 'Количество участников', 'Ключевое слово']
            ws_groups.append(headers)
            
            # Стилизация заголовков
            header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF")
            
            for cell in ws_groups[1]:
                cell.fill = header_fill
                cell.font = header_font
            
            # Данные
            for group in groups:
                members = group['members_count']
                # Форматируем количество участников
                if isinstance(members, int) and members > 0:
                    members_str = f"{members:,}".replace(',', ' ')  # Разделитель тысяч
                else:
                    members_str = str(members) if members else 'N/A'
                
                ws_groups.append([
                    group['id'],
                    group['title'],
                    group['username'] or 'N/A',
                    members_str,
                    group['keyword']
                ])
            
            # Автоматическая ширина колонок
            for column in ws_groups.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                ws_groups.column_dimensions[column_letter].width = adjusted_width
            
            wb_groups.save(groups_file)
            print(f"✅ Группы сохранены в: {groups_file}")
        else:
            print("⚠️ Группы не найдены")
        
        # Сохранение каналов
        if channels:
            wb_channels = Workbook()
            ws_channels = wb_channels.active
            ws_channels.title = "Telegram Channels"
            
            # Заголовки
            headers = ['ID', 'Название', 'Username', 'Количество подписчиков', 'Ключевое слово']
            ws_channels.append(headers)
            
            # Стилизация заголовков
            header_fill = PatternFill(start_color="70AD47", end_color="70AD47", fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF")
            
            for cell in ws_channels[1]:
                cell.fill = header_fill
                cell.font = header_font
            
            # Данные
            for channel in channels:
                members = channel['members_count']
                # Форматируем количество подписчиков
                if isinstance(members, int) and members > 0:
                    members_str = f"{members:,}".replace(',', ' ')  # Разделитель тысяч
                else:
                    members_str = str(members) if members else 'N/A'
                
                ws_channels.append([
                    channel['id'],
                    channel['title'],
                    channel['username'] or 'N/A',
                    members_str,
                    channel['keyword']
                ])
            
            # Автоматическая ширина колонок
            for column in ws_channels.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                ws_channels.column_dimensions[column_letter].width = adjusted_width
            
            wb_channels.save(channels_file)
            print(f"✅ Каналы сохранены в: {channels_file}")
        else:
            print("⚠️ Каналы не найдены")
    
    async def disconnect(self):
        """Отключение от Telegram"""
        await self.client.disconnect()
        print("👋 Отключено от Telegram")


async def main():
    """
    Основная функция для запуска поиска
    """
    # Попытка загрузить конфигурацию из config.py
    try:
        import config
        API_ID = config.API_ID
        API_HASH = config.API_HASH
        KEYWORDS = getattr(config, 'KEYWORDS', [])
        CITIES = getattr(config, 'CITIES', [])
        LIMIT_PER_KEYWORD = getattr(config, 'LIMIT_PER_KEYWORD', 50)
        USE_TRANSLITERATION = getattr(config, 'USE_TRANSLITERATION', True)
        USE_CITY_COMBINATIONS = getattr(config, 'USE_CITY_COMBINATIONS', True)
        SEARCH_DELAY = getattr(config, 'SEARCH_DELAY', 1.0)  # Задержка между запросами в секундах
        
        print("✅ Конфигурация загружена из config.py")
        
        # Генерируем поисковые запросы
        if USE_CITY_COMBINATIONS and CITIES:
            search_queries = TelegramSearcher.generate_search_queries(KEYWORDS, CITIES)
            print(f"📝 Сгенерировано {len(search_queries)} поисковых запросов из {len(KEYWORDS)} ключевых слов и {len(CITIES)} городов")
        elif USE_TRANSLITERATION:
            search_queries = TelegramSearcher.generate_search_queries(KEYWORDS)
            print(f"📝 Сгенерировано {len(search_queries)} поисковых запросов (с транслитерацией)")
        else:
            search_queries = KEYWORDS
            print(f"📝 Используется {len(search_queries)} ключевых слов (без комбинаций)")
        
    except ImportError:
        print("⚠️ Файл config.py не найден. Используются значения по умолчанию.")
        print("   Создайте config.py на основе config_example.py")
        # Значения по умолчанию (нужно заменить!)
        API_ID = 12345678  # ⚠️ Замените на ваш API ID
        API_HASH = 'your_api_hash_here'  # ⚠️ Замените на ваш API Hash
        KEYWORDS = ['python', 'programming', 'tech']
        CITIES = []
        LIMIT_PER_KEYWORD = 50
        USE_TRANSLITERATION = True
        USE_CITY_COMBINATIONS = False
        SEARCH_DELAY = 1.0
        search_queries = TelegramSearcher.generate_search_queries(KEYWORDS) if USE_TRANSLITERATION else KEYWORDS
    
    # Проверка корректности API credentials
    if API_ID == 12345678 or API_HASH == 'your_api_hash_here':
        print("\n❌ ОШИБКА: Необходимо настроить API_ID и API_HASH!")
        print("   1. Получите их на https://my.telegram.org")
        print("   2. Создайте config.py на основе config_example.py")
        print("   3. Заполните свои данные")
        return
    
    # Создание экземпляра поисковика
    try:
        searcher = TelegramSearcher(API_ID, API_HASH, search_delay=SEARCH_DELAY)
    except NameError:
        # Если SEARCH_DELAY не определен (старый config.py)
        searcher = TelegramSearcher(API_ID, API_HASH, search_delay=1.0)
    
    results = {'groups': [], 'channels': []}
    
    try:
        # Подключение
        await searcher.connect()
        
        # Поиск
        results = await searcher.search_channels_and_groups(search_queries, limit_per_keyword=LIMIT_PER_KEYWORD)
        
        # Сохранение в Excel
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        groups_file = f'telegram_groups_{timestamp}.xlsx'
        channels_file = f'telegram_channels_{timestamp}.xlsx'
        
        searcher.save_to_excel(
            results['groups'],
            results['channels'],
            groups_file,
            channels_file
        )
        
        print(f"\n🎉 Готово! Результаты сохранены в файлы:")
        print(f"   📁 {groups_file}")
        print(f"   📁 {channels_file}")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Поиск прерван пользователем (Ctrl+C)")
        print("💾 Сохраняю уже найденные результаты...")
        
        # Используем результаты из класса (они обновляются по мере поиска)
        saved_results = searcher.current_results
        
        if saved_results['groups'] or saved_results['channels']:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            groups_file = f'telegram_groups_interrupted_{timestamp}.xlsx'
            channels_file = f'telegram_channels_interrupted_{timestamp}.xlsx'
            
            searcher.save_to_excel(
                saved_results['groups'],
                saved_results['channels'],
                groups_file,
                channels_file
            )
            
            print(f"\n✅ Найденные результаты сохранены:")
            print(f"   📁 {groups_file} ({len(saved_results['groups'])} групп)")
            print(f"   📁 {channels_file} ({len(saved_results['channels'])} каналов)")
        else:
            print("⚠️ Результаты не найдены, сохранять нечего")
            print("   (Поиск был прерван до того, как что-то было найдено)")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        
        # Пытаемся сохранить результаты даже при ошибке
        saved_results = searcher.current_results
        if saved_results['groups'] or saved_results['channels']:
            print("\n💾 Пытаюсь сохранить найденные результаты...")
            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                groups_file = f'telegram_groups_error_{timestamp}.xlsx'
                channels_file = f'telegram_channels_error_{timestamp}.xlsx'
                
                searcher.save_to_excel(
                    saved_results['groups'],
                    saved_results['channels'],
                    groups_file,
                    channels_file
                )
                print(f"✅ Результаты сохранены в файлы с префиксом 'error_'")
            except Exception as save_error:
                print(f"❌ Не удалось сохранить результаты: {save_error}")
    
    finally:
        await searcher.disconnect()


if __name__ == '__main__':
    # Запуск асинхронной функции
    asyncio.run(main())

