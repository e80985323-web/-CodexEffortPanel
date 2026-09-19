"""Enable the purple MaxEffects on the model-picker reasoning slider for a
CUSTOM (external-API) provider -- version-agnostic and fast.

WHY A PATCH IS NEEDED
---------------------
The purple plasma / particle burst renders only when the slider component's
"isMax" flag is true, and the flag is derived like this:

    <flag> = <lastIndex> > 0 && <selOption>?.isMax === !0
    ...
    <H> = <flag> || <requiresExplicitSelection>   -> gates className: MaxEffects

and  isMax = !isLocked && (isMaximum === !0 || reasoningEffort === 'ultra').

* `isMaximum` is set ONLY from official lane data (`isMaximum: lane === 'pro'`)
  -- `lane` is a ChatGPT TPP model field and a custom catalog has none.
* `ultra` is additionally gated behind a Statsig feature gate and the a6api
  upstream rejects effort=ultra with HTTP 400 anyway.

So for an external-API provider neither branch can ever be true, and the
purple effects can never fire.

THE PATCH
---------
Same-length, byte-for-byte replacement inside the slider module
(webview/assets/impl-*.js). Derived per build, e.g.:

    build 26.915.4065.0   B=I>0&&R?.isMax===!0   ->   B=I>0&&L===I/*pppp*/
    build 26.915.3509.0   V=L>0&&z?.isMax===!0   ->   V=L>0&&R===L/*pppp*/
    build 26.911.7940.0   z=F>0&&L?.isMax===!0   ->   z=F>0&&I===F/*pppp*/
    build 26.908.4834.0   L=N>0&&F?.isMax===!0   ->   L=N>0&&P===N/*pppp*/

In every build the shape is  <flag>=<last> > 0 && <selected>?.isMax===!0  and
the selected INDEX is a separate variable already in scope, so the rewrite
means "the top level is selected".  The literal is DERIVED FROM THE BUNDLE at
run time (minified names change on every app update), so this script survives
updates without hand-editing.

Equal length keeps every asar offset valid, so no repack is needed.

WHY THE CACHE COPY
------------------
The installed app lives in C:\\Program Files\\WindowsApps\\..., which is owned by
TrustedInstaller and is NOT writable by a normal user (verified: PermissionError
on open 'r+b'). Profile Manager -- and this machine's launcher scripts -- already
run the app from a plain copy under
    %LOCALAPPDATA%\\Codex Profile Manager\\WindowsAppsCache\\<pkg>\\app
so the patch is applied there.

PERFORMANCE
-----------
Only the asar header (a few MB) and the ~28 KB slider module are read, and only
20 bytes are written -- so this is fast enough to run on every launch.

USAGE
    python codex-max-effects-patch.py status
    python codex-max-effects-patch.py apply
    python codex-max-effects-patch.py restore
    python codex-max-effects-patch.py apply --asar <path-to-app.asar>
    python codex-max-effects-patch.py ensure-max [--dry-run]
"""
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid

HOME = os.path.expanduser("~")
LOCAL = os.path.join(HOME, "AppData", "Local")
CACHE = os.path.join(LOCAL, "Codex Profile Manager", "WindowsAppsCache")

TAG = ".bak-dsh-maxe"

# The minified condition that computes the slider's "isMax" flag.
SITE_RE = re.compile(
    rb"([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)>0&&([A-Za-z_$][\w$]*)\?\.isMax===!0"
)
# The same condition AFTER patching: `<flag>=<last>>0&&<idx>===<last>/*..*/`
PATCHED_RE = re.compile(
    rb"([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)>0&&([A-Za-z_$][\w$]*)===\2/\*[^*]*\**\*/"
)

# --------------------------------------------------------------------------
# THE SECOND PATCH: MSIX package identity (only needed for the cache copy)
# --------------------------------------------------------------------------
# The bootstrap module runs, inside the `bootstrap-import-main` phase:
#
#     try{ if(a){ let e=OT()?.getCurrentPackageFamily(); ... }
#          if(await sparkle.initialize(), a&&c&&RA(), a||s){ ... }
#          const {runMainAppStartup} = await import("./main-*.js");
#          await runMainAppStartup() }
#     catch(e){ ...destroy windows...; log "Desktop bootstrap failed to start
#               the main app"; Sentry.captureException(e); TaskDialog("ChatGPT
#               failed to start.") }
#
# `a` is the "this process has an MSIX package identity" flag. A plain copy of
# the app under WindowsAppsCache has NO package identity, so
# getCurrentPackageFamily() throws HRESULT 0x80073D54 (APPMODEL_ERROR_NO_PACKAGE
# = 15700, "该进程没有程序包标识符").  That call sits INSIDE the same try as
# runMainAppStartup(), so the throw aborts startup and the app dies with a
# modal "ChatGPT failed to start." dialog before any window appears.
#
#     try{if(<flag>){   ->   try{if(0){        (equal length: 1 byte)
#
# This skips ONLY the `CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY` env assignment.
# The other identity dependency (the sparkle updater) is switched off by the
# launcher with CODEX_SPARKLE_ENABLED=false, which makes
#   shouldIncludeUpdater() === false -> KT.initializeUpdater() returns
#   Promise.resolve() instead of throwing.
#
# The flag is a minified single letter, so it is matched generically -- see
# find_boot_site() below.  A RIGID regex was tried first and proved too brittle:
# the inner code differs between builds, and 26.908/26.911 have no such site at
# all while 26.915 introduced it.  find_boot_site() therefore matches on the
# try/if SHAPE plus the presence of the call, and reports 'absent' (measure it)
# or 'conflict' (refuse) instead of silently failing to match.


