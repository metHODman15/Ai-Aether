"""Tests for the query layer of backend.salesforce_client.

All tests are offline — no real Salesforce connection is made.
The simple_salesforce.Salesforce object is mocked at the call site.
"""
from __future__ import annotations

import re
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

import pytest
from simple_salesforce.exceptions import (
    SalesforceAuthenticationFailed,
    SalesforceError,
    SalesforceExpiredSession,
)

from backend.oauth import OAuthError
from backend.salesforce_client import (
    SalesforceClient,
    CrmResult,
    _stage_distribution,
    _amount_timeline,
    _has_searchable_input,
)
from backend.entities import Entities


def _sf_mock() -> MagicMock:
    """Return a minimal mock that behaves like simple_salesforce.Salesforce."""
    sf = MagicMock()
    sf.query.return_value = {"records": []}
    return sf


def _entities(**kwargs) -> Entities:
    base = Entities(
        customer_name=None, contact_name=None,
        deal_amount=None, deal_stage=None, keywords=[],
    )
    base.update(kwargs)
    return base


def _make_client() -> SalesforceClient:
    """Construct a SalesforceClient with a mocked TokenStore."""
    store = MagicMock()
    store.has_tokens.return_value = True
    return SalesforceClient(
        token_store=store,
        sf_client_id="fake-id",
        sf_client_secret="fake-secret",
    )


class TestHasSearchableInput:
    def test_all_null_returns_false(self):
        assert not _has_searchable_input(_entities())

    def test_customer_name_is_searchable(self):
        assert _has_searchable_input(_entities(customer_name="Acme"))

    def test_keywords_is_searchable(self):
        assert _has_searchable_input(_entities(keywords=["cloud"]))


class TestSearchAccounts:
    def test_no_results_returns_empty(self):
        sf = _sf_mock()
        result = SalesforceClient._search_accounts(sf, "Acme")
        assert result == []
        sf.query.assert_called_once()

    def test_results_returned_as_list(self):
        sf = _sf_mock()
        sf.query.return_value = {
            "records": [{"Id": "001ABC123456789", "Name": "Acme Corp"}]
        }
        result = SalesforceClient._search_accounts(sf, "Acme")
        assert len(result) == 1
        assert result[0]["Name"] == "Acme Corp"


class TestSearchOpportunities:
    def test_no_results_returns_empty(self):
        sf = _sf_mock()
        result = SalesforceClient._search_opportunities(sf, "renewal")
        assert result == []

    def test_results_returned(self):
        sf = _sf_mock()
        sf.query.return_value = {
            "records": [{"Id": "006ABCDEFABCDEF", "Name": "Acme Renewal", "StageName": "Closed Won"}]
        }
        result = SalesforceClient._search_opportunities(sf, "renewal")
        assert result[0]["Name"] == "Acme Renewal"


class TestOpportunitiesByAccounts:
    def test_empty_account_ids_returns_empty(self):
        sf = _sf_mock()
        result = SalesforceClient._opportunities_by_accounts(sf, set())
        sf.query.assert_not_called()
        assert result == []

    def test_valid_ids_are_queried(self):
        sf = _sf_mock()
        sf.query.return_value = {"records": [{"Id": "006ABCDEFABCDEF", "Name": "Deal"}]}
        result = SalesforceClient._opportunities_by_accounts(sf, {"001ABCDEFABCDEF"})
        sf.query.assert_called_once()
        assert result[0]["Name"] == "Deal"

    def test_invalid_id_formats_are_filtered(self):
        sf = _sf_mock()
        sf.query.return_value = {"records": []}
        bad_ids = {"not-an-id", "'; DROP TABLE Account; --", "tooshort"}
        result = SalesforceClient._opportunities_by_accounts(sf, bad_ids)
        sf.query.assert_not_called()
        assert result == []

    def test_mixed_valid_invalid_ids_only_uses_valid(self):
        sf = _sf_mock()
        sf.query.return_value = {"records": []}
        valid_id = "001ABCDEFABCDEF"
        bad_id = "'; DROP TABLE"
        SalesforceClient._opportunities_by_accounts(sf, {valid_id, bad_id})
        sf.query.assert_called_once()
        query_str = sf.query.call_args[0][0]
        assert "DROP TABLE" not in query_str


