#!/usr/bin/env python3
"""Export only reviewed sources, then independently inspect the actual ZIP bytes."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile
import zipfile

try:
    from .check_release import external_path, scan_archive, scan_directory, write_report
except ImportError:
    from check_release import external_path, scan_archive, scan_directory, write_report


def package_release(root: Path, output: Path, report_path: Path) -> dict:
    root = root.resolve()
    output = external_path(output, root)
    report_path = external_path(report_path, root)
    if output == report_path:
        raise ValueError("Archive and report paths must differ.")
    if output.exists():
        raise ValueError("Refusing to overwrite an existing archive.")
    source = scan_directory(root)
    report = {"ok": False, "source": source, "archive": None}
    if not source["ok"]:
        write_report(report_path, report)
        return report
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atlas-release-", dir=output.parent) as scratch:
        candidate = Path(scratch) / "candidate.zip"
        with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for entry in source["files"]:
                path = root / entry["path"]
                if any(parent.is_symlink() for parent in (path, *path.parents) if parent != root.parent):
                    raise ValueError("A source path became a symlink during export.")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                    raise ValueError("A source file changed during export; run the check again.")
                info = zipfile.ZipInfo(entry["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                executable = entry["path"] == "start.command"
                info.external_attr = (0o100755 if executable else 0o100644) << 16
                bundle.writestr(info, content)
        report["archive"] = scan_archive(candidate, source["files"])
        report["ok"] = report["archive"]["ok"]
        if report["ok"]:
            # Creating exclusively prevents overwriting an archive made concurrently.
            with output.open("xb") as destination:
                destination.write(candidate.read_bytes())
            report["archive_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
        write_report(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New ZIP path outside the release source directory")
    parser.add_argument("--report", type=Path, required=True, help="JSON report path outside the release source directory")
    args = parser.parse_args()
    try:
        result = package_release(args.source, args.output, args.report)
    except (OSError, ValueError):
        print("Release export failed: invalid output path, changing sources, or filesystem error.")
        return 2
    if result["ok"]:
        print(f"Release export: PASS; {len(result['archive']['files'])} reviewed files. No runtime data included.")
        return 0
    print("Release export: BLOCKED; see the JSON report. No archive created.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
