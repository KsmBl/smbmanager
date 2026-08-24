"""Talking to the system: samba status, and privileged calls via pkexec."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import threading

from gi.repository import GLib

APP_ID = "de.synthelicz.SmbManager"

# Where the helper lives once the project is installed, plus the in-tree copy
# so the application also runs straight from a git checkout.
_INSTALLED_HELPER = "/usr/lib/smbmanager/smbmanager-helper"
_LOCAL_HELPER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "helper",
    "smbmanager-helper",
)


class PrivilegedError(Exception):
    """Raised when a helper call fails or the user cancels the auth dialog."""


def helper_path() -> str:
    if os.path.exists(_INSTALLED_HELPER):
        return _INSTALLED_HELPER
    return _LOCAL_HELPER


def current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "")


# ------------------------------------------------------------ unprivileged


def samba_installed() -> bool:
    for name in ("smbd", "smbpasswd"):
        if shutil.which(name, path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"):
            return True
    return os.path.exists("/usr/bin/smbd")


SAMBA_FAMILY = ("samba", "smbclient", "libwbclient", "ldb", "talloc",
                "tevent", "tdb")

PARTIAL_UPGRADE_FIX = "sudo pacman -Syu"


def _first_lines(text: str, limit: int = 3) -> str:
    """The linker repeats itself once per library; a few lines are enough."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    extra = len(lines) - limit
    kept = "\n".join(lines[:limit])
    return kept + (f"\n... and {extra} more lines like this" if extra > 0 else "")


def samba_healthy() -> tuple:
    """Can smbd actually be executed?  Returns (ok, detail).

    A samba package whose companion libraries are outdated - the classic Arch
    partial upgrade - installs fine but cannot start.  The dynamic linker
    reports that without any privileges, so this check needs no pkexec.
    """
    if not samba_installed():
        return False, "samba is not installed"
    try:
        result = subprocess.run(
            ["smbd", "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stderr or "") + (result.stdout or "")
    broken = (
        "PRIVATE_SAMBA' not found" in output
        or "error while loading shared libraries" in output
        or "symbol lookup error" in output
    )
    if result.returncode != 0 or broken:
        return False, _first_lines(output)
    return True, result.stdout.strip()


def pending_samba_updates() -> list:
    """Samba packages with a newer version in the local sync database."""
    try:
        result = subprocess.run(
            ["pacman", "-Qu"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        line.strip() for line in result.stdout.splitlines()
        if line.split(" ", 1)[0] in SAMBA_FAMILY
    ]


def _systemctl(*args) -> str:
    try:
        result = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def service_active() -> bool:
    return _systemctl("is-active", "smb.service") == "active"


def service_enabled() -> bool:
    return _systemctl("is-enabled", "smb.service") == "enabled"


def hostname() -> str:
    return GLib.get_host_name()


def local_addresses() -> list:
    """Best effort list of LAN addresses, used for the connection hint."""
    addresses = []
    try:
        result = subprocess.run(
            ["ip", "-4", "-json", "addr"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for iface in json.loads(result.stdout or "[]"):
            if iface.get("ifname") == "lo":
                continue
            for info in iface.get("addr_info", []):
                if info.get("family") == "inet" and info.get("local"):
                    addresses.append(info["local"])
    except Exception:
        pass
    return addresses


class Status:
    """Snapshot of everything the window needs to render itself."""

    def __init__(self):
        self.installed = samba_installed()
        self.healthy, self.health_detail = (
            samba_healthy() if self.installed else (False, "")
        )
        self.active = self.installed and service_active()
        self.enabled = self.installed and service_enabled()

    @property
    def usable(self) -> bool:
        return self.installed and self.healthy

    @property
    def summary(self) -> str:
        if not self.installed:
            return "Samba is not installed"
        if not self.healthy:
            return "Samba is installed but cannot run"
        return "Running" if self.active else "Stopped"


# -------------------------------------------------------------- privileged


def run_privileged(args, stdin_data: str | None = None, timeout: int = 600):
    """Run the helper through pkexec and return its stdout.

    Raises :class:`PrivilegedError` with a readable message on failure.
    """
    helper = helper_path()
    if not os.path.exists(helper):
        raise PrivilegedError(f"The helper script is missing: {helper}")
    argv = ["pkexec", helper, *args]
    try:
        result = subprocess.run(
            argv,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise PrivilegedError(
            "pkexec was not found. Install polkit to grant SMB Manager "
            "administrator rights."
        )
    except subprocess.TimeoutExpired:
        raise PrivilegedError("The privileged helper timed out.")
    if result.returncode == 126:
        raise PrivilegedError("Authentication was cancelled.")
    if result.returncode == 127:
        raise PrivilegedError("pkexec could not start the helper.")
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise PrivilegedError(message or "The privileged helper failed.")
    return result.stdout.strip()


def run_async(work, on_success, on_error):
    """Run ``work`` off the main loop and deliver the result on the UI thread."""

    def target():
        try:
            value = work()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            GLib.idle_add(on_error, exc)
        else:
            GLib.idle_add(on_success, value)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------- app state


def _state_file() -> str:
    directory = os.path.join(GLib.get_user_config_dir(), "smbmanager")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "state.json")


def load_state() -> dict:
    try:
        with open(_state_file(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict):
    try:
        with open(_state_file(), "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


def password_is_set(user: str) -> bool:
    return user in load_state().get("samba_users", [])


def remember_password_set(user: str):
    state = load_state()
    users = set(state.get("samba_users", []))
    users.add(user)
    state["samba_users"] = sorted(users)
    save_state(state)


def forget_password(user: str):
    state = load_state()
    state["samba_users"] = [u for u in state.get("samba_users", []) if u != user]
    save_state(state)
