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

from . import __version__, exports as exports_mod, shares as shares_mod, system

# Sub commands that keep us out of GTK.  Everything else starts the window.
CLI_COMMANDS = ("add", "list", "server", "nfs")

_SANITISE_RE = re.compile(r"[^A-Za-z0-9 ._-]+")

FILE_NOTICE = """\
Samba exports folders, not single files, so the folder holding the file is
shared instead:  {folder}
Everything else in that folder becomes reachable too. The share is read only
unless you pass --writable."""


def _split(value: str) -> list:
    """Accept both "a,b" and "a b" for list valued options."""
    return [word for word in re.split(r"[\s,]+", value or "") if word]


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
        max_connections=args.max_connections,
        available=not args.disabled,
        hosts_allow=_split(args.hosts_allow),
        hosts_deny=_split(args.hosts_deny),
        write_list=_split(args.write_list),
    )

    problem = share.validate()
    if problem:
        if share.name_problem():
            problem += "\nUse --name to pick a different share name."
        return fail(problem)

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
            ["apply"],
            stdin_data=shares_mod.dump(
                existing + [share], shares_mod.load_settings()
            ),
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
    settings = shares_mod.load_settings()
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
    for share in shares:
        notes = list(extra_notes(share))
        if notes:
            print(f"\n{share.name}: " + "; ".join(notes))
    print(f"\nProtocol: {protocol_range(settings)}")
    return 0


def extra_notes(share):
    """The advanced options, mentioned only when they are actually set."""
    if not share.available:
        yield "disabled"
    if not share.browseable:
        yield "hidden from browsing"
    if share.max_connections:
        yield f"at most {share.max_connections} connections"
    if share.write_list:
        yield "may write: " + ", ".join(share.write_list)
    if share.hosts_allow:
        yield "only from " + ", ".join(share.hosts_allow)
    if share.hosts_deny:
        yield "never from " + ", ".join(share.hosts_deny)


def protocol_range(settings) -> str:
    newest = settings.max_protocol or "whatever Samba supports"
    return f"{settings.min_protocol or 'Samba default'} up to {newest}"


def cmd_server(args) -> int:
    settings = shares_mod.load_settings()
    if args.min_protocol is None and args.max_protocol is None:
        print(f"Oldest allowed protocol: {settings.min_protocol or '(default)'}")
        print(f"Newest allowed protocol: {settings.max_protocol or '(default)'}")
        print()
        print("Known dialects, oldest first:")
        for name, description in shares_mod.PROTOCOLS:
            print(f"    {name:<8} {description}")
        return 0

    problem = require_samba()
    if problem:
        return fail(problem)
    if args.min_protocol is not None:
        settings.min_protocol = args.min_protocol
    if args.max_protocol is not None:
        settings.max_protocol = args.max_protocol
    problem = settings.validate()
    if problem:
        return fail(problem)

    try:
        system.run_privileged(
            ["apply"], stdin_data=shares_mod.dump(shares_mod.load(), settings)
        )
    except system.PrivilegedError as exc:
        return fail(str(exc))
    print(f"Protocol range: {protocol_range(settings)}")
    if settings.min_protocol == "NT1":
        print(
            "\nWarning: SMB1 is obsolete and insecure. Only allow it for a "
            "device that genuinely cannot speak anything newer."
        )
    if system.service_active():
        print("Restart Samba for this to affect new connections: "
              "smbmanager is not restarting it for you.")
    return 0


# ------------------------------------------------------------------- nfs


def require_nfs() -> str | None:
    if not system.nfs_installed():
        return (
            "NFS is not installed.\n"
            "Install it with 'sudo pacman -S nfs-utils', or open the SMB "
            "Manager window, which offers to do it for you."
        )
    return None


def cmd_nfs_add(args) -> int:
    problem = require_nfs()
    if problem:
        return fail(problem)

    target = os.path.realpath(os.path.expanduser(args.path))
    if not os.path.isdir(target):
        if os.path.exists(target):
            return fail(
                f"{target} is a file. NFS exports whole folders, so pass the "
                "folder you want to offer."
            )
        return fail(f"no such folder: {target}")

    specs = []
    for value in args.clients or []:
        specs.extend(_split(value))
    template = exports_mod.Client(
        writable=not args.read_only,
        sync=not args.async_writes,
        root_squash=not args.no_root_squash,
        all_squash=args.all_squash,
    )
    export = exports_mod.Export(
        path=target,
        comment=args.comment,
        clients=[
            exports_mod.Client(
                spec=spec,
                writable=template.writable,
                sync=template.sync,
                root_squash=template.root_squash,
                all_squash=template.all_squash,
            )
            for spec in (specs or ["*"])
        ],
    )

    problem = export.validate()
    if problem:
        return fail(problem)

    existing = exports_mod.load()
    clash = next((e for e in existing if e.path == export.path), None)
    if clash and not args.force:
        return fail(
            f"'{clash.path}' is already exported.\n"
            "Pass --force to replace that export."
        )
    if clash:
        existing = [e for e in existing if e is not clash]

    try:
        system.run_privileged(
            ["nfs-apply"], stdin_data=exports_mod.dump(existing + [export])
        )
    except system.PrivilegedError as exc:
        return fail(str(exc))

    access = "read-write" if export.clients[0].writable else "read-only"
    print(f"Exporting {export.path} ({access}) to "
          + ", ".join(c.spec for c in export.clients))

    if not args.no_start and not system.nfs_service_active():
        try:
            system.run_privileged(["nfs-service", "start"])
            print("Started the NFS server.")
        except system.PrivilegedError as exc:
            print(f"The export was saved, but NFS did not start: {exc}",
                  file=sys.stderr)

    address = (system.local_addresses() or [system.hostname()])[0]
    print(f"Mount it with  sudo mount -t nfs {address}:{export.path} /mnt")
    if any(c.spec == "*" for c in export.clients):
        print()
        print(
            "Note: * means every host that can reach this machine. NFS "
            "authenticates the client, not a user, so restrict it with "
            "--client 192.168.1.0/24 if this is not a trusted network."
        )
    return 0


