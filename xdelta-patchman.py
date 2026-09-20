#!/usr/bin/env python3
"""
Xdelta Patch Manager - one file, fully interactive, no arguments needed.

    python3 xdelta_patch_manager.py        (Windows: python xdelta_patch_manager.py)

Supported: Windows, macOS, Linux and Android (Termux), 32-bit and 64-bit.

What it does
------------
1. Detects the OS / CPU / package manager and shows where paths start
   (user folder, drive or filesystem root, shared storage on Android).
2. Finds a *working* Xdelta 3 (it is actually test-run: version check plus a
   small encode/decode round trip), or installs one:
       a) with the OS package manager, then
       b) from the official project, https://github.com/jmacd/xdelta
          - prebuilt release binary where the project publishes one, else
          - the release source tarball, built locally with CMake.
3. Installs/updates the OS documentation viewer (mandoc on Linux, `man` on
   Termux; built in on macOS; `more` on Windows).
4. Creates or applies patches from full or relative paths.
5. Shows your selections, asks to confirm, shows the exact command, asks again.

Checksums: Xdelta's "-n" (disable checksum) is permanently refused, in every
spelling (-n, -vn, -fn ...). Long options are refused as well, because Xdelta 3
documents none. Only a whitelist of known-safe switches is accepted.

Requires Python 3.7+ and only uses the standard library.
"""

from __future__ import annotations

import bz2
import gzip
import hashlib
import json
import lzma
import os
import platform
import re
import shlex
import shutil
import stat
import string
import struct
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info < (3, 7):  # pragma: no cover
    sys.exit("This script needs Python 3.7 or newer.")

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
GITHUB_REPO = "jmacd/xdelta"
GITHUB_API_LATEST = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO
GITHUB_LATEST_PAGE = "https://github.com/%s/releases/latest" % GITHUB_REPO
DOCS_URL = "https://jmacd.github.io/xdelta/commandline/"
USER_AGENT = "xdelta-patch-manager/2.0"

# Everything this script downloads or builds lives here (never system-wide).
MANAGED_DIR = Path.home() / ".xdelta-patch-manager"
MANAGED_BIN = MANAGED_DIR / "bin"
MANAGED_MAN = MANAGED_DIR / "share" / "man"
MANAGED_META = MANAGED_DIR / "installed.json"

FORBIDDEN_MESSAGE = (
    "The -n switch (disable checksum) is permanently refused by this tool. "
    "Disabling checksums is what silently breaks modified ROMs."
)

# Package names per package manager. Several candidates are tried in order.
XDELTA_PACKAGES = {
    "apt-get": ["xdelta3"],
    "dnf": ["xdelta", "xdelta3"],  # Fedora/RHEL call it "xdelta" (it ships the xdelta3 binary)
    "yum": ["xdelta", "xdelta3"],
    "pacman": ["xdelta3"],
    "zypper": ["xdelta3"],
    "apk": ["xdelta3"],
    "xbps-install": ["xdelta3"],
    "brew": ["xdelta"],  # the Homebrew formula "xdelta" provides the xdelta3 binary
    "pkg": ["xdelta3"],  # Termux
    "choco": ["xdelta3"],
    "winget": ["xdelta3"],  # best effort; falls through to GitHub if the ID does not exist
}

DOC_PACKAGES = {
    "apt-get": ["mandoc"],
    "dnf": ["mandoc"],
    "yum": ["mandoc"],
    "pacman": ["mandoc"],
    "zypper": ["mandoc"],
    "apk": ["mandoc"],
    "xbps-install": ["mandoc"],
    "pkg": ["man", "mandoc"],  # Termux
}

# Only needed when Xdelta has to be built from the official source tarball.
BUILD_PACKAGES = {
    "apt-get": ["cmake", "g++", "make", "git", "liblzma-dev"],
    "dnf": ["cmake", "gcc-c++", "make", "git", "xz-devel"],
    "yum": ["cmake", "gcc-c++", "make", "git", "xz-devel"],
    "pacman": ["cmake", "gcc", "make", "git", "xz"],
    "zypper": ["cmake", "gcc-c++", "make", "git", "xz-devel"],
    "apk": ["cmake", "build-base", "git", "xz-dev"],
    "xbps-install": ["cmake", "gcc", "make", "git", "xz-devel"],
    "brew": ["cmake", "git", "xz"],
    "pkg": ["cmake", "clang", "make", "git", "liblzma"],  # Termux
}

PACKAGE_MANAGERS = {
    "apt-get": {
        "admin": True,
        "refresh": ["apt-get", "update"],
        "install": ["apt-get", "install", "-y"],
        "upgrade": ["apt-get", "install", "--only-upgrade", "-y"],
    },
    "dnf": {
        "admin": True,
        "install": ["dnf", "install", "-y"],
        "upgrade": ["dnf", "upgrade", "-y"],
    },
    "yum": {
        "admin": True,
        "install": ["yum", "install", "-y"],
        "upgrade": ["yum", "update", "-y"],
    },
    "pacman": {
        "admin": True,
        "install": ["pacman", "-S", "--needed", "--noconfirm"],
        # Arch does not support partial upgrades, so no single-package upgrade.
        "upgrade": None,
    },
    "zypper": {
        "admin": True,
        "refresh": ["zypper", "--non-interactive", "refresh"],
        "install": ["zypper", "--non-interactive", "install"],
        "upgrade": ["zypper", "--non-interactive", "update"],
    },
    "apk": {
        "admin": True,
        "refresh": ["apk", "update"],
        "install": ["apk", "add"],
        "upgrade": ["apk", "add", "-u"],
    },
    "xbps-install": {
        "admin": True,
        "install": ["xbps-install", "-Sy"],
        "upgrade": ["xbps-install", "-Suy"],
    },
    "brew": {
        "admin": False,
        "refresh": ["brew", "update"],
        "install": ["brew", "install"],
        "upgrade": ["brew", "upgrade"],
    },
    "pkg": {  # Termux
        "admin": False,
        "refresh": ["pkg", "update", "-y"],
        "install": ["pkg", "install", "-y"],
        "upgrade": ["pkg", "install", "-y"],  # re-installing an installed package upgrades it
    },
    "choco": {
        "admin": False,
        "install": ["choco", "install", "-y"],
        "upgrade": ["choco", "upgrade", "-y"],
    },
    "winget": {
        "admin": False,
        "install": [
            "winget", "install", "--exact", "--accept-source-agreements",
            "--accept-package-agreements", "--id",
        ],
        "upgrade": [
            "winget", "upgrade", "--exact", "--accept-source-agreements",
            "--accept-package-agreements", "--id",
        ],
    },
}

PM_ORDER = {
    "windows": ["winget", "choco"],
    "macos": ["brew"],
    "linux": ["apt-get", "dnf", "yum", "pacman", "zypper", "apk", "xbps-install"],
    "android": ["pkg", "apt-get"],
}

# winget exit codes that mean "nothing to do", not "failed".
WINGET_NOOP_CODES = {0x8A15002B, 0x8A150061}


# --------------------------------------------------------------------------
# Small UI helpers (ASCII only, so legacy Windows consoles never choke)
# --------------------------------------------------------------------------

def banner(title: str) -> None:
    print("=" * 72)
    print(title)
    print("=" * 72)


def section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def confirm(prompt: str, default: bool | None = None) -> bool:
    hint = "[Y/n]" if default is True else "[y/N]" if default is False else "[y/n]"
    while True:
        answer = input("%s %s: " % (prompt, hint)).strip().lower()
        if not answer and default is not None:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


def choose(title: str, options: list, default: str | None = None) -> str:
    """options: list of (key, label). Returns the chosen key."""
    print(title)
    for key, label in options:
        print("  %s. %s" % (key, label))
    valid = {key.lower(): key for key, _ in options}
    while True:
        suffix = " [%s]" % default if default else ""
        raw = input("Choice%s: " % suffix).strip().lower()
        if not raw and default:
            return default
        if raw in valid:
            return valid[raw]
        print("Please choose one of: " + ", ".join(k for k, _ in options))


