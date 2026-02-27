from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class FaqEntry:
    title: str
    keywords: tuple[str, ...]
    answer: str


_FAQ: list[FaqEntry] = [
    FaqEntry(
        title="How to book a ticket",
        keywords=(
            "book",
            "ticket",
            "booking",
            "select stop",
            "source",
            "destination",
            "fare",
            "otp",
        ),
        answer=(
            "1) Login as Passenger\n"
            "2) Enter bus OTP\n"
            "3) Select Source and Destination stops\n"
            "4) Confirm to generate the ticket\n"
            "If fare shows 0 or error, re-check stops and OTP."
        ),
    ),
    FaqEntry(
        title="OTP is invalid",
        keywords=("invalid otp", "otp", "wrong otp", "not working", "bus code"),
        answer=(
            "The OTP must match the bus OTP exactly (5 characters). "
            "Please confirm with the driver/bus display and try again."
        ),
    ),
    FaqEntry(
        title="Ticket expiry",
        keywords=("expire", "expired", "valid", "validity", "30 minutes"),
        answer=(
            "Tickets are time-limited. If your ticket is expired, please book a new ticket using the bus OTP."
        ),
    ),
    FaqEntry(
        title="Login issues",
        keywords=("login", "password", "username", "invalid credentials", "not loading"),
        answer=(
            "If login fails, re-check username/password. If the page keeps reloading, it usually means invalid credentials. "
            "If the page is blank/loading, it may be a server/host issue—contact the admin."
        ),
    ),
    FaqEntry(
        title="Fare calculation error",
        keywords=("fare", "price", "0", "error", "calculate"),
        answer=(
            "Fare is calculated based on the selected Source and Destination. "
            "Ensure both are selected and they are not the same stop."
        ),
    ),
    FaqEntry(
        title="Driver live tracking not updating",
        keywords=("tracking", "map", "live", "location", "gps"),
        answer=(
            "Live tracking updates only when GPS is available and the driver is sending location. "
            "If it does not update, check network/GPS permissions and try again."
        ),
    ),
]


def all_faq_entries() -> list[FaqEntry]:
    return list(_FAQ)


_WORD_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def select_relevant_faq(message: str, *, limit: int = 4) -> list[FaqEntry]:
    """Returns up to `limit` FAQ entries most relevant to the message.

    Lightweight, dependency-free similarity ranking.
    """

    msg = _normalize(message)
    if not msg:
        return []

    scored: list[tuple[float, FaqEntry]] = []
    for entry in _FAQ:
        hay = " ".join([entry.title, *entry.keywords, entry.answer])
        hay_n = _normalize(hay)

        # Prefer keyword hits, then fuzzy match.
        kw_hits = 0
        for kw in entry.keywords:
            kw_n = _normalize(kw)
            if kw_n and kw_n in msg:
                kw_hits += 1

        ratio = SequenceMatcher(a=msg, b=hay_n).ratio()
        score = (kw_hits * 2.0) + ratio
        scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [e for s, e in scored[: max(0, int(limit))] if s > 0]
    return top
