"""
highlights.py
Reusable highlight detection engine for parsed CS2 demo sessions.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone


HIGHLIGHT_SCHEMA_VERSION = 1
TRADE_WINDOW_TICKS = 320
FLASH_ASSIST_WINDOW_TICKS = 192
GRENADE_DAMAGE_WINDOW_TICKS = 96
MULTIKILL_PRE_TICKS = 64
MULTIKILL_POST_TICKS = 160


def detect_highlights(parsed_data: dict) -> dict:
    rounds = parsed_data.get("rounds", []) or []
    kills = parsed_data.get("kills", []) or []
    damages = parsed_data.get("damages", []) or []
    grenades = parsed_data.get("grenades", []) or []
    bomb_events = parsed_data.get("bomb_events", []) or []
    player_positions = parsed_data.get("player_positions", []) or []

    round_map = {int(r.get("round_num")): r for r in rounds if _safe_int(r.get("round_num"), None) is not None}
    kills_by_round = _group_by_round(kills)
    damages_by_round = _group_by_round(damages)
    grenades_by_round = _group_by_round(grenades)
    bomb_by_round = _group_by_round(bomb_events)
    rosters_by_round = _build_round_rosters(player_positions, kills)

    highlights: list[dict] = []
    highlights.extend(_detect_multikills(kills_by_round))
    highlights.extend(_detect_opening_kills(kills_by_round))
    highlights.extend(_detect_trade_kills(kills_by_round))
    highlights.extend(_detect_clutches(kills_by_round, round_map, rosters_by_round))
    highlights.extend(_detect_flash_assists(kills_by_round, grenades_by_round))
    highlights.extend(_detect_grenade_damage_spikes(damages_by_round))
    highlights.extend(_detect_bomb_pressure_moments(bomb_by_round, kills_by_round))

    deduped = _deduplicate_highlights(highlights)
    finalized = _finalize_highlights(deduped)
    summary = _build_highlight_summary(finalized)
    return {
        "schema_version": HIGHLIGHT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_highlights": len(finalized),
        "counts_by_type": summary["counts_by_type"],
        "counts_by_round": summary["counts_by_round"],
        "warnings": summary["warnings"],
        "highlights": finalized,
    }


def _safe_int(value, default: int | None = 0) -> int | None:
    if value in ("", None):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clean_text(value) -> str:
    return str(value or "").strip()


def _normalize_side(value) -> str:
    side = _clean_text(value).upper()
    if side.startswith("CT"):
        return "CT"
    if side.startswith("T"):
        return "T"
    return side


def _other_side(side: str) -> str:
    return "CT" if side == "T" else "T"


def _group_by_round(rows: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows or []:
        round_num = _safe_int(row.get("round_num"), None)
        if round_num is None:
            continue
        grouped[round_num].append(row)
    for round_rows in grouped.values():
        round_rows.sort(key=lambda item: _safe_int(item.get("tick"), 0) or 0)
    return grouped


def _build_round_rosters(player_positions: list[dict], kills: list[dict]) -> dict[int, dict[str, set[str]]]:
    rosters: dict[int, dict[str, set[str]]] = defaultdict(lambda: {"T": set(), "CT": set()})
    for row in player_positions or []:
        round_num = _safe_int(row.get("round_num"), None)
        side = _normalize_side(row.get("side"))
        name = _clean_text(row.get("player_name"))
        if round_num is None or side not in {"T", "CT"} or not name:
            continue
        rosters[round_num][side].add(name)
    for kill in kills or []:
        round_num = _safe_int(kill.get("round_num"), None)
        if round_num is None:
            continue
        attacker_name = _clean_text(kill.get("attacker_name"))
        attacker_side = _normalize_side(kill.get("attacker_side"))
        victim_name = _clean_text(kill.get("victim_name"))
        victim_side = _normalize_side(kill.get("victim_side"))
        if attacker_name and attacker_side in {"T", "CT"}:
            rosters[round_num][attacker_side].add(attacker_name)
        if victim_name and victim_side in {"T", "CT"}:
            rosters[round_num][victim_side].add(victim_name)
    return rosters


def _build_highlight(
    *,
    htype: str,
    title: str,
    description: str,
    round_number: int,
    start_tick: int,
    end_tick: int,
    anchor_tick: int,
    primary_player: str,
    involved_players: list[str],
    side: str,
    score: float,
    tags: list[str],
    metadata: dict,
) -> dict:
    start_tick = max(0, int(start_tick))
    end_tick = max(start_tick, int(end_tick))
    anchor_tick = max(start_tick, min(int(anchor_tick), end_tick))
    return {
        "highlight_id": "",
        "type": htype,
        "title": title,
        "description": description,
        "round_number": int(round_number),
        "start_tick": start_tick,
        "end_tick": end_tick,
        "anchor_tick": anchor_tick,
        "primary_player": primary_player,
        "involved_players": sorted({p for p in involved_players if p}),
        "side": side,
        "score": round(max(0.0, min(score, 1.0)), 3),
        "tags": sorted({tag for tag in tags if tag}),
        "metadata": metadata or {},
    }


def _detect_multikills(kills_by_round: dict[int, list[dict]]) -> list[dict]:
    highlights = []
    labels = {2: "Double Kill", 3: "Triple Kill", 4: "4K", 5: "Ace"}
    for round_num, kills in kills_by_round.items():
        by_player: dict[str, list[dict]] = defaultdict(list)
        for kill in kills:
            attacker = _clean_text(kill.get("attacker_name"))
            if attacker:
                by_player[attacker].append(kill)
        for attacker, player_kills in by_player.items():
            kill_count = len(player_kills)
            if kill_count < 2:
                continue
            first_tick = _safe_int(player_kills[0].get("tick"), 0) or 0
            last_tick = _safe_int(player_kills[-1].get("tick"), first_tick) or first_tick
            span = max(1, last_tick - first_tick)
            score = {2: 0.66, 3: 0.78, 4: 0.9}.get(kill_count, 0.98)
            score += min(0.08, 160.0 / span * 0.05)
            victims = [_clean_text(k.get("victim_name")) for k in player_kills]
            side = _normalize_side(player_kills[0].get("attacker_side"))
            highlights.append(_build_highlight(
                htype="ace" if kill_count >= 5 else "multi_kill",
                title=labels.get(min(kill_count, 5), "Multi Kill"),
                description=f"{attacker} got {kill_count} kills in round {round_num}",
                round_number=round_num,
                start_tick=first_tick - MULTIKILL_PRE_TICKS,
                end_tick=last_tick + MULTIKILL_POST_TICKS,
                anchor_tick=last_tick,
                primary_player=attacker,
                involved_players=[attacker, *victims],
                side=side,
                score=score,
                tags=["kill", "impact", "multi-kill", f"{kill_count}k"],
                metadata={"kill_count": kill_count, "victims": victims, "kill_ticks": [_safe_int(k.get("tick"), 0) or 0 for k in player_kills]},
            ))
    return highlights


def _detect_opening_kills(kills_by_round: dict[int, list[dict]]) -> list[dict]:
    highlights = []
    for round_num, kills in kills_by_round.items():
        if not kills:
            continue
        first = kills[0]
        tick = _safe_int(first.get("tick"), 0) or 0
        attacker = _clean_text(first.get("attacker_name"))
        victim = _clean_text(first.get("victim_name"))
        side = _normalize_side(first.get("attacker_side"))
        highlights.append(_build_highlight(
            htype="opening_kill",
            title="Opening Kill",
            description=f"{attacker} opened the round against {victim}",
            round_number=round_num,
            start_tick=max(0, tick - 128),
            end_tick=tick + 160,
            anchor_tick=tick,
            primary_player=attacker,
            involved_players=[attacker, victim],
            side=side,
            score=0.58,
            tags=["opening", "kill", "entry"],
            metadata={"victim": victim, "weapon": _clean_text(first.get("weapon"))},
        ))
    return highlights


def _detect_trade_kills(kills_by_round: dict[int, list[dict]]) -> list[dict]:
    highlights = []
    for round_num, kills in kills_by_round.items():
        for idx, kill in enumerate(kills):
            trade_tick = _safe_int(kill.get("tick"), 0) or 0
            attacker = _clean_text(kill.get("attacker_name"))
            victim = _clean_text(kill.get("victim_name"))
            attacker_side = _normalize_side(kill.get("attacker_side"))
            for previous in reversed(kills[:idx]):
                prev_tick = _safe_int(previous.get("tick"), 0) or 0
                if trade_tick - prev_tick > TRADE_WINDOW_TICKS:
                    break
                traded_teammate = _clean_text(previous.get("victim_name"))
                previous_killer = _clean_text(previous.get("attacker_name"))
                if not traded_teammate or not previous_killer:
                    continue
                if victim != previous_killer:
                    continue
                if attacker_side != _normalize_side(previous.get("victim_side")):
                    continue
                highlights.append(_build_highlight(
                    htype="trade_kill",
                    title="Trade Kill",
                    description=f"{attacker} traded {traded_teammate} in {trade_tick - prev_tick} ticks",
                    round_number=round_num,
                    start_tick=prev_tick - 64,
                    end_tick=trade_tick + 128,
                    anchor_tick=trade_tick,
                    primary_player=attacker,
                    involved_players=[attacker, traded_teammate, victim],
                    side=attacker_side,
                    score=0.62,
                    tags=["trade", "kill", "teamplay"],
                    metadata={"traded_teammate": traded_teammate, "traded_opponent": victim, "response_ticks": trade_tick - prev_tick},
                ))
                break
    return highlights


def _detect_clutches(kills_by_round: dict[int, list[dict]], round_map: dict[int, dict], rosters_by_round: dict[int, dict[str, set[str]]]) -> list[dict]:
    highlights = []
    for round_num, kills in kills_by_round.items():
        rosters = rosters_by_round.get(round_num, {"T": set(), "CT": set()})
        alive = {"T": set(rosters.get("T", set())), "CT": set(rosters.get("CT", set()))}
        candidates: dict[tuple[str, str], dict] = {}
        for kill in kills:
            victim = _clean_text(kill.get("victim_name"))
            victim_side = _normalize_side(kill.get("victim_side"))
            attacker = _clean_text(kill.get("attacker_name"))
            tick = _safe_int(kill.get("tick"), 0) or 0
            if victim and victim_side in alive:
                alive[victim_side].discard(victim)
            for side in ("T", "CT"):
                enemy_side = _other_side(side)
                if len(alive[side]) == 1 and len(alive[enemy_side]) >= 2:
                    clutch_player = next(iter(alive[side]))
                    key = (clutch_player, side)
                    if key not in candidates:
                        candidates[key] = {
                            "player": clutch_player,
                            "side": side,
                            "vs": len(alive[enemy_side]),
                            "start_tick": tick,
                            "anchor_tick": tick,
                            "kills_after_start": 0,
                        }
            attacker_side = _normalize_side(kill.get("attacker_side"))
            candidate = candidates.get((attacker, attacker_side))
            if candidate and tick >= candidate["start_tick"]:
                candidate["kills_after_start"] += 1

        winner_side = _normalize_side((round_map.get(round_num) or {}).get("winner_side"))
        for candidate in candidates.values():
            won = winner_side == candidate["side"]
            if not won and candidate["kills_after_start"] <= 0:
                continue
            vs_count = candidate["vs"]
            score = (0.72 if won else 0.56) + min(0.16, max(0, vs_count - 2) * 0.06) + min(0.08, candidate["kills_after_start"] * 0.02)
            title = f"1v{vs_count} Clutch {'Win' if won else 'Attempt'}"
            highlights.append(_build_highlight(
                htype="clutch_win" if won else "clutch_attempt",
                title=title,
                description=f"{candidate['player']} faced a 1v{vs_count} clutch {'and won' if won else 'attempt'}",
                round_number=round_num,
                start_tick=candidate["start_tick"] - 96,
                end_tick=candidate["start_tick"] + 640,
                anchor_tick=candidate["anchor_tick"],
                primary_player=candidate["player"],
                involved_players=[candidate["player"]],
                side=candidate["side"],
                score=score,
                tags=["clutch", "impact", "high-pressure"],
                metadata={"vs": vs_count, "won": won, "kills_after_start": candidate["kills_after_start"]},
            ))
    return highlights


def _detect_flash_assists(kills_by_round: dict[int, list[dict]], grenades_by_round: dict[int, list[dict]]) -> list[dict]:
    highlights = []
    for round_num, kills in kills_by_round.items():
        flashes = [g for g in grenades_by_round.get(round_num, []) if _clean_text(g.get("grenade_type")) == "flash"]
        if not flashes:
            continue
        for kill in kills:
            assister = _clean_text(kill.get("assister_name"))
            if not assister:
                continue
            kill_tick = _safe_int(kill.get("tick"), 0) or 0
            matching_flash = None
            for flash in reversed(flashes):
                if _clean_text(flash.get("thrower_name")) != assister:
                    continue
                flash_tick = _safe_int(flash.get("tick"), 0) or 0
                if flash_tick <= 0 or kill_tick <= flash_tick:
                    continue
                if kill_tick - flash_tick > FLASH_ASSIST_WINDOW_TICKS:
                    continue
                matching_flash = flash
                break
            if not matching_flash:
                continue
            attacker = _clean_text(kill.get("attacker_name"))
            victim = _clean_text(kill.get("victim_name"))
            side = _normalize_side(kill.get("attacker_side"))
            highlights.append(_build_highlight(
                htype="flash_assist",
                title="Flash Assist",
                description=f"{assister} set up {attacker}'s kill on {victim}",
                round_number=round_num,
                start_tick=(_safe_int(matching_flash.get("tick"), 0) or 0) - 64,
                end_tick=kill_tick + 128,
                anchor_tick=kill_tick,
                primary_player=assister,
                involved_players=[assister, attacker, victim],
                side=side,
                score=0.57,
                tags=["flash", "assist", "teamplay"],
                metadata={"attacker": attacker, "victim": victim, "flash_tick": _safe_int(matching_flash.get("tick"), 0) or 0},
            ))
    return highlights


def _is_utility_damage(weapon: str) -> bool:
    weapon_name = _clean_text(weapon).lower()
    tokens = ("he", "grenade", "molotov", "incendiary", "inferno", "fire")
    return any(token in weapon_name for token in tokens)


def _detect_grenade_damage_spikes(damages_by_round: dict[int, list[dict]]) -> list[dict]:
    highlights = []
    for round_num, damages in damages_by_round.items():
        relevant = [d for d in damages if _is_utility_damage(d.get("weapon"))]
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for damage in relevant:
            grouped[(_clean_text(damage.get("attacker_name")), _clean_text(damage.get("weapon")).lower())].append(damage)
        for (attacker, weapon), rows in grouped.items():
            rows.sort(key=lambda row: _safe_int(row.get("tick"), 0) or 0)
            cluster: list[dict] = []
            for row in rows:
                tick = _safe_int(row.get("tick"), 0) or 0
                if not cluster:
                    cluster = [row]
                    continue
                prev_tick = _safe_int(cluster[-1].get("tick"), 0) or 0
                if tick - prev_tick <= GRENADE_DAMAGE_WINDOW_TICKS:
                    cluster.append(row)
                else:
                    _append_grenade_cluster_highlight(highlights, round_num, attacker, weapon, cluster)
                    cluster = [row]
            _append_grenade_cluster_highlight(highlights, round_num, attacker, weapon, cluster)
    return highlights


def _append_grenade_cluster_highlight(highlights: list[dict], round_num: int, attacker: str, weapon: str, cluster: list[dict]) -> None:
    if not cluster:
        return
    total_damage = sum(_safe_int(row.get("hp_damage"), 0) or 0 for row in cluster)
    victims = sorted({_clean_text(row.get("victim_name")) for row in cluster if _clean_text(row.get("victim_name"))})
    if total_damage < 45 and len(victims) < 2:
        return
    first_tick = _safe_int(cluster[0].get("tick"), 0) or 0
    last_tick = _safe_int(cluster[-1].get("tick"), first_tick) or first_tick
    score = 0.48 + min(0.32, total_damage / 160.0) + min(0.12, max(0, len(victims) - 1) * 0.06)
    highlights.append(_build_highlight(
        htype="grenade_damage_spike",
        title="Utility Damage Spike",
        description=f"{attacker} dealt {total_damage} utility damage with {weapon}",
        round_number=round_num,
        start_tick=first_tick - 64,
        end_tick=last_tick + 160,
        anchor_tick=last_tick,
        primary_player=attacker,
        involved_players=[attacker, *victims],
        side="",
        score=score,
        tags=["utility", "damage", "impact"],
        metadata={"weapon": weapon, "total_damage": total_damage, "victims": victims, "event_count": len(cluster)},
    ))


def _detect_bomb_pressure_moments(bomb_by_round: dict[int, list[dict]], kills_by_round: dict[int, list[dict]]) -> list[dict]:
    highlights = []
    for round_num, events in bomb_by_round.items():
        plant = next((event for event in events if _clean_text(event.get("event")) in {"plant", "plant_start"}), None)
        if not plant:
            continue
        plant_tick = _safe_int(plant.get("tick"), 0) or 0
        nearby_kills = [kill for kill in kills_by_round.get(round_num, []) if abs((_safe_int(kill.get("tick"), 0) or 0) - plant_tick) <= 192]
        if len(nearby_kills) < 2:
            continue
        player = _clean_text(plant.get("player_name"))
        involved = [player]
        involved.extend(_clean_text(k.get("attacker_name")) for k in nearby_kills)
        involved.extend(_clean_text(k.get("victim_name")) for k in nearby_kills)
        highlights.append(_build_highlight(
            htype="bomb_pressure",
            title="Bombsite Pressure",
            description=f"Heavy action around the plant in round {round_num}",
            round_number=round_num,
            start_tick=plant_tick - 128,
            end_tick=plant_tick + 256,
            anchor_tick=plant_tick,
            primary_player=player,
            involved_players=involved,
            side="T",
            score=0.53,
            tags=["bomb", "site-hit", "pressure"],
            metadata={"kill_count_near_plant": len(nearby_kills), "bomb_event": _clean_text(plant.get("event"))},
        ))
    return highlights


def _deduplicate_highlights(highlights: list[dict]) -> list[dict]:
    kept: list[dict] = []
    highlights = sorted(highlights, key=lambda item: (item.get("score", 0), item.get("anchor_tick", 0)), reverse=True)
    for highlight in highlights:
        duplicate = False
        for existing in kept:
            if existing["type"] != highlight["type"]:
                continue
            if existing["round_number"] != highlight["round_number"]:
                continue
            if existing["primary_player"] != highlight["primary_player"]:
                continue
            if abs(existing["anchor_tick"] - highlight["anchor_tick"]) <= 128:
                duplicate = True
                break
        if not duplicate:
            kept.append(highlight)
    kept.sort(key=lambda item: (item["round_number"], item["anchor_tick"]))
    return kept


def _finalize_highlights(highlights: list[dict]) -> list[dict]:
    type_counts: Counter[str] = Counter()
    finalized = []
    for highlight in highlights:
        htype = highlight["type"]
        type_counts[htype] += 1
        primary = _clean_text(highlight.get("primary_player")).lower().replace(" ", "_") or "moment"
        highlight["highlight_id"] = f"r{highlight['round_number']}_{htype}_{primary}_{type_counts[htype]:03d}"
        finalized.append(highlight)
    return finalized


def _build_highlight_summary(highlights: list[dict]) -> dict:
    counts_by_type = Counter()
    counts_by_round = Counter()
    warnings = []
    for highlight in highlights:
        counts_by_type[highlight["type"]] += 1
        counts_by_round[str(highlight["round_number"])] += 1
        if highlight["start_tick"] > highlight["end_tick"]:
            warnings.append(f"invalid_window:{highlight['highlight_id'] or highlight['type']}")
    return {
        "counts_by_type": dict(sorted(counts_by_type.items())),
        "counts_by_round": dict(sorted(counts_by_round.items(), key=lambda item: int(item[0]))),
        "warnings": warnings,
    }
