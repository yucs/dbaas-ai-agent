from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from dbass_ai_agent.infra.logging import (
    extract_session_id_from_path,
    redact_log_text,
    sanitize_log_value,
)


class LoggingUtilityTests(unittest.TestCase):
    def test_extract_session_id_from_path(self) -> None:
        self.assertEqual(
            extract_session_id_from_path("/api/v1/sessions/sess_001/messages/stream"),
            "sess_001",
        )
        self.assertEqual(
            extract_session_id_from_path("/api/v1/sessions/sess-001/approvals"),
            "sess-001",
        )
        self.assertEqual(extract_session_id_from_path("/api/v1/sessions"), "-")
        self.assertEqual(extract_session_id_from_path("/healthz"), "-")
        self.assertEqual(extract_session_id_from_path("/internal/sessions/sess_001"), "-")
        self.assertEqual(extract_session_id_from_path("/api/v1/sessions/bad id"), "-")

    def test_sanitize_log_value_removes_unsafe_characters(self) -> None:
        self.assertEqual(sanitize_log_value(" admin\nuser "), "admin_user")
        self.assertEqual(sanitize_log_value(""), "-")
        self.assertEqual(sanitize_log_value(None), "-")

    def test_redact_log_text_masks_common_secrets(self) -> None:
        redacted = redact_log_text("api_key=abc123\nAuthorization: Bearer token-value")
        self.assertIn("[redacted]", redacted)
        self.assertNotIn("abc123", redacted)
        self.assertNotIn("token-value", redacted)

    def test_redact_log_text_truncates_long_input(self) -> None:
        redacted = redact_log_text("x" * 20, max_length=8)
        self.assertEqual(redacted, "xxxxxxxx...")


if __name__ == "__main__":
    unittest.main()
