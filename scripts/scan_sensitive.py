#!/usr/bin/env python3
"""Block commits that contain secrets, real identifiers, or private terms.

Two layers:
  1. Generic patterns (always on, safe to publish): tokens, keys, private keys,
     Databricks workspace hosts, AWS account IDs in ARNs, emails.
  2. A private blocklist (optional) read from $EDIP_BLOCKLIST or
     ~/.config/edip/blocklist.txt. It lives outside the repo so the terms
     themselves are never committed.

Usage:
  scan_sensitive.py --staged        scan files staged for commit (pre-commit hook)
  scan_sensitive.py PATH [PATH...]  scan files or directories
Exit code 1 if anything is found.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

GENERIC = {
    "databricks token": re.compile(r"\bdapi[0-9a-f]{32}\b"),
    "databricks oauth secret": re.compile(r"\bdose[0-9a-f]{32,}\b"),
    "aws access key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),
    "workspace host": re.compile(r"\bdbc-[0-9a-f]{8}-[0-9a-f]{4}\.cloud\.databricks\.com"),
    "aws account in arn": re.compile(r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@(?!example\.(com|org)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}
# Public, non-identifying values that the generic patterns would otherwise flag.
ALLOW = [
    "arn:aws:iam::414351767826:role/unity-catalog-prod-UCMasterRole",  # Databricks' documented UC role
    "noreply@anthropic.com",
]
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "__pycache__", ".databricks"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".parquet", ".zip", ".woff", ".woff2"}


def load_blocklist():
    path = Path(os.environ.get("EDIP_BLOCKLIST", Path.home() / ".config/edip/blocklist.txt"))
    if not path.exists():
        return []
    terms = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append((line, re.compile(r"(?<![A-Za-z0-9])" + re.escape(line) + r"(?![A-Za-z0-9])", re.I)))
    return terms


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         capture_output=True, text=True, check=True).stdout
    return [Path(p) for p in out.splitlines() if p]


def iter_files(paths):
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and not (set(f.parts) & SKIP_DIRS):
                    yield f
        elif p.is_file():
            yield p


def scan(files, blocklist):
    findings = []
    for f in files:
        if f.suffix.lower() in SKIP_SUFFIX or f.name == "scan_sensitive.py":
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(a in line for a in ALLOW):
                continue
            for label, rx in GENERIC.items():
                if rx.search(line):
                    findings.append((f, n, label))
            for term, rx in blocklist:
                if rx.search(line):
                    findings.append((f, n, "private term"))
    return findings


def main(argv):
    files = staged_files() if argv[:1] == ["--staged"] else list(iter_files(argv or ["."]))
    blocklist = load_blocklist()
    findings = scan(files, blocklist)
    for f, n, label in findings:
        # Never echo the matched text: the point is to keep it out of logs too.
        print(f"BLOCKED {f}:{n} ({label})")
    if findings:
        print(f"{len(findings)} finding(s). Remove them, or move values to local config.")
        return 1
    print(f"scan ok ({len(files)} files, private blocklist {'on' if blocklist else 'off'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
