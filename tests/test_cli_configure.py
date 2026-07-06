"""Tests for the `configure` CLI command."""

from pathlib import Path

from typer.testing import CliRunner

from mrrmaid.cli import app

runner = CliRunner()


def test_configure_skips_shopify_with_empty_input(tmp_path, monkeypatch):
    """Pressing Enter at the Shopify prompts should skip them and still
    save a Stripe-only configuration."""
    monkeypatch.chdir(tmp_path)

    # Empty Shopify token, empty Shopify org id, then a Stripe key.
    result = runner.invoke(app, ["configure"], input="\n\nsk_test_ABC123\n")

    assert result.exit_code == 0, result.output

    env_text = (tmp_path / ".env").read_text()
    assert "STRIPE_API_KEY=sk_test_ABC123" in env_text
    assert "SHOPIFY_PARTNER_TOKEN" not in env_text
    assert "SHOPIFY_ORGANIZATION_ID" not in env_text


def test_configure_skip_preserves_existing_shopify_config(tmp_path, monkeypatch):
    """Skipping a value must not wipe an entry already present in .env."""
    monkeypatch.chdir(tmp_path)
    env_path = Path(tmp_path) / ".env"
    env_path.write_text("SHOPIFY_PARTNER_TOKEN=existing_token\n")

    # Skip both Shopify prompts, set only the Stripe key.
    result = runner.invoke(app, ["configure"], input="\n\nsk_test_XYZ\n")

    assert result.exit_code == 0, result.output

    env_text = env_path.read_text()
    assert "SHOPIFY_PARTNER_TOKEN=existing_token" in env_text
    assert "STRIPE_API_KEY=sk_test_XYZ" in env_text
