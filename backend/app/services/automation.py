"""Auto-execution policy: which account environments may place qualified strategy trades
without asking the owner first.

* Practice / demo / testnet: the owner's switch decides.
* Real money (BROKER LIVE, CRYPTO LIVE): only when the live gate is approved AND not expired AND the
  owner switched auto-trading on for that mode with their password. Otherwise every setup waits for approval.
* Only setups whose verdict is at least `auto_min_verdict` and score ≥ `auto_min_score` are auto-placed;
  the risk engine still checks every order (size, loss limits, kill switch, data).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import state

MODES = ["PAPER", "MT5_DEMO", "MT5_LIVE", "CRYPTO_PAPER", "CRYPTO_TEST", "CRYPTO_LIVE"]
LIVE = {"MT5_LIVE", "CRYPTO_LIVE"}
VERDICT_RANK = {"NO_TRADE": 0, "WATCH": 1, "PAPER_TEST": 2, "RESEARCH_SUPPORTED": 3}


def flags_of(c: dict) -> dict:
    """Per-mode switches; the legacy 'ask me before each strategy trade = off' means practice + demo are on."""
    flags = dict(c.get("auto_execute") or {})
    if not flags and not c.get("require_approval", True):
        flags = {"PAPER": True, "MT5_DEMO": True}
    return flags


def auto_ok(db: Session, mode: str, assessment=None) -> bool:
    c = state.get(db, "controls")
    on = bool(flags_of(c).get(mode))
    if not on:
        return False
    if mode in LIVE:
        from .livegate import is_approved
        if not is_approved(db):
            return False
    if assessment is not None:
        if VERDICT_RANK.get(assessment.verdict, 0) < VERDICT_RANK.get(c.get("auto_min_verdict", "PAPER_TEST"), 2):
            return False
        if assessment.score < int(c.get("auto_min_score", 70)):
            return False
    return True


def announce(db: Session, sig, filled_volume=None, price=None, mode: str = "") -> None:
    """AUTO_TRADE alert (Telegram/email per preferences) after an automatic order."""
    from ..models import SignalAssessment
    from .alerts import raise_alert, signal_text
    a = db.get(SignalAssessment, sig.id)
    title, body = signal_text(sig, a, label="AUTO TRADE PLACED")
    body = f"Mode: {mode} · size {filled_volume} @ {price}\n" + body.replace("Action: Review in Qivynex (no automatic live order)",
                                                                           "Action: placed automatically by your auto-trade setting — manage it in Qivynex")
    raise_alert(db, "AUTO_TRADE", title, body, severity="warning", dedup=f"auto:{sig.id}", symbol=sig.symbol, signal_id=sig.id, link="#/live")