class TestSoqlInjectionVectors:
    def test_apostrophe_escaped_in_account_search(self):
        """format_soql must escape the apostrophe so the string literal is not terminated."""
        sf = _sf_mock()
        SalesforceClient._search_accounts(sf, "O'Brien")
        query_str = sf.query.call_args[0][0]
        assert "O\\'Brien" in query_str

    def test_apostrophe_escaped_in_opportunity_search(self):
        """Leading apostrophe in adversarial input must be escaped."""
        sf = _sf_mock()
        SalesforceClient._search_opportunities(sf, "'; DROP TABLE Account; --")
        query_str = sf.query.call_args[0][0]
        assert sf.query.called
        assert "\\'" in query_str

    def test_backslash_in_account_search_is_escaped(self):
        """Backslashes in search terms must be escaped in the generated SOQL."""
        sf = _sf_mock()
        SalesforceClient._search_accounts(sf, "name\\with\\backslashes")
        sf.query.assert_called_once()
        query_str = sf.query.call_args[0][0]
        assert "\\\\" in query_str, "Backslash must be escaped as \\\\\\\\ in SOQL"

    def test_apostrophe_in_opportunity_search_escaped(self):
        sf = _sf_mock()
        SalesforceClient._search_opportunities(sf, "O'Reilly")
        query_str = sf.query.call_args[0][0]
        assert "O\\'Reilly" in query_str


class TestStageDistribution:
    def test_empty_list_returns_empty(self):
        assert _stage_distribution([]) == []

    def test_counts_and_amounts_grouped_by_stage(self):
        opps = [
            {"StageName": "Prospecting", "Amount": 1000},
            {"StageName": "Closed Won", "Amount": 5000},
            {"StageName": "Prospecting", "Amount": 2000},
        ]
        result = _stage_distribution(opps)
        by_stage = {r["stage"]: r for r in result}
        assert by_stage["Prospecting"]["count"] == 2
        assert by_stage["Prospecting"]["amount"] == 3000.0
        assert by_stage["Closed Won"]["count"] == 1

    def test_missing_amount_defaults_to_zero(self):
        opps = [{"StageName": "Qualification", "Amount": None}]
        result = _stage_distribution(opps)
        assert result[0]["amount"] == 0.0


class TestAmountTimeline:
    def test_empty_list_returns_empty(self):
        assert _amount_timeline([]) == []

    def test_amounts_grouped_by_close_date(self):
        opps = [
            {"CloseDate": "2025-01-01", "Amount": 1000},
            {"CloseDate": "2025-03-01", "Amount": 2000},
            {"CloseDate": "2025-01-01", "Amount": 500},
        ]
        result = _amount_timeline(opps)
        by_date = {r["date"]: r["amount"] for r in result}
        assert by_date["2025-01-01"] == 1500.0
        assert by_date["2025-03-01"] == 2000.0

    def test_result_sorted_by_date(self):
        opps = [
            {"CloseDate": "2025-12-01", "Amount": 100},
            {"CloseDate": "2025-01-01", "Amount": 200},
        ]
        result = _amount_timeline(opps)
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)

    def test_missing_date_or_amount_skipped(self):
        opps = [
            {"CloseDate": None, "Amount": 1000},
            {"CloseDate": "2025-06-01", "Amount": None},
            {"CloseDate": "2025-06-01", "Amount": 500},
        ]
        result = _amount_timeline(opps)
        assert len(result) == 1
        assert result[0]["amount"] == 500.0


