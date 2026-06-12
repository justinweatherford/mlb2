"""
discord_listener/listener.py — Discord gateway client.

Thin wrapper around pipeline.dispatch_message.
All pipeline logic lives in pipeline.py so it can be tested without Discord.
"""
import logging
import sqlite3
from datetime import datetime

import discord

from config import Config
from discord_listener.pipeline import dispatch_message
from game_state.memory import GameStateMemory
from trading.fee_calculator import FeeConfig

log = logging.getLogger(__name__)


class KalshiMLBClient(discord.Client):

    def __init__(self, cfg: Config, conn: sqlite3.Connection, memory: GameStateMemory):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.cfg = cfg
        self.conn = conn
        self.memory = memory
        self.fee_cfg = FeeConfig(
            taker_fee_rate=cfg.taker_fee_rate,
            maker_fee_rate=cfg.maker_fee_rate,
            fee_multiplier=cfg.fee_multiplier,
        )

    async def on_ready(self):
        log.info("[CONNECTED] Logged in as %s (id=%s)", self.user, self.user.id)

        channel = self.get_channel(self.cfg.discord_channel_id)
        if channel:
            log.info("[CHANNEL]   Watching #%s (id=%s)", channel.name, channel.id)
        else:
            log.warning(
                "[CHANNEL]   id=%s not found in guild cache — "
                "check DISCORD_CHANNEL_ID and that the bot has access to the channel",
                self.cfg.discord_channel_id,
            )

        log.info("[DB]        %s", self.cfg.db_path)
        log.info("[MEMORY]    GameStateMemory initialized")
        log.info("[MODE]      paper_mode=%s  dry_run=%s  paper_units=%s",
                 self.cfg.paper_mode, self.cfg.dry_run, self.cfg.paper_units)
        log.info("[READY]     Listening for messages")

    async def on_message(self, message: discord.Message):
        # Channel filter — ignore everything outside the configured channel
        if message.channel.id != self.cfg.discord_channel_id:
            return

        # Ignore the bot's own messages
        if message.author.id == self.user.id:
            return

        raw = message.content
        if not raw.strip():
            return

        dispatch_message(
            raw=raw,
            message_id=str(message.id),
            channel_id=str(message.channel.id),
            received_at=datetime.now(),
            conn=self.conn,
            memory=self.memory,
            fee_cfg=self.fee_cfg,
            cfg=self.cfg,
        )

    async def on_error(self, event_method: str, *args, **kwargs):
        log.exception("Unhandled error in event '%s'", event_method)
