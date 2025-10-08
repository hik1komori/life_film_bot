import logging
import sqlite3
import re
import asyncio
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, CallbackQueryHandler, filters
from config import BOT_TOKEN, ADMIN_IDS, ARCHIVE_CHANNEL_ID, REQUIRED_CHANNELS, CODES_CHANNEL

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для категорий
GENRES = [
    "Jangari", "Drama", "Komediya","Sarguzasht", 
    "Qorqinchli", "Tarixiy", "Klassika", "Fantastika", "Hayotiy",
    "Triller", "Detektiv", "Hujjatli_film", "Anime", "Kriminal",
    "Fentezi", "Afsona", "Vester", "Musiqiy"
]

COUNTRIES = [
    "Rossiya", "AQSH", "Turkiya", "Xitoy", "Hindiston", 
    "Avstraliya", "Buyuk_britaniya", "Janubiy_koreya", "Ukraina",
    "Qozogiston", "Fransiya", "Eron", "Yaponiya"
]

YEARS = [str(year) for year in range(2025, 2009, -1)]
QUALITIES = ["1080P", "720P", "480P", "4K"]
LANGUAGES = ["UZ", "RU", "EN", "TR", "KR", "CN"]

# БАЗА ДАННЫХ
class Database:
    def __init__(self, db_path="movies.db"):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Основные таблицы
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS movies (
                code TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                caption TEXT,
                title TEXT,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                views INTEGER DEFAULT 0,
                duration INTEGER DEFAULT 0,
                file_size INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_requests INTEGER DEFAULT 0,
                is_premium BOOLEAN DEFAULT FALSE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY,
                username TEXT,
                title TEXT,
                invite_link TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                is_private BOOLEAN DEFAULT FALSE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS movie_tags (
                code TEXT,
                tag_type TEXT,
                tag_value TEXT,
                FOREIGN KEY (code) REFERENCES movies (code),
                PRIMARY KEY (code, tag_type, tag_value)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER,
                movie_code TEXT,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (movie_code) REFERENCES movies (code),
                PRIMARY KEY (user_id, movie_code)
            )
        ''')
        
        # НОВЫЕ ТАБЛИЦЫ ДЛЯ УЛУЧШЕНИЙ
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ratings (
                user_id INTEGER,
                movie_code TEXT,
                rating INTEGER CHECK(rating >= 1 AND rating <= 5),
                review TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (movie_code) REFERENCES movies (code),
                PRIMARY KEY (user_id, movie_code)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS achievements (
                user_id INTEGER,
                achievement_type TEXT,
                achieved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                PRIMARY KEY (user_id, achievement_type)
            )
        ''')
        
        # НОВАЯ ТАБЛИЦА ДЛЯ ЖАЛОБ
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                movie_code TEXT,
                report_type TEXT,
                description TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                resolved_at DATETIME,
                resolved_by INTEGER,
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (movie_code) REFERENCES movies (code)
            )
        ''')
        
        # Добавляем каналы из config если их нет
        for channel_id, username in REQUIRED_CHANNELS.items():
            clean_username = username.strip()
            if not clean_username.startswith('@'):
                clean_username = '@' + clean_username
            
            cursor.execute(
                'INSERT OR IGNORE INTO channels (channel_id, username, title) VALUES (?, ?, ?)',
                (channel_id, clean_username, None)
            )
        
        conn.commit()
        conn.close()
        print("✅ Ma'lumotlar bazasi yangilandi")
    
    def update_database(self):
        """Обновляет структуру базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Добавляем новые колонки если их нет
        new_columns = [
            ("movies", "duration", "INTEGER DEFAULT 0"),
            ("movies", "file_size", "INTEGER DEFAULT 0"),
            ("users", "first_name", "TEXT"),
            ("users", "last_name", "TEXT"),
            ("users", "total_requests", "INTEGER DEFAULT 0"),
            ("users", "is_premium", "BOOLEAN DEFAULT FALSE"),
            ("channels", "is_active", "BOOLEAN DEFAULT TRUE"),
            ("channels", "is_private", "BOOLEAN DEFAULT FALSE")
        ]
        
        for table, column, col_type in new_columns:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                print(f"✅ Kolonna '{column}' qo'shildi")
            except sqlite3.OperationalError:
                pass
        
        # Обновляем существующие записи
        cursor.execute('UPDATE movies SET title = ? WHERE title IS NULL', ("Nomsiz film",))
        
        conn.commit()
        conn.close()

    def add_movie(self, code, file_id, caption=None, duration=0, file_size=0):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            title = self._extract_title(caption)
            
            cursor.execute('''
                INSERT OR REPLACE INTO movies (code, file_id, caption, title, duration, file_size) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (code, file_id, caption, title, duration, file_size))
            
            if caption:
                self._parse_and_add_tags(code, caption, cursor)
            
            conn.commit()
            print(f"✅ Video #{code} bazaga qo'shildi - Nomi: {title}")
            return True
        except Exception as e:
            print(f"❌ Videoni qo'shishda xato: {e}")
            return False
        finally:
            conn.close()

    def delete_movie(self, code):
        """Удаляет фильм из базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            # Сначала получаем информацию о фильме для лога
            cursor.execute('SELECT title FROM movies WHERE code = ?', (code,))
            movie = cursor.fetchone()
            
            if not movie:
                return False, "Film topilmadi"
            
            title = movie[0]
            
            # Удаляем связанные данные
            cursor.execute('DELETE FROM movie_tags WHERE code = ?', (code,))
            cursor.execute('DELETE FROM favorites WHERE movie_code = ?', (code,))
            cursor.execute('DELETE FROM ratings WHERE movie_code = ?', (code,))
            cursor.execute('DELETE FROM reports WHERE movie_code = ?', (code,))
            
            # Удаляем сам фильм
            cursor.execute('DELETE FROM movies WHERE code = ?', (code,))
            
            conn.commit()
            print(f"✅ Video #{code} bazadan o'chirildi - Nomi: {title}")
            return True, f"Film '{title}' (#{code}) o'chirildi"
            
        except Exception as e:
            print(f"❌ Filmlarni o'chirishda xato: {e}")
            return False, f"Xatolik: {str(e)}"
        finally:
            conn.close()
    
    def _extract_title(self, caption):
        """Извлекает название фильма из описания"""
        if not caption:
            return "Nomsiz film"
        
        # Сначала ищем хештег #nomi_Название
        nomi_match = re.search(r'#nomi[_:]?(\w+)', caption, re.IGNORECASE)
        if nomi_match:
            return nomi_match.group(1).strip()
        
        # Ищем хештег #nazar_Название
        nazar_match = re.search(r'#nazar[_:]?(\w+)', caption, re.IGNORECASE)
        if nazar_match:
            return nazar_match.group(1).strip()
        
        # Если нет хештегов, берем первую строку без хештегов
        clean_text = re.sub(r'#\w+', '', caption).strip()
        first_line = clean_text.split('\n')[0] if '\n' in clean_text else clean_text
        title = first_line.strip()
        
        if not title:
            return f"Video #{self._get_next_code()}"
        
        return title[:100]
    
    def _get_next_code(self):
        """Генерирует следующий код для безымянных фильмов"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM movies')
        count = cursor.fetchone()[0]
        conn.close()
        return f"VID{count + 1:04d}"
    
    def _parse_and_add_tags(self, code, caption, cursor):
        hashtags = re.findall(r'#(\w+)', caption)
        
        for tag in hashtags:
            tag_lower = tag.lower()
            tag_type = None
            tag_value = tag
            
            if tag_lower.startswith('nomi') or tag_lower.startswith('nazar'):
                tag_type = "title"
                tag_value = tag[5:] if len(tag) > 5 else tag
                cursor.execute('UPDATE movies SET title = ? WHERE code = ?', (tag_value, code))
                continue
            
            # УЛУЧШЕННЫЙ ПАРСИНГ ТЕГОВ - регистронезависимый поиск
            for genre in GENRES:
                if genre.lower() in tag_lower or tag_lower in genre.lower():
                    tag_type = "genre"
                    tag_value = genre  # Сохраняем оригинальное название
                    break
            
            if not tag_type:
                for country in COUNTRIES:
                    if country.lower() in tag_lower or tag_lower in country.lower():
                        tag_type = "country"
                        tag_value = country
                        break
            
            if not tag_type and tag.isdigit() and len(tag) == 4:
                if tag in YEARS:
                    tag_type = "year"
                    tag_value = tag
            
            if not tag_type:
                for quality in QUALITIES:
                    if quality.lower() in tag_lower or tag_lower in quality.lower():
                        tag_type = "quality"
                        tag_value = quality
                        break
            
            if not tag_type:
                for lang in LANGUAGES:
                    if lang.lower() in tag_lower or tag_lower in lang.lower():
                        tag_type = "language"
                        tag_value = lang
                        break
            
            if tag_type:
                cursor.execute(
                    'INSERT OR REPLACE INTO movie_tags (code, tag_type, tag_value) VALUES (?, ?, ?)',
                    (code, tag_type, tag_value)
                )

    # УЛУЧШЕННЫЙ ПОИСК
    def search_movies(self, query):
        """Улучшенный поиск: по коду, названию и хештегам"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Поиск по коду (точное совпадение)
        cursor.execute('SELECT code, title FROM movies WHERE code = ?', (query,))
        exact_code_match = cursor.fetchone()
        if exact_code_match:
            conn.close()
            return [exact_code_match]
        
        # Поиск по названию (частичное совпадение)
        cursor.execute('''
            SELECT code, title FROM movies 
            WHERE title LIKE ? OR code LIKE ?
            ORDER BY 
                CASE WHEN code = ? THEN 1
                     WHEN title LIKE ? THEN 2
                     ELSE 3
                END,
                views DESC
            LIMIT 10
        ''', (f'%{query}%', f'%{query}%', query, f'{query}%'))
        
        results = cursor.fetchall()
        conn.close()
        return results

    def search_movies_by_title(self, query, limit=10):
        """Поиск фильмов по названию"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT code, title FROM movies WHERE title LIKE ? ORDER BY views DESC LIMIT ?',
            (f'%{query}%', limit)
        )
        result = cursor.fetchall()
        conn.close()
        return result

    # УЛУЧШЕННЫЙ ПОИСК ПО КАТЕГОРИЯМ
    def get_movies_by_tag(self, tag_type, tag_value, limit=10, offset=0):
        """Улучшенный поиск фильмов по тегам - регистронезависимый"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Регистронезависимый поиск
        cursor.execute('''
            SELECT DISTINCT m.code, m.title 
            FROM movies m
            JOIN movie_tags mt ON m.code = mt.code
            WHERE mt.tag_type = ? AND LOWER(mt.tag_value) = LOWER(?)
            ORDER BY m.added_date DESC
            LIMIT ? OFFSET ?
        ''', (tag_type, tag_value, limit, offset))
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_movies_count_by_tag(self, tag_type, tag_value):
        """Улучшенный подсчет фильмов по тегам - регистронезависимый"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(DISTINCT m.code)
            FROM movies m
            JOIN movie_tags mt ON m.code = mt.code
            WHERE mt.tag_type = ? AND LOWER(mt.tag_value) = LOWER(?)
        ''', (tag_type, tag_value))
        result = cursor.fetchone()[0]
        conn.close()
        return result

    # НОВЫЕ МЕТОДЫ ДЛЯ УЛУЧШЕНИЙ
    
    def log_user_activity(self, user_id, action, details=None):
        """Логирует действия пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO user_activity_logs (user_id, action, details) VALUES (?, ?, ?)',
            (user_id, action, details)
        )
        conn.commit()
        conn.close()
    
    def add_user(self, user_id, username=None, first_name=None, last_name=None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)',
            (user_id, username, first_name, last_name)
        )
        conn.commit()
        conn.close()
    
    def update_user_activity(self, user_id):
        """Обновляет активность пользователя и счетчик запросов"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE users SET last_activity = CURRENT_TIMESTAMP, total_requests = total_requests + 1 WHERE user_id = ?',
            (user_id,)
        )
        conn.commit()
        conn.close()
    
    def add_rating(self, user_id, movie_code, rating, review=None):
        """Добавляет оценку фильму"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT OR REPLACE INTO ratings (user_id, movie_code, rating, review) VALUES (?, ?, ?, ?)',
                (user_id, movie_code, rating, review)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Reyting qo'shishda xato: {e}")
            return False
        finally:
            conn.close()
    
    def get_movie_rating(self, movie_code):
        """Получает средний рейтинг фильма"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT AVG(rating), COUNT(*) FROM ratings WHERE movie_code = ?',
            (movie_code,)
        )
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0] is not None:
            return float(result[0]), result[1]
        return 0.0, 0
    
    def get_user_rating(self, user_id, movie_code):
        """Получает оценку пользователя для фильма"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT rating, review FROM ratings WHERE user_id = ? AND movie_code = ?',
            (user_id, movie_code)
        )
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_random_movie(self):
        """Получает случайный фильм"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT code, title FROM movies ORDER BY RANDOM() LIMIT 1'
        )
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_popular_movies(self, limit=10):
        """Получает популярные фильмы"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT code, title, views FROM movies ORDER BY views DESC LIMIT ?',
            (limit,)
        )
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_daily_active_users(self):
        """Получает количество активных пользователей за сегодня"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COUNT(DISTINCT user_id) FROM user_activity_logs WHERE DATE(created_at) = DATE("now")'
        )
        result = cursor.fetchone()[0]
        conn.close()
        return result
    
    def get_user_stats(self, user_id):
        """Получает статистику пользователя"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM favorites WHERE user_id = ?', (user_id,))
        favorites_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM ratings WHERE user_id = ?', (user_id,))
        ratings_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT joined_at, total_requests FROM users WHERE user_id = ?', (user_id,))
        user_info = cursor.fetchone()
        
        conn.close()
        
        return {
            'favorites_count': favorites_count,
            'ratings_count': ratings_count,
            'joined_at': user_info[0] if user_info else None,
            'total_requests': user_info[1] if user_info else 0
        }

    # СИСТЕМА ЖАЛОБ
    def add_report(self, user_id, movie_code, report_type, description=None):
        """Добавляет жалобу на фильм"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT INTO reports (user_id, movie_code, report_type, description) VALUES (?, ?, ?, ?)',
                (user_id, movie_code, report_type, description)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Shikoyat qo'shishda xato: {e}")
            return False
        finally:
            conn.close()
    
    def get_pending_reports(self):
        """Получает все необработанные жалобы"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.id, r.user_id, r.movie_code, r.report_type, r.description, r.created_at,
                   u.username, u.first_name, m.title
            FROM reports r
            LEFT JOIN users u ON r.user_id = u.user_id
            LEFT JOIN movies m ON r.movie_code = m.code
            WHERE r.status = 'pending'
            ORDER BY r.created_at DESC
        ''')
        result = cursor.fetchall()
        conn.close()
        return result
    
    def resolve_report(self, report_id, admin_id):
        """Помечает жалобу как решенную"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                'UPDATE reports SET status = "resolved", resolved_at = CURRENT_TIMESTAMP, resolved_by = ? WHERE id = ?',
                (admin_id, report_id)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Shikoyatni hal qilishda xato: {e}")
            return False
        finally:
            conn.close()
    
    def get_reports_count(self):
        """Получает количество жалоб"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM reports WHERE status = "pending"')
        pending_count = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM reports')
        total_count = cursor.fetchone()[0]
        conn.close()
        return pending_count, total_count

    # КАНАЛЫ - ОБНОВЛЕННЫЕ МЕТОДЫ
    def get_all_channels(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT channel_id, username, title, invite_link, is_private FROM channels WHERE is_active = TRUE')
        result = cursor.fetchall()
        conn.close()
        return result
    
    def add_channel(self, channel_id, username=None, title=None, invite_link=None, is_private=False):
        """Добавляет канал в базу данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                'INSERT OR REPLACE INTO channels (channel_id, username, title, invite_link, is_private) VALUES (?, ?, ?, ?, ?)',
                (channel_id, username, title, invite_link, is_private)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Kanal qo'shishda xato: {e}")
            return False
        finally:
            conn.close()
    
    def delete_channel(self, channel_id):
        """Удаляет канал из базы данных"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('DELETE FROM channels WHERE channel_id = ?', (channel_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Kanalni o'chirishda xato: {e}")
            return False
        finally:
            conn.close()

    # СУЩЕСТВУЮЩИЕ МЕТОДЫ (обновленные)
    def get_movie(self, code):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT code, file_id, caption, title, duration, file_size FROM movies WHERE code = ?', (code,))
        result = cursor.fetchone()
        conn.close()
        return result
    
    def get_all_users(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, username, first_name, last_name, joined_at, total_requests FROM users')
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_users_count(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def increment_views(self, movie_code):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE movies SET views = views + 1 WHERE code = ?', (movie_code,))
        conn.commit()
        conn.close()
    
    def get_top_movies(self, limit=10, offset=0, min_views=100):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT code, title, views 
            FROM movies 
            WHERE views >= ?
            ORDER BY views DESC
            LIMIT ? OFFSET ?
        ''', (min_views, limit, offset))
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_top_movies_count(self, min_views=100):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM movies WHERE views >= ?', (min_views,))
        result = cursor.fetchone()[0]
        conn.close()
        return result
    
    def get_recent_movies_by_years(self, years_range, limit=10, offset=0):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(years_range))
        cursor.execute(f'''
            SELECT m.code, m.title 
            FROM movies m
            JOIN movie_tags mt ON m.code = mt.code
            WHERE mt.tag_type = 'year' AND mt.tag_value IN ({placeholders})
            ORDER BY m.added_date DESC
            LIMIT ? OFFSET ?
        ''', years_range + [limit, offset])
        
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_recent_movies_count_by_years(self, years_range):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(years_range))
        cursor.execute(f'''
            SELECT COUNT(DISTINCT m.code)
            FROM movies m
            JOIN movie_tags mt ON m.code = mt.code
            WHERE mt.tag_type = 'year' AND mt.tag_value IN ({placeholders})
        ''', years_range)
        
        result = cursor.fetchone()[0]
        conn.close()
        return result

    def add_to_favorites(self, user_id, movie_code):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute('INSERT OR IGNORE INTO favorites (user_id, movie_code) VALUES (?, ?)', 
                         (user_id, movie_code))
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Избранноега qo'shishda xato: {e}")
            return False
        finally:
            conn.close()
    
    def remove_from_favorites(self, user_id, movie_code):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM favorites WHERE user_id = ? AND movie_code = ?', 
                     (user_id, movie_code))
        conn.commit()
        conn.close()
        return True
    
    def get_favorites(self, user_id, limit=10, offset=0):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.code, m.title 
            FROM movies m
            JOIN favorites f ON m.code = f.movie_code
            WHERE f.user_id = ?
            ORDER BY f.added_date DESC
            LIMIT ? OFFSET ?
        ''', (user_id, limit, offset))
        result = cursor.fetchall()
        conn.close()
        return result
    
    def get_favorites_count(self, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM favorites WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()[0]
        conn.close()
        return result
    
    def is_favorite(self, user_id, movie_code):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM favorites WHERE user_id = ? AND movie_code = ?', 
                     (user_id, movie_code))
        result = cursor.fetchone() is not None
        conn.close()
        return result

    def get_all_movies(self, limit=50, offset=0):
        """Получает все фильмы с пагинацией"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT code, title 
            FROM movies 
            ORDER BY added_date DESC
            LIMIT ? OFFSET ?
        ''', (limit, offset))
        result = cursor.fetchall()
        conn.close()
        return result

    def get_all_movies_count(self):
        """Получает общее количество фильмов"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM movies')
        result = cursor.fetchone()[0]
        conn.close()
        return result

db = Database()
db.update_database()

# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет подписку на все каналы с обработкой приватных каналов"""
    channels = db.get_all_channels()
    not_subscribed = []
    
    if not channels:
        return []  # Нет каналов для подписки
    
    for channel_id, username, title, invite_link, is_private in channels:
        try:
            if is_private:
                # Для приватных каналов просто проверяем наличие invite_link
                if not invite_link:
                    not_subscribed.append((channel_id, username, title, invite_link, is_private))
                    continue
                
                # Пытаемся проверить подписку через get_chat_member
                try:
                    member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
                    if member.status in ['left', 'kicked']:
                        not_subscribed.append((channel_id, username, title, invite_link, is_private))
                except Exception as e:
                    # Если не можем проверить через get_chat_member, считаем что пользователь не подписан
                    not_subscribed.append((channel_id, username, title, invite_link, is_private))
            else:
                # Для публичных каналов стандартная проверка
                member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
                if member.status in ['left', 'kicked']:
                    not_subscribed.append((channel_id, username, title, invite_link, is_private))
                    
        except Exception as e:
            logger.warning(f"Kanal {channel_id} tekshirishda xato: {e}")
            not_subscribed.append((channel_id, username, title, invite_link, is_private))
    
    return not_subscribed

async def require_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет подписку перед выполнением действия"""
    user = update.effective_user
    if not user:
        return False
    
    if user.id in ADMIN_IDS:
        return True
    
    db.update_user_activity(user.id)
    db.log_user_activity(user.id, "subscription_check")
    
    not_subscribed = await check_subscription(user.id, context)
    
    if not_subscribed:
        await show_subscription_required(update, context, not_subscribed)
        return False
    
    return True

async def show_subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE, not_subscribed_channels):
    """Показывает требования подписки с поддержкой приватных каналов"""
    if not not_subscribed_channels:
        return True
    
    keyboard = []
    for channel_id, username, title, invite_link, is_private in not_subscribed_channels:
        channel_name = title or username or f"Kanal {channel_id}"
        
        if is_private and invite_link:
            # Для приватных каналов используем invite_link
            url = invite_link
            button_text = f"🔒 {channel_name} (Maxfiy kanal)"
        elif invite_link:
            # Для публичных каналов с invite_link
            url = invite_link
            button_text = f"📢 {channel_name}"
        else:
            # Для публичных каналов без invite_link
            clean_username = (username or '').lstrip('@')
            if clean_username:
                url = f"https://t.me/{clean_username}"
                button_text = f"📢 {channel_name}"
            else:
                # Если нет ни username ни invite_link, пропускаем
                continue
        
        keyboard.append([InlineKeyboardButton(button_text, url=url)])
    
    keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_subscription")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📢 Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n\n"
    for channel_id, username, title, invite_link, is_private in not_subscribed_channels:
        channel_name = title or username or f"Kanal {channel_id}"
        if is_private:
            text += f"• 🔒 {channel_name} (Maxfiy kanal - invite link orqali)\n"
        else:
            text += f"• 📢 {channel_name}\n"
    
    try:
        if update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, reply_markup=reply_markup)
        return False
    except Exception as e:
        logger.error(f"Obunani ko'rsatish xatosi: {e}")
        return False

# КЛАВИАТУРЫ
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🔍 Kod orqali qidirish"), KeyboardButton("🎬 Kategoriyalar")],
        [KeyboardButton("📊 Yangi filmlar (2020-2025)"), KeyboardButton("🏆 Top filmlar")],
        [KeyboardButton("⭐ Tasodifiy film"), KeyboardButton("❤️ Mening filmlarim")],
        [KeyboardButton("ℹ️ Yordam")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_main_menu_inline_keyboard():
    """Inline клавиатура для главного меню"""
    keyboard = [
        [InlineKeyboardButton("🔍 Kod orqali qidirish", callback_data="search_by_code")],
        [InlineKeyboardButton("🎬 Kategoriyalar", callback_data="categories")],
        [InlineKeyboardButton("📊 Yangi filmlar (2020-2025)", callback_data="recent_movies_0")],
        [InlineKeyboardButton("🏆 Top filmlar", callback_data="top_movies_0")],
        [InlineKeyboardButton("⭐ Tasodifiy film", callback_data="random_movie")],
        [InlineKeyboardButton("❤️ Mening filmlarim", callback_data="favorites_0")],
        [InlineKeyboardButton("ℹ️ Yordam", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_search_keyboard():
    """Клавиатура для поиска"""
    keyboard = [
        [KeyboardButton("🔍 Kod bo'yicha"), KeyboardButton("🔍 Nomi bo'yicha")],
        [KeyboardButton("🔙 Bosh menyu")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_movie_keyboard(user_id, movie_code):
    is_fav = db.is_favorite(user_id, movie_code)
    favorite_text = "❌ Olib tashlash" if is_fav else "❤️ Saqlash"
    
    user_rating = db.get_user_rating(user_id, movie_code)
    rating_text = "⭐ Baholash" if not user_rating else "✏️ Bahoni o'zgartirish"
    
    keyboard = [
        [InlineKeyboardButton(favorite_text, callback_data=f"fav_{movie_code}")],
        [InlineKeyboardButton(rating_text, callback_data=f"rate_{movie_code}")],
        [InlineKeyboardButton("⚠️ Shikoyat qilish", callback_data=f"report_{movie_code}")],
        [InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_rating_keyboard(movie_code):
    keyboard = [
        [
            InlineKeyboardButton("1⭐", callback_data=f"rating_{movie_code}_1"),
            InlineKeyboardButton("2⭐", callback_data=f"rating_{movie_code}_2"),
            InlineKeyboardButton("3⭐", callback_data=f"rating_{movie_code}_3"),
            InlineKeyboardButton("4⭐", callback_data=f"rating_{movie_code}_4"),
            InlineKeyboardButton("5⭐", callback_data=f"rating_{movie_code}_5")
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data=f"back_to_movie_{movie_code}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_report_keyboard(movie_code):
    keyboard = [
        [
            InlineKeyboardButton("❌ Noto'g'ri video", callback_data=f"report_type_{movie_code}_wrong"),
            InlineKeyboardButton("📛 Hakoratli", callback_data=f"report_type_{movie_code}_offensive")
        ],
        [
            InlineKeyboardButton("⚖️ Mualliflik huquqi", callback_data=f"report_type_{movie_code}_copyright"),
            InlineKeyboardButton("🔞 18+ kontent", callback_data=f"report_type_{movie_code}_adult")
        ],
        [
            InlineKeyboardButton("📉 Sifat past", callback_data=f"report_type_{movie_code}_quality"),
            InlineKeyboardButton("🚫 Boshqa sabab", callback_data=f"report_type_{movie_code}_other")
        ],
        [InlineKeyboardButton("🔙 Orqaga", callback_data=f"back_to_movie_{movie_code}")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_categories_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎭 Janrlar", callback_data="category_genre")],
        [InlineKeyboardButton("🌎 Davlatlar", callback_data="category_country")],
        [InlineKeyboardButton("🗓️ Yillar", callback_data="category_year")],
        [InlineKeyboardButton("📹 Sifat", callback_data="category_quality")],
        [InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_genres_keyboard():
    """Клавиатура для выбора жанров"""
    keyboard = []
    row = []
    
    for i, genre in enumerate(GENRES):
        row.append(InlineKeyboardButton(genre, callback_data=f"select_genre_{genre}"))
        if len(row) == 2 or i == len(GENRES) - 1:
            keyboard.append(row)
            row = []
    
    keyboard.append([InlineKeyboardButton("🔙 Kategoriyalar", callback_data="categories")])
    return InlineKeyboardMarkup(keyboard)

def get_countries_keyboard():
    """Клавиатура для выбора стран"""
    keyboard = []
    row = []
    
    for i, country in enumerate(COUNTRIES):
        row.append(InlineKeyboardButton(country, callback_data=f"select_country_{country}"))
        if len(row) == 2 or i == len(COUNTRIES) - 1:
            keyboard.append(row)
            row = []
    
    keyboard.append([InlineKeyboardButton("🔙 Kategoriyalar", callback_data="categories")])
    return InlineKeyboardMarkup(keyboard)

def get_years_keyboard():
    """Клавиатура для выбора годов"""
    keyboard = []
    row = []
    
    for i, year in enumerate(YEARS):
        row.append(InlineKeyboardButton(year, callback_data=f"select_year_{year}"))
        if len(row) == 3 or i == len(YEARS) - 1:
            keyboard.append(row)
            row = []
    
    keyboard.append([InlineKeyboardButton("🔙 Kategoriyalar", callback_data="categories")])
    return InlineKeyboardMarkup(keyboard)

def get_qualities_keyboard():
    """Клавиатура для выбора качества"""
    keyboard = []
    row = []
    
    for i, quality in enumerate(QUALITIES):
        row.append(InlineKeyboardButton(quality, callback_data=f"select_quality_{quality}"))
        if len(row) == 2 or i == len(QUALITIES) - 1:
            keyboard.append(row)
            row = []
    
    keyboard.append([InlineKeyboardButton("🔙 Kategoriyalar", callback_data="categories")])
    return InlineKeyboardMarkup(keyboard)

def get_movies_list_keyboard(movies, page, total_pages, callback_prefix):
    keyboard = []
    
    for code, title in movies:
        display_title = title[:35] + "..." if len(title) > 35 else title
        keyboard.append([InlineKeyboardButton(f"🎬 {display_title}", callback_data=f"download_{code}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"{callback_prefix}_{page-1}"))
    
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="current_page"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"{callback_prefix}_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Orqaga", callback_data="categories")])
    
    return InlineKeyboardMarkup(keyboard)

def get_search_results_keyboard(movies):
    """Клавиатура для результатов поиска"""
    keyboard = []
    
    for code, title in movies:
        display_title = title[:30] + "..." if len(title) > 30 else title
        keyboard.append([InlineKeyboardButton(f"🎬 {display_title}", callback_data=f"download_{code}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Qidiruv menyusi", callback_data="search_menu")])
    
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton("🎬 Filmlar", callback_data="admin_movies_0")],
        [InlineKeyboardButton("🗑️ Filmlarni o'chirish", callback_data="admin_delete_movies_0")],
        [InlineKeyboardButton("📢 Kanallar", callback_data="admin_channels")],
        [InlineKeyboardButton("⚠️ Shikoyatlar", callback_data="admin_reports_0")],
        [InlineKeyboardButton("📈 Analytics", callback_data="admin_analytics")],
        [InlineKeyboardButton("📨 Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🔙 Bosh menyu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_movies_keyboard(movies, page, total_pages, delete_mode=False):
    """Клавиатура для админ-панели управления фильмами с улучшенной пагинацией"""
    keyboard = []
    
    for code, title in movies:
        display_title = title[:30] + "..." if len(title) > 30 else title
        if delete_mode:
            keyboard.append([
                InlineKeyboardButton(f"🎬 {display_title}", callback_data=f"admin_movie_info_{code}"),
                InlineKeyboardButton("❌", callback_data=f"admin_delete_{code}")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(f"🎬 {display_title}", callback_data=f"admin_movie_info_{code}")
            ])
    
    # Улучшенная пагинация
    nav_buttons = []
    
    # Первая страница
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⏪ 1", callback_data=f"admin_movies_{0}" if not delete_mode else f"admin_delete_movies_{0}"))
    
    # Предыдущая страница
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"admin_movies_{page-1}" if not delete_mode else f"admin_delete_movies_{page-1}"))
    
    # Текущая страница
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="current_page"))
    
    # Следующая страница
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"admin_movies_{page+1}" if not delete_mode else f"admin_delete_movies_{page+1}"))
    
    # Последняя страница
    if page < total_pages - 2:
        nav_buttons.append(InlineKeyboardButton(f"{total_pages} ⏩", callback_data=f"admin_movies_{total_pages-1}" if not delete_mode else f"admin_delete_movies_{total_pages-1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    # Кнопки действий
    action_buttons = []
    if not delete_mode:
        action_buttons.append(InlineKeyboardButton("🗑️ O'chirish rejimi", callback_data="admin_delete_movies_0"))
    else:
        action_buttons.append(InlineKeyboardButton("📋 Ko'rish rejimi", callback_data="admin_movies_0"))
    
    action_buttons.append(InlineKeyboardButton("🔙 Admin panel", callback_data="main_menu"))
    keyboard.append(action_buttons)
    
    return InlineKeyboardMarkup(keyboard)

def get_admin_delete_confirmation_keyboard(movie_code):
    """Клавиатура подтверждения удаления фильма"""
    keyboard = [
        [
            InlineKeyboardButton("✅ HA, o'chirish", callback_data=f"admin_confirm_delete_{movie_code}"),
            InlineKeyboardButton("❌ BEKOR QILISH", callback_data="admin_delete_movies_0")
        ],
        [InlineKeyboardButton("🔙 Admin panel", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_reports_keyboard(reports, page, total_pages):
    """Клавиатура для управления жалобами с улучшенной пагинацией"""
    keyboard = []
    
    for report_id, user_id, movie_code, report_type, description, created_at, username, first_name, title in reports:
        user_display = f"@{username}" if username else first_name
        report_text = f"#{report_id} {user_display} - {title[:20]}..."
        keyboard.append([
            InlineKeyboardButton(report_text, callback_data=f"admin_report_info_{report_id}"),
            InlineKeyboardButton("✅", callback_data=f"admin_resolve_report_{report_id}")
        ])
    
    # Улучшенная пагинация
    nav_buttons = []
    
    # Первая страница
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⏪ 1", callback_data=f"admin_reports_{0}"))
    
    # Предыдущая страница
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"admin_reports_{page-1}"))
    
    # Текущая страница
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="current_page"))
    
    # Следующая страница
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"admin_reports_{page+1}"))
    
    # Последняя страница
    if page < total_pages - 2:
        nav_buttons.append(InlineKeyboardButton(f"{total_pages} ⏩", callback_data=f"admin_reports_{total_pages-1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([InlineKeyboardButton("🔙 Admin panel", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(keyboard)

# ОСНОВНЫЕ ФУНКЦИИ
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    db.update_user_activity(user.id)
    db.log_user_activity(user.id, "start_command")
    
    if user.id in ADMIN_IDS:
        await update.message.reply_text(
            "👨‍💻 Admin paneliga xush kelibsiz!",
            reply_markup=get_admin_keyboard()
        )
        return
    
    if not await require_subscription(update, context):
        return
    
    await update.message.reply_text(
        f"🎬 Xush kelibsiz, {user.first_name}!\n\n"
        "Video yuklab olish uchun quyidagi imkoniyatlardan foydalaning:",
        reply_markup=get_main_keyboard()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.update_user_activity(user.id)
    
    if user.id not in ADMIN_IDS:
        if not await require_subscription(update, context):
            return
    
    text = update.message.text.strip()
    db.log_user_activity(user.id, "message", text)
    
    if text == "🔍 Kod orqali qidirish":
        await update.message.reply_text(
            "🔍 Video yuklab olish uchun kodni kiriting:\n\n"
            "Misol: <code>AVATAR2024</code> yoki <code>12345</code>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Bosh menyu")]], resize_keyboard=True)
        )
        context.user_data['search_mode'] = 'code'
        return
    
    elif text == "🔍 Nomi bo'yicha":
        await update.message.reply_text(
            "🔍 Film nomini kiriting:\n\n"
            "Misol: <code>Avatar</code> yoki <code>Dune</code>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Bosh menyu")]], resize_keyboard=True)
        )
        context.user_data['search_mode'] = 'title'
        return
    
    elif text == "🔍 Kod bo'yicha":
        await update.message.reply_text(
            "🔍 Video yuklab olish uchun kodni kiriting:\n\n"
            "Misol: <code>AVATAR2024</code> yoki <code>12345</code>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("🔙 Bosh menyu")]], resize_keyboard=True)
        )
        context.user_data['search_mode'] = 'code'
        return
    
    elif text == "🎬 Kategoriyalar":
        await update.message.reply_text(
            "Qidiruv turini tanlang:",
            reply_markup=get_categories_keyboard()
        )
        return
    
    elif text == "📊 Yangi filmlar (2020-2025)":
        await show_recent_movies(update, context)
        return
    
    elif text == "🏆 Top filmlar":
        await show_top_movies(update, context)
        return
    
    elif text == "⭐ Tasodifiy film":
        await send_random_movie(update, context)
        return
    
    elif text == "❤️ Mening filmlarim":
        await show_favorites(update, context)
        return
    
    elif text == "ℹ️ Yordam":
        await show_help(update, context)
        return
    
    elif text == "🔙 Bosh menyu":
        await update.message.reply_text(
            "Bosh menyu:",
            reply_markup=get_main_keyboard()
        )
        return
    
    elif text == "🔙 Qidiruv menyusi":
        await update.message.reply_text(
            "Qidiruv turini tanlang:",
            reply_markup=get_search_keyboard()
        )
        return
    
    # Обработка поискового запроса
    search_mode = context.user_data.get('search_mode')
    
    if search_mode == 'code':
        # Поиск по коду (точное совпадение)
        if text.isdigit() or re.match(r'^[a-zA-Z0-9]+$', text):
            await send_movie_to_user(update, context, text, user.id)
        else:
            await update.message.reply_text(
                "❌ Noto'g'ri format! Faqat raqamlar va harflardan foydalaning.\n"
                "Misol: <code>AVATAR2024</code> yoki <code>12345</code>",
                parse_mode="HTML"
            )
    
    elif search_mode == 'title':
        # Поиск по названию
        await search_movies(update, context, text)
    
    else:
        # Универсальный поиск (и по коду, и по названию)
        await universal_search(update, context, text)

async def universal_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    """Универсальный поиск по коду и названию"""
    movies = db.search_movies(query)
    
    if not movies:
        await update.message.reply_text(
            f"❌ '{query}' so'rovi bo'yicha hech narsa topilmadi\n\n"
            "Qidiruvni aniqroq qilish uchun:\n"
            "• Kod bo'yicha qidirish - aniq kod kiriting\n"
            "• Nomi bo'yicha qidirish - film nomini kiriting",
            reply_markup=get_search_keyboard()
        )
        return
    
    if len(movies) == 1:
        # Если найден только один результат, сразу показываем фильм
        code, title = movies[0]
        await send_movie_to_user(update, context, code, update.effective_user.id)
    else:
        # Если несколько результатов, показываем список
        await show_search_results(update, context, movies, query)

async def search_movies(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    """Поиск фильмов по названию"""
    movies = db.search_movies_by_title(query)
    
    if not movies:
        await update.message.reply_text(
            f"❌ '{query}' so'rovi bo'yicha hech narsa topilmadi\n\n"
            "Qidiruvni aniqroq qilish uchun:\n"
            "• To'liq film nomini yozing\n"
            "• Kalit so'zlardan foydalaning\n"
            "• Kod bo'yicha qidirishni sinab ko'ring",
            reply_markup=get_search_keyboard()
        )
        return
    
    await show_search_results(update, context, movies, query)

async def show_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, movies, query):
    """Показывает результаты поиска"""
    text = f"🔍 '{query}' bo'yicha qidiruv natijalari:\n\n"
    
    for i, (code, title) in enumerate(movies[:10], 1):  # Ограничиваем 10 результатами
        text += f"{i}. 🎬 {title}\n   🔗 Kod: {code}\n\n"
    
    if len(movies) > 10:
        text += f"... va yana {len(movies) - 10} ta film\n\n"
    
    text += "Filmlardan birini tanlang:"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=get_search_results_keyboard(movies))
    else:
        await update.message.reply_text(text, reply_markup=get_search_results_keyboard(movies))

# ИСПРАВЛЕННЫЙ ОБРАБОТЧИК CALLBACK
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    db.update_user_activity(user.id)
    db.log_user_activity(user.id, "callback", data)
    
    if user.id not in ADMIN_IDS:
        if not await require_subscription(update, context):
            return
    
    # Основные обработчики
    if data == "main_menu":
        if user.id in ADMIN_IDS:
            await query.edit_message_text("👨‍💻 Admin paneli:", reply_markup=get_admin_keyboard())
        else:
            await query.edit_message_text("Bosh menyu:", reply_markup=get_main_menu_inline_keyboard())
    
    elif data == "search_menu":
        await query.edit_message_text("Qidiruv turini tanlang:", reply_markup=get_search_keyboard())
    
    elif data == "categories":
        await query.edit_message_text("Qidiruv turini tanlang:", reply_markup=get_categories_keyboard())
    
    elif data == "search_by_code":
        await query.edit_message_text(
            "🔍 Video yuklab olish uchun kodni kiriting:\n\n"
            "Misol: <code>AVATAR2024</code> yoki <code>12345</code>",
            parse_mode="HTML"
        )
        context.user_data['search_mode'] = 'code'
    
    elif data == "random_movie":
        await send_random_movie(update, context)
    
    elif data == "help":
        await show_help(update, context)
    
    # УЛУЧШЕННЫЕ ОБРАБОТЧИКИ КАТЕГОРИЙ
    elif data == "category_genre":
        await query.edit_message_text("🎭 Janrni tanlang:", reply_markup=get_genres_keyboard())
    
    elif data == "category_country":
        await query.edit_message_text("🌎 Davlatni tanlang:", reply_markup=get_countries_keyboard())
    
    elif data == "category_year":
        await query.edit_message_text("🗓️ Yilni tanlang:", reply_markup=get_years_keyboard())
    
    elif data == "category_quality":
        await query.edit_message_text("📹 Sifatni tanlang:", reply_markup=get_qualities_keyboard())
    
    elif data.startswith("select_"):
        parts = data.split("_")
        if len(parts) >= 3:
            category_type = parts[1]
            category_value = parts[2]
            await show_movies_by_category(query, category_type, category_value)
        else:
            await query.answer("❌ Noto'g'ri so'rov", show_alert=True)
    
    elif data.startswith("category_page_"):
        parts = data.split("_")
        if len(parts) >= 5:
            category_type = parts[2]
            category_value = parts[3]
            page = int(parts[4])
            await show_movies_by_category(query, category_type, category_value, page)
        else:
            await query.answer("❌ Noto'g'ri so'rov", show_alert=True)
    
    elif data.startswith("recent_movies_"):
        page = int(data.split("_")[2])
        await show_recent_movies(update, context, page)
    
    elif data.startswith("top_movies_"):
        page = int(data.split("_")[2])
        await show_top_movies(update, context, page)
    
    elif data.startswith("favorites_"):
        page = int(data.split("_")[1])
        await show_favorites(update, context, page)
    
    elif data.startswith("download_"):
        movie_code = data.split("_")[1]
        success = await send_movie_to_user(update, context, movie_code, user.id)
        if not success:
            await query.answer("❌ Videoni yuborishda xato", show_alert=True)
    
    elif data.startswith("fav_"):
        movie_code = data.split("_")[1]
        
        if db.is_favorite(user.id, movie_code):
            db.remove_from_favorites(user.id, movie_code)
            await query.answer("❌ Film olib tashlandi")
        else:
            db.add_to_favorites(user.id, movie_code)
            await query.answer("❤️ Film saqlandi")
        
        # Обновляем кнопки после изменения
        movie = db.get_movie(movie_code)
        if movie:
            code, file_id, caption, title, duration, file_size = movie
            movie_info = await format_movie_info(movie_code, user.id)
            await query.edit_message_text(
                movie_info,
                reply_markup=get_movie_keyboard(user.id, movie_code)
            )
    
    elif data.startswith("rate_"):
        movie_code = data.split("_")[1]
        await show_rating_options(query, movie_code)
    
    elif data.startswith("rating_"):
        parts = data.split("_")
        movie_code = parts[1]
        rating = int(parts[2])
        
        db.add_rating(user.id, movie_code, rating)
        await query.answer(f"✅ {rating} baho qo'yildi!")
        
        # Возвращаемся к информации о фильме с обновленным рейтингом
        movie_info = await format_movie_info(movie_code, user.id)
        await query.edit_message_text(
            movie_info,
            reply_markup=get_movie_keyboard(user.id, movie_code)
        )
    
    # ИСПРАВЛЕННЫЙ ОБРАБОТЧИК ЖАЛОБ
    elif data.startswith("report_"):
        movie_code = data.split("_")[1]
        movie = db.get_movie(movie_code)
        if not movie:
            await query.answer("❌ Film topilmadi", show_alert=True)
            return
        await show_report_options(query, movie_code)
    
    elif data.startswith("report_type_"):
        parts = data.split("_")
        if len(parts) >= 4:
            movie_code = parts[2]
            report_type = parts[3]
            
            # Проверяем существование фильма
            movie = db.get_movie(movie_code)
            if not movie:
                await query.answer("❌ Film topilmadi", show_alert=True)
                return
            
            # Сохраняем тип жалобы в контексте
            context.user_data['current_report'] = {
                'movie_code': movie_code,
                'report_type': report_type
            }
            
            await query.edit_message_text(
                f"⚠️ Shikoyat turi: {get_report_type_name(report_type)}\n\n"
                "Qo'shimcha izoh yozing (ixtiyoriy):\n\n"
                "Misol: <i>Video sifat yomon, to'liq ko'rinmayapti</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚫 Izohsiz yuborish", callback_data=f"report_submit_{movie_code}")],
                    [InlineKeyboardButton("🔙 Orqaga", callback_data=f"back_to_movie_{movie_code}")]
                ])
            )
        else:
            await query.answer("❌ Noto'g'ri so'rov", show_alert=True)
    
    elif data.startswith("report_submit_"):
        parts = data.split("_")
        if len(parts) >= 3:
            movie_code = parts[2]
            report_data = context.user_data.get('current_report', {})
            
            # Проверяем существование фильма
            movie = db.get_movie(movie_code)
            if not movie:
                await query.answer("❌ Film topilmadi", show_alert=True)
                return
            
            if report_data.get('movie_code') == movie_code:
                report_type = report_data.get('report_type')
                description = report_data.get('description')
                
                success = db.add_report(user.id, movie_code, report_type, description)
                if success:
                    await query.edit_message_text(
                        "✅ Shikoyatingiz qabul qilindi!\n\n"
                        "Administratorlar tez orada ko'rib chiqishadi.\n"
                        "Hisobingizga e'tiboringiz uchun rahmat!",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Orqaga", callback_data=f"back_to_movie_{movie_code}")]
                        ])
                    )
                else:
                    await query.answer("❌ Shikoyat yuborishda xato", show_alert=True)
            
            # Очищаем контекст
            if 'current_report' in context.user_data:
                del context.user_data['current_report']
        else:
            await query.answer("❌ Noto'g'ri so'rov", show_alert=True)
    
    elif data.startswith("back_to_movie_"):
        parts = data.split("_")
        if len(parts) >= 4:
            movie_code = parts[3]
            await send_movie_details(query, movie_code, user.id)
        else:
            await query.answer("❌ Noto'g'ri so'rov", show_alert=True)
    
    elif data == "check_subscription":
        await check_subscription_callback(update, context)
    
    # АДМИН ФУНКЦИИ
    elif data == "admin_stats":
        await show_admin_stats(query)
    elif data.startswith("admin_movies_"):
        page = int(data.split("_")[2])
        await show_admin_movies(query, page)
    elif data.startswith("admin_delete_movies_"):
        page = int(data.split("_")[3])
        await show_admin_movies(query, page, delete_mode=True)
    elif data.startswith("admin_delete_"):
        movie_code = data.split("_")[2]
        await show_delete_confirmation(query, movie_code)
    elif data.startswith("admin_confirm_delete_"):
        movie_code = data.split("_")[3]
        await delete_movie_confirmed(query, movie_code)
    elif data.startswith("admin_movie_info_"):
        movie_code = data.split("_")[3]
        await show_admin_movie_info(query, movie_code)
    elif data.startswith("admin_reports_"):
        page = int(data.split("_")[2])
        await show_admin_reports(query, page)
    elif data.startswith("admin_report_info_"):
        report_id = int(data.split("_")[3])
        await show_admin_report_info(query, report_id)
    elif data.startswith("admin_resolve_report_"):
        report_id = int(data.split("_")[3])
        await resolve_report_confirmed(query, report_id)
    elif data == "admin_channels":
        await show_admin_channels(query)
    elif data == "admin_analytics":
        await show_admin_analytics(query)
    elif data == "admin_broadcast":
        await query.message.reply_text("📨 Xabar yuborish uchun xabarga javob bering: /broadcast")

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    not_subscribed = await check_subscription(user.id, context)
    
    if not not_subscribed:
        await query.message.reply_text(
            "✅ Ajoyib! Endi siz botdan foydalanishingiz mumkin.",
            reply_markup=get_main_keyboard()
        )
    else:
        await show_subscription_required(update, context, not_subscribed)

# НОВАЯ ФУНКЦИЯ ДЛЯ ФОРМАТИРОВАНИЯ ИНФОРМАЦИИ О ФИЛЬМЕ
async def format_movie_info(movie_code, user_id):
    """Форматирует информацию о фильме для отображения"""
    movie = db.get_movie(movie_code)
    if not movie:
        return "❌ Film topilmadi"
    
    code, file_id, caption, title, duration, file_size = movie
    avg_rating, rating_count = db.get_movie_rating(movie_code)
    user_rating = db.get_user_rating(user_id, movie_code)
    
    movie_info = f"🎬 **{title}**\n\n"
    
    if avg_rating > 0:
        movie_info += f"⭐ **Reyting:** {avg_rating:.1f}/5 ({rating_count} baho)\n"
    
    if user_rating:
        rating, review = user_rating
        movie_info += f"📝 **Sizning bahoingiz:** {rating} ⭐\n"
    
    if duration and duration > 0:
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        movie_info += f"⏱ **Davomiylik:** {hours:02d}:{minutes:02d}\n"
    
    if file_size and file_size > 0:
        size_mb = file_size / (1024 * 1024)
        movie_info += f"📦 **Hajmi:** {size_mb:.1f} MB\n"
    
    movie_info += f"\n🔗 **Kod:** `{code}`"
    
    return movie_info

# НОВЫЕ ФУНКЦИИ ДЛЯ СИСТЕМЫ ЖАЛОБ
def get_report_type_name(report_type):
    """Возвращает читаемое название типа жалобы"""
    report_types = {
        'wrong': "❌ Noto'g'ri video",
        'offensive': "📛 Hakoratli kontent",
        'copyright': "⚖️ Mualliflik huquqi",
        'adult': "🔞 18+ kontent",
        'quality': "📉 Sifat past",
        'other': "🚫 Boshqa sabab"
    }
    return report_types.get(report_type, "Noma'lum")

async def show_report_options(query, movie_code):
    """Показывает опции для жалобы"""
    movie_info = await format_movie_info(movie_code, query.from_user.id)
    text = f"⚠️ **FILMGA SHIKOYAT** ⚠️\n\n{movie_info}\n\nShikoyat turini tanlang:"
    
    await query.edit_message_text(text, reply_markup=get_report_keyboard(movie_code))

async def show_admin_reports(query, page=0):
    """Показывает список жалоб для админа"""
    limit = 10
    offset = page * limit
    
    reports = db.get_pending_reports()
    total_count = len(reports)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    
    if not reports:
        await query.edit_message_text(
            "✅ Hozircha shikoyatlar yo'q",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin panel", callback_data="main_menu")]])
        )
        return
    
    # Берем только нужную страницу
    page_reports = reports[offset:offset + limit]
    
    pending_count, total_count_all = db.get_reports_count()
    
    text = f"⚠️ **Shikoyatlar** (Sahifa {page+1}/{total_pages})\n\n"
    text += f"📊 Jami: {total_count_all} ta\n"
    text += f"⏳ Ko'rib chiqilishi kerak: {pending_count} ta\n\n"
    
    for i, report in enumerate(page_reports, offset + 1):
        report_id, user_id, movie_code, report_type, description, created_at, username, first_name, title = report
        user_display = f"@{username}" if username else first_name
        text += f"{i}. **#{report_id}** {user_display}\n"
        text += f"   🎬 {title}\n"
        text += f"   📝 {get_report_type_name(report_type)}\n\n"
    
    await query.edit_message_text(text, reply_markup=get_admin_reports_keyboard(page_reports, page, total_pages))

async def show_admin_report_info(query, report_id):
    """Показывает детальную информацию о жалобе"""
    reports = db.get_pending_reports()
    report = next((r for r in reports if r[0] == report_id), None)
    
    if not report:
        await query.answer("❌ Shikoyat topilmadi", show_alert=True)
        return
    
    report_id, user_id, movie_code, report_type, description, created_at, username, first_name, title = report
    user_display = f"@{username}" if username else first_name
    
    text = f"⚠️ **SHIKOYAT MA'LUMOTLARI** ⚠️\n\n"
    text += f"🆔 **ID:** #{report_id}\n"
    text += f"👤 **Foydalanuvchi:** {user_display} (ID: {user_id})\n"
    text += f"🎬 **Film:** {title}\n"
    text += f"🔗 **Kod:** {movie_code}\n"
    text += f"📝 **Turi:** {get_report_type_name(report_type)}\n"
    text += f"📅 **Sana:** {created_at}\n\n"
    
    if description:
        text += f"📄 **Izoh:**\n{description}\n\n"
    else:
        text += "📄 **Izoh:** Yo'q\n\n"
    
    text += "Shikoyatni hal qilganingizda, uni arxivlang:"
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Hal qilindi", callback_data=f"admin_resolve_report_{report_id}"),
            InlineKeyboardButton("🗑️ Filmlarni o'chirish", callback_data=f"admin_delete_{movie_code}")
        ],
        [InlineKeyboardButton("🔙 Shikoyatlar ro'yxati", callback_data="admin_reports_0")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def resolve_report_confirmed(query, report_id):
    """Подтверждает решение жалобы"""
    success = db.resolve_report(report_id, query.from_user.id)
    
    if success:
        await query.edit_message_text(
            f"✅ Shikoyat #{report_id} hal qilindi!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Shikoyatlar ro'yxati", callback_data="admin_reports_0")]])
        )
    else:
        await query.edit_message_text(
            f"❌ Shikoyatni hal qilishda xato!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Shikoyatlar ro'yxati", callback_data="admin_reports_0")]])
        )

# НОВЫЕ АДМИН ФУНКЦИИ ДЛЯ УДАЛЕНИЯ ФИЛЬМОВ
async def show_admin_movies(query, page=0, delete_mode=False):
    """Показывает список фильмов в админ-панели с улучшенной пагинацией"""
    limit = 10
    offset = page * limit
    
    movies = db.get_all_movies(limit, offset)
    total_count = db.get_all_movies_count()
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    
    if not movies:
        await query.edit_message_text(
            "📭 Hozircha filmlar mavjud emas",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin panel", callback_data="main_menu")]])
        )
        return
    
    if delete_mode:
        text = f"🗑️ **Filmlarni o'chirish** (Sahifa {page+1}/{total_pages})\n\n"
        text += "Quyidagi filmlardan birini o'chirishingiz mumkin:\n\n"
    else:
        text = f"🎬 **Barcha filmlar** (Sahifa {page+1}/{total_pages})\n\n"
        text += f"Jami filmlar: {total_count} ta\n\n"
    
    for i, (code, title) in enumerate(movies, offset + 1):
        text += f"{i}. 🎬 {title}\n   🔗 Kod: {code}\n\n"
    
    await query.edit_message_text(text, reply_markup=get_admin_movies_keyboard(movies, page, total_pages, delete_mode))

async def show_delete_confirmation(query, movie_code):
    """Показывает подтверждение удаления фильма"""
    movie = db.get_movie(movie_code)
    if not movie:
        await query.answer("❌ Film topilmadi", show_alert=True)
        return
    
    code, file_id, caption, title, duration, file_size = movie
    
    text = f"⚠️ **FILMNI O'CHIRISH** ⚠️\n\n"
    text += f"🎬 **Film:** {title}\n"
    text += f"🔗 **Kod:** {code}\n"
    
    # Исправленная строка - безопасное получение рейтинга
    avg_rating, rating_count = db.get_movie_rating(movie_code)
    text += f"📊 **Ko'rishlar:** {rating_count}\n\n"
    
    text += "❌ **Diqqat! Bu amalni ortga qaytarib bo'lmaydi!**\n"
    text += "Film butunlay o'chib ketadi.\n\n"
    text += "Rostan ham o'chirmoqchimisiz?"
    
    await query.edit_message_text(text, reply_markup=get_admin_delete_confirmation_keyboard(movie_code))

async def delete_movie_confirmed(query, movie_code):
    """Удаляет фильм после подтверждения"""
    success, message = db.delete_movie(movie_code)
    
    if success:
        await query.edit_message_text(
            f"✅ {message}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Filmlar ro'yxati", callback_data="admin_delete_movies_0")]])
        )
    else:
        await query.edit_message_text(
            f"❌ {message}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Filmlar ro'yxati", callback_data="admin_delete_movies_0")]])
        )

async def show_admin_movie_info(query, movie_code):
    """Показывает детальную информацию о фильме для админа"""
    movie = db.get_movie(movie_code)
    if not movie:
        await query.answer("❌ Film topilmadi", show_alert=True)
        return
    
    code, file_id, caption, title, duration, file_size = movie
    
    # ИСПРАВЛЕННАЯ СТРОКА - безопасное форматирование рейтинга
    avg_rating, rating_count = db.get_movie_rating(movie_code)
    
    # Получаем количество пользователей, добавивших в избранное
    favorites_count = sum(1 for user in db.get_all_users() if db.is_favorite(user[0], movie_code))
    
    text = f"🎬 **Film ma'lumotlari**\n\n"
    text += f"📝 **Nomi:** {title}\n"
    text += f"🔗 **Kodi:** {code}\n"
    
    # Безопасное отображение рейтинга
    if avg_rating > 0:
        text += f"⭐ **Reyting:** {avg_rating:.1f} ({rating_count} baho)\n"
    else:
        text += f"⭐ **Reyting:** Baho yo'q\n"
        
    text += f"❤️ **Saqlangan:** {favorites_count} marta\n"
    text += f"👁️ **Ko'rishlar:** {rating_count}\n"
    
    if duration and duration > 0:
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        text += f"⏱ **Davomiylik:** {hours:02d}:{minutes:02d}\n"
    
    if file_size and file_size > 0:
        size_mb = file_size / (1024 * 1024)
        text += f"📦 **Hajmi:** {size_mb:.1f} MB\n"
    
    if caption:
        text += f"\n📄 **Tavsif:**\n{caption[:200]}..."
    
    keyboard = [
        [InlineKeyboardButton("🗑️ O'chirish", callback_data=f"admin_delete_{movie_code}")],
        [InlineKeyboardButton("🔙 Filmlar ro'yxati", callback_data="admin_movies_0")]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

# ОСТАЛЬНЫЕ ФУНКЦИИ (без изменений)
async def send_random_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет случайный фильм"""
    random_movie = db.get_random_movie()
    
    if not random_movie:
        if update.callback_query:
            await update.callback_query.message.reply_text("❌ Hozircha filmlar mavjud emas")
        else:
            await update.message.reply_text("❌ Hozircha filmlar mavjud emas")
        return
    
    code, title = random_movie
    
    if update.callback_query:
        await send_movie_to_user(update, context, code, update.callback_query.from_user.id)
    else:
        await send_movie_to_user(update, context, code, update.effective_user.id)

async def show_rating_options(query, movie_code):
    """Показывает опции для оценки фильма"""
    movie_info = await format_movie_info(movie_code, query.from_user.id)
    text = f"{movie_info}\n\nFilmini baholang:"
    
    await query.edit_message_text(text, reply_markup=get_rating_keyboard(movie_code))

async def send_movie_details(query, movie_code, user_id):
    """Отправляет детали фильма с обновленной информацией"""
    movie_info = await format_movie_info(movie_code, user_id)
    await query.edit_message_text(movie_info, reply_markup=get_movie_keyboard(user_id, movie_code))

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает расширенную помощь"""
    help_text = f"""
🤖 Botdan foydalanish bo'yicha ko'rsatma:

🔍 **Qidirish:**
• Kod orqali qidirish - aniq video kodini kiriting
• Nomi bo'yicha qidirish - film nomini kiriting
• Kategoriyalar - janr, davlat, yil bo'yicha qidiring

📊 **Ko'rish:**
• Yangi filmlar (2020-2025) - so'nggi yillardagi yangi filmlar
• Top filmlar - eng ko'p ko'rilgan filmlar
• Tasodifiy film - tasodifiy filmni ko'rish

❤️ **Shaxsiy:**
• Mening filmlarim - saqlangan filmlaringiz
• Baholash - filmlarni baholashingiz mumkin
• Shikoyat qilish - muammoli filmlarni xabar bering

⚡ **Tez buyruqlar:**
• /random - tasodifiy film
• /top - eng mashhur filmlar  
• /stats - shaxsiy statistika

📺 Barcha video kodlari: {CODES_CHANNEL}

🎯 **Qidiruv bo'yicha maslahatlar:**
• Kod bo'yicha: AVATAR2024, 12345
• Nomi bo'yicha: Avatar, Dune, O'zbek filmi
• Xususiy belgilar: #nomi_Avatar, #nazar_FilmNomi
    """
    
    if update.callback_query:
        await update.callback_query.message.reply_text(help_text)
    else:
        await update.message.reply_text(help_text)

# УЛУЧШЕННЫЕ ФУНКЦИИ КАТЕГОРИЙ
async def show_category_options(query, category_type):
    """Показывает опции для выбранной категории"""
    if category_type == "genre":
        await query.edit_message_text("🎭 Janrni tanlang:", reply_markup=get_genres_keyboard())
    elif category_type == "country":
        await query.edit_message_text("🌎 Davlatni tanlang:", reply_markup=get_countries_keyboard())
    elif category_type == "year":
        await query.edit_message_text("🗓️ Yilni tanlang:", reply_markup=get_years_keyboard())
    elif category_type == "quality":
        await query.edit_message_text("📹 Sifatni tanlang:", reply_markup=get_qualities_keyboard())
    else:
        await query.answer("❌ Noto'g'ri kategoriya", show_alert=True)

async def show_movies_by_category(query, category_type, category_value, page=0):
    """Показывает фильмы по выбранной категории"""
    limit = 5
    offset = page * limit
    
    movies = db.get_movies_by_tag(category_type, category_value, limit, offset)
    total_count = db.get_movies_count_by_tag(category_type, category_value)
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    
    if not movies:
        await query.edit_message_text(
            f"❌ '{category_value}' bo'yicha videolar topilmadi",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data=f"category_{category_type}")]])
        )
        return
    
    text = f"🎬 {category_value} bo'yicha videolar (Sahifa {page+1}/{total_pages}):\n\n"
    
    for code, title in movies:
        text += f"🎬 {title}\n🔗 Kod: {code}\n\n"
    
    keyboard = get_movies_list_keyboard(movies, page, total_pages, f"category_{category_type}_{category_value}")
    
    await query.edit_message_text(text, reply_markup=keyboard)

async def show_recent_movies(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    limit = 5
    offset = page * limit
    years_range = [str(year) for year in range(2020, 2026)]
    
    movies = db.get_recent_movies_by_years(years_range, limit, offset)
    total_count = db.get_recent_movies_count_by_years(years_range)
    total_pages = (total_count + limit - 1) // limit
    
    if not movies:
        if update.callback_query:
            await update.callback_query.edit_message_text("📭 2020-2025 yillardagi filmlar topilmadi")
        else:
            await update.message.reply_text("📭 2020-2025 yillardagi filmlar topilmadi")
        return
    
    text = f"📊 Yangi filmlar 2020-2025 (Sahifa {page+1}/{total_pages}):\n\n"
    
    for code, title in movies:
        text += f"🎬 {title}\n🔗 Kod: {code}\n\n"
    
    keyboard = get_movies_list_keyboard(movies, page, total_pages, "recent_movies")
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

async def show_top_movies(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    limit = 5
    offset = page * limit
    min_views = 100
    
    movies = db.get_top_movies(limit, offset, min_views)
    total_count = db.get_top_movies_count(min_views)
    total_pages = (total_count + limit - 1) // limit
    
    if not movies:
        if update.callback_query:
            await update.callback_query.edit_message_text("🏆 Hozircha top filmlar yo'q (minimal 100 ko'rish)")
        else:
            await update.message.reply_text("🏆 Hozircha top filmlar yo'q (minimal 100 ko'rish)")
        return
    
    text = f"🏆 Top filmlar (Sahifa {page+1}/{total_pages}):\n\n"
    
    for code, title, views in movies:
        text += f"🎬 {title}\n👁️ Ko'rishlar: {views}\n🔗 Kod: {code}\n\n"
    
    keyboard = get_movies_list_keyboard([(code, title) for code, title, views in movies], page, total_pages, "top_movies")
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    user = update.effective_user
    limit = 5
    offset = page * limit
    
    movies = db.get_favorites(user.id, limit, offset)
    total_count = db.get_favorites_count(user.id)
    total_pages = (total_count + limit - 1) // limit
    
    if not movies:
        if update.callback_query:
            await update.callback_query.edit_message_text("❤️ Sizda saqlangan filmlar yo'q")
        else:
            await update.message.reply_text("❤️ Sizda saqlangan filmlar yo'q")
        return
    
    text = f"❤️ Mening filmlarim (Sahifa {page+1}/{total_pages}):\n\n"
    
    for code, title in movies:
        text += f"🎬 {title}\n🔗 Kod: {code}\n\n"
    
    keyboard = get_movies_list_keyboard(movies, page, total_pages, "favorites")
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, reply_markup=keyboard)

# ИСПРАВЛЕННАЯ ФУНКЦИЯ ОТПРАВКИ ФИЛЬМА
async def send_movie_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, movie_code, user_id):
    """Отправляет фильм пользователю с кнопками"""
    movie = db.get_movie(movie_code)
    if not movie:
        try:
            if update.callback_query:
                await update.callback_query.answer("❌ Film topilmadi", show_alert=True)
            else:
                await update.message.reply_text(f"❌ #{movie_code} kodli video topilmadi")
        except:
            pass
        return False
    
    code, file_id, caption, title, duration, file_size = movie
    
    try:
        # Сначала отправляем видео
        if caption:
            message_caption = caption
        else:
            message_caption = f"🎬 {title}\n\nKod: #{code}"
        
        # Отправляем видео
        await context.bot.send_video(
            chat_id=user_id,
            video=file_id,
            caption=message_caption,
            protect_content=True
        )
        
        # Затем отправляем информацию о фильме с кнопками
        movie_info = await format_movie_info(movie_code, user_id)
        await context.bot.send_message(
            chat_id=user_id,
            text=movie_info,
            reply_markup=get_movie_keyboard(user_id, movie_code)
        )
        
        # Обновляем статистику
        db.increment_views(code)
        db.log_user_activity(user_id, "watch_movie", movie_code)
        
        # Проверяем достижения
        favorites_count = db.get_favorites_count(user_id)
        if favorites_count >= 10 and not any(ach[0] == "film_lover" for ach in db.get_user_achievements(user_id)):
            db.add_achievement(user_id, "film_lover")
        
        return True
        
    except Exception as e:
        logger.error(f"Videoni yuborishda xato: {e}")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ Videoni yuborishda xato. Iltimos, keyinroq urunib ko'ring."
            )
        except:
            pass
        return False

# ДОБАВЛЕННЫЕ ФУНКЦИИ КОМАНД
async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет канал в базу данных"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args and len(context.args) >= 2:
        try:
            channel_id = int(context.args[0])
            username = context.args[1]
            title = context.args[2] if len(context.args) > 2 else None
            invite_link = context.args[3] if len(context.args) > 3 else None
            is_private = context.args[4].lower() == 'true' if len(context.args) > 4 else False
            
            success = db.add_channel(channel_id, username, title, invite_link, is_private)
            
            if success:
                await update.message.reply_text(f"✅ Kanal {username} qo'shildi!")
            else:
                await update.message.reply_text("❌ Kanal qo'shishda xato")
        except ValueError:
            await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak")
    else:
        await update.message.reply_text(
            "❌ Foydalanish: /addchannel <id> <@username> [nomi] [invite_link] [private]\n\n"
            "Misol: /addchannel -100123456789 @my_channel \"Mening kanalim\" https://t.me/my_channel\n"
            "Maxfiy kanal: /addchannel -100123456789 @my_channel \"Maxfiy\" https://t.me/+abc123 true"
        )

async def add_private_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавляет приватный канал"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args and len(context.args) >= 2:
        try:
            channel_id = int(context.args[0])
            invite_link = context.args[1]
            title = context.args[2] if len(context.args) > 2 else f"Maxfiy kanal {channel_id}"
            
            success = db.add_channel(channel_id, None, title, invite_link, True)
            
            if success:
                await update.message.reply_text(f"✅ Maxfiy kanal {title} qo'shildi!")
            else:
                await update.message.reply_text("❌ Kanal qo'shishda xato")
        except ValueError:
            await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak")
    else:
        await update.message.reply_text(
            "❌ Foydalanish: /addprivatechannel <id> <invite_link> [nomi]\n\n"
            "Misol: /addprivatechannel -100123456789 https://t.me/+abc123 \"Mening maxfiy kanalim\""
        )

async def delete_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет канал из базы данных"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args:
        try:
            channel_id = int(context.args[0])
            success = db.delete_channel(channel_id)
            
            if success:
                await update.message.reply_text("✅ Kanal o'chirildi!")
            else:
                await update.message.reply_text("❌ Kanalni o'chirishda xato")
        except ValueError:
            await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak")
    else:
        await update.message.reply_text("❌ Kanal ID sini ko'rsating: /deletechannel <id>")

async def delete_movie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для удаления фильма по коду"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if context.args:
        movie_code = context.args[0]
        success, message = db.delete_movie(movie_code)
        
        if success:
            await update.message.reply_text(f"✅ {message}")
        else:
            await update.message.reply_text(f"❌ {message}")
    else:
        await update.message.reply_text(
            "❌ Foydalanish: /deletemovie <kod>\n\n"
            "Misol: /deletemovie AVATAR2024\n"
            "Yoki admin panel orqali o'chirishingiz mumkin"
        )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка сообщения всем пользователям"""
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    if update.message.reply_to_message:
        # Если ответ на сообщение
        message_to_send = update.message.reply_to_message
        users = db.get_all_users()
        total_users = len(users)
        success_count = 0
        failed_count = 0
        
        status_message = await update.message.reply_text(
            f"📨 Xabar yuborish boshlandi...\n"
            f"👥 Jami foydalanuvchilar: {total_users}\n"
            f"✅ Muvaffaqiyatli: 0\n"
            f"❌ Muvaffaqiyatsiz: 0"
        )
        
        for user_data in users:
            user_id = user_data[0]
            try:
                await message_to_send.copy(chat_id=user_id)
                success_count += 1
                
                # Обновляем статус каждые 10 сообщений
                if success_count % 10 == 0:
                    await status_message.edit_text(
                        f"📨 Xabar yuborish davom etmoqda...\n"
                        f"👥 Jami foydalanuvchilar: {total_users}\n"
                        f"✅ Muvaffaqiyatli: {success_count}\n"
                        f"❌ Muvaffaqiyatsiz: {failed_count}"
                    )
                
                # Небольшая задержка чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.1)
                
            except Exception as e:
                failed_count += 1
                logger.error(f"Xabar yuborishda xato {user_id}: {e}")
        
        # Финальное сообщение
        await status_message.edit_text(
            f"✅ Xabar yuborish yakunlandi!\n\n"
            f"👥 Jami foydalanuvchilar: {total_users}\n"
            f"✅ Muvaffaqiyatli: {success_count}\n"
            f"❌ Muvaffaqiyatsiz: {failed_count}"
        )
    else:
        await update.message.reply_text(
            "📨 Xabar yuborish uchun xabarga javob bering:\n\n"
            "1. Xabar yozing (matn, rasm, video)\n"
            "2. Xabarga javob bering: /broadcast"
        )

async def random_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для случайного фильма"""
    await send_random_movie(update, context)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для личной статистики"""
    user = update.effective_user
    user_stats = db.get_user_stats(user.id)
    achievements = db.get_user_achievements(user.id)
    
    text = f"📊 {user.first_name}, sizning statistikangiz:\n\n"
    text += f"❤️ Saqlangan filmlar: {user_stats['favorites_count']}\n"
    text += f"⭐ Baholangan filmlar: {user_stats['ratings_count']}\n"
    text += f"🔍 Umumiy so'rovlar: {user_stats['total_requests']}\n"
    
    if user_stats['joined_at']:
        try:
            join_date = datetime.datetime.strptime(user_stats['joined_at'], '%Y-%m-%d %H:%M:%S')
            days_ago = (datetime.datetime.now() - join_date).days
            text += f"📅 Botda: {days_ago} kun\n"
        except:
            pass
    
    if achievements:
        text += f"\n🏆 Sizning yutuqlaringiz:\n"
        for achievement_type, achieved_at in achievements:
            if achievement_type == "film_lover":
                text += f"• 🎬 Film Sevargisi (10+ saqlangan film)\n"
    else:
        text += f"\n🎯 Yutuqlar: Hali yo'q. Filmlarni saqlashni va baholashni davom eting!"
    
    await update.message.reply_text(text)

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для топ фильмов"""
    await show_top_movies(update, context)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для поиска"""
    if context.args:
        query = ' '.join(context.args)
        await universal_search(update, context, query)
    else:
        await update.message.reply_text(
            "🔍 Qidirish uchun film nomi yoki kodini kiriting:\n\n"
            "Misol: /search Avatar\n"
            "Yoki: /search AVATAR2024",
            reply_markup=get_search_keyboard()
        )

# АДМИН ФУНКЦИИ
async def handle_admin_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик видео для админов"""
    if not update.message or not update.effective_user:
        return
    
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    
    message = update.message
    caption = message.caption or ""
    
    code_match = re.search(r'#(\w+)', caption)
    if not code_match:
        await message.reply_text("❌ Izohda #123 formatida kod ko'rsating")
        return
    
    code = code_match.group(1)
    
    file_id = None
    duration = 0
    file_size = 0
    
    if message.video:
        file_id = message.video.file_id
        duration = message.video.duration or 0
        file_size = message.video.file_size or 0
    elif message.document and message.document.mime_type and 'video' in message.document.mime_type:
        file_id = message.document.file_id
        file_size = message.document.file_size or 0
    
    if not file_id:
        await message.reply_text("❌ Xabar video faylni o'z ichiga olmaydi")
        return
    
    try:
        if message.video:
            await context.bot.send_video(
                chat_id=ARCHIVE_CHANNEL_ID,
                video=file_id,
                caption=caption
            )
        else:
            await context.bot.send_document(
                chat_id=ARCHIVE_CHANNEL_ID,
                document=file_id,
                caption=caption
            )
        
        if db.add_movie(code, file_id, caption, duration, file_size):
            await message.reply_text(f"✅ Video #{code} qo'shildi va nashr qilindi!")
        else:
            await message.reply_text("❌ Bazaga qo'shishda xato")
            
    except Exception as e:
        await message.reply_text(f"❌ Nashr qilishda xato: {e}")

async def show_admin_stats(query):
    movies_count = db.get_all_movies_count()
    users_count = db.get_users_count()
    channels_count = len(db.get_all_channels())
    daily_users = db.get_daily_active_users()
    pending_reports, total_reports = db.get_reports_count()
    
    text = f"""📊 **Admin statistikasi:**

🎬 **Filmlar:** {movies_count}
👥 **Foydalanuvchilar:** {users_count}
📢 **Kanallar:** {channels_count}
📈 **Kunlik aktiv:** {daily_users}
⚠️ **Shikoyatlar:** {pending_reports}/{total_reports}

**Kanallar ro'yxati:**"""
    
    channels = db.get_all_channels()
    for channel_id, username, title, invite_link, is_private in channels:
        channel_type = "🔒 Maxfiy" if is_private else "📢 Ochiq"
        text += f"\n• {channel_type} {title or username or f'Kanal {channel_id}'}"
    
    keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_admin_analytics(query):
    """Показывает расширенную аналитику"""
    popular_movies = db.get_popular_movies(5)
    total_requests = sum(user[5] for user in db.get_all_users() if user[5] is not None)
    
    text = "📈 **Batafsil analitika:**\n\n"
    text += f"📊 **Jami so'rovlar:** {total_requests}\n\n"
    text += "🏆 **Eng mashhur filmlar:**\n"
    
    for i, (code, title, views) in enumerate(popular_movies, 1):
        text += f"{i}. {title} - {views} ko'rish\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_admin_channels(query):
    channels = db.get_all_channels()
    
    text = "📢 **Kanallar ro'yxati:**\n\n"
    if channels:
        for channel_id, username, title, invite_link, is_private in channels:
            channel_type = "🔒 Maxfiy" if is_private else "📢 Ochiq"
            text += f"• {channel_type} {title or username or f'Kanal {channel_id}'}\n"
            if invite_link:
                text += f"  🔗 Link: {invite_link}\n"
            text += f"  🆔 ID: {channel_id}\n\n"
    else:
        text += "📭 Hozircha kanallar yo'q\n"
    
    text += "\n**Kanal qo'shish:** /addchannel <id> <@username> [nomi] [invite_link] [private]"
    text += "\n**Maxfiy kanal qo'shish:** /addprivatechannel <id> <invite_link> [nomi]"
    text += "\n**Kanal o'chirish:** /deletechannel <id>"
    
    keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addchannel", add_channel_command))
    application.add_handler(CommandHandler("addprivatechannel", add_private_channel_command))
    application.add_handler(CommandHandler("deletechannel", delete_channel_command))
    application.add_handler(CommandHandler("deletemovie", delete_movie_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("random", random_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("top", top_command))
    application.add_handler(CommandHandler("search", search_command))
    
    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(
        (filters.VIDEO | filters.Document.ALL) & filters.CAPTION,
        handle_admin_video
    ))
    
    # Обработчики callback-кнопок
    application.add_handler(CallbackQueryHandler(handle_callback, pattern="^.*$"))
    
    print("🤖 Bot ishga tushdi!")
    print("✅ Barcha funksiyalar ishga tushirildi:")
    print("   • 🔐 Avtomatik obuna tekshiruvi (privat kanallar bilan)")
    print("   • 👨‍💻 Admin paneli (Yaxshilangan paginatsiya)")
    print("   • 📨 Xabar yuborish")
    print("   • 📢 Kanal boshqaruvi (privat kanallar qo'shildi)")
    print("   • ⭐ Reyting tizimi")
    print("   • 🔍 Kengaytirilgan qidiruv (Tuzatilgan kategoriyalar)")
    print("   • 🎯 Tasodifiy film")
    print("   • 📊 Batafsil statistika")
    print("   • 🏆 Achievement tizimi")
    print("   • 🎬 Universal qidiruv (kod va nom bo'yicha)")
    print("   • 🗑️ Filmlarni o'chirish (admin)")
    print("   • ⚠️ Shikoyat tizimi (Tuzatilgan)")
    print("   • ❌ KOLLEKSIYALAR O'CHIRILDI")
    print("   • 🎯 Tuzatilgan video yuborish (knopkalar bilan)")
    print("   • 🎭 Tuzatilgan kategoriyalar (Melodrama va boshqalar)")
    
    application.run_polling()



    
if __name__ == "__main__":
    main()
