import sqlite3
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

DB_PATH = "database.db"

# --- 1. ИНИЦИАЛИЗАЦИЯ БД ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # В таблицу задач добавили is_habit (0 - разовое дело, 1 - привычка)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    cursor.execute("INSERT OR IGNORE INTO user_balance (id, balance) VALUES (1, 0)")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            price INTEGER NOT NULL
        )
    """)
    
    # Стартовый пресет магазина по твоей экономике!
    cursor.execute("SELECT COUNT(*) FROM shop_items")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Кастомный обед до 220 грн', 150)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Пачка сигарет', 130)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Картридж + жижа', 550)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Рестик на 500 грн', 500)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Вечер с алкоголем', 350)")
        cursor.execute("INSERT INTO shop_items (title, price) VALUES ('Вечер в говно', 900)")
        
    conn.commit()
    conn.close()

init_db()


# --- 2. ЛЕНИВОЕ ОБНОВЛЕНИЕ 2.0 (С ПРИВЫЧКАМИ) ---
def process_pending_rewards():
    today_str = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Ищем вчерашние выполненные задачи
    cursor.execute("""
        SELECT id, reward, is_habit FROM tasks 
        WHERE is_completed = 1 AND reward_claimed = 0 AND completed_date < ?
    """, (today_str,))
    
    pending = cursor.fetchall()
    
    if pending:
        total_coins = sum(t[1] for t in pending)
        
        # 1. Зачисляем общий куш на баланс
        cursor.execute("UPDATE user_balance SET balance = balance + ? WHERE id = 1", (total_coins,))
        
        # Разделяем ID на две группы
        one_off_ids = [t[0] for t in pending if t[2] == 0]
        habit_ids = [t[0] for t in pending if t[2] == 1]
        
        # 2. Разовые дела отправляем в вечный архив (reward_claimed = 1)
        if one_off_ids:
            placeholders = ",".join("?" for _ in one_off_ids)
            cursor.execute(f"UPDATE tasks SET reward_claimed = 1 WHERE id IN ({placeholders})", one_off_ids)
            
        # 3. ПРИВЫЧКИ ВОСКРЕШАЕМ! Сбрасываем им статус выполнения на 0
        if habit_ids:
            placeholders = ",".join("?" for _ in habit_ids)
            cursor.execute(f"""
                UPDATE tasks 
                SET is_completed = 0, completed_date = NULL, reward_claimed = 0 
                WHERE id IN ({placeholders})
            """, habit_ids)
            
        conn.commit()
        print(f"[Бэкенд]: Начислено {total_coins} монет! Привычки сброшены на новый круг.")
        
    conn.close()


# --- МОДЕЛИ ДАННЫХ ---
class TaskCreate(BaseModel):
    title: str
    reward: int
    is_habit: bool = False  # По умолчанию галочка выключена


@app.get("/balance")
def get_balance():
    process_pending_rewards()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    balance = cursor.fetchone()[0]
    conn.close()
    return {"balance": balance}

@app.get("/tasks")
def get_tasks():
    process_pending_rewards()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, reward, is_completed, reward_claimed, is_habit FROM tasks")
    rows = cursor.fetchall()
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (title, reward, is_habit) VALUES (?, ?, ?)", 
        (task.title, task.reward, int(task.is_habit))
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/tasks/{task_id}/toggle")
def toggle_task(task_id: int):
    today_str = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT reward, is_completed, reward_claimed FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Задача не найдена")
        
    reward, is_completed, reward_claimed = task
    
    if is_completed == 0:
        cursor.execute("UPDATE tasks SET is_completed = 1, completed_date = ?, reward_claimed = 0 WHERE id = ?", (today_str, task_id))
    else:
        if reward_claimed == 1:
            cursor.execute("UPDATE user_balance SET balance = balance - ? WHERE id = 1", (reward,))
        cursor.execute("UPDATE tasks SET is_completed = 0, completed_date = NULL, reward_claimed = 0 WHERE id = ?", (task_id,))
            
    conn.commit()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    curr_balance = cursor.fetchone()[0]
    conn.close()
    return {"status": "success", "current_balance": curr_balance}

@app.delete("/tasks/{task_id}")
def delete_task(task_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT reward, is_completed, reward_claimed FROM tasks WHERE id = ?", (task_id,))
    task = cursor.fetchone()
    if not task:
        conn.close()
        raise HTTPException(status_code=404, detail="Задача не найдена")
        
    reward, is_completed, reward_claimed = task
    if is_completed == 1 and reward_claimed == 1:
        cursor.execute("UPDATE user_balance SET balance = balance - ? WHERE id = 1", (reward,))
        
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/shop")
def get_shop_items():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, price FROM shop_items")
    items = [{"id": r[0], "title": r[1], "price": r[2]} for r in cursor.fetchall()]
    conn.close()
    return items

@app.post("/shop/{item_id}/buy")
def buy_item(item_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT title, price FROM shop_items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    if not item:
        conn.close()
        raise HTTPException(status_code=404, detail="Товар не найден")
        
    title, price = item
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    balance = cursor.fetchone()[0]
    
    if balance < price:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Не хватает {price - balance} монет!")
        
    cursor.execute("UPDATE user_balance SET balance = balance - ? WHERE id = 1", (price,))
    conn.commit()
    cursor.execute("SELECT balance FROM user_balance WHERE id = 1")
    new_bal = cursor.fetchone()[0]
    conn.close()
    return {"status": "success", "new_balance": new_bal, "bought": title}

@app.post("/shop")
def create_shop_item(item: ShopItemCreate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO shop_items (title, price) VALUES (?, ?)", (item.title, item.price))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/shop/{item_id}")
def delete_shop_item(item_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return {"status": "success"}