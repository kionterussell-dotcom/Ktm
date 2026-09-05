"""Market signal detection — the exact labels from CLAUDE.md, no others.

Every function takes facts already in desk.db and returns a Signal or None. None
means the condition was not met; it never means "we could not tell". When the
inputs are insufficient the caller gets UNVERIFIED from evaluate_game(), which is
a different and visible state.

Thresholds are CLAUDE.md's, hardcoded here so they cannot drift silently:
  PUBLIC OVERLOAD      70%+ tickets one side (80%+ EXTREME)
  BOOK NEED            ticket% AND handle% both 70%+ same side, line barely moved
  SHARP DIVERGENCE     handle% trails ticket% by 15+
  REVERSE LINE MOVE    60%+ tickets one way, line moves the other
  STEAM                0.5+ same-direction move across 3+ books
  KEY NUMBER MOVE      bought through 3 or 7 against the public
"""
from __future__ import annotations

from dataclasses import dataclass, field

KEY_NUMBERS = (3.0, 7.0)
STATIC_LINE_TOL = 0.5          # "line static or barely moved"


@dataclass
class Signal:
    label: str
    side: str
    market: str
    strength: int              # 1-5, for ranking the board. Not a win probability.
    detail: str
    sources: list[str] = field(default_factory=list)
    book: str = ""


@dataclass
class GameSplits:
    """One book's view of one market on one game."""
    book: str
    market: str
    side: str                  # the side the percentages describe
    ticket_pct: float | None
    handle_pct: float | None
    fetched_at: str
    source: str


def public_overload(s: GameSplits) -> Signal | None:
    if s.ticket_pct is None or s.ticket_pct < 70:
        return None
    extreme = s.ticket_pct >= 80
    return Signal(
        "PUBLIC OVERLOAD" + (" — EXTREME" if extreme else ""),
        s.side, s.market, 3 if extreme else 2,
        f"{s.book} {s.ticket_pct:.0f}% tickets on {s.side}. Not a bet by itself.",
        [s.source], s.book,
    )


def sharp_divergence(s: GameSplits) -> Signal | None:
    if s.ticket_pct is None or s.handle_pct is None:
        return None
    gap = s.ticket_pct - s.handle_pct
    if gap < 15:
        return None
    return Signal(
        "SHARP DIVERGENCE", s.side, s.market, 4,
        f"{s.book} handle trails tickets by {gap:.0f} pts on {s.side} "
        f"({s.ticket_pct:.0f}% tickets / {s.handle_pct:.0f}% handle) — "
        f"many small bets on {s.side}, bigger money elsewhere.",
        [s.source], s.book,
    )


def book_need(s: GameSplits, line_move: float | None) -> Signal | None:
    """Highest-value flag: both percentages heavy the same way and the book has
    not moved off it, so it is carrying real exposure."""
    if s.ticket_pct is None or s.handle_pct is None:
        return None
    if s.ticket_pct < 70 or s.handle_pct < 70:
        return None
    if line_move is not None and abs(line_move) > STATIC_LINE_TOL:
        return None
    moved = "line static" if line_move in (None, 0) else f"line moved only {line_move:+.1f}"
    return Signal(
        "BOOK NEED", s.side, s.market, 5,
        f"{s.book} {s.ticket_pct:.0f}% tickets AND {s.handle_pct:.0f}% handle on {s.side}, "
        f"{moved}. Book is exposed and needs {s.side} to lose.",
        [s.source], s.book,
    )


def reverse_line_movement(s: GameSplits, open_num: float | None,
                          cur_num: float | None) -> Signal | None:
    """Strongest single sharp indicator. CLAUDE.md requires verification against
    the opener across two books before it is called — enforced by the caller,
    which only passes an opener established from more than one book."""
    if s.ticket_pct is None or s.ticket_pct < 60:
        return None
    if open_num is None or cur_num is None:
        return None
    move = cur_num - open_num
    if abs(move) < 0.5:
        return None
    # The public side got worse for the public = line moved against them.
    if move <= 0:
        return None
    return Signal(
        "REVERSE LINE MOVEMENT", s.side, s.market, 5,
        f"{s.ticket_pct:.0f}% tickets on {s.side} but the number moved {move:+.1f} "
        f"against them ({open_num:+.1f} -> {cur_num:+.1f}). Sharp money the other way.",
        [s.source], s.book,
    )


