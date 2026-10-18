#!/usr/bin/env python3
"""
Bluetooth Device Control - Qt interface (Windows / macOS / Linux)

Keep this file in the same folder as bluetooth_re_pairing.py (the core).

    pip install PySide6          (PyQt6 also works)
    pythonw bt_device_control_gui.py          (Windows, no console window)
    python  bt_device_control_gui.py          (Linux / macOS)

Options
    --version     print the version and exit
    --autostart   start hidden in the tray only (no window). This is the
                  command the "Start at login" option registers.

Automatic re-pairing is a separate option ("Re-pair all saved devices
automatically when the program starts"). When it is checked, the program
re-pairs every saved device each time it starts (manual start or login).
When it is not checked, nothing is paired until you ask for it.
"""

import argparse
import json
import os
import platform
import plistlib
import re
import shlex
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.0.0"
APP_TITLE = "Bluetooth Device Control"


def build_parser():
    ap = argparse.ArgumentParser(description=APP_TITLE)
    ap.add_argument("--autostart", action="store_true",
                    help="start hidden in the tray (used by Start at login)")
    ap.add_argument("--version", action="version",
                    version=f"{APP_TITLE} {__version__}")
    return ap


# --help / --version must work without a display and without the system
# libraries Qt needs (CI runners, minimal containers), so they are handled
# here, BEFORE Qt is imported.
if __name__ == "__main__" and {"-h", "--help", "--version"} & set(sys.argv[1:]):
    build_parser().parse_known_args()

try:
    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtGui import (QAction, QColor, QIcon, QPainter, QPainterPath,
                               QPen, QPixmap, QTextCursor)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QCheckBox, QGroupBox, QHBoxLayout,
        QHeaderView, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
        QProgressBar, QPushButton, QSystemTrayIcon, QTableWidget,
        QTableWidgetItem, QVBoxLayout, QWidget)
except ImportError as err_pyside:
    try:
        from PyQt6.QtCore import QObject, Qt, QTimer
        from PyQt6.QtCore import pyqtSignal as Signal
        from PyQt6.QtGui import (QAction, QColor, QIcon, QPainter, QPainterPath,
                                 QPen, QPixmap, QTextCursor)
        from PyQt6.QtNetwork import QLocalServer, QLocalSocket
        from PyQt6.QtWidgets import (
            QAbstractItemView, QApplication, QCheckBox, QGroupBox, QHBoxLayout,
            QHeaderView, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
            QProgressBar, QPushButton, QSystemTrayIcon, QTableWidget,
            QTableWidgetItem, QVBoxLayout, QWidget)
    except ImportError as err_pyqt:
        # Show WHY the import failed: a missing system library (libEGL, libGL,
        # libxkbcommon ...) looks exactly like a missing package otherwise.
        sys.exit("Qt for Python could not be loaded.\n"
                 f"  PySide6: {err_pyside}\n"
                 f"  PyQt6  : {err_pyqt}\n"
                 "If a package is missing:  pip install PySide6\n"
                 "If a library (.so) is missing, install it with your package manager.")

try:
    import bluetooth_re_pairing as core
except ImportError:
    sys.exit("bluetooth_re_pairing.py must be in the same folder as this file.")

AUTOSTART_NAME = "BTDeviceControl"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_DELAY_S = 20      # let Windows/Bluetooth settle after login
MANUAL_START_DELAY_S = 2    # delay when the program is opened by hand
CONNECT_CHECKS = 3          # tries before deciding a device is not connected
CONNECT_GAP_S = 4
SERVER_NAME = "bt_device_control_single_instance"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def settings_path():
    return os.path.join(os.path.dirname(core.config_path()), "settings.json")


def load_settings():
    settings = {"skip_connected": True, "auto_repair": False}
    try:
        with open(settings_path(), encoding="utf-8") as f:
            settings.update(json.load(f))
    except (OSError, json.JSONDecodeError):
        pass
    return settings


