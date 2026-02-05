import os
import sys
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

# ================= Debug 参数 =================
DEBUG_LEVEL = 0
if "--debug" in sys.argv:
    idx = sys.argv.index("--debug")
    try:
        DEBUG_LEVEL = int(sys.argv[idx + 1])
    except:
        DEBUG_LEVEL = 1

# ================= 参数 =================
CPU_PRINT_THRESHOLD = 30.0

CPU_ALARM_THRESHOLD = 90.0
CPU_DAILY_LIMIT_SEC = 3600      # 当天累计 1h
CPU_CONT_LIMIT_SEC = 3600       # 连续 1h
CPU_24H_AVG_THRESHOLD = 30.0

TAB_BATCH_SIZE = 5
POLL_INTERVAL = 3

SERVER_REFRESH_INTERVAL = 3600
WATCHDOG_TIMEOUT = 120

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

VF_EMAIL = input("VirtFusion Email: ")
VF_PASSWORD = input_password_masked("VirtFusion Password: ")

# ================= 配置 =================
BASE_URL = "https://vf.ciallo.ee"
SERVERS_URL = f"{BASE_URL}/admin/servers"
LOG_ROOT = "logs"

# ================= 状态 =================
warn_count = {}

daily_90_usage_sec = {}     # sid -> 今日累计 >=90% 秒
cpu_90_cont_since = {}      # sid -> 连续 >=90% 起始时间
cpu_samples = {}            # sid -> [(ts, cpu)]

last_day = datetime.now().date()
last_success_ts = time.time()

# ================= 工具 =================
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def log_cpu(sid, cpu):
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(LOG_ROOT, sid)
    ensure_dir(path)
    with open(os.path.join(path, f"{date}.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {cpu}\n")

def color_cpu(cpu):
    if cpu >= 95:
        return f"\033[5;31m{cpu:6.2f}%\033[0m"
    elif cpu >= 50:
        return f"\033[31m{cpu:6.2f}%\033[0m"
    elif cpu >= 30:
        return f"\033[33m{cpu:6.2f}%\033[0m"
    else:
        return f"{cpu:6.2f}%"

def get_24h_avg(sid):
    samples = cpu_samples.get(sid, [])
    if not samples:
        return None
    return sum(c for _, c in samples) / len(samples)

# ================= 报警判定 =================
def should_alarm(sid, cpu, now):
    global last_day

    # ===== 跨天清零 =====
    today = datetime.now().date()
    if today != last_day:
        daily_90_usage_sec.clear()
        cpu_90_cont_since.clear()
        last_day = today

    # ===== 记录样本 =====
    samples = cpu_samples.setdefault(sid, [])
    samples.append((now, cpu))

    cutoff = now - 24 * 3600
    cpu_samples[sid] = [(t, c) for t, c in samples if t >= cutoff]

    # ===== 规则 1：当天累计 >=90% =====
    if cpu >= CPU_ALARM_THRESHOLD:
        daily_90_usage_sec[sid] = daily_90_usage_sec.get(sid, 0) + POLL_INTERVAL
        if daily_90_usage_sec[sid] >= CPU_DAILY_LIMIT_SEC:
            return True, "当天累计跑满 CPU ≥ 1h"

    # ===== 规则 2：连续 >=90% =====
    if cpu >= CPU_ALARM_THRESHOLD:
        cpu_90_cont_since.setdefault(sid, now)
        if now - cpu_90_cont_since[sid] >= CPU_CONT_LIMIT_SEC:
            return True, "连续跑满 CPU ≥ 1h"
    else:
        cpu_90_cont_since.pop(sid, None)

    # ===== 规则 3：前 24h 平均 =====
    avg = get_24h_avg(sid)
    if avg is not None and avg >= CPU_24H_AVG_THRESHOLD:
        return True, f"前24h平均 CPU {avg:.1f}%"

    return False, None

def maybe_print(sid, cpu, alarm, reason):
    ts = datetime.now().strftime("%H:%M:%S")
    avg = get_24h_avg(sid)

    if alarm:
        cnt = warn_count.get(sid, 0) + 1
        warn_count[sid] = cnt
        print(
            f"[{ts}] SID={sid} CPU={color_cpu(cpu)} "
            f"!!! 第{cnt}次警告 | {reason}"
        )
        return

    if DEBUG_LEVEL >= 1:
        if DEBUG_LEVEL >= 2 and avg is not None:
            print(
                f"[{ts}] SID={sid} CPU={color_cpu(cpu)} "
                f"| 24h_avg={avg:5.1f}%"
            )
        else:
            print(f"[{ts}] SID={sid} CPU={color_cpu(cpu)}")

# ================= 登录 / 抓取 =================
def auto_login(page):
    page.goto(BASE_URL)
    page.wait_for_selector("input[type='email']")
    page.fill("input[type='email']", VF_EMAIL)
    page.fill("input[type='password']", VF_PASSWORD)
    page.click("button.btn-primary")
    page.wait_for_url("**/admin/dashboard")

def get_all_server_ids(page):
    page.goto(SERVERS_URL)
    time.sleep(2)
    ids = set()
    for r in page.query_selector_all("tr"):
        if r.query_selector("span.badge-success"):
            cb = r.query_selector("input.form-check-input")
            if cb:
                ids.add(cb.get_attribute("value"))
    print(f"[+] 发现 Active 服务器 {len(ids)} 台")
    return list(ids)

def fetch_cpu(page):
    global last_success_ts
    page.wait_for_selector("#cpuGauge text.value-text", timeout=15_000)
    txt = page.text_content("#cpuGauge text.value-text")
    last_success_ts = time.time()
    return float(txt.replace("%", "").strip())

# ================= 单次运行 =================
def run_once():
    ensure_dir(LOG_ROOT)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        auto_login(page)
        ids = get_all_server_ids(page)
        last_refresh = time.time()

        print("[*] 开始监控（Ctrl+C 退出）")

        while True:
            now = time.time()

            if now - last_success_ts > WATCHDOG_TIMEOUT:
                print("[WATCHDOG] 超时，重启 Playwright")
                break

            if now - last_refresh > SERVER_REFRESH_INTERVAL:
                ids = get_all_server_ids(page)
                last_refresh = now

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
                    try:
                        cpu = fetch_cpu(p)
                        log_cpu(sid, cpu)
                        alarm, reason = should_alarm(sid, cpu, now)
                        maybe_print(sid, cpu, alarm, reason)
                    finally:
                        p.close()

            time.sleep(POLL_INTERVAL)

# ================= 主入口 =================
def main():
    while True:
        try:
            run_once()
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n[+] 正常退出")
            break
        except Exception as e:
            print(f"[FATAL] {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
