import os
import re
import sys
import math
import requests
import datetime
from statistics import mean
from urllib.parse import urlparse

from qtpy import QtWidgets, QtCore, QtGui

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure


# =========================
# 工具 & 管理类
# =========================

class SettingsManager(QtCore.QObject):
    settings_changed = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.vf_url = ""
        self.theme = "auto"  # auto | dark | light

    def normalize_url(self, text: str):
        text = text.strip()
        if not text:
            return "", False

        if not re.match(r"^https?://", text):
            text = "https://" + text

        try:
            parsed = urlparse(text)
            if not parsed.netloc:
                return "", False
            clean = f"{parsed.scheme}://{parsed.netloc}"
            return clean, clean != text
        except Exception:
            return "", False

    def save(self, url, theme):
        self.vf_url = url
        self.theme = theme
        self.settings_changed.emit()


class LogManager:
    BASE = "./logs"

    @staticmethod
    def get_today():
        try:
            return datetime.date.today()
        except Exception:
            r = requests.get("https://worldtimeapi.org/api/ip")
            ts = datetime.datetime.fromisoformat(r.json()["datetime"])
            return ts.date()

    @staticmethod
    def list_servers():
        if not os.path.isdir(LogManager.BASE):
            return []
        return sorted([d for d in os.listdir(LogManager.BASE) if d.isdigit()],
                      key=lambda x: int(x))

    @staticmethod
    def list_dates(server_id):
        path = os.path.join(LogManager.BASE, server_id)
        if not os.path.isdir(path):
            return []
        return sorted([f[:-4] for f in os.listdir(path) if f.endswith(".log")])

    @staticmethod
    def read_log(server_id, date_str):
        path = os.path.join(LogManager.BASE, server_id, f"{date_str}.log")
        if not os.path.isfile(path):
            return []

        data = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts, val = line.split()
                    data.append((datetime.datetime.fromisoformat(ts), float(val)))
                except Exception:
                    pass
        return data


# =========================
# 折线图
# =========================

class HistoryChart(FigureCanvasQTAgg):
    def __init__(self):
        self.fig = Figure(figsize=(6, 4), dpi=100)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)

    def draw_data(self, data):
        self.ax.clear()
        if not data:
            self.draw()
            return

        times = [t for t, _ in data]
        values = [v for _, v in data]

        def color_for(v):
            if v < 50:
                return "green"
            elif v < 85:
                return "orange"
            return "red"

        for i in range(len(values) - 1):
            x = times[i:i+2]
            y = values[i:i+2]
            self.ax.plot(x, y, color=color_for(mean(y)))

        self.ax.set_ylabel("CPU %")
        self.ax.grid(True, alpha=0.3)
        self.fig.autofmt_xdate()
        self.draw()


# =========================
# 页面：服务器
# =========================

class ServerPage(QtWidgets.QScrollArea):
    def __init__(self, settings: SettingsManager):
        super().__init__()
        self.settings = settings
        self.widget = QtWidgets.QWidget()
        self.setWidget(self.widget)
        self.setWidgetResizable(True)
        self.layout = QtWidgets.QGridLayout(self.widget)
        self.refresh()

    def refresh(self):
        while self.layout.count():
            self.layout.takeAt(0).widget().deleteLater()

        today = LogManager.get_today().isoformat()
        servers = LogManager.list_servers()

        for i, sid in enumerate(servers):
            data = LogManager.read_log(sid, today)
            if not data:
                text = f"# {sid}\nError"
            else:
                values = [v for _, v in data]
                text = (
                    f"# {sid}\n"
                    f"最新 {values[-1]:.1f}%\n"
                    f"avg {mean(values):.1f}%\n"
                    f"max {max(values):.1f}%\n"
                    f"min {min(values):.1f}%"
                )

            btn = QtWidgets.QPushButton(text)
            btn.clicked.connect(lambda _, x=sid: self.open_server(x))
            self.layout.addWidget(btn, i // 2, i % 2)

    def open_server(self, sid):
        if not self.settings.vf_url:
            return
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl(f"{self.settings.vf_url}/admin/servers/{sid}")
        )


# =========================
# 页面：历史
# =========================

class HistoryPage(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)

        self.list = QtWidgets.QListWidget()
        self.chart = HistoryChart()
        self.info = QtWidgets.QLabel()

        right = QtWidgets.QVBoxLayout()
        right.addWidget(self.chart)
        right.addWidget(self.info)

        layout.addWidget(self.list, 1)
        layout.addLayout(right, 4)

        for sid in LogManager.list_servers():
            self.list.addItem(sid)

        self.list.currentTextChanged.connect(self.load)

    def load(self, sid):
        today = LogManager.get_today().isoformat()
        data = LogManager.read_log(sid, today)
        self.chart.draw_data(data)

        if data:
            values = [v for _, v in data]
            self.info.setText(
                f"[Today] max {max(values):.1f}%  "
                f"min {min(values):.1f}%  "
                f"avg {mean(values):.1f}%"
            )
        else:
            self.info.setText("No data")


# =========================
# 页面：设置
# =========================

class SettingsPage(QtWidgets.QWidget):
    def __init__(self, settings: SettingsManager):
        super().__init__()
        self.settings = settings

        layout = QtWidgets.QFormLayout(self)

        self.url = QtWidgets.QLineEdit()
        self.url.setPlaceholderText("https://serv.example.com")

        self.theme = QtWidgets.QComboBox()
        self.theme.addItems(["auto", "dark", "light"])

        save = QtWidgets.QPushButton("保存")

        layout.addRow("VirtFusion URL", self.url)
        layout.addRow("主题", self.theme)
        layout.addRow(save)

        save.clicked.connect(self.save)

    def save(self):
        clean, changed = self.settings.normalize_url(self.url.text())
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
        self.setWindowTitle("Server Monitor")

        self.settings = SettingsManager()

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(ServerPage(self.settings), "服务器")
        tabs.addTab(HistoryPage(), "历史")
        tabs.addTab(SettingsPage(self.settings), "设置")

        self.setCentralWidget(tabs)
        self.resize(1100, 700)


# =========================
# 启动
# =========================

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
