import os
import sys
import time
from datetime import datetime, timedelta
from collections import deque, defaultdict

from playwright.sync_api import sync_playwright

# ================= 参数 =================

# 这里写 Virtfusion 面板访问地址 仅在 Virtfusion 6.2.0 测试通过
BASE_URL = "https://vf.ciallo.ee"

DEBUG_LEVEL = 0
if "--debug" in sys.argv:
    idx = sys.argv.index("--debug")
    if idx + 1 < len(sys.argv):
        try:
            DEBUG_LEVEL = int(sys.argv[idx + 1])
        except ValueError:
            DEBUG_LEVEL = 1
    else:
        DEBUG_LEVEL = 1

DEBUG = DEBUG_LEVEL >= 1
HEADFUL = DEBUG_LEVEL >= 3

CPU_AVG_THRESHOLD = 30.0
CPU_HIGH = 90.0

TAB_BATCH_SIZE = 5
POLL_INTERVAL = 3
SERVER_REFRESH_INTERVAL = 3600
WATCHDOG_TIMEOUT = 120

CPU_5MIN_WINDOW = 300

# ================= Watchdog =================
class WatchdogRestart(Exception):
    pass

# ================= 状态 =================
cpu_90_accumulate = {}
cpu_90_continuous = {}
alerted = set()

cpu_5min_samples = defaultdict(deque)
last_5min_report = 0

last_success_ts = time.time()

# 进度条状态
progress_done = 0
progress_total = 0

# ================= UI =================
def clear_progress():
    print("\r" + " " * 120 + "\r", end="", flush=True)

def render_progress(done, total):
    if total <= 0:
        return
    bar_len = 30
    filled = int(bar_len * done / total)
    bar = "█" * filled + "-" * (bar_len - filled)
    print(f"\r[SCAN] |{bar}| {done}/{total}", end="", flush=True)

def ui_print(msg):
    clear_progress()
    print(msg)
    render_progress(progress_done, progress_total)

def ui_print_lines(lines):
    clear_progress()
    for l in lines:
        print(l)
    render_progress(progress_done, progress_total)

# ================= 密码 =================
def input_password_masked(prompt="Password: "):
    import msvcrt
    print(prompt, end="", flush=True)
    pwd = ""
    while True:
        ch = msvcrt.getch()
        if ch in (b"\r", b"\n"):
            print()
            break
        elif ch == b"\x08":
            if pwd:
                pwd = pwd[:-1]
                print("\b \b", end="", flush=True)
        elif ch == b"\x03":
            raise KeyboardInterrupt
        else:
            try:
                pwd += ch.decode("utf-8")
                print("*", end="", flush=True)
            except:
                pass
    return pwd

# ================= 登录信息 =================
VF_EMAIL = input("VirtFusion Email: ")
VF_PASSWORD = input_password_masked("VirtFusion Password: ")

SERVERS_URL = f"{BASE_URL}/admin/servers"
LOG_ROOT = "logs"

