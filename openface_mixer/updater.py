"""Updating an install from the git checkout it was installed from.

scripts/install.sh records that checkout and its commit in <share>/install-source. Checking
fetches origin/main there; updating backs up the config directory, fast-forwards the checkout
and runs the installer again, which also restarts the engines so they match the new GUI.
"""
import shutil
import subprocess
import time
from pathlib import Path

from .config import CONFIG_DIR

SOURCE_FILE = Path(__file__).resolve().parent.parent / "install-source"
BRANCH = "main"


class UpdateError(Exception):
    pass


def install_source():
    """(checkout, installed commit), or None if this copy wasn't installed from a checkout."""
    try:
        path, commit = SOURCE_FILE.read_text().splitlines()[:2]
    except (OSError, ValueError):
        return None
    return (Path(path), commit) if (Path(path) / ".git").exists() else None


def manual_hint():
    return ("This copy wasn't installed from a git checkout, so it can't update itself. To "
            "update: cd into your Openface-Mixer folder, then git pull && make install")


def _git(src, *args, timeout=60):
    try:
        r = subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f"git {args[0]}: {e}") from e
    if r.returncode:
        raise UpdateError((r.stderr or r.stdout).strip() or f"git {args[0]} failed")
    return r.stdout.strip()


def check(src, installed):
    """Fetch and return the subjects of the commits on origin/main that aren't installed."""
    _git(src, "fetch", "--quiet", "origin", BRANCH)
    log = _git(src, "log", "--format=%s", f"{installed}..origin/{BRANCH}")
    return log.splitlines() if log else []


def backup_config():
    if not CONFIG_DIR.is_dir():
        return None
    dest = CONFIG_DIR.with_name(f"{CONFIG_DIR.name}.backup-{time.strftime('%Y-%m-%d-%H%M%S')}")
    shutil.copytree(CONFIG_DIR, dest)
    return dest


def update(src):
    """Back up the mixes, fast-forward the checkout to origin/main and reinstall."""
    if _git(src, "symbolic-ref", "--short", "HEAD") != BRANCH:
        raise UpdateError(f"{src} is not on the {BRANCH} branch; update it by hand.")
    if _git(src, "status", "--porcelain", "--untracked-files=no"):
        raise UpdateError(f"{src} has local changes; update it by hand.")
    backup_config()
    _git(src, "merge", "--ff-only", f"origin/{BRANCH}")
    # A new session has no terminal, so sudo fails instead of waiting for a password nobody
    # can type; the installer only needs it for missing packages and changed udev rules.
    try:
        r = subprocess.run([str(src / "scripts" / "install.sh")], cwd=src, capture_output=True,
                           text=True, timeout=600, stdin=subprocess.DEVNULL,
                           start_new_session=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f"install.sh: {e}") from e
    if r.returncode:
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-15:])
        raise UpdateError(f"The installer failed. Run make install in {src} to see why.\n\n{tail}")