def save_settings(settings):
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# --------------------------------------------------------------------------
# Autostart (Windows registry / Linux .desktop / macOS LaunchAgent)
# --------------------------------------------------------------------------
def launch_command():
    """Command line used to start this program at login."""
    appimage = os.environ.get("APPIMAGE")           # running inside an AppImage
    if appimage:                                    # sys.executable is a temp mount
        return [appimage, "--autostart"]
    if getattr(sys, "frozen", False):               # PyInstaller build
        return [sys.executable, "--autostart"]
    exe = sys.executable
    if platform.system() == "Windows":              # avoid a console window
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pyw):
            exe = pyw
    return [exe, os.path.abspath(__file__), "--autostart"]


def autostart_file():
    system = platform.system()
    if system == "Linux":
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return os.path.join(base, "autostart", "bt-device-control.desktop")
    if system == "Darwin":
        return os.path.expanduser("~/Library/LaunchAgents/com.btdevicecontrol.plist")
    return None


def autostart_enabled():
    if platform.system() == "Windows":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
        except OSError:
            return False
    return os.path.exists(autostart_file())


def set_autostart(enabled):
    cmd = launch_command()
    if platform.system() == "Windows":
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                                  subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return

    path = autostart_file()
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if platform.system() == "Darwin":
        with open(path, "wb") as f:
            plistlib.dump({"Label": "com.btdevicecontrol",
                           "ProgramArguments": cmd,
                           "RunAtLoad": True}, f)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Desktop Entry]\n"
                    "Type=Application\n"
                    f"Name={APP_TITLE}\n"
                    f"Exec={shlex.join(cmd)}\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n")


# --------------------------------------------------------------------------
# Plumbing: stdout -> log window, and a single worker thread for Bluetooth
# --------------------------------------------------------------------------
class SignalStream(QObject):
    """File-like object: everything the core prints ends up in the log view."""
    text = Signal(str)

    def write(self, s):
        if s:
            self.text.emit(s)
        return len(s or "")

    def flush(self):
        pass


