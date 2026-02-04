import os
import time
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

# ================= 参数 =================
DEBUG = "--debug" in sys.argv
CPU_PRINT_THRESHOLD = 30.0
TAB_BATCH_SIZE = 5
POLL_INTERVAL = 3
SERVER_REFRESH_INTERVAL = 3600  # 1 小时

warn_count = {}

# ================= 配置 =================
BASE_URL = "https://vf.ciallo.ee"
SERVERS_URL = f"{BASE_URL}/admin/servers"
LOG_ROOT = "logs"

EMA_TARGET_HOURS = 24
EMA_N = int((EMA_TARGET_HOURS * 3600) / POLL_INTERVAL)
EMA_ALPHA = 2 / (EMA_N + 1)

# ================= 状态 =================
cpu_ema = {}

# ================= 工具 =================
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def log_cpu(sid, cpu):
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(LOG_ROOT, sid)
    ensure_dir(path)
    with open(os.path.join(path, f"{date}.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {cpu}\n")

def maybe_print(sid, cpu):
    ts = datetime.now().strftime("%H:%M:%S")

    if cpu >= CPU_PRINT_THRESHOLD:
        cnt = warn_count.get(sid, 0) + 1
        warn_count[sid] = cnt
        print(f"[{ts}] SID={sid} CPU={cpu:.2f}% !!! 第{cnt}次警告")
    elif DEBUG:
        print(f"[{ts}] SID={sid} CPU={cpu:.2f}%")

# ================= 登录 =================
def wait_for_login(page):
    print("[*] 请手动登录 Virtfusion")
    page.goto(BASE_URL)
    page.wait_for_url("**/admin/dashboard", timeout=0)
    print("[+] 登录完成")

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
    try:
        page.wait_for_selector(
            "#cpuGauge svg.gauge text.value-text",
            timeout=30_000
        )
        txt = page.text_content("#cpuGauge svg.gauge text.value-text")
        if not txt:
            raise RuntimeError("empty cpu text")

        return float(txt.replace("%", "").strip())
    except Exception as e:
        print(f"[WARN] SID={sid} 抓取失败: {e}")
        return None

# ================= 主循环 =================
def main():
    ensure_dir(LOG_ROOT)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context()
        ctx.add_init_script(
            "Object.defineProperty(document,'visibilityState',{value:'visible'})"
        )

        page = ctx.new_page()
        wait_for_login(page)

        ids = get_all_server_ids(page)
        last_server_refresh_ts = time.time()

        print("[*] 开始监控（Ctrl+C 退出）")

        while True:
            now = time.time()

            # ===== 每 1 小时刷新一次服务器列表 =====
            if now - last_server_refresh_ts >= SERVER_REFRESH_INTERVAL:
                try:
                    print("[*] 刷新服务器列表中 …")
                    ids = get_all_server_ids(page)
                    last_server_refresh_ts = now
                except Exception as e:
                    print(f"[WARN] 刷新服务器列表失败: {e}")

            for i in range(0, len(ids), TAB_BATCH_SIZE):
                batch = ids[i:i + TAB_BATCH_SIZE]

                pages = {}
                for sid in batch:
                    p = ctx.new_page()
                    try:
                        p.goto(
                            f"{BASE_URL}/admin/servers/{sid}",
                            wait_until="domcontentloaded",
                            timeout=15_000
                        )
                        pages[sid] = p
                    except Exception as e:
                        print(f"[WARN] SID={sid} 页面打开失败，跳过: {e}")
                        try:
                            p.close()
                        except:
                            pass


                for sid, p in pages.items():
                    cpu = fetch_cpu(p, sid)
                    if cpu is None:
                        continue

                    prev = cpu_ema.get(sid, cpu)
                    cpu_ema[sid] = EMA_ALPHA * cpu + (1 - EMA_ALPHA) * prev

                    log_cpu(sid, cpu)
                    maybe_print(sid, cpu)

                for p in pages.values():
                    try:
                        p.close()
                    except:
                        pass

            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
