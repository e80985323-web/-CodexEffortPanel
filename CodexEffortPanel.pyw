# -*- coding: utf-8 -*-
"""Codex 思考强度 —— 一键管理面板

双击即用。把「思考强度相关」的改动整合到一个窗口里：

  1. 模型目录的 max 档位       让滑块能拉到最高档
  2. config.toml 的强度白名单   让 max 出现在选项里
  3. 紫光特效补丁 (asar)        最高档的 蓝 -> 紫 + 粒子
  4. Ultra 标签补丁 (asar)      「最高」显示为 Ultra
  5. 包身份补丁 (asar)          让打过补丁的副本能启动（支撑 3、4）

引擎复用同目录的 codex-max-effects-patch.py —— 不复制一份逻辑，
所以 Codex 更新后只要引擎还能修，这个面板就跟着还能修。

改动都是外观 / 配置层面：不碰供应商、不碰中继、不改模型路由。
"""

import io
import os
import queue
import re
import subprocess
import sys
import threading
import traceback

# --------------------------------------------------------------------------
# pythonw.exe 下 sys.stdout / sys.stderr 是 None，任何 print() 都会炸。
# 引擎大量用 print 汇报进度，所以必须先把它们换成能收的管道。
# --------------------------------------------------------------------------
_ENGINE_OUT = io.StringIO()
if sys.stdout is None:
    sys.stdout = _ENGINE_OUT
if sys.stderr is None:
    sys.stderr = _ENGINE_OUT

import tkinter as tk                                    # noqa: E402
from tkinter import messagebox, ttk                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE_FILE = os.path.join(HERE, "codex-max-effects-patch.py")
LAUNCHER = os.path.join(HERE, "launch-codex-default.cmd")
LOGFILE = os.path.join(os.environ.get("TEMP", HERE), "codex-effort-panel.log")

BG = "#faf9f7"
CARD = "#ffffff"
INK = "#1f1f1f"
MUTE = "#6b6b6b"
OK_C = "#1a7f37"
BAD_C = "#c0392b"
WARN_C = "#b7791f"
ACCENT = "#6b4ee6"


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------
def load_engine():
    """把同目录的补丁脚本当模块载入。返回 (module, None) 或 (None, 错误文本)。"""
    if not os.path.isfile(ENGINE_FILE):
        return None, "找不到引擎文件：\n" + ENGINE_FILE
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("codex_effort_engine", ENGINE_FILE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["codex_effort_engine"] = mod
        spec.loader.exec_module(mod)
        return mod, None
    except Exception:
        return None, traceback.format_exc()


def installed_app_dir():
    """已安装的 Codex app 目录（只读的那份）。

    用 Get-AppxPackage 的通配符查询，而不是写死包名 —— 包名里有方括号，
    而且每次更新版本号都会变。
    """
    q = ("(Get-AppxPackage -Name '*Codex*' | "
         "Sort-Object Version -Descending | "
         "Select-Object -First 1).InstallLocation")
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", q],
                             capture_output=True, text=True, timeout=60)
        loc = (out.stdout or "").strip()
        if loc:
            app = os.path.join(loc, "app")
            if os.path.isdir(app):
                return app, None
            return None, "找到包目录但里面没有 app\\ ：\n" + loc
    except Exception as exc:
        return None, "查询已安装的 Codex 失败：%s" % exc
    return None, "没有找到已安装的 Codex 包（Get-AppxPackage -Name '*Codex*' 无结果）"


def launcher_proxy():
    """从启动器里读代理地址，保持和命令行启动完全一致。"""
    try:
        txt = open(LAUNCHER, encoding="utf-8", errors="replace").read()
        m = re.search(r'(?m)^\s*set\s+"?PROXY=([^"\r\n]+)"?', txt)
        if m:
            return m.group(1).strip()
    except OSError:
        pass
    return None


