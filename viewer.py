import os
import sys
import re
import math
import datetime
from statistics import mean
from urllib.parse import urlparse

import requests
from qtpy import QtWidgets, QtCore, QtGui

# =========================
# 基础设置 & 工具
# =========================

APP_NAME = "ServerMonitor"
ORG_NAME = "LocalTools"


def today_date():
    try:
        return datetime.date.today()
    except Exception:
        r = requests.get("https://worldtimeapi.org/api/ip", timeout=3)
        return datetime.datetime.fromisoformat(r.json()["datetime"]).date()


# =========================
# 设置管理（QSettings）
# =========================

class SettingsManager(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.qs = QtCore.QSettings(ORG_NAME, APP_NAME)
        self.vf_url = self.qs.value("vf_url", "", str)
        self.theme = self.qs.value("theme", "auto", str)

    def normalize_url(self, text: str):
        text = text.strip()
        if not text:
            return "", False

        if not re.match(r"^https?://", text):
            text = "https://" + text

        try:
            p = urlparse(text)
            if not p.netloc:
                return "", False
            clean = f"{p.scheme}://{p.netloc}"
            return clean, clean != text
        except Exception:
            return "", False

    def save(self, url, theme):
        self.vf_url = url
        self.theme = theme
        self.qs.setValue("vf_url", url)
        self.qs.setValue("theme", theme)
        self.changed.emit()


# =========================
# 日志管理 + 24h 补齐
# =========================

class LogManager:
    BASE = "./logs"

    @staticmethod
    def servers():
        if not os.path.isdir(LogManager.BASE):
            return []
        return sorted(
            [d for d in os.listdir(LogManager.BASE) if d.isdigit()],
            key=lambda x: int(x)
        )

    @staticmethod
    def dates(server_id):
        p = os.path.join(LogManager.BASE, server_id)
        if not os.path.isdir(p):
            return []
        return sorted(f[:-4] for f in os.listdir(p) if f.endswith(".log"))

    @staticmethod
    def read(server_id, date_str):
        path = os.path.join(LogManager.BASE, server_id, f"{date_str}.log")
        if not os.path.isfile(path):
            return []

        out = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts, val = line.split()
                    out.append((datetime.datetime.fromisoformat(ts), float(val)))
                except Exception:
                    pass
        return out

    @staticmethod
    def read_last_24h(server_id):
        now = datetime.datetime.now()
        today = now.date()
        yesterday = today - datetime.timedelta(days=1)

        data = []
        for d in (yesterday, today):
            data.extend(LogManager.read(server_id, d.isoformat()))

        cutoff = now - datetime.timedelta(hours=24, minutes=5)
        return [(t, v) for t, v in data if t >= cutoff and t <= now]


# =========================
# 仪表盘（油门表）
# =========================

class GaugeWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.value = 0

    def set_value(self, v):
        self.value = max(0, min(100, v))
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        rect = self.rect().adjusted(10, 10, -10, -10)
        start_angle = 225 * 16
        span = int(-270 * 16 * (self.value / 100))

        if self.value < 50:
            color = QtGui.QColor("#3cb371")
        elif self.value < 85:
            color = QtGui.QColor("#f0ad4e")
        else:
            color = QtGui.QColor("#d9534f")

        pen_bg = QtGui.QPen(QtGui.QColor("#333"), 12)
        pen_fg = QtGui.QPen(color, 12)

        p.setPen(pen_bg)
        p.drawArc(rect, 225 * 16, -270 * 16)

        p.setPen(pen_fg)
        p.drawArc(rect, start_angle, span)

        p.setPen(QtGui.QColor("white"))
        f = p.font()
        f.setPointSize(14)
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), QtCore.Qt.AlignCenter, f"{int(self.value)}%")


# =========================
# 服务器卡片
# =========================

