"""
routers/congrats.py
Congrats endpoints: POST /api/congrats, GET /api/congrats/{user_id}.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.auth import CurrentUser, get_current_user
from nova_db.congrats import save_congrats, get_congrats_for_user

router = APIRouter()


class CongratsBody(BaseModel):
    receiver_user_id: int
    activity_id: int
    message: str


@router.post("/congrats")
def post_congrats(body: CongratsBody, user: CurrentUser = Depends(get_current_user)):
    sender_id = user.classmate_user_id if user.classmate_user_id is not None else 0
    congrats_id = save_congrats(
        sender_user_id=sender_id,
        receiver_user_id=body.receiver_user_id,
        activity_id=body.activity_id,
        message=body.message,
    )
    return {"success": True, "congrats_id": congrats_id}


@router.get("/congrats/{user_id}")
def get_congrats(user_id: int, user: CurrentUser = Depends(get_current_user)):
    return get_congrats_for_user(user_id)