class Controller(QObject):
    """Runs Bluetooth work on ONE dedicated thread (WinRT objects stay there)."""
    scanned = Signal(list)
    busy_changed = Signal(bool, str)
    saved_changed = Signal()
    message = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bt")
        self._be = None
        self.busy = False

    # ---- called from the GUI thread
    def scan(self):
        return self._submit("Scanning ...", self._do_scan)

    def repair(self, mac, name):
        return self._submit(f"Re-pairing {name} ...", self._do_repair, mac, name)

    def service(self, skip_connected):
        return self._submit("Re-pairing saved devices ...", self._do_service,
                            skip_connected)

    def shutdown(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _submit(self, label, fn, *args):
        if self.busy:
            return False
        self.busy = True
        self.busy_changed.emit(True, label)
        self._pool.submit(self._run, fn, *args)
        return True

    # ---- everything below runs on the worker thread
    def _run(self, fn, *args):
        try:
            fn(*args)
        except SystemExit as e:
            print(f"[x] {e}")
        except Exception:
            traceback.print_exc()
        finally:
            self.busy = False
            self.busy_changed.emit(False, "")

    def _backend(self):
        if self._be is None:
            be = core.get_backend()
            be.prepare()
            be.open_settings_on_failure = False
            self._be = be
        return self._be

    def _do_scan(self):
        be = self._backend()
        print(f"[*] Scanning for {core.SCAN_TIME}s ... "
              "(new devices must be in pairing mode)")
        devices = be.list_devices(core.SCAN_TIME)
        for d in devices:
            d["name"] = core.clean_name(d["name"])
        devices = [d for d in devices if re.search(r"\w", d["name"])]
        devices.sort(key=lambda d: (not d["paired"], d["name"].lower()))
        print(f"[+] {len(devices)} device(s) found.")
        self.scanned.emit(devices)

    def _do_repair(self, mac, name):
        be = self._backend()
        ok = core.repair_device(be, mac, name)
        if ok:
            core.save_device(mac, name)
            self.saved_changed.emit()
        self.message.emit(APP_TITLE,
                          f"{name}: paired successfully." if ok
                          else f"{name}: pairing failed.")

    def _connected_with_grace(self, be, mac):
        """A device may need a few seconds to connect after login."""
        state = None
        for n in range(CONNECT_CHECKS):
            state = be.is_connected(mac)
            if state is True:
                return True
            if n < CONNECT_CHECKS - 1:
                time.sleep(CONNECT_GAP_S)
        print(f"    (connection state: {state})")
        return False

    def _do_service(self, skip_connected):
        be = self._backend()
        devices = core.load_devices()
        if not devices:
            print(f"[x] No saved devices in {core.config_path()}.")
            self.message.emit(APP_TITLE,
                              "No saved devices yet. Re-pair a device once to save it.")
            return

        results = []
        for d in devices:
            mac = d["mac"]
            name = d.get("name") or mac
            if skip_connected:
                print(f"[*] Checking whether {name} is connected ...")
                if self._connected_with_grace(be, mac):
                    print(f"[=] {name} is already connected; skipping.")
                    results.append((name, "already connected"))
                    continue
            self.message.emit(APP_TITLE, f"Put {name} in pairing mode now.")
            ok = core.repair_device(be, mac, name)
            if ok:
                core.save_device(mac, name)
                self.saved_changed.emit()
            results.append((name, "OK" if ok else "FAILED"))

        summary = "\n".join(f"{n}: {r}" for n, r in results)
        print("\nSummary:\n" + "\n".join(f"  {n}: {r}" for n, r in results))
        self.message.emit(APP_TITLE, summary)


# --------------------------------------------------------------------------
# Window + tray
# --------------------------------------------------------------------------
def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1976d2"))
    p.drawEllipse(2, 2, 60, 60)
    pen = QPen(QColor("white"))
    pen.setWidth(5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()                      # the Bluetooth rune
    path.moveTo(23, 23)
    for x, y in ((41, 41), (32, 50), (32, 14), (41, 23), (23, 41)):
        path.lineTo(x, y)
    p.drawPath(path)
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self, ctl, settings):
        super().__init__()
        self.ctl = ctl
        self.settings = settings
        self._quitting = False
        self._hint_shown = False
        self.icon = make_icon()
        self.setWindowIcon(self.icon)
        self.setWindowTitle(APP_TITLE)
        self.resize(780, 720)

        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        # --- nearby devices
        box = QGroupBox("Nearby devices")
        lay = QVBoxLayout(box)
        self.nearby = self._make_table(["Name", "Address", "State"])
        lay.addWidget(self.nearby)
        row = QHBoxLayout()
        self.btn_scan = QPushButton(f"Scan ({core.SCAN_TIME}s)")
        self.btn_pair = QPushButton("Re-pair selected")
        row.addWidget(self.btn_scan)
        row.addWidget(self.btn_pair)
        row.addStretch()
        lay.addLayout(row)
        root.addWidget(box)

        # --- saved devices
        box = QGroupBox("Saved devices")
        lay = QVBoxLayout(box)
        self.saved = self._make_table(["Name", "Address", "Last paired"])
        lay.addWidget(self.saved)
        row = QHBoxLayout()
        self.btn_repair_saved = QPushButton("Re-pair selected")
        self.btn_forget = QPushButton("Forget")
        self.btn_service = QPushButton("Re-pair all saved now")
        row.addWidget(self.btn_repair_saved)
        row.addWidget(self.btn_forget)
        row.addStretch()
        row.addWidget(self.btn_service)
        lay.addLayout(row)
        root.addWidget(box)

        # --- options
        self.chk_autostart = QCheckBox(
            "Start automatically at login (hidden in the tray)")
        self.chk_auto_repair = QCheckBox(
            "Re-pair all saved devices automatically when the program starts")
        self.chk_auto_repair.setChecked(bool(settings.get("auto_repair", False)))
        self.chk_skip = QCheckBox(
            "Skip saved devices that are already connected (recommended)")
        self.chk_skip.setChecked(bool(settings.get("skip_connected", True)))
        root.addWidget(self.chk_autostart)
        root.addWidget(self.chk_auto_repair)
        root.addWidget(self.chk_skip)

        # --- log
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setPlaceholderText("Log")
        root.addWidget(self.log, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)               # indeterminate
        self.progress.setMaximumWidth(140)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

        self._action_buttons = [self.btn_scan, self.btn_pair, self.btn_repair_saved,
                                self.btn_forget, self.btn_service]

        # --- tray
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.setToolTip(APP_TITLE)
            menu = QMenu()
            act_open = QAction("Open", menu)
            act_service = QAction("Re-pair saved devices now", menu)
            self.act_autostart = QAction("Start at login", menu)
            self.act_autostart.setCheckable(True)
            act_quit = QAction("Quit", menu)
            for a in (act_open, act_service):
                menu.addAction(a)
            menu.addSeparator()
            menu.addAction(self.act_autostart)
            menu.addSeparator()
            menu.addAction(act_quit)
            self.tray.setContextMenu(menu)
            self._tray_menu = menu                 # keep a reference
            act_open.triggered.connect(self.show_normal)
            act_service.triggered.connect(self.run_service)
            self.act_autostart.toggled.connect(self.on_autostart)
            act_quit.triggered.connect(self.quit_app)
            self.tray.activated.connect(self.on_tray_activated)
            self.tray.show()

        # --- wiring
        self.btn_scan.clicked.connect(lambda *_: self.ctl.scan())
        self.btn_pair.clicked.connect(lambda: self.on_repair(self.nearby))
        self.btn_repair_saved.clicked.connect(lambda: self.on_repair(self.saved))
        self.btn_forget.clicked.connect(self.on_forget)
        self.btn_service.clicked.connect(self.run_service)
        self.chk_autostart.toggled.connect(self.on_autostart)
        self.chk_skip.toggled.connect(self.on_skip_toggled)
        self.chk_auto_repair.toggled.connect(self.on_auto_repair_toggled)
        ctl.scanned.connect(self.populate_nearby)
        ctl.busy_changed.connect(self.set_busy)
        ctl.saved_changed.connect(self.refresh_saved)
        ctl.message.connect(self.notify)

        self._sync_autostart_widgets(self._safe_autostart_state())
        self.refresh_saved()

    # ---- helpers
    @staticmethod
    def _make_table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        t.setMinimumHeight(120)
        return t

    @staticmethod
    def _fill(table, rows):
        table.setRowCount(len(rows))
        for r, (name, mac, extra) in enumerate(rows):
            first = QTableWidgetItem(name)
            first.setData(Qt.ItemDataRole.UserRole, mac)
            table.setItem(r, 0, first)
            table.setItem(r, 1, QTableWidgetItem(core.fmt_mac(mac)))
            table.setItem(r, 2, QTableWidgetItem(extra))

    @staticmethod
    def _selected(table):
        r = table.currentRow()
        if r < 0:
            return None
        item = table.item(r, 0)
        return item.data(Qt.ItemDataRole.UserRole), item.text()

    def _ask(self, title, text):
        answer = QMessageBox.question(self, title, text)
        return answer == QMessageBox.StandardButton.Yes

    # ---- slots
    def append_log(self, text):
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def populate_nearby(self, devices):
        self._fill(self.nearby, [(d["name"], d["mac"],
                                  "paired" if d["paired"] else "new")
                                 for d in devices])

    def refresh_saved(self):
        rows = []
        for d in core.load_devices():
            when = (d.get("last_paired") or "").replace("T", " ")
            rows.append((d.get("name") or d["mac"], d["mac"], when))
        self._fill(self.saved, rows)

    def set_busy(self, busy, label):
        for b in self._action_buttons:
            b.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.statusBar().showMessage(label if busy else "Ready")

    def notify(self, title, text):
        if self.tray:
            self.tray.showMessage(title, text,
                                  QSystemTrayIcon.MessageIcon.Information, 8000)
        self.statusBar().showMessage(text.splitlines()[0] if text else "", 8000)

    def on_repair(self, table):
        sel = self._selected(table)
        if not sel:
            QMessageBox.information(self, APP_TITLE, "Select a device first.")
            return
        mac, name = sel
        if self._ask(APP_TITLE,
                     f"Remove and re-pair \"{name}\"?\n\n"
                     "Put the device in pairing mode right after clicking Yes "
                     "(hold its pairing/Connect button until the LED blinks fast)."):
            self.ctl.repair(mac, name)

    def on_forget(self, *_):
        sel = self._selected(self.saved)
        if not sel:
            QMessageBox.information(self, APP_TITLE, "Select a saved device first.")
            return
        mac, name = sel
        if self._ask(APP_TITLE, f"Forget \"{name}\"?\n\n"
                                "It stays paired in the system, but the service "
                                "will no longer handle it."):
            core.forget_device(mac)
            self.refresh_saved()

    def run_service(self, *_):
        self.ctl.service(self.chk_skip.isChecked())

    def _save_setting(self, key, value):
        self.settings[key] = bool(value)
        try:
            save_settings(self.settings)
        except OSError as e:
            print(f"[!] Could not save settings: {e}")

    def on_skip_toggled(self, checked):
        self._save_setting("skip_connected", checked)

    def on_auto_repair_toggled(self, checked):
        self._save_setting("auto_repair", checked)
        print(f"[*] Automatic re-pairing at startup: "
              f"{'enabled' if checked else 'disabled'}")

    # ---- autostart
    @staticmethod
    def _safe_autostart_state():
        try:
            return autostart_enabled()
        except Exception:
            return False

    def _sync_autostart_widgets(self, state):
        widgets = [self.chk_autostart]
        if self.tray:
            widgets.append(self.act_autostart)
        for w in widgets:
            w.blockSignals(True)
            w.setChecked(state)
            w.blockSignals(False)

    def on_autostart(self, enabled):
        try:
            set_autostart(enabled)
            print(f"[*] Start at login: {'enabled' if enabled else 'disabled'}")
        except Exception as e:
            print(f"[x] Could not change autostart: {e}")
            QMessageBox.warning(self, APP_TITLE, f"Could not change autostart:\n{e}")
        self._sync_autostart_widgets(self._safe_autostart_state())

    # ---- window / tray behaviour
    def show_normal(self, *_):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_normal()

    def closeEvent(self, event):
        if self.tray and not self._quitting:
            event.ignore()
            self.hide()
            if not self._hint_shown:
                self._hint_shown = True
                self.notify(APP_TITLE, "Still running in the tray. "
                                       "Use the tray menu to quit.")
        else:
            event.accept()

    def quit_app(self, *_):
        if self.ctl.busy and not self._ask(
                APP_TITLE, "An operation is still running. Quit anyway?"):
            return
        self._quitting = True
        if self.tray:
            self.tray.hide()
        QApplication.quit()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main():
    args, qt_args = build_parser().parse_known_args()

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)

    # Single instance: a second launch just opens the first one's window
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        if not args.autostart:
            sock.write(b"show")
            sock.flush()
            sock.waitForBytesWritten(500)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)

    stream = SignalStream()
    sys.stdout = sys.stderr = stream               # pythonw has no stdout at all

    ctl = Controller()
    window = MainWindow(ctl, load_settings())
    stream.text.connect(window.append_log)
    server.newConnection.connect(
        lambda: (server.nextPendingConnection(), window.show_normal()))

    print(f"[*] Platform: {platform.system()}")
    print(f"[*] Saved devices file: {core.config_path()}")

    if window.settings.get("auto_repair"):
        delay = AUTOSTART_DELAY_S if args.autostart else MANUAL_START_DELAY_S
        print(f"[*] Automatic re-pairing is ON: starting in {delay}s ...")
        QTimer.singleShot(delay * 1000, window.run_service)
    else:
        print("[*] Automatic re-pairing is OFF: use the buttons to re-pair manually.")
    if not (args.autostart and window.tray):
        window.show()

    code = app.exec()
    ctl.shutdown()
    os._exit(code)          # do not wait for a Bluetooth operation in progress


if __name__ == "__main__":
    sys.exit(main())
