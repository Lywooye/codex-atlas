"""Offline release checks use generated canaries; never real account data."""
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from scripts.check_release import inspect_bytes, scan_archive, scan_directory
from scripts.package_release import package_release


class ReleaseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.base = Path(self.workspace.name)
        self.root = self.base / "source"
        self.root.mkdir()
        (self.root / "README.md").write_text("Fictional demo only.\n", encoding="utf-8")

    def categories(self, text):
        return {finding["category"] for finding in inspect_bytes("README.md", text.encode())}

    def test_source_export_and_actual_archive_match(self):
        (self.root / "data").mkdir()
        (self.root / "data" / "private.sqlite3").write_bytes(b"private runtime data")
        report_path = self.base / "report.json"
        archive = self.base / "release.zip"
        report = package_release(self.root, archive, report_path)
        self.assertTrue(report["ok"])
        self.assertEqual([item["path"] for item in report["archive"]["files"]], ["README.md"])
        self.assertEqual(report["source"]["files"], report["archive"]["files"])
        self.assertTrue(scan_archive(archive)["ok"])
        self.assertEqual(json.loads(report_path.read_text()), report)
        self.assertNotIn(str(self.root), report_path.read_text())
        self.assertIn("data", report["source"]["excluded"])

    def test_unrecognized_reports_and_binary_block_export(self):
        for name in ("preview.jpg", "token-cost-sample.json", "personal-report.md"):
            with self.subTest(name=name):
                path = self.root / name
                path.write_bytes(b"not public")
                report = package_release(self.root, self.base / "release.zip", self.base / "report.json")
                self.assertFalse(report["ok"])
                self.assertFalse((self.base / "release.zip").exists())
                path.unlink()
        self.assertIn("non_utf8_or_binary", {item["category"] for item in inspect_bytes("README.md", b"\xff\xd8")})
        self.assertIn("binary_control_character", {item["category"] for item in inspect_bytes("README.md", b"a\x00b")})

    def test_symlinks_fail_even_when_their_names_are_excluded(self):
        target = self.base / "elsewhere"
        target.mkdir()
        (self.root / "data").symlink_to(target, target_is_directory=True)
        self.assertIn("symlink", {item["category"] for item in scan_directory(self.root)["findings"]})
        (self.root / "data").unlink()
        (self.root / "start.command").symlink_to(self.root / "README.md")
        self.assertIn("symlink", {item["category"] for item in scan_directory(self.root)["findings"]})

    def test_private_identifiers_and_credentials_are_detected_without_echo(self):
        cases = [
            ("/Users/" + "private-person/documents", "personal_home_path"),
            ("/home/" + "private-person/documents", "personal_home_path"),
            ("C:\\Users\\" + "private-person\\documents", "personal_home_path"),
            ("private.person" + "@" + "nonpublic.invalid", "non_example_email"),
            ("12345678" + "-1234-4123-8123-123456789abc", "non_demo_uuid"),
            ("sk-" + "a" * 30, "credential_pattern"),
            ("ghp_" + "b" * 30, "credential_pattern"),
            ("-----BEGIN " + "PRIVATE KEY-----", "credential_pattern"),
            ("ATLAS_PRIVATE_" + "CANARY", "private_marker"),
        ]
        for text, category in cases:
            with self.subTest(category=category):
                findings = inspect_bytes("README.md", text.encode())
                self.assertIn(category, {item["category"] for item in findings})
                self.assertNotIn(text, json.dumps(findings))

    def test_only_explicit_synthetic_identifiers_are_allowed(self):
        text = "/Users/example/demo\nuser@example.com\n00000000-0000-4000-8000-000000000001\n"
        self.assertEqual(inspect_bytes("README.md", text.encode()), [])

    def test_archive_rejects_links_traversal_duplicates_and_runtime_data(self):
        for name, mode in [("../README.md", 0o100644), ("data/private.txt", 0o100644),
                           ("README.md", stat.S_IFLNK | 0o777)]:
            with self.subTest(name=name, mode=mode):
                archive = self.base / "hostile.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = mode << 16
                    bundle.writestr(info, "example")
                self.assertFalse(scan_archive(archive)["ok"])
        archive = self.base / "duplicate.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("README.md", "example")
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                bundle.writestr("README.md", "different")
        self.assertIn("unsafe_or_duplicate_archive_path", {item["category"] for item in scan_archive(archive)["findings"]})

    def test_archive_comments_cannot_hide_private_values(self):
        archive = self.base / "commented.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.comment = b"private account metadata"
            info = zipfile.ZipInfo("README.md")
            info.comment = b"private source metadata"
            bundle.writestr(info, "fictional")
        result = scan_archive(archive)
        self.assertFalse(result["ok"])
        self.assertEqual({item["category"] for item in result["findings"]}, {"unexpected_archive_metadata"})

    def test_archive_content_is_rescanned_and_manifest_compared(self):
        archive = self.base / "modified.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("README.md", "sk-" + "x" * 30)
        result = scan_archive(archive, scan_directory(self.root)["files"])
        self.assertEqual({item["category"] for item in result["findings"]},
                         {"credential_pattern", "source_archive_manifest_mismatch"})

    def test_reports_and_archives_cannot_pollute_source_or_overwrite_each_other(self):
        with self.assertRaises(ValueError):
            package_release(self.root, self.root / "release.zip", self.base / "report.json")
        with self.assertRaises(ValueError):
            package_release(self.root, self.base / "release.zip", self.root / "report.json")
        with self.assertRaises(ValueError):
            package_release(self.root, self.base / "same", self.base / "same")
        existing = self.base / "release.zip"
        existing.write_bytes(b"keep existing")
        with self.assertRaises(ValueError):
            package_release(self.root, existing, self.base / "report.json")
        self.assertEqual(existing.read_bytes(), b"keep existing")


if __name__ == "__main__":
    unittest.main()
