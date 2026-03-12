from __future__ import annotations


def resolve_order_tr_id(env: str, side: str) -> str:
    table = {
        ("real", "BUY"): "TTTC0012U",
        ("real", "SELL"): "TTTC0011U",
        ("demo", "BUY"): "VTTC0012U",
        ("demo", "SELL"): "VTTC0011U",
    }
    return table[(env, side)]


def resolve_revise_cancel_tr_id(env: str) -> str:
    return "TTTC0013U" if env == "real" else "VTTC0013U"