class TestQueryForEntities:
    @pytest.mark.asyncio
    async def test_all_null_entities_returns_empty_without_calling_sf(self):
        """Early exit: if no searchable input, _ensure_session must not be called."""
        client = _make_client()
        with patch.object(client, "_ensure_session", new_callable=AsyncMock) as mock_ensure:
            result = await client.query_for_entities(_entities())
        mock_ensure.assert_not_called()
        assert result["accounts"] == []
        assert result["opportunities"] == []
        assert result["stage_distribution"] == []
        assert result["amount_timeline"] == []

    @pytest.mark.asyncio
    async def test_searchable_entities_calls_ensure_session_and_query_sync(self):
        """With a customer_name, _ensure_session and _query_sync should each be called once."""
        client = _make_client()
        sf = _sf_mock()
        expected = CrmResult(
            accounts=[{"Id": "001ABCDEFABCDEF", "Name": "Acme"}],
            opportunities=[],
            stage_distribution=[],
            amount_timeline=[],
        )
        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_query_sync", return_value=expected) as mock_query:
                result = await client.query_for_entities(_entities(customer_name="Acme"))
        mock_query.assert_called_once_with(sf, _entities(customer_name="Acme"))
        assert result["accounts"][0]["Name"] == "Acme"

    @pytest.mark.asyncio
    async def test_none_sf_session_returns_empty_result(self):
        """If _ensure_session returns None (not authorized), return empty CrmResult."""
        client = _make_client()
        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=None):
            result = await client.query_for_entities(_entities(customer_name="Acme"))
        assert result["accounts"] == []

    @pytest.mark.asyncio
    async def test_expired_session_triggers_refresh_and_retries_successfully(self):
        """SalesforceExpiredSession on first call → _handle_auth_error → retry succeeds."""
        client = _make_client()
        sf = _sf_mock()
        refreshed_sf = _sf_mock()
        expected = CrmResult(
            accounts=[{"Id": "001ABCDEFABCDEF", "Name": "Acme"}],
            opportunities=[],
            stage_distribution=[],
            amount_timeline=[],
        )
        expired_exc = SalesforceExpiredSession("http://sf.example.com", 401, "query", b"expired")

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_handle_auth_error", new_callable=AsyncMock,
                              return_value=refreshed_sf):
                with patch.object(client, "_query_sync",
                                  side_effect=[expired_exc, expected]):
                    with patch.object(client, "_set_online", new_callable=AsyncMock):
                        result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"][0]["Name"] == "Acme"

    @pytest.mark.asyncio
    async def test_expired_session_refresh_failure_returns_empty(self):
        """SalesforceExpiredSession and _handle_auth_error returns None → empty result."""
        client = _make_client()
        sf = _sf_mock()
        expired_exc = SalesforceExpiredSession("http://sf.example.com", 401, "query", b"expired")

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_handle_auth_error", new_callable=AsyncMock,
                              return_value=None):
                with patch.object(client, "_query_sync", side_effect=expired_exc):
                    result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"] == []
        assert result["opportunities"] == []

    @pytest.mark.asyncio
    async def test_expired_session_refresh_succeeds_but_retry_fails(self):
        """After token refresh, retry raises SalesforceError → clears session, returns empty."""
        client = _make_client()
        sf = _sf_mock()
        refreshed_sf = _sf_mock()
        expired_exc = SalesforceExpiredSession("http://sf.example.com", 401, "query", b"expired")
        retry_exc = SalesforceError("http://sf.example.com", 500, "query", b"failed")

        def _query_side_effect(session, entities):
            if session is sf:
                raise expired_exc
            raise retry_exc

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_handle_auth_error", new_callable=AsyncMock,
                              return_value=refreshed_sf):
                with patch.object(client, "_query_sync", side_effect=_query_side_effect):
                    with patch.object(client, "_set_online", new_callable=AsyncMock):
                        result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"] == []
        assert client._sf is None

    @pytest.mark.asyncio
    async def test_auth_failed_triggers_refresh_and_retries_successfully(self):
        """SalesforceAuthenticationFailed on first call → _handle_auth_error → retry succeeds."""
        client = _make_client()
        sf = _sf_mock()
        refreshed_sf = _sf_mock()
        expected = CrmResult(
            accounts=[{"Id": "001ABCDEFABCDEF", "Name": "Acme"}],
            opportunities=[],
            stage_distribution=[],
            amount_timeline=[],
        )
        auth_exc = SalesforceAuthenticationFailed(401, "Auth failed")

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_handle_auth_error", new_callable=AsyncMock,
                              return_value=refreshed_sf):
                with patch.object(client, "_query_sync",
                                  side_effect=[auth_exc, expected]):
                    with patch.object(client, "_set_online", new_callable=AsyncMock):
                        result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"][0]["Name"] == "Acme"

    @pytest.mark.asyncio
    async def test_auth_failed_oauth_error_clears_store_and_fires_auth_required(self):
        """SalesforceAuthenticationFailed + OAuthError during refresh → store cleared, callback fires."""
        auth_required_calls = []

        async def _on_auth_required() -> None:
            auth_required_calls.append(True)

        store = MagicMock()
        store.has_tokens.return_value = True
        store.load.return_value = {
            "refresh_token": "old-refresh-token",
            "access_token": "old-access-token",
            "instance_url": "https://test.salesforce.com",
        }
        client = SalesforceClient(
            token_store=store,
            sf_client_id="fake-id",
            sf_client_secret="fake-secret",
            on_auth_required=_on_auth_required,
        )

        sf = _sf_mock()
        auth_exc = SalesforceAuthenticationFailed(401, "Auth failed")

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_query_sync", side_effect=auth_exc):
                with patch("backend.salesforce_client.refresh_access_token",
                           side_effect=OAuthError("token revoked")):
                    result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"] == []
        store.clear.assert_called_once(), "token store must be cleared after OAuthError"
        assert auth_required_calls, "on_auth_required must be called when refresh fails"

    @pytest.mark.asyncio
    async def test_generic_exception_logs_warning_returns_empty(self, caplog):
        """Any unexpected exception → logs warning, clears session, returns empty result."""
        import logging
        client = _make_client()
        sf = _sf_mock()

        with caplog.at_level(logging.WARNING, logger="backend.salesforce_client"):
            with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
                with patch.object(client, "_query_sync",
                                  side_effect=RuntimeError("Completely unexpected error")):
                    with patch.object(client, "_set_online", new_callable=AsyncMock):
                        result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"] == []
        assert result["opportunities"] == []
        assert client._sf is None
        assert any("Completely unexpected error" in r.message for r in caplog.records), (
            "Expected a warning log containing the exception message"
        )


