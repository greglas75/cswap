"""Small: loginExpiresAt — when a stored login's refresh token lapses (ported
from realiti4/claude-swap 8d7547b), so the status page can warn before an
account turns relogin_required."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from claude_swap import oauth
from claude_swap.json_output import account_row
from tests.test_autoswitch import EngineHarness


class TestLoginExpiresAtIso:
    def test_refresh_token_expiry_is_reported_as_iso_utc(self):
        creds = json.dumps({"claudeAiOauth": {"accessToken": "sk-x", "refreshTokenExpiresAt": 1791421596865}})
        assert oauth.login_expires_at_iso(creds) == "2026-10-08T01:06:36Z"

    @pytest.mark.parametrize("creds", [
        "", "not json",
        json.dumps({"claudeAiOauth": {"accessToken": "sk-x"}}),
        json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": "soon"}}),
        json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": True}}),
        json.dumps({"claudeAiOauth": {"refreshTokenExpiresAt": 0}}),
        json.dumps({"other": {}}),
    ])
    def test_anything_but_a_positive_epoch_is_unknown(self, creds):
        assert oauth.login_expires_at_iso(creds) is None


def test_the_row_carries_it_only_when_known():
    row = account_row(1, "a@x.com", "", "", True, None, login_expires_at="2026-10-08T01:06:36Z")
    assert row["loginExpiresAt"] == "2026-10-08T01:06:36Z"
    assert "loginExpiresAt" not in account_row(1, "a@x.com", "", "", True, None)


def test_list_json_reports_each_slots_own_login_expiry(temp_home):
    h = EngineHarness(temp_home)
    h.seed(1, "a@x.com")
    h.seed(2, "b@x.com")
    h.make_live("a@x.com", 1)
    h.switcher._write_account_credentials("2", "b@x.com", json.dumps({"claudeAiOauth": {
        "accessToken": "sk-2", "refreshToken": "rt-2", "refreshTokenExpiresAt": 1791421596865}}))
    with patch("claude_swap.oauth.try_fetch_usage_for_account", return_value=oauth.UsageOutcome(None)):
        payload = h.switcher.list_accounts(json_output=True)
    rows = {a["number"]: a for a in payload["accounts"]}
    assert rows[2]["loginExpiresAt"] == "2026-10-08T01:06:36Z"
    assert "loginExpiresAt" not in rows[1]
