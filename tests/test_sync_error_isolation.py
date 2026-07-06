"""Tests that a single source failure doesn't abort the whole sync."""

import pytest

from mrrmaid.services.sync import DataSyncService


def _raise(message):
    def _boom(**_kwargs):
        raise RuntimeError(message)

    return _boom


def test_shopify_failure_does_not_block_stripe(monkeypatch):
    """A failing Shopify sync is captured as an error while Stripe still runs."""
    svc = DataSyncService(shopify_client=object(), stripe_client=object())
    monkeypatch.setattr(
        svc, "sync_shopify_transactions", _raise("404 Client Error: Not Found")
    )
    monkeypatch.setattr(svc, "sync_stripe_subscriptions", lambda **_: 5)
    monkeypatch.setattr(svc, "sync_stripe_invoices", lambda **_: 3)

    results, errors = svc.sync_all()

    assert results == {"stripe_subscriptions": 5, "stripe_invoices": 3}
    assert "shopify" in errors
    assert "404" in errors["shopify"]
    assert "stripe" not in errors


def test_stripe_failure_does_not_block_shopify(monkeypatch):
    """A failing Stripe sync is captured as an error while Shopify still runs."""
    svc = DataSyncService(shopify_client=object(), stripe_client=object())
    monkeypatch.setattr(svc, "sync_shopify_transactions", lambda **_: 10)
    monkeypatch.setattr(
        svc, "sync_stripe_subscriptions", _raise("Invalid API Key provided")
    )

    results, errors = svc.sync_all()

    assert results == {"shopify_transactions": 10}
    assert "stripe" in errors
    assert "Invalid API Key" in errors["stripe"]
    assert "shopify" not in errors


def test_skipped_source_is_neither_result_nor_error(monkeypatch):
    """An unconfigured (None) source is simply skipped, not an error."""
    svc = DataSyncService(shopify_client=None, stripe_client=object())
    monkeypatch.setattr(svc, "sync_stripe_subscriptions", lambda **_: 1)
    monkeypatch.setattr(svc, "sync_stripe_invoices", lambda **_: 2)

    results, errors = svc.sync_all()

    assert results == {"stripe_subscriptions": 1, "stripe_invoices": 2}
    assert errors == {}