class TestQueryForEntitiesExtraErrorPaths:
    @pytest.mark.asyncio
    async def test_auth_failed_retry_raises_salesforce_error_returns_empty(self):
        """SalesforceAuthenticationFailed + retry SalesforceError → clears session, empty result."""
        client = _make_client()
        sf = _sf_mock()
        refreshed_sf = _sf_mock()
        auth_exc = SalesforceAuthenticationFailed(401, "Auth failed")
        retry_exc = SalesforceError("http://sf.example.com", 500, "query", b"retry failed")

        def _query_side_effect(session, entities):
            if session is sf:
                raise auth_exc
            raise retry_exc

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_handle_auth_error", new_callable=AsyncMock,
                              return_value=refreshed_sf):
                with patch.object(client, "_query_sync", side_effect=_query_side_effect):
                    with patch.object(client, "_set_online", new_callable=AsyncMock):
                        result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"] == []
        assert client._sf is None

    @pytest.mark.asyncio
    async def test_salesforce_error_on_initial_query_clears_session(self):
        """A SalesforceError (not expired/auth) on the first call → clears session, empty result."""
        client = _make_client()
        sf = _sf_mock()
        sf_err = SalesforceError("http://sf.example.com", 503, "query", b"unavailable")

        with patch.object(client, "_ensure_session", new_callable=AsyncMock, return_value=sf):
            with patch.object(client, "_query_sync", side_effect=sf_err):
                with patch.object(client, "_set_online", new_callable=AsyncMock):
                    result = await client.query_for_entities(_entities(customer_name="Acme"))

        assert result["accounts"] == []
        assert client._sf is None


