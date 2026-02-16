import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QCalendarWidget, QSpinBox, QComboBox,
    QPushButton, QLabel, QLineEdit, QDateEdit, QTimeEdit, QTableWidget,
    QTableWidgetItem, QMessageBox, QDialogButtonBox, QDialog, QCheckBox,
    QProgressBar, QFrame, QTabWidget
)
from PyQt5.QtCore import QDate, QTime, Qt, QDateTime
from PyQt5.QtGui import QFont, QColor

LOG_ROOT = "logs"
LABELS_FILE = "labels.json"
DISPLAY_LIMIT = 100  # 在数据表中显示的最大数据点数，设为None显示全部

def ensure_labels_file():
    if not os.path.exists(LABELS_FILE):
        with open(LABELS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)

def load_labels():
    ensure_labels_file()
    with open(LABELS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_labels(labels):
    with open(LABELS_FILE, "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)

def get_all_server_ids():
    if not os.path.exists(LOG_ROOT):
        return []
    try:
        return sorted([d for d in os.listdir(LOG_ROOT) if os.path.isdir(os.path.join(LOG_ROOT, d))])
    except:
        return []

def read_cpu_data(sid, start_dt=None, end_dt=None):
    """
    读取服务器日志，返回 [(timestamp, cpu_value), ...]，支持时间范围过滤。
    """
    path = os.path.join(LOG_ROOT, sid)
    if not os.path.exists(path):
        return []

    data = []
    for fn in sorted(os.listdir(path)):
        if not fn.endswith(".log"):
            continue
        try:
            with open(os.path.join(path, fn), encoding="utf-8") as f:
                for line in f:
                    try:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            ts_str = parts[0]
                            cpu_str = parts[1]
                            ts = datetime.fromisoformat(ts_str)
                            cpu = float(cpu_str)
                            # 时间过滤
                            if start_dt and ts < start_dt:
                                continue
                            if end_dt and ts > end_dt:
                                continue
                            data.append((ts, cpu))
                    except:
                        pass
        except:
            pass
    return sorted(data)

def calc_stats(data):
    """计算平均值、最高值、最低值。"""
    if not data:
        return None, None, None, None
    cpus = [cpu for _, cpu in data]
    avg = sum(cpus) / len(cpus)
    max_cpu = max(cpus)
    min_cpu = min(cpus)
    return avg, max_cpu, min_cpu, len(cpus)

class TagDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加标签")
        self.setGeometry(100, 100, 400, 250)
        layout = QVBoxLayout()

        # 标签名
        layout.addWidget(QLabel("标签名:"))
        self.label_input = QLineEdit()
        layout.addWidget(self.label_input)

        # 备注
        layout.addWidget(QLabel("备注:"))
        self.note_input = QLineEdit()
        layout.addWidget(self.note_input)

        # 是否限时
        self.time_limited_check = QCheckBox("限时标签 (小时)")
        layout.addWidget(self.time_limited_check)

        layout.addWidget(QLabel("时长 (小时):"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setMinimum(1)
        self.duration_spin.setValue(24)
        self.duration_spin.setEnabled(False)
        self.time_limited_check.toggled.connect(self.duration_spin.setEnabled)
        layout.addWidget(self.duration_spin)

        # 按钮
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def get_data(self):
        return {
            "label": self.label_input.text(),
            "note": self.note_input.text(),
            "time_limited": self.time_limited_check.isChecked(),
            "duration_hours": self.duration_spin.value() if self.time_limited_check.isChecked() else None
        }

class ViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VF CPU 监控查看器")
        self.setGeometry(100, 100, 1200, 700)
        
        ensure_labels_file()
        self.init_ui()
        self.refresh_servers()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout()
        
        # 标签页
        self.tab_widget = QTabWidget()
        
        # 第一页：概览
        overview_page = self.create_overview_page()
        self.tab_widget.addTab(overview_page, "概览")
        
        # 第二页：详细查询
        detail_page = self.create_detail_page()
        self.tab_widget.addTab(detail_page, "详细查询")
        
        layout.addWidget(self.tab_widget)
        central.setLayout(layout)

    def create_overview_page(self):
        """创建概览页面 - 显示所有服务器的24h统计"""
        page = QWidget()
        layout = QVBoxLayout()
        
        # 排序选项
        sort_layout = QHBoxLayout()
        sort_layout.addWidget(QLabel("排序方式:"))
        self.sort_mode = QComboBox()
        self.sort_mode.addItems(["按24h平均占用", "按24h最高占用", "按24h内>95%的时间"])
        self.sort_mode.currentTextChanged.connect(self.on_overview_refresh)
        sort_layout.addWidget(self.sort_mode)
        sort_layout.addStretch()
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.on_overview_refresh)
        sort_layout.addWidget(refresh_btn)
        
        layout.addLayout(sort_layout)
        
        # 服务器统计表
        self.overview_table = QTableWidget()
        self.overview_table.setColumnCount(5)
        self.overview_table.setHorizontalHeaderLabels(["服务器ID", "24h平均占用(%)", "24h最高占用(%)", "24h>95%次数", "数据点数"])
        layout.addWidget(self.overview_table)
        
        page.setLayout(layout)
        return page
    
    def create_detail_page(self):
        """创建详细查询页面 - 原有逻辑"""
        page = QWidget()
        main_layout = QHBoxLayout()
        
        # 初始化查询模式
        self.query_mode = "今天"
        
        # ===== 左侧：服务器列表 =====
        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("服务器:"))
        self.server_list = QListWidget()
        self.server_list.itemClicked.connect(self.on_server_selected)
        left_layout.addWidget(self.server_list)

        left_panel = QWidget()
        left_panel.setLayout(left_layout)
        left_panel.setMaximumWidth(150)
        main_layout.addWidget(left_panel)

        # ===== 右侧：查询和结果 =====
        right_layout = QVBoxLayout()

        # --- 时间选择 ---
        time_group_layout = QHBoxLayout()
        time_group_layout.addWidget(QLabel("时间范围:"))
        self.time_preset = QComboBox()
        self.time_preset.addItems(["今天", "最近24h", "最近7天", "最近30天", "全部", "自定义"])
        self.time_preset.currentTextChanged.connect(self.on_time_preset_changed)
        time_group_layout.addWidget(self.time_preset)

        # 自定义日期框（初始隐藏）
        self.custom_date_widget = QWidget()
        custom_date_layout = QHBoxLayout()
        custom_date_layout.setContentsMargins(0, 0, 0, 0)
        custom_date_layout.addWidget(QLabel("开始:"))
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDate(QDate.currentDate().addDays(-1))
        custom_date_layout.addWidget(self.start_date)
        custom_date_layout.addWidget(QLabel("结束:"))
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDate(QDate.currentDate())
        custom_date_layout.addWidget(self.end_date)
        self.custom_date_widget.setLayout(custom_date_layout)
        self.custom_date_widget.setVisible(False)
        # 日期变化时自动查询
        self.start_date.dateChanged.connect(self.on_custom_date_changed)
        self.end_date.dateChanged.connect(self.on_custom_date_changed)
        time_group_layout.addWidget(self.custom_date_widget)

        time_group_layout.addStretch()

        right_layout.addLayout(time_group_layout)

        # --- 查询按钮 =====
        btn_layout = QHBoxLayout()
        self.query_btn = QPushButton("查询")
        self.query_btn.clicked.connect(self.on_query)
        btn_layout.addWidget(self.query_btn)

        self.add_label_btn = QPushButton("添加标签")
        self.add_label_btn.clicked.connect(self.on_add_label)
        btn_layout.addWidget(self.add_label_btn)

        self.refresh_btn = QPushButton("刷新服务器")
        self.refresh_btn.clicked.connect(self.refresh_servers)
        btn_layout.addWidget(self.refresh_btn)

        btn_layout.addStretch()
        right_layout.addLayout(btn_layout)

        # --- 统计信息 ---
        stats_layout = QHBoxLayout()
        
        self.stats_labels = {}
        
        # 24h平均占用率 - 特殊处理，用进度条+彩色显示
        stats_24h_widget = QWidget()
        stats_24h_layout = QVBoxLayout()
        stats_24h_layout.setContentsMargins(0, 0, 0, 0)
        label_24h = QLabel("24h平均占用率")
        font_label = QFont()
        font_label.setBold(True)
        label_24h.setFont(font_label)
        stats_24h_layout.addWidget(label_24h)
        
        self.progress_24h = QProgressBar()
        self.progress_24h.setMaximum(100)
        self.progress_24h.setValue(0)
        self.progress_24h.setMaximumHeight(25)
        stats_24h_layout.addWidget(self.progress_24h)
        
        self.value_24h = QLabel("--")
        font_value = QFont()
        font_value.setPointSize(12)
        font_value.setBold(True)
        self.value_24h.setFont(font_value)
        stats_24h_layout.addWidget(self.value_24h)
        
        stats_24h_widget.setLayout(stats_24h_layout)
        stats_layout.addWidget(stats_24h_widget)
        self.stats_labels["24h_avg"] = self.value_24h
        
        # 其他统计指标
        for key, cn_name in [("range_avg", "范围平均"), ("all_time_avg", "有史以来"), 
                              ("max", "最高"), ("min", "最低"), ("count", "数据点数")]:
            label_widget = QWidget()
            label_layout = QVBoxLayout()
            label_layout.setContentsMargins(0, 0, 0, 0)
            label_layout.addWidget(QLabel(cn_name))
            value_label = QLabel("--")
            self.stats_labels[key] = value_label
            label_layout.addWidget(value_label)
            label_widget.setLayout(label_layout)
            stats_layout.addWidget(label_widget)

        stats_layout.addStretch()
        right_layout.addLayout(stats_layout)

        # --- 标签表 ---
        right_layout.addWidget(QLabel("标签:"))
        self.labels_table = QTableWidget()
        self.labels_table.setColumnCount(4)
        self.labels_table.setHorizontalHeaderLabels(["标签", "备注", "过期时间", "删除"])
        self.labels_table.setMaximumHeight(150)
        right_layout.addWidget(self.labels_table)

        # --- 数据表 ---
        right_layout.addWidget(QLabel("数据点:"))
        self.data_table = QTableWidget()
        self.data_table.setColumnCount(2)
        self.data_table.setHorizontalHeaderLabels(["时间戳", "CPU占用率(%)"])
        right_layout.addWidget(self.data_table)

        right_layout.addStretch()
        
        right_panel = QWidget()
        right_panel.setLayout(right_layout)
        main_layout.addWidget(right_panel, 1)

        page.setLayout(main_layout)
        return page

    def refresh_servers(self):
        self.server_list.clear()
        for sid in get_all_server_ids():
            item = QListWidgetItem(sid)
            self.server_list.addItem(item)
        # 同时刷新概览页面
        self.on_overview_refresh()

    def on_overview_refresh(self):
        """刷新概览页面 - 显示所有服务器的24h统计"""
        self.overview_table.setRowCount(0)
        
        all_sids = get_all_server_ids()
        stats = []
        
        # 计算每个服务器的24h统计
        cutoff_24h = datetime.now() - timedelta(hours=24)
        for sid in all_sids:
            all_data = read_cpu_data(sid)
            data_24h = [d for d in all_data if d[0] >= cutoff_24h]
            
            if data_24h:
                cpus = [cpu for _, cpu in data_24h]
                avg_24h = sum(cpus) / len(cpus)
                max_24h = max(cpus)
                count = len(data_24h)
                # 计算24h内CPU占用率>95%的数据点数
                count_high = sum(1 for cpu in cpus if cpu > 95)
            else:
                avg_24h = 0
                max_24h = 0
                count = 0
                count_high = 0
            
            stats.append((sid, avg_24h, max_24h, count_high, count))
        
        # 根据排序模式排序
        sort_text = self.sort_mode.currentText()
        if sort_text == "按24h平均占用":
            stats.sort(key=lambda x: x[1], reverse=True)
        elif sort_text == "按24h最高占用":
            stats.sort(key=lambda x: x[2], reverse=True)
        else:  # 按24h内>95%的时间
            stats.sort(key=lambda x: x[3], reverse=True)
        
        # 填充表格
        for sid, avg_24h, max_24h, count_high, count in stats:
            row = self.overview_table.rowCount()
            self.overview_table.insertRow(row)
            self.overview_table.setItem(row, 0, QTableWidgetItem(sid))
            self.overview_table.setItem(row, 1, QTableWidgetItem(f"{avg_24h:.2f}"))
            self.overview_table.setItem(row, 2, QTableWidgetItem(f"{max_24h:.2f}"))
            self.overview_table.setItem(row, 3, QTableWidgetItem(str(count_high)))
            self.overview_table.setItem(row, 4, QTableWidgetItem(str(count)))

    def on_server_selected(self, item):
        self.current_sid = item.text()
        self.on_query()

    def on_time_preset_changed(self, preset):
        # 只在自定义模式下显示日期选择框
        self.custom_date_widget.setVisible(preset == "自定义")
        self.query_mode = preset
        # 非自定义模式自动查询
        if preset != "自定义":
            self.on_query()

    def on_custom_date_changed(self):
        # 如果用户修改了自定义日期，确保切换到自定义模式并自动查询
        try:
            self.time_preset.setCurrentText("自定义")
        except Exception:
            # 兼容早期 Qt 版本
            for i in range(self.time_preset.count()):
                if self.time_preset.itemText(i) == "自定义":
                    self.time_preset.setCurrentIndex(i)
                    break
        self.query_mode = "自定义"
        self.on_query()

    def on_query(self):
        if not hasattr(self, 'current_sid'):
            QMessageBox.warning(self, "错误", "请先选择一个服务器。")
            return

        # 读取全部数据以便决定最新时间点
        all_data = read_cpu_data(self.current_sid)
        query_mode = getattr(self, 'query_mode', '今天')

        start_dt = None
        end_dt = None
        latest_ts = all_data[-1][0] if all_data else None

        if query_mode == "今天":
            start_dt = datetime.combine(QDate.currentDate().toPyDate(), datetime.min.time())
            end_dt = datetime.now()
        elif query_mode == "最近24h":
            if latest_ts:
                start_dt = latest_ts - timedelta(hours=24)
                end_dt = latest_ts
            else:
                start_dt = None
                end_dt = None
        elif query_mode == "最近7天":
            if latest_ts:
                start_dt = latest_ts - timedelta(days=7)
                end_dt = latest_ts
        elif query_mode == "最近30天":
            if latest_ts:
                start_dt = latest_ts - timedelta(days=30)
                end_dt = latest_ts
        elif query_mode == "全部":
            start_dt = None
            end_dt = None
        else:  # 自定义
            start_dt = datetime.combine(self.start_date.date().toPyDate(), datetime.min.time())
            end_dt = datetime.combine(self.end_date.date().toPyDate(), datetime.max.time())

        # 获取范围数据（start_dt/end_dt 为 None 时表示全范围）
        range_data = read_cpu_data(self.current_sid, start_dt, end_dt) if (start_dt or end_dt) else read_cpu_data(self.current_sid)

        # 计算 24h 数据（使用最新时间点向后24小时）
        if latest_ts:
            cutoff_24h = latest_ts - timedelta(hours=24)
            data_24h = [d for d in all_data if d[0] >= cutoff_24h]
        else:
            data_24h = []

        # 统计
        avg_24h, _, _, _ = calc_stats(data_24h) if data_24h else (None, None, None, None)
        avg_range, max_cpu, min_cpu, count = calc_stats(range_data) if range_data else (None, None, None, None)
        avg_all, _, _, _ = calc_stats(all_data) if all_data else (None, None, None, None)

        # 更新显示
        # 24h平均占用率 - 用彩色进度条
        if avg_24h is not None:
            self.progress_24h.setValue(int(avg_24h))
            # 根据CPU占用率设置进度条颜色：绿色(<50%) 黄色(50-70%) 红色(>70%)
            if avg_24h < 50:
                color = "background-color: #90EE90;"  # 浅绿
            elif avg_24h < 70:
                color = "background-color: #FFD700;"  # 金黄
            else:
                color = "background-color: #FF6B6B;"  # 红色
            self.progress_24h.setStyleSheet(f"QProgressBar::chunk {{{color}}}")
            self.value_24h.setText(f"{avg_24h:.1f}%")
            # 根据占用率调整文字颜色
            if avg_24h < 50:
                self.value_24h.setStyleSheet("color: green;")
            elif avg_24h < 70:
                self.value_24h.setStyleSheet("color: #FF8C00;")
            else:
                self.value_24h.setStyleSheet("color: red;")
        else:
            self.progress_24h.setValue(0)
            self.value_24h.setText("N/A")
            self.value_24h.setStyleSheet("color: gray;")
        
        self.stats_labels["range_avg"].setText(f"{avg_range:.2f}%" if avg_range else "N/A")
        self.stats_labels["all_time_avg"].setText(f"{avg_all:.2f}%" if avg_all else "N/A")
        self.stats_labels["max"].setText(f"{max_cpu:.2f}%" if max_cpu else "N/A")
        self.stats_labels["min"].setText(f"{min_cpu:.2f}%" if min_cpu else "N/A")
        self.stats_labels["count"].setText(str(count if count else 0))

        # 更新数据表
        self.data_table.setRowCount(0)
        for ts, cpu in range_data[-100:]:  # 只显示最后 100 条
            row = self.data_table.rowCount()
            self.data_table.insertRow(row)
            self.data_table.setItem(row, 0, QTableWidgetItem(ts.isoformat()))
            self.data_table.setItem(row, 1, QTableWidgetItem(f"{cpu:.1f}"))

        # 更新标签表
        self.update_labels_display()

    def update_labels_display(self):
        if not hasattr(self, 'current_sid'):
            return
        
        labels_data = load_labels()
        server_labels = labels_data.get(self.current_sid, [])

        self.labels_table.setRowCount(0)
        now = datetime.now()

        for idx, label_info in enumerate(server_labels):
            # 检查标签是否过期
            expires = label_info.get("expires")
            is_expired = False
            if expires:
                try:
                    exp_dt = datetime.fromisoformat(expires)
                    is_expired = now > exp_dt
                except:
                    pass

            row = self.labels_table.rowCount()
            self.labels_table.insertRow(row)

            label_text = label_info.get("label", "")
            if is_expired:
                label_text += " [EXPIRED]"
            self.labels_table.setItem(row, 0, QTableWidgetItem(label_text))
            self.labels_table.setItem(row, 1, QTableWidgetItem(label_info.get("note", "")))

            expires_text = label_info.get("expires", "∞")
            if expires_text != "∞":
                try:
                    exp_dt = datetime.fromisoformat(expires_text)
                    expires_text = exp_dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
            self.labels_table.setItem(row, 2, QTableWidgetItem(expires_text))

            # 删除按钮
            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda checked, i=idx: self.on_remove_label(i))
            self.labels_table.setCellWidget(row, 3, remove_btn)

    def on_add_label(self):
        if not hasattr(self, 'current_sid'):
            QMessageBox.warning(self, "错误", "请先选择一个服务器。")
            return

        dialog = TagDialog(self)
        if dialog.exec_():
            data = dialog.get_data()
            label_name = data["label"].strip()
            if not label_name:
                QMessageBox.warning(self, "错误", "标签名不能为空。")
                return

            labels_data = load_labels()
            if self.current_sid not in labels_data:
                labels_data[self.current_sid] = []

            expires = None
            if data["time_limited"]:
                expires = (datetime.now() + timedelta(hours=data["duration_hours"])).isoformat()

            label_info = {
                "label": label_name,
                "note": data["note"],
                "created": datetime.now().isoformat(),
                "expires": expires
            }
            labels_data[self.current_sid].append(label_info)
            save_labels(labels_data)
            self.update_labels_display()
            QMessageBox.information(self, "成功", f"标签 '{label_name}' 已添加。")

    def on_remove_label(self, idx):
        if not hasattr(self, 'current_sid'):
            return

        labels_data = load_labels()
        if self.current_sid in labels_data:
            if 0 <= idx < len(labels_data[self.current_sid]):
                labels_data[self.current_sid].pop(idx)
                save_labels(labels_data)
                self.update_labels_display()
                QMessageBox.information(self, "成功", "标签已删除。")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ViewerWindow()
    window.show()
    sys.exit(app.exec_())
