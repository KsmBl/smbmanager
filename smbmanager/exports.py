"""Parsing and serialising the NFS exports managed by SMB Manager.

Like the Samba side, the exports live in a file of their own -
``/etc/exports.d/smbmanager.exports``.  ``exportfs`` reads every ``.exports``
file in that directory, so the user's own ``/etc/exports`` is never rewritten
and does not even need to exist.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

EXPORT_FILE = "/etc/exports.d/smbmanager.exports"

HEADER = """\
# /etc/exports.d/smbmanager.exports
#
# Managed by SMB Manager.  exportfs reads every *.exports file in
# /etc/exports.d, so your own /etc/exports is left alone.
"""

# Options this application understands; anything else in an export is kept.
_MANAGED_OPTIONS = {
    "rw", "ro", "sync", "async", "subtree_check", "no_subtree_check",
    "root_squash", "no_root_squash", "all_squash", "no_all_squash",
}

# A client spec: a host name, an address, a subnet, a netgroup or a wildcard.
_SPEC_RE = re.compile(r"^[A-Za-z0-9_.:*?/\[\]@-]+$")
_LINE_RE = re.compile(r"^(?P<path>\"[^\"]+\"|\S+)(?P<rest>.*)$")
_CLIENT_RE = re.compile(r"(?P<spec>[^\s(]+)(?:\((?P<options>[^)]*)\))?")


@dataclass
class Client:
    """One host (or subnet) an export is offered to, with its options."""

    spec: str = "*"
    writable: bool = True
    sync: bool = True
    subtree_check: bool = False
    root_squash: bool = True
    all_squash: bool = False
    extra: list = field(default_factory=list)

    def options(self) -> list:
        options = [
            "rw" if self.writable else "ro",
            "sync" if self.sync else "async",
            "subtree_check" if self.subtree_check else "no_subtree_check",
            "root_squash" if self.root_squash else "no_root_squash",
        ]
        if self.all_squash:
            options.append("all_squash")
        return options + list(self.extra)

    def to_text(self) -> str:
        return f"{self.spec}({','.join(self.options())})"

    def same_options(self, other: "Client") -> bool:
        return self.options() == other.options()

    @classmethod
    def from_text(cls, spec: str, options: str) -> "Client":
        names = [o.strip() for o in options.split(",") if o.strip()]
        lookup = {n.split("=", 1)[0] for n in names}
        return cls(
            spec=spec,
            writable="rw" in lookup or "ro" not in lookup,
            sync="async" not in lookup,
            subtree_check=("subtree_check" in lookup
                           and "no_subtree_check" not in lookup),
            root_squash="no_root_squash" not in lookup,
            all_squash="all_squash" in lookup,
            extra=[n for n in names
                   if n.split("=", 1)[0] not in _MANAGED_OPTIONS],
        )


@dataclass
class Export:
    path: str = ""
    comment: str = ""
    clients: list = field(default_factory=list)

    @property
    def name(self) -> str:
        """NFS has no share names; the folder is the identity."""
        return self.path

    def validate(self) -> str | None:
        if not self.path:
            return "Pick a folder to export."
        if not os.path.isabs(self.path):
            return "The exported folder must be an absolute path."
        if not self.clients:
            return "Add at least one client, or use * for everyone."
        for client in self.clients:
            if not _SPEC_RE.match(client.spec):
                return f"'{client.spec}' is not a host, subnet or wildcard."
        return None

    def uniform(self) -> bool:
        """True when every client is offered the same options."""
        return all(c.same_options(self.clients[0]) for c in self.clients)

    def to_text(self) -> str:
        path = f'"{self.path}"' if " " in self.path else self.path
        line = " ".join([path] + [c.to_text() for c in self.clients])
        return (f"# {self.comment}\n" if self.comment else "") + line + "\n"


def parse(text: str) -> list:
    """Parse an exports(5) file into a list of :class:`Export`."""
    exports = []
    comment = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            comment = ""
            continue
        if line.startswith("#"):
            comment = line.lstrip("#").strip()
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        path = match.group("path").strip('"')
        clients = [
            Client.from_text(m.group("spec"), m.group("options") or "")
            for m in _CLIENT_RE.finditer(match.group("rest"))
        ]
        exports.append(Export(path=path, comment=comment, clients=clients))
        comment = ""
    return exports


def load(path: str = EXPORT_FILE) -> list:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse(fh.read())
    except OSError:
        return []


def dump(exports) -> str:
    return "\n".join([HEADER] + [e.to_text() for e in exports])