def human_size(num: int) -> str:
    size = float(num)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return ("%d %s" % (size, unit)) if unit == "bytes" else ("%.1f %s" % (size, unit))
        size /= 1024.0
    return "%d bytes" % num


def format_command(cmd: list) -> str:
    """Render a command the way the user could paste it into their own shell."""
    if os.name == "nt":
        return subprocess.list2cmdline([str(c) for c in cmd])
    return " ".join(shlex.quote(str(c)) for c in cmd)


# --------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------

@dataclass
class Platform:
    os: str            # windows | macos | linux | android
    label: str
    arch: str          # x86_64 | x86 | arm64 | arm32 | <raw>
    raw_arch: str
    py_bits: int
    pm: str | None = None

    @property
    def bits(self) -> int:
        if self.arch in ("x86_64", "arm64"):
            return 64
        if self.arch in ("x86", "arm32"):
            return 32
        return self.py_bits

    @property
    def exe_name(self) -> str:
        return "xdelta3.exe" if self.os == "windows" else "xdelta3"


def is_termux() -> bool:
    if os.environ.get("TERMUX_VERSION"):
        return True
    if "com.termux" in os.environ.get("PREFIX", ""):
        return True
    if Path("/data/data/com.termux/files/usr").is_dir():
        return True
    # Android without the usual Termux variables (but not a proot Linux distro).
    return bool(os.environ.get("ANDROID_ROOT")) and not Path("/etc/os-release").exists()


def normalize_arch(raw: str) -> str:
    m = raw.strip().lower()
    if m in ("amd64", "x86_64", "x64", "x86-64"):
        return "x86_64"
    if m == "x86" or re.fullmatch(r"i[3-6]86", m):
        return "x86"
    if m in ("arm64", "aarch64", "aarch64_be", "armv8b"):
        return "arm64"
    # armv8l = 32-bit userland running on a 64-bit ARM kernel (common on Android).
    if m.startswith("arm"):
        return "arm32"
    return m or "unknown"


def detect_platform() -> Platform:
    sysname = platform.system().lower()
    if sys.platform == "android" or sysname == "android":
        os_name = "android"
    elif sysname == "windows" or sys.platform == "win32":
        os_name = "windows"
    elif sysname == "darwin":
        os_name = "macos"
    elif sysname == "linux":
        os_name = "android" if is_termux() else "linux"
    else:
        raise RuntimeError("Unsupported operating system: %s" % platform.system())

    if os_name == "windows":
        # Under 32-bit Python on 64-bit Windows, PROCESSOR_ARCHITEW6432 holds the real CPU.
        raw = (os.environ.get("PROCESSOR_ARCHITEW6432")
               or os.environ.get("PROCESSOR_ARCHITECTURE")
               or platform.machine())
    else:
        raw = platform.machine() or getattr(os.uname(), "machine", "")
    arch = normalize_arch(raw)

    if os_name == "macos" and arch == "x86_64":
        # Python running under Rosetta reports x86_64 on Apple Silicon.
        try:
            out = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, timeout=10).stdout
            if out.strip() == "1":
                arch = "arm64"
        except (OSError, subprocess.SubprocessError):
            pass

    labels = {"windows": "Windows", "macos": "macOS", "linux": "Linux",
              "android": "Android (Termux)"}
    return Platform(os=os_name, label=labels[os_name], arch=arch, raw_arch=raw,
                    py_bits=struct.calcsize("P") * 8)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def detect_pm(p: Platform) -> str | None:
    for name in PM_ORDER[p.os]:
        if command_exists(name):
            return name
    return None


def extra_path_dirs(p: Platform) -> list:
    dirs = []
    home = Path.home()
    if p.os == "windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "WinGet" / "Links")
        choco = os.environ.get("ChocolateyInstall")
        if choco:
            dirs.append(Path(choco) / "bin")
        dirs.append(Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "chocolatey" / "bin")
        dirs.append(home / "scoop" / "shims")
    elif p.os == "macos":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin")]
    elif p.os == "android":
        prefix = os.environ.get("PREFIX")
        if prefix:
            dirs.append(Path(prefix) / "bin")
    else:
        dirs += [Path("/usr/local/bin"), Path("/usr/bin"), home / ".local" / "bin",
                 Path("/home/linuxbrew/.linuxbrew/bin")]
    dirs.append(MANAGED_BIN)  # last: system copies win ties
    return dirs