def key_number_move(open_num: float | None, cur_num: float | None,
                    side: str, market: str) -> Signal | None:
    if open_num is None or cur_num is None or open_num == cur_num:
        return None
    lo, hi = sorted((abs(open_num), abs(cur_num)))
    crossed = [k for k in KEY_NUMBERS if lo < k < hi or lo == k or hi == k]
    if not crossed:
        return None
    return Signal(
        "KEY NUMBER MOVE", side, market, 4,
        f"Number moved {open_num:+.1f} -> {cur_num:+.1f}, through "
        f"{' and '.join(str(int(k)) for k in crossed)}. Someone paid to cross it.",
        [],
    )


def steam(moves_by_book: dict[str, float], window: str = "") -> Signal | None:
    same_dir_up = [b for b, m in moves_by_book.items() if m >= 0.5]
    same_dir_dn = [b for b, m in moves_by_book.items() if m <= -0.5]
    books = same_dir_up if len(same_dir_up) >= len(same_dir_dn) else same_dir_dn
    if len(books) < 3:
        return None
    direction = "up" if books is same_dir_up else "down"
    return Signal(
        "STEAM", "", "", 4,
        f"0.5+ move {direction} across {len(books)} books ({', '.join(sorted(books)[:5])})"
        + (f" in {window}" if window else "") + ".",
        [],
    )


def consensus_trap(s: GameSplits, line_move: float | None) -> Signal | None:
    """Lopsided, and the line moved WITH the public, with no sharp counter.
    Explicitly not a fade spot — the label exists so the desk says so plainly
    instead of treating every lopsided game as a fade."""
    if s.ticket_pct is None or s.ticket_pct < 70:
        return None
    if line_move is None or line_move >= 0:
        return None
    if s.handle_pct is not None and (s.ticket_pct - s.handle_pct) >= 15:
        return None                        # sharp money disagrees; not a trap
    return Signal(
        "CONSENSUS TRAP", s.side, s.market, 1,
        f"{s.ticket_pct:.0f}% tickets on {s.side} and the line moved {line_move:+.1f} with "
        f"them, no sharp counter. Not a fade spot.",
        [s.source], s.book,
    )


def evaluate_market(splits: list[GameSplits], open_num: float | None,
                    cur_num: float | None, moves_by_book: dict[str, float] | None = None
                    ) -> list[Signal]:
    """All signals for one market on one game, strongest first.

    An empty list means 'no signal met its threshold'. It does not mean the game
    is unread — evaluate_game() reports UNVERIFIED separately when the inputs
    were not there in the first place.
    """
    out: list[Signal] = []
    line_move = None if (open_num is None or cur_num is None) else round(cur_num - open_num, 1)
    for s in splits:
        for sig in (book_need(s, line_move),
                    reverse_line_movement(s, open_num, cur_num),
                    sharp_divergence(s),
                    public_overload(s),
                    consensus_trap(s, line_move)):
            if sig:
                out.append(sig)
    if splits:
        k = key_number_move(open_num, cur_num, splits[0].side, splits[0].market)
        if k:
            out.append(k)
    if moves_by_book:
        st = steam(moves_by_book)
        if st:
            out.append(st)
    out.sort(key=lambda s: -s.strength)
    return out


def verification_state(splits: list[GameSplits]) -> tuple[str, str]:
    """CLAUDE.md: two independent sources minimum before flagging any signal."""
    sources = {s.source for s in splits}
    if not sources:
        return "UNVERIFIED", "no split source returned for this game"
    if len(sources) < 2:
        return "UNVERIFIED", f"only one source ({sources.pop()}) — two required to flag"
    return "VERIFIED", f"{len(sources)} independent sources"
