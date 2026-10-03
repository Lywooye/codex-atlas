#!/usr/bin/env python3
"""Offline, fail-closed checks for the public source bundle. Never prints matches.

This is a release safeguard, not a guarantee that arbitrary prose is anonymous.
Review the listed source files and use only fictional demonstrations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

# Every public path is reviewed. Add new files explicitly; never allow whole trees.
ALLOWED_FILES = frozenset({
    ".gitignore", ".gitattributes", ".github/workflows/ci.yml", "LICENSE", "README.md", "README.zh-CN.md",
    "PRIVACY.md", "SECURITY.md", "CONTRIBUTING.md", "requirements.txt",
    "service_agent.py", "start.command", "demo.py",
    "atlas/__init__.py", "atlas/analysis.py", "atlas/app.py", "atlas/ingest.py",
    "atlas/metadata.py", "atlas/model_settings.py", "atlas/server.py", "atlas/store.py",
    "tests/test_analysis.py", "tests/test_api.py", "tests/test_ingest.py",
    "tests/test_model_settings.py", "tests/test_server.py", "tests/test_store.py",
    "tests/test_release_safety.py", "tests/test_demo.py", "tests/test_security.py",
    "scripts/check_release.py", "scripts/package_release.py", "scripts/smoke_demo.py",
    "docs/demo.md", "docs/installation.md", "docs/troubleshooting.md",
    "web/package.json", "web/package-lock.json", "web/tsconfig.json", "web/scripts/check-i18n.mjs",
    "web/vite.config.ts", "web/index.html", "web/src/App.tsx", "web/src/api.ts",
    "web/src/main.tsx", "web/src/styles.css", "web/src/types.ts", "web/src/i18n.ts",
    "web/src/translations.ts", "web/src/errorMessages.ts",
})
EXCLUDED_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__",
                           ".pytest_cache", ".mypy_cache", ".ruff_cache", ".codegraph"})
EXCLUDED_PATHS = frozenset({"data", "web/dist", "tests/artifacts"})
EXCLUDED_FILES = frozenset({"web/tsconfig.tsbuildinfo"})
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024

# Only this reserved, explicitly fictional namespace is permitted in static data.
DEMO_UUID = re.compile(r"00000000-0000-4000-8000-[0-9a-f]{12}\Z", re.I)
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
HOME_PATH = re.compile(r"(?:" + "/Us" + "ers/|/ho" + r"me/)([^/\s\\\"'<>]+)|[A-Za-z]:[\\/]Users[\\/]([^\\/\s\"'<>]+)")
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)
SAFE_HOME_NAMES = {"example", "user", "username", "demo", "public", "shared", "runner"}
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "example.invalid", "example.test"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)https?://[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"(?i)\bBearer +[A-Za-z0-9_.-]{16,}"),
    re.compile(r"(?i)(?:api[_-]?key|password|client[_-]?secret|access[_-]?token)\s*[=:]\s*[\"'][A-Za-z0-9_./+-]{16,}[\"']"),
)
PRIVATE_MARKERS = ("ATLAS_PRIVATE_CANARY", "DO_NOT_PUBLISH_PRIVATE")


def issue(path: str, category: str, line: int | None = None) -> dict:
    item = {"path": path, "category": category}
    if line is not None:
        item["line"] = line
    return item


def inspect_bytes(path: str, content: bytes) -> list[dict]:
    findings = []
    if path not in ALLOWED_FILES:
        findings.append(issue(path, "unapproved_path"))
    if len(content) > MAX_FILE_BYTES:
        return findings + [issue(path, "file_too_large")]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return findings + [issue(path, "non_utf8_or_binary")]
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        findings.append(issue(path, "binary_control_character"))
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in HOME_PATH.finditer(line):
            name = next(value for value in match.groups() if value)
            if name.casefold() not in SAFE_HOME_NAMES:
                findings.append(issue(path, "personal_home_path", line_number))
        if any(not DEMO_UUID.fullmatch(match.group()) for match in UUID.finditer(line)):
            findings.append(issue(path, "non_demo_uuid", line_number))
        if any(match.group(1).lower() not in SAFE_EMAIL_DOMAINS for match in EMAIL.finditer(line)):
            findings.append(issue(path, "non_example_email", line_number))
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            findings.append(issue(path, "credential_pattern", line_number))
        # The definitions themselves are not private values; a marker elsewhere is a canary.
        if path != "scripts/check_release.py" and any(marker in line for marker in PRIVATE_MARKERS):
            findings.append(issue(path, "private_marker", line_number))
    return findings


def manifest_entry(path: str, content: bytes) -> dict:
    return {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def excluded(path: str, directory: bool) -> bool:
    parts = PurePosixPath(path).parts
    return any(part in EXCLUDED_DIRS for part in parts) or path in EXCLUDED_PATHS or (not directory and path in EXCLUDED_FILES)


def scan_directory(root: Path) -> dict:
    root = root.resolve()
    findings, files, skipped = [], [], []
    if not root.is_dir():
        return {"ok": False, "kind": "directory", "files": [], "excluded": [], "findings": [issue(".", "missing_directory")]}
    for current, directories, names in os.walk(root, followlinks=False):
        directories.sort()
        names.sort()
        for name in directories[:]:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                findings.append(issue(relative, "symlink"))
                directories.remove(name)
            elif excluded(relative, True):
                skipped.append(relative)
                directories.remove(name)
        for name in names:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                findings.append(issue(relative, "symlink"))
                continue
            if not stat.S_ISREG(mode):
                findings.append(issue(relative, "non_regular_file"))
                continue
            if excluded(relative, False):
                skipped.append(relative)
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                findings.append(issue(relative, "file_too_large"))
                continue
            content = path.read_bytes()
            findings.extend(inspect_bytes(relative, content))
            files.append(manifest_entry(relative, content))
    if sum(item["bytes"] for item in files) > MAX_TOTAL_BYTES:
        findings.append(issue(".", "bundle_too_large"))
    if not files:
        findings.append(issue(".", "empty_bundle"))
    return {"ok": not findings, "kind": "directory", "files": sorted(files, key=lambda item: item["path"]),
            "excluded": sorted(skipped), "findings": findings}


def scan_archive(archive: Path, expected_files: list[dict] | None = None) -> dict:
    findings, files, seen = [], [], set()
    total = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            if bundle.comment:
                findings.append(issue(".", "unexpected_archive_metadata"))
            for entry in bundle.infolist():
                path = entry.filename
                if entry.comment or entry.extra:
                    findings.append(issue(path, "unexpected_archive_metadata"))
                parts = PurePosixPath(path).parts
                if (path in seen or path.startswith("/") or "\\" in path or ".." in parts
                        or ":" in path or str(PurePosixPath(path)) != path):
                    findings.append(issue(path, "unsafe_or_duplicate_archive_path"))
                    continue
                seen.add(path)
                mode = entry.external_attr >> 16
                if entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                    findings.append(issue(path, "non_regular_archive_entry"))
                    continue
                total += entry.file_size
                if entry.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    findings.append(issue(path, "archive_size_limit"))
                    continue
                content = bundle.read(entry)
                findings.extend(inspect_bytes(path, content))
                files.append(manifest_entry(path, content))
    except (OSError, zipfile.BadZipFile, RuntimeError):
        findings.append(issue(".", "unreadable_archive"))
    files.sort(key=lambda item: item["path"])
    if not files:
        findings.append(issue(".", "empty_bundle"))
    if expected_files is not None and files != sorted(expected_files, key=lambda item: item["path"]):
        findings.append(issue(".", "source_archive_manifest_mismatch"))
    return {"ok": not findings, "kind": "archive", "files": files, "findings": findings}


def external_path(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved == root.resolve() or root.resolve() in resolved.parents:
        raise ValueError("Output and reports must be outside the release source directory.")
    return resolved


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Release source directory or ZIP archive")
    parser.add_argument("--report", type=Path, required=True, help="JSON report path outside the source directory")
    args = parser.parse_args()
    try:
        if args.source.is_dir():
            report_path = external_path(args.report, args.source)
            result = scan_directory(args.source)
        else:
            report_path = args.report.resolve()
            if report_path == args.source.resolve():
                raise ValueError("Report cannot overwrite the archive.")
            result = scan_archive(args.source)
        write_report(report_path, result)
    except (OSError, ValueError) as exc:
        print(type(exc).__name__ + ": unable to check or save report")
        return 2
    print(f"Release check: {'PASS' if result['ok'] else 'FAIL'}; {len(result['files'])} files; {len(result['findings'])} findings. See the JSON report.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
