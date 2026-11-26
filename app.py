import os, json, hmac, hashlib, secrets, sqlite3, time
from urllib.parse import parse_qsl
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
REQUIRE_AUTH = os.getenv("REQUIRE_TELEGRAM_AUTH", "true").lower() == "true"
DB_PATH = os.getenv("DATABASE_PATH", "dice_duel.sqlite3")

app = FastAPI(title="DICE DUEL MVP")
app.mount("/static", StaticFiles(directory="static"), name="static")

def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          tg_id INTEGER PRIMARY KEY, username TEXT, balance INTEGER NOT NULL DEFAULT 1000,
          wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS games(
          id TEXT PRIMARY KEY, p1 INTEGER NOT NULL, p2 INTEGER,
          stake INTEGER NOT NULL, status TEXT NOT NULL,
          d1 INTEGER, d2 INTEGER, winner INTEGER, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ledger(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER NOT NULL,
          game_id TEXT, delta INTEGER NOT NULL, reason TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_games_status ON games(status);
        """)
init_db()

def verify_init_data(raw: str) -> dict:
    if not BOT_TOKEN:
        raise HTTPException(503, "مدیر هنوز توکن ربات را تنظیم نکرده است.")
    pairs = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "اطلاعات تلگرام معتبر نیست.")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(401, "امضای اطلاعات تلگرام نادرست است.")
    auth_date = int(pairs.get("auth_date", "0"))
    if abs(int(time.time()) - auth_date) > 86400:
        raise HTTPException(401, "نشست تلگرام منقضی شده است.")
    try:
        return json.loads(pairs["user"])
    except Exception:
        raise HTTPException(401, "شناسه کاربر تلگرام پیدا نشد.")

def current_user(x_telegram_init_data: Optional[str]):
    if not x_telegram_init_data:
        if REQUIRE_AUTH:
            raise HTTPException(401, "بازی را از داخل تلگرام باز کنید.")
        # فقط برای توسعه محلی؛ هرگز در انتشار عمومی فعال نماند.
        return {"id": 10001, "username": "local_test"}
    return verify_init_data(x_telegram_init_data)

def ensure_user(user):
    tg_id = int(user["id"])
    username = user.get("username")
    with db() as con:
        con.execute("INSERT OR IGNORE INTO users(tg_id,username,balance) VALUES(?,?,1000)", (tg_id, username))
        if username:
            con.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
        row = con.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()
        return dict(row)

def auth_user(header):
    user = current_user(header)
    return ensure_user(user)

class StartGame(BaseModel):
    stake: int = Field(ge=10, le=10000)

class JoinGame(BaseModel):
    game_id: str

@app.get("/")
def home():
    return FileResponse("static/index.html")

@app.get("/api/me")
def me(x_telegram_init_data: Optional[str] = Header(default=None)):
    user = auth_user(x_telegram_init_data)
    return {"id": user["tg_id"], "username": user["username"], "balance": user["balance"],
            "wins": user["wins"], "losses": user["losses"]}

@app.post("/api/games")
def create_game(body: StartGame, x_telegram_init_data: Optional[str] = Header(default=None)):
    user = auth_user(x_telegram_init_data)
    gid = secrets.token_urlsafe(8)
    now = int(time.time())
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT balance FROM users WHERE tg_id=?", (user["tg_id"],)).fetchone()
        if row["balance"] < body.stake:
            raise HTTPException(400, "امتیاز کافی نیست.")
        con.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (body.stake, user["tg_id"]))
        con.execute("INSERT INTO games(id,p1,stake,status,created_at) VALUES(?,?,?,'waiting',?)",
                    (gid, user["tg_id"], body.stake, now))
        con.execute("INSERT INTO ledger(tg_id,game_id,delta,reason,created_at) VALUES(?,?,?,'stake_lock',?)",
                    (user["tg_id"], gid, -body.stake, now))
    return {"game_id": gid, "status": "waiting", "stake": body.stake}

@app.post("/api/games/join")
def join_game(body: JoinGame, x_telegram_init_data: Optional[str] = Header(default=None)):
    user = auth_user(x_telegram_init_data)
    now = int(time.time())
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        game = con.execute("SELECT * FROM games WHERE id=?", (body.game_id,)).fetchone()
        if not game or game["status"] != "waiting":
            raise HTTPException(404, "این اتاق در دسترس نیست.")
        if game["p1"] == user["tg_id"]:
            raise HTTPException(400, "نمی‌توانید وارد بازی خودتان شوید.")
        wallet = con.execute("SELECT balance FROM users WHERE tg_id=?", (user["tg_id"],)).fetchone()
        if wallet["balance"] < game["stake"]:
            raise HTTPException(400, "امتیاز کافی نیست.")
        con.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (game["stake"], user["tg_id"]))
        con.execute("INSERT INTO ledger(tg_id,game_id,delta,reason,created_at) VALUES(?,?,?,'stake_lock',?)",
                    (user["tg_id"], body.game_id, -game["stake"], now))
        # نتیجه فقط روی سرور تولید می‌شود.
        d1, d2 = secrets.randbelow(6)+1, secrets.randbelow(6)+1
        winner = game["p1"] if d1 > d2 else user["tg_id"] if d2 > d1 else None
        if winner is None:
            # در تساوی هر دو ورودی برمی‌گردد؛ دور تازه را می‌توان در توسعه بعدی افزود.
            con.execute("UPDATE users SET balance=balance+? WHERE tg_id IN (?,?)",
                        (game["stake"], game["p1"], user["tg_id"]))
            con.execute("UPDATE games SET p2=?,d1=?,d2=?,status='draw' WHERE id=?",
                        (user["tg_id"], d1, d2, body.game_id))
            con.execute("INSERT INTO ledger(tg_id,game_id,delta,reason,created_at) VALUES(?,?,?,'draw_refund',?)",
                        (game["p1"], body.game_id, game["stake"], now))
            con.execute("INSERT INTO ledger(tg_id,game_id,delta,reason,created_at) VALUES(?,?,?,'draw_refund',?)",
                        (user["tg_id"], body.game_id, game["stake"], now))
            status = "draw"
        else:
            con.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (game["stake"]*2, winner))
            loser = user["tg_id"] if winner == game["p1"] else game["p1"]
            con.execute("UPDATE users SET wins=wins+1 WHERE tg_id=?", (winner,))
            con.execute("UPDATE users SET losses=losses+1 WHERE tg_id=?", (loser,))
            con.execute("UPDATE games SET p2=?,d1=?,d2=?,winner=?,status='finished' WHERE id=?",
                        (user["tg_id"], d1, d2, winner, body.game_id))
            con.execute("INSERT INTO ledger(tg_id,game_id,delta,reason,created_at) VALUES(?,?,?,'prize',?)",
                        (winner, body.game_id, game["stake"]*2, now))
            status = "finished"
    return {"game_id": body.game_id, "status": status, "dice": [d1, d2],
            "winner_id": winner, "prize": game["stake"]*2 if winner else 0}

@app.get("/api/games/{game_id}")
def game_status(game_id: str, x_telegram_init_data: Optional[str] = Header(default=None)):
    user = auth_user(x_telegram_init_data)
    with db() as con:
        g = con.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not g or user["tg_id"] not in (g["p1"], g["p2"]):
        raise HTTPException(404, "بازی پیدا نشد.")
    return dict(g)
