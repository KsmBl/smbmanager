"""Parsing and serialising the share definitions managed by SMB Manager.

Shares live in their own file (``/etc/samba/smbmanager.conf``) which is pulled
into the main ``smb.conf`` with an ``include`` directive.  That way the user's
hand written ``smb.conf`` is never rewritten by this application.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

SHARE_FILE = "/etc/samba/smbmanager.conf"

HEADER = """\
# /etc/samba/smbmanager.conf
#
# Managed by SMB Manager.  This file is included from /etc/samba/smb.conf.
# Hand edits to known keys are overwritten when a share is saved from the GUI;
# unknown keys inside a share are preserved.
"""

_TRUE = {"yes", "true", "1", "on"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
_RESERVED = {"global", "homes", "printers", "print$"}

# Keys this application owns; anything else found in a section is kept as-is.
_MANAGED_KEYS = {
    "path",
    "comment",
    "valid users",
    "read only",
    "writable",
    "writeable",
    "guest ok",
    "public",
    "browseable",
    "browsable",
    "create mask",
    "directory mask",
    "force user",
}


def _to_bool(value: str, default: bool = False) -> bool:
    return value.strip().lower() in _TRUE if value.strip() else default


def _yn(value: bool) -> str:
    return "yes" if value else "no"


@dataclass
class Share:
    name: str
    path: str = ""
    comment: str = ""
    valid_users: list = field(default_factory=list)
    writable: bool = True
    guest_ok: bool = False
    browseable: bool = True
    create_mask: str = "0664"
    directory_mask: str = "2775"
    force_user: str = ""
    extra: dict = field(default_factory=dict)

    # -- validation -------------------------------------------------------
    def validate(self) -> str | None:
        """Return a human readable problem, or None when the share is sane."""
        name = self.name.strip()
        if not name:
            return "The share needs a name."
        if name.lower() in _RESERVED:
            return f"'{name}' is a reserved Samba section name."
        if not _NAME_RE.match(name):
            return (
                "Share names may only contain letters, digits, spaces and "
                "the characters . _ -"
            )
        if not self.path:
            return "Pick a folder to share."
        if not os.path.isabs(self.path):
            return "The shared folder must be an absolute path."
        if not self.guest_ok and not self.valid_users:
            return "Add at least one user, or enable guest access."
        return None

    # -- serialisation ----------------------------------------------------
    def to_ini(self) -> str:
        lines = [f"[{self.name}]"]
        if self.comment:
            lines.append(f"   comment = {self.comment}")
        lines.append(f"   path = {self.path}")
        lines.append(f"   read only = {_yn(not self.writable)}")
        lines.append(f"   browseable = {_yn(self.browseable)}")
        lines.append(f"   guest ok = {_yn(self.guest_ok)}")
        if self.valid_users:
            lines.append("   valid users = " + " ".join(self.valid_users))
        if self.force_user:
            lines.append(f"   force user = {self.force_user}")
        lines.append(f"   create mask = {self.create_mask}")
        lines.append(f"   directory mask = {self.directory_mask}")
        for key, value in self.extra.items():
            lines.append(f"   {key} = {value}")
        return "\n".join(lines) + "\n"

    @classmethod
    def from_section(cls, name: str, values: dict) -> "Share":
        get = lambda k, d="": values.get(k, d)  # noqa: E731
        writable = True
        if "read only" in values:
            writable = not _to_bool(values["read only"], False)
        elif "writable" in values or "writeable" in values:
            writable = _to_bool(get("writable") or get("writeable"), True)
        browseable = _to_bool(
            get("browseable") or get("browsable") or "yes", True
        )
        guest = _to_bool(get("guest ok") or get("public") or "no", False)
        users = [u for u in re.split(r"[\s,]+", get("valid users")) if u]
        extra = {k: v for k, v in values.items() if k not in _MANAGED_KEYS}
        return cls(
            name=name,
            path=get("path"),
            comment=get("comment"),
            valid_users=users,
            writable=writable,
            guest_ok=guest,
            browseable=browseable,
            create_mask=get("create mask", "0664"),
            directory_mask=get("directory mask", "2775"),
            force_user=get("force user"),
            extra=extra,
        )


def parse(text: str) -> list:
    """Parse an smb.conf style fragment into a list of :class:`Share`."""
    shares = []
    current_name = None
    current: dict = {}

    def flush():
        if current_name is not None:
            shares.append(Share.from_section(current_name, dict(current)))

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            current_name = line[1:-1].strip()
            current = {}
            continue
        if "=" in line and current_name is not None:
            key, _, value = line.partition("=")
            current[key.strip().lower()] = value.strip()
    flush()
    return [s for s in shares if s.name.lower() not in _RESERVED]


def load(path: str = SHARE_FILE) -> list:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse(fh.read())
    except FileNotFoundError:
        return []
    except OSError:
        return []


def dump(shares) -> str:
    parts = [HEADER]
    for share in shares:
        parts.append(share.to_ini())
    return "\n".join(parts)
