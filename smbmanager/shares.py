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
#
# The [global] block below is deliberate: `include` is expanded in place, and
# naming the section explicitly keeps the server wide settings global even when
# the include line happens to sit inside a share in smb.conf.
"""

# Protocol dialects a user may choose, oldest first.  Samba knows more names
# than this; these are the ones worth offering.
PROTOCOLS = (
    ("NT1", "SMB1 — obsolete and insecure, only for very old devices"),
    ("SMB2_02", "SMB 2.0 — Windows Vista"),
    ("SMB2_10", "SMB 2.1 — Windows 7"),
    ("SMB3_00", "SMB 3.0 — Windows 8"),
    ("SMB3_02", "SMB 3.0.2 — Windows 8.1"),
    ("SMB3_11", "SMB 3.1.1 — Windows 10 and later"),
)
PROTOCOL_NAMES = tuple(name for name, _ in PROTOCOLS)
DEFAULT_MIN_PROTOCOL = "SMB2_10"

_TRUE = {"yes", "true", "1", "on"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
_RESERVED = {"global", "homes", "printers", "print$"}
# Host names, IPv4/IPv6 literals, subnets and the EXCEPT keyword Samba allows.
_HOST_RE = re.compile(r"^(EXCEPT|[A-Za-z0-9_.:@/-]+)$")

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
    "max connections",
    "available",
    "hosts allow",
    "allow hosts",
    "hosts deny",
    "deny hosts",
    "write list",
}

# Keys owned by this application inside the [global] section.
_MANAGED_GLOBAL_KEYS = {"server min protocol", "server max protocol"}


def _to_bool(value: str, default: bool = False) -> bool:
    return value.strip().lower() in _TRUE if value.strip() else default


def _yn(value: bool) -> str:
    return "yes" if value else "no"


def _words(value: str) -> list:
    """Samba accepts both spaces and commas as separators in list values."""
    return [word for word in re.split(r"[\s,]+", value or "") if word]


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
    # -- advanced, all optional -------------------------------------------
    max_connections: int = 0        # 0 means "as many as Samba allows"
    available: bool = True          # a share can be parked without deleting it
    hosts_allow: list = field(default_factory=list)
    hosts_deny: list = field(default_factory=list)
    write_list: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    # -- validation -------------------------------------------------------
    def name_problem(self) -> str | None:
        """Everything wrong with the share's name, on its own.

        Kept separate so callers can tell a bad name from a bad option and
        offer the right advice.
        """
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
        return None

    def validate(self) -> str | None:
        """Return a human readable problem, or None when the share is sane."""
        problem = self.name_problem()
        if problem:
            return problem
        if not self.path:
            return "Pick a folder to share."
        if not os.path.isabs(self.path):
            return "The shared folder must be an absolute path."
        if not self.guest_ok and not self.valid_users:
            return "Add at least one user, or enable guest access."
        try:
            if int(self.max_connections) < 0:
                return "The connection limit cannot be negative."
        except (TypeError, ValueError):
            return "The connection limit has to be a whole number."
        for host in list(self.hosts_allow) + list(self.hosts_deny):
            if not _HOST_RE.match(host):
                return f"'{host}' is not a host name, address or subnet."
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
        if self.write_list:
            lines.append("   write list = " + " ".join(self.write_list))
        if int(self.max_connections or 0) > 0:
            lines.append(f"   max connections = {int(self.max_connections)}")
        if self.hosts_allow:
            lines.append("   hosts allow = " + " ".join(self.hosts_allow))
        if self.hosts_deny:
            lines.append("   hosts deny = " + " ".join(self.hosts_deny))
        if not self.available:
            lines.append("   available = no")
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
        users = _words(get("valid users"))
        extra = {k: v for k, v in values.items() if k not in _MANAGED_KEYS}
        try:
            limit = int(get("max connections", "0") or 0)
        except ValueError:
            limit = 0
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
            max_connections=max(0, limit),
            available=_to_bool(get("available") or "yes", True),
            hosts_allow=_words(get("hosts allow") or get("allow hosts")),
            hosts_deny=_words(get("hosts deny") or get("deny hosts")),
            write_list=_words(get("write list")),
            extra=extra,
        )


@dataclass
class ServerSettings:
    """The handful of server wide options SMB Manager owns.

    Protocol dialects are negotiated before a client picks a share, so Samba
    only accepts them in [global] - they cannot be set per share however much
    one would like to.
    """

    min_protocol: str = DEFAULT_MIN_PROTOCOL
    max_protocol: str = ""          # empty: leave Samba's own default alone
    extra: dict = field(default_factory=dict)

    def validate(self) -> str | None:
        for value in (self.min_protocol, self.max_protocol):
            if value and value not in PROTOCOL_NAMES:
                return f"'{value}' is not a protocol Samba knows."
        if self.min_protocol and self.max_protocol:
            order = PROTOCOL_NAMES
            if order.index(self.min_protocol) > order.index(self.max_protocol):
                return "The minimum protocol is newer than the maximum."
        return None

    def to_ini(self) -> str:
        lines = ["[global]"]
        if self.min_protocol:
            lines.append(f"   server min protocol = {self.min_protocol}")
        if self.max_protocol:
            lines.append(f"   server max protocol = {self.max_protocol}")
        for key, value in self.extra.items():
            lines.append(f"   {key} = {value}")
        return "\n".join(lines) + "\n"

    @classmethod
    def from_section(cls, values: dict) -> "ServerSettings":
        return cls(
            min_protocol=values.get("server min protocol", DEFAULT_MIN_PROTOCOL),
            max_protocol=values.get("server max protocol", ""),
            extra={
                k: v for k, v in values.items()
                if k not in _MANAGED_GLOBAL_KEYS
            },
        )


def _sections(text: str):
    """Yield (name, {key: value}) for every section in an smb.conf fragment."""
    name = None
    values: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            if name is not None:
                yield name, values
            name, values = line[1:-1].strip(), {}
            continue
        if "=" in line and name is not None:
            key, _, value = line.partition("=")
            values[key.strip().lower()] = value.strip()
    if name is not None:
        yield name, values


def parse_settings(text: str) -> ServerSettings:
    """Pull the server wide options out of the fragment's [global] section."""
    for name, values in _sections(text):
        if name.lower() == "global":
            return ServerSettings.from_section(values)
    return ServerSettings()


def parse(text: str) -> list:
    """Parse an smb.conf style fragment into a list of :class:`Share`."""
    return [
        Share.from_section(name, values)
        for name, values in _sections(text)
        if name.lower() not in _RESERVED
    ]


def load(path: str = SHARE_FILE) -> list:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse(fh.read())
    except FileNotFoundError:
        return []
    except OSError:
        return []


def load_settings(path: str = SHARE_FILE) -> ServerSettings:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_settings(fh.read())
    except OSError:
        return ServerSettings()


def dump(shares, settings: "ServerSettings | None" = None) -> str:
    parts = [HEADER, (settings or ServerSettings()).to_ini()]
    for share in shares:
        parts.append(share.to_ini())
    return "\n".join(parts)
