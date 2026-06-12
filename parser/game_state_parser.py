import re
from datetime import datetime
from typing import Optional

from models import ParsedGameState
from parser.common import parse_header, extract_kv


def _parse_kalshi_yes(s: str) -> Optional[dict]:
    """'HOU 0c LAA 99c' → {'HOU': 0, 'LAA': 99}"""
    matches = re.findall(r'(\w+)\s+(\d+)c', s)
    return {team: int(price) for team, price in matches} if matches else None


def _parse_runners(s: str) -> list:
    """'1B • 3B' → ['1B', '3B']"""
    return [r.strip() for r in re.split(r'[\s•·,]+', s)
            if re.match(r'^[123]B$', r.strip())]


def _parse_kalshi_lead(s: str) -> Optional[float]:
    m = re.search(r'([+-]?\d+\.?\d*)\s*s', s)
    return float(m.group(1)) if m else None


def _parse_pitch(s: str) -> tuple:
    """Returns (pitch_type, velocity_mph, zone_int)."""
    parts = [p.strip() for p in re.split(r'[·•]', s)]
    pitch_type = parts[0] if parts else None
    velocity = zone = None
    for p in parts:
        vm = re.search(r'(\d+\.?\d*)mph', p)
        if vm:
            velocity = float(vm.group(1))
        zm = re.search(r'zone\s+(\d+)', p, re.IGNORECASE)
        if zm:
            zone = int(zm.group(1))
    return pitch_type, velocity, zone


def _parse_hit(s: str) -> tuple:
    """Returns (exit_velocity, launch_angle, distance_ft, hit_type_str)."""
    ev = la = dist = hit_type = None
    m = re.search(r'EV\s+(\d+\.?\d*)', s)
    if m:
        ev = float(m.group(1))
    m = re.search(r'LA\s+([+-]?\d+\.?\d*)', s)
    if m:
        la = float(m.group(1))
    m = re.search(r'dist\s+(\d+\.?\d*)ft', s)
    if m:
        dist = float(m.group(1))
    # Hit type: last segment with no digits (e.g. "line drive")
    for part in reversed([p.strip() for p in re.split(r'[·•]', s)]):
        if part and not re.search(r'\d', part):
            hit_type = part
            break
    return ev, la, dist, hit_type


def parse_game_state(raw: str, received_at: datetime) -> ParsedGameState:
    header = parse_header(raw)
    body = raw[header["_header_end"]:]
    kv = extract_kv(body)

    pitch_type = pitch_vel = pitch_zone = None
    if "Pitch" in kv:
        pitch_type, pitch_vel, pitch_zone = _parse_pitch(kv["Pitch"])

    ev = la = dist = hit_type = None
    if "Hit" in kv:
        ev, la, dist, hit_type = _parse_hit(kv["Hit"])

    outs_str = kv.get("Outs", "")
    outs = int(outs_str) if outs_str.isdigit() else None

    return ParsedGameState(
        raw_message=raw,
        timestamp_received=received_at,
        game_id=header["game_id"],
        away_team=header["away_team"],
        home_team=header["home_team"],
        away_score=header["away_score"],
        home_score=header["home_score"],
        inning_half=header["inning_half"],
        inning_number=header["inning_number"],
        outs=outs,
        count=kv.get("Count") or None,
        runners=_parse_runners(kv["Runners"]) if "Runners" in kv else [],
        scored_player=kv.get("Scored") or None,
        play_description=kv.get("Play") or None,
        pitch_type=pitch_type,
        pitch_velocity=pitch_vel,
        pitch_zone=pitch_zone,
        exit_velocity=ev,
        launch_angle=la,
        hit_distance=dist,
        hit_type=hit_type,
        kalshi_lead_seconds=_parse_kalshi_lead(kv["Kalshi lead"]) if "Kalshi lead" in kv else None,
        kalshi_yes_prices=_parse_kalshi_yes(kv["Kalshi YES"]) if "Kalshi YES" in kv else None,
        message_type="game_state",
    )
