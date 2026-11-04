from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str | None
    enable_demo: bool
    execution_mode: str


def get_settings() -> Settings:
    mode = os.getenv("COREI_EXECUTION_MODE", "paper").strip().lower()
    if mode != "paper":
        raise RuntimeError("COREI-OS-MINI currently supports paper execution only")

    enabled = os.getenv("COREI_ENABLE_DEMO", "false").strip().lower()
    if enabled not in {"true", "false"}:
        raise RuntimeError("COREI_ENABLE_DEMO must be 'true' or 'false'")

    return Settings(
        database_url=os.getenv("COREI_DATABASE_URL"),
        enable_demo=enabled == "true",
        execution_mode="PAPER",
    )
