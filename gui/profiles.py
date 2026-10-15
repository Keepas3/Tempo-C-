"""Profile registry: persists which player accounts have their own archive
(db file), so profile tabs survive across app restarts. No settings
persistence existed before this feature -- gui/data/profiles.json is new.
"""
from __future__ import annotations

import json
import re
import shutil
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
    pinned: bool = False  # a pinned profile's tab has no close button, so it can't be deleted by accident
    group: str = ""  # tabs sharing a group name sit together and show it as a prefix

    def tab_label(self) -> str:
        return f"{self.group} · {self.display_name}" if self.group else self.display_name

    def resolved_db_path(self) -> Path:
        return PROJECT_ROOT / self.db_path

    def is_db_path_safe(self) -> bool:
        """True if db_path resolves to somewhere under PROJECT_ROOT. Guards
        against a hand-edited (or otherwise externally supplied)
        profiles.json pointing a profile's "own" archive at an arbitrary
        file elsewhere on disk -- either via a `../` sequence, or via an
        absolute db_path outright (pathlib's `/` operator silently discards
        the left-hand side whenever the right side is already absolute, so
        resolved_db_path() would return exactly that absolute path with
        PROJECT_ROOT having no effect at all)."""
        try:
            self.resolved_db_path().resolve().relative_to(PROJECT_ROOT.resolve())
            return True
        except ValueError:
            return False


    def data_paths(self) -> list[Path]:
        """Every on-disk path that belongs to this profile and must go when it
        is deleted. A profile in its own profiles/<id>/ folder owns that whole
        folder. Any other profile (the original root-level tempo_archive.db)
        owns just its db file (and a leftover SQLite journal) plus the
        generated monthly-PGN folders (games/<YYYY>/, games/unknown/) beside
        it -- not the tracked games/README.md or any .pgn files the user
        dropped into games/ by hand. Only paths that exist are returned, and
        nothing outside PROJECT_ROOT ever is."""
        if not self.is_db_path_safe():
            return []
        db = self.resolved_db_path().resolve()
        folder = db.parent
        profiles_root = (PROJECT_ROOT / "profiles").resolve()
        if folder != profiles_root and folder.is_relative_to(profiles_root):
            return [folder] if folder.exists() else []

        paths = [db, db.with_name(db.name + "-journal")]
        games_dir = folder / "games"
        if games_dir.is_dir():
            paths += [c for c in games_dir.iterdir() if c.is_dir() and (c.name == "unknown" or c.name.isdigit())]
        return [p for p in paths if p.exists()]


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
        profiles = []
        for p in data.get("profiles", []):
            record = ProfileRecord(**p)
            if not record.is_db_path_safe():
                # profiles.json is a local, user-editable file, not a
                # remote-attacker-controlled input -- but it's still
                # untrusted enough (synced dotfiles, a shared project
                # folder, a future import-profile feature) that a
                # `db_path` escaping the project directory shouldn't be
                # silently followed. Skip it rather than open/create a
                # SQLite file wherever it points.
                print(f"[Warning] skipping profile '{record.display_name}': "
                      f"db_path escapes the project directory ({record.db_path!r})")
                continue
            profiles.append(record)
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


def group_ordered(profiles: list[ProfileRecord]) -> list[ProfileRecord]:
    """Same profiles, with every group's members pulled together at the
    position of the group's first member. Ungrouped profiles and the
    relative order within each group keep their original order."""
    ordered: list[ProfileRecord] = []
    placed_groups: set[str] = set()
    for p in profiles:
        if not p.group:
            ordered.append(p)
        elif p.group not in placed_groups:
            placed_groups.add(p.group)
            ordered.extend(q for q in profiles if q.group == p.group)
    return ordered


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def delete_profile_data(paths: list[Path]) -> tuple[bool, list[str]]:
    """Erases `paths` all-or-nothing as far as the filesystem allows.

    Step 1 renames every path to "<name>.deleting"; renaming fails right away
    on Windows if anything inside is still open or locked, and on any failure
    every rename already done is rolled back, so a locked file leaves the
    profile completely untouched (returns (False, [reasons])) instead of
    half-deleted. Step 2 deletes the renamed paths; any that still refuse are
    reported in the second element (returns (True, [leftover paths])) -- the
    profile's data is already out of the way by then, so those are just
    harmless "*.deleting" leftovers to remove by hand."""
    renamed: list[tuple[Path, Path]] = []
    try:
        for path in paths:
            target = path.with_name(path.name + ".deleting")
            if target.exists():  # leftover from an earlier attempt
                _remove_path(target)
            path.rename(target)
            renamed.append((path, target))
    except OSError as e:
        problems = [f"{path}: {e}"]
        for original, target in reversed(renamed):
            try:
                target.rename(original)
            except OSError as rollback_error:
                problems.append(f"could not restore {original}: {rollback_error}")
        return False, problems

    leftovers = []
    for _original, target in renamed:
        try:
            _remove_path(target)
        except OSError as e:
            leftovers.append(f"{target}: {e}")
    return True, leftovers


def remove_profile(config: ProfilesConfig, profile_id: str) -> None:
    """Removes a profile from `config` (in memory -- caller still needs to
    save_profiles). Does not touch any files on disk."""
    config.profiles = [p for p in config.profiles if p.id != profile_id]
    if config.last_active == profile_id:
        config.last_active = config.profiles[0].id if config.profiles else None


def add_profile(config: ProfilesConfig, display_name: str, platform: str, username: str) -> ProfileRecord:
    """Adds a new profile to `config` (in memory -- caller still needs to
    save_profiles) and returns the record, with a unique id/db path derived
    from the username."""
    existing_ids = {p.id for p in config.profiles}
    slug = _slugify(username)
    profile_id = slug
    n = 2
    # Also skip ids whose folder already exists on disk: a leftover folder
    # (e.g. from a failed delete) must never be silently adopted by a new profile.
    while profile_id in existing_ids or (PROJECT_ROOT / "profiles" / profile_id).exists():
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
