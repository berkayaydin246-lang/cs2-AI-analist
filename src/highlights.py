"""
highlights.py
Best-moments extraction from parsed CS2 demo data.

Identifies and scores the most impactful player moments:
- Opening kills
- Multi-kills (2k, 3k, 4k, ace)
- Clutch attempts / wins
- Trade kills
- Impactful entries

Each highlight carries full identity, timing, and scoring metadata.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any

# CS2 tick rate
TICK_RATE = 64
TICKS_PER_SECOND = TICK_RATE


def _ticks(seconds: float) -> int:
    return int(seconds * TICKS_PER_SECOND)


# ── Highlight data model ─────────────────────────────────────────────────────

@dataclass
class Highlight:
    highlight_id: str
    player_name: str
    player_steamid64: str
    round_number: int
    event_type: str          # opening_kill, multi_kill, clutch_win, clutch_attempt, trade_kill, entry_kill
    anchor_tick: int         # the primary event tick
    score: float             # composite importance score (0-100)
    priority: int            # rank within demo (1 = best)
    confidence: float        # 0.0-1.0
    selection_reason: str
    kills_in_sequence: list[dict] = field(default_factory=list)
    clutch_vs: int = 0
    clutch_won: bool = False

    # Round context
    round_start_tick: int = 0
    round_end_tick: int = 0
    freeze_end_tick: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Scoring weights ──────────────────────────────────────────────────────────

SCORE_WEIGHTS = {
    "opening_kill":    15.0,
    "entry_kill":      12.0,
    "trade_kill":       8.0,
    "multi_kill_2k":   18.0,
    "multi_kill_3k":   30.0,
    "multi_kill_4k":   45.0,
    "multi_kill_ace":  60.0,
    "clutch_attempt":  20.0,
    "clutch_win":      50.0,
}

HEADSHOT_BONUS = 3.0
WEAPON_BONUS = {
    "deagle": 4.0, "awp": 2.0, "ssg08": 3.0, "knife": 8.0,
    "knife_t": 8.0, "bayonet": 8.0, "taser": 10.0,
}
RAPID_KILL_BONUS_PER_KILL = 5.0   # bonus per kill within 3s window
ROUND_IMPORTANCE_BONUS = 5.0       # for match-point / overtime rounds


# ── Utility ──────────────────────────────────────────────────────────────────

def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _normalize_steamid64(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        if isinstance(value, str):
            s = value.strip()
            if not s or not s.isdigit():
                return ""
            int_val = int(s)
        elif isinstance(value, float):
            if value != value:
                return ""
            int_val = int(value)
        else:
            int_val = int(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if int_val <= 0:
        return ""
    sval = str(int_val)
    if len(sval) < 16 or len(sval) > 20:
        return ""
    return sval


def _make_highlight_id(event_type: str, round_num: int, player: str, tick: int) -> str:
    raw = f"{event_type}_{round_num}_{player}_{tick}"
    short_hash = hashlib.md5(raw.encode()).hexdigest()[:8]
    return f"hl_{event_type}_r{round_num}_{short_hash}"


def _resolve_player_steamid(parsed: dict, player_name: str) -> str:
    """Resolve steamid64 from parsed identities, then fallback to kill events."""
    identities = parsed.get("player_identities", {})
    if isinstance(identities, dict) and "by_name" in identities:
        by_name = identities.get("by_name") or {}
        sid = _normalize_steamid64(by_name.get(player_name))
        if sid:
            return sid
        lname = player_name.strip().lower()
        for name, steamid in by_name.items():
            if str(name).strip().lower() == lname:
                sid = _normalize_steamid64(steamid)
                if sid:
                    return sid

    # Fallback: scan kills for this player's steamid
    for k in parsed.get("kills", []):
        if _safe_str(k.get("attacker_name")) == player_name:
            sid = _normalize_steamid64(k.get("attacker_steamid"))
            if sid:
                return sid
        if _safe_str(k.get("victim_name")) == player_name:
            sid = _normalize_steamid64(k.get("victim_steamid"))
            if sid:
                return sid
    return ""


# ── Round metadata helpers ───────────────────────────────────────────────────

def _build_round_meta(parsed: dict) -> dict[int, dict]:
    """Build {round_num: {start, freeze_end, end, winner_side}} from rounds data."""
    meta = {}
    for r in parsed.get("rounds", []):
        rn = _safe_int(r.get("round_num", r.get("round")))
        if rn <= 0:
            continue
        meta[rn] = {
            "start": _safe_int(r.get("start")),
            "freeze_end": _safe_int(r.get("freeze_end")),
            "end": max(_safe_int(r.get("official_end")), _safe_int(r.get("end"))),
            "winner_side": _safe_str(r.get("winner_side") or r.get("winnerSide")),
        }
    return meta


def _build_kills_by_round(parsed: dict) -> dict[int, list[dict]]:
    """Group kills by round_num, sorted by tick."""
    by_round: dict[int, list[dict]] = {}
    for k in parsed.get("kills", []):
        rn = _safe_int(k.get("round_num"))
        if rn <= 0:
            continue
        by_round.setdefault(rn, []).append(k)
    for rn in by_round:
        by_round[rn].sort(key=lambda x: _safe_int(x.get("tick")))
    return by_round


# ── Highlight detectors ──────────────────────────────────────────────────────

def _detect_opening_kills(
    kills_by_round: dict[int, list[dict]],
    round_meta: dict[int, dict],
    parsed: dict,
) -> list[Highlight]:
    """Detect the first kill of each round as an opening kill."""
    highlights = []
    for rn, kills in kills_by_round.items():
        if not kills:
            continue
        first_kill = kills[0]
        attacker = _safe_str(first_kill.get("attacker_name"))
        if not attacker:
            continue

        tick = _safe_int(first_kill.get("tick"))
        victim = _safe_str(first_kill.get("victim_name"))
        weapon = _safe_str(first_kill.get("weapon"))
        headshot = bool(first_kill.get("headshot"))

        score = SCORE_WEIGHTS["opening_kill"]
        if headshot:
            score += HEADSHOT_BONUS
        score += WEAPON_BONUS.get(weapon.lower(), 0)

        rmeta = round_meta.get(rn, {})
        steamid = _normalize_steamid64(first_kill.get("attacker_steamid"))
        if not steamid:
            steamid = _resolve_player_steamid(parsed, attacker)

        hl = Highlight(
            highlight_id=_make_highlight_id("opening_kill", rn, attacker, tick),
            player_name=attacker,
            player_steamid64=steamid,
            round_number=rn,
            event_type="opening_kill",
            anchor_tick=tick,
            score=score,
            priority=0,
            confidence=0.95,
            selection_reason=f"First kill of round {rn}: {attacker} killed {victim} with {weapon}" + (" (headshot)" if headshot else ""),
            kills_in_sequence=[first_kill],
            round_start_tick=rmeta.get("start", 0),
            round_end_tick=rmeta.get("end", 0),
            freeze_end_tick=rmeta.get("freeze_end", 0),
        )
        highlights.append(hl)
    return highlights


def _detect_multi_kills(
    kills_by_round: dict[int, list[dict]],
    round_meta: dict[int, dict],
    parsed: dict,
) -> list[Highlight]:
    """Detect rounds where a player gets 2+ kills."""
    highlights = []
    for rn, kills in kills_by_round.items():
        player_kills: dict[str, list[dict]] = {}
        for k in kills:
            attacker = _safe_str(k.get("attacker_name"))
            if attacker:
                player_kills.setdefault(attacker, []).append(k)

        for player, pks in player_kills.items():
            if len(pks) < 2:
                continue

            kill_count = len(pks)
            if kill_count == 2:
                etype = "multi_kill_2k"
            elif kill_count == 3:
                etype = "multi_kill_3k"
            elif kill_count == 4:
                etype = "multi_kill_4k"
            else:
                etype = "multi_kill_ace"

            # Anchor = first kill tick
            first_tick = _safe_int(pks[0].get("tick"))
            last_tick = _safe_int(pks[-1].get("tick"))

            score = SCORE_WEIGHTS.get(etype, 18.0)

            # Rapid kill bonus: if all kills within 5 seconds
            if last_tick - first_tick <= _ticks(5):
                score += RAPID_KILL_BONUS_PER_KILL * kill_count

            # Headshot bonus
            hs_count = sum(1 for k in pks if k.get("headshot"))
            score += HEADSHOT_BONUS * hs_count * 0.5

            steamid = _normalize_steamid64(pks[0].get("attacker_steamid"))
            if not steamid:
                steamid = _resolve_player_steamid(parsed, player)

            rmeta = round_meta.get(rn, {})
            hl = Highlight(
                highlight_id=_make_highlight_id("multi_kill", rn, player, first_tick),
                player_name=player,
                player_steamid64=steamid,
                round_number=rn,
                event_type=etype.replace("multi_kill_", "multi_kill_"),  # keep as-is
                anchor_tick=first_tick,
                score=score,
                priority=0,
                confidence=0.9,
                selection_reason=f"{kill_count}K round by {player} in round {rn} ({hs_count} headshots)",
                kills_in_sequence=pks,
                round_start_tick=rmeta.get("start", 0),
                round_end_tick=rmeta.get("end", 0),
                freeze_end_tick=rmeta.get("freeze_end", 0),
            )
            highlights.append(hl)
    return highlights


def _detect_clutches(
    kills_by_round: dict[int, list[dict]],
    round_meta: dict[int, dict],
    parsed: dict,
    total_rounds: int,
) -> list[Highlight]:
    """Detect clutch attempts and wins (1vX situations)."""
    highlights = []

    all_players: set[str] = set()
    for kills in kills_by_round.values():
        for k in kills:
            a = _safe_str(k.get("attacker_name"))
            v = _safe_str(k.get("victim_name"))
            if a:
                all_players.add(a)
            if v:
                all_players.add(v)

    for rn, kills in kills_by_round.items():
        if not kills:
            continue
        rmeta = round_meta.get(rn, {})

        # Identify team composition from sides in this round's kills
        t_players: set[str] = set()
        ct_players: set[str] = set()
        for k in kills:
            for name_key, side_key in [("attacker_name", "attacker_side"), ("victim_name", "victim_side")]:
                name = _safe_str(k.get(name_key))
                side = _safe_str(k.get(side_key)).upper()
                if name and side in ("T", "CT"):
                    if side == "T":
                        t_players.add(name)
                    else:
                        ct_players.add(name)

        # Simulate round: track alive players
        for player in all_players:
            if player not in t_players and player not in ct_players:
                continue

            player_side = "T" if player in t_players else "CT"
            team_set = t_players if player_side == "T" else ct_players
            enemy_set = ct_players if player_side == "T" else t_players

            team_alive = set(team_set)
            enemy_alive = set(enemy_set)
            player_dead = False
            clutch_started = False
            clutch_vs = 0
            clutch_start_tick = 0
            clutch_kills: list[dict] = []

            for k in kills:
                victim = _safe_str(k.get("victim_name"))
                attacker = _safe_str(k.get("attacker_name"))
                tick = _safe_int(k.get("tick"))

                if victim in team_alive:
                    team_alive.discard(victim)
                    if victim == player:
                        player_dead = True
                elif victim in enemy_alive:
                    enemy_alive.discard(victim)

                # Detect clutch onset: player alive, alone on team, enemies remain
                if not clutch_started and not player_dead and player in team_alive:
                    if len(team_alive) == 1 and len(enemy_alive) > 0:
                        clutch_started = True
                        clutch_vs = len(enemy_alive)
                        clutch_start_tick = tick

                # Track kills by clutching player
                if clutch_started and not player_dead and attacker == player:
                    clutch_kills.append(k)

            if not clutch_started or clutch_vs < 1:
                continue

            won = not player_dead and len(enemy_alive) == 0
            etype = "clutch_win" if won else "clutch_attempt"
            score = SCORE_WEIGHTS[etype]
            score += clutch_vs * 5.0  # harder clutch = higher score
            if won:
                score += len(clutch_kills) * 3.0

            steamid = _resolve_player_steamid(parsed, player)

            hl = Highlight(
                highlight_id=_make_highlight_id(etype, rn, player, clutch_start_tick),
                player_name=player,
                player_steamid64=steamid,
                round_number=rn,
                event_type=etype,
                anchor_tick=clutch_start_tick,
                score=score,
                priority=0,
                confidence=0.85 if won else 0.7,
                selection_reason=f"1v{clutch_vs} {'won' if won else 'attempt'} by {player} in round {rn}" +
                                 (f" ({len(clutch_kills)} kills)" if clutch_kills else ""),
                kills_in_sequence=clutch_kills,
                clutch_vs=clutch_vs,
                clutch_won=won,
                round_start_tick=rmeta.get("start", 0),
                round_end_tick=rmeta.get("end", 0),
                freeze_end_tick=rmeta.get("freeze_end", 0),
            )
            highlights.append(hl)
    return highlights


def _detect_trade_kills(
    kills_by_round: dict[int, list[dict]],
    round_meta: dict[int, dict],
    parsed: dict,
) -> list[Highlight]:
    """Detect trade kills (teammate dies, player gets revenge within ~5s)."""
    TRADE_WINDOW_TICKS = _ticks(5)
    highlights = []

    for rn, kills in kills_by_round.items():
        rmeta = round_meta.get(rn, {})

        for i, k in enumerate(kills):
            attacker = _safe_str(k.get("attacker_name"))
            attacker_side = _safe_str(k.get("attacker_side")).upper()
            victim = _safe_str(k.get("victim_name"))
            tick = _safe_int(k.get("tick"))

            if not attacker or not victim:
                continue

            # Look backward: was a teammate of the attacker killed recently by the victim?
            for j in range(i - 1, max(i - 10, -1), -1):
                prev = kills[j]
                prev_tick = _safe_int(prev.get("tick"))
                if tick - prev_tick > TRADE_WINDOW_TICKS:
                    break

                prev_victim = _safe_str(prev.get("victim_name"))
                prev_victim_side = _safe_str(prev.get("victim_side")).upper()
                prev_attacker = _safe_str(prev.get("attacker_name"))

                # Trade: prev_victim was on same side as current attacker, prev_attacker is current victim
                if prev_victim_side == attacker_side and prev_attacker == victim:
                    steamid = _normalize_steamid64(k.get("attacker_steamid"))
                    if not steamid:
                        steamid = _resolve_player_steamid(parsed, attacker)

                    score = SCORE_WEIGHTS["trade_kill"]
                    delta_s = (tick - prev_tick) / TICKS_PER_SECOND
                    if delta_s <= 1.5:
                        score += 5.0  # instant trade bonus

                    hl = Highlight(
                        highlight_id=_make_highlight_id("trade_kill", rn, attacker, tick),
                        player_name=attacker,
                        player_steamid64=steamid,
                        round_number=rn,
                        event_type="trade_kill",
                        anchor_tick=tick,
                        score=score,
                        priority=0,
                        confidence=0.8,
                        selection_reason=f"Trade kill by {attacker}: avenged {prev_victim} by killing {victim} ({delta_s:.1f}s)",
                        kills_in_sequence=[prev, k],
                        round_start_tick=rmeta.get("start", 0),
                        round_end_tick=rmeta.get("end", 0),
                        freeze_end_tick=rmeta.get("freeze_end", 0),
                    )
                    highlights.append(hl)
                    break  # one trade per kill
    return highlights


# ── Main extraction ──────────────────────────────────────────────────────────

def extract_highlights(
    parsed: dict,
    player_filter: str | None = None,
    max_highlights: int = 20,
) -> list[dict]:
    """Extract and rank the best moments from parsed demo data.

    Args:
        parsed: Parsed demo data dict from parser.py
        player_filter: If set, only return highlights for this player
        max_highlights: Maximum number of highlights to return

    Returns:
        List of highlight dicts, sorted by score descending, with priority assigned.
    """
    round_meta = _build_round_meta(parsed)
    kills_by_round = _build_kills_by_round(parsed)
    total_rounds = _safe_int(parsed.get("total_rounds"))

    all_highlights: list[Highlight] = []

    all_highlights.extend(_detect_opening_kills(kills_by_round, round_meta, parsed))
    all_highlights.extend(_detect_multi_kills(kills_by_round, round_meta, parsed))
    all_highlights.extend(_detect_clutches(kills_by_round, round_meta, parsed, total_rounds))
    all_highlights.extend(_detect_trade_kills(kills_by_round, round_meta, parsed))

    # Filter by player if requested
    if player_filter:
        lname = player_filter.strip().lower()
        all_highlights = [h for h in all_highlights if h.player_name.strip().lower() == lname]

    # Deduplicate: prefer higher score when same player+round+type
    seen: dict[str, Highlight] = {}
    for hl in all_highlights:
        dedup_key = f"{hl.player_name}_{hl.round_number}_{hl.event_type}"
        existing = seen.get(dedup_key)
        if existing is None or hl.score > existing.score:
            seen[dedup_key] = hl
    deduped = list(seen.values())

    # Sort by score descending
    deduped.sort(key=lambda h: h.score, reverse=True)

    # Assign priority
    for i, hl in enumerate(deduped):
        hl.priority = i + 1

    # Limit
    result = deduped[:max_highlights]

    return [hl.to_dict() for hl in result]
