import os
import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import date

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Вытягиваем секретную ссылку из настроек сервера. 
# Если сервера нет (запустили локально на Маке), упадем на встроенный SQLite или дефолт
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    # Если мы в облаке Render — подключаемся к вечному Postgres
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    # Локальная заглушка на случай тестов без интернета
    else:
        import sqlite3
        return sqlite3.connect("database.db")

# --- 1. ИНИЦИАЛИЗАЦИЯ БД ---
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # В Postgres вместо INTEGER PRIMARY KEY AUTOINCREMENT используется SERIAL PRIMARY KEY
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            reward INTEGER NOT NULL,
            is_completed INTEGER DEFAULT 0,
            completed_date TEXT,
            reward_claimed INTEGER DEFAULT 0,
            is_habit INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_balance (
            id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0
        )
    """)
    # Аналог INSERT OR IGNORE для Postgres
    cursor.execute("INSERT INTO user_balance (id, balance) VALUES (1, 0) ON CONFLICT (id) DO NOTHING")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            price INTEGER NOT NULL
        )
    """)
    
    cursor.execute("SELECT COUNT(*) FROM shop_items")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Кастомный обед до 220 грн', 150)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Пачка сигарет', 130)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Картридж + жижа', 550)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Рестик на 500 грн', 500)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Вечер с алкоголем', 350)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Вечер в говно', 900)")
        
    conn.commit()
    cursor.close()
    conn.close()

# Запускаем создание таблиц в облачном Postgres при старте
try:
    init_db()
    print("[Бэкенд]: Успешно подключились к облачной базе PostgreSQL!")
except Exception as e:
    print(f"[Ошибка БД]: Не удалось запустить базу: {e}")


# --- 2. ЛЕНИВОЕ ОБНОВЛЕНИЕ С ПРИВЫЧКАМИ ---
def process_pending_rewards():
    today_str = date.today().isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # В Postgres вместо '?' пишем '%s'
    cursor.execute("""
        SELECT id, reward, is_habit FROM tasks 
        WHERE is_completed = 1 AND reward_claimed = 0 AND completed_date < %s
    """, (today_str,))
    
    pending = cursor.fetchall()
    
    if pending:
        total_coins = sum(t[1] for t in pending)
        cursor.execute("UPDATE user_balance SET balance = balance + %s WHERE id = 1", (total_coins,))
        
        one_off_ids = [t[0] for t in pending if t[2] == 0]
        habit_ids = [t[0] for t in pending if t[2] == 1]
        
        if one_off_ids:
            placeholders = ",".join("%s" for _ in one_off_ids)
            cursor.execute(f"UPDATE tasks SET reward_claimed = 1 WHERE id IN ({placeholders})", tuple(one_off_ids))
            
        if habit_ids:
            placeholders = ",".join("%s" for _ in habit_ids)
            cursor.execute(f"""
                UPDATE tasks 
                SET is_completed = 0, completed_date = NULL, reward_claimed = 0 
                WHERE id IN ({placeholders})
            """, tuple(habit_ids))
            
        conn.commit()
    cursor.close()
    conn.close()


# --- МОДЕЛИ ДАННЫХ ---
class TaskCreate(BaseModel):
    title: str
    reward: int
    is_habit: bool = False

class ShopItemCreate(BaseModel):
    title: str
    price: int


# --- 4. ЭНДПОИНТЫ ---

@app.get("/balance")
def get_balance():
    process_pending_rewards()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    balance = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return {"balance": balance}

@app.get("/tasks")
def get_tasks():
    process_pending_rewards()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, reward, is_completed, reward_claimed, is_habit FROM tasks ORDER BY id DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        {
            "id": r[0], 
            "title": r[1], 
            "reward": r[2], 
            "is_completed": bool(r[3]), 
            "reward_claimed": bool(r[4]),
            "is_habit": bool(r[5])
        } for r in rows
    ]

@app.post("/tasks")
def create_task(task: TaskCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (title, reward, is_habit) VALUES (%s, %s, %s)", 
        (task.title, task.reward, int(task.is_habit))
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    today_str = date.today().isoformat()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT reward, is_completed, reward_claimed FROM tasks WHERE id = %s", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Задача не найдена")
        
    reward, is_completed, reward_claimed = task
    
    if is_completed == 0:
        cursor.execute("UPDATE tasks SET is_completed = 1, completed_date = %s, reward_claimed = 0 WHERE id = %s", (today_str, task_id))
    else:
        if reward_claimed == 1:
            cursor.execute("UPDATE user_balance SET balance = balance - %s WHERE id = 1", (reward,))
        cursor.execute("UPDATE tasks SET is_completed = 0, completed_date = NULL, reward_claimed = 0 WHERE id = %s", (task_id,))
            
    conn.commit()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    curr_balance = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return {"status": "success", "current_balance": curr_balance}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT reward, is_completed, reward_claimed FROM tasks WHERE id = %s", (task_id,))
    task = cursor.fetchone()
    if not task:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Задача не найдена")
        
    reward, is_completed, reward_claimed = task
    if is_completed == 1 and reward_claimed == 1:
        cursor.execute("UPDATE user_balance SET balance = balance - %s WHERE id = 1", (reward,))
        
    cursor.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.get("/shop")
def get_shop_items():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, price FROM shop_items ORDER BY price ASC")
    items = [{"id": r[0], "title": r[1], "price": r[2]} for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return items

@app.post("/shop/{item_id}/buy")
def buy_item(item_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title, price FROM shop_items WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    if not item:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Товар не найден")
        
    title, price = item
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    balance = cursor.fetchone()[0]
    
    if balance < price:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail=f"Не хватает {price - balance} монет!")
        
    cursor.execute("UPDATE user_balance SET balance = balance - %s WHERE id = 1", (price,))
    conn.commit()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    new_bal = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return {"status": "success", "new_balance": new_bal, "bought": title}

@app.post("/shop")
def create_shop_item(item: ShopItemCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO shop_items (title, price) VALUES (%s, %s)", (item.title, item.price))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}

@app.delete("/shop/{item_id}")
def delete_shop_item(item_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shop_items WHERE id = %s", (item_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return {"status": "success"}