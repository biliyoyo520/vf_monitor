import os
import sys
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
    QProgressBar,
    QVBoxLayout,
    QWidget
)
from PySide6.QtCore import QTimer, Qt


LOG_ROOT = "./logs"
REFRESH_INTERVAL_MS = 5000      # 5 秒
WINDOW_MINUTES = 10


def parse_log(path, since_ts):
    data = []
    if not os.path.exists(path):
        return data

    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                ts_s, cpu_s = line.strip().split()
                ts = datetime.fromisoformat(ts_s)
                cpu = float(cpu_s)
                if ts >= since_ts:
                    data.append(cpu)
            except:
                continue
    return data


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VirtFusion CPU Log Viewer")
        self.resize(900, 500)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "SID", "Latest (%)", "Avg (%)", "Max (%)", "Usage"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)

        layout = QVBoxLayout()
        layout.addWidget(self.table)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(REFRESH_INTERVAL_MS)

        self.refresh()

    def refresh(self):
        today = datetime.now().strftime("%Y-%m-%d")
        since = datetime.now() - timedelta(minutes=WINDOW_MINUTES)

        rows = []

        if not os.path.exists(LOG_ROOT):
            return

        for sid in os.listdir(LOG_ROOT):
            log_path = os.path.join(LOG_ROOT, sid, f"{today}.log")
            cpus = parse_log(log_path, since)
            if not cpus:
                continue

            rows.append({
                "sid": sid,
                "latest": cpus[-1],
                "avg": sum(cpus) / len(cpus),
                "max": max(cpus)
            })

        rows.sort(key=lambda x: x["latest"], reverse=True)

        self.table.setRowCount(len(rows))

        for row, r in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(r["sid"]))
            self.table.setItem(row, 1, QTableWidgetItem(f"{r['latest']:.1f}"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{r['avg']:.1f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{r['max']:.1f}"))

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(r["latest"]))
            bar.setTextVisible(True)
            bar.setFormat("%p%")

            # 颜色分级
            if r["latest"] >= 95:
                bar.setStyleSheet("QProgressBar::chunk{background:red;}")
            elif r["latest"] >= 50:
                bar.setStyleSheet("QProgressBar::chunk{background:orange;}")
            elif r["latest"] >= 30:
                bar.setStyleSheet("QProgressBar::chunk{background:gold;}")
            else:
                bar.setStyleSheet("")

            self.table.setCellWidget(row, 4, bar)

        self.table.resizeColumnsToContents()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
