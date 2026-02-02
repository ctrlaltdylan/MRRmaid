"""Data synchronization service for fetching and storing data from APIs."""

import json
from datetime import datetime
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
    ):
        """
        Initialize the sync service.

        Args:
            shopify_client: Optional Shopify Partner API client
            stripe_client: Optional Stripe API client
        """
        self.shopify_client = shopify_client
        self.stripe_client = stripe_client

    def sync_shopify_transactions(
        self,
        created_at_min: Optional[datetime] = None,
        created_at_max: Optional[datetime] = None,
        progress_callback: Optional[callable] = None,
    ) -> int:
        """
        Sync transactions from Shopify Partner API.

        Args:
            created_at_min: Only sync transactions after this date
            created_at_max: Only sync transactions before this date
            progress_callback: Optional callback for progress updates

        Returns:
            Number of transactions synced
        """
        if not self.shopify_client:
            raise ValueError("Shopify client not configured")

        count = 0
        with get_session() as session:
            for txn in self.shopify_client.iter_all_transactions(
                created_at_min=created_at_min,
                created_at_max=created_at_max,
            ):
                self._upsert_shopify_transaction(session, txn)
                count += 1

                if progress_callback and count % 10 == 0:
                    progress_callback(count)

            session.commit()

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
    ) -> int:
        """
        Sync subscriptions from Stripe.

        Args:
            created_gte: Only sync subscriptions created after this date
            created_lte: Only sync subscriptions created before this date
            progress_callback: Optional callback for progress updates

        Returns:
            Number of subscriptions synced
        """
        if not self.stripe_client:
            raise ValueError("Stripe client not configured")

        count = 0
        with get_session() as session:
            for sub in self.stripe_client.iter_all_subscriptions(
                created_gte=created_gte,
                created_lte=created_lte,
            ):
                self._upsert_stripe_subscription(session, sub)
                count += 1

                if progress_callback and count % 10 == 0:
                    progress_callback(count)

            session.commit()

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
    ) -> int:
        """
        Sync invoices from Stripe as transactions.

        Args:
            created_gte: Only sync invoices created after this date
            created_lte: Only sync invoices created before this date
            progress_callback: Optional callback for progress updates

        Returns:
            Number of invoices synced
        """
        if not self.stripe_client:
            raise ValueError("Stripe client not configured")

        count = 0
        with get_session() as session:
            for inv in self.stripe_client.iter_all_invoices(
                status="paid",
                created_gte=created_gte,
                created_lte=created_lte,
            ):
                self._upsert_stripe_invoice(session, inv)
                count += 1

                if progress_callback and count % 10 == 0:
                    progress_callback(count)

            session.commit()

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
    ) -> dict[str, int]:
        """
        Sync all data from configured sources.

        Args:
            created_at_min: Only sync data after this date
            created_at_max: Only sync data before this date
            progress_callback: Optional callback for progress updates

        Returns:
            Dictionary with counts for each source
        """
        results = {}

        if self.shopify_client:
            results["shopify_transactions"] = self.sync_shopify_transactions(
                created_at_min=created_at_min,
                created_at_max=created_at_max,
                progress_callback=progress_callback,
            )

        if self.stripe_client:
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

        return results
