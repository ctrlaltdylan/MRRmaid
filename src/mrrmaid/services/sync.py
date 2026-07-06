"""Data synchronization service for fetching and storing data from APIs."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from mrrmaid.api.shopify import ShopifyPartnerClient, ShopifyTransaction
from mrrmaid.api.stripe_client import StripeClient, StripeSubscription, StripeInvoice
from mrrmaid.models.database import get_session
from mrrmaid.models.transaction import (
    Subscription,
    Transaction,
    TransactionSource,
    TransactionType,
)


class SyncState:
    """Manages sync state for resumable syncing."""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or Path(".mrrmaid_sync_state.json")
        self._state: dict = {}
        self._load()

    def _load(self) -> None:
        """Load state from file."""
        if self.state_file.exists():
            try:
                self._state = json.loads(self.state_file.read_text())
            except (json.JSONDecodeError, IOError):
                self._state = {}

    def _save(self) -> None:
        """Save state to file."""
        self.state_file.write_text(json.dumps(self._state, indent=2))

    def get_cursor(self, source: str) -> Optional[str]:
        """Get the saved cursor for a source."""
        return self._state.get(source, {}).get("cursor")

    def get_last_id(self, source: str) -> Optional[str]:
        """Get the last processed ID for a source."""
        return self._state.get(source, {}).get("last_id")

    def get_count(self, source: str) -> int:
        """Get the count of records synced so far."""
        return self._state.get(source, {}).get("count", 0)

    def save_progress(
        self,
        source: str,
        cursor: Optional[str] = None,
        last_id: Optional[str] = None,
        count: int = 0,
    ) -> None:
        """Save sync progress for a source."""
        if source not in self._state:
            self._state[source] = {}
        if cursor:
            self._state[source]["cursor"] = cursor
        if last_id:
            self._state[source]["last_id"] = last_id
        self._state[source]["count"] = count
        self._state[source]["updated_at"] = datetime.utcnow().isoformat()
        self._save()

    def clear(self, source: str) -> None:
        """Clear state for a source after successful sync."""
        if source in self._state:
            del self._state[source]
            self._save()

    def clear_all(self) -> None:
        """Clear all sync state."""
        self._state = {}
        if self.state_file.exists():
            self.state_file.unlink()


class DataSyncService:
    """Service for synchronizing data from Shopify Partner and Stripe APIs."""

    # Mapping from Shopify transaction types to our enum
    SHOPIFY_TYPE_MAP = {
        "app_subscription": TransactionType.APP_SUBSCRIPTION,
        "app_usage": TransactionType.APP_USAGE,
        "app_one_time": TransactionType.APP_ONE_TIME,
        "app_credit": TransactionType.APP_CREDIT,
        "service": TransactionType.SERVICE,
        "referral": TransactionType.REFERRAL,
        "unknown": TransactionType.UNKNOWN,
    }

    def __init__(
        self,
        shopify_client: Optional[ShopifyPartnerClient] = None,
        stripe_client: Optional[StripeClient] = None,
        sync_state: Optional[SyncState] = None,
    ):
        """
        Initialize the sync service.

        Args:
            shopify_client: Optional Shopify Partner API client
            stripe_client: Optional Stripe API client
            sync_state: Optional sync state manager for resumable syncing
        """
        self.shopify_client = shopify_client
        self.stripe_client = stripe_client
        self.sync_state = sync_state or SyncState()

    def sync_shopify_transactions(
        self,
        created_at_min: Optional[datetime] = None,
        created_at_max: Optional[datetime] = None,
        progress_callback: Optional[callable] = None,
        resume: bool = True,
    ) -> int:
        """
        Sync transactions from Shopify Partner API.

        Args:
            created_at_min: Only sync transactions after this date
            created_at_max: Only sync transactions before this date
            progress_callback: Optional callback for progress updates
            resume: Whether to resume from last saved cursor

        Returns:
            Number of transactions synced
        """
        if not self.shopify_client:
            raise ValueError("Shopify client not configured")

        source_key = "shopify_transactions"

        # Check for saved progress
        start_cursor = None
        count = 0
        if resume:
            start_cursor = self.sync_state.get_cursor(source_key)
            count = self.sync_state.get_count(source_key)
            if start_cursor:
                # Progress will be reported to user via CLI

                pass

        def on_page_complete(cursor: str, page_count: int) -> None:
            """Save progress after each page."""
            self.sync_state.save_progress(
                source_key,
                cursor=cursor,
                count=count + page_count,
            )

        with get_session() as session:
            for txn in self.shopify_client.iter_all_transactions(
                created_at_min=created_at_min,
                created_at_max=created_at_max,
                start_cursor=start_cursor,
                on_page_complete=on_page_complete,
            ):
                self._upsert_shopify_transaction(session, txn)
                count += 1

                if progress_callback and count % 10 == 0:
                    progress_callback(count)

            session.commit()

        # Clear state on successful completion
        self.sync_state.clear(source_key)

        return count

    def _upsert_shopify_transaction(
        self, session: Session, txn: ShopifyTransaction
    ) -> Transaction:
        """Insert or update a Shopify transaction."""
        existing = (
            session.query(Transaction)
            .filter(
                Transaction.external_id == txn.id,
                Transaction.source == TransactionSource.SHOPIFY,
            )
            .first()
        )

        txn_type = self.SHOPIFY_TYPE_MAP.get(
            txn.transaction_type, TransactionType.UNKNOWN
        )

        if existing:
            existing.amount = txn.gross_amount
            existing.net_amount = txn.net_amount
            existing.currency = txn.currency
            existing.transaction_type = txn_type
            existing.app_id = txn.app_id
            existing.app_name = txn.app_name
            existing.shop_domain = txn.shop_domain
            existing.shop_id = txn.shop_id
            existing.synced_at = datetime.utcnow()
            return existing
        else:
            transaction = Transaction(
                external_id=txn.id,
                source=TransactionSource.SHOPIFY,
                transaction_type=txn_type,
                amount=txn.gross_amount,
                net_amount=txn.net_amount,
                currency=txn.currency,
                created_at=txn.created_at,
                app_id=txn.app_id,
                app_name=txn.app_name,
                shop_domain=txn.shop_domain,
                shop_id=txn.shop_id,
                customer_id=txn.shop_id,  # Use shop_id as customer identifier
                monthly_amount=txn.net_amount if txn.transaction_type == "app_subscription" else None,
                raw_data=json.dumps(txn.model_dump(), default=str),
            )
            session.add(transaction)
            return transaction

    def sync_stripe_subscriptions(
        self,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
        progress_callback: Optional[callable] = None,
        resume: bool = True,
    ) -> int:
        """
        Sync subscriptions from Stripe.

        Args:
            created_gte: Only sync subscriptions created after this date
            created_lte: Only sync subscriptions created before this date
            progress_callback: Optional callback for progress updates
            resume: Whether to resume from last saved position

        Returns:
            Number of subscriptions synced
        """
        if not self.stripe_client:
            raise ValueError("Stripe client not configured")

        source_key = "stripe_subscriptions"

        # Check for saved progress
        start_after = None
        count = 0
        if resume:
            start_after = self.sync_state.get_last_id(source_key)
            count = self.sync_state.get_count(source_key)

        def on_page_complete(last_id: str, page_count: int) -> None:
            """Save progress after each page."""
            self.sync_state.save_progress(
                source_key,
                last_id=last_id,
                count=count + page_count,
            )

        with get_session() as session:
            for sub in self.stripe_client.iter_all_subscriptions(
                created_gte=created_gte,
                created_lte=created_lte,
                start_after=start_after,
                on_page_complete=on_page_complete,
            ):
                self._upsert_stripe_subscription(session, sub)
                count += 1

                if progress_callback and count % 10 == 0:
                    progress_callback(count)

            session.commit()

        # Clear state on successful completion
        self.sync_state.clear(source_key)

        return count

    def _upsert_stripe_subscription(
        self, session: Session, sub: StripeSubscription
    ) -> Subscription:
        """Insert or update a Stripe subscription."""
        existing = (
            session.query(Subscription)
            .filter(
                Subscription.external_id == sub.id,
                Subscription.source == TransactionSource.STRIPE,
            )
            .first()
        )

        if existing:
            existing.status = sub.status
            existing.monthly_amount = sub.monthly_amount
            existing.currency = sub.currency
            existing.current_period_start = sub.current_period_start
            existing.current_period_end = sub.current_period_end
            existing.canceled_at = sub.canceled_at
            existing.trial_start = sub.trial_start
            existing.trial_end = sub.trial_end
            existing.cancel_at_period_end = sub.cancel_at_period_end
            existing.interval = sub.interval
            existing.interval_count = sub.interval_count
            existing.synced_at = datetime.utcnow()
            existing.raw_data = json.dumps(sub.model_dump(), default=str)
            return existing
        else:
            subscription = Subscription(
                external_id=sub.id,
                source=TransactionSource.STRIPE,
                customer_id=sub.customer_id,
                status=sub.status,
                monthly_amount=sub.monthly_amount,
                currency=sub.currency,
                created_at=sub.created_at,
                current_period_start=sub.current_period_start,
                current_period_end=sub.current_period_end,
                canceled_at=sub.canceled_at,
                trial_start=sub.trial_start,
                trial_end=sub.trial_end,
                cancel_at_period_end=sub.cancel_at_period_end,
                interval=sub.interval,
                interval_count=sub.interval_count,
                raw_data=json.dumps(sub.model_dump(), default=str),
            )
            session.add(subscription)
            return subscription

    def sync_stripe_invoices(
        self,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
        progress_callback: Optional[callable] = None,
        resume: bool = True,
    ) -> int:
        """
        Sync invoices from Stripe as transactions.

        Args:
            created_gte: Only sync invoices created after this date
            created_lte: Only sync invoices created before this date
            progress_callback: Optional callback for progress updates
            resume: Whether to resume from last saved position

        Returns:
            Number of invoices synced
        """
        if not self.stripe_client:
            raise ValueError("Stripe client not configured")

        source_key = "stripe_invoices"

        # Check for saved progress
        start_after = None
        count = 0
        if resume:
            start_after = self.sync_state.get_last_id(source_key)
            count = self.sync_state.get_count(source_key)

        def on_page_complete(last_id: str, page_count: int) -> None:
            """Save progress after each page."""
            self.sync_state.save_progress(
                source_key,
                last_id=last_id,
                count=count + page_count,
            )

        with get_session() as session:
            for inv in self.stripe_client.iter_all_invoices(
                status="paid",
                created_gte=created_gte,
                created_lte=created_lte,
                start_after=start_after,
                on_page_complete=on_page_complete,
            ):
                self._upsert_stripe_invoice(session, inv)
                count += 1

                if progress_callback and count % 10 == 0:
                    progress_callback(count)

            session.commit()

        # Clear state on successful completion
        self.sync_state.clear(source_key)

        return count

    def _upsert_stripe_invoice(
        self, session: Session, inv: StripeInvoice
    ) -> Transaction:
        """Insert or update a Stripe invoice as a transaction."""
        existing = (
            session.query(Transaction)
            .filter(
                Transaction.external_id == inv.id,
                Transaction.source == TransactionSource.STRIPE,
            )
            .first()
        )

        # Determine transaction type based on billing reason
        txn_type = TransactionType.SUBSCRIPTION_RENEWAL
        if inv.billing_reason == "subscription_create":
            txn_type = TransactionType.SUBSCRIPTION_NEW
        elif inv.billing_reason == "subscription_update":
            txn_type = TransactionType.SUBSCRIPTION_UPGRADE
        elif inv.billing_reason == "manual":
            txn_type = TransactionType.ONE_TIME

        if existing:
            existing.amount = inv.amount_paid
            existing.net_amount = inv.amount_paid
            existing.currency = inv.currency
            existing.transaction_type = txn_type
            existing.subscription_id = inv.subscription_id
            existing.period_start = inv.period_start
            existing.period_end = inv.period_end
            existing.synced_at = datetime.utcnow()
            return existing
        else:
            transaction = Transaction(
                external_id=inv.id,
                source=TransactionSource.STRIPE,
                transaction_type=txn_type,
                amount=inv.amount_paid,
                net_amount=inv.amount_paid,
                currency=inv.currency,
                created_at=inv.created_at,
                period_start=inv.period_start,
                period_end=inv.period_end,
                customer_id=inv.customer_id,
                subscription_id=inv.subscription_id,
                raw_data=json.dumps(inv.model_dump(), default=str),
            )
            session.add(transaction)
            return transaction

    def sync_all(
        self,
        created_at_min: Optional[datetime] = None,
        created_at_max: Optional[datetime] = None,
        progress_callback: Optional[callable] = None,
    ) -> tuple[dict[str, int], dict[str, str]]:
        """
        Sync all data from configured sources.

        Each source is synced independently: a failure in one source (e.g.
        invalid credentials) is captured and reported rather than aborting the
        whole run, so a bad Shopify config can't stop a Stripe-only sync.

        Args:
            created_at_min: Only sync data after this date
            created_at_max: Only sync data before this date
            progress_callback: Optional callback for progress updates

        Returns:
            A ``(results, errors)`` tuple where ``results`` maps each
            successfully synced source to its record count and ``errors`` maps
            each failed source ("shopify"/"stripe") to its error message.
        """
        results = {}
        errors = {}

        if self.shopify_client:
            try:
                results["shopify_transactions"] = self.sync_shopify_transactions(
                    created_at_min=created_at_min,
                    created_at_max=created_at_max,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                errors["shopify"] = str(e)

        if self.stripe_client:
            try:
                results["stripe_subscriptions"] = self.sync_stripe_subscriptions(
                    created_gte=created_at_min,
                    created_lte=created_at_max,
                    progress_callback=progress_callback,
                )
                results["stripe_invoices"] = self.sync_stripe_invoices(
                    created_gte=created_at_min,
                    created_lte=created_at_max,
                    progress_callback=progress_callback,
                )
            except Exception as e:
                errors["stripe"] = str(e)

        return results, errors