# ================= 工具 =================
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def log_cpu(sid, cpu):
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(LOG_ROOT, sid)
    ensure_dir(path)
    with open(os.path.join(path, f"{date}.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {cpu}\n")

def read_last_24h_avg(sid):
    path = os.path.join(LOG_ROOT, sid)
    if not os.path.exists(path):
        return None
    cutoff = datetime.now() - timedelta(hours=24)
    total = count = 0
    for fn in os.listdir(path):
        if not fn.endswith(".log"):
            continue
        with open(os.path.join(path, fn), encoding="utf-8") as f:
            for line in f:
                try:
                    ts, cpu = line.split()
                    if datetime.fromisoformat(ts) >= cutoff:
                        total += float(cpu)
                        count += 1
                except:
                    pass
    return total / count if count else None

def alert(sid, reason):
    if sid in alerted:
        return
    alerted.add(sid)
    ui_print(f"[ALERT] SID={sid} 命中规则: {reason}")

# ================= 登录 =================
def auto_login(page):
    page.goto(BASE_URL)
    page.fill("input[type='email']", VF_EMAIL)
    page.fill("input[type='password']", VF_PASSWORD)
    page.click("button.btn-primary")
    page.wait_for_url("**/admin/dashboard", timeout=30_000)

# ================= 抓服务器 =================
def get_all_server_ids(page):
    page.goto(SERVERS_URL)
    time.sleep(2)

    ids = set()
    page_no = 1

    while True:
        if DEBUG:
            ui_print(f"[*] 扫描服务器列表 第 {page_no} 页")

        for r in page.query_selector_all("tr"):
            if not r.query_selector("span.badge-success"):
                continue
            cb = r.query_selector("input.form-check-input[type='checkbox']")
            if cb:
                ids.add(cb.get_attribute("value"))

        next_btn = page.query_selector(
            "ul.pagination li.page-item.c-pointer span.page-link:text-is('»')"
        )
        if not next_btn:
            break

        parent = next_btn.evaluate_handle("el => el.parentElement")
        if "disabled" in (parent.get_attribute("class") or ""):
            break

        next_btn.click()
        page_no += 1
        time.sleep(2)

    ui_print(f"[+] 发现 Active 服务器: {len(ids)}")
    return list(ids)

# ================= 抓 CPU =================
def fetch_cpu(page):
    global last_success_ts
    try:
        txt = page.text_content("#cpuGauge text.value-text")
        cpu = float(txt.replace("%", ""))
        last_success_ts = time.time()
        return cpu
    except:
        return None

# ================= 单次运行 =================
def run_once(pw):
    global last_5min_report, progress_done, progress_total

    browser = pw.chromium.launch(
        headless=not HEADFUL,
        args=["--disable-gpu", "--no-sandbox"]
    )
    ctx = browser.new_context()
    page = ctx.new_page()

    auto_login(page)
    ids = get_all_server_ids(page)
    last_refresh = time.time()

    ui_print("[*] 开始监控")

    while True:
        now = time.time()

        if now - last_success_ts > WATCHDOG_TIMEOUT:
            browser.close()
            raise WatchdogRestart()

        if now - last_refresh > SERVER_REFRESH_INTERVAL:
            ids = get_all_server_ids(page)
            last_refresh = now

        progress_done = 0
        progress_total = len(ids)

        for i in range(0, len(ids), TAB_BATCH_SIZE):
            pages = {}
            for sid in ids[i:i + TAB_BATCH_SIZE]:
                p = ctx.new_page()
                try:
                    p.goto(f"{BASE_URL}/admin/servers/{sid}", timeout=15_000)
                    pages[sid] = p
                except:
                    p.close()

            for sid, p in pages.items():
                cpu = fetch_cpu(p)
                p.close()

                progress_done += 1
                render_progress(progress_done, progress_total)

                if cpu is None:
                    continue

                log_cpu(sid, cpu)

                now_ts = time.time()
                dq = cpu_5min_samples[sid]
                dq.append((now_ts, cpu))
                while dq and now_ts - dq[0][0] > CPU_5MIN_WINDOW:
                    dq.popleft()

                if DEBUG_LEVEL >= 1:
                    msg = f"[CPU] SID={sid} now={cpu:.1f}%"
                    if DEBUG_LEVEL >= 2:
                        avg = read_last_24h_avg(sid)
                        msg += f" | 24h_avg={avg:.1f}%" if avg else " | 24h_avg=N/A"
                    ui_print(msg)

                if cpu >= CPU_HIGH:
                    cpu_90_accumulate[sid] = cpu_90_accumulate.get(sid, 0) + POLL_INTERVAL
                    if cpu_90_accumulate[sid] >= 3600:
                        alert(sid, "R1(累计90%≥1h)")
                    cpu_90_continuous.setdefault(sid, now)
                    if now - cpu_90_continuous[sid] >= 3600:
                        alert(sid, "R2(连续90%≥1h)")
                else:
                    cpu_90_continuous.pop(sid, None)

                avg = read_last_24h_avg(sid)
                if avg is not None and avg >= CPU_AVG_THRESHOLD:
                    alert(sid, "R3(24h平均≥30%)")

        # ===== 每 5 分钟 Top5 =====
        if time.time() - last_5min_report >= 300:
            last_5min_report = time.time()
            lines = ["[STATS][Last Scan] Top5 CPU:"]
            stats = []
            for sid, dq in cpu_5min_samples.items():
                if dq:
                    stats.append((sum(v for _, v in dq) / len(dq), sid))
            for avg, sid in sorted(stats, reverse=True)[:5]:
                lines.append(f"  SID={sid} high={avg:.1f}%")

            lines.append("[STATS][24h] Top5 CPU:")
            stats = []
            for sid in cpu_5min_samples:
                avg = read_last_24h_avg(sid)
                if avg is not None:
                    stats.append((avg, sid))
            for avg, sid in sorted(stats, reverse=True)[:5]:
                lines.append(f"  SID={sid} avg={avg:.1f}%")

            lines.append("-" * 40)
            ui_print_lines(lines)

        time.sleep(POLL_INTERVAL)

# ================= 主入口 =================
def main():
    ensure_dir(LOG_ROOT)
    with sync_playwright() as pw:
        while True:
            try:
                run_once(pw)
            except WatchdogRestart:
                ui_print("[WATCHDOG] 重启浏览器")

if __name__ == "__main__":
    main()