def say(s=""):
    sys.stdout.write(str(s).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------
# asar access (header + one entry only)
# --------------------------------------------------------------------------
class Asar:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            head = f.read(16)
            _, self.header_size, _, self.json_len = struct.unpack("<IIII", head[:16])
            f.seek(16)
            self.hdr, _ = json.JSONDecoder().raw_decode(
                f.read(self.json_len).decode("utf-8", "replace")
            )
        self.base = 8 + self.header_size

    def entries(self, node=None, prefix=""):
        node = self.hdr["files"] if node is None else node
        for k, v in node.items():
            pp = prefix + "/" + k if prefix else k
            if "files" in v:
                yield from self.entries(v["files"], pp)
            elif "offset" in v:
                yield pp, v

    def read(self, entry):
        with open(self.path, "rb") as f:
            f.seek(self.base + int(entry["offset"]))
            return f.read(int(entry["size"]))


def candidates():
    """Every app.asar under the WindowsAppsCache, newest version first."""
    out = []
    for p in glob.glob(os.path.join(CACHE, "*", "app", "resources", "app.asar")):
        m = re.search(r"Codex_(\d+)\.(\d+)\.(\d+)\.(\d+)_", p)
        ver = tuple(int(x) for x in m.groups()) if m else (0, 0, 0, 0)
        out.append((ver, p))
    out.sort(reverse=True)
    return out


def find_site(asar):
    """Return (module_entry, module_bytes, info) for the slider module."""
    best = None
    for pp, v in asar.entries():
        if not pp.endswith(".js") or "/impl-" not in pp:
            continue
        try:
            mod = asar.read(v)
        except Exception:
            continue
        old, new, info = derive(mod)
        if isinstance(info, dict) and info.get("patched"):
            return pp, v, mod, None, None, info
        if old is not None:
            best = (pp, v, mod, old, new, info)
            break
    if best:
        return best
    return None, None, None, None, None, "no slider module with an isMax site found"


def derive(mod):
    """Derive (OLD, NEW, info) from the slider module bytes."""
    pm = list(PATCHED_RE.finditer(mod))
    if len(pm) == 1:
        g = pm[0]
        return None, None, {
            "patched": True, "flag": g.group(1).decode(), "last": g.group(2).decode(),
            "idx": g.group(3).decode(), "new": g.group(0).decode(), "offset": g.start(),
        }
    if len(pm) > 1:
        return None, None, f"multiple patched sites ({len(pm)})"

    ms = list(SITE_RE.finditer(mod))
    if len(ms) != 1:
        return None, None, f"expected 1 isMax site, found {len(ms)}"
    m = ms[0]
    flag = m.group(1).decode()
    last = m.group(2).decode()
    sel = m.group(3).decode()
    old = m.group(0)
    off = m.start()

    # The selected INDEX: the code does `<sel>=<array>[<index>]` just before the
    # site, e.g. `...let I=p.length-1,L=Math.min(...),R=p[L],z=...` then the site.
    head = mod[max(0, off - 400):off].decode("utf-8", "replace")
    m2 = re.search(re.escape(sel) + r"=([A-Za-z_$][\w$]*)\[([A-Za-z_$][\w$]*)\]", head)
    if not m2:
        return None, None, "could not find '<sel>=<array>[<index>]' before the site"
    idx = m2.group(2)

    # Both must be declared BEFORE the site, else the rewrite would reference an
    # undefined binding.
    decl = r"\b(?:let|const|var)\b[^;]{0,220}\b"
    if not re.search(decl + re.escape(last) + r"\s*=", head):
        return None, None, f"'{last}' not declared before the site"
    if not (re.search(r"[,;]\s*" + re.escape(idx) + r"\s*=", head) or
            re.search(decl + re.escape(idx) + r"\s*=", head)):
        return None, None, f"'{idx}' not declared before the site"

    core = f"{flag}={last}>0&&{idx}==={last}".encode()
    if len(core) > len(old):
        return None, None, f"new condition ({len(core)}B) > old ({len(old)}B)"
    pad = len(old) - len(core)
    if pad < 4:
        return None, None, f"only {pad}B padding room (need >=4)"
    new = core + b"/*" + b"p" * (pad - 4) + b"*/"
    assert len(new) == len(old), "padding maths broken"

    return old, new, {
        "flag": flag, "last": last, "sel": sel, "idx": idx,
        "old": old.decode(), "new": new.decode(), "offset": off,
    }


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def status(only=None):
    for ver, path in candidates():
        if only and os.path.normcase(path) != os.path.normcase(only):
            continue
        v = ".".join(map(str, ver))
        bak = "yes" if os.path.exists(path + TAG) else "no"
        try:
            a = Asar(path)
            pp, ent, mod, old, new, info = find_site(a)
        except Exception as e:
            say(f"  {v:18} ERROR     {e}")
            continue
        if isinstance(info, dict) and info.get("patched"):
            say(f"  {v:18} PATCHED   backup={bak}  module={pp}")
            say(f"      site : {info['new']}")
            say(f"      flag={info['flag']} last={info['last']} selected={info['idx']}")
        elif old is not None:
            say(f"  {v:18} ORIGINAL  backup={bak}  module={pp}")
            say(f"      site : {info['old']}")
            say(f"      ->   : {info['new']}")
        else:
            say(f"  {v:18} NO-SITE   backup={bak}  ({info})")
    # `max` reachability is a separate concern from the asar, so report it here
    # too -- `status` is what a human runs to answer "is everything still on?".
    cat, cfg = active_catalog()
    say("")
    say("  reasoning-level guard")
    if cat is None:
        say("    catalog : no model_catalog_json in config.toml")
    elif not os.path.isfile(cat):
        say(f"    catalog : MISSING {cat}")
    else:
        try:
            doc = json.loads(open(cat, encoding="utf-8").read())
            ms = doc.get("models") or []
            miss = _missing_max(ms)
            if miss:
                say(f"    catalog : {os.path.basename(cat)} -- "
                    f"{len(miss)}/{len(ms)} model(s) LACK `max` -> {miss}")
                say("              (run `ensure-max` to repair)")
            else:
                say(f"    catalog : {os.path.basename(cat)} -- "
                    f"`max` present on all {len(ms)} model(s)")
        except Exception as e:
            say(f"    catalog : {os.path.basename(cat)} unreadable ({e})")
    try:
        txt = open(cfg, encoding="utf-8", errors="replace").read()
        m = re.search(r"(?m)^\s*enabled-reasoning-efforts\s*=\s*\[(.*?)\]", txt)
        if m:
            has = '"max"' in m.group(1) or "'max'" in m.group(1)
            say(f"    efforts : [desktop] enabled-reasoning-efforts "
                f"{'includes' if has else 'LACKS'} `max`")
        else:
            say("    efforts : no [desktop] enabled-reasoning-efforts key")
    except OSError:
        say("    efforts : config.toml unreadable")

    # The "Ultra" label is the third concern: it lives in a locale bundle, not
    # in config.toml, so it gets its own block.
    say("")
    say("  max label (frontend)")
    loc = active_locale()
    say(f"    locale  : {loc or '(no localeOverride -> app default)'}")
    for ver, path in candidates():
        if only and os.path.normcase(path) != os.path.normcase(only):
            continue
        v = ".".join(map(str, ver))
        try:
            a = Asar(path)
            sites = find_label_sites(a, loc) or find_label_sites(a, None)
        except Exception as e:
            say(f"    {v:18} ERROR {e}")
            continue
        if not sites:
            say(f"    {v:18} no locale bundle carries "
                f"composer.mode.local.reasoning.max.label")
            continue
        got = ", ".join(
            f"{pp.rsplit('/', 1)[-1]} -> {m.group(1).decode('utf-8', 'replace').rstrip()!r}"
            for pp, ent, data, m in sites)
        say(f"    {v:18} {got}")
    return 0


def apply_patch(only=None):
    cands = candidates()
    if only:
        cands = [(v, p) for v, p in cands if os.path.normcase(p) == os.path.normcase(only)]
        if not cands:
            cands = [((0, 0, 0, 0), only)]
    if not cands:
        say("  no app.asar found")
        return 1
    ver, path = cands[0]
    say(f"  target = {'.'.join(map(str, ver)) if ver != (0,0,0,0) else path}")
    say(f"  {path}")
    if not os.path.isfile(path):
        say("  ABORT: file does not exist")
        return 2

    a = Asar(path)
    pp, ent, mod, old, new, info = find_site(a)
    if isinstance(info, dict) and info.get("patched"):
        say(f"  ALREADY PATCHED  module={pp}")
        say(f"  site : {info['new']}")
        return 0
    if old is None:
        say(f"  ABORT: {info}")
        return 2

    say(f"  module = {pp}  ({len(mod)} B)")
    say(f"  derived: {info['old']}  ->  {info['new']}")
    if mod.count(old) != 1:
        say(f"  ABORT: expected 1 occurrence in module, found {mod.count(old)}")
        return 2

    bak = path + TAG
    if not os.path.exists(bak):
        say(f"  backing up -> {os.path.basename(bak)} ...")
        shutil.copy2(path, bak)
    else:
        say(f"  backup exists: {os.path.basename(bak)}")

    abs_off = a.base + int(ent["offset"]) + info["offset"]
    with open(path, "r+b") as f:
        f.seek(abs_off)
        cur = f.read(len(old))
        if cur != old:
            say(f"  ABORT: bytes at {abs_off} are {cur!r}, expected {old!r}")
            return 3
        f.seek(abs_off)
        f.write(new)
        f.flush()
        os.fsync(f.fileno())

    # verify by re-reading through the asar entry
    a2 = Asar(path)
    pp2, ent2, mod2, old2, new2, info2 = find_site(a2)
    ok = isinstance(info2, dict) and info2.get("patched") and new in mod2 and old not in mod2
    say(f"  patched {len(old)} B at abs offset {abs_off}")
    say(f"  size unchanged    = {os.path.getsize(path) == os.path.getsize(bak)}")
    say(f"  re-read module    = {pp2}  NEW present={new in mod2}  OLD gone={old not in mod2}")
    say(f"  RESULT: {'PATCH OK' if ok else 'FAILED'}")
    if ok:
        say()
        say("  >>> restart Codex (launch-codex-default.cmd), open the model picker and")
        say("      drag the reasoning slider to the TOP level -- the purple plasma +")
        say("      particle burst should now fire on the external-API model.")
        say(f"  >>> rollback: python {os.path.basename(__file__)} restore")
    return 0 if ok else 3


# --------------------------------------------------------------------------
# boot patch (package identity) + one-shot "prepare" used by the launcher
# --------------------------------------------------------------------------
# Matching is deliberately SHAPE-TOLERANT.  The exact site changed between
# builds before: 26.908/26.911 have NO such site at all, 26.915 introduced it.
# A rigid regex would therefore silently stop matching on some future build, so
# instead of one exact pattern we look for ANY `try{if(<flag>){` whose block then
# calls getCurrentPackageFamily().  That survives renamed locals and reordered
# code.  The inner code itself never needs to be understood or rewritten.
TRY_IF_RE = re.compile(rb"try\{if\(([A-Za-z_$][\w$]*|0)\)\{")
PKG_CALL = b"getCurrentPackageFamily"
CALL_WINDOW = 260           # how far past `try{if(X){` to look for the call
BOOT_MARK = ".bootless-ok"  # caches the self-test verdict (version-scoped)


def find_boot_site(mod):
    """Locate the bootstrap try-block that calls getCurrentPackageFamily().

    Returns (kind, flag_start, flag_end, flag):
      'unpatched' -- a real identifier flag; patch it to 0
      'patched'   -- already the literal 0
      'absent'    -- no try-block wraps the call: the crash site is gone
      'conflict'  -- ambiguous (>1 candidate) -- do not touch it
    """
    cands = [m for m in TRY_IF_RE.finditer(mod)
             if PKG_CALL in mod[m.end():m.end() + CALL_WINDOW]]
    if len(cands) > 1:
        return "conflict", -1, -1, None
    if not cands:
        return "absent", -1, -1, None
    m = cands[0]
    flag = m.group(1)
    if flag == b"0":
        return "patched", m.start(1), m.end(1), flag
    if not re.fullmatch(rb"[A-Za-z_$][\w$]*", flag):
        return "conflict", -1, -1, None
    return "unpatched", m.start(1), m.end(1), flag


def boot_patch(asar_path):
    """Patch the package-identity call out of the bootstrap module.

    Returns 'ok' | 'already' | 'absent' | 'conflict' | 'verify-failed'.
    """
    a = Asar(asar_path)
    hit = None
    for pp, v in a.entries():
        if re.search(r"bootstrap-[\w-]+\.js$", pp):
            hit = (pp, v, a.read(v))
            break
    if hit is None:
        return "absent"
    pp, v, mod = hit
    kind, s, e, flag = find_boot_site(mod)
    if kind == "patched":
        return "already"
    if kind in ("absent", "conflict"):
        return kind
    abs_off = a.base + int(v["offset"]) + s
    with open(asar_path, "r+b") as f:
        f.seek(abs_off)
        cur = f.read(1)
        if cur != flag[:1]:
            return f"conflict({cur!r})"
        f.seek(abs_off)
        f.write(b"0")
        f.flush()
        os.fsync(f.fileno())
    b = Asar(asar_path)
    for pp2, v2 in b.entries():
        if pp2 == pp:
            k2 = find_boot_site(b.read(v2))[0]
            return "ok" if k2 == "patched" else "verify-failed"
    return "verify-failed"


def selftest_boot(exe, timeout_s=66):
    """Empirically answer: can this copy start at all?

    Used only when find_boot_site() reports 'absent' -- i.e. the crash site is
    not in the bundle, so whether the copy boots is genuinely UNKNOWN.  Rather
    than guess (either way could be wrong), measure it: launch with a throwaway
    profile so a running instance is not disturbed, wait for a main window of
    THIS pid, then kill it.
    """
    pid = None
    prof = os.path.join(tempfile.gettempdir(), "codex-boottest-" + uuid.uuid4().hex[:8])
    try:
        os.makedirs(prof, exist_ok=True)
        env = dict(os.environ)
        env["CODEX_SPARKLE_ENABLED"] = "false"
        with open(prof + ".log", "wb") as log:
            p = subprocess.Popen([exe, "--user-data-dir=" + prof],
                                 cwd=os.path.dirname(exe),
                                 stdout=log, stderr=log, env=env)
        pid = p.pid
        say(f"    self-test: launched pid {pid} with a throwaway profile")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(3)
            if p.poll() is not None:
                say(f"    self-test: the copy exited on its own (rc={p.returncode})")
                return False
            q = ("(Get-Process -Id %d -ErrorAction SilentlyContinue | "
                 "Where-Object {$_.MainWindowTitle} | Measure-Object).Count" % pid)
            out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", q],
                                 capture_output=True, text=True, timeout=90).stdout.strip()
            if out.isdigit() and int(out) > 0:
                say("    self-test: a main window appeared -> the copy CAN boot")
                return True
        say(f"    self-test: no main window within {timeout_s}s")
        return False
    except Exception as exc:                       # never let a probe crash the launch
        say(f"    self-test: probe failed ({exc}) -- assuming it cannot boot")
        return False
    finally:
        if pid is not None:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=90)
            except Exception:
                pass
        time.sleep(1)
        shutil.rmtree(prof, ignore_errors=True)
        try:
            os.remove(prof + ".log")
        except OSError:
            pass


