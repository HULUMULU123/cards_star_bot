import os

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
import uvicorn

from bot import bot
from config import ADMIN_ID
from db import (
    get_internal_stars,
    get_internal_stars_pool,
    update_internal_stars,
    update_internal_stars_pool
)
from keyboards import back_to_main_keyboard


API_KEY = os.getenv("INTERNAL_STARS_API_KEY")
API_HOST = os.getenv("INTERNAL_STARS_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("INTERNAL_STARS_API_PORT", "9000"))

app = FastAPI()


def require_api_key(x_api_key: str = Header(None)):
    if not API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


class AmountRequest(BaseModel):
    amount: int


class WithdrawalRequest(BaseModel):
    amount: int
    username: str | None = None
    user_id: int | None = None
    comment: str | None = None


@app.get("/internal-stars/balance")
def get_internal_stars_balance(_: None = Depends(require_api_key)):
    return {"balance": get_internal_stars_pool()}


@app.get("/internal-stars/user/{user_id}")
def get_internal_stars_user_balance(user_id: int, _: None = Depends(require_api_key)):
    return {"user_id": user_id, "balance": get_internal_stars(user_id)}


@app.post("/internal-stars/user/{user_id}/credit")
def credit_internal_stars_user(user_id: int, body: AmountRequest, _: None = Depends(require_api_key)):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount_must_be_positive")
    update_internal_stars(user_id, body.amount)
    return {"user_id": user_id, "balance": get_internal_stars(user_id)}

@app.get("/internal-stars/user/{user_id}/credit")
def credit_internal_stars_user_get(
    user_id: int,
    amount: int = Query(..., gt=0),
    _: None = Depends(require_api_key)
):
    update_internal_stars(user_id, amount)
    return {"user_id": user_id, "balance": get_internal_stars(user_id)}


@app.post("/internal-stars/user/{user_id}/debit")
def debit_internal_stars_user(user_id: int, body: AmountRequest, _: None = Depends(require_api_key)):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount_must_be_positive")
    current = get_internal_stars(user_id)
    if current < body.amount:
        raise HTTPException(status_code=400, detail="insufficient_balance")
    update_internal_stars(user_id, -body.amount)
    return {"user_id": user_id, "balance": get_internal_stars(user_id)}


@app.get("/internal-stars/user/{user_id}/debit")
def debit_internal_stars_user_get(
    user_id: int,
    amount: int = Query(..., gt=0),
    _: None = Depends(require_api_key)
):
    current = get_internal_stars(user_id)
    if current < amount:
        raise HTTPException(status_code=400, detail="insufficient_balance")
    update_internal_stars(user_id, -amount)
    return {"user_id": user_id, "balance": get_internal_stars(user_id)}


@app.post("/internal-stars/credit")
def credit_internal_stars(body: AmountRequest, _: None = Depends(require_api_key)):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount_must_be_positive")
    update_internal_stars_pool(body.amount)
    return {"balance": get_internal_stars_pool()}


@app.post("/internal-stars/debit")
def debit_internal_stars(body: AmountRequest, _: None = Depends(require_api_key)):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount_must_be_positive")
    ok = update_internal_stars_pool(-body.amount)
    if not ok:
        raise HTTPException(status_code=400, detail="insufficient_balance")
    return {"balance": get_internal_stars_pool()}


@app.post("/withdrawals/notify")
def notify_withdrawal(body: WithdrawalRequest, _: None = Depends(require_api_key)):
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount_must_be_positive")
    if not ADMIN_ID:
        raise HTTPException(status_code=500, detail="admin_id_not_configured")

    username_text = f"@{body.username}" if body.username else "not_set"
    message = (
        "*Заявка на вывод, проверьте админнку*\n\n"
        f"Пользователь: {username_text}\n"
        f"Количество: {body.amount}"
    )
    try:
        bot.send_message(
            ADMIN_ID,
            message,
            parse_mode="Markdown",
            reply_markup=back_to_main_keyboard()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"send_failed:{e}")
    return {"status": "ok"}


def run_api_server():
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
