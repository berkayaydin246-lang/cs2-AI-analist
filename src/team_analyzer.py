"""
team_analyzer.py
Team-level analysis helpers for single-demo workflows.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


TRADE_WINDOW_TICKS = 320
FLASH_WINDOW_TICKS = 192
EARLY_ROUND_TICKS = 15 * 64
ENTRY_WINDOW_TICKS = 20 * 64


_SITE_CENTERS = {
    "de_mirage": {
        "A": (800.0, -300.0),
        "B": (-1600.0, 400.0),
    },
    "de_dust2": {
        "A": (1200.0, 2400.0),
        "B": (-1450.0, 2450.0),
    },
    "de_inferno": {
        "A": (1300.0, 700.0),
        "B": (400.0, 2600.0),
    },
    "de_nuke": {
        "A": (350.0, -400.0),
        "B": (-600.0, -1450.0),
    },
    "de_ancient": {
        "A": (1100.0, 400.0),
        "B": (-1200.0, 1200.0),
    },
    "de_anubis": {
        "A": (900.0, 2100.0),
        "B": (-1200.0, 2400.0),
    },
    "de_vertigo": {
        "A": (-400.0, -700.0),
        "B": (-1450.0, 300.0),
    },
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_side(side_val: Any) -> str:
    side = str(side_val or "").strip().lower()
    if side in ("2", "t", "terrorist"):
        return "T"
    if side in ("3", "ct", "counter-terrorist", "counterterrorist"):
        return "CT"
    return str(side_val or "").upper()


def _round_num(row: dict[str, Any]) -> int:
    return _safe_int(row.get("round_num", row.get("round", 0)), 0)


def _extract_players(parsed_data: dict[str, Any]) -> list[str]:
    players = set(parsed_data.get("players", []) or [])
    for k in parsed_data.get("kills", []):
        if k.get("attacker_name"):
            players.add(k["attacker_name"])
        if k.get("victim_name"):
            players.add(k["victim_name"])
    return sorted(p for p in players if isinstance(p, str) and p)


def _build_side_by_round(
    kills: list[dict[str, Any]], player_positions: list[dict[str, Any]]
) -> dict[str, dict[int, str]]:
    side_votes: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))

    for pos in player_positions:
        player = pos.get("player_name")
        if not player:
            continue
        rn = _round_num(pos)
        if rn <= 0:
            continue
        side = _normalize_side(pos.get("side"))
        if side in ("T", "CT"):
            side_votes[player][rn].append(side)

    for kill in kills:
        rn = _round_num(kill)
        if rn <= 0:
            continue
        attacker = kill.get("attacker_name")
        victim = kill.get("victim_name")
        att_side = _normalize_side(kill.get("attacker_side"))
        vic_side = _normalize_side(kill.get("victim_side"))
        if attacker and att_side in ("T", "CT"):
            side_votes[attacker][rn].append(att_side)
        if victim and vic_side in ("T", "CT"):
            side_votes[victim][rn].append(vic_side)

    result: dict[str, dict[int, str]] = {}
    for player, by_round in side_votes.items():
        result[player] = {}
        for rn, votes in by_round.items():
            if votes:
                result[player][rn] = Counter(votes).most_common(1)[0][0]
    return result


def _assign_teams(
    kills: list[dict[str, Any]],
    player_positions: list[dict[str, Any]],
    total_rounds: int,
) -> dict[str, Any]:
    players = sorted({
        p.get("player_name")
        for p in player_positions
        if p.get("player_name")
    } | {
        k.get("attacker_name") for k in kills if k.get("attacker_name")
    } | {
        k.get("victim_name") for k in kills if k.get("victim_name")
    })

    side_by_round = _build_side_by_round(kills, player_positions)
    half_split = max(1, min(12, total_rounds // 2 if total_rounds else 12))

    first_half_pref: dict[str, str] = {}
    first_half_score: dict[str, tuple[int, int]] = {}
    for player in players:
        votes = [
            side
            for rn, side in side_by_round.get(player, {}).items()
            if 1 <= rn <= half_split and side in ("T", "CT")
        ]
        if not votes:
            first_half_pref[player] = "UNK"
            first_half_score[player] = (0, 0)
            continue
        c = Counter(votes)
        first_half_pref[player] = c.most_common(1)[0][0]
        first_half_score[player] = (c.get("CT", 0), c.get("T", 0))

    ct_candidates = [p for p in players if first_half_pref.get(p) == "CT"]
    if len(ct_candidates) < 5:
        ranked = sorted(players, key=lambda p: first_half_score.get(p, (0, 0))[0], reverse=True)
        for p in ranked:
            if p not in ct_candidates:
                ct_candidates.append(p)
            if len(ct_candidates) >= 5:
                break
    team1_players = sorted(ct_candidates[:5])
    team2_players = sorted([p for p in players if p not in set(team1_players)])

    if len(team2_players) > 5:
        team2_players = team2_players[:5]

    player_to_team = {}
    for p in team1_players:
        player_to_team[p] = "team1"
    for p in team2_players:
        player_to_team[p] = "team2"

    team_side_by_round: dict[str, dict[int, str]] = {"team1": {}, "team2": {}}
    for team_id, plist in (("team1", team1_players), ("team2", team2_players)):
        all_rounds = sorted({
            rn
            for player in plist
            for rn in side_by_round.get(player, {}).keys()
        })
        for rn in all_rounds:
            votes = [
                side_by_round.get(player, {}).get(rn)
                for player in plist
                if side_by_round.get(player, {}).get(rn) in ("T", "CT")
            ]
            if votes:
                team_side_by_round[team_id][rn] = Counter(votes).most_common(1)[0][0]

    return {
        "players": players,
        "player_to_team": player_to_team,
        "team1_players": team1_players,
        "team2_players": team2_players,
        "side_by_round": side_by_round,
        "team_side_by_round": team_side_by_round,
    }


def _player_core_stats(
    parsed_data: dict[str, Any],
    player_name: str,
    total_rounds: int,
) -> dict[str, Any]:
    kills = parsed_data.get("kills", [])
    damages = parsed_data.get("damages", [])

    player_kills = [k for k in kills if k.get("attacker_name") == player_name]
    player_deaths = [k for k in kills if k.get("victim_name") == player_name]
    headshots = [k for k in player_kills if bool(k.get("headshot"))]

    total_damage = sum(
        _safe_float(d.get("hp_damage"), 0.0)
        for d in damages
        if d.get("attacker_name") == player_name
    )

    by_round = defaultdict(list)
    for k in kills:
        rn = _round_num(k)
        if rn > 0:
            by_round[rn].append(k)

    opening_kills = 0
    opening_deaths = 0
    multi_by_round = defaultdict(int)
    for k in player_kills:
        rn = _round_num(k)
        if rn > 0:
            multi_by_round[rn] += 1

    for rn, rkills in by_round.items():
        first = min(rkills, key=lambda x: _safe_int(x.get("tick"), 10**9))
        if first.get("attacker_name") == player_name:
            opening_kills += 1
        if first.get("victim_name") == player_name:
            opening_deaths += 1

    clutches_won = 0
    kast_rounds = 0
    for rn, rkills in by_round.items():
        sorted_k = sorted(rkills, key=lambda x: _safe_int(x.get("tick"), 0))
        has_kill = any(k.get("attacker_name") == player_name for k in sorted_k)
        died_event = next((k for k in sorted_k if k.get("victim_name") == player_name), None)
        has_survive = died_event is None

        has_trade = False
        if died_event:
            death_tick = _safe_int(died_event.get("tick"), 0)
            killer = died_event.get("attacker_name")
            for k2 in sorted_k:
                if _safe_int(k2.get("tick"), 0) <= death_tick:
                    continue
                if _safe_int(k2.get("tick"), 0) - death_tick > TRADE_WINDOW_TICKS:
                    break
                if k2.get("victim_name") == killer:
                    has_trade = True
                    break

        if has_kill or has_survive or has_trade:
            kast_rounds += 1

        # basic clutch: became last alive and won the round
        teammates_alive = 5
        opp_alive = 5
        for ev in sorted_k:
            if ev.get("victim_name") == player_name:
                teammates_alive = 0
                break
            if ev.get("attacker_name") == player_name:
                opp_alive = max(0, opp_alive - 1)
            else:
                teammates_alive = max(0, teammates_alive - (1 if ev.get("victim_side") == ev.get("attacker_side") else 0))
        if teammates_alive == 1 and opp_alive >= 1 and has_survive and has_kill:
            clutches_won += 1

    kill_count = len(player_kills)
    death_count = len(player_deaths)
    hs_rate = round((len(headshots) / max(kill_count, 1)) * 100, 1)
    adr = round(total_damage / max(total_rounds, 1), 1)
    kpr = kill_count / max(total_rounds, 1)
    dpr = death_count / max(total_rounds, 1)

    multi_3k = sum(1 for v in multi_by_round.values() if v == 3)
    multi_4k = sum(1 for v in multi_by_round.values() if v == 4)
    multi_5k = sum(1 for v in multi_by_round.values() if v >= 5)

    impact = (
        (opening_kills * 0.15)
        + (multi_3k * 0.3)
        + (multi_4k * 0.45)
        + (multi_5k * 0.6)
        + (clutches_won * 0.2)
    ) / max(total_rounds, 1)
    impact_rating = round(min(impact * 10, 3.0), 2)

    kast_pct = round((kast_rounds / max(total_rounds, 1)) * 100, 1)
    hltv_rating = round(
        0.0073 * kast_pct
        + 0.3591 * kpr
        - 0.5329 * dpr
        + 0.2372 * impact_rating
        + 0.0032 * adr
        + 0.1587,
        2,
    )

    return {
        "player": player_name,
        "kills": kill_count,
        "deaths": death_count,
        "kd_ratio": round(kill_count / max(death_count, 1), 2),
        "adr": adr,
        "kast": kast_pct,
        "hs_rate": hs_rate,
        "opening_kills": opening_kills,
        "opening_deaths": opening_deaths,
        "impact_rating": impact_rating,
        "rating": hltv_rating,
    }


def _build_scoreboard(
    parsed_data: dict[str, Any],
    team_map: dict[str, Any],
    total_rounds: int,
) -> list[dict[str, Any]]:
    rows = []
    for player in team_map.get("players", []):
        stats = _player_core_stats(parsed_data, player, total_rounds)
        stats["team"] = team_map.get("player_to_team", {}).get(player, "unknown")
        rows.append(stats)
    rows.sort(key=lambda x: x.get("rating", 0.0), reverse=True)
    return rows


def _site_from_position(map_name: str, x: float, y: float) -> str:
    centers = _SITE_CENTERS.get(map_name)
    if not centers:
        return "Unknown"

    def _dist2(cx: float, cy: float) -> float:
        return (x - cx) ** 2 + (y - cy) ** 2

    da = _dist2(*centers["A"])
    db = _dist2(*centers["B"])
    return "A" if da <= db else "B"


def _detect_ct_setups(
    player_positions: list[dict[str, Any]],
    team_data: dict[str, Any],
    map_name: str,
) -> list[dict[str, Any]]:
    by_round_player: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for pos in player_positions:
        rn = _round_num(pos)
        player = pos.get("player_name")
        if rn <= 0 or not player:
            continue
        by_round_player[rn][player].append(pos)

    setups = []
    for rn, players_map in sorted(by_round_player.items()):
        for team_id in ("team1", "team2"):
            team_side = team_data.get("team_side_by_round", {}).get(team_id, {}).get(rn)
            if team_side != "CT":
                continue
            a_players = []
            b_players = []

            for player in team_data.get(f"{team_id}_players", []):
                rows = players_map.get(player, [])
                if not rows:
                    continue
                first = min(rows, key=lambda r: _safe_int(r.get("tick"), 10**9))
                x = _safe_float(first.get("x"), None)
                y = _safe_float(first.get("y"), None)
                tick = _safe_int(first.get("tick"), 0)
                if x is None or y is None:
                    continue
                if tick > EARLY_ROUND_TICKS:
                    continue
                site = _site_from_position(map_name, x, y)
                if site == "A":
                    a_players.append(player)
                elif site == "B":
                    b_players.append(player)

            if a_players or b_players:
                setups.append(
                    {
                        "round": rn,
                        "team": team_id,
                        "a_anchors": len(a_players),
                        "b_anchors": len(b_players),
                        "a_players": ", ".join(sorted(a_players)),
                        "b_players": ", ".join(sorted(b_players)),
                        "setup_type": f"{len(a_players)}A-{len(b_players)}B",
                    }
                )
    return setups


def _detect_t_executes(
    kills: list[dict[str, Any]],
    player_positions: list[dict[str, Any]],
    team_data: dict[str, Any],
    map_name: str,
) -> list[dict[str, Any]]:
    round_start_tick = {}
    by_round_pos = defaultdict(list)
    for pos in player_positions:
        rn = _round_num(pos)
        if rn <= 0:
            continue
        tick = _safe_int(pos.get("tick"), 0)
        by_round_pos[rn].append(tick)
    for rn, ticks in by_round_pos.items():
        if ticks:
            round_start_tick[rn] = min(ticks)

    by_round_kills = defaultdict(list)
    for k in kills:
        rn = _round_num(k)
        if rn > 0:
            by_round_kills[rn].append(k)

    executes = []
    for rn, rkills in sorted(by_round_kills.items()):
        for team_id in ("team1", "team2"):
            if team_data.get("team_side_by_round", {}).get(team_id, {}).get(rn) != "T":
                continue

            team_players = set(team_data.get(f"{team_id}_players", []))
            team_kills = [k for k in rkills if k.get("attacker_name") in team_players]
            if len(team_kills) < 2:
                continue

            site_counts = Counter()
            first_contact_tick = None
            for ev in team_kills:
                tick = _safe_int(ev.get("tick"), 0)
                if first_contact_tick is None or (tick and tick < first_contact_tick):
                    first_contact_tick = tick

                x = _safe_float(ev.get("victim_x", ev.get("victim_X")), None)
                y = _safe_float(ev.get("victim_y", ev.get("victim_Y")), None)
                if x is None or y is None:
                    continue
                site = _site_from_position(map_name, x, y)
                if site in ("A", "B"):
                    site_counts[site] += 1

            if not site_counts:
                continue
            target_site, on_site_kills = site_counts.most_common(1)[0]
            if on_site_kills < 2:
                continue

            start_tick = round_start_tick.get(rn, 0)
            contact_time = 0.0
            if start_tick and first_contact_tick and first_contact_tick > start_tick:
                contact_time = round((first_contact_tick - start_tick) / 64.0, 1)

            executes.append(
                {
                    "round": rn,
                    "team": team_id,
                    "site": target_site,
                    "site_kills": on_site_kills,
                    "total_team_kills": len(team_kills),
                    "first_contact_s": contact_time,
                }
            )
    return executes


def _team_coordination(
    kills: list[dict[str, Any]],
    grenades: list[dict[str, Any]],
    team_data: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    by_round_kills = defaultdict(list)
    for k in kills:
        rn = _round_num(k)
        if rn > 0:
            by_round_kills[rn].append(k)

    coord = {}
    for team_id in ("team1", "team2"):
        team_players = set(team_data.get(f"{team_id}_players", []))
        death_events = []
        trade_hits = 0
        refrag_ticks = []

        for rn, rkills in by_round_kills.items():
            sorted_k = sorted(rkills, key=lambda x: _safe_int(x.get("tick"), 0))
            for ev in sorted_k:
                victim = ev.get("victim_name")
                if victim not in team_players:
                    continue
                killer = ev.get("attacker_name")
                death_tick = _safe_int(ev.get("tick"), 0)
                death_events.append((rn, death_tick))

                for ev2 in sorted_k:
                    tick2 = _safe_int(ev2.get("tick"), 0)
                    if tick2 <= death_tick:
                        continue
                    if tick2 - death_tick > TRADE_WINDOW_TICKS:
                        break
                    if ev2.get("attacker_name") in team_players and ev2.get("victim_name") == killer:
                        trade_hits += 1
                        refrag_ticks.append(tick2 - death_tick)
                        break

        flashes = [g for g in grenades if g.get("thrower_name") in team_players and g.get("grenade_type") == "flash"]
        flash_to_kill = 0
        for fl in flashes:
            flash_tick = _safe_int(fl.get("tick"), 0)
            if flash_tick <= 0:
                continue
            for ev in kills:
                kill_tick = _safe_int(ev.get("tick"), 0)
                if kill_tick <= flash_tick:
                    continue
                if kill_tick - flash_tick > FLASH_WINDOW_TICKS:
                    continue
                if ev.get("attacker_name") in team_players:
                    flash_to_kill += 1
                    break

        total_deaths = len(death_events)
        traded_rate = round((trade_hits / max(total_deaths, 1)) * 100, 1)
        avg_refrag_ms = round((sum(refrag_ticks) / max(len(refrag_ticks), 1)) / 64.0 * 1000, 1) if refrag_ticks else 0.0
        flash_combo_rate = round((flash_to_kill / max(len(flashes), 1)) * 100, 1)
        coordination_score = round(
            min(100.0, (traded_rate * 0.5) + (flash_combo_rate * 0.3) + (max(0.0, 100.0 - min(avg_refrag_ms, 2000.0) / 20.0) * 0.2)),
            1,
        )

        coord[team_id] = {
            "team": team_id,
            "traded_deaths": trade_hits,
            "team_deaths": total_deaths,
            "traded_rate": traded_rate,
            "avg_refrag_ms": avg_refrag_ms,
            "flash_count": len(flashes),
            "flash_to_kill": flash_to_kill,
            "flash_combo_rate": flash_combo_rate,
            "coordination_score": coordination_score,
        }

    return coord


def _team_aggregate(scoreboard: list[dict[str, Any]], team_id: str) -> dict[str, Any]:
    rows = [r for r in scoreboard if r.get("team") == team_id]
    if not rows:
        return {
            "players": 0,
            "kills": 0,
            "deaths": 0,
            "team_adr": 0.0,
            "team_kast": 0.0,
            "avg_rating": 0.0,
            "opening_kills": 0,
            "opening_deaths": 0,
        }

    return {
        "players": len(rows),
        "kills": sum(r.get("kills", 0) for r in rows),
        "deaths": sum(r.get("deaths", 0) for r in rows),
        "team_adr": round(sum(r.get("adr", 0.0) for r in rows) / len(rows), 1),
        "team_kast": round(sum(r.get("kast", 0.0) for r in rows) / len(rows), 1),
        "avg_rating": round(sum(r.get("rating", 0.0) for r in rows) / len(rows), 2),
        "opening_kills": sum(r.get("opening_kills", 0) for r in rows),
        "opening_deaths": sum(r.get("opening_deaths", 0) for r in rows),
    }


def tag_rounds(parsed_data: dict[str, Any], team_data: dict[str, Any]) -> list[dict[str, Any]]:
    rounds = parsed_data.get("rounds", [])
    kills = parsed_data.get("kills", [])
    player_positions = parsed_data.get("player_positions", [])
    total_rounds = parsed_data.get("total_rounds", 0)

    by_round_kills = defaultdict(list)
    for k in kills:
        rn = _round_num(k)
        if rn > 0:
            by_round_kills[rn].append(k)

    by_round_start_tick = defaultdict(list)
    for p in player_positions:
        rn = _round_num(p)
        if rn > 0:
            by_round_start_tick[rn].append(_safe_int(p.get("tick"), 0))

    tags = []
    for rn in range(1, max(total_rounds, len(rounds)) + 1):
        current_tags: list[str] = []

        if rn == 1 or rn == 13:
            current_tags.append("pistol")

        round_row = rounds[rn - 1] if 0 <= rn - 1 < len(rounds) else {}
        t_eq = _safe_float(round_row.get("t_eq_val"), 0.0)
        ct_eq = _safe_float(round_row.get("ct_eq_val"), 0.0)

        if t_eq > 0:
            if t_eq < 8000:
                current_tags.append("eco_t")
            elif t_eq < 20000:
                current_tags.append("force_t")
        if ct_eq > 0:
            if ct_eq < 8000:
                current_tags.append("eco_ct")
            elif ct_eq < 20000:
                current_tags.append("force_ct")

        if t_eq >= 20000 and ct_eq >= 20000:
            current_tags.append("full_buy")
        if (t_eq >= 20000 and ct_eq < 8000) or (ct_eq >= 20000 and t_eq < 8000):
            current_tags.append("anti_eco")

        rkills = by_round_kills.get(rn, [])
        if rkills:
            first_kill_tick = min(_safe_int(k.get("tick"), 10**9) for k in rkills)
            start_tick = min(by_round_start_tick.get(rn, [first_kill_tick]))
            if first_kill_tick - start_tick <= ENTRY_WINDOW_TICKS:
                current_tags.append("entry_round")

            kill_counts = Counter(k.get("attacker_name") for k in rkills if k.get("attacker_name"))
            if any(v >= 5 for v in kill_counts.values()):
                current_tags.append("ace")

            # clutch_1vX approximate detection from alive counts
            t_alive = 5
            ct_alive = 5
            clutch_seen = False
            for ev in sorted(rkills, key=lambda x: _safe_int(x.get("tick"), 0)):
                side = _normalize_side(ev.get("victim_side"))
                if side == "T":
                    t_alive = max(0, t_alive - 1)
                elif side == "CT":
                    ct_alive = max(0, ct_alive - 1)
                if (t_alive == 1 and ct_alive >= 2) or (ct_alive == 1 and t_alive >= 2):
                    clutch_seen = True
            if clutch_seen:
                current_tags.append("clutch_1vX")

        tags.append({
            "round": rn,
            "tags": sorted(set(current_tags)),
            "manual_tags": [],
        })

    return tags


def apply_manual_round_tags(
    round_tags: list[dict[str, Any]], manual_tags: dict[int, list[str]] | None
) -> list[dict[str, Any]]:
    manual_tags = manual_tags or {}
    merged = []
    for row in round_tags:
        rn = _safe_int(row.get("round"), 0)
        current_manual = sorted(set(manual_tags.get(rn, []) or []))
        merged.append(
            {
                "round": rn,
                "tags": sorted(set(row.get("tags", []))),
                "manual_tags": current_manual,
                "all_tags": sorted(set((row.get("tags", []) or []) + current_manual)),
            }
        )
    return merged


def _compute_team_scores(
    parsed_data: dict[str, Any],
    team_side_by_round: dict[str, dict[int, str]],
) -> dict[str, int]:
    """Count rounds won by each team using rounds winner_side + team side map."""
    rounds = parsed_data.get("rounds", [])
    team1_score = 0
    team2_score = 0
    t1_sides = team_side_by_round.get("team1", {})
    t2_sides = team_side_by_round.get("team2", {})

    for r in rounds:
        rn = _safe_int(r.get("round_num", r.get("round", 0)), 0)
        raw_winner = str(r.get("winner_side") or r.get("winnerSide") or "").strip()
        if not rn or not raw_winner:
            continue
        winner_side = _normalize_side(raw_winner)
        if winner_side not in ("T", "CT"):
            continue
        if t1_sides.get(rn) == winner_side:
            team1_score += 1
        elif t2_sides.get(rn) == winner_side:
            team2_score += 1

    return {"team1": team1_score, "team2": team2_score}


def analyze_team(parsed_data: dict[str, Any]) -> dict[str, Any]:
    kills = parsed_data.get("kills", [])
    grenades = parsed_data.get("grenades", [])
    player_positions = parsed_data.get("player_positions", [])
    map_name = parsed_data.get("map", "unknown")
    total_rounds = parsed_data.get("total_rounds", 0)

    team_data = _assign_teams(kills, player_positions, total_rounds)

    # fallback to parsed players if team extraction was sparse
    if not team_data.get("players"):
        team_data["players"] = _extract_players(parsed_data)

    scoreboard = _build_scoreboard(parsed_data, team_data, total_rounds)
    team1_agg = _team_aggregate(scoreboard, "team1")
    team2_agg = _team_aggregate(scoreboard, "team2")

    ct_setups = _detect_ct_setups(player_positions, team_data, map_name)
    t_executes = _detect_t_executes(kills, player_positions, team_data, map_name)
    coordination = _team_coordination(kills, grenades, team_data)
    round_tags = tag_rounds(parsed_data, team_data)
    team_side_by_round = team_data.get("team_side_by_round", {})
    score = _compute_team_scores(parsed_data, team_side_by_round)

    return {
        "map": map_name,
        "total_rounds": total_rounds,
        "score": score,
        "team_map": team_data.get("player_to_team", {}),
        "teams": {
            "team1": {
                "id": "team1",
                "name": "Team 1",
                "players": team_data.get("team1_players", []),
                "aggregate": team1_agg,
            },
            "team2": {
                "id": "team2",
                "name": "Team 2",
                "players": team_data.get("team2_players", []),
                "aggregate": team2_agg,
            },
        },
        "scoreboard": scoreboard,
        "ct_setups": ct_setups,
        "t_executes": t_executes,
        "coordination": coordination,
        "round_tags": round_tags,
        "side_by_round": team_data.get("side_by_round", {}),
        "team_side_by_round": team_side_by_round,
    }