def copy_tree(src, dst, verbose=say):
    """Byte-level copy (the source may carry the EFS attribute, so plain
    shutil.copytree/robocopy can silently write nothing)."""
    made = 0
    failed = []
    for dp, dn, fn in os.walk(src):
        rel = os.path.relpath(dp, src)
        tgt = dst if rel == "." else os.path.join(dst, rel)
        try:
            os.makedirs(tgt, exist_ok=True)
        except OSError as e:
            failed.append((tgt, str(e)))
            continue
        for f in fn:
            s = os.path.join(dp, f)
            t = os.path.join(tgt, f)
            try:
                if os.path.isfile(t) and os.path.getsize(t) == os.path.getsize(s):
                    continue
                with open(s, "rb") as fi, open(t, "wb") as fo:
                    shutil.copyfileobj(fi, fo, 4 << 20)
                made += 1
                if made % 400 == 0:
                    verbose(f"    ... {made} files")
            except OSError as e:
                failed.append((s, str(e)))
    return made, failed


def count_files(root):
    n = 0
    for dp, dn, fn in os.walk(root):
        n += len(fn)
    return n


# --------------------------------------------------------------------------
# THE THIRD GUARD: keep `max` reachable in the model catalog
# --------------------------------------------------------------------------
# WHY THIS EXISTS (measured, not assumed)
# ---------------------------------------
# `max` is not something CC Switch knows about.  Its own source of truth is
#
#     cc-switch.db -> providers.settings_config.modelCatalog.models
#         [{"model": "gpt-6-astra", "displayName": "gpt-6-astra"}, ...]
#
# which stores NO reasoning levels at all, and the catalog it regenerates on a
# takeover therefore declares only low/medium/high/xhigh.  A takeover silently
# costs the desktop slider its top level -- the slider simply cannot reach `max`
# any more, which is exactly the state this machine was in before `max` was
# added by hand.
#
# There is no column in that database for a reasoning level, and the program
# that writes it is third-party (patching its bundle would break on its next
# update), so there is nothing to fix at the source.  The durable fix is to
# RE-ASSERT `max` here, on every launch, through the launcher -- instead of
# hoping nothing rewrites the file.
#
# Fail-open by design, same as the asar patch: if anything about this goes
# wrong we log it and leave the file alone.  The worst case is "max is missing",
# never a broken config.toml.

