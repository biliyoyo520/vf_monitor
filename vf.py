import os
import sys
import time
from datetime import datetime, timedelta

from playwright.sync_api import sync_playwright

# ================= 参数 =================
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

BASE_URL = "https://vf.ciallo.ee"
SERVERS_URL = f"{BASE_URL}/admin/servers"
LOG_ROOT = "logs"

# ================= 状态 =================
cpu_90_accumulate = {}    # sid -> seconds
cpu_90_continuous = {}    # sid -> start_ts
alerted = set()

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
                    ts = datetime.fromisoformat(ts)
                    if ts >= cutoff:
                        total += float(cpu)
                        count += 1
                except:
                    pass

    return total / count if count else None

def alert(sid, reason):
    if sid in alerted:
        return
    alerted.add(sid)
    print(f"[ALERT] SID={sid} 命中规则: {reason}")

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
            print(f"[*] 扫描服务器列表 第 {page_no} 页")

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

    print(f"[+] 发现 Active 服务器: {len(ids)}")
    return list(ids)

# ================= 抓 CPU =================
def fetch_cpu(page, sid):
    global last_success_ts
    try:
        txt = page.text_content("#cpuGauge text.value-text")
        cpu = float(txt.replace("%", ""))
        last_success_ts = time.time()
        return cpu
    except:
        return None

# ================= 主循环 =================
def main():
    ensure_dir(LOG_ROOT)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not HEADFUL,
            args=["--disable-gpu", "--no-sandbox"]
        )
        ctx = browser.new_context()
        page = ctx.new_page()

        auto_login(page)
        ids = get_all_server_ids(page)
        last_refresh = time.time()

        print("[*] 开始监控")

        while True:
            now = time.time()

            if now - last_success_ts > WATCHDOG_TIMEOUT:
                print("[WATCHDOG] 重启浏览器")
                browser.close()
                return main()

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
                    cpu = fetch_cpu(p, sid)
                    p.close()
                    if cpu is None:
                        continue

                    log_cpu(sid, cpu)

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

            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