def cmd_nfs_list(args) -> int:
    all_exports = sorted(exports_mod.load(), key=lambda e: e.path.lower())
    if not all_exports:
        print("No NFS exports are managed by SMB Manager yet.")
        return 0
    for export in all_exports:
        first = export.clients[0] if export.clients else exports_mod.Client()
        access = "read-write" if first.writable else "read-only"
        print(f"{export.path}  ({access})")
        if export.comment:
            print(f"    {export.comment}")
        for client in export.clients:
            print(f"    {client.spec}  ({','.join(client.options())})")
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

    advanced = add.add_argument_group("advanced")
    advanced.add_argument("--max-connections", type=int, default=0,
                          metavar="N",
                          help="clients allowed at once (0: no limit)")
    advanced.add_argument("--write-list", default="", metavar="USERS",
                          help="users who may write even when read only")
    advanced.add_argument("--hosts-allow", default="", metavar="HOSTS",
                          help="only these hosts may connect, e.g. "
                               "192.168.1.0/24")
    advanced.add_argument("--hosts-deny", default="", metavar="HOSTS",
                          help="hosts that are always turned away")
    advanced.add_argument("--disabled", action="store_true",
                          help="save the share but do not serve it yet")
    add.set_defaults(func=cmd_add)

    listing = subparsers.add_parser(
        "list", help="show the shares managed by SMB Manager"
    )
    listing.set_defaults(func=cmd_list)

    server = subparsers.add_parser(
        "server",
        help="show or change server wide settings",
        description="Protocol versions are negotiated before a client picks a "
                    "share, so Samba only accepts them server wide. Without "
                    "options this prints the current range.",
    )
    server.add_argument("--min-protocol", metavar="DIALECT",
                        choices=shares_mod.PROTOCOL_NAMES,
                        help="oldest dialect a client may use")
    server.add_argument("--max-protocol", metavar="DIALECT",
                        choices=("",) + shares_mod.PROTOCOL_NAMES,
                        help="newest dialect a client may use "
                             "(empty: Samba's own default)")
    server.set_defaults(func=cmd_server)

    nfs = subparsers.add_parser(
        "nfs",
        help="offer folders over NFS instead of SMB",
        description="NFS is the file sharing Unix and Linux clients speak "
                    "natively. It trusts hosts rather than passwords, so who "
                    "may mount an export is decided by address.",
    )
    nfs_commands = nfs.add_subparsers(dest="nfs_command")

    nfs_add = nfs_commands.add_parser("add", help="export a folder over NFS")
    nfs_add.add_argument("path", help="folder to export")
    nfs_add.add_argument("-c", "--comment", default="",
                         help="description kept as a comment in the file")
    nfs_add.add_argument("--client", dest="clients", action="append",
                         metavar="SPEC",
                         help="host, subnet or wildcard allowed to mount, "
                              "repeatable (default: *)")
    nfs_add.add_argument("-r", "--read-only", action="store_true",
                         help="export read only")
    nfs_add.add_argument("--no-root-squash", action="store_true",
                         help="let a client's root act as root here "
                              "(rarely a good idea)")
    nfs_add.add_argument("--all-squash", action="store_true",
                         help="map every client user to the anonymous account")
    nfs_add.add_argument("--async", dest="async_writes", action="store_true",
                         help="acknowledge writes before they reach the disk")
    nfs_add.add_argument("--force", action="store_true",
                         help="replace an existing export of the same folder")
    nfs_add.add_argument("--no-start", action="store_true",
                         help="do not start the NFS server afterwards")
    nfs_add.set_defaults(func=cmd_nfs_add)

    nfs_list = nfs_commands.add_parser(
        "list", help="show the exports managed by SMB Manager"
    )
    nfs_list.set_defaults(func=cmd_nfs_list)

    nfs.set_defaults(func=lambda _a: (nfs.print_help(), 0)[1])

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