CODEX_HOME = os.path.join(HOME, ".codex")
MAX_TAG = ".bak-dsh-max"

# Matches the wording already used for `max` in this catalog, so a repair is
# indistinguishable from the original hand edit.
MAX_LEVEL = {"description": "Maximum reasoning depth for the hardest problems",
             "effort": "max"}


def _atomic_write(path, data):
    """Replace a file in one step, so a crash can never leave it half-written."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".dsh-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _dump_like(doc, raw):
    """Serialise `doc` with the SAME formatting conventions as the bytes `raw`.

    Preserving the file's own indent, line ending and trailing-newline state is
    what keeps a repair auditable: only the entries that had to be added change,
    instead of every line in the file.  (Measured: the live catalog is CRLF and
    does NOT end with a newline, so a naive dump would rewrite all 338 lines.)

    The invariant this buys, and that the test asserts: for a file already in the
    expected shape, `_dump_like(json.loads(raw), raw) == raw` -- byte for byte.
    """
    m = re.search(rb"\n([ \t]+)\"", raw)
    if m:
        body = json.dumps(doc, indent=len(m.group(1)), ensure_ascii=False)
    else:
        body = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    eol = "\r\n" if b"\r\n" in raw else "\n"
    if raw.endswith(b"\n"):
        body += eol
    if eol != "\n":
        body = body.replace("\n", eol)
    return body.encode("utf-8")


def active_catalog():
    """(catalog path, config.toml path) -- the catalog Codex actually reads.

    Resolved from config.toml rather than hardcoded, so this follows the user if
    the catalog file is renamed or another provider is switched in.
    """
    cfg = os.path.join(CODEX_HOME, "config.toml")
    if not os.path.isfile(cfg):
        return None, cfg
    try:
        text = open(cfg, encoding="utf-8", errors="replace").read()
    except OSError:
        return None, cfg
    m = re.search(r'(?m)^\s*model_catalog_json\s*=\s*"([^"]+)"', text)
    if not m:
        return None, cfg
    name = m.group(1)
    path = name if os.path.isabs(name) else os.path.join(CODEX_HOME, name)
    return path, cfg


def _missing_max(models):
    """Slugs of models that do NOT declare a `max` reasoning level."""
    out = []
    for m in models:
        if not isinstance(m, dict):
            continue
        slug = m.get("slug") or m.get("id") or "?"
        lv = m.get("supported_reasoning_levels")
        if not isinstance(lv, list) or not any(
                isinstance(x, dict) and x.get("effort") == "max" for x in lv):
            out.append(slug)
    return out


def ensure_catalog_max(dry=False):
    """Re-assert `max` in the active catalog.  Returns 1 if it changed anything."""
    cat, _ = active_catalog()
    if cat is None:
        say("  catalog: no model_catalog_json in config.toml -- nothing to guard")
        return 0
    say(f"  catalog: {cat}")
    if not os.path.isfile(cat):
        say("  catalog: file missing -- skipping (Codex falls back to its own list)")
        return 0
    try:
        raw = open(cat, "rb").read()
    except OSError as e:
        say(f"  catalog: unreadable ({e}) -- skipping")
        return 0
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        say(f"  catalog: NOT valid JSON ({e}) -- refusing to touch it")
        return 0

    models = doc.get("models")
    if not isinstance(models, list) or not models:
        say("  catalog: no models[] -- skipping")
        return 0

    missing = _missing_max(models)
    if not missing:
        say(f"  catalog: OK -- all {len(models)} model(s) already offer `max`")
        return 0
    if dry:
        say(f"  catalog: WOULD add `max` to {len(missing)} model(s): {missing}")
        return 0

    # Preserve the file's own formatting: re-dump with the indent, line ending
    # and trailing-newline convention it already uses, so a repair changes ONLY
    # the added entries.  See _dump_like().
    for mm in models:
        if not isinstance(mm, dict):
            continue
        lv = mm.get("supported_reasoning_levels")
        if not isinstance(lv, list):
            lv = []
            mm["supported_reasoning_levels"] = lv
        if not any(isinstance(x, dict) and x.get("effort") == "max" for x in lv):
            lv.append(dict(MAX_LEVEL))

    bak = cat + MAX_TAG
    if not os.path.isfile(bak):
        shutil.copy2(cat, bak)
        say(f"  catalog: backup -> {os.path.basename(bak)}")
    try:
        _atomic_write(cat, _dump_like(doc, raw))
    except OSError as e:
        say(f"  catalog: write failed ({e}) -- left unchanged")
        return 0

    # verify by re-reading from disk
    try:
        back = json.loads(open(cat, encoding="utf-8").read())
        still = _missing_max(back.get("models") or [])
    except Exception as e:
        still = ["<re-read failed: %s>" % e]
    if still:
        if os.path.isfile(bak):
            shutil.copy2(bak, cat)
        say(f"  catalog: VERIFY FAILED ({still}) -> restored the backup")
        return 0
    for s in missing:
        say(f"  catalog: added `max` to {s}")
    say(f"  catalog: repaired and verified ({len(missing)} model(s) gained `max`)")
    return 1


def ensure_efforts_whitelist(dry=False):
    """Add "max" to [desktop] enabled-reasoning-efforts, if that key exists.

    Strictly additive and surgical: one line, one list item.  If the key is
    absent we do NOT invent it -- that would change semantics we have not
    measured -- we only say so.
    """
    _, cfg = active_catalog()
    if not os.path.isfile(cfg):
        say(f"  efforts: {cfg} missing -- skipping")
        return 0
    try:
        text = open(cfg, encoding="utf-8", errors="replace").read()
    except OSError as e:
        say(f"  efforts: unreadable ({e}) -- skipping")
        return 0

    lines = text.splitlines(keepends=True)
    section = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s.strip("[]").strip()
            continue
        if section != "desktop":
            continue
        m = re.match(r'^(\s*enabled-reasoning-efforts\s*=\s*\[)(.*?)(\]\s*)$', ln)
        if not m:
            continue
        items = [x.strip() for x in m.group(2).split(",") if x.strip()]
        if any(x.strip('"\'') == "max" for x in items):
            say(f"  efforts: OK -- [desktop] already whitelists `max` "
                f"({len(items)} levels)")
            return 0
        if dry:
            say(f"  efforts: WOULD add `max` to [desktop] enabled-reasoning-efforts")
            return 0
        items.append('"max"')
        lines[i] = m.group(1) + ", ".join(items) + m.group(3)
        new = "".join(lines)

        # verify the edit is still valid TOML AND actually contains max
        try:
            import tomllib
            parsed = tomllib.loads(new)
            got = parsed.get("desktop", {}).get("enabled-reasoning-efforts")
            if not isinstance(got, list) or "max" not in got:
                raise ValueError(f"re-parse gave {got!r}")
        except Exception as e:
            say(f"  efforts: refusing to write -- the edit did not verify ({e})")
            return 0

        bak = cfg + MAX_TAG
        if not os.path.isfile(bak):
            shutil.copy2(cfg, bak)
            say(f"  efforts: backup -> {os.path.basename(bak)}")
        try:
            _atomic_write(cfg, new.encode("utf-8"))
        except OSError as e:
            say(f"  efforts: write failed ({e}) -- left unchanged")
            return 0
        say(f"  efforts: added `max` to [desktop] enabled-reasoning-efforts "
            f"({len(items)} levels) and verified the TOML")
        return 1
    say("  efforts: no [desktop] enabled-reasoning-efforts key -- left alone")
    return 0


def ensure_max(dry=False):
    """Run both `max` guards.  Never raises -- the launch must not be at risk."""
    changed = 0
    try:
        changed += ensure_catalog_max(dry=dry)
    except Exception as e:
        say(f"  catalog: guard error ({type(e).__name__}: {e}) -- left alone")
    try:
        changed += ensure_efforts_whitelist(dry=dry)
    except Exception as e:
        say(f"  efforts: guard error ({type(e).__name__}: {e}) -- left alone")
    return changed


# --------------------------------------------------------------------------
# THE FOURTH PATCH: show the `max` reasoning level as "Ultra" (display only)
# --------------------------------------------------------------------------
# WHY THIS EXISTS
# ---------------
# The model picker renders the effort label from ONE i18n key per level:
#
#     composer.mode.local.reasoning.<effort>.label
#
# and the value comes from the active locale bundle -- for a Chinese UI,
# `webview/assets/zh-CN-<hash>.js`, which contains
#
#     "composer.mode.local.reasoning.max.label":`最高`
#     "composer.mode.local.reasoning.ultra.label":`Ultra`
#
# So every model that offers `max` displays "最高".  This patch makes that one
# level READ as "Ultra".  It is a pure display change: no behaviour, no config,
# no other string, and `max` is still the effort actually sent upstream.
#
# The rewrite is in place and EXACTLY the same byte length -- the label is
# padded with trailing spaces, which a text node does not render -- so every
# entry offset in the asar header stays valid.  Same trick as the purple patch.
# It is idempotent, and fail-open: any problem is logged and the bundle is left
# alone, so the worst case is "the label still says 最高".
#
# LIMIT, measured rather than assumed: a locale whose own word for `max` is
# shorter than 5 bytes (English "Max", 3 B) cannot hold "Ultra" at the same
# length, so those bundles are reported and skipped.  Chinese 最高 (6 B) fits.

LABEL_KEY_RE = re.compile(
    rb'"composer\.mode\.local\.reasoning\.max\.label":`([^`]*)`')
LABEL_NEW = b"Ultra"

# webview/assets/<locale>-<hash>.js  -- e.g. zh-CN-6b83584f4688.js
LOCALE_BUNDLE_RE = re.compile(
    r"(^|/)webview/assets/[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*-[0-9a-f]{8,}\.js$")


def active_locale():
    """The UI locale Codex will load, from config.toml's localeOverride."""
    cfg = os.path.join(CODEX_HOME, "config.toml")
    try:
        text = open(cfg, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r'(?m)^\s*localeOverride\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def find_label_sites(asar, locale=None):
    """[(entry_path, entry, data, match)] for bundles holding the max label.

    `locale` narrows the scan to one locale bundle, which is what keeps this
    affordable on the launch path: steady state reads a single ~1.4 MB entry.
    """
    out = []
    for pp, v in asar.entries():
        if not LOCALE_BUNDLE_RE.search(pp):
            continue
        if locale and ("/" + locale + "-") not in pp:
            continue
        try:
            data = asar.read(v)
        except Exception:
            continue
        ms = list(LABEL_KEY_RE.finditer(data))
        if len(ms) == 1:
            out.append((pp, v, data, ms[0]))
    return out


def label_patch(asar_path):
    """Make the `max` effort level read as "Ultra".  Equal length, idempotent.

    Returns "ok" | "already" | "absent" | "failed: <why>".  Never raises, and
    never changes the file's size, so the asar header stays valid.
    """
    try:
        a = Asar(asar_path)
        loc = active_locale()
        sites = find_label_sites(a, loc)
        if not sites and loc:
            # localeOverride may not correspond to a bundle name -- widen.
            sites = find_label_sites(a, None)
    except Exception as e:
        return f"failed: {type(e).__name__}: {e}"

    if not sites:
        return "absent"

    todo = []
    for pp, v, data, m in sites:
        val = m.group(1)
        if val.startswith(LABEL_NEW):
            continue
        if len(val) < len(LABEL_NEW):
            say(f"    label: {pp.rsplit('/', 1)[-1]} word "
                f"{val.decode('utf-8', 'replace')!r} is only {len(val)}B -- cannot "
                f"hold {LABEL_NEW.decode()} at the same length, skipped")
            continue
        todo.append((pp, v, m.start(1), val,
                     LABEL_NEW + b" " * (len(val) - len(LABEL_NEW))))

    if not todo:
        return "already"

    # Pre-flight: every target must hold exactly `val` right now, so a partial
    # write cannot happen.
    try:
        with open(asar_path, "rb") as f:
            for pp, v, off, val, _new in todo:
                f.seek(a.base + int(v["offset"]) + off)
                if f.read(len(val)) != val:
                    return f"failed: pre-flight mismatch in {pp}"
    except Exception as e:
        return f"failed: pre-flight {type(e).__name__}: {e}"

    try:
        with open(asar_path, "r+b") as f:
            for pp, v, off, val, new in todo:
                f.seek(a.base + int(v["offset"]) + off)
                f.write(new)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        return f"failed: write {type(e).__name__}: {e}"

    # Verify by re-reading through the asar header.
    try:
        a2 = Asar(asar_path)
        by_path = {pp2: v2 for pp2, v2 in a2.entries()}
        for pp, v, off, val, new in todo:
            ent = by_path.get(pp)
            ms = list(LABEL_KEY_RE.finditer(a2.read(ent))) if ent else []
            if len(ms) != 1 or ms[0].group(1) != new:
                return f"failed: verification of {pp} failed"
    except Exception as e:
        return f"failed: verify {type(e).__name__}: {e}"

    for pp, v, off, val, new in todo:
        say(f"    label: {pp.rsplit('/', 1)[-1]}  "
            f"{val.decode('utf-8', 'replace')!r} -> "
            f"{new.decode('utf-8', 'replace').rstrip()!r}")
    return "ok"


def prepare(app_dir, out_path):
    """Make sure a writable, patched copy exists; write the exe path to out.

    Contract: `PATCHED` is returned ONLY when the copy is complete, matches the
    installed build, and the purple + package-identity patches verified.  The
    "Ultra" label patch is cosmetic and best-effort on top of that.  Anything
    else falls back to the installed app, so the worst case is "no purple
    effect" -- never a window that fails to start.
    """
    pkg = os.path.basename(os.path.dirname(app_dir))
    dst = os.path.join(CACHE, pkg, "app")
    cache_exe = os.path.join(dst, "ChatGPT.exe")
    cache_asar = os.path.join(dst, "resources", "app.asar")
    plain_exe = os.path.join(app_dir, "ChatGPT.exe")
    plain_asar = os.path.join(app_dir, "resources", "app.asar")

    # Keep `max` reachable.  Independent of the asar work below, and fail-open:
    # ensure_max() swallows its own errors, so this can never cost a launch.
    say("  -- reasoning-level guard --")
    ensure_max()
    say("  -- app copy --")

    def finish(exe, mode):
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(exe + "\n" + mode + "\n")
        say(f"  mode   = {mode}")
        say(f"  launch = {exe}")
        return 0

    def plain(reason):
        say(f"  FALLBACK: {reason}")
        say("  -> launching the installed app, UNPATCHED (no purple effect, but it starts)")
        return finish(plain_exe, "PLAIN")

    if not os.path.isfile(plain_exe):
        say(f"  ABORT: installed exe not found: {plain_exe}")
        return finish(plain_exe, "MISSING")

    # ---- is the existing copy usable for THIS build? ----------------------
    # The package dir name embeds the version, so a new Codex build lands in a
    # brand-new cache dir.  The size checks additionally catch a same-named but
    # different copy.  The patches are equal-length, so sizes MUST match.
    def why_unusable():
        if not os.path.isfile(cache_exe):
            return "no exe in the copy"
        if not os.path.isfile(cache_asar):
            return "no app.asar in the copy"
        if os.path.isfile(plain_asar):
            a, b = os.path.getsize(plain_asar), os.path.getsize(cache_asar)
            if a != b:
                return f"app.asar size {b} != installed {a} (different build)"
        a, b = os.path.getsize(plain_exe), os.path.getsize(cache_exe)
        if a != b:
            return f"ChatGPT.exe size {b} != installed {a} (different build)"
        return None

    reason = why_unusable()
    if reason is None:
        say(f"  writable copy present for {pkg} and matches this build")
    else:
        say(f"  (re)building the writable copy: {reason}")
        say(f"    src = {app_dir}")
        say(f"    dst = {dst}")
        if os.path.isdir(dst):
            say("    removing the previous copy")
            shutil.rmtree(dst, ignore_errors=True)
        made, failed = copy_tree(app_dir, dst)
        say(f"    copied {made} files, {len(failed)} errors")
        for s, e in failed[:5]:
            say(f"      ! {s}: {e}")
        a, b = count_files(app_dir), count_files(dst)
        say(f"    completeness: source {a} files, copy {b} files")
        reason = why_unusable()
        if reason is not None:
            return plain(f"the new copy is not usable: {reason}")

    # ---- patch 1: the purple max-effects flag ----------------------------
    say("  patching (purple max-effects + package identity)")
    rc = apply_patch(cache_asar)
    if rc != 0:
        return plain("the purple max-effects patch failed")

    # ---- patch 2: MSIX package identity (required, or the copy cannot boot)
    bp = boot_patch(cache_asar)
    say(f"  boot patch: {bp}")

    # ---- patch 3: the `max` effort level reads as "Ultra" (display only) --
    # Cosmetic, so it must never cost a launch: a failure is reported and the
    # purple + package-identity patches still stand.
    lp = label_patch(cache_asar)
    say(f"  label patch: {lp}")

    mark = cache_asar + BOOT_MARK
    keep = bp in ("ok", "already")

    if bp == "absent":
        # The static crash site is not in this bundle.  Two very different
        # possibilities, and guessing is unsafe in both directions:
        #   * the app no longer needs the package family -> the copy boots fine
        #   * the throw moved somewhere we cannot see  -> the copy will NOT boot
        # So MEASURE it.  The verdict is cached inside the version-scoped cache
        # dir, so this costs one probe per Codex build, not one per launch.
        verdict = ""
        if os.path.isfile(mark):
            try:
                verdict = open(mark, encoding="utf-8").read().strip()
            except OSError:
                verdict = ""
        if verdict == "ok":
            say("  boot patch: not needed for this build (verified on an earlier run)")
            keep = True
        elif verdict == "no":
            say("  boot patch: this copy cannot boot without it (verified earlier)")
        else:
            say("  boot patch: no site found -- running a boot self-test (once per build)")
            keep = selftest_boot(cache_exe)
            try:
                with open(mark, "w", encoding="utf-8") as f:
                    f.write("ok\n" if keep else "no\n")
            except OSError:
                pass
            if keep:
                say("  boot patch: the copy BOOTS without it -> keeping the purple patch")

    if not keep:
        # Never leave a half-patched copy on disk: restore the pristine asar.
        # The INSTALL asar is the authoritative original, so prefer it and use
        # the backup only if the install copy is unavailable.
        src = None
        if os.path.isfile(plain_asar) and \
                os.path.getsize(plain_asar) == os.path.getsize(cache_asar):
            src = plain_asar
        elif os.path.isfile(cache_asar + TAG):
            src = cache_asar + TAG
        if src is not None:
            shutil.copy2(src, cache_asar)
            say("  (reverted the purple patch so the copy is not left half-patched)")
        return plain(f"the package-identity patch could not be applied ({bp})")

    if not os.path.isfile(cache_exe):
        return plain("no exe in the writable copy")
    return finish(cache_exe, "PATCHED")


def restore(only=None):
    """Revert patched asars from their `.bak-dsh-maxe` backups.

    `only` limits the restore to ONE build (a version string like "26.915.3509.0"
    or a full app.asar path).  This matters: a bare `restore` reverts EVERY build
    that has a backup -- including the one the launcher is currently using, which
    silently costs you the purple effect until the next launch re-patches it.

    The backup is deleted ONLY after the restore verified as the pristine
    original; if verification fails the backup is kept, so a failed restore is
    always retryable.
    """
    if only is None:
        say("  NOTE: no --only given -> restoring EVERY build that has a backup.")
        say("        (use --only <version|path> to revert just one build)")
    hit = 0
    for ver, path in candidates():
        v = ".".join(map(str, ver))
        if only is not None and os.path.normcase(path) != os.path.normcase(only) \
                and v != only and not v.startswith(only):
            continue
        hit += 1
        bak = path + TAG
        if not os.path.exists(bak):
            say(f"  {v:18}: no backup, skipped")
            continue
        shutil.copy2(bak, path)
        ok = False
        try:
            a = Asar(path)
            pp, ent, mod, old, new, info = find_site(a)
            ok = old is not None
            st = ("restored to ORIGINAL" if ok
                  else f"restored but NOT verified ({info})")
        except Exception as e:
            st = f"restored but NOT verified ({type(e).__name__}: {e})"
        say(f"  {v:18}: {st}")
        if ok:
            os.remove(bak)
            say(f"    backup removed: {os.path.basename(bak)}")
        else:
            say(f"    backup KEPT (restore unverified, retry is safe): "
                f"{os.path.basename(bak)}")
    if only is not None and hit == 0:
        say(f"  no build matched --only {only!r}")
        say("  known builds: " + ", ".join(
            ".".join(map(str, v)) for v, _ in candidates()))
        return 4
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "status"

    def opt(name):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return None

    only = opt("--asar") or opt("--only")

    if cmd == "prepare":
        app_dir = opt("--app")
        out_path = opt("--out")
        if not app_dir or not out_path:
            say("usage: prepare --app <installed app dir> --out <file>")
            sys.exit(9)
        say("=" * 74)
        say("Codex max-effects patch -- prepare")
        say("=" * 74)
        sys.exit(prepare(app_dir, out_path) or 0)

    if cmd == "ensure-max":
        say("=" * 74)
        say("Codex max-effects patch -- ensure-max")
        say("=" * 74)
        ensure_max(dry="--dry-run" in args)
        sys.exit(0)

    if cmd not in ("status", "apply", "restore"):
        say("usage: status|apply|restore [--asar <path>] [--only <version|path>]"
            " | prepare --app <dir> --out <file>"
            " | ensure-max [--dry-run]")
        sys.exit(9)
    say("=" * 74)
    say(f"Codex max-effects patch -- {cmd}")
    say("=" * 74)
    if cmd == "status":
        rc = status(only)
    elif cmd == "apply":
        rc = apply_patch(only)
    else:
        rc = restore(only)
    sys.exit(rc or 0)