def codex_running():
    """返回正在运行的 Codex 进程数（0 = 没在跑）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ChatGPT.exe", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=0x08000000,
        ).stdout or ""
    except Exception:
        return 0
    return out.lower().count("chatgpt.exe")


def newest_asar(eng):
    cands = eng.candidates()
    return cands[0] if cands else None


# --------------------------------------------------------------------------
# 只读状态检查
# --------------------------------------------------------------------------
def check_catalog(eng):
    cat, _cfg = eng.active_catalog()
    if cat is None:
        return False, "config.toml 里没有 model_catalog_json", ""
    if not os.path.isfile(cat):
        return False, "目录文件不存在：%s" % os.path.basename(cat), ""
    try:
        import json
        doc = json.loads(open(cat, encoding="utf-8").read())
    except Exception as exc:
        return False, "目录读不出来（%s）" % exc, ""
    models = doc.get("models") or []
    miss = eng._missing_max(models)
    if miss:
        return False, "%d/%d 个模型缺 max：%s" % (len(miss), len(models), ", ".join(miss)), cat
    return True, "%d/%d 个模型都有 max 档" % (len(models), len(models)), cat


def check_efforts(eng):
    _cat, cfg = eng.active_catalog()
    try:
        txt = open(cfg, encoding="utf-8", errors="replace").read()
    except OSError:
        return False, "config.toml 读不出来", cfg
    m = re.search(r"(?m)^\s*enabled-reasoning-efforts\s*=\s*\[(.*?)\]", txt, re.S)
    if not m:
        return False, "没有 [desktop] enabled-reasoning-efforts 这一项", cfg
    body = m.group(1)
    if '"max"' in body or "'max'" in body:
        n = len([x for x in body.split(",") if x.strip()])
        return True, "白名单含 max（共 %d 档）" % n, cfg
    return False, "白名单里没有 max", cfg


def check_asar_patches(eng):
    """(紫光, Ultra标签, 包身份) 三项的只读检查。"""
    hit = newest_asar(eng)
    if hit is None:
        miss = (False, "没有可写副本（先点「一键生效」）", None)
        return (miss, miss, miss)
    ver, path = hit
    vtxt = ".".join(map(str, ver))
    a = eng.Asar(path)

    # -- 紫光特效 ---------------------------------------------------------
    try:
        _pp, _v, _mod, _old, _new, info = eng.find_site(a)
        if isinstance(info, dict) and info.get("patched"):
            purple = (True, "已打：%s" % info.get("new", ""), vtxt)
        elif _old is not None:
            purple = (False, "未打（找到补丁点）", vtxt)
        else:
            purple = (False, "找不到补丁点：%s" % info, vtxt)
    except Exception as exc:
        purple = (False, "检查失败：%s" % exc, vtxt)

    # -- Ultra 标签 -------------------------------------------------------
    try:
        loc = eng.active_locale() or "(默认)"
        sites = eng.find_label_sites(a, eng.active_locale()) or eng.find_label_sites(a, None)
        if not sites:
            label = (False, "没有语言包带 max 标签", loc)
        else:
            vals = [m.group(1) for _p, _e, _d, m in sites]
            if all(x.startswith(b"Ultra") for x in vals):
                label = (True, "%s -> Ultra" % loc, loc)
            else:
                shown = ", ".join(x.decode("utf-8", "replace").rstrip() for x in vals)
                label = (False, "%s -> %s" % (loc, shown), loc)
    except Exception as exc:
        label = (False, "检查失败：%s" % exc, None)

    # -- 包身份 -----------------------------------------------------------
    try:
        boot = (False, "找不到 bootstrap 模块", vtxt)
        for pp, v in a.entries():
            if re.search(r"bootstrap-[\w-]+\.js$", pp):
                kind, _s, _e, _f = eng.find_boot_site(a.read(v))
                if kind == "patched":
                    boot = (True, "已打（包身份调用已跳过）", vtxt)
                elif kind == "absent":
                    mark = os.path.join(path, ".bootless-ok")
                    try:
                        mv = open(mark, encoding="utf-8").read().strip()
                    except OSError:
                        mv = ""
                    boot = (True, "这个版本没有该崩溃点，无需补丁%s"
                            % ("（自检=%s）" % mv if mv else ""), vtxt)
                elif kind == "unpatched":
                    boot = (False, "未打", vtxt)
                else:
                    boot = (False, "冲突（%s）—— 不动它" % kind, vtxt)
                break
    except Exception as exc:
        boot = (False, "检查失败：%s" % exc, vtxt)

    return (purple, label, boot)


def gather_status(eng):
    rows = []
    ok, det, _ = check_catalog(eng)
    rows.append(("1. 模型目录 max 档位", ok, det, "让滑块能拉到最高档"))
    ok, det, _ = check_efforts(eng)
    rows.append(("2. 强度白名单", ok, det, "[desktop] enabled-reasoning-efforts"))
    p, l, b = check_asar_patches(eng)
    rows.append(("3. 紫光特效", p[0], p[1], "最高档 蓝->紫 + 粒子"))
    rows.append(("4. Ultra 标签", l[0], l[1], "「最高」显示为 Ultra"))
    rows.append(("5. 包身份补丁", b[0], b[1], "支撑 3、4（打过补丁的副本能启动）"))
    return rows


# --------------------------------------------------------------------------
# 界面
# --------------------------------------------------------------------------
class Panel:
    def __init__(self, root):
        self.root = root
        self.eng = None
        self.eng_err = None
        self.q = queue.Queue()
        self.busy = False
        self.rows = []
        self.build()
        self.root.after(80, self.load_engine_async)
        self.root.after(120, self.pump)

    # -- 布局 -------------------------------------------------------------
    def build(self):
        r = self.root
        r.title("Codex 思考强度")
        r.configure(bg=BG)
        r.minsize(660, 560)
        w, h = 760, 620
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, max(0, (sh - h) // 2 - 40)))

        head = tk.Frame(r, bg=BG)
        head.pack(fill="x", padx=20, pady=(18, 6))
        tk.Label(head, text="Codex 思考强度", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(head, text="外接 API 模式 · 让思考强度能拉到最高档，并显示为 Ultra",
                 bg=BG, fg=MUTE, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 0))

        self.card = tk.Frame(r, bg=CARD, highlightthickness=1,
                             highlightbackground="#e6e2dc")
        self.card.pack(fill="x", padx=20, pady=(10, 10))

        self.rowframe = tk.Frame(self.card, bg=CARD)
        self.rowframe.pack(fill="x", padx=14, pady=12)
        for i in range(5):
            self.rows.append(self.make_row(self.rowframe, i))

        bar = tk.Frame(r, bg=BG)
        bar.pack(fill="x", padx=20, pady=(0, 10))
        self.b_refresh = self.mkbtn(bar, "刷新状态", self.on_refresh, primary=False)
        self.b_apply = self.mkbtn(bar, "一键生效", self.on_apply, primary=True)
        self.b_launch = self.mkbtn(bar, "启动 Codex", self.on_launch, primary=False)
        self.b_restore = self.mkbtn(bar, "回退", self.on_restore, primary=False)
        for b in (self.b_refresh, self.b_apply, self.b_launch, self.b_restore):
            b.pack(side="left", padx=(0, 8))

        logwrap = tk.Frame(r, bg=BG)
        logwrap.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        tk.Label(logwrap, text="日志", bg=BG, fg=MUTE,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        box = tk.Frame(logwrap, bg="#f4f2ee", highlightthickness=1,
                       highlightbackground="#e6e2dc")
        box.pack(fill="both", expand=True, pady=(3, 0))
        self.log = tk.Text(box, height=10, wrap="word", bg="#f4f2ee", fg="#333",
                           relief="flat", font=("Consolas", 9), padx=8, pady=6)
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.configure(state="disabled")

        self.status = tk.Label(r, text="正在加载引擎 ...", bg=BG, fg=MUTE,
                               anchor="w", font=("Microsoft YaHei UI", 9))
        self.status.pack(fill="x", padx=20, pady=(0, 14))

    def make_row(self, parent, i):
        f = tk.Frame(parent, bg=CARD)
        f.grid(row=i, column=0, sticky="ew", pady=3)
        parent.columnconfigure(0, weight=1)
        mark = tk.Label(f, text="…", bg=CARD, fg=MUTE, width=2,
                        font=("Microsoft YaHei UI", 12, "bold"))
        mark.pack(side="left")
        name = tk.Label(f, text="", bg=CARD, fg=INK, width=20, anchor="w",
                        font=("Microsoft YaHei UI", 10))
        name.pack(side="left")
        det = tk.Label(f, text="", bg=CARD, fg=MUTE, anchor="w",
                       font=("Microsoft YaHei UI", 9))
        det.pack(side="left", fill="x", expand=True)
        return (mark, name, det)

    def mkbtn(self, parent, text, cmd, primary=False):
        return tk.Button(parent, text=text, command=cmd,
                         bg=(ACCENT if primary else "#ffffff"),
                         fg=("#ffffff" if primary else INK),
                         activebackground=("#5a3fd4" if primary else "#f0eee9"),
                         activeforeground=("#ffffff" if primary else INK),
                         relief="flat", bd=0, padx=16, pady=7, cursor="hand2",
                         font=("Microsoft YaHei UI", 9, "bold" if primary else "normal"))

    # -- 日志 / 状态 -------------------------------------------------------
    def say(self, s=""):
        text = str(s)
        self.q.put(("log", text))
        # 同时落盘一份，出问题时可以直接看文件（面板关掉也不丢）
        try:
            with open(LOGFILE, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass

    def setstatus(self, s):
        self.q.put(("status", str(s)))

    def setrows(self, rows):
        self.q.put(("rows", rows))

    def setbusy(self, b):
        self.q.put(("busy", b))

    def pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", payload + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "status":
                    self.status.configure(text=payload)
                elif kind == "busy":
                    self.busy = payload
                    st = ("disabled" if payload else "normal")
                    for b in (self.b_refresh, self.b_apply, self.b_launch, self.b_restore):
                        b.configure(state=st)
                elif kind == "rows":
                    for i, (nm, ok, det, hint) in enumerate(payload[:5]):
                        mark, name, d = self.rows[i]
                        mark.configure(text=("✓" if ok else "✕"),
                                       fg=(OK_C if ok else BAD_C))
                        name.configure(text=nm)
                        d.configure(text=det, fg=(MUTE if ok else BAD_C))
                        _ = hint
                elif kind == "done":
                    self.setbusy(False)
        except queue.Empty:
            pass
        self.root.after(120, self.pump)

    # -- 异步任务 ---------------------------------------------------------
    def run(self, fn, label):
        if self.busy:
            return
        self.setbusy(True)
        self.setstatus(label + " ...")

        def worker():
            try:
                fn()
            except Exception:
                self.say("!! 出错：\n" + traceback.format_exc())
                self.setstatus("出错（见日志）")
            finally:
                self.q.put(("done", None))
        threading.Thread(target=worker, daemon=True).start()

    # -- 引擎加载 ---------------------------------------------------------
    def load_engine_async(self):
        def work():
            self.say("=" * 60)
            self.say("Codex 思考强度 · 管理面板")
            self.say("=" * 60)
            self.say("引擎：" + ENGINE_FILE)
            self.say("日志文件：" + LOGFILE)
            self.eng, self.eng_err = load_engine()
            if self.eng is None:
                self.say("!! 引擎加载失败")
                self.say(self.eng_err)
                self.setstatus("引擎加载失败")
                self.setrows([("引擎", False, "加载失败，见日志", "")])
                return
            self.say("引擎已加载。")
            self.refresh_now()
        self.run(work, "加载引擎")

    def refresh_now(self):
        if self.eng is None:
            return
        self.say("")
        self.say("-- 检查状态 --")
        rows = gather_status(self.eng)
        for nm, ok, det, _hint in rows:
            self.say("  %s %-18s %s" % ("✓" if ok else "✕", nm, det))
        self.setrows(rows)
        n_ok = sum(1 for _n, ok, _d, _h in rows if ok)
        if n_ok == len(rows):
            self.setstatus("全部生效 —— 滑块可以拉到最高档，标签显示 Ultra")
        else:
            self.setstatus("%d/%d 项已生效，点「一键生效」补齐" % (n_ok, len(rows)))

    def on_refresh(self):
        self.run(self.refresh_now, "检查状态")

    # -- 一键生效 ---------------------------------------------------------
    def on_apply(self):
        if self.eng is None:
            messagebox.showerror("引擎未加载", self.eng_err or "未知错误")
            return

        def work():
            eng = self.eng
            app, err = installed_app_dir()
            if app is None:
                self.say("!! " + err)
                self.setstatus("找不到已安装的 Codex")
                return
            self.say("")
            self.say("-- 一键生效 --")
            self.say("已安装的 Codex：" + app)

            # 引擎会把「要启动哪个 exe / 什么模式」写进这个临时文件
            import tempfile
            fd, out = tempfile.mkstemp(prefix="codexpanel_", suffix=".txt")
            os.close(fd)
            try:
                rc = eng.prepare(app, out)
                self.say("prepare 返回码：%s" % rc)
                try:
                    lines = open(out, encoding="utf-8").read().splitlines()
                except OSError:
                    lines = []
                if len(lines) >= 2:
                    self.say("启动模式：" + lines[1])
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass

            self.refresh_now()
        self.run(work, "应用全部改动")

    # -- 启动 -------------------------------------------------------------
    def on_launch(self):
        if self.eng is None:
            messagebox.showerror("引擎未加载", self.eng_err or "未知错误")
            return

        def work():
            eng = self.eng
            app, err = installed_app_dir()
            if app is None:
                self.say("!! " + err)
                self.setstatus("找不到已安装的 Codex")
                return
            self.say("")
            self.say("-- 启动 Codex --")

            # Codex 是单实例程序：已经在跑的时候再启动，新进程会立刻退出，
            # 窗口不会变化 —— 先把这件事说清楚，免得以为「点了没反应」。
            running = codex_running()
            if running:
                self.say("检测到 Codex 已经在运行（%d 个进程）。" % running)
                self.say("它是单实例程序：新启动会被转发到已有窗口，")
                self.say("窗口内容不会变化。要让补丁生效，请先完全退出 Codex。")

            import tempfile
            fd, out = tempfile.mkstemp(prefix="codexpanel_", suffix=".txt")
            os.close(fd)
            exe, mode = None, "PLAIN"
            try:
                eng.prepare(app, out)
                lines = open(out, encoding="utf-8").read().splitlines()
                if lines:
                    exe = lines[0].strip()
                if len(lines) >= 2:
                    mode = lines[1].strip()
            except Exception:
                self.say("!! prepare 失败：\n" + traceback.format_exc())
            finally:
                try:
                    os.remove(out)
                except OSError:
                    pass

            if not exe or not os.path.isfile(exe):
                exe = os.path.join(app, "ChatGPT.exe")
            self.say("启动模式：%s" % mode)
            self.say("可执行文件：" + exe)

            env = dict(os.environ)
            if mode.upper() == "PATCHED":
                env["CODEX_SPARKLE_ENABLED"] = "false"
            else:
                env.pop("CODEX_SPARKLE_ENABLED", None)
            cmd = [exe]
            prox = launcher_proxy()
            if prox:
                cmd.append("--proxy-server=" + prox)
                self.say("代理：" + prox)
            try:
                subprocess.Popen(cmd, cwd=os.path.dirname(exe), env=env)
                if running:
                    self.say("已发起启动（会被已有窗口接收）。")
                    self.setstatus("Codex 已在运行，未重启")
                else:
                    self.say("已启动。")
                    self.setstatus("已启动 Codex（模式 %s）" % mode)
            except Exception:
                self.say("!! 启动失败：\n" + traceback.format_exc())
                self.setstatus("启动失败（见日志）")
        self.run(work, "启动 Codex")

    # -- 回退 -------------------------------------------------------------
    def on_restore(self):
        if self.eng is None:
            return
        hit = newest_asar(self.eng)
        if hit is None:
            messagebox.showinfo("没有可回退的副本", "还没有生成可写副本。")
            return
        ver, _p = hit
        vtxt = ".".join(map(str, ver))
        if not messagebox.askyesno(
                "确认回退",
                "把 %s 的可写副本还原成原始文件？\n\n"
                "紫光特效和 Ultra 标签会消失，下次启动会重新打上。\n"
                "只影响这一个版本。" % vtxt):
            return

        def work():
            self.say("")
            self.say("-- 回退 %s --" % vtxt)
            rc = self.eng.restore(only=vtxt)
            self.say("restore 返回码：%s" % rc)
            self.refresh_now()
        self.run(work, "回退")


def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    Panel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
