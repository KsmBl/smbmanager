"""Command line interface for SMB Manager.

Running ``smbmanager`` without arguments opens the GTK window; the sub
commands below do the same work from a terminal, through the very same polkit
helper the GUI uses.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from . import __version__, shares as shares_mod, system

# Sub commands that keep us out of GTK.  Everything else starts the window.
CLI_COMMANDS = ("add", "list")

_SANITISE_RE = re.compile(r"[^A-Za-z0-9 ._-]+")

FILE_NOTICE = """\
Samba exports folders, not single files, so the folder holding the file is
shared instead:  {folder}
Everything else in that folder becomes reachable too. The share is read only
unless you pass --writable."""


def fail(message: str) -> int:
    print(f"smbmanager: {message}", file=sys.stderr)
    return 1


# --------------------------------------------------------------- helpers


def default_name(path: str) -> str:
    """A share name derived from the last path component."""
    base = os.path.basename(path.rstrip("/")) or "root"
    cleaned = _SANITISE_RE.sub("-", base).strip(" ._-")
    return (cleaned or "share")[:80]


def require_samba() -> str | None:
    """Return a problem description, or None when Samba is ready to serve."""
    if not system.samba_installed():
        return (
            "Samba is not installed.\n"
            "Install it with 'sudo pacman -S samba', or open the SMB Manager "
            "window, which offers to do it for you."
        )
    healthy, detail = system.samba_healthy()
    if not healthy:
        return (
            "Samba is installed but cannot run:\n"
            f"{detail}\n\n"
            f"This is usually a partial upgrade. Fix it with: "
            f"{system.PARTIAL_UPGRADE_FIX}"
        )
    return None


def access_label(share) -> str:
    return "read-write" if share.writable else "read-only"


def audience(share) -> str:
    people = list(share.valid_users)
    if share.guest_ok:
        people.append("guest")
    return ", ".join(people) or "-"


# -------------------------------------------------------------- commands


def cmd_add(args) -> int:
    problem = require_samba()
    if problem:
        return fail(problem)

    target = os.path.realpath(os.path.expanduser(args.path))
    if not os.path.exists(target):
        return fail(f"no such file or directory: {target}")

    shared_path = target
    is_file = os.path.isfile(target)
    if is_file:
        shared_path = os.path.dirname(target) or "/"
        print(FILE_NOTICE.format(folder=shared_path))
        print()
    elif not os.path.isdir(target):
        return fail(f"not a regular file or folder: {target}")

    # Directories are shared read-write, a folder opened up for the sake of one
    # file is not.  Either default can be overridden on the command line.
    writable = args.writable if args.writable is not None else not is_file

    users = args.users or ([] if args.guest else [system.current_user()])
    share = shares_mod.Share(
        name=(args.name or default_name(shared_path)).strip(),
        path=shared_path,
        comment=args.comment,
        valid_users=users,
        writable=writable,
        guest_ok=args.guest,
        browseable=not args.no_browse,
    )

    problem = share.validate()
    if problem:
        return fail(f"{problem}\nUse --name to pick a different share name.")

    existing = shares_mod.load()
    clash = next(
        (s for s in existing if s.name.lower() == share.name.lower()), None
    )
    if clash and not args.force:
        return fail(
            f"a share named '{clash.name}' already exists ({clash.path}).\n"
            "Pass --force to replace it, or --name to add a second one."
        )
    if clash:
        existing = [s for s in existing if s is not clash]
    else:
        twin = next((s for s in existing if s.path == share.path), None)
        if twin:
            print(f"Note: '{twin.name}' already shares this folder.")

    try:
        system.run_privileged(
            ["apply"], stdin_data=shares_mod.dump(existing + [share])
        )
    except system.PrivilegedError as exc:
        return fail(str(exc))

    print(f"Added share '{share.name}' -> {share.path} ({access_label(share)})")

    if not args.no_start and not system.service_active():
        try:
            system.run_privileged(["service", "start"])
            print("Started the Samba service.")
        except system.PrivilegedError as exc:
            print(f"The share was saved, but Samba did not start: {exc}",
                  file=sys.stderr)

    address = (system.local_addresses() or [system.hostname()])[0]
    print(f"Reachable at  smb://{address}/{share.name}")

    unknown = [u for u in share.valid_users if not system.password_is_set(u)]
    if unknown:
        who = "this user" if len(unknown) == 1 else "these users"
        print()
        print(
            f"Samba keeps its own passwords, separate from the login ones. If "
            f"{who} never had one set, do it now with:"
        )
        for user in unknown:
            print(f"    sudo smbpasswd -a {user}")
    return 0


def cmd_list(args) -> int:
    shares = sorted(shares_mod.load(), key=lambda s: s.name.lower())
    if not shares:
        print("No shares are managed by SMB Manager yet.")
        return 0
    rows = [("NAME", "PATH", "ACCESS", "USERS")]
    rows += [
        (s.name, s.path, access_label(s), audience(s)) for s in shares
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(3)) + "  " + row[3])
    return 0


# ---------------------------------------------------------------- parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smbmanager",
        description="Manage SMB shares. Without a sub command the GTK window "
                    "opens instead.",
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"smbmanager {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command")

    add = subparsers.add_parser(
        "add",
        help="share a folder, or the folder holding a file",
        description="Share a folder over SMB. Passing a file shares the "
                    "folder that contains it.",
    )
    add.add_argument("path", help="folder or file to share")
    add.add_argument("-n", "--name", default="",
                     help="share name (default: the folder's own name)")
    add.add_argument("-c", "--comment", default="",
                     help="description shown to clients")
    add.add_argument("-u", "--user", dest="users", action="append",
                     metavar="USER",
                     help="user allowed in, repeatable "
                          "(default: the current user)")
    add.add_argument("-g", "--guest", action="store_true",
                     help="allow access without a password")
    add.add_argument("-w", "--writable", dest="writable", action="store_true",
                     default=None, help="allow clients to change files")
    add.add_argument("-r", "--read-only", dest="writable", action="store_false",
                     help="export the share read only")
    add.add_argument("--no-browse", action="store_true",
                     help="hide the share from network browsing")
    add.add_argument("--force", action="store_true",
                     help="replace an existing share of the same name")
    add.add_argument("--no-start", action="store_true",
                     help="do not start the Samba service afterwards")
    add.set_defaults(func=cmd_add)

    listing = subparsers.add_parser(
        "list", help="show the shares managed by SMB Manager"
    )
    listing.set_defaults(func=cmd_list)

    return parser


def run_cli(args) -> int:
    parser = build_parser()
    parsed = parser.parse_args(args)
    if not getattr(parsed, "func", None):
        parser.print_help()
        return 0
    return parsed.func(parsed)


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    rest = argv[1:]
    if rest and (rest[0] in CLI_COMMANDS
                 or rest[0] in {"-h", "--help", "-V", "--version"}):
        return run_cli(rest)
    if rest and rest[0] == "gui":
        rest = rest[1:]
    from .app import main as gui_main  # imported late: GTK is not always needed

    return gui_main([argv[0], *rest])


if __name__ == "__main__":
    raise SystemExit(main())