def augment_path(p: Platform) -> None:
    """Make freshly installed tools visible to this process (PATH is otherwise stale)."""
    parts = [x for x in os.environ.get("PATH", "").split(os.pathsep) if x]
    if p.os == "windows":
        try:
            import winreg  # type: ignore
            keys = [(winreg.HKEY_LOCAL_MACHINE,
                     r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                    (winreg.HKEY_CURRENT_USER, "Environment")]
            for root, sub in keys:
                try:
                    with winreg.OpenKey(root, sub) as key:
                        value, _ = winreg.QueryValueEx(key, "Path")
                        for item in os.path.expandvars(value).split(os.pathsep):
                            if item and item not in parts:
                                parts.append(item)
                except OSError:
                    continue
        except ImportError:
            pass
    known = {os.path.normcase(x) for x in parts}
    for d in extra_path_dirs(p):
        if d.is_dir() and os.path.normcase(str(d)) not in known:
            parts.append(str(d))
            known.add(os.path.normcase(str(d)))
    os.environ["PATH"] = os.pathsep.join(parts)


def run(cmd: list, *, capture: bool = False, timeout: float | None = None,
        env: dict | None = None, cwd: str | None = None,
        input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    """Run a program without a shell. With capture=True, stderr is merged into stdout."""
    if input_bytes is not None:
        return subprocess.run(cmd, input=input_bytes, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout, env=env, cwd=cwd)
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True, errors="replace", timeout=timeout, env=env, cwd=cwd,
    )


def safe_rmtree(path: Path) -> None:
    def handler(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=handler)
    else:
        shutil.rmtree(str(path), onerror=handler)


# --------------------------------------------------------------------------
# Where can the user type paths? (root / user directory for this OS)
# --------------------------------------------------------------------------

def windows_drives() -> list:
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        return ["%s:\\" % chr(65 + i) for i in range(26) if mask & (1 << i)]
    except Exception:
        return ["%s:\\" % c for c in string.ascii_uppercase if os.path.exists("%s:\\" % c)]


def shared_storage_readable() -> bool:
    try:
        os.listdir("/storage/emulated/0")
        return True
    except OSError:
        return False


def show_directories(p: Platform) -> None:
    home = Path.home()
    section("Where paths start on this system (%s)" % p.label)
    if p.os == "windows":
        print("  Your user folder : %s   (also %%USERPROFILE%% or ~)" % home)
        print("  Drive roots      : %s" % "  ".join(windows_drives()))
        example = str(home / "Downloads" / "game.gba")
        print("  Full path example: %s" % example)
        print("  Network paths    : \\\\server\\share\\folder\\game.gba")
    elif p.os == "macos":
        print("  Root of the disk : /")
        print("  Your user folder : %s   (also ~ or $HOME)" % home)
        print("  External drives  : /Volumes/<name>")
        print("  Full path example: %s" % (home / "Downloads" / "game.gba"))
    elif p.os == "android":
        prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
        print("  Termux home      : %s   (also ~ or $HOME)" % home)
        print("  Termux root      : %s   (its 'usr' folder)" % prefix)
        print("  Phone storage    : /storage/emulated/0   (Downloads = /storage/emulated/0/Download)")
        print("                     after 'termux-setup-storage' also ~/storage/shared and ~/storage/downloads")
        print("  Full path example: /storage/emulated/0/Download/game.gba")
    else:
        print("  Root of the disk : /")
        print("  Your user folder : %s   (also ~ or $HOME)" % home)
        print("  Removable media  : /media/<user>/<name> or /mnt/<name>")
        print("  Full path example: %s" % (home / "Downloads" / "game.gba"))
    print()
    print("  Current folder   : %s" % Path.cwd())
    print("  Script folder    : %s" % SCRIPT_DIR)
    print()
    print("  You can type a full path or a relative one. Relative paths are looked up in the")
    print("  current folder first, then next to this script. Quotes, drag-and-drop and")
    print("  ~ / environment variables are understood.")


def offer_android_storage_setup(p: Platform) -> None:
    if p.os != "android" or shared_storage_readable():
        return
    print()
    print("Termux cannot see your phone's shared storage (Downloads, etc.) yet.")
    if command_exists("termux-setup-storage") and confirm(
            "Run 'termux-setup-storage' now? Android will show a permission dialog", default=True):
        try:
            run(["termux-setup-storage"])
        except OSError as exc:
            print("Could not run termux-setup-storage: %s" % exc)
            return
        input("Press Enter once you have answered the permission dialog... ")
        print("Shared storage is now %s." % ("readable" if shared_storage_readable()
                                            else "still not readable (you can retry later)"))


# --------------------------------------------------------------------------
# Package-manager layer
# --------------------------------------------------------------------------

def with_privileges(cmd: list) -> list | None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return cmd
    for tool in ("sudo", "doas"):
        if command_exists(tool):
            return [tool] + cmd
    return None


def pm_commands(p: Platform, pm: str, action: str, pkgs: list) -> list | None:
    spec = PACKAGE_MANAGERS[pm]
    if spec.get(action) is None:
        return None
    if pm == "winget":
        cmds = [list(spec[action]) + [pkgs[0]]]
    else:
        cmds = []
        if spec.get("refresh"):
            cmds.append(list(spec["refresh"]))
        cmds.append(list(spec[action]) + list(pkgs))
    if spec.get("admin") and p.os != "android":
        wrapped = []
        for cmd in cmds:
            w = with_privileges(cmd)
            if w is None:
                print("This needs administrator rights, but neither sudo nor doas exists. "
                      "Re-run the script as root to use %s." % pm)
                return None
            wrapped.append(w)
        cmds = wrapped
    return cmds


def run_commands(pm: str, cmds: list) -> bool:
    for cmd in cmds:
        print("Running: %s" % format_command(cmd))
        try:
            rc = run(cmd).returncode
        except OSError as exc:
            print("Could not start it: %s" % exc)
            return False
        ok = rc == 0 or (pm == "winget" and (rc & 0xFFFFFFFF) in WINGET_NOOP_CODES)
        if not ok:
            print("The command failed (exit status %s)." % rc)
            if pm == "pkg":
                print("Termux hint: if downloads fail, run 'termux-change-repo' to pick a working "
                      "mirror, then 'pkg update'.")
            return False
    return True


def pm_run(p: Platform, action: str, pkgs: list) -> bool | None:
    """True = success, False = failed, None = not possible with this package manager."""
    if not p.pm:
        return None
    cmds = pm_commands(p, p.pm, action, pkgs)
    if cmds is None:
        return None
    return run_commands(p.pm, cmds)


# --------------------------------------------------------------------------
# Finding and verifying Xdelta
# --------------------------------------------------------------------------

@dataclass
class XdeltaInfo:
    path: str
    version: str = "unknown"
    managed: bool = False
    lzma: bool | None = None

    @property
    def version_tuple(self) -> tuple:
        return version_tuple(self.version)


def version_tuple(text: str) -> tuple:
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def identify_xdelta(path: str):
    """Return (XdeltaInfo | None, reason)."""
    try:
        out = run([path, "-V"], capture=True, timeout=60).stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "it could not be run (%s)" % exc
    m = re.search(r"xdelta\s*(?:3\s*)?version\s+(\d+(?:\.\d+)*)", out, re.I)
    if m:
        version = m.group(1)
        if version_tuple(version)[0] < 3:
            return None, "it is Xdelta %s, which has an incompatible command line" % version
        return XdeltaInfo(path=path, version=version), ""
    try:
        out = run([path, "-h"], capture=True, timeout=60).stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "it could not be run (%s)" % exc
    if re.search(r"usage:\s*xdelta3", out, re.I):
        return XdeltaInfo(path=path), ""
    return None, "its output was not recognised as Xdelta 3"


def self_test(exe: str):
    """Real encode/decode round trip. Returns (works, lzma_works, message)."""
    tmp = Path(tempfile.mkdtemp(prefix="xdelta-selftest-"))
    try:
        src = tmp / "src.bin"
        tgt = tmp / "tgt.bin"
        data = bytearray((i * 31 + (i >> 3)) % 251 for i in range(64 * 1024))
        src.write_bytes(bytes(data))
        new = bytearray(data)
        new[1000:1010] = b"0123456789"
        new[40000:40000] = b"INSERTED-BYTES"
        tgt.write_bytes(bytes(new))
        expected = bytes(new)

        def roundtrip(tag: str, extra: list):
            patch = tmp / ("%s.xd" % tag)
            out = tmp / ("%s.out" % tag)
            enc = run([exe] + extra + ["-e", "-f", "-s", str(src), str(tgt), str(patch)],
                      capture=True, timeout=120)
            if enc.returncode != 0:
                return False, "encode failed (exit %s): %s" % (enc.returncode, (enc.stdout or "").strip())
            dec = run([exe, "-d", "-f", "-s", str(src), str(patch), str(out)],
                      capture=True, timeout=120)
            if dec.returncode != 0:
                return False, "decode failed (exit %s): %s" % (dec.returncode, (dec.stdout or "").strip())
            if not out.exists() or out.read_bytes() != expected:
                return False, "decoded output differs from the original"
            return True, ""

        try:
            ok, msg = roundtrip("plain", [])
            if not ok:
                return False, None, msg
            lzma_ok, _ = roundtrip("lzma", ["-S", "lzma"])
            return True, lzma_ok, ""
        except (OSError, subprocess.SubprocessError) as exc:
            return False, None, "could not run (%s)" % exc
    finally:
        safe_rmtree(tmp)


def find_working_xdelta(p: Platform, quiet: bool = False) -> XdeltaInfo | None:
    """Locate every Xdelta 3, test them, and return the newest one that really works."""
    candidates = []
    for name in ("xdelta3", "xdelta"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    managed = MANAGED_BIN / p.exe_name
    if managed.is_file():
        candidates.append(str(managed))

    seen = set()
    identified = []
    for cand in candidates:
        key = os.path.normcase(os.path.realpath(cand))
        if key in seen:
            continue
        seen.add(key)
        info, reason = identify_xdelta(cand)
        if info is None:
            if not quiet:
                print("Ignoring %s: %s." % (cand, reason))
            continue
        info.managed = os.path.normcase(os.path.realpath(cand)).startswith(
            os.path.normcase(os.path.realpath(str(MANAGED_BIN))) + os.sep)
        identified.append(info)

    # Newest version first; sort is stable, so on ties the earlier PATH entry wins.
    identified.sort(key=lambda i: i.version_tuple, reverse=True)
    for info in identified:
        works, lzma_ok, message = self_test(info.path)
        if works:
            info.lzma = lzma_ok
            return info
        if not quiet:
            print("Ignoring %s (Xdelta %s): its self-test failed - %s" % (info.path, info.version, message))
    return None


# --------------------------------------------------------------------------
# GitHub layer (official project: https://github.com/jmacd/xdelta)
# --------------------------------------------------------------------------

@dataclass
class Asset:
    name: str
    url: str
    sha256: str | None = None


@dataclass
class Release:
    tag: str
    version: str
    assets: dict = field(default_factory=dict)

    def find(self, suffix: str) -> Asset | None:
        for name, asset in self.assets.items():
            if name.endswith("-" + suffix):
                return asset
        return None

    def source_tarball(self) -> Asset | None:
        for name, asset in self.assets.items():
            if re.fullmatch(r"xdelta3-\d[\d.]*\.tar\.gz", name):
                return asset
        return None


def http_open(url: str, accept: str | None = None, timeout: float = 30):
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def fetch_latest_release() -> Release | None:
    try:
        with http_open(GITHUB_API_LATEST, accept="application/vnd.github+json") as resp:
            data = json.load(resp)
        tag = data["tag_name"]
        assets = {}
        for a in data.get("assets", []):
            digest = a.get("digest") or ""
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            assets[a["name"]] = Asset(a["name"], a["browser_download_url"], sha)
        if assets:
            return Release(tag=tag, version=tag.lstrip("vV"), assets=assets)
    except Exception as exc:  # network, TLS, rate limit, JSON ...
        print("  GitHub API not available (%s); trying the release page instead." % exc)

    try:
        with http_open(GITHUB_LATEST_PAGE) as resp:
            final_url = resp.geturl()
        m = re.search(r"/releases/tag/([^/?#]+)", final_url)
        if not m:
            return None
        tag = m.group(1)
        ver = tag.lstrip("vV")
        base = "https://github.com/%s/releases/download/%s/" % (GITHUB_REPO, tag)
        # Asset names are fixed by the project's release workflow.
        names = ["xdelta3-%s.tar.gz" % ver,
                 "xdelta3-%s-linux-x86_64.tar.gz" % ver,
                 "xdelta3-%s-macos-arm64.tar.gz" % ver,
                 "xdelta3-%s-windows-x86_64.zip" % ver]
        return Release(tag=tag, version=ver, assets={n: Asset(n, base + n) for n in names})
    except Exception as exc:
        print("  Could not reach GitHub: %s" % exc)
        return None


def download(url: str, dest: Path, expected_sha256: str | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    print("  Downloading %s" % url)
    with http_open(url, timeout=60) as resp, open(str(dest), "wb") as fh:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            digest.update(chunk)
            total += len(chunk)
    print("  Downloaded %s." % human_size(total))
    if expected_sha256:
        if digest.hexdigest().lower() != expected_sha256.lower():
            dest.unlink()
            raise RuntimeError("SHA-256 mismatch for %s - the download was discarded." % dest.name)
        print("  SHA-256 verified against the value published by GitHub.")
    else:
        print("  (GitHub published no checksum for this file, so none could be checked.)")


def prebuilt_suffix(p: Platform) -> str | None:
    """Which prebuilt archive the official release offers for this machine, if any."""
    if p.os == "windows" and p.arch in ("x86_64", "arm64"):
        return "windows-x86_64.zip"  # Windows on ARM64 runs it through emulation
    if p.os == "macos" and p.arch == "arm64":
        return "macos-arm64.tar.gz"
    if p.os == "linux" and p.arch == "x86_64" and p.py_bits == 64:
        return "linux-x86_64.tar.gz"
    return None


def save_meta(**values) -> None:
    MANAGED_DIR.mkdir(parents=True, exist_ok=True)
    MANAGED_META.write_text(json.dumps(values, indent=2))


def load_meta() -> dict:
    try:
        return json.loads(MANAGED_META.read_text())
    except (OSError, ValueError):
        return {}


def install_managed_file(src: Path, dest: Path, executable: bool = True) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".new")
    shutil.copyfile(str(src), str(tmp))
    if executable and os.name != "nt":
        os.chmod(str(tmp), 0o755)
    os.replace(str(tmp), str(dest))


def install_prebuilt(p: Platform, asset: Asset, rel: Release) -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="xdelta-dl-"))
    try:
        archive = tmp / asset.name
        download(asset.url, archive, asset.sha256)
        extracted = tmp / "extracted"
        extracted.mkdir()
        man_src = None
        if asset.name.endswith(".zip"):
            with zipfile.ZipFile(str(archive)) as zf:
                member = next((m for m in zf.namelist()
                               if m.replace("\\", "/").rsplit("/", 1)[-1].lower() == "xdelta3.exe"), None)
                if member is None:
                    print("  xdelta3.exe was not inside the archive.")
                    return False
                with zf.open(member) as fin, open(str(extracted / "xdelta3.exe"), "wb") as fout:
                    shutil.copyfileobj(fin, fout)
        else:
            with tarfile.open(str(archive)) as tf:
                for m in tf.getmembers():
                    base = Path(m.name).name
                    if m.isfile() and base in ("xdelta3", "xdelta3.1"):
                        fin = tf.extractfile(m)
                        if fin is None:
                            continue
                        with fin, open(str(extracted / base), "wb") as fout:
                            shutil.copyfileobj(fin, fout)
            if not (extracted / "xdelta3").is_file():
                print("  xdelta3 was not inside the archive.")
                return False
            if (extracted / "xdelta3.1").is_file():
                man_src = extracted / "xdelta3.1"

        target = MANAGED_BIN / p.exe_name
        install_managed_file(extracted / p.exe_name, target)
        info, reason = identify_xdelta(str(target))
        works = False
        if info is not None:
            works, _, message = self_test(str(target))
            if not works:
                reason = message
        if info is None or not works:
            print("  The downloaded binary does not run on this machine: %s" % reason)
            try:
                target.unlink()
            except OSError:
                pass
            return False
        if man_src is not None:
            install_managed_file(man_src, MANAGED_MAN / "man1" / "xdelta3.1", executable=False)
        save_meta(tag=rel.tag, version=rel.version, method="prebuilt", asset=asset.name)
        print("  Installed Xdelta %s to %s" % (info.version, target))
        return True
    except Exception as exc:
        print("  Prebuilt install failed: %s" % exc)
        return False
    finally:
        safe_rmtree(tmp)


