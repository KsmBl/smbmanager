"""Dialogs: editing shares and exports, server settings, and passwords."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk  # noqa: E402

from . import system  # noqa: E402
from .exports import Client, Export  # noqa: E402
from .shares import PROTOCOLS, Share, ServerSettings  # noqa: E402


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
        row += 1

        grid.attach(self._build_advanced(), 0, row, 2, 1)

        self._share = share or Share(name="", path="", valid_users=[self._user])
        self._load(self._share)
        self.show_all()
        self._update_hint()

    # -- advanced ----------------------------------------------------------
    def _build_advanced(self) -> Gtk.Expander:
        """Options most people never touch, folded away until they do."""
        expander = Gtk.Expander(label="Advanced")
        expander.set_margin_top(6)
        grid = Gtk.Grid(row_spacing=10, column_spacing=12, margin_top=12,
                        margin_start=12)
        expander.add(grid)

        row = 0
        self.max_connections = Gtk.SpinButton.new_with_range(0, 4096, 1)
        self.max_connections.set_hexpand(True)
        self.max_connections.set_halign(Gtk.Align.START)
        self.max_connections.set_tooltip_text(
            "How many clients may use this share at the same time. "
            "0 leaves it to Samba."
        )
        limit_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        limit_box.add(self.max_connections)
        unlimited = Gtk.Label(label="0 = no limit", xalign=0.0)
        unlimited.get_style_context().add_class("dim-label")
        limit_box.add(unlimited)
        grid.attach(_row_label("Max connections"), 0, row, 1, 1)
        grid.attach(limit_box, 1, row, 1, 1)
        row += 1

        self.write_list = Gtk.Entry(hexpand=True)
        self.write_list.set_placeholder_text("Space separated user names")
        self.write_list.set_tooltip_text(
            "Users who may write even when the share is read only."
        )
        grid.attach(_row_label("Write anyway"), 0, row, 1, 1)
        grid.attach(self.write_list, 1, row, 1, 1)
        row += 1

        self.hosts_allow = Gtk.Entry(hexpand=True)
        self.hosts_allow.set_placeholder_text("Any host  (e.g. 192.168.1.)")
        self.hosts_allow.set_tooltip_text(
            "Only these hosts may connect. Names, addresses or subnets such "
            "as 192.168.1. or 192.168.1.0/24."
        )
        grid.attach(_row_label("Allowed hosts"), 0, row, 1, 1)
        grid.attach(self.hosts_allow, 1, row, 1, 1)
        row += 1

        self.hosts_deny = Gtk.Entry(hexpand=True)
        self.hosts_deny.set_placeholder_text("No host")
        self.hosts_deny.set_tooltip_text("Hosts that are turned away.")
        grid.attach(_row_label("Denied hosts"), 0, row, 1, 1)
        grid.attach(self.hosts_deny, 1, row, 1, 1)
        row += 1

        self.available = self._switch_row(
            grid, row, "Share is available",
            "Turn off to park the share without deleting it.",
        )
        return expander

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
        self.max_connections.set_value(share.max_connections)
        self.write_list.set_text(" ".join(share.write_list))
        self.hosts_allow.set_text(" ".join(share.hosts_allow))
        self.hosts_deny.set_text(" ".join(share.hosts_deny))
        self.available.set_active(share.available)

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
        share.max_connections = int(self.max_connections.get_value())
        share.write_list = self.write_list.get_text().split()
        share.hosts_allow = self.hosts_allow.get_text().split()
        share.hosts_deny = self.hosts_deny.get_text().split()
        share.available = self.available.get_active()
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


class ServerSettingsDialog(Gtk.Dialog):
    """Settings that apply to the whole server rather than a single share."""

    def __init__(self, parent, settings: ServerSettings):
        super().__init__(
            title="Server settings",
            transient_for=parent,
            modal=True,
            use_header_bar=True,
        )
        self.set_default_size(540, -1)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = self.add_button("Save", Gtk.ResponseType.OK)
        save.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        self._settings = settings
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18
        )
        box.set_valign(Gtk.Align.START)
        self.get_content_area().add(box)

        intro = Gtk.Label(xalign=0.0, wrap=True)
        intro.set_text(
            "Clients and the server agree on a protocol version before they "
            "get as far as picking a share, so these limits apply to every "
            "share at once — Samba has no per share equivalent."
        )
        intro.get_style_context().add_class("dim-label")
        box.add(intro)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12)
        box.add(grid)

        self.min_protocol = self._protocol_combo(allow_empty=False)
        grid.attach(_row_label("Oldest allowed"), 0, 0, 1, 1)
        grid.attach(self.min_protocol, 1, 0, 1, 1)

        self.max_protocol = self._protocol_combo(allow_empty=True)
        grid.attach(_row_label("Newest allowed"), 0, 1, 1, 1)
        grid.attach(self.max_protocol, 1, 1, 1, 1)

        self.warning = Gtk.Label(xalign=0.0, wrap=True)
        box.add(self.warning)

        self._select(self.min_protocol, settings.min_protocol)
        self._select(self.max_protocol, settings.max_protocol)
        self.min_protocol.connect("changed", self._on_changed)
        self.show_all()
        self._on_changed()

    def _protocol_combo(self, allow_empty: bool) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        combo.set_hexpand(True)
        if allow_empty:
            combo.append("", "Whatever Samba supports")
        for name, description in PROTOCOLS:
            combo.append(name, description)
        return combo

    @staticmethod
    def _select(combo: Gtk.ComboBoxText, value: str):
        if not combo.set_active_id(value or ""):
            combo.set_active(0)

    def _on_changed(self, _combo=None):
        if self.min_protocol.get_active_id() == "NT1":
            self.warning.set_markup(
                "<b>SMB1 is off by default for good reason.</b> It is the "
                "protocol WannaCry spread over. Only allow it if a device on "
                "your network genuinely cannot speak anything newer."
            )
            self.warning.show()
        else:
            self.warning.set_text("")

    def get_settings(self) -> ServerSettings:
        self._settings.min_protocol = self.min_protocol.get_active_id() or ""
        self._settings.max_protocol = self.max_protocol.get_active_id() or ""
        return self._settings

    def problem(self) -> str | None:
        return self.get_settings().validate()


class ExportDialog(Gtk.Dialog):
    """Create or edit a single NFS export."""

    def __init__(self, parent, export: Export | None = None, existing_paths=()):
        editing = export is not None
        super().__init__(
            title="Edit export" if editing else "New export",
            transient_for=parent,
            modal=True,
            use_header_bar=True,
        )
        self.set_default_size(540, -1)
        self._existing = set(existing_paths)
        self._editing_path = export.path if editing else None

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        save = self.add_button("Save" if editing else "Add",
                               Gtk.ResponseType.OK)
        save.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

        grid = Gtk.Grid(row_spacing=10, column_spacing=12, margin=18)
        grid.set_valign(Gtk.Align.START)
        self.get_content_area().add(grid)

        row = 0
        self.folder = Gtk.FileChooserButton(
            title="Select the folder to export",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        self.folder.set_hexpand(True)
        grid.attach(_row_label("Folder"), 0, row, 1, 1)
        grid.attach(self.folder, 1, row, 1, 1)
        row += 1

        self.comment = Gtk.Entry(hexpand=True)
        self.comment.set_placeholder_text("Optional description")
        grid.attach(_row_label("Description"), 0, row, 1, 1)
        grid.attach(self.comment, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(), 0, row, 2, 1)
        row += 1

        self.clients = Gtk.Entry(hexpand=True)
        self.clients.set_placeholder_text("*  or  192.168.1.0/24  or  host.lan")
        self.clients.set_tooltip_text(
            "Who may mount this export. Space separated: host names, "
            "addresses, subnets, or * for everyone who can reach this machine."
        )
        grid.attach(_row_label("Clients"), 0, row, 1, 1)
        grid.attach(self.clients, 1, row, 1, 1)
        row += 1

        self.writable = self._switch_row(
            grid, row, "Allow writing",
            "Clients may create and change files (rw instead of ro).",
        )
        row += 1
        self.sync = self._switch_row(
            grid, row, "Write safely",
            "Confirm writes only once they reached the disk. Turning this "
            "off is faster and risks losing data if the server crashes.",
        )
        row += 1
        self.root_squash = self._switch_row(
            grid, row, "Squash remote root",
            "Treat a client's root as an anonymous user instead of this "
            "machine's root. Leave this on unless you know you need it.",
        )
        row += 1
        self.all_squash = self._switch_row(
            grid, row, "Squash every user",
            "Map all client users to the anonymous account, so files belong "
            "to nobody in particular.",
        )
        row += 1

        self.note = Gtk.Label(xalign=0.0, wrap=True)
        self.note.get_style_context().add_class("dim-label")
        grid.attach(self.note, 0, row, 2, 1)

        self._export = export or Export(path="", clients=[Client(spec="*")])
        self._load(self._export)
        self.show_all()

    _switch_row = ShareDialog._switch_row

    def _load(self, export: Export):
        if export.path:
            self.folder.set_filename(export.path)
        self.comment.set_text(export.comment)
        self.clients.set_text(" ".join(c.spec for c in export.clients))
        first = export.clients[0] if export.clients else Client()
        self.writable.set_active(first.writable)
        self.sync.set_active(first.sync)
        self.root_squash.set_active(first.root_squash)
        self.all_squash.set_active(first.all_squash)
        if export.clients and not export.uniform():
            self.note.set_markup(
                "<b>This export gives different options to different "
                "clients.</b> The switches show the first client's settings; "
                "saving applies them to all of them."
            )

    def get_export(self) -> Export:
        export = self._export
        export.path = self.folder.get_filename() or export.path
        export.comment = self.comment.get_text().strip()
        previous = {c.spec: c for c in export.clients}
        clients = []
        for spec in self.clients.get_text().split():
            # keep options this application does not model, per client
            extra = previous[spec].extra if spec in previous else []
            clients.append(Client(
                spec=spec,
                writable=self.writable.get_active(),
                sync=self.sync.get_active(),
                root_squash=self.root_squash.get_active(),
                all_squash=self.all_squash.get_active(),
                extra=list(extra),
            ))
        export.clients = clients
        return export

    def problem(self) -> str | None:
        export = self.get_export()
        error = export.validate()
        if error:
            return error
        if export.path != self._editing_path and export.path in self._existing:
            return f"'{export.path}' is already exported."
        return None