class TestHelperMethods:
    @pytest.mark.asyncio
    async def test_set_online_calls_status_change_callback(self):
        """_set_online triggers on_status_change when the status actually changes."""
        status_events = []

        async def _on_status_change(online: bool, reason: str | None) -> None:
            status_events.append((online, reason))

        store = MagicMock()
        store.has_tokens.return_value = True
        client = SalesforceClient(
            token_store=store,
            sf_client_id="fake-id",
            sf_client_secret="fake-secret",
            on_status_change=_on_status_change,
        )
        client._is_online = True

        await client._set_online(False, "test reason")

        assert status_events == [(False, "test reason")]

    @pytest.mark.asyncio
    async def test_set_online_no_op_when_status_unchanged(self):
        """_set_online does nothing when the status doesn't change."""
        called = []

        async def _on_status_change(online: bool, reason: str | None) -> None:
            called.append(True)

        store = MagicMock()
        store.has_tokens.return_value = True
        client = SalesforceClient(
            token_store=store,
            sf_client_id="fake-id",
            sf_client_secret="fake-secret",
            on_status_change=_on_status_change,
        )
        client._is_online = False

        await client._set_online(False, "no change")

        assert not called

    @pytest.mark.asyncio
    async def test_emit_loading_calls_on_loading_callback(self):
        """_emit_loading triggers the on_loading callback when one is set."""
        loading_events = []

        async def _on_loading(loading: bool) -> None:
            loading_events.append(loading)

        store = MagicMock()
        store.has_tokens.return_value = True
        client = SalesforceClient(
            token_store=store,
            sf_client_id="fake-id",
            sf_client_secret="fake-secret",
            on_loading=_on_loading,
        )

        await client._emit_loading(True)
        await client._emit_loading(False)

        assert loading_events == [True, False]

    def test_notify_reauthorized_clears_session_and_stage_cache(self):
        """notify_reauthorized must set _sf to None and reset stage cache timestamp."""
        client = _make_client()
        client._sf = MagicMock()
        client._stages_fetched_at = 999.9
        client._last_activity = 999.9

        client.notify_reauthorized()

        assert client._sf is None
        assert client._stages_fetched_at == 0.0
        assert client._last_activity == 0.0

    def test_is_online_property_reflects_internal_state(self):
        """is_online property must return the current _is_online value."""
        client = _make_client()
        client._is_online = False
        assert client.is_online is False
        client._is_online = True
        assert client.is_online is True

    def test_get_stage_names_returns_cached_stages(self):
        """get_stage_names must return the current _stages tuple."""
        client = _make_client()
        assert client.get_stage_names() == client._stages

    def test_is_authorized_delegates_to_token_store(self):
        """is_authorized must delegate to the token store."""
        client = _make_client()
        client._store.has_tokens.return_value = True
        assert client.is_authorized() is True
        client._store.has_tokens.return_value = False
        assert client.is_authorized() is False

    @pytest.mark.asyncio
    async def test_warm_up_calls_ensure_session(self):
        """warm_up must call _ensure_session exactly once."""
        client = _make_client()
        with patch.object(client, "_ensure_session", new_callable=AsyncMock,
                          return_value=None) as mock_ensure:
            await client.warm_up()
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_emit_loading_swallows_callback_exception(self):
        """_emit_loading must not propagate exceptions raised by the on_loading callback."""
        async def _bad_loading(loading: bool) -> None:
            raise RuntimeError("callback blew up")

        store = MagicMock()
        store.has_tokens.return_value = True
        client = SalesforceClient(
            token_store=store,
            sf_client_id="fake-id",
            sf_client_secret="fake-secret",
            on_loading=_bad_loading,
        )
        await client._emit_loading(True)

    @pytest.mark.asyncio
    async def test_set_online_swallows_callback_exception(self):
        """_set_online must not propagate exceptions raised by on_status_change."""
        async def _bad_callback(online: bool, reason: str | None) -> None:
            raise RuntimeError("status callback blew up")

        store = MagicMock()
        store.has_tokens.return_value = True
        client = SalesforceClient(
            token_store=store,
            sf_client_id="fake-id",
            sf_client_secret="fake-secret",
            on_status_change=_bad_callback,
        )
        client._is_online = True
        await client._set_online(False, "error")

    @pytest.mark.asyncio
    async def test_fire_auth_required_swallows_callback_exception(self):
        """_fire_auth_required must not propagate exceptions from the on_auth_required callback."""
        async def _bad_auth_required() -> None:
            raise RuntimeError("auth callback blew up")

        store = MagicMock()
        store.has_tokens.return_value = True
        client = SalesforceClient(
            token_store=store,
            sf_client_id="fake-id",
            sf_client_secret="fake-secret",
            on_auth_required=_bad_auth_required,
        )
        await client._fire_auth_required()


