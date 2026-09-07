"""Profile registry: persists which player accounts have their own archive
(db file), so profile tabs survive across app restarts. No settings
persistence existed before this feature -- gui/data/profiles.json is new.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILES_PATH = PROJECT_ROOT / "gui" / "data" / "profiles.json"
GAME_H_PATH = PROJECT_ROOT / "game.h"

DEFAULT_PROFILE_ID = "profile_1"
DEFAULT_DB_PATH = "tempo_archive.db"


@dataclass
class ProfileRecord:
    id: str
    display_name: str
    platform: str  # "chesscom" | "lichess"
    username: str
    db_path: str  # relative to PROJECT_ROOT

    def resolved_db_path(self) -> Path:
        return PROJECT_ROOT / self.db_path


@dataclass
class ProfilesConfig:
    profiles: list[ProfileRecord] = field(default_factory=list)
    last_active: str | None = None


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug or "profile"


def _default_username() -> str:
    """Recovers game.h's compile-time YOUR_USERNAME so the bootstrap profile
    keeps tagging your_color correctly on future re-fetches via --user."""
    try:
        text = GAME_H_PATH.read_text(encoding="utf-8")
        m = re.search(r'YOUR_USERNAME\s*=\s*"([^"]*)"', text)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "Player"


def save_profiles(config: ProfilesConfig) -> None:
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "last_active": config.last_active,
        "profiles": [asdict(p) for p in config.profiles],
    }
    PROFILES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def bootstrap_if_missing() -> ProfilesConfig:
    """Loads the profile registry, creating it on first run after this
    feature shipped: the existing tempo_archive.db becomes Profile 1,
    unchanged path, no migration, no re-fetch."""
    if PROFILES_PATH.exists():
        data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        profiles = [ProfileRecord(**p) for p in data.get("profiles", [])]
        return ProfilesConfig(profiles=profiles, last_active=data.get("last_active"))

    username = _default_username()
    record = ProfileRecord(
        id=DEFAULT_PROFILE_ID,
        display_name=username,
        platform="chesscom",
        username=username,
        db_path=DEFAULT_DB_PATH,
    )
    config = ProfilesConfig(profiles=[record], last_active=record.id)
    save_profiles(config)
    return config


def add_profile(config: ProfilesConfig, display_name: str, platform: str, username: str) -> ProfileRecord:
    """Adds a new profile to `config` (in memory -- caller still needs to
    save_profiles) and returns the record, with a unique id/db path derived
    from the username."""
    existing_ids = {p.id for p in config.profiles}
    slug = _slugify(username)
    profile_id = slug
    n = 2
    while profile_id in existing_ids:
        profile_id = f"{slug}-{n}"
        n += 1

    record = ProfileRecord(
        id=profile_id,
        display_name=display_name.strip() or username,
        platform=platform,
        username=username,
        db_path=f"profiles/{profile_id}/tempo_archive.db",
    )
    config.profiles.append(record)
    return record


def remove_profile(config: ProfilesConfig, profile_id: str) -> None:
    config.profiles = [p for p in config.profiles if p.id != profile_id]
    if config.last_active == profile_id:
        config.last_active = config.profiles[0].id if config.profiles else None
