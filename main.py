#!/usr/bin/env python3
import socket
import sys
import threading
import time
from typing import override

import pygetwindow as gw
from PyQt6.QtCore import QEvent, QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget

import csp.picker_server as srv
from csp.config import config

# ── Constants ─────────────────────────────────────────────────────────────────
RESIZE_MARGIN = 8  # px — edge/corner resize hit zone
DRAG_BAR_H = 22  # px — invisible drag strip at top
SERVER_PORT = config.port
SERVER_URL = f"http://{config.host}:{SERVER_PORT}/"

# ── Geometry helpers ──────────────────────────────────────────────────────────
def _get_edges(pos, win_w, win_h, margin):
    """Return Qt.Edges for the window edge(s) nearest to pos."""
    x, y = pos.x(), pos.y()
    edges = Qt.Edge(0)
    if x < margin:
        edges |= Qt.Edge.LeftEdge
    if x > win_w - margin:
        edges |= Qt.Edge.RightEdge
    if y < margin:
        edges |= Qt.Edge.TopEdge
    if y > win_h - margin:
        edges |= Qt.Edge.BottomEdge
    return edges


def _cursor_for_edges(edges):
    """Map a Qt.Edges combination to the appropriate resize cursor."""
    L, R, T, B = (Qt.Edge.LeftEdge, Qt.Edge.RightEdge, Qt.Edge.TopEdge, Qt.Edge.BottomEdge)
    has = lambda e: bool(edges & e)  # noqa: E731
    horiz = has(L) or has(R)
    vert = has(T) or has(B)

    if horiz and vert:
        if (has(T) and has(L)) or (has(B) and has(R)):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor
    if horiz:
        return Qt.CursorShape.SizeHorCursor
    if vert:
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor


# ── DragBar ───────────────────────────────────────────────────────────────────
class DragBar(QWidget):  # Strip at top to allow dragging
    def __init__(self, parent: QMainWindow):
        super().__init__(parent)
        self.setFixedHeight(DRAG_BAR_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    @override
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle:
                handle.startSystemMove()


# ── PickerWindow ──────────────────────────────────────────────────────────────
class FocusWorker(QObject):
    focus_changed = pyqtSignal(bool)

    def run(self):
        prev_focus_state = None
        while True:
            active_window = gw.getActiveWindow()
            focus_state = False
            if active_window:
                if not active_window.title:
                    focus_state = prev_focus_state
                else:
                    focus_state = (
                        active_window.title.strip().upper() == "CLIP STUDIO PAINT"
                        or active_window.title == "Connect to smartphone"
                        or active_window.title.startswith((config.window_title, "Javascript"))
                    )  # Also allow color picker

            if prev_focus_state != focus_state:
                self.focus_changed.emit(focus_state)
                prev_focus_state = focus_state

            time.sleep(0.25)


class PickerWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(config.window_title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(160, 120)
        self.resize(config.win_size[0] if config.win_size is not None else 396, config.win_size[1] if config.win_size is not None else 400)
        if config.win_pos is not None:
            self.move(config.win_pos[0], config.win_pos[1])
        self.setStyleSheet("background-color: #16161a;")  # matches var(--surface)

        icon_path = config.icon_path
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # ── Page ───────────────────────────────────────────────────────────
        storage_path = str(config.web_data_path.absolute())
        profile = QWebEngineProfile("AppData", self)
        profile.setPersistentStoragePath(storage_path)
        profile.setCachePath(storage_path)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)

        page = QWebEnginePage(profile, self)
        settings = page.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)

        self._web = QWebEngineView(self)
        self._web.page().quotaRequested.connect(lambda request: request.accept())
        self._web.setContentsMargins(0, 0, 0, 0)
        self._web.setPage(page)

        self.setCentralWidget(self._web)
        self.setContentsMargins(0, 0, 5, 5)

        self._drag_bar = DragBar(self)
        self._drag_bar.raise_()

        self._web.loadFinished.connect(self._on_load_finished)
        self._web.load(QUrl(SERVER_URL))

        self.start_win_config_save_timer()
        if config.auto_hide:
            self.start_autohide_focus_listener()

    def start_autohide_focus_listener(self):
        self.thread = QThread()
        self.worker = FocusWorker()
        self.worker.moveToThread(self.thread)
        self.worker.focus_changed.connect(self._autoshow_from_csp_focus)
        self.thread.started.connect(self.worker.run)
        self.thread.start()

    def start_win_config_save_timer(self):
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self._save_win_stats)
        self.stats_timer.start(30000)

    def _autoshow_from_csp_focus(self, is_focused):
        if is_focused:
            self.showNormal()
        else:
            self.showMinimized()

    def _save_win_stats(self):
        pos = self.pos()
        size = self.size()
        config.save_window_position(pos.x(), pos.y(), size.width(), size.height())

    # ── Layout ────────────────────────────────────────────────────────────────
    @override
    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        rect = self.rect()

        on_right = pos.x() >= rect.width() - RESIZE_MARGIN
        on_bottom = pos.y() >= rect.height() - RESIZE_MARGIN

        # Update cursor shape to give user feedback
        if on_right and on_bottom:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif on_right:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif on_bottom:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    @override
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            if pos.x() >= self.width() - RESIZE_MARGIN or pos.y() >= self.height() - RESIZE_MARGIN:
                self.windowHandle().startSystemResize(_get_edges(pos, self.width(), self.height(), RESIZE_MARGIN))
        super().mousePressEvent(event)

    @override
    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Keep drag bar flush with the top edge, full width
        self._drag_bar.setGeometry(0, 0, self.width(), DRAG_BAR_H)

    # ── Event filter  (resize edge detection on viewport) ─────────────────────
    @override
    def eventFilter(self, obj, event):
        if obj is not self._web.viewport():
            return False

        t = event.type()
        if t == QEvent.Type.MouseMove:
            pos = event.position().toPoint()
            edges = _get_edges(pos, self.width(), self.height(), RESIZE_MARGIN)
            self._web.viewport().setCursor(_cursor_for_edges(edges) if edges else Qt.CursorShape.ArrowCursor)

        elif t == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                edges = _get_edges(pos, self.width(), self.height(), RESIZE_MARGIN)
                if edges:
                    handle = self.windowHandle()
                    if handle:
                        handle.startSystemResize(edges)
                    return True  # consume event — do not pass to web content
        return False

    # ── JS injection ──────────────────────────────────────────────────────────
    def _on_load_finished(self, ok: bool):
        if ok:
            self._web.page().runJavaScript(f"WS_URL = 'ws://{config.host}:{SERVER_PORT}/ws';")
        else:
            print("[picker] page failed to load — retrying in 2s")
            QTimer.singleShot(2000, lambda: self._web.load(QUrl(SERVER_URL)))


# ── Server bootstrap ─────────────────────────────────────────────────────────
def _wait_for_server(port: int, timeout: float = 10.0) -> bool:
    """Spin until the TCP port accepts connections (or timeout)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:  # noqa: PERF203
            time.sleep(0.1)
    return False


def _start_server():
    """Target for the background thread — blocks forever."""
    srv.start(port=SERVER_PORT)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    server_thread = threading.Thread(target=_start_server, name="uvicorn", daemon=True)
    server_thread.start()
    print(f"[main] waiting for server on port {SERVER_PORT} …")

    if not _wait_for_server(SERVER_PORT):
        print("[main] ERROR: server did not start in time", file=sys.stderr)
        sys.exit(1)

    print(f"[main] server ready  →  {SERVER_URL}")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = PickerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
