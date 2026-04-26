"""
Contact resolution — reads macOS AddressBook SQLite DBs directly.
Lifted and simplified from archive/jarvisv0/backend.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# ── Phone normalization (from jarvisv0) ───────────────────────────────────────


def _digits_only(phone: str) -> str | None:
    d = re.sub(r"\D", "", phone)
    return d if d else None


def _phone_variants(phone: str) -> list[str]:
    """Return all normalized forms to maximise match rate across DBs."""
    digits = _digits_only(phone)
    if not digits:
        return []
    variants = [digits, f"+{digits}"]
    if len(digits) == 11 and digits.startswith("1"):
        short = digits[1:]
        variants += [short, f"+{short}"]
    elif len(digits) == 10:
        long = f"1{digits}"
        variants += [long, f"+{long}"]
    return list(dict.fromkeys(variants))  # deduplicate, preserve order


# ── AddressBook paths ─────────────────────────────────────────────────────────


def _addressbook_paths() -> list[Path]:
    base = Path.home() / "Library/Application Support/AddressBook"
    paths: list[Path] = []
    for version in ("v22", "v21", "v20"):
        p = base / f"AddressBook-{version}.abcddb"
        if p.exists():
            paths.append(p)
            break
    sources = base / "Sources"
    if sources.exists():
        try:
            for src in sources.iterdir():
                if src.is_dir():
                    for version in ("v22", "v21", "v20"):
                        p = src / f"AddressBook-{version}.abcddb"
                        if p.exists():
                            paths.append(p)
                            break
        except PermissionError:
            # macOS may require Full Disk Access for AddressBook folder
            pass
    return paths


# ── Core loader ───────────────────────────────────────────────────────────────


def load_contact_map() -> dict[str, str]:
    """
    Build a lookup dict mapping every normalized phone/email variant → display name.
    Reads all AddressBook source databases in one pass.
    """
    result: dict[str, str] = {}

    for db_path in _addressbook_paths():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()

            # Check table exists
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ZABCDRECORD'")
            if not cur.fetchone():
                conn.close()
                continue

            # Fetch all contacts with their primary phone + email in one pass
            cur.execute("""
                SELECT
                    r.Z_PK,
                    r.ZFIRSTNAME,
                    r.ZLASTNAME,
                    r.ZMIDDLENAME,
                    r.ZORGANIZATION,
                    (
                        SELECT ZFULLNUMBER FROM ZABCDPHONENUMBER
                        WHERE ZOWNER = r.Z_PK AND ZFULLNUMBER IS NOT NULL
                        ORDER BY ZISPRIMARY DESC, ZORDERINGINDEX ASC LIMIT 1
                    ) as phone,
                    (
                        SELECT ZADDRESS FROM ZABCDEMAILADDRESS
                        WHERE ZOWNER = r.Z_PK AND ZADDRESS IS NOT NULL
                        ORDER BY ZISPRIMARY DESC, ZORDERINGINDEX ASC LIMIT 1
                    ) as email
                FROM ZABCDRECORD r
                WHERE r.ZFIRSTNAME IS NOT NULL
                   OR r.ZLASTNAME IS NOT NULL
                   OR r.ZORGANIZATION IS NOT NULL
            """)

            for _pk, first, last, _middle, org, phone, email in cur.fetchall():
                # Build display name
                parts = [p for p in (first, last) if p]
                if not parts and org:
                    parts = [org]
                name = " ".join(parts) if parts else None
                if not name:
                    continue

                # Map all phone variants
                if phone:
                    for variant in _phone_variants(phone):
                        result.setdefault(variant.lower(), name)

                # Map email (case-insensitive)
                if email:
                    result.setdefault(email.lower(), name)

            conn.close()
        except Exception:
            continue

    return result


# ── Public resolver ───────────────────────────────────────────────────────────


class ContactBook:
    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def load(self) -> int:
        self._map = load_contact_map()
        return len(self._map)

    def resolve(self, identifier: str) -> str:
        """Return display name for a phone number or email, or the raw identifier."""
        if not identifier:
            return identifier
        key = identifier.lower().strip()
        # Direct hit
        if key in self._map:
            return self._map[key]
        # Try phone variants
        for variant in _phone_variants(identifier):
            if variant.lower() in self._map:
                return self._map[variant.lower()]
        return identifier
