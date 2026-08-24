"""Dialogs: editing a share and setting the Samba password."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk  # noqa: E402

from . import system  # noqa: E402
from .shares import Share  # noqa: E402


def _row_label(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=1.0)
    label.get_style_context().add_class("dim-label")
    return label


class ShareDialog(Gtk.Dialog):
    """Create or edit a single share."""

    def __init__(self, parent, share: Share | None = None, existing_names=()):
        editing = share is not None
        super().__init__(
            title="Edit share" if editing else "New share",
            transient_for=parent,
            modal=True,
            use_header_bar=True,
        )
        self.set_default_size(520, -1)
        self._existing = {n.lower() for n in existing_names}
        self._editing_name = share.name.lower() if editing else None
        self._user = system.current_user()
        self._name_touched = editing

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = self.add_button("Save" if editing else "Add", Gtk.ResponseType.OK)
        save.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12, margin=18)
        grid.set_column_homogeneous(False)
        grid.set_valign(Gtk.Align.START)
        self.get_content_area().add(grid)

        row = 0
        # -- folder ---------------------------------------------------------
        self.folder = Gtk.FileChooserButton(
            title="Select the folder to share",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        self.folder.set_hexpand(True)
        self.folder.connect("file-set", self._on_folder_set)
        grid.attach(_row_label("Folder"), 0, row, 1, 1)
        grid.attach(self.folder, 1, row, 1, 1)
        row += 1

        # -- name -----------------------------------------------------------
        self.name = Gtk.Entry(hexpand=True)
        self.name.set_placeholder_text("Name shown on the network")
        self.name.connect("changed", self._on_name_changed)
        grid.attach(_row_label("Share name"), 0, row, 1, 1)
        grid.attach(self.name, 1, row, 1, 1)
        row += 1

        # -- comment --------------------------------------------------------
        self.comment = Gtk.Entry(hexpand=True)
        self.comment.set_placeholder_text("Optional description")
        grid.attach(_row_label("Description"), 0, row, 1, 1)
        grid.attach(self.comment, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        # -- users ----------------------------------------------------------
        self.users = Gtk.Entry(hexpand=True)
        self.users.set_placeholder_text("Space separated user names")
        self.users.set_tooltip_text(
            "Users allowed to connect. They need a Samba password, which you "
            "can set from the menu."
        )
        grid.attach(_row_label("Allowed users"), 0, row, 1, 1)
        grid.attach(self.users, 1, row, 1, 1)
        row += 1

        self.writable = self._switch_row(
            grid, row, "Allow writing",
            "Clients may create and change files in the share.",
        )
        row += 1
        self.guest = self._switch_row(
            grid, row, "Guest access",
            "Anyone on the network can open the share without a password.",
        )
        self.guest.connect("notify::active", self._on_guest_toggled)
        row += 1
        self.browseable = self._switch_row(
            grid, row, "Visible when browsing",
            "Show the share in the network neighbourhood instead of hiding it.",
        )
        row += 1

        self.hint = Gtk.Label(xalign=0.0, wrap=True)
        self.hint.get_style_context().add_class("dim-label")
        grid.attach(self.hint, 0, row, 2, 1)

        self._share = share or Share(name="", path="", valid_users=[self._user])
        self._load(self._share)
        self.show_all()
        self._update_hint()

    # -- helpers -----------------------------------------------------------
    def _switch_row(self, grid, row, title, subtitle) -> Gtk.Switch:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        label = Gtk.Label(label=title, xalign=0.0)
        sub = Gtk.Label(label=subtitle, xalign=0.0)
        sub.get_style_context().add_class("dim-label")
        sub.set_line_wrap(True)
        box.add(label)
        box.add(sub)
        switch = Gtk.Switch(halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        grid.attach(box, 0, row, 1, 1)
        grid.attach(switch, 1, row, 1, 1)
        box.set_hexpand(True)
        return switch

    def _load(self, share: Share):
        if share.path:
            self.folder.set_filename(share.path)
        self.name.set_text(share.name)
        self.comment.set_text(share.comment)
        self.users.set_text(" ".join(share.valid_users or [self._user]))
        self.writable.set_active(share.writable)
        self.guest.set_active(share.guest_ok)
        self.browseable.set_active(share.browseable)
        self.users.set_sensitive(not share.guest_ok)

    def _on_folder_set(self, chooser):
        path = chooser.get_filename() or ""
        if path and not self._name_touched:
            self.name.set_text(os.path.basename(path.rstrip("/")) or "share")
            self._name_touched = False
        self._update_hint()

    def _on_name_changed(self, _entry):
        self._name_touched = True

    def _on_guest_toggled(self, switch, _param):
        self.users.set_sensitive(not switch.get_active())
        self._update_hint()

    def _update_hint(self):
        if self.guest.get_active():
            self.hint.set_text(
                "Guest shares are readable by everyone who can reach this "
                "machine. Files are accessed as "
                f"'{self._user}', so permissions of that account apply."
            )
        else:
            self.hint.set_text(
                "Each allowed user connects with their Samba password. "
                "Set yours from the menu if you have not done so."
            )

    # -- result ------------------------------------------------------------
    def get_share(self) -> Share:
        share = self._share
        share.name = self.name.get_text().strip()
        share.path = self.folder.get_filename() or share.path
        share.comment = self.comment.get_text().strip()
        share.valid_users = self.users.get_text().split()
        share.writable = self.writable.get_active()
        share.guest_ok = self.guest.get_active()
        share.browseable = self.browseable.get_active()
        share.force_user = self._user if share.guest_ok else ""
        return share

    def problem(self) -> str | None:
        share = self.get_share()
        error = share.validate()
        if error:
            return error
        if share.name.lower() != self._editing_name and \
                share.name.lower() in self._existing:
            return f"A share called '{share.name}' already exists."
        return None


class PasswordDialog(Gtk.Dialog):
    """Ask for the Samba password of a user."""

    def __init__(self, parent, user: str):
        super().__init__(
            title="Samba password",
            transient_for=parent,
            modal=True,
            use_header_bar=True,
        )
        self.set_default_size(460, -1)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self._ok = self.add_button("Set password", Gtk.ResponseType.OK)
        self._ok.get_style_context().add_class("suggested-action")
        self._ok.set_sensitive(False)
        self.set_default_response(Gtk.ResponseType.OK)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=18
        )
        box.set_valign(Gtk.Align.START)
        self.get_content_area().add(box)

        intro = Gtk.Label(xalign=0.0, wrap=True)
        intro.set_markup(
            f"Samba keeps its own password database, so it cannot read the "
            f"login password of <b>{GLib.markup_escape_text(user)}</b> from the system.\n"
            f"Type your system password here to keep both in sync — or pick a "
            f"different one just for file sharing."
        )
        box.add(intro)

        grid = Gtk.Grid(row_spacing=8, column_spacing=12)
        box.add(grid)

        self.entry = Gtk.Entry(visibility=False, hexpand=True)
        self.entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic"
        )
        self.entry.connect("icon-press", self._toggle_visibility)
        self.repeat = Gtk.Entry(visibility=False, hexpand=True)
        self.repeat.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        for entry in (self.entry, self.repeat):
            entry.connect("changed", self._validate)
            entry.set_activates_default(True)

        grid.attach(_row_label("Password"), 0, 0, 1, 1)
        grid.attach(self.entry, 1, 0, 1, 1)
        grid.attach(_row_label("Repeat"), 0, 1, 1, 1)
        grid.attach(self.repeat, 1, 1, 1, 1)

        self.feedback = Gtk.Label(xalign=0.0, wrap=True)
        self.feedback.get_style_context().add_class("dim-label")
        box.add(self.feedback)
        self.show_all()

    def _toggle_visibility(self, entry, _pos, _event):
        visible = not entry.get_visibility()
        entry.set_visibility(visible)
        entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY,
            "view-conceal-symbolic" if visible else "view-reveal-symbolic",
        )

    def _validate(self, _entry=None):
        password = self.entry.get_text()
        repeat = self.repeat.get_text()
        if not password:
            self.feedback.set_text("")
            self._ok.set_sensitive(False)
            return
        if repeat and password != repeat:
            self.feedback.set_text("The two passwords do not match.")
            self._ok.set_sensitive(False)
            return
        self.feedback.set_text("")
        self._ok.set_sensitive(password == repeat)

    def get_password(self) -> str:
        return self.entry.get_text()
