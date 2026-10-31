"""Qivynex Academy: step-by-step lessons about this app, quizzes, progress and a Claude tutor."""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import state
from ..audit import audit
from ..db import get_db
from ..deps import client_ip, require
from ..models import AcademyDay, AcademyProgress, BacktestRun, ExamResult, Position, User, utcnow
from ..secretbox import decrypt
from ..services.academy_content import COURSES, all_lessons, lesson

router = APIRouter(prefix="/api/academy", tags=["academy"])

CHECK_LABEL = {
    "totp": "Two-factor login is on", "paper_trade": "You have placed a practice trade", "backtest": "You have run a backtest",
    "lab": "You have run the Strategy lab", "telegram": "Telegram is connected", "auto_on": "Auto-trade is on for an account",
    "crypto_keys": "Exchange API keys are saved", "broker": "A forex broker is connected", "live_gate": "Live gate is approved",
    "ai_on": "The AI assistant is on",
}


def app_checks(db: Session, user: User) -> dict:
    from ..services import automation
    from ..services.crypto import desk
    from ..services.livegate import is_approved
    tg = state.get(db, "alert_prefs").get("telegram", {})
    cc = state.get(db, "crypto")
    ai = state.get(db, "ai")
    return {
        "totp": bool(user.totp_enabled),
        "paper_trade": db.scalar(select(Position.id).where(Position.mode.in_(["PAPER", "CRYPTO_PAPER", "CRYPTO_TEST", "MT5_DEMO"])).limit(1)) is not None,
        "backtest": db.scalar(select(BacktestRun.id).where(BacktestRun.status == "DONE").limit(1)) is not None,
        "lab": state.get(db, "strategy_lab").get("status") == "DONE",
        "telegram": bool(tg.get("bot_token_enc") and tg.get("chat_id")),
        "auto_on": any(automation.flags_of(state.get(db, "controls")).values()),
        "crypto_keys": desk.has_keys(db, cc.get("exchange", "binance")),
        "broker": state.get(db, "brokers").get("active") not in (None, "", "none"),
        "live_gate": is_approved(db),
        "ai_on": bool(ai.get("enabled") and ai.get("api_key_enc")),
    }


def _progress(db: Session, user: User) -> dict[str, AcademyProgress]:
    return {p.lesson_id: p for p in db.scalars(select(AcademyProgress).where(AcademyProgress.user_id == user.id)).all()}


def _today(db: Session) -> str:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(state.get(db, "ai_jobs").get("timezone") or "Asia/Riyadh")
    return utcnow().astimezone(tz).strftime("%Y-%m-%d")


def touch_day(db: Session, user: User) -> None:
    from sqlalchemy.dialects.postgresql import insert
    db.execute(insert(AcademyDay).values(user_id=user.id, day=_today(db)).on_conflict_do_nothing(constraint="uq_academy_day"))


def streak(db: Session, user: User) -> dict:
    from datetime import date, timedelta
    days = sorted({date.fromisoformat(d) for d in db.scalars(select(AcademyDay.day).where(AcademyDay.user_id == user.id)).all()})
    if not days:
        return {"current": 0, "best": 0, "days": 0, "today": False}
    best = run = 1
    for a, b in zip(days, days[1:]):
        run = run + 1 if b - a == timedelta(days=1) else 1
        best = max(best, run)
    today = date.fromisoformat(_today(db))
    cur = 0
    if days[-1] >= today - timedelta(days=1):
        cur, d = 1, days[-1]
        s = set(days)
        while d - timedelta(days=1) in s:
            cur, d = cur + 1, d - timedelta(days=1)
    return {"current": cur, "best": best, "days": len(days), "today": days[-1] == today}


def _row(db: Session, user: User, lid: str) -> AcademyProgress:
    p = db.scalar(select(AcademyProgress).where(AcademyProgress.user_id == user.id, AcademyProgress.lesson_id == lid))
    if p is None:
        p = AcademyProgress(user_id=user.id, lesson_id=lid, steps_done=[])
        db.add(p)
    return p


