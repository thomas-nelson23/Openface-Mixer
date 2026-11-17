"""Updating an install from the git checkout it was installed from.

scripts/install.sh records that checkout and its commit in <share>/install-source. Checking
fetches origin/main and the release tags there; updating backs up the config directory,
fast-forwards the checkout and runs the installer again, which also restarts the engines so
they match the new GUI. An update moves to the newest release, a tag vMAJOR.MINOR.PATCH on
main, or to main itself for people who chose the latest changes and while there is no release.
A copy installed by a distribution package has "package" there and is left to its package
manager.
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import __version__
from .config import CONFIG_DIR

SOURCE_FILE = Path(__file__).resolve().parent.parent / "install-source"
BRANCH = "main"
RELEASE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
KEEP_BACKUPS = 3             # config backups kept, one is made before every update


class UpdateError(Exception):
    pass


def install_source():
    """(checkout, installed commit), or None if this copy wasn't installed from a checkout."""
    try:
        path, commit = SOURCE_FILE.read_text().splitlines()[:2]
    except (OSError, ValueError):
        return None
    return (Path(path), commit) if (Path(path) / ".git").exists() else None


def packaged():
    try:
        return SOURCE_FILE.read_text().strip() == "package"
    except OSError:
        return False


def manual_hint():
    if packaged():
        return "This copy was installed as a package, so it is updated by your package manager."
    return ("This copy wasn't installed from a git checkout, so it can't update itself. To "
            "update: cd into your Openface-Mixer folder, then git pull && make install")


def _git(src, *args, timeout=60, env=None):
    try:
        r = subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f"git {args[0]}: {e}") from e
    if r.returncode:
        raise UpdateError((r.stderr or r.stdout).strip() or f"git {args[0]} failed")
    return r.stdout.strip()


def _release_number(tag):
    m = RELEASE.fullmatch(tag)
    return m and tuple(int(n) for n in m.groups())


def _releases(src, tip, timeout=60):
    """The release tags at or below the commit tip, the newest version last."""
    tags = _git(src, "tag", "--list", "v*", "--merged", tip, timeout=timeout).split()
    return sorted(filter(_release_number, tags), key=_release_number)


def _target(src, releases):
    """(release number or None, commit) an update moves to: the newest release on origin/main,
    or origin/main itself when releases is off (the latest changes) or there is none yet."""
    tags = _releases(src, f"origin/{BRANCH}") if releases else []
    if not tags:
        return None, f"origin/{BRANCH}"
    return tags[-1][1:], f"refs/tags/{tags[-1]}^{{commit}}"


def version_text(source):
    """The installed copy in words: "Version 0.4.0" on a release, "Version 0.4.0 + 12 changes
    (abc1234)" past one, and this package's own number where git can't tell (source: what
    install_source() gave)."""
    if source is None:
        return f"Version {__version__}"
    src, installed = source
    try:
        tags = _releases(src, installed, timeout=5)
        ahead = int(_git(src, "rev-list", "--count", f"{tags[-1]}..{installed}", timeout=5)
                    if tags else 0)
    except (UpdateError, ValueError):
        tags = []
    if not tags:
        return f"Version {__version__} ({installed[:7]})"
    if not ahead:
        return f"Version {tags[-1][1:]}"
    return (f"Version {tags[-1][1:]} + {ahead} change{'s' if ahead > 1 else ''} "
            f"({installed[:7]})")


def check(src, installed, releases=True, quiet=False):
    """Fetch, and return (the release an update installs or None, the subjects of the commits
    it brings that aren't installed). quiet: never ask for a password or a passphrase."""
    env = None
    if quiet:
        ssh = os.environ.get("GIT_SSH_COMMAND", "ssh") + " -oBatchMode=yes"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "",
               "GIT_SSH_COMMAND": ssh}
    # origin's release tags replace local ones of the same name, so a moved tag is followed;
    # --no-prune: with fetch.prune set, git would delete the v* tags that are only here
    _git(src, "fetch", "--quiet", "--no-prune", "origin", BRANCH,
         "+refs/tags/v*:refs/tags/v*", env=env)
    release, commit = _target(src, releases)
    log = _git(src, "log", "--format=%s", f"{installed}..{commit}")
    return release, log.splitlines() if log else []


def backup_config():
    """Copy the config directory beside itself, and keep only the newest KEEP_BACKUPS copies."""
    if not CONFIG_DIR.is_dir():
        return None
    dest = CONFIG_DIR.with_name(f"{CONFIG_DIR.name}.backup-{time.strftime('%Y-%m-%d-%H%M%S')}")
    shutil.copytree(CONFIG_DIR, dest)
    # the time stamps sort by age; a folder that isn't named exactly like a backup is left alone
    stamp = "-".join("[0-9]" * n for n in (4, 2, 2, 6))
    for old in sorted(CONFIG_DIR.parent.glob(f"{CONFIG_DIR.name}.backup-{stamp}"))[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def update(src, releases=True):
    """Back up the mixes, fast-forward the checkout to what the last check found (_target)
    and reinstall. The checkout stays on its main branch and never moves backwards."""
    if _git(src, "rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        raise UpdateError(f"{src} is not on the {BRANCH} branch; update it by hand.")
    if _git(src, "status", "--porcelain", "--untracked-files=no"):
        raise UpdateError(f"{src} has local changes; update it by hand.")
    commit = _target(src, releases)[1]
    ahead, behind = _git(src, "rev-list", "--left-right", "--count", f"HEAD...{commit}").split()
    if int(ahead) and int(behind):
        raise UpdateError(f"{src} has commits of its own; update it by hand.")
    backup_config()
    _git(src, "merge", "--ff-only", commit)
    # A new session has no terminal, so sudo fails instead of waiting for a password nobody
    # can type; the installer only needs it for missing packages and changed udev rules.
    # PREFIX: reinstall where this copy is (<prefix>/share/openface-mixer/install-source).
    try:
        r = subprocess.run([str(src / "scripts" / "install.sh")], cwd=src, capture_output=True,
                           text=True, timeout=600, stdin=subprocess.DEVNULL,
                           start_new_session=True,
                           env={**os.environ, "PREFIX": str(SOURCE_FILE.parents[2])})
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(f"install.sh: {e}") from e
    if r.returncode:
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-15:])
        raise UpdateError(f"The installer failed. Run make install in {src} to see why.\n\n{tail}")
