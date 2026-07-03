import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from prismax.client import PrismaXClient
from prismax.manifest import build_manifest_payload, manifest_placeholder
from prismax.errors import PrismaxApiError, PrismaxValidationError
from prismax.scanner import episode_keys, scan_folder, validate_mcap_mp4
from prismax.upload import wait_for_upload


class UploadHelperTests(unittest.TestCase):
    def test_scan_validate_and_build_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")
            (root / "1" / "high.mp4").write_bytes(b"env")
            (root / "1" / "_MANIFEST.json").write_text("ignored")

            files = scan_folder(root)

            self.assertEqual(
                sorted(item.relative_path for item in files),
                ["1.mcap", "1/high.mp4", "1/left.mp4", "1/right.mp4"],
            )
            self.assertEqual(validate_mcap_mp4(files), [])
            self.assertEqual(episode_keys(files), ["1"])
            self.assertEqual(
                manifest_placeholder("1"),
                {
                    "relative_path": "1/_MANIFEST.json",
                    "size_bytes": None,
                    "content_type": "application/json",
                },
            )

            manifest = build_manifest_payload(
                episode_key="1",
                upload_id=123,
                machine_id="machine-1",
                task_id=9,
                files=files,
            )

            self.assertEqual(manifest["manifest_version"], 1)
            self.assertEqual(manifest["upload_id"], 123)
            self.assertEqual(manifest["episode_key"], "1")
            self.assertEqual(len(manifest["files"]), 4)

    def test_validate_rejects_missing_mcap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1").mkdir()
            (root / "1" / "left.mp4").write_bytes(b"left")
            (root / "1" / "right.mp4").write_bytes(b"right")
            (root / "1" / "high.mp4").write_bytes(b"env")

            files = scan_folder(root)
            errors = validate_mcap_mp4(files)

            self.assertTrue(any("exactly 1 .mcap" in error for error in errors))

    def test_validate_rejects_nested_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "1" / "nested").mkdir(parents=True)
            (root / "1.mcap").write_bytes(b"mcap")
            (root / "1" / "nested" / "left.mp4").write_bytes(b"left")

            files = scan_folder(root)
            errors = validate_mcap_mp4(files)

            self.assertTrue(any("Nested folders are not allowed" in error for error in errors))

    def test_wait_for_upload_times_out(self):
        mock_client = Mock()
        mock_client.get_upload.return_value = {"upload_id": 123, "status": "UPLOADING"}
        upload_module = importlib.import_module("prismax.upload")

        with patch.object(upload_module, "PrismaXClient", return_value=mock_client):
            with self.assertRaises(PrismaxApiError):
                wait_for_upload(
                    123,
                    api_key="test",
                    poll_interval=1,
                    max_wait=0,
                )

    def test_create_upload_session_sends_serial_number(self):
        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {"success": True, "data": {"upload_id": 1}}

        with patch("prismax.client.requests.request", return_value=mock_response) as request_mock:
            client = PrismaXClient(api_key="pxu_test", base_url="https://example.test")
            client.create_upload_session(
                task_id=12,
                serial_number="MD100101000019205Z00082",
                files=[],
            )

        request_mock.assert_called_once()
        _, _, kwargs = request_mock.mock_calls[0]
        self.assertEqual(kwargs["json"]["serial_number"], "MD100101000019205Z00082")
        self.assertNotIn("machine_id", kwargs["json"])

    def test_client_wraps_request_exceptions(self):
        client = PrismaXClient(api_key="pxu_test", base_url="https://example.test")

        with patch("prismax.client.requests.request", side_effect=requests.Timeout("timed out")):
            with self.assertRaises(PrismaxApiError) as ctx:
                client.get_upload(123)

        self.assertIn("PrismaX API request failed", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, requests.Timeout)

    def test_base_url_rejects_remote_http(self):
        with self.assertRaises(PrismaxValidationError):
            PrismaXClient(api_key="pxu_test", base_url="http://evil.example.com")

    def test_base_url_allows_localhost_http(self):
        client = PrismaXClient(api_key="pxu_test", base_url="http://127.0.0.1:8082")
        self.assertEqual(client.base_url, "http://127.0.0.1:8082")

    def test_base_url_allows_https(self):
        client = PrismaXClient(api_key="pxu_test", base_url="https://data.prismaxserver.com")
        self.assertEqual(client.base_url, "https://data.prismaxserver.com")

    def test_base_url_env_var_is_not_honored(self):
        with patch.dict(os.environ, {"PRISMAX_BASE_URL": "http://evil.example.com"}, clear=False):
            client = PrismaXClient(api_key="pxu_test")
        self.assertEqual(client.base_url, "https://data.prismaxserver.com")


if __name__ == "__main__":
    unittest.main()