class TestQuerySync:
    def test_single_term_calls_search_accounts_and_opportunities_once(self):
        """One customer_name → _search_accounts and _search_opportunities called once each."""
        client = _make_client()
        sf = _sf_mock()
        with patch.object(SalesforceClient, "_search_accounts", return_value=[]) as mock_accs:
            with patch.object(SalesforceClient, "_search_opportunities", return_value=[]) as mock_opps:
                client._query_sync(sf, _entities(customer_name="Acme"))
        mock_accs.assert_called_once_with(sf, "Acme")
        mock_opps.assert_called_once_with(sf, "Acme")

    def test_account_ids_deduplicate_second_pass_opportunities(self):
        """Opp returned by _search_opportunities AND _opportunities_by_accounts → appears once."""
        client = _make_client()
        sf = _sf_mock()
        acc = {"Id": "001ABCDEFABCDEF", "Name": "Acme Corp"}
        shared_opp = {"Id": "006ABCDEFABCDEF", "Name": "Acme Renewal", "StageName": "Prospecting",
                      "Amount": 5000, "CloseDate": "2025-12-01", "AccountId": "001ABCDEFABCDEF",
                      "Account.Name": "Acme Corp"}

        with patch.object(SalesforceClient, "_search_accounts", return_value=[acc]):
            with patch.object(SalesforceClient, "_search_opportunities", return_value=[shared_opp]):
                with patch.object(SalesforceClient, "_opportunities_by_accounts",
                                  return_value=[shared_opp]) as mock_second:
                    result = client._query_sync(sf, _entities(customer_name="Acme"))

        mock_second.assert_called_once()
        ids = [o["Id"] for o in result["opportunities"]]
        assert ids.count("006ABCDEFABCDEF") == 1, "Duplicate opp must be deduplicated"

    def test_all_null_entities_returns_empty_without_calling_sf(self):
        client = _make_client()
        sf = _sf_mock()
        with patch.object(SalesforceClient, "_search_accounts", return_value=[]) as mock_accs:
            result = client._query_sync(sf, _entities())
        mock_accs.assert_not_called()
        assert result["accounts"] == []

    def test_unique_keyword_added_to_search_terms(self):
        """A keyword not already in customer/contact names is added as a search term."""
        client = _make_client()
        sf = _sf_mock()
        searched_terms = []

        def _capture_accounts(sf_session, term):
            searched_terms.append(term)
            return []

        with patch.object(SalesforceClient, "_search_accounts", side_effect=_capture_accounts):
            with patch.object(SalesforceClient, "_search_opportunities", return_value=[]):
                client._query_sync(sf, _entities(customer_name="Acme", keywords=["cloud", "Acme"]))

        assert "cloud" in searched_terms
        assert searched_terms.count("Acme") == 1, "Duplicate keyword must not be repeated"

    def test_opportunities_by_accounts_adds_new_opps(self):
        """Opp returned only by _opportunities_by_accounts (not search) → included in result."""
        client = _make_client()
        sf = _sf_mock()
        acc = {"Id": "001ABCDEFABCDEF", "Name": "Acme Corp"}
        search_opp = {"Id": "006SEARCH000000", "Name": "Search Hit", "StageName": "Prospecting",
                      "Amount": 1000, "CloseDate": "2025-12-01"}
        account_opp = {"Id": "006ACCOUNT00000", "Name": "Account Hit", "StageName": "Closed Won",
                       "Amount": 2000, "CloseDate": "2025-06-01"}

        with patch.object(SalesforceClient, "_search_accounts", return_value=[acc]):
            with patch.object(SalesforceClient, "_search_opportunities", return_value=[search_opp]):
                with patch.object(SalesforceClient, "_opportunities_by_accounts",
                                  return_value=[account_opp]):
                    result = client._query_sync(sf, _entities(customer_name="Acme"))

        opp_ids = [o["Id"] for o in result["opportunities"]]
        assert "006SEARCH000000" in opp_ids
        assert "006ACCOUNT00000" in opp_ids