@router.get("")
def overview(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    prog = _progress(db, user)
    checks = app_checks(db, user)
    courses, total, done, nxt = [], 0, 0, None
    for c in COURSES:
        ls = []
        for l in c["lessons"]:
            p = prog.get(l["id"])
            d = bool(p and p.completed_at)
            total += 1
            done += d
            if not d and nxt is None:
                nxt = {"id": l["id"], "title": l["title"], "course": c["title"]}
            ls.append({"id": l["id"], "title": l["title"], "minutes": l["minutes"], "done": d,
                       "started": bool(p and (p.steps_done or p.quiz_score is not None)),
                       "check": l.get("check"), "check_ok": checks.get(l.get("check")) if l.get("check") else None})
        courses.append({"id": c["id"], "title": c["title"], "icon": c["icon"], "summary": c["summary"], "lessons": ls,
                        "done": sum(x["done"] for x in ls), "total": len(ls)})
    ex = db.scalar(select(ExamResult).where(ExamResult.user_id == user.id, ExamResult.passed.is_(True)).order_by(ExamResult.score.desc()).limit(1))
    return {"courses": courses, "done": done, "total": total, "next": nxt, "streak": streak(db, user),
            "exam": {"passed": bool(ex), "score": ex.score if ex else None}, "certificate": bool(ex) and done == total,
            "checks": [{"key": k, "label": CHECK_LABEL[k], "ok": v} for k, v in checks.items()],
            "ai_ready": checks["ai_on"]}


@router.get("/lessons/{lid}")
def get_lesson(lid: str, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    c, l = lesson(lid)
    if not l:
        raise HTTPException(404, "Lesson not found")
    p = _progress(db, user).get(lid)
    ids = [x["id"] for _, x in all_lessons()]
    i = ids.index(lid)
    quiz = [{"q": q["q"], "options": q["options"]} for q in l.get("quiz", [])]  # answers stay on the server
    chk = l.get("check")
    return {**{k: v for k, v in l.items() if k != "quiz"}, "quiz": quiz, "course": {"id": c["id"], "title": c["title"]},
            "steps_done": (p.steps_done if p else []), "quiz_score": p.quiz_score if p else None, "done": bool(p and p.completed_at),
            "check_label": CHECK_LABEL.get(chk), "check_ok": app_checks(db, user).get(chk) if chk else None,
            "prev": ids[i - 1] if i > 0 else None, "next": ids[i + 1] if i + 1 < len(ids) else None}


class StepIn(BaseModel):
    step: int = Field(ge=0, le=50)
    done: bool


@router.post("/lessons/{lid}/steps")
def mark_step(lid: str, body: StepIn, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    _, l = lesson(lid)
    if not l or body.step >= len(l["steps"]):
        raise HTTPException(404, "Step not found")
    p = _row(db, user, lid)
    s = set(p.steps_done or [])
    (s.add if body.done else s.discard)(body.step)
    p.steps_done, p.updated_at = sorted(s), utcnow()
    touch_day(db, user)
    db.commit()
    return {"steps_done": p.steps_done}


class QuizIn(BaseModel):
    answers: list[int]


@router.post("/lessons/{lid}/quiz")
def quiz(lid: str, body: QuizIn, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    _, l = lesson(lid)
    if not l:
        raise HTTPException(404, "Lesson not found")
    qs = l.get("quiz", [])
    if len(body.answers) != len(qs):
        raise HTTPException(400, "Answer every question")
    res = [{"correct": a == q["answer"], "answer": q["answer"], "why": q["why"]} for a, q in zip(body.answers, qs)]
    score = round(100 * sum(r["correct"] for r in res) / max(1, len(qs)))
    p = _row(db, user, lid)
    p.quiz_score, p.updated_at = max(score, p.quiz_score or 0), utcnow()
    touch_day(db, user)
    db.commit()
    return {"score": score, "results": res, "passed": score == 100}


@router.post("/lessons/{lid}/complete")
def complete(lid: str, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    _, l = lesson(lid)
    if not l:
        raise HTTPException(404, "Lesson not found")
    p = _row(db, user, lid)
    if l.get("quiz") and (p.quiz_score or 0) < 100:
        raise HTTPException(400, "Answer the quiz correctly first (you can retry as often as you like)")
    p.completed_at = p.completed_at or utcnow()
    p.updated_at = utcnow()
    touch_day(db, user)
    db.commit()
    return {"done": True}


@router.post("/reset")
def reset(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    for p in _progress(db, user).values():
        db.delete(p)
    db.commit()
    return {"status": "ok"}


TUTOR = """You are the Qivynex Academy tutor inside the user's private trading app "Qivynex Trade" (forex + crypto spot).
Teach like a patient teacher with a complete beginner: short sentences, one idea at a time, everyday examples, and a tiny
practical task at the end ("Now try: open X → press Y"). Use the app's real page names: Terminal, Dashboard, Market overview,
Alerts, Setups to approve, Strategy lab & auto-trade, Live trading, News, SMC scanner, Market, Signals, Trades, Backtest,
Risk & Strategy, Brokers, AI Assistant, Guide, Audit & Events, System, My account, Academy.
Facts about the app: every order needs a stop-loss and passes the risk engine; practice (paper), broker demo and crypto testnet
use fake money; real money is locked until the live gate (Risk & Strategy) is approved and auto-trade on a real-money account needs
the owner's password; crypto is spot only (buy, no shorting); alerts go to Telegram/email; the AI cannot place trades.
Rules: never promise profit or predict prices; never suggest removing stops, martingale, averaging down or raising risk to recover
losses; recommend practice/demo before real money; for choosing a broker/exchange give a checklist (regulation, availability in the
user's country, demo, fees, app support) and tell the user to verify on official sites — do not claim a platform is licensed or
available where you are not sure. This is education, not financial advice. Reply in the user's language (English or Urdu)."""
_rate: dict[int, deque] = defaultdict(deque)


class TutorIn(BaseModel):
    lesson_id: str | None = Field(default=None, max_length=64)
    messages: list[dict] = Field(max_length=30)
    simple: bool = True


@router.post("/tutor")
def tutor(body: TutorIn, request: Request, user: User = Depends(require("ai.use")), db: Session = Depends(get_db)):
    from .ai import _call, _context
    ai = state.get(db, "ai")
    if not ai["enabled"] or not ai.get("api_key_enc"):
        raise HTTPException(400, "Turn on the AI assistant first (AI Assistant page)")
    q = _rate[user.id]
    now = time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 12:
        raise HTTPException(429, "Too many questions in a minute; please wait a moment")
    q.append(now)
    msgs = [{"role": m["role"], "content": str(m.get("content", ""))[:3000]} for m in body.messages[-16:]
            if m.get("role") in ("user", "assistant") and m.get("content")]
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(400, "Last message must be from the user")
    system = TUTOR
    if body.simple:
        system += "\nExplain as if to a 10-year-old: no jargon without a one-line explanation, max ~180 words."
    if body.lesson_id:
        c, l = lesson(body.lesson_id)
        if l:
            system += "\n\nCURRENT LESSON (JSON):\n" + json.dumps({"course": c["title"], **l}, ensure_ascii=False)[:6000]
    checks = app_checks(db, user)
    system += "\n\nUSER'S SETUP STATUS: " + json.dumps({CHECK_LABEL[k]: v for k, v in checks.items()})
    if ai.get("share_data", True):
        system += "\n\nAPP DATA (live, JSON):\n" + _context(db)[:6000]
    reply = _call(ai["provider"], ai["model"], decrypt(ai["api_key_enc"]), system, msgs)
    audit(db, "academy.tutor", user=user, ip=client_ip(request), details={"lesson": body.lesson_id})
    touch_day(db, user)
    db.commit()
    return {"reply": reply}


# ------------------------------------------------------------------ readiness exam + certificate
EXAM_SIZE = 15
PASS_PCT = 80


def _pool() -> dict:
    out = {}
    for c, l in all_lessons():
        for i, q in enumerate(l.get("quiz", [])):
            out[f"{l['id']}:{i}"] = {**q, "lesson": l["title"], "course": c["title"]}
    return out


@router.post("/exam/start")
def exam_start(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    import random
    pool = _pool()
    ids = random.sample(sorted(pool), min(EXAM_SIZE, len(pool)))
    ex = ExamResult(user_id=user.id, score=0, passed=False, questions=ids)
    db.add(ex)
    db.commit()
    return {"exam_id": ex.id, "pass_pct": PASS_PCT,
            "questions": [{"id": k, "q": pool[k]["q"], "options": pool[k]["options"], "lesson": pool[k]["lesson"]} for k in ids]}


class ExamIn(BaseModel):
    answers: dict[str, int]


@router.post("/exam/{exam_id}/submit")
def exam_submit(exam_id: int, body: ExamIn, user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    import secrets
    ex = db.get(ExamResult, exam_id)
    if not ex or ex.user_id != user.id:
        raise HTTPException(404, "Exam not found")
    if ex.finished_at:
        raise HTTPException(409, "This exam was already submitted — start a new one")
    if (utcnow() - ex.started_at).total_seconds() > 3 * 3600:
        raise HTTPException(409, "This exam is older than 3 hours — start a new one")
    pool = _pool()
    res = []
    for k in ex.questions:
        q = pool.get(k)
        if not q:
            continue
        a = body.answers.get(k)
        res.append({"id": k, "q": q["q"], "correct": a == q["answer"], "answer": q["answer"], "why": q["why"], "lesson": q["lesson"]})
    score = round(100 * sum(r["correct"] for r in res) / max(1, len(res)))
    ex.score, ex.passed, ex.finished_at = score, score >= PASS_PCT, utcnow()
    if ex.passed:
        ex.cert_id = "QVX-" + secrets.token_hex(4).upper()
    touch_day(db, user)
    db.commit()
    return {"score": score, "passed": ex.passed, "pass_pct": PASS_PCT, "results": res}


@router.get("/certificate")
def certificate(user: User = Depends(require("dashboard.view")), db: Session = Depends(get_db)):
    ex = db.scalar(select(ExamResult).where(ExamResult.user_id == user.id, ExamResult.passed.is_(True)).order_by(ExamResult.score.desc()).limit(1))
    prog = _progress(db, user)
    total = sum(1 for _ in all_lessons())
    done = sum(1 for p in prog.values() if p.completed_at)
    if not ex or done < total:
        raise HTTPException(400, f"Finish all lessons ({done}/{total}) and pass the readiness exam first")
    last = max(p.completed_at for p in prog.values() if p.completed_at)
    return {"name": user.full_name or user.username, "score": ex.score, "cert_id": ex.cert_id, "exam_date": ex.finished_at,
            "lessons": total, "completed": max(last, ex.finished_at), "streak": streak(db, user)}
