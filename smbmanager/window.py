"""The main SMB Manager window."""

from __future__ import annotations

import copy
import os

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import shares as shares_mod  # noqa: E402
from . import system  # noqa: E402
from .dialogs import PasswordDialog, ShareDialog  # noqa: E402

CSS = b"""
.share-name { font-weight: bold; }
.mono { font-family: monospace; }
"""


def apply_css():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


class ShareRow(Gtk.ListBoxRow):
    def __init__(self, share, on_edit, on_delete):
        super().__init__()
        self.share = share
        # selection is off, so a focused row must not look like a selected one
        self.set_can_focus(False)
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
            margin=12,
        )
        self.add(box)

        icon = Gtk.Image.new_from_icon_name(
            "folder-publicshare-symbolic", Gtk.IconSize.DND
        )
        icon.set_valign(Gtk.Align.START)
        box.add(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_hexpand(True)
        box.add(text)

        title = Gtk.Label(label=share.name, xalign=0.0)
        title.get_style_context().add_class("share-name")
        text.add(title)

        path = Gtk.Label(label=share.path, xalign=0.0)
        path.get_style_context().add_class("dim-label")
        path.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        text.add(path)

        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for caption in self._badges(share):
            label = Gtk.Label(label=caption, xalign=0.0)
            label.get_style_context().add_class("dim-label")
            badges.add(label)
        text.add(badges)

        edit = Gtk.Button.new_from_icon_name(
            "document-edit-symbolic", Gtk.IconSize.BUTTON
        )
        edit.set_tooltip_text("Edit this share")
        edit.set_valign(Gtk.Align.CENTER)
        edit.connect("clicked", lambda _b: on_edit(share))
        box.add(edit)

        delete = Gtk.Button.new_from_icon_name(
            "user-trash-symbolic", Gtk.IconSize.BUTTON
        )
        delete.set_tooltip_text("Remove this share")
        delete.set_valign(Gtk.Align.CENTER)
        delete.connect("clicked", lambda _b: on_delete(share))
        box.add(delete)

    @staticmethod
    def _badges(share):
        yield "writable" if share.writable else "read only"
        yield "guest access" if share.guest_ok else (
            "users: " + ", ".join(share.valid_users) if share.valid_users
            else "no users"
        )
        if not share.browseable:
            yield "hidden"
        if share.path and not os.path.isdir(share.path):
            yield "⚠ folder is missing"


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title="SMB Manager")
        self.set_default_size(720, 620)
        self.user = system.current_user()
        self.status = system.Status()
        self.shares = []
        self._busy = 0

        self._warning_action = None
        self._build_body()
        self.refresh()

    # ------------------------------------------------------------- layout
    def _menu_item(self, menu, label, callback, key=None, mod=0):
        item = Gtk.MenuItem(label=label, use_underline=True)
        item.connect("activate", lambda _i: callback())
        if key is not None:
            item.add_accelerator(
                "activate", self.accels, key, mod, Gtk.AccelFlags.VISIBLE
            )
        menu.append(item)
        return item

    def _build_menubar(self) -> Gtk.MenuBar:
        bar = Gtk.MenuBar()
        control = Gdk.ModifierType.CONTROL_MASK

        file_menu = Gtk.Menu()
        self.add_item = self._menu_item(
            file_menu, "_Add Share…", self.on_add_share, Gdk.KEY_n, control
        )
        self._menu_item(
            file_menu, "_Reload", lambda: self.refresh(notify=True), Gdk.KEY_F5
        )
        file_menu.append(Gtk.SeparatorMenuItem())
        self._menu_item(file_menu, "_Quit", self._quit, Gdk.KEY_q, control)

        samba_menu = Gtk.Menu()
        self.start_item = self._menu_item(
            samba_menu, "_Start", lambda: self.on_service("start")
        )
        self.stop_item = self._menu_item(
            samba_menu, "S_top", lambda: self.on_service("stop")
        )
        self.restart_item = self._menu_item(
            samba_menu, "_Restart", lambda: self.on_service("restart")
        )
        samba_menu.append(Gtk.SeparatorMenuItem())
        self.boot_item = Gtk.CheckMenuItem(label="Start at _Boot",
                                           use_underline=True)
        self._boot_handler = self.boot_item.connect(
            "toggled", self.on_boot_toggled
        )
        samba_menu.append(self.boot_item)
        samba_menu.append(Gtk.SeparatorMenuItem())
        self.password_item = self._menu_item(
            samba_menu, "Set Samba _Password…", self.on_set_password
        )
        samba_menu.append(Gtk.SeparatorMenuItem())
        self.install_item = self._menu_item(
            samba_menu, "_Install Samba…", self.on_install
        )
        self.repair_item = self._menu_item(
            samba_menu, "Repair Installation…", self.on_repair
        )

        help_menu = Gtk.Menu()
        self._menu_item(help_menu, "_About", self.on_about)

        for label, menu in (
            ("_File", file_menu), ("_Samba", samba_menu), ("_Help", help_menu)
        ):
            item = Gtk.MenuItem(label=label, use_underline=True)
            item.set_submenu(menu)
            bar.append(item)
        return bar

    def _build_toolbar(self) -> Gtk.Toolbar:
        bar = Gtk.Toolbar()
        bar.set_style(Gtk.ToolbarStyle.BOTH_HORIZ)
        bar.get_style_context().add_class("primary-toolbar")

        def tool(icon, label, tooltip, callback, important=False):
            button = Gtk.ToolButton(icon_name=icon, label=label)
            button.set_tooltip_text(tooltip)
            button.set_is_important(important)
            button.connect("clicked", lambda _b: callback())
            bar.insert(button, -1)
            return button

        self.add_button = tool(
            "list-add", "Add Share", "Share a folder (Ctrl+N)",
            self.on_add_share, important=True,
        )
        self.reload_button = tool(
            "view-refresh", "Reload", "Read the configuration again (F5)",
            lambda: self.refresh(notify=True),
        )
        bar.insert(Gtk.SeparatorToolItem(), -1)
        self.start_button = tool(
            "media-playback-start", "Start", "Start the Samba service",
            lambda: self.on_service("start"),
        )
        self.stop_button = tool(
            "media-playback-stop", "Stop", "Stop the Samba service",
            lambda: self.on_service("stop"),
        )
        self.restart_button = tool(
            "system-reboot", "Restart", "Restart the Samba service",
            lambda: self.on_service("restart"),
        )

        spacer = Gtk.SeparatorToolItem()
        spacer.set_draw(False)
        spacer.set_expand(True)
        bar.insert(spacer, -1)

        holder = Gtk.ToolItem()
        self.spinner = Gtk.Spinner()
        self.spinner.set_margin_end(6)
        holder.add(self.spinner)
        bar.insert(holder, -1)
        return bar

    def _build_body(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)

        self.accels = Gtk.AccelGroup()
        self.add_accel_group(self.accels)

        self.menubar = self._build_menubar()
        outer.pack_start(self.menubar, False, False, 0)
        outer.pack_start(self._build_toolbar(), False, False, 0)

        # Standing problem with the installation - stays until it is fixed.
        self.warning_bar = Gtk.InfoBar()
        self.warning_label = Gtk.Label(xalign=0.0, wrap=True)
        self.warning_bar.get_content_area().add(self.warning_label)
        self.warning_action = self.warning_bar.add_button("Install Samba", 1)
        self.warning_bar.connect("response", self._on_warning_response)
        self.warning_bar.set_no_show_all(True)
        outer.pack_start(self.warning_bar, False, False, 0)

        # Transient feedback for the last action.
        self.infobar = Gtk.InfoBar(show_close_button=True)
        self.infobar.connect("response", lambda bar, _r: bar.hide())
        self.infobar_label = Gtk.Label(xalign=0.0, wrap=True)
        self.infobar.get_content_area().add(self.infobar_label)
        self.infobar.set_no_show_all(True)
        outer.pack_start(self.infobar, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.set_vexpand(True)
        outer.pack_start(scroller, True, True, 0)

        self.share_list = Gtk.ListBox()
        self.share_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.share_list.connect(
            "row-activated", lambda _lb, row: self.on_edit_share(row.share)
        )
        placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=32
        )
        empty_icon = Gtk.Image.new_from_icon_name(
            "folder-publicshare-symbolic", Gtk.IconSize.DIALOG
        )
        empty_icon.get_style_context().add_class("dim-label")
        placeholder.add(empty_icon)
        empty_label = Gtk.Label(
            label="No shares yet — use Add Share to share a folder."
        )
        empty_label.get_style_context().add_class("dim-label")
        placeholder.add(empty_label)
        placeholder.show_all()
        self.share_list.set_placeholder(placeholder)
        scroller.add(self.share_list)

        self.statusbar = Gtk.Statusbar()
        self._status_context = self.statusbar.get_context_id("state")
        outer.pack_start(self.statusbar, False, False, 0)

        # A file manager starts with the focus in its view, not on a button.
        for button in (self.add_button, self.reload_button, self.start_button,
                       self.stop_button, self.restart_button):
            button.set_focus_on_click(False)
        self.set_focus_child(scroller)
        GLib.idle_add(self.share_list.grab_focus)

    def _quit(self):
        application = self.get_application()
        application.quit() if application else self.destroy()

    def _on_warning_response(self, _bar, _response):
        if self._warning_action == "install":
            self.on_install()
        elif self._warning_action == "repair":
            self.on_repair()

    def refresh(self, notify: bool = False):
        self.status = system.Status()
        self.shares = shares_mod.load()
        self._render()
        if notify:
            self.message("Reloaded from disk.")

    def _render(self):
        status = self.status
        installed, active = status.installed, status.active
        broken = installed and not status.healthy

        if not installed:
            self._warning_action = "install"
            self.warning_bar.set_message_type(Gtk.MessageType.INFO)
            self.warning_label.set_text(
                "Samba is not installed. The samba package provides the file "
                "server that shares your folders."
            )
            self.warning_action.set_label("Install Samba")
            self._show_warning(True)
        elif broken:
            self._warning_action = "repair"
            self.warning_bar.set_message_type(Gtk.MessageType.WARNING)
            self.warning_label.set_markup(
                "<b>Samba cannot start.</b> Its libraries belong to a "
                "different Samba version — a partial upgrade. Repair it with "
                f"<b>{system.PARTIAL_UPGRADE_FIX}</b>."
            )
            self.warning_action.set_label("Repair")
            self._show_warning(True)
        else:
            self._warning_action = None
            self._show_warning(False)

        self.add_button.set_sensitive(installed)
        self.add_item.set_sensitive(installed)
        self.password_item.set_sensitive(installed)
        self.install_item.set_sensitive(not installed)
        self.repair_item.set_sensitive(broken)
        for widget in (self.start_button, self.start_item):
            widget.set_sensitive(status.usable and not active)
        for widget in (self.stop_button, self.stop_item):
            widget.set_sensitive(status.usable and active)
        for widget in (self.restart_button, self.restart_item):
            widget.set_sensitive(status.usable and active)

        self.boot_item.set_sensitive(status.usable)
        with self.boot_item.handler_block(self._boot_handler):
            self.boot_item.set_active(status.enabled)

        for child in self.share_list.get_children():
            self.share_list.remove(child)
        for share in sorted(self.shares, key=lambda s: s.name.lower()):
            self.share_list.add(
                ShareRow(share, self.on_edit_share, self.on_delete_share)
            )
        self.share_list.show_all()

        self.statusbar.pop(self._status_context)
        self.statusbar.push(self._status_context, self._status_text())

    def _show_warning(self, visible: bool):
        self.warning_bar.set_no_show_all(not visible)
        if visible:
            self.warning_bar.show_all()
        else:
            self.warning_bar.hide()

    def _status_text(self) -> str:
        """The statusbar summary, in the spirit of a file manager's."""
        count = len(self.shares)
        shares = f"{count} share{'' if count == 1 else 's'}"
        if not self.status.installed:
            return "Samba is not installed"
        if not self.status.healthy:
            return f"Samba cannot start  |  {shares}"
        state = "Samba is running" if self.status.active else "Samba is stopped"
        parts = [state, shares]
        if self.status.active and self.shares:
            address = (system.local_addresses() or [system.hostname()])[0]
            first = sorted(self.shares, key=lambda s: s.name.lower())[0].name
            parts.append(f"smb://{address}/{first}")
        return "  |  ".join(parts)

    def message(self, text: str, kind=Gtk.MessageType.INFO):
        self.infobar.set_message_type(kind)
        self.infobar_label.set_text(text)
        self.infobar.set_no_show_all(False)
        self.infobar.show_all()

    def error(self, text: str):
        text = (text or "Something went wrong.").strip()
        first, _, rest = text.partition("\n")
        self.message(first, Gtk.MessageType.ERROR)
        if rest.strip():
            self._error_details(first, rest.strip())

    def _error_details(self, primary: str, detail: str):
        """Long helper output belongs in a dialog, not in a one line bar."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=primary,
        )
        expander = Gtk.Expander(label="Details")
        view = Gtk.TextView(editable=False, cursor_visible=False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(detail)
        view.get_style_context().add_class("mono")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(180)
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        scroller.add(view)
        expander.add(scroller)
        expander.set_expanded(True)
        dialog.get_content_area().add(expander)
        dialog.get_content_area().show_all()
        dialog.run()
        dialog.destroy()

    def set_busy(self, busy: bool):
        self._busy += 1 if busy else -1
        self._busy = max(0, self._busy)
        running = self._busy > 0
        if running:
            self.spinner.start()
        else:
            self.spinner.stop()
        for widget in (
            self.menubar, self.add_button, self.reload_button,
            self.start_button, self.stop_button, self.restart_button,
            self.share_list, self.warning_action,
        ):
            widget.set_sensitive(not running)
        if not running:
            self._render()

    def privileged(self, args, stdin_data=None, done=None, busy_text=None):
        """Run a helper command in the background and refresh afterwards."""
        if busy_text:
            self.message(busy_text)
        self.set_busy(True)

        def work():
            return system.run_privileged(args, stdin_data)

        def success(output):
            self.set_busy(False)
            self.refresh()
            if done:
                done(output)

        def failed(exc):
            self.set_busy(False)
            self.refresh()
            self.error(str(exc))

        system.run_async(work, success, failed)

    # ------------------------------------------------------------- actions
    def on_install(self):
        pending = system.pending_samba_updates()
        if pending and not self._confirm_full_upgrade(pending):
            return
        command = "upgrade" if pending else "install"
        self.privileged(
            [command],
            busy_text=(
                "Updating the system and installing Samba — this may take a "
                "while…" if pending else
                "Installing Samba with pacman — this may take a while…"
            ),
            done=lambda _out: self.message(
                "Samba is installed. Press Start to run the service."
            ),
        )

    def _confirm_full_upgrade(self, pending) -> bool:
        """Installing samba next to outdated libraries breaks smbd."""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="Your system has pending updates",
        )
        dialog.format_secondary_text(
            "Installing Samba on its own would pair new programs with the "
            "outdated libraries below, and Samba would refuse to start.\n\n"
            + "\n".join(pending)
            + "\n\nSMB Manager can run a full system update "
            f"({system.PARTIAL_UPGRADE_FIX}) and install Samba afterwards. "
            "Other packages on your system will be updated too."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        proceed = dialog.add_button(
            "Update system and install", Gtk.ResponseType.OK
        )
        proceed.get_style_context().add_class("suggested-action")
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def on_repair(self):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Repair Samba with a full system update?",
        )
        dialog.format_secondary_text(
            "Samba was installed while parts of the system were older, so its "
            "libraries do not match. Running "
            f"{system.PARTIAL_UPGRADE_FIX} brings everything to the same "
            "version and makes Samba start again. Other packages will be "
            "updated as well."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        proceed = dialog.add_button("Update system", Gtk.ResponseType.OK)
        proceed.get_style_context().add_class("suggested-action")
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        self.privileged(
            ["upgrade"],
            busy_text="Running a full system update — this may take a while…",
            done=lambda _out: self.message(
                "Samba is repaired. Press Start to run the service."
            ),
        )

    def on_service(self, action: str):
        self.privileged(
            ["service", action],
            busy_text=f"Asking systemd to {action} Samba…",
            done=lambda _out: self.message(f"Samba service: {action} done."),
        )

    def on_boot_toggled(self, item):
        action = "enable" if item.get_active() else "disable"
        self.privileged(
            ["service", action],
            busy_text=f"{action.capitalize()} Samba at boot…",
            done=lambda _out: self.message(
                "Samba will start at boot."
                if action == "enable"
                else "Samba will no longer start at boot."
            ),
        )

    def on_set_password(self):
        dialog = PasswordDialog(self, self.user)
        if dialog.run() == Gtk.ResponseType.OK:
            password = dialog.get_password()
            dialog.destroy()
            if not self.status.installed:
                self.error("Install Samba before setting a password.")
                return
            self.privileged(
                ["passwd", self.user],
                stdin_data=password + "\n",
                busy_text="Storing the Samba password…",
                done=lambda _out: (
                    system.remember_password_set(self.user),
                    self.message(
                        f"Samba password for {self.user} is set."
                    ),
                )[1],
            )
        else:
            dialog.destroy()

    def on_add_share(self):
        dialog = ShareDialog(self, None, [s.name for s in self.shares])
        self._run_share_dialog(dialog, replace=None)

    def on_edit_share(self, share):
        dialog = ShareDialog(
            self, copy.deepcopy(share), [s.name for s in self.shares]
        )
        self._run_share_dialog(dialog, replace=share)

    def _run_share_dialog(self, dialog, replace):
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                dialog.destroy()
                return
            problem = dialog.problem()
            if problem:
                self.error(problem)
                continue
            share = dialog.get_share()
            dialog.destroy()
            break

        updated = [s for s in self.shares if s is not replace]
        updated.append(share)
        self._save_shares(
            updated,
            share,
            "Share saved." if replace else f"'{share.name}' is now shared.",
        )

    def on_delete_share(self, share):
        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Stop sharing '{share.name}'?",
        )
        confirm.format_secondary_text(
            "The folder and its contents stay untouched, only the share is "
            "removed."
        )
        confirm.add_button("Cancel", Gtk.ResponseType.CANCEL)
        remove = confirm.add_button("Remove share", Gtk.ResponseType.OK)
        remove.get_style_context().add_class("destructive-action")
        response = confirm.run()
        confirm.destroy()
        if response != Gtk.ResponseType.OK:
            return
        updated = [s for s in self.shares if s is not share]
        self._save_shares(updated, None, f"'{share.name}' is no longer shared.")

    def _save_shares(self, share_list, new_share, success_text):
        content = shares_mod.dump(share_list)
        args_queue = []
        if new_share and not os.path.isdir(new_share.path):
            args_queue.append((["mkdir", new_share.path, self.user], None))
        args_queue.append((["apply"], content))

        def done(_output):
            text = success_text
            needs_password = (
                new_share is not None
                and not new_share.guest_ok
                and self.user in new_share.valid_users
                and not system.password_is_set(self.user)
            )
            if needs_password:
                text += (
                    "  Set your Samba password from the menu, otherwise "
                    "clients cannot log in."
                )
            self.message(text)

        self._run_queue(args_queue, done, "Writing the Samba configuration…")

    def _run_queue(self, queue, done, busy_text):
        """Run several helper calls in one background thread."""
        self.message(busy_text)
        self.set_busy(True)

        def work():
            output = ""
            for args, stdin_data in queue:
                output = system.run_privileged(args, stdin_data)
            return output

        def success(output):
            self.set_busy(False)
            self.refresh()
            done(output)

        def failed(exc):
            self.set_busy(False)
            self.refresh()
            self.error(str(exc))

        system.run_async(work, success, failed)

    def on_about(self):
        about = Gtk.AboutDialog(transient_for=self, modal=True)
        about.set_program_name("SMB Manager")
        about.set_version("1.0.0")
        about.set_comments(
            "Share folders over SMB, manage the Samba service and its users "
            "on Arch Linux."
        )
        about.set_logo_icon_name("folder-publicshare")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.run()
        about.destroy()
