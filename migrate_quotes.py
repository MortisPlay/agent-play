"""
Скрипт миграции: импортирует старые цитаты из корневого quote_stats.json
в SQLite и data/quote_stats.json, объединяя с существующими данными.
"""
import json
from pathlib import Path
import sqlite3

# Пути
BASE_DIR = Path(__file__).resolve().parent
LEGACY_PATH = BASE_DIR / "quote_stats.json"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "bot_data.sqlite3"
STATS_PATH = DATA_DIR / "quote_stats.json"


def main():
    # 1. Загружаем существующие данные из SQLite
    existing: dict[str, dict] = {}
    if DB_PATH.exists():
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute("SELECT key, payload FROM quote_stats").fetchall()
                existing = {key: json.loads(payload) for key, payload in rows if isinstance(payload, str)}
        except Exception as e:
            print(f"Ошибка загрузки из SQLite: {e}")

    print(f"Загружено из SQLite: {len(existing)} записей")

    # 2. Загружаем из data/quote_stats.json (если есть)
    if STATS_PATH.exists():
        try:
            with STATS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, value in data.items():
                    if key not in existing:
                        existing[key] = value
        except Exception as e:
            print(f"Ошибка загрузки из {STATS_PATH}: {e}")

    print(f"После data/quote_stats.json: {len(existing)} записей")

    # 3. Импортируем из корневого quote_stats.json
    if LEGACY_PATH.exists():
        try:
            with LEGACY_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                imported = 0
                for key, value in data.items():
                    if key not in existing:
                        existing[key] = value
                        imported += 1
                print(f"Импортировано из корневого quote_stats.json: {imported} новых записей")
        except Exception as e:
            print(f"Ошибка загрузки из {LEGACY_PATH}: {e}")

    print(f"Всего записей после объединения: {len(existing)}")

    # 4. Сохраняем в SQLite
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS quote_stats (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        conn.execute("DELETE FROM quote_stats")
        for key, entry in existing.items():
            if not isinstance(entry, dict):
                continue
            conn.execute(
                "INSERT INTO quote_stats (key, payload) VALUES (?, ?)",
                (key, json.dumps(entry, ensure_ascii=False)),
            )
        conn.commit()

    print(f"Сохранено в SQLite: {len(existing)} записей")

    # 5. Сохраняем в data/quote_stats.json
    with STATS_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"Сохранено в {STATS_PATH}: {len(existing)} записей")
    print("Миграция завершена!")


if __name__ == "__main__":
    main()