class ServerCard(QtWidgets.QFrame):
    def __init__(self, server_id, settings: SettingsManager):
        super().__init__()
        self.server_id = server_id
        self.settings = settings

        self.setFrameShape(QtWidgets.QFrame.StyledPanel)

        layout = QtWidgets.QHBoxLayout(self)

        self.gauge = GaugeWidget()
        self.gauge.setFixedSize(120, 120)

        right = QtWidgets.QVBoxLayout()
        self.title = QtWidgets.QLabel(f"# {server_id}")
        self.stats = QtWidgets.QLabel("")

        self.title.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.title.mousePressEvent = self.open_panel

        right.addWidget(self.title)
        right.addWidget(self.stats)
        right.addStretch()

        layout.addWidget(self.gauge)
        layout.addLayout(right)

        self.refresh()

    def open_panel(self, _):
        if not self.settings.vf_url:
            return
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(f"{self.settings.vf_url}/admin/servers/{self.server_id}")
        )

    def refresh(self):
        data = LogManager.read(self.server_id, today_date().isoformat())
        if not data:
            self.stats.setText("Error")
            self.gauge.set_value(0)
            return

        values = [v for _, v in data]
        self.gauge.set_value(values[-1])
        self.stats.setText(
            f"Max {max(values):.1f}%\n"
            f"Min {min(values):.1f}%\n"
            f"Avg {mean(values):.1f}%"
        )


# =========================
# 页面：服务器
# =========================

class ServerPage(QtWidgets.QScrollArea):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.container = QtWidgets.QWidget()
        self.setWidget(self.container)
        self.setWidgetResizable(True)

        self.grid = QtWidgets.QGridLayout(self.container)
        self.cards = {}

        self.load()

    def load(self):
        servers = LogManager.servers()
        for i, sid in enumerate(servers):
            card = ServerCard(sid, self.settings)
            self.cards[sid] = card
            self.grid.addWidget(card, i // 2, i % 2)


# =========================
# 页面：历史
# =========================

class HistoryPage(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QHBoxLayout()
        self.server_label = QtWidgets.QLabel("Server")
        self.date_btn = QtWidgets.QPushButton("选择日期")
        top.addWidget(self.server_label)
        top.addStretch()
        top.addWidget(self.date_btn)

        layout.addLayout(top)
        self.info = QtWidgets.QLabel("")
        layout.addWidget(self.info)

        self.list = QtWidgets.QListWidget()
        for sid in LogManager.servers():
            self.list.addItem(sid)

        layout.addWidget(self.list)

        self.list.currentTextChanged.connect(self.load)

    def load(self, sid):
        self.server_label.setText(f"# {sid}  {today_date().isoformat()}")

        data = LogManager.read(sid, today_date().isoformat())
        if not data:
            self.info.setText("No data")
            return

        values = [v for _, v in data]
        text = (
            f"[Today] "
            f"Max {max(values):.1f}%  "
            f"Min {min(values):.1f}%  "
            f"Avg {mean(values):.1f}%"
        )

        data24 = LogManager.read_last_24h(sid)
        if data24:
            v24 = [v for _, v in data24]
            text += (
                f"\n[24h] "
                f"Max {max(v24):.1f}%  "
                f"Min {min(v24):.1f}%  "
                f"Avg {mean(v24):.1f}%"
            )

        self.info.setText(text)


# =========================
# 页面：设置
# =========================

class SettingsPage(QtWidgets.QWidget):
    def __init__(self, settings: SettingsManager):
        super().__init__()
        self.settings = settings

        layout = QtWidgets.QFormLayout(self)
        self.url = QtWidgets.QLineEdit(settings.vf_url)
        self.url.setPlaceholderText("https://serv.example.com")

        self.theme = QtWidgets.QComboBox()
        self.theme.addItems(["auto", "dark", "light"])
        self.theme.setCurrentText(settings.theme)

        save = QtWidgets.QPushButton("保存")

        layout.addRow("VirtFusion URL", self.url)
        layout.addRow("主题", self.theme)
        layout.addRow(save)

        save.clicked.connect(self.save)

    def save(self):
        clean, _ = self.settings.normalize_url(self.url.text())
        if not clean:
            QtWidgets.QMessageBox.warning(self, "错误", "非法 URL")
            return
        self.url.setText(clean)
        self.settings.save(clean, self.theme.currentText())


# =========================
# 主窗口
# =========================

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = SettingsManager()
        self.setWindowTitle("Server Monitor")

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(ServerPage(self.settings), "服务器")
        tabs.addTab(HistoryPage(), "历史")
        tabs.addTab(SettingsPage(self.settings), "设置")

        self.setCentralWidget(tabs)
        self.resize(1100, 720)


# =========================
# 启动
# =========================

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
