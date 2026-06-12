import os
import sys
from dataclasses import dataclass
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed; variables must be set in the real environment


@dataclass
class KalshiConfig:
    api_key_id: str
    api_private_key: str
    env: str
    read_only: bool


@dataclass
class Config:
    discord_token: str
    discord_channel_id: int
    db_path: str
    paper_mode: str
    maker_fee_rate: float
    taker_fee_rate: float
    fee_multiplier: float
    min_price_cents: int
    max_price_cents: int
    max_chase_price_cents: int
    log_level: str
    dry_run: bool = False
    paper_units: int = 10


def load_config() -> Config:
    return Config(
        discord_token=os.environ.get("DISCORD_TOKEN", ""),
        discord_channel_id=int(os.environ.get("DISCORD_CHANNEL_ID", "0")),
        db_path=os.environ.get("DB_PATH", "kalshi_mlb.db"),
        paper_mode=os.environ.get("PAPER_MODE", "realistic"),
        maker_fee_rate=float(os.environ.get("MAKER_FEE_RATE", "0.035")),
        taker_fee_rate=float(os.environ.get("TAKER_FEE_RATE", "0.07")),
        fee_multiplier=float(os.environ.get("FEE_MULTIPLIER", "1.0")),
        min_price_cents=int(os.environ.get("MIN_PRICE_CENTS", "3")),
        max_price_cents=int(os.environ.get("MAX_PRICE_CENTS", "97")),
        max_chase_price_cents=int(os.environ.get("MAX_CHASE_PRICE_CENTS", "85")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        dry_run=os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes"),
        paper_units=int(os.environ.get("PAPER_UNITS", "10")),
    )


def load_kalshi_config() -> KalshiConfig:
    return KalshiConfig(
        api_key_id=os.environ.get("KALSHI_API_KEY_ID", ""),
        api_private_key=os.environ.get("KALSHI_API_PRIVATE_KEY", ""),
        env=os.environ.get("KALSHI_ENV", "prod"),
        read_only=os.environ.get("KALSHI_READ_ONLY", "true").lower() in ("1", "true", "yes"),
    )


def validate_for_discord(cfg: Config) -> None:
    """
    Raise ValueError with a clear message if required Discord config is missing.
    Call this in main.py before starting the bot.
    """
    errors = []
    if not cfg.discord_token or cfg.discord_token == "your_bot_token_here":
        errors.append("DISCORD_TOKEN is not set (edit .env and add your bot token)")
    if cfg.discord_channel_id == 0:
        errors.append("DISCORD_CHANNEL_ID is not set (edit .env and add the channel id)")
    if errors:
        print("ERROR: Missing required configuration:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        raise ValueError("\n".join(errors))
