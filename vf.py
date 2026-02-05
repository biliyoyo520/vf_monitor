import os
import sys
import time
import getpass
from datetime import datetime

from playwright.sync_api import sync_playwright

# ================= 参数 =================
DEBUG = "--debug" in sys.argv

CPU_PRINT_THRESHOLD = 30.0
SUSTAIN_CPU_THRESHOLD = 30.0
SUSTAIN_HIT_COUNT = 3      # 连续 3 次超占用
TAB_BATCH_SIZE = 5
MAX_SUSTAIN_TABS = 20

POLL_INTERVAL = 3
SUSTAIN_INTERVAL = 3

SERVER_REFRESH_INTERVAL = 3600  # 1 小时
SUSTAIN_ENTER_SEC = 30 * 60
SUSTAIN_EXIT_SEC = 30 * 60

WATCHDOG_TIMEOUT = 120  # 秒

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
        elif ch == b"\x08":  # Backspace
            if pwd:
                pwd = pwd[:-1]
                print("\b \b", end="", flush=True)
        elif ch == b"\x03":  # Ctrl+C
            raise KeyboardInterrupt
        else:
            try:
                c = ch.decode("utf-8")
            except:
                continue
            pwd += c
            print("*", end="", flush=True)
    return pwd
# ================= 登录信息 =================
VF_EMAIL = input("VirtFusion Email: ")
VF_PASSWORD = input_password_masked("VirtFusion Password: ")

# ================= 配置 =================
BASE_URL = "https://vf.ciallo.ee"
SERVERS_URL = f"{BASE_URL}/admin/servers"
LOG_ROOT = "logs"

# ================= 状态 =================
warn_count = {}
cpu_ema = {}

high_cpu_hits = {}     # sid -> 连续命中次数
sustain_since = {}    # sid -> 时间戳
sustain_tabs = {}     # sid -> page

last_success_ts = time.time()
running = True

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

def maybe_print(sid, cpu):
    ts = datetime.now().strftime("%H:%M:%S")
    if cpu >= CPU_PRINT_THRESHOLD:
        cnt = warn_count.get(sid, 0) + 1
        warn_count[sid] = cnt
        print(f"[{ts}] SID={sid} CPU={color_cpu(cpu)} !!! 第{cnt}次警告")
    elif DEBUG:
        print(f"[{ts}] SID={sid} CPU={color_cpu(cpu)}")

# ================= 登录 =================
def auto_login(page):
    print("[*] 自动登录 VirtFusion")
    page.goto(BASE_URL)

    page.wait_for_selector("input[type='email']")
    page.fill("input[type='email']", VF_EMAIL)
    page.fill("input[type='password']", VF_PASSWORD)

    page.wait_for_function(
        """() => {
            const btn = document.querySelector("button.btn-primary");
            return btn && !btn.disabled;
        }""",
        timeout=10_000
    )

    page.click("button.btn-primary")
    page.wait_for_url("**/admin/dashboard", timeout=30_000)
    print("[+] 登录成功")

# ================= 翻页抓 ID =================
def get_all_server_ids(page):
    page.goto(SERVERS_URL)
    time.sleep(2)

    ids = set()
    visited = set()

    while True:
        cur = page.query_selector(
            "ul.pagination li.page-item.active span.page-link"
        )
        if not cur:
            break

        pno = cur.inner_text().strip()
        if pno in visited:
            break
        visited.add(pno)

        for r in page.query_selector_all("tr"):
            if not r.query_selector("span.badge-success"):
                continue
            cb = r.query_selector("input.form-check-input[type='checkbox']")
            if cb:
                ids.add(cb.get_attribute("value"))

        nxt = None
        for el in page.query_selector_all(
            "ul.pagination li.page-item.c-pointer span.page-link"
        ):
            t = el.inner_text().strip()
            if t.isdigit() and t not in visited:
                nxt = el
                break

        if not nxt:
            break

        nxt.click()
        time.sleep(2)

    print(f"[+] 发现 Active 服务器: {len(ids)}")
    return list(ids)