def extract_tar_safely(archive: Path, dest: Path) -> None:
    with tarfile.open(str(archive)) as tf:
        if hasattr(tarfile, "data_filter"):
            tf.extractall(str(dest), filter="data")
            return
        root = os.path.realpath(str(dest))
        for m in tf.getmembers():
            target = os.path.realpath(os.path.join(root, m.name))
            if not (target == root or target.startswith(root + os.sep)):
                raise RuntimeError("Unsafe path in archive: %s" % m.name)
            if m.issym() or m.islnk() or m.isdev():
                continue
            tf.extract(m, str(dest))


def missing_build_tools(p: Platform) -> list:
    missing = []
    if not command_exists("cmake"):
        missing.append("cmake")
    else:
        try:
            out = run(["cmake", "--version"], capture=True, timeout=30).stdout or ""
            m = re.search(r"version\s+(\d+(?:\.\d+)*)", out)
            if m and version_tuple(m.group(1)) < (3, 13):
                missing.append("cmake 3.13 or newer (found %s)" % m.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    if p.os == "windows":
        return missing  # CMake finds Visual Studio / MinGW itself
    if p.os == "macos":
        try:
            if run(["xcode-select", "-p"], capture=True, timeout=30).returncode != 0:
                missing.append("Xcode Command Line Tools (install with: xcode-select --install)")
        except OSError:
            missing.append("Xcode Command Line Tools")
    else:
        if not any(command_exists(c) for c in ("cc", "gcc", "clang")):
            missing.append("a C compiler")
        if not any(command_exists(c) for c in ("c++", "g++", "clang++")):
            missing.append("a C++ compiler")
    if not (command_exists("make") or command_exists("ninja")):
        missing.append("make")
    return missing


def ensure_build_tools(p: Platform) -> bool:
    missing = missing_build_tools(p)
    if not missing:
        return True
    print("  Building from source needs: %s" % ", ".join(missing))
    packages = BUILD_PACKAGES.get(p.pm or "")
    if packages and confirm("  Install the build tools with %s?" % p.pm, default=True):
        cmds = pm_commands(p, p.pm, "install", packages)
        ok = bool(cmds) and run_commands(p.pm, cmds)
        if not ok:
            # One unknown package name must not block the rest.
            for pkg in packages:
                cmds = pm_commands(p, p.pm, "install", [pkg])
                if cmds:
                    run_commands(p.pm, cmds)
        augment_path(p)
        missing = missing_build_tools(p)
    if missing:
        print("  Still missing: %s" % ", ".join(missing))
        if p.os == "windows":
            print("  Install CMake and Visual Studio Build Tools (or MinGW-w64), then run this again.")
        return False
    return True


def build_from_source(p: Platform, rel: Release) -> bool:
    asset = rel.source_tarball()
    if asset is None:
        print("  The release has no source tarball.")
        return False
    if not ensure_build_tools(p):
        return False
    if not command_exists("git"):
        print("  Note: git is not installed, so the optional BLAKE3 'armor' feature may be left out.")

    tmp = Path(tempfile.mkdtemp(prefix="xdelta-build-"))
    try:
        archive = tmp / asset.name
        download(asset.url, archive, asset.sha256)
        src_root = tmp / "src"
        src_root.mkdir()
        extract_tar_safely(archive, src_root)
        cmake_dir = next((c.parent for c in src_root.rglob("CMakeLists.txt")
                          if (c.parent / "xdelta3.c").is_file()), None)
        if cmake_dir is None:
            print("  Could not find the xdelta3 sources inside the tarball.")
            return False

        build_dir = tmp / "build"
        configure = ["cmake", "-S", str(cmake_dir), "-B", str(build_dir),
                     "-DCMAKE_BUILD_TYPE=Release", "-DXD3_BUILD_TESTS=OFF"]
        if p.os != "windows" and not command_exists("make") and command_exists("ninja"):
            configure += ["-G", "Ninja"]
        if p.os == "macos":
            for prefix in ("/opt/homebrew", "/usr/local"):
                if Path(prefix, "include", "lzma.h").exists():
                    configure.append("-DCMAKE_PREFIX_PATH=" + prefix)
                    break

        print("  Configuring (this needs internet access for the optional BLAKE3 part)...")
        if run(configure).returncode != 0:
            print("  Configure failed; retrying without armor mode (-DXD3_ARMOR=OFF)...")
            if build_dir.exists():
                safe_rmtree(build_dir)
            if run(configure + ["-DXD3_ARMOR=OFF"]).returncode != 0:
                print("  Configuration failed.")
                return False

        jobs = str(max(1, min(os.cpu_count() or 1, 4 if p.os != "android" else 2)))
        print("  Building (this can take a few minutes)...")
        if run(["cmake", "--build", str(build_dir), "--config", "Release", "-j", jobs]).returncode != 0:
            print("  The build failed.")
            return False

        built = next((f for f in build_dir.rglob(p.exe_name)
                      if f.is_file() and "CMakeFiles" not in f.parts), None)
        if built is None:
            print("  The build finished but %s was not produced." % p.exe_name)
            return False

        target = MANAGED_BIN / p.exe_name
        install_managed_file(built, target)
        info, reason = identify_xdelta(str(target))
        works = False
        if info is not None:
            works, _, message = self_test(str(target))
            if not works:
                reason = message
        if info is None or not works:
            print("  The freshly built binary failed its self-test: %s" % reason)
            return False
        man_page = cmake_dir / "xdelta3.1"
        if man_page.is_file():
            install_managed_file(man_page, MANAGED_MAN / "man1" / "xdelta3.1", executable=False)
        save_meta(tag=rel.tag, version=rel.version, method="source")
        print("  Built and installed Xdelta %s to %s" % (info.version, target))
        return True
    except Exception as exc:
        print("  Build from source failed: %s" % exc)
        return False
    finally:
        safe_rmtree(tmp)


def install_from_github(p: Platform, rel: Release | None = None) -> bool:
    print()
    print("Installing from the official project: https://github.com/%s" % GITHUB_REPO)
    rel = rel or fetch_latest_release()
    if rel is None:
        print("Could not read the latest release from GitHub.")
        return False
    print("  Latest release: %s" % rel.tag)
    suffix = prebuilt_suffix(p)
    if suffix:
        asset = rel.find(suffix)
        if asset and install_prebuilt(p, asset, rel):
            return True
        print("  Prebuilt binary unavailable or unusable here; building from source instead.")
    else:
        print("  The official release has no prebuilt binary for %s %s; building from source."
              % (p.label, "%s-bit" % p.bits))
    return build_from_source(p, rel)


# --------------------------------------------------------------------------
# Install / update orchestration
# --------------------------------------------------------------------------

def manual_install_help(p: Platform) -> str:
    lines = ["Xdelta 3 could not be installed automatically.", ""]
    if p.os == "windows":
        lines.append("Download xdelta3-<version>-windows-x86_64.zip from "
                     "https://github.com/%s/releases (64-bit Windows)," % GITHUB_REPO)
        lines.append("or build the source with CMake + Visual Studio Build Tools (32-bit Windows),")
        lines.append("and put xdelta3.exe in a folder on your PATH.")
    elif p.os == "android":
        lines.append("In Termux run:  pkg update && pkg install xdelta3")
        lines.append("If that fails, try: termux-change-repo   (choose another mirror)")
    elif p.os == "macos":
        lines.append("Install Homebrew (https://brew.sh), then:  brew install xdelta")
    else:
        lines.append("Install the 'xdelta3' package with your distribution's package manager")
        lines.append("(Fedora/RHEL: 'xdelta').")
    return "\n".join(lines)


def install_xdelta(p: Platform) -> bool:
    if p.pm:
        pkgs = XDELTA_PACKAGES.get(p.pm, [])
        print("Package manager found: %s" % p.pm)
        preview = pm_commands(p, p.pm, "install", pkgs[:1]) if pkgs else None
        if preview:
            print("Command(s) that would run:")
            for cmd in preview:
                print("  " + format_command(cmd))
            if confirm("Install Xdelta 3 with %s?" % p.pm, default=True):
                for pkg in pkgs:
                    cmds = pm_commands(p, p.pm, "install", [pkg])
                    if cmds and run_commands(p.pm, cmds):
                        augment_path(p)
                        if find_working_xdelta(p, quiet=True):
                            return True
                print("The package manager could not provide a working Xdelta 3.")
            else:
                print("Skipping the package manager.")
    else:
        print("No supported package manager was found.")

    print("Next option: the official GitHub project.")
    if not confirm("Download/build Xdelta from https://github.com/%s ?" % GITHUB_REPO, default=True):
        return False
    if not install_from_github(p):
        return False
    augment_path(p)
    return find_working_xdelta(p, quiet=True) is not None


def github_update_if_newer(p: Platform, info: XdeltaInfo) -> None:
    rel = fetch_latest_release()
    if rel is None:
        print("Could not check GitHub for a newer release.")
        return
    if version_tuple(rel.version) <= info.version_tuple:
        print("Xdelta %s is already at least as new as the latest GitHub release (%s)."
              % (info.version, rel.tag))
        return
    print("A newer release exists on GitHub: %s (you have %s)." % (rel.tag, info.version))
    if confirm("Install %s from GitHub into %s ?" % (rel.tag, MANAGED_DIR), default=True):
        install_from_github(p, rel)
        augment_path(p)


def update_xdelta(p: Platform, info: XdeltaInfo) -> None:
    result = None
    if not info.managed and p.pm:
        result = False
        for pkg in XDELTA_PACKAGES.get(p.pm, []):
            outcome = pm_run(p, "upgrade", [pkg])
            if outcome is None:
                result = None
                break
            if outcome:
                result = True
                break
        if result is None:
            print("%s cannot safely upgrade a single package; skipping the package-manager update." % p.pm)
    if info.managed or p.pm is None or result is False:
        github_update_if_newer(p, info)


# --------------------------------------------------------------------------
# Documentation viewer
# --------------------------------------------------------------------------

@dataclass
class DocViewer:
    kind: str            # mandoc | man | more | none
    updatable: bool = False


def detect_doc_viewer(p: Platform) -> DocViewer:
    if p.os == "windows":
        return DocViewer("more" if command_exists("more") else "none")
    if command_exists("mandoc"):
        return DocViewer("mandoc", updatable=p.os in ("linux", "android"))
    if command_exists("man"):
        return DocViewer("man", updatable=p.os == "android")
    return DocViewer("none")


def ensure_doc_viewer(p: Platform):
    """Returns (DocViewer, freshly_installed)."""
    section("Documentation viewer")
    viewer = detect_doc_viewer(p)
    fresh = False
    if p.os == "windows":
        print("Windows has no manual pages; Xdelta's built-in help is shown with the built-in 'more' pager.")
    elif viewer.kind == "mandoc":
        print("Found mandoc: %s" % shutil.which("mandoc"))
    elif p.os == "macos":
        print("Using the manual viewer built into macOS (%s)." % viewer.kind)
    else:
        pkgs = DOC_PACKAGES.get(p.pm or "", [])
        if viewer.kind == "man":
            print("Found man: %s" % shutil.which("man"))
        else:
            print("No manual-page viewer (mandoc/man) is installed.")
        if pkgs and viewer.kind != "man" and confirm(
                "Install %s with %s?" % (pkgs[0], p.pm), default=True):
            for pkg in pkgs:
                cmds = pm_commands(p, p.pm, "install", [pkg])
                if cmds and run_commands(p.pm, cmds):
                    augment_path(p)
                    viewer = detect_doc_viewer(p)
                    if viewer.kind in ("mandoc", "man"):
                        fresh = True
                        break
        if viewer.kind == "none":
            print("No manual viewer available; Xdelta's built-in help will be used instead.")
    return viewer, fresh


def update_doc_viewer(p: Platform) -> None:
    for pkg in DOC_PACKAGES.get(p.pm or "", []):
        outcome = pm_run(p, "upgrade", [pkg])
        if outcome is None:
            print("%s cannot safely upgrade a single package; skipped." % p.pm)
            return
        if outcome:
            return


def offer_updates(p: Platform, info: XdeltaInfo, viewer: DocViewer, fresh: set) -> XdeltaInfo:
    items = []
    if "xdelta" not in fresh:
        items.append("Xdelta")
    if viewer.updatable and "docs" not in fresh:
        items.append("the documentation viewer")
    if not items:
        return info
    section("Updates")
    if not confirm("Check for updates to %s?" % " and ".join(items), default=True):
        return info
    if "Xdelta" in items:
        update_xdelta(p, info)
        augment_path(p)
    if "the documentation viewer" in items:
        update_doc_viewer(p)
    return find_working_xdelta(p, quiet=True) or info


def man_page_candidates(exe: str | None) -> list:
    dirs = [MANAGED_MAN]
    for entry in os.environ.get("MANPATH", "").split(os.pathsep):
        if entry:
            dirs.append(Path(entry))
    prefix = os.environ.get("PREFIX")
    if prefix:
        dirs += [Path(prefix) / "share" / "man", Path(prefix) / "man"]
    if exe:
        dirs.append(Path(os.path.realpath(exe)).parent.parent / "share" / "man")
    dirs += [Path(x) for x in ("/usr/share/man", "/usr/local/share/man", "/usr/man",
                               "/opt/homebrew/share/man", "/opt/local/share/man")]
    files = []
    for d in dirs:
        for name in ("xdelta3.1", "xdelta.1"):
            for ext in ("", ".gz", ".bz2", ".xz"):
                files.append(d / "man1" / (name + ext))
    return files


def find_man_page(exe: str | None) -> Path | None:
    for f in man_page_candidates(exe):
        if f.is_file():
            return f
    return None


def read_man_source(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        return gzip.decompress(raw)
    if path.suffix == ".bz2":
        return bz2.decompress(raw)
    if path.suffix == ".xz":
        return lzma.decompress(raw)
    return raw


def page_text(text: str) -> None:
    """Show text through the OS pager (less on Unix, more on Windows) or a tiny built-in pager."""
    if sys.stdout.isatty():
        pager = None
        if os.name == "nt" and command_exists("more"):
            pager = ["more"]
        elif command_exists("less"):
            pager = ["less", "-R"]
        elif command_exists("more"):
            pager = ["more"]
        if pager:
            try:
                subprocess.run(pager, input=text.encode("utf-8", "replace"))
                return
            except OSError:
                pass
        lines = text.splitlines()
        height = max(10, shutil.get_terminal_size((80, 24)).lines - 2)
        for start in range(0, len(lines), height):
            print("\n".join(lines[start:start + height]))
            if start + height < len(lines):
                if input("-- Enter for more, q then Enter to stop -- ").strip().lower() == "q":
                    return
    else:
        print(text)


def show_manual(info: XdeltaInfo, viewer: DocViewer) -> None:
    page = find_man_page(info.path)
    if viewer.kind == "mandoc" and page is not None:
        try:
            source = read_man_source(page)
            for fmt in ("utf8", "ascii"):
                result = run(["mandoc", "-T", fmt], input_bytes=source, timeout=60)
                if result.returncode == 0 and result.stdout:
                    text = result.stdout.decode("utf-8", "replace")
                    page_text(re.sub(r".\x08", "", text))  # drop overstrike bold/underline
                    return
        except Exception as exc:
            print("mandoc could not render %s: %s" % (page, exc))
    if command_exists("man"):
        env = dict(os.environ)
        env["MANPATH"] = str(MANAGED_MAN) + os.pathsep + env.get("MANPATH", "")
        try:
            if run(["man", "xdelta3"], env=env).returncode == 0:
                return
        except OSError:
            pass
    print("No xdelta3 manual page is installed here; showing Xdelta's built-in help instead.")
    print()
    show_builtin_help(info)


def show_builtin_help(info: XdeltaInfo) -> None:
    try:
        out = run([info.path, "-h"], capture=True, timeout=60).stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        print("Could not run xdelta3 -h: %s" % exc)
        return
    page_text(out)


def open_online_docs(p: Platform) -> None:
    print("Online documentation: %s" % DOCS_URL)
    if not confirm("Try to open it in a browser?", default=False):
        return
    try:
        if p.os == "android" and command_exists("termux-open-url"):
            run(["termux-open-url", DOCS_URL])
        else:
            import webbrowser
            if not webbrowser.open(DOCS_URL):
                print("No browser could be started; open the link above manually.")
    except Exception as exc:
        print("Could not open a browser (%s); open the link above manually." % exc)


def documentation_menu(p: Platform, info: XdeltaInfo, viewer: DocViewer) -> None:
    while True:
        print()
        options = []
        if viewer.kind in ("mandoc", "man"):
            options.append(("1", "Xdelta manual page (via %s)" % viewer.kind))
        options.append(("2", "Xdelta built-in help (xdelta3 -h)"))
        options.append(("3", "Online documentation"))
        options.append(("b", "Back"))
        choice = choose("Documentation", options, default="b")
        if choice == "1":
            show_manual(info, viewer)
        elif choice == "2":
            show_builtin_help(info)
        elif choice == "3":
            open_online_docs(p)
        else:
            return


# --------------------------------------------------------------------------
# Path prompts
# --------------------------------------------------------------------------

def clean_path_text(raw: str) -> str:
    text = raw.strip()
    if text.startswith("& "):  # PowerShell drag-and-drop
        text = text[2:].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    if os.name != "nt" and "\\" in text and not os.path.exists(os.path.expanduser(text)):
        try:
            parts = shlex.split(text)  # terminal drag-and-drop: /My\ Games/rom.gba
        except ValueError:
            parts = []
        if len(parts) == 1:
            return parts[0]
    return text


def resolve_user_path(raw: str, must_exist: bool) -> Path:
    text = os.path.expandvars(os.path.expanduser(clean_path_text(raw)))
    path = Path(text)
    if path.is_absolute():
        return path.resolve()
    from_cwd = Path.cwd() / path
    if must_exist and not from_cwd.exists():
        from_script = SCRIPT_DIR / path
        if from_script.exists():
            print("  (found next to the script: %s)" % from_script.resolve())
            return from_script.resolve()
    return from_cwd.resolve()


def same_file(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return os.path.samefile(str(a), str(b))
    except OSError:
        pass
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def prompt_path(label: str, *, must_exist: bool, default: Path | None = None, avoid: tuple = ()) -> Path:
    while True:
        suffix = " [%s]" % default if default else ""
        raw = input("%s%s: " % (label, suffix)).strip()
        if not raw and default is not None:
            path = default
        elif not raw:
            print("Please enter a path.")
            continue
        else:
            path = resolve_user_path(raw, must_exist)

        if must_exist:
            if not path.exists():
                print("File not found: %s" % path)
                continue
            if not path.is_file():
                print("That is a folder, not a file: %s" % path)
                continue
            if not os.access(str(path), os.R_OK):
                print("You do not have permission to read: %s" % path)
                continue
        else:
            if path.is_dir():
                print("That is a folder; please give a file name: %s" % path)
                continue
            if not path.parent.is_dir():
                print("The folder does not exist: %s" % path.parent)
                continue
            if not os.access(str(path.parent), os.W_OK):
                print("You cannot write to the folder: %s" % path.parent)
                continue

        clash = next((other for other in avoid if same_file(other, path)), None)
        if clash is not None:
            print("That is the same file as %s. Please choose a different one." % clash)
            continue
        return path


# --------------------------------------------------------------------------
# Switch validation (never allows -n)
# --------------------------------------------------------------------------

FLAG_LETTERS = set("fFqvNDRGa")          # switches without a value
VALUE_LETTERS = set("SBWPIA")            # switches that take a value
SECONDARY_VALUES = {"lzma", "djw", "fgk", "none"}
RESERVED_MESSAGES = {
    "e": "-e is added automatically when creating a patch.",
    "d": "-d is added automatically when applying a patch.",
    "s": "-s (source file) is added automatically from your file choices.",
    "c": "-c (write to stdout) would bypass the output file this tool manages.",
    "J": "-J (no output) would produce no file.",
    "h": "-h / -V are informational; use the documentation menu instead.",
    "V": "-h / -V are informational; use the documentation menu instead.",
    "m": "-m belongs to Xdelta's 'merge' command, which this tool does not run.",
}


def check_switches(tokens: list) -> list:
    """Validate extra Xdelta switches. Returns warnings; raises ValueError when refused."""
    warnings = []
    seen_values = set()
    levels = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            raise ValueError("Long options (%s) are not accepted; Xdelta 3 documents only "
                             "single-letter switches." % tok)
        if not tok.startswith("-") or tok == "-":
            raise ValueError("Unexpected argument %r. A value must directly follow the switch "
                             "that needs it (for example: -S lzma)." % tok)
        body = tok[1:]
        j = 0
        while j < len(body):
            ch = body[j]
            if ch == "n":
                raise ValueError(FORBIDDEN_MESSAGE)
            if ch.isdigit():
                levels += 1
                if levels > 1:
                    raise ValueError("Only one compression level (-0 ... -9) may be given.")
                j += 1
                continue
            if ch in FLAG_LETTERS:
                if ch == "a":
                    warnings.append("-a turns off Xdelta 3.2's whole-file BLAKE3 verification "
                                    "(the normal checksums stay on).")
                j += 1
                continue
            if ch in VALUE_LETTERS:
                if ch != "A" and ch in seen_values:
                    raise ValueError("-%s was given twice." % ch)
                seen_values.add(ch)
                value = body[j + 1:]
                if not value and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                    i += 1
                    value = tokens[i]
                if ch == "S":
                    if value not in SECONDARY_VALUES:
                        raise ValueError("-S needs one of: lzma, djw, fgk, none.")
                elif ch in "BWPI":
                    if not re.fullmatch(r"\d+", value or ""):
                        raise ValueError("-%s needs a whole number of bytes (for example: -%s 16777216)."
                                         % (ch, ch))
                break  # the rest of the token was this switch's value
            if ch in RESERVED_MESSAGES:
                raise ValueError(RESERVED_MESSAGES[ch])
            raise ValueError("Unknown or unsupported switch: -%s" % ch)
        i += 1
    return warnings


def parse_switch_text(text: str) -> list:
    if not text.strip():
        return []
    try:
        return shlex.split(text, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ValueError("Could not read those switches: %s" % exc)


# --------------------------------------------------------------------------
# Patch / apply workflow
# --------------------------------------------------------------------------

@dataclass
class Job:
    op: str                    # create | apply
    source: Path               # original / base file (always an input)
    patch: Path                # create: output   apply: input
    target: Path               # create: modified file (input)   apply: patched result (output)
    switches: list
    notes: list = field(default_factory=list)   # (label, value) pairs for the summary

    @property
    def output(self) -> Path:
        return self.patch if self.op == "create" else self.target


def identify_patch_format(path: Path) -> str:
    try:
        with open(str(path), "rb") as fh:
            head = fh.read(8)
    except OSError:
        return "unknown"
    if head[:3] == b"\xd6\xc3\xc4":
        return "vcdiff"
    if head[:5] == b"PATCH":
        return "IPS"
    if head[:4] == b"BPS1":
        return "BPS"
    if head[:4] == b"UPS1":
        return "UPS"
    if head[:2] == b"\x1f\x8b" or head[:6] == b"\xfd7zXZ\x00":
        return "compressed"  # Xdelta can auto-decompress some external compression
    return "unknown"


def choose_secondary(info: XdeltaInfo) -> tuple:
    options = [
        ("1", "Xdelta default (no switch)"),
        ("2", "LZMA - strongest compression (-S lzma)"
              + ("" if info.lzma is not False else "  [NOT supported by this Xdelta build]")),
        ("3", "None - most compatible with other patching tools (-S none)"),
        ("4", "DJW - built-in coder (-S djw)"),
        ("5", "FGK - built-in coder (-S fgk)"),
    ]
    mapping = {"1": [], "2": ["-S", "lzma"], "3": ["-S", "none"], "4": ["-S", "djw"], "5": ["-S", "fgk"]}
    while True:
        key = choose("Secondary compression:", options, default="1")
        if key == "2" and info.lzma is False:
            print("This Xdelta was built without LZMA. Pick another option, or update/install a build with LZMA "
                  "(the official GitHub release binaries include it).")
            continue
        return mapping[key], options[int(key) - 1][1]


def collect_options(op: str, info: XdeltaInfo) -> tuple:
    """Returns (switches, description lines)."""
    section("Options")
    switches = []
    lines = []
    if op == "create":
        sec, sec_label = choose_secondary(info)
        switches += sec
        lines.append(("Secondary compr.", sec_label))
        while True:
            raw = input("Compression level 0-9 (-9 = smallest patch, slowest; Enter = default): ").strip()
            if not raw:
                lines.append(("Compression level", "Xdelta default"))
                break
            if re.fullmatch(r"[0-9]", raw):
                switches.append("-" + raw)
                lines.append(("Compression level", "-%s" % raw))
                break
            print("Please enter a single digit from 0 to 9, or press Enter.")
    else:
        print("Applying: the secondary-compression type is read from the patch itself, so no switch is needed.")
        if info.lzma is False:
            print("Note: this Xdelta lacks LZMA, so patches made with '-S lzma' cannot be applied by it.")

    print()
    print("Extra switches (advanced). Examples: -B 268435456   -W 16777216   -v")
    print("See the Documentation menu. Refused: -n (checksums stay on), long options, -e/-d/-s/-c.")
    while True:
        raw = input("Extra switches (Enter for none): ").strip()
        try:
            extra = parse_switch_text(raw)
            warnings = check_switches(switches + extra)
        except ValueError as exc:
            print("Not accepted: %s" % exc)
            continue
        for w in warnings:
            print("WARNING: %s" % w)
        switches += extra
        lines.append(("Extra switches", " ".join(extra) if extra else "(none)"))
        return switches, lines


def collect_job(op: str, info: XdeltaInfo) -> Job | None:
    section("Choose files")
    if op == "create":
        source = prompt_path("Original (base) file, the one the patch is made FROM", must_exist=True)
        target = prompt_path("Modified file, the version the patch should produce",
                             must_exist=True, avoid=(source,))
        default = target.with_name(target.stem + ".xdelta")
        patch = prompt_path("Patch file to create", must_exist=False, default=default,
                            avoid=(source, target))
    else:
        source = prompt_path("Original (base) file the patch was made for", must_exist=True)
        patch = prompt_path("Patch file to apply (.xdelta / .vcdiff)", must_exist=True, avoid=(source,))
        fmt = identify_patch_format(patch)
        if fmt in ("IPS", "BPS", "UPS"):
            print("\nThis file looks like an %s patch. Xdelta cannot apply %s patches." % (fmt, fmt))
            if not confirm("Continue anyway?", default=False):
                return None
        elif fmt == "unknown":
            print("\nNote: this file does not start with the standard Xdelta/VCDIFF signature. "
                  "Xdelta will tell you if it cannot read it.")
        default = source.with_name(patch.stem + source.suffix)
        if same_file(default, source) or same_file(default, patch):
            default = source.with_name(source.stem + " (patched)" + source.suffix)
        target = prompt_path("Output file to create (the patched result)", must_exist=False,
                             default=default, avoid=(source, patch))

    switches, option_lines = collect_options(op, info)
    return Job(op=op, source=source, patch=patch, target=target, switches=switches, notes=option_lines)


def build_command(exe: str, job: Job) -> list:
    """Assemble the Xdelta argument list. Refuses anything that could disable checksums."""
    check_switches(job.switches)  # second, independent gate right before use
    switches = list(job.switches)
    if job.output.exists() and "-f" not in switches:
        switches.append("-f")  # Xdelta refuses to overwrite without -f; the user already confirmed
    if job.op == "create":
        command = [exe] + switches + ["-e", "-s", str(job.source), str(job.target), str(job.patch)]
    else:
        command = [exe] + switches + ["-d", "-s", str(job.source), str(job.patch), str(job.target)]
    if any(arg == "-n" for arg in command[1:]):  # belt and braces
        raise ValueError(FORBIDDEN_MESSAGE)
    return command


def describe(path: Path, must_exist: bool) -> str:
    if path.exists():
        return "%s  (%s)" % (path, human_size(path.stat().st_size))
    return "%s  (new file)" % path


def print_summary(job: Job, info: XdeltaInfo) -> None:
    section("Your selections")
    if job.op == "create":
        print("  Operation        : CREATE a patch")
        print("  Original file    : %s" % describe(job.source, True))
        print("  Modified file    : %s" % describe(job.target, True))
        print("  Patch to create  : %s" % describe(job.patch, False))
    else:
        print("  Operation        : APPLY a patch")
        print("  Original file    : %s" % describe(job.source, True))
        print("  Patch file       : %s" % describe(job.patch, True))
        print("  Output file      : %s" % describe(job.target, False))
    for label, value in job.notes:
        print("  %-17s: %s" % (label, value))
    print("  Checksums        : ON (this tool never disables them)")
    print("  Xdelta           : %s (version %s)" % (info.path, info.version))
    if job.output.exists():
        print()
        print("  WARNING: the output file already exists and WILL BE OVERWRITTEN (-f will be added):")
        print("           %s" % job.output)


def failure_hints(job: Job, info: XdeltaInfo, rc: int) -> None:
    print()
    print("Xdelta exited with status %s." % rc)
    if rc == 2 and job.op == "apply":
        print("Status 2 means the file you supplied already matches the patch's result "
              "(the patch was already applied).")
        return
    if rc < 0:
        print("The program was killed by a signal - this often means it ran out of memory. "
              "Try smaller windows, e.g. extra switches: -B 16777216 -W 8388608")
    if job.op == "apply":
        print("Common causes:")
        print("  - The original file is not exactly the one the patch was made for "
              "(different region/revision, or a header was added or removed).")
        print("  - The patch file is damaged or is not an Xdelta patch.")
        if info.lzma is False:
            print("  - The patch uses LZMA compression and this Xdelta build lacks LZMA.")
        print("  'target window checksum mismatch' means the input file is not the expected one.")
    else:
        print("Check that both input files are readable and that there is enough free disk space.")


def execute(job: Job, info: XdeltaInfo) -> None:
    command = build_command(info.path, job)
    section("Command that is about to run")
    print(format_command(command))
    print()
    if not confirm("Run this command now?", default=False):
        print("Cancelled - nothing was run.")
        return

    existed = job.output.exists()
    print()
    try:
        rc = subprocess.run(command).returncode
    except OSError as exc:
        print("Could not start Xdelta: %s" % exc)
        return

    if rc == 0:
        size = job.output.stat().st_size if job.output.exists() else 0
        print()
        print("Done. %s created: %s (%s)" % ("Patch" if job.op == "create" else "Output file",
                                              job.output, human_size(size)))
        return

    failure_hints(job, info, rc)
    if not existed and job.output.exists():
        if confirm("An incomplete output file may have been left behind. Delete it?", default=True):
            try:
                job.output.unlink()
                print("Deleted %s" % job.output)
            except OSError as exc:
                print("Could not delete it: %s" % exc)


def run_operation(op: str, info: XdeltaInfo) -> None:
    job = collect_job(op, info)
    if job is None:
        print("Cancelled.")
        return
    print_summary(job, info)
    print()
    prompt = "Proceed with these selections?"
    if job.output.exists():
        prompt = "Proceed with these selections (the existing output will be overwritten)?"
    if not confirm(prompt, default=False):
        print("Cancelled - nothing was run.")
        return
    execute(job, info)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    banner("Xdelta Patch Manager")
    try:
        p = detect_platform()
        augment_path(p)
        p.pm = detect_pm(p)

        section("Detected system")
        print("  Operating system : %s" % p.label)
        note = ""
        if p.py_bits != p.bits and p.bits:
            note = "  (Python itself is %d-bit)" % p.py_bits
        print("  CPU architecture : %s (%d-bit)%s" % (p.arch if p.arch == p.raw_arch.lower() else
                                                      "%s [reported as %s]" % (p.arch, p.raw_arch),
                                                      p.bits, note))
        print("  Python           : %s, %d-bit" % (platform.python_version(), p.py_bits))
        print("  Package manager  : %s" % (p.pm or "none found"))

        show_directories(p)
        offer_android_storage_setup(p)

        section("Xdelta")
        fresh = set()
        info = find_working_xdelta(p)
        if info is None:
            print("No working Xdelta 3 was found on this system.")
            if not confirm("Install it now?", default=True):
                print("Xdelta is required. Nothing else can be done without it.")
                return 1
            if not install_xdelta(p):
                raise RuntimeError(manual_install_help(p))
            info = find_working_xdelta(p, quiet=True)
            if info is None:
                raise RuntimeError("Xdelta was installed but does not pass its self-test.\n"
                                   + manual_install_help(p))
            fresh.add("xdelta")
        print("Using Xdelta %s: %s" % (info.version, info.path))
        print("Self-test passed (encode/decode round trip). LZMA secondary compression: %s."
              % {True: "supported", False: "not supported", None: "unknown"}[info.lzma])

        viewer, docs_fresh = ensure_doc_viewer(p)
        if docs_fresh:
            fresh.add("docs")
        info = offer_updates(p, info, viewer, fresh)

        while True:
            print()
            choice = choose("What would you like to do?", [
                ("1", "Create a patch"),
                ("2", "Apply a patch"),
                ("3", "Documentation"),
                ("4", "Check for updates"),
                ("q", "Quit"),
            ], default=None)
            if choice == "1":
                run_operation("create", info)
            elif choice == "2":
                run_operation("apply", info)
            elif choice == "3":
                documentation_menu(p, info, viewer)
            elif choice == "4":
                info = offer_updates(p, info, viewer, set())
            else:
                print("Goodbye.")
                return 0

    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    except EOFError:
        print("\nInput ended.")
        return 1
    except Exception as exc:
        print("\nERROR: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
