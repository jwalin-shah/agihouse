"""
Person resolver: collapse email addresses, phone numbers, and iMessage handles
onto a single canonical person id, using macOS Contacts.app as the linker.

Rules:
  1. Anything that appears together in one Contacts record → same person.
  2. Bare email/phone with no Contacts hit → its own person (id = the handle).
  3. Names are normalized lowercase, phones to E.164-ish (+digits only).

Output: a Resolver with .canon(handle) -> str (canonical id) and a debug map.
"""
from __future__ import annotations
import sqlite3
import glob
import re
import os
from dataclasses import dataclass, field

CONTACTS_GLOB = os.path.expanduser(
    "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
)


def norm_phone(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    if not digits: return ""
    # default to US if 10 digits, leading 1 if 11
    if len(digits) == 10: digits = "1" + digits
    return "+" + digits


def norm_email(s: str) -> str:
    return (s or "").strip().lower()


def norm_handle(s: str) -> str:
    """A handle is whatever appears in inbox.sender / chat.db handle."""
    if not s: return ""
    s = s.strip()
    if "@" in s: return norm_email(s)
    if any(c.isdigit() for c in s) and len(re.sub(r"\D", "", s)) >= 7:
        return norm_phone(s)
    return s.lower()


@dataclass
class Resolver:
    # handle (normalized) -> canonical person id
    _h2c: dict[str, str] = field(default_factory=dict)
    # canonical id -> display name
    _names: dict[str, str] = field(default_factory=dict)

    def canon(self, raw_handle: str) -> str:
        h = norm_handle(raw_handle)
        if not h: return ""
        return self._h2c.get(h, h)

    def name(self, canonical: str) -> str:
        return self._names.get(canonical, canonical)

    def all_handles(self) -> dict[str, str]:
        return dict(self._h2c)


SELF_HANDLES = {
    "jshah1331@gmail.com", "jwalinshah13@gmail.com", "jwalinsshah@gmail.com",
}
SELF_ID = "me@self"


def build_resolver() -> Resolver:
    r = Resolver()
    # Pin self handles first so Contacts records that include "me" can't steal them.
    for h in SELF_HANDLES:
        r._h2c[h] = SELF_ID
    r._names[SELF_ID] = "me"
    db_paths = glob.glob(CONTACTS_GLOB)
    if not db_paths:
        print(f"[resolver] no Contacts dbs found at {CONTACTS_GLOB}")
        return r

    for db in db_paths:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            cur = con.cursor()
        except sqlite3.OperationalError as e:
            print(f"[resolver] skip {db}: {e}")
            continue

        # Pull all records w/ their emails and phones in one go.
        # ZABCDRECORD.Z_PK is the person; ZOWNER on email/phone tables points to it.
        cur.execute("""
            SELECT r.Z_PK, r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION
            FROM ZABCDRECORD r
        """)
        records = {pk: (fn or "", ln or "", org or "") for pk, fn, ln, org in cur.fetchall()}

        cur.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZADDRESS IS NOT NULL")
        emails_by_pk: dict[int, list[str]] = {}
        for pk, addr in cur.fetchall():
            emails_by_pk.setdefault(pk, []).append(norm_email(addr))

        cur.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZFULLNUMBER IS NOT NULL")
        phones_by_pk: dict[int, list[str]] = {}
        for pk, num in cur.fetchall():
            phones_by_pk.setdefault(pk, []).append(norm_phone(num))

        con.close()

        # For each Contacts record, all its handles → same canonical id.
        # Canonical id = first email if any, else first phone, else "person:<pk>".
        for pk, (fn, ln, org) in records.items():
            emails = [e for e in emails_by_pk.get(pk, []) if e]
            phones = [p for p in phones_by_pk.get(pk, []) if p]
            handles = emails + phones
            if not handles: continue
            canon = emails[0] if emails else phones[0]
            display = (f"{fn} {ln}".strip() or org or canon).strip()
            # If THIS Contacts record contains any of my self handles, skip it —
            # it's a contact-of-self entry that would otherwise pull others onto me.
            if any(h in SELF_HANDLES for h in handles):
                continue
            for h in handles:
                if h in SELF_HANDLES: continue
                r._h2c.setdefault(h, canon)
            r._names.setdefault(canon, display)

    return r


if __name__ == "__main__":
    r = build_resolver()
    print(f"[resolver] mapped {len(r.all_handles())} handles → {len(r._names)} canonical persons")
    # Show 5 examples
    for h, c in list(r.all_handles().items())[:5]:
        print(f"  {h:30s} -> {c:30s}  ({r.name(c)})")