# ================= 抓 CPU =================
def fetch_cpu(page, sid):
    global last_success_ts
    try:
        page.wait_for_selector("#cpuGauge text.value-text", timeout=15_000)
        txt = page.text_content("#cpuGauge text.value-text")
        cpu = float(txt.replace("%", "").strip())
        last_success_ts = time.time()
        return cpu
    except Exception as e:
        print(f"[WARN] SID={sid} 抓取失败: {e}")
        return None

# ================= 主循环 =================
def main():
    global last_success_ts

    ensure_dir(LOG_ROOT)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-backgrounding-occluded-windows"
            ]
        )

        ctx = browser.new_context()
        page = ctx.new_page()

        auto_login(page)
        ids = get_all_server_ids(page)
        last_server_refresh_ts = time.time()

        print("[*] 开始监控（Ctrl+C 退出）")

        try:
            while True:
                now = time.time()

                # ===== Watchdog =====
                if now - last_success_ts > WATCHDOG_TIMEOUT:
                    print("[WATCHDOG] 浏览器假死，重启")
                    browser.close()
                    return main()

                # ===== 刷新服务器列表 =====
                if now - last_server_refresh_ts >= SERVER_REFRESH_INTERVAL:
                    ids = get_all_server_ids(page)
                    last_server_refresh_ts = now

                # ===== 普通轮询 =====
                for i in range(0, len(ids), TAB_BATCH_SIZE):
                    batch = ids[i:i + TAB_BATCH_SIZE]
                    pages = {}

                    for sid in batch:
                        if sid in sustain_tabs:
                            continue
                        p = ctx.new_page()
                        try:
                            p.goto(
                                f"{BASE_URL}/admin/servers/{sid}",
                                wait_until="domcontentloaded",
                                timeout=15_000
                            )
                            pages[sid] = p
                        except:
                            p.close()

                    for sid, p in pages.items():
                        cpu = fetch_cpu(p, sid)
                        if cpu is None:
                            p.close()
                            continue

                        log_cpu(sid, cpu)
                        maybe_print(sid, cpu)

                        # ===== sustain 判定 =====
                        if cpu >= SUSTAIN_CPU_THRESHOLD:
                            high_cpu_hits[sid] = high_cpu_hits.get(sid, 0) + 1
                        else:
                            high_cpu_hits.pop(sid, None)
                            sustain_since.pop(sid, None)

                        hit_ok = high_cpu_hits.get(sid, 0) >= SUSTAIN_HIT_COUNT
                        if hit_ok:
                            sustain_since.setdefault(sid, now)

                        time_ok = (
                            sid in sustain_since and
                            now - sustain_since[sid] >= SUSTAIN_ENTER_SEC
                        )

                        if (hit_ok or time_ok):
                            if sid not in sustain_tabs and len(sustain_tabs) < MAX_SUSTAIN_TABS:
                                sustain_tabs[sid] = p
                                print(
                                    f"[SUSTAIN] SID={sid} "
                                    f"hits={high_cpu_hits.get(sid, 0)}"
                                )
                                continue

                        p.close()

                # ===== 持续监控 =====
                for sid, p in list(sustain_tabs.items()):
                    # 保险：页面已被关掉就直接跳过
                    if p.is_closed():
                        sustain_tabs.pop(sid, None)
                        sustain_since.pop(sid, None)
                        high_cpu_hits.pop(sid, None)
                        continue
                    
                    cpu = fetch_cpu(p, sid)
                    if cpu is None:
                        continue
                    
                    log_cpu(sid, cpu)
                    maybe_print(sid, cpu)
                
                    if cpu < SUSTAIN_CPU_THRESHOLD:
                        sustain_since.setdefault(sid, now)
                        if now - sustain_since[sid] >= SUSTAIN_EXIT_SEC:
                            print(f"[SUSTAIN] SID={sid} 移出持续监控")
                            try:
                                p.close()
                            except:
                                pass
                            sustain_tabs.pop(sid, None)
                            sustain_since.pop(sid, None)
                            high_cpu_hits.pop(sid, None)
                            continue   # ⭐⭐ 关键：绝不能再 reload
                    else:
                        sustain_since.pop(sid, None)
                
                    try:
                        p.reload()
                    except:
                        pass
                    
                    time.sleep(SUSTAIN_INTERVAL)


                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\n[+] 正常退出")
        finally:
            browser.close()

if __name__ == "__main__":
    main()
