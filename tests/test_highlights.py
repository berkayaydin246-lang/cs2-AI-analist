"""Tests for highlights.py — best moments extraction."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src.highlights import (
    extract_highlights,
    _detect_opening_kills,
    _detect_multi_kills,
    _detect_clutches,
    _detect_trade_kills,
    _build_round_meta,
    _build_kills_by_round,
    _normalize_steamid64,
    _make_highlight_id,
    Highlight,
)


# ── Test fixtures ─────────────────────────────────────────────────────────────

def _make_kill(round_num, tick, attacker, victim, attacker_side="T", victim_side="CT",
               weapon="ak47", headshot=False, attacker_steamid="", victim_steamid=""):
    return {
        "round_num": round_num,
        "tick": tick,
        "attacker_name": attacker,
        "victim_name": victim,
        "attacker_side": attacker_side,
        "victim_side": victim_side,
        "weapon": weapon,
        "headshot": headshot,
        "attacker_steamid": attacker_steamid or f"7656119800000{hash(attacker) % 10000:04d}",
        "victim_steamid": victim_steamid or f"7656119800000{hash(victim) % 10000:04d}",
    }


def _make_parsed(kills, rounds=None, total_rounds=None):
    if rounds is None:
        round_nums = sorted(set(k["round_num"] for k in kills))
        rounds = []
        for rn in round_nums:
            round_kills = [k for k in kills if k["round_num"] == rn]
            first_tick = min(k["tick"] for k in round_kills)
            last_tick = max(k["tick"] for k in round_kills)
            rounds.append({
                "round_num": rn,
                "start": first_tick - 500,
                "freeze_end": first_tick - 200,
                "end": last_tick + 300,
            })
    return {
        "kills": kills,
        "rounds": rounds,
        "total_rounds": total_rounds or len(rounds),
        "player_positions": [],
        "player_identities": {"by_steamid": {}, "by_name": {}},
    }


# ── Unit tests ────────────────────────────────────────────────────────────────

class TestNormalizeSteamId64:
    def test_valid_string(self):
        assert _normalize_steamid64("76561198356633543") == "76561198356633543"

    def test_valid_int(self):
        assert _normalize_steamid64(76561198356633543) == "76561198356633543"

    def test_none(self):
        assert _normalize_steamid64(None) == ""

    def test_empty(self):
        assert _normalize_steamid64("") == ""

    def test_short(self):
        assert _normalize_steamid64("12345") == ""

    def test_float_nan(self):
        assert _normalize_steamid64(float("nan")) == ""


class TestMakeHighlightId:
    def test_deterministic(self):
        id1 = _make_highlight_id("opening_kill", 5, "player1", 1000)
        id2 = _make_highlight_id("opening_kill", 5, "player1", 1000)
        assert id1 == id2

    def test_different_input(self):
        id1 = _make_highlight_id("opening_kill", 5, "player1", 1000)
        id2 = _make_highlight_id("opening_kill", 5, "player2", 1000)
        assert id1 != id2

    def test_format(self):
        hid = _make_highlight_id("opening_kill", 3, "test", 500)
        assert hid.startswith("hl_opening_kill_r3_")


class TestDetectOpeningKills:
    def test_first_kill_per_round(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(1, 1200, "alice", "carol"),
            _make_kill(2, 5000, "dave", "eve"),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_opening_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 2
        assert highlights[0].player_name == "alice"
        assert highlights[0].round_number == 1
        assert highlights[0].event_type == "opening_kill"
        assert highlights[1].player_name == "dave"

    def test_headshot_bonus(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob", headshot=True),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_opening_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 1
        # Score should be higher than base opening kill score
        assert highlights[0].score > 15.0


class TestDetectMultiKills:
    def test_2k(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(1, 1100, "alice", "carol"),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_multi_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 1
        assert highlights[0].event_type == "multi_kill_2k"

    def test_ace(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(1, 1100, "alice", "carol"),
            _make_kill(1, 1200, "alice", "dave"),
            _make_kill(1, 1300, "alice", "eve"),
            _make_kill(1, 1400, "alice", "frank"),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_multi_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 1
        assert highlights[0].event_type == "multi_kill_ace"
        assert highlights[0].score > 50  # ace should be very high score

    def test_single_kill_not_multi(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(1, 1200, "carol", "dave"),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_multi_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 0

    def test_rapid_kill_bonus(self):
        # Two kills within 3 seconds (192 ticks)
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(1, 1100, "alice", "carol"),  # 100 ticks = 1.56s
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_multi_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 1
        # Rapid kills should get bonus
        assert highlights[0].score > 18.0


class TestDetectTradeKills:
    def test_trade_detected(self):
        kills = [
            # bob kills alice's teammate charlie
            _make_kill(1, 1000, "bob", "charlie", attacker_side="CT", victim_side="T"),
            # alice (T) kills bob (CT) within 5s — this is a trade
            _make_kill(1, 1200, "alice", "bob", attacker_side="T", victim_side="CT"),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_trade_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 1
        assert highlights[0].player_name == "alice"
        assert highlights[0].event_type == "trade_kill"

    def test_no_trade_if_too_slow(self):
        kills = [
            _make_kill(1, 1000, "bob", "charlie", attacker_side="CT", victim_side="T"),
            # 500 ticks = ~7.8s — too slow for trade
            _make_kill(1, 1500, "alice", "bob", attacker_side="T", victim_side="CT"),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_trade_kills(kills_by_round, round_meta, parsed)
        assert len(highlights) == 0


class TestDetectClutches:
    def test_1v1_clutch_win(self):
        kills = [
            # alice's teammates die (all T, enemies are CT)
            _make_kill(1, 1000, "bob_ct", "charlie_t", attacker_side="CT", victim_side="T"),
            _make_kill(1, 1100, "bob_ct", "dave_t", attacker_side="CT", victim_side="T"),
            _make_kill(1, 1200, "bob_ct", "eve_t", attacker_side="CT", victim_side="T"),
            _make_kill(1, 1300, "bob_ct", "frank_t", attacker_side="CT", victim_side="T"),
            # alice (T) is now alone (1v1), kills bob_ct
            _make_kill(1, 1500, "alice_t", "bob_ct", attacker_side="T", victim_side="CT"),
        ]
        parsed = _make_parsed(kills)
        round_meta = _build_round_meta(parsed)
        kills_by_round = _build_kills_by_round(parsed)
        highlights = _detect_clutches(kills_by_round, round_meta, parsed, 1)

        # Should detect alice_t's clutch
        clutch_wins = [h for h in highlights if h.event_type == "clutch_win"]
        assert len(clutch_wins) >= 1
        alice_clutch = [h for h in clutch_wins if h.player_name == "alice_t"]
        assert len(alice_clutch) == 1
        assert alice_clutch[0].clutch_won


class TestExtractHighlights:
    def test_empty_demo(self):
        parsed = _make_parsed([], rounds=[], total_rounds=0)
        highlights = extract_highlights(parsed)
        assert highlights == []

    def test_mixed_highlights_sorted_by_score(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(1, 1050, "alice", "carol"),
            _make_kill(1, 1100, "alice", "dave"),
            _make_kill(1, 1150, "alice", "eve"),
            _make_kill(1, 1200, "alice", "frank"),  # ace
            _make_kill(2, 5000, "george", "harry"),  # opening kill only
        ]
        parsed = _make_parsed(kills)
        highlights = extract_highlights(parsed)
        assert len(highlights) > 0
        # Ace should be highest priority
        scores = [h["score"] for h in highlights]
        assert scores == sorted(scores, reverse=True)

    def test_player_filter(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(2, 5000, "carol", "dave"),
        ]
        parsed = _make_parsed(kills)
        highlights = extract_highlights(parsed, player_filter="alice")
        for h in highlights:
            assert h["player_name"] == "alice"

    def test_max_highlights(self):
        kills = []
        for rn in range(1, 20):
            kills.append(_make_kill(rn, rn * 1000, "alice", "bob"))
        parsed = _make_parsed(kills)
        highlights = extract_highlights(parsed, max_highlights=5)
        assert len(highlights) <= 5

    def test_priority_assigned(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob"),
            _make_kill(2, 5000, "carol", "dave"),
        ]
        parsed = _make_parsed(kills)
        highlights = extract_highlights(parsed)
        priorities = [h["priority"] for h in highlights]
        assert priorities == sorted(priorities)
        assert priorities[0] == 1

    def test_steamid64_propagated(self):
        kills = [
            _make_kill(1, 1000, "alice", "bob",
                       attacker_steamid="76561198356633543",
                       victim_steamid="76561198260091355"),
        ]
        parsed = _make_parsed(kills)
        parsed["player_identities"] = {
            "by_steamid": {"76561198356633543": {"player_name": "alice", "appearances": 100}},
            "by_name": {"alice": "76561198356633543"},
        }
        highlights = extract_highlights(parsed, player_filter="alice")
        for h in highlights:
            assert h["player_steamid64"] == "76561198356633543"

    def test_highlight_contains_required_fields(self):
        kills = [_make_kill(1, 1000, "alice", "bob")]
        parsed = _make_parsed(kills)
        highlights = extract_highlights(parsed)
        assert len(highlights) > 0
        h = highlights[0]
        required_fields = [
            "highlight_id", "player_name", "player_steamid64",
            "round_number", "event_type", "anchor_tick", "score",
            "priority", "confidence", "selection_reason",
            "round_start_tick", "round_end_tick",
        ]
        for field in required_fields:
            assert field in h, f"Missing field: {field}"
