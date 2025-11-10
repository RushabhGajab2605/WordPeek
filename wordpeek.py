# wordpeek_packaged.py
"""
WordPeek packaged-friendly script.
Save as wordpeek.py before packaging with PyInstaller.
Dependencies:
 pip install PyQt5 keyboard pyautogui pyperclip requests pillow
Notes:
 - Global hotkeys may need accessibility/permission settings on macOS.
 - Build an .ico (Windows) or .icns (macOS) for the tray icon and pass via --add-data
"""

import sys
import threading
import time
import platform
import requests
import pyperclip
import pyautogui
import os

from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtCore import pyqtSignal

# Hotkey config
HOTKEY_WINDOWS_LINUX = "ctrl+shift+d"
HOTKEY_MAC = "command+shift+d"
COPY_MODIFIER = "ctrl" if platform.system() != "Darwin" else "command"

# Dictionary API (free example)
DICTIONARY_API = "https://api.dictionaryapi.dev/api/v2/entries/en/"

# Helper for PyInstaller resource path
def resource_path(relative_path):
    """Return path to resource, works for dev and for PyInstaller bundle."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ---------- UI components ----------
class LookupDialog(QtWidgets.QDialog):
    def __init__(self, selection_text):
        super().__init__()
        self.selection_text = selection_text
        self.setWindowTitle("Lookup word?")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.setFixedSize(420, 140)

        v = QtWidgets.QVBoxLayout()
        label = QtWidgets.QLabel(f"Look up meaning of:\n\n\"{selection_text}\"")
        label.setWordWrap(True)
        v.addWidget(label)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Yes | QtWidgets.QDialogButtonBox.No)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)
        self.setLayout(v)

class ResultWindow(QtWidgets.QWidget):
    """
    Shows the lookup result and provides two actions:
      - Continue reading: close this window and keep the app running.
      - Close app: quit the entire application.
    """
    def __init__(self, title, text, app_ref=None):
        super().__init__()
        self.app_ref = app_ref
        self.setWindowTitle(title)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.resize(520, 420)

        main_layout = QtWidgets.QVBoxLayout()

        txt = QtWidgets.QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(text)
        main_layout.addWidget(txt)

        # Buttons area
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)

        self.continue_btn = QtWidgets.QPushButton("Continue reading")
        self.close_app_btn = QtWidgets.QPushButton("Close app")

        btn_layout.addWidget(self.continue_btn)
        btn_layout.addWidget(self.close_app_btn)

        main_layout.addLayout(btn_layout)
        self.setLayout(main_layout)

        # Connections
        self.continue_btn.clicked.connect(self._on_continue)
        self.close_app_btn.clicked.connect(self._on_close_app)

    def _on_continue(self):
        """Close this result window only; the application keeps running."""
        self.close()

    def _on_close_app(self):
        """Quit the entire application immediately."""
        self.close()
        QtWidgets.QApplication.instance().quit()

# ---------- networking / API ----------
def lookup_word_api(word):
    """
    Query the dictionary API; return a nicely formatted string or None if not found.
    """
    url = DICTIONARY_API + requests.utils.quote(word)
    try:
        r = requests.get(url, timeout=6)
    except Exception as e:
        return f"Error contacting dictionary API: {e}"

    if r.status_code == 200:
        try:
            data = r.json()
            out_lines = []
            for entry in data:
                if "word" in entry:
                    out_lines.append(f"Word: {entry.get('word')}")
                if "phonetics" in entry and entry["phonetics"]:
                    ph = [p.get("text","") for p in entry["phonetics"] if p.get("text")]
                    if ph:
                        out_lines.append(f"Pronunciation: {', '.join(ph)}")
                if "meanings" in entry:
                    for meaning in entry["meanings"]:
                        part = meaning.get("partOfSpeech","")
                        out_lines.append(f"\nPart of speech: {part}")
                        for i, defn in enumerate(meaning.get("definitions", []), start=1):
                            d = defn.get("definition","")
                            ex = defn.get("example","")
                            out_lines.append(f"  {i}. {d}")
                            if ex:
                                out_lines.append(f"     e.g., {ex}")
            return "\n".join(out_lines) if out_lines else None
        except Exception as e:
            return f"Error parsing API response: {e}"
    elif r.status_code == 404:
        return None
    else:
        return f"Unexpected API response: {r.status_code}"

# ---------- Main application ----------
class DictHelperApp(QtWidgets.QApplication):
    request_lookup = pyqtSignal(str)
    lookup_result = pyqtSignal(str, object)  # (selection, result_text_or_None or error message)

    def __init__(self, argv):
        super().__init__(argv)

        # keep the app running even when no windows are open
        self.setQuitOnLastWindowClosed(False)

        self.open_windows = []

        # Tray icon
        icon_path = resource_path("icon.png")  # bundle icon.png next to exe or use an .ico/.icns for platform
        icon = None
        if os.path.exists(icon_path):
            try:
                icon = QtGui.QIcon(icon_path)
                if icon.isNull():
                    icon = None
            except Exception:
                icon = None

        if icon is None:
            icon = self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogInfoView)

        self.tray = QtWidgets.QSystemTrayIcon(icon)
        menu = QtWidgets.QMenu()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit)
        menu.addAction("Lookup history (not implemented)")
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("WordPeek - press hotkey to lookup selection")
        self.tray.show()

        # Connect signals
        self.request_lookup.connect(self._handle_lookup_request)
        self.lookup_result.connect(self._handle_lookup_result)

        # Register hotkey in background thread (keyboard library may need permissions)
        hk = HOTKEY_MAC if platform.system() == "Darwin" else HOTKEY_WINDOWS_LINUX

        def register_hotkey():
            try:
                import keyboard  # may require admin or accessibility permissions
                # register callback that starts a daemon thread to handle the hotkey action
                keyboard.add_hotkey(hk, lambda: threading.Thread(target=self._on_hotkey_press_worker, daemon=True).start())
                # DO NOT call keyboard.wait() here because Qt's event loop keeps the process alive
            except Exception as e:
                # Show error on the GUI thread
                QtCore.QTimer.singleShot(0, lambda: QtWidgets.QMessageBox.critical(None, "Hotkey Error", f"Failed to register global hotkey ({hk}): {e}"))

        thr = threading.Thread(target=register_hotkey, daemon=True)
        thr.start()

    def _on_hotkey_press_worker(self):
        """
        Runs in a worker thread started by the hotkey callback.
        It simulates copy, reads clipboard, and asks GUI to prompt the user.
        """
        try:
            try:
                pyautogui.hotkey(COPY_MODIFIER, "c")
            except Exception:
                pyautogui.keyDown(COPY_MODIFIER)
                pyautogui.press("c")
                pyautogui.keyUp(COPY_MODIFIER)
        except Exception:
            pass

        time.sleep(0.12)
        try:
            selection = pyperclip.paste()
        except Exception:
            selection = ""

        if not selection or not str(selection).strip():
            QtCore.QTimer.singleShot(0, lambda: QtWidgets.QMessageBox.information(None, "No selection", "No text was selected (or nothing copied). Select text and press the hotkey again."))
            return

        selection = str(selection).strip()
        self.request_lookup.emit(selection)

    def _handle_lookup_request(self, selection):
        dlg = LookupDialog(selection)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            loading = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, "Looking up", f"Looking up \"{selection}\"...", QtWidgets.QMessageBox.NoButton)
            loading.setWindowFlags(loading.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
            loading.show()
            QtWidgets.QApplication.processEvents()

            def bg_lookup():
                try:
                    result = lookup_word_api(selection)
                    self.lookup_result.emit(selection, result)
                except Exception as e:
                    self.lookup_result.emit(selection, f"__ERROR__:{e}")
                finally:
                    QtCore.QTimer.singleShot(0, loading.close)

            t = threading.Thread(target=bg_lookup, daemon=True)
            t.start()
        else:
            pass

    def _handle_lookup_result(self, selection, result):
        if isinstance(result, str) and result.startswith("__ERROR__:"):
            err = result.split(":", 1)[1]
            QtWidgets.QMessageBox.critical(None, "Lookup Error", f"Error while looking up \"{selection}\":\n{err}")
            return

        if result is None:
            QtWidgets.QMessageBox.information(None, "Not found", f"No such word exists: \"{selection}\"")
            return

        # Show result with Continue/Close-app options
        resw = ResultWindow(f"Meaning: {selection}", result, app_ref=self)
        resw.show()

        # keep a reference so it doesn't get garbage-collected and remove on close
        self.open_windows.append(resw)
        resw.destroyed.connect(lambda: self._remove_window_ref(resw))

    def _remove_window_ref(self, w):
        try:
            if w in self.open_windows:
                self.open_windows.remove(w)
        except Exception:
            pass

# ---------- Entrypoint ----------
def main():
    app = DictHelperApp(sys.argv)

    # Show a simple startup notification using the tray
    QtCore.QTimer.singleShot(500, lambda: app.tray.showMessage(
        "WordPeek",
        "WordPeek is now running.\nPress the hotkey to lookup selected text.",
        QtWidgets.QSystemTrayIcon.Information,
        5000  # duration in ms
    ))

    # No windows shown initially; app runs in background
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
