import os
import sys
import subprocess
import time

SCRIPT = os.path.join(os.path.dirname(__file__), "vf.py")

def prompt_credentials():
    email = input("VirtFusion Email: ")
    pwd = input_password_masked("VirtFusion Password: ")
    return email, pwd


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
                s = ch.decode("utf-8")
            except:
                continue
            pwd += s
            print("*", end="", flush=True)
    return pwd

def run_once(email, pwd):
    env = os.environ.copy()
    env["VF_EMAIL"] = email
    env["VF_PASSWORD"] = pwd
    # use same python executable
    proc = subprocess.run([sys.executable, SCRIPT], env=env)
    return proc.returncode

def main():
    print("Launcher: 启动 vf.py，崩溃后会重新启动并重新输入凭据。按 Ctrl+C 退出。")
    try:
        while True:
            email, pwd = prompt_credentials()
            print("Starting vf.py...")
            rc = run_once(email, pwd)
            print(f"vf.py exited with code {rc}. Restarting and re-prompting credentials...")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nLauncher: 已退出。")

if __name__ == '__main__':
    main()
