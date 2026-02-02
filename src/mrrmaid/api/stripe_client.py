"""Stripe API client for subscriptions and invoices."""

from datetime import datetime
from typing import Any, Generator, Optional

import stripe
from pydantic import BaseModel


class StripeSubscription(BaseModel):
    """Represents a subscription from Stripe."""

    id: str
    customer_id: str
    status: str
    currency: str
    current_period_start: datetime
    current_period_end: datetime
    created_at: datetime
    canceled_at: Optional[datetime] = None
    cancel_at_period_end: bool = False
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    items: list[dict[str, Any]]
    monthly_amount: float  # Normalized to monthly
    interval: str
    interval_count: int
    metadata: dict[str, str] = {}


class StripeInvoice(BaseModel):
    """Represents an invoice from Stripe."""

    id: str
    customer_id: str
    subscription_id: Optional[str] = None
    status: str
    currency: str
    amount_due: float
    amount_paid: float
    created_at: datetime
    period_start: datetime
    period_end: datetime
    billing_reason: Optional[str] = None
    lines: list[dict[str, Any]] = []


class StripeCustomer(BaseModel):
    """Represents a customer from Stripe."""

    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    created_at: datetime
    metadata: dict[str, str] = {}


class StripeClient:
    """Client for interacting with the Stripe API."""

    # Active subscription statuses that count towards MRR
    ACTIVE_STATUSES = {"active", "trialing"}
    # Statuses that indicate churn
    CHURNED_STATUSES = {"canceled", "unpaid"}

    def __init__(self, api_key: str):
        """
        Initialize the Stripe client.

        Args:
            api_key: Stripe API secret key
        """
        self.api_key = api_key
        stripe.api_key = api_key

    def test_connection(self) -> bool:
        """Test the API connection."""
        try:
            stripe.Account.retrieve()
            return True
        except Exception:
            return False

    def get_subscriptions(
        self,
        status: Optional[str] = None,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
        limit: int = 100,
        starting_after: Optional[str] = None,
    ) -> list[stripe.Subscription]:
        """
        Fetch subscriptions from Stripe.

        Args:
            status: Filter by subscription status
            created_gte: Filter subscriptions created after this date
            created_lte: Filter subscriptions created before this date
            limit: Number of subscriptions to fetch (max 100)
            starting_after: Cursor for pagination

        Returns:
            List of Stripe Subscription objects
        """
        params: dict[str, Any] = {"limit": min(limit, 100)}

        if status:
            params["status"] = status
        if starting_after:
            params["starting_after"] = starting_after

        # Build created filter
        if created_gte or created_lte:
            params["created"] = {}
            if created_gte:
                params["created"]["gte"] = int(created_gte.timestamp())
            if created_lte:
                params["created"]["lte"] = int(created_lte.timestamp())

        return list(stripe.Subscription.list(**params))

    def iter_all_subscriptions(
        self,
        status: Optional[str] = None,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
    ) -> Generator[StripeSubscription, None, None]:
        """
        Iterate through all subscriptions with automatic pagination.

        Args:
            status: Filter by subscription status
            created_gte: Filter subscriptions created after this date
            created_lte: Filter subscriptions created before this date

        Yields:
            StripeSubscription objects
        """
        starting_after = None
        has_more = True

        while has_more:
            subscriptions = self.get_subscriptions(
                status=status,
                created_gte=created_gte,
                created_lte=created_lte,
                limit=100,
                starting_after=starting_after,
            )

            if not subscriptions:
                break

            for sub in subscriptions:
                parsed = self._parse_subscription(sub)
                if parsed:
                    yield parsed
                starting_after = sub.id

            has_more = len(subscriptions) == 100

    def _parse_subscription(self, sub: stripe.Subscription) -> Optional[StripeSubscription]:
        """Parse a Stripe Subscription into our model."""
        if not sub:
            return None

        # Calculate monthly normalized amount
        items = []
        total_monthly = 0.0

        for item in sub.get("items", {}).get("data", []):
            price = item.get("price", {})
            quantity = item.get("quantity", 1)

            unit_amount = price.get("unit_amount", 0) / 100  # Convert from cents
            interval = price.get("recurring", {}).get("interval", "month")
            interval_count = price.get("recurring", {}).get("interval_count", 1)

            # Normalize to monthly
            monthly_amount = self._normalize_to_monthly(
                unit_amount * quantity, interval, interval_count
            )
            total_monthly += monthly_amount

            items.append(
                {
                    "id": item.get("id"),
                    "price_id": price.get("id"),
                    "product_id": price.get("product"),
                    "quantity": quantity,
                    "unit_amount": unit_amount,
                    "interval": interval,
                    "interval_count": interval_count,
                }
            )

        # Get primary interval from first item
        primary_interval = "month"
        primary_interval_count = 1
        if items:
            primary_interval = items[0].get("interval", "month")
            primary_interval_count = items[0].get("interval_count", 1)

        return StripeSubscription(
            id=sub.id,
            customer_id=sub.customer if isinstance(sub.customer, str) else sub.customer.id,
            status=sub.status,
            currency=sub.currency.upper(),
            current_period_start=datetime.fromtimestamp(sub.current_period_start),
            current_period_end=datetime.fromtimestamp(sub.current_period_end),
            created_at=datetime.fromtimestamp(sub.created),
            canceled_at=(
                datetime.fromtimestamp(sub.canceled_at) if sub.canceled_at else None
            ),
            cancel_at_period_end=sub.cancel_at_period_end,
            trial_start=(
                datetime.fromtimestamp(sub.trial_start) if sub.trial_start else None
            ),
            trial_end=datetime.fromtimestamp(sub.trial_end) if sub.trial_end else None,
            items=items,
            monthly_amount=total_monthly,
            interval=primary_interval,
            interval_count=primary_interval_count,
            metadata=dict(sub.metadata) if sub.metadata else {},
        )

    def _normalize_to_monthly(
        self, amount: float, interval: str, interval_count: int
    ) -> float:
        """
        Normalize an amount to monthly.

        Args:
            amount: The amount to normalize
            interval: The billing interval (day, week, month, year)
            interval_count: How many intervals per billing cycle

        Returns:
            Monthly normalized amount
        """
        if interval == "month":
            return amount / interval_count
        elif interval == "year":
            return amount / (12 * interval_count)
        elif interval == "week":
            return (amount * 52) / (12 * interval_count)
        elif interval == "day":
            return (amount * 365) / (12 * interval_count)
        return amount

    def get_invoices(
        self,
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
        limit: int = 100,
        starting_after: Optional[str] = None,
    ) -> list[stripe.Invoice]:
        """
        Fetch invoices from Stripe.

        Args:
            subscription_id: Filter by subscription
            customer_id: Filter by customer
            status: Filter by invoice status
            created_gte: Filter invoices created after this date
            created_lte: Filter invoices created before this date
            limit: Number of invoices to fetch (max 100)
            starting_after: Cursor for pagination

        Returns:
            List of Stripe Invoice objects
        """
        params: dict[str, Any] = {"limit": min(limit, 100)}

        if subscription_id:
            params["subscription"] = subscription_id
        if customer_id:
            params["customer"] = customer_id
        if status:
            params["status"] = status
        if starting_after:
            params["starting_after"] = starting_after

        if created_gte or created_lte:
            params["created"] = {}
            if created_gte:
                params["created"]["gte"] = int(created_gte.timestamp())
            if created_lte:
                params["created"]["lte"] = int(created_lte.timestamp())

        return list(stripe.Invoice.list(**params))

    def iter_all_invoices(
        self,
        subscription_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        status: Optional[str] = None,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
    ) -> Generator[StripeInvoice, None, None]:
        """
        Iterate through all invoices with automatic pagination.

        Yields:
            StripeInvoice objects
        """
        starting_after = None
        has_more = True

        while has_more:
            invoices = self.get_invoices(
                subscription_id=subscription_id,
                customer_id=customer_id,
                status=status,
                created_gte=created_gte,
                created_lte=created_lte,
                limit=100,
                starting_after=starting_after,
            )

            if not invoices:
                break

            for inv in invoices:
                parsed = self._parse_invoice(inv)
                if parsed:
                    yield parsed
                starting_after = inv.id

            has_more = len(invoices) == 100

    def _parse_invoice(self, inv: stripe.Invoice) -> Optional[StripeInvoice]:
        """Parse a Stripe Invoice into our model."""
        if not inv:
            return None

        lines = []
        for line in inv.get("lines", {}).get("data", []):
            lines.append(
                {
                    "id": line.get("id"),
                    "amount": line.get("amount", 0) / 100,
                    "description": line.get("description"),
                    "period_start": line.get("period", {}).get("start"),
                    "period_end": line.get("period", {}).get("end"),
                    "price_id": line.get("price", {}).get("id") if line.get("price") else None,
                    "subscription_id": line.get("subscription"),
                }
            )

        return StripeInvoice(
            id=inv.id,
            customer_id=inv.customer if isinstance(inv.customer, str) else inv.customer.id,
            subscription_id=inv.subscription if isinstance(inv.subscription, str) else (
                inv.subscription.id if inv.subscription else None
            ),
            status=inv.status or "unknown",
            currency=inv.currency.upper() if inv.currency else "USD",
            amount_due=inv.amount_due / 100 if inv.amount_due else 0,
            amount_paid=inv.amount_paid / 100 if inv.amount_paid else 0,
            created_at=datetime.fromtimestamp(inv.created),
            period_start=datetime.fromtimestamp(inv.period_start) if inv.period_start else datetime.fromtimestamp(inv.created),
            period_end=datetime.fromtimestamp(inv.period_end) if inv.period_end else datetime.fromtimestamp(inv.created),
            billing_reason=inv.billing_reason,
            lines=lines,
        )

    def get_customers(
        self,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
        limit: int = 100,
        starting_after: Optional[str] = None,
    ) -> list[stripe.Customer]:
        """Fetch customers from Stripe."""
        params: dict[str, Any] = {"limit": min(limit, 100)}

        if starting_after:
            params["starting_after"] = starting_after

        if created_gte or created_lte:
            params["created"] = {}
            if created_gte:
                params["created"]["gte"] = int(created_gte.timestamp())
            if created_lte:
                params["created"]["lte"] = int(created_lte.timestamp())

        return list(stripe.Customer.list(**params))

    def iter_all_customers(
        self,
        created_gte: Optional[datetime] = None,
        created_lte: Optional[datetime] = None,
    ) -> Generator[StripeCustomer, None, None]:
        """Iterate through all customers with automatic pagination."""
        starting_after = None
        has_more = True

        while has_more:
            customers = self.get_customers(
                created_gte=created_gte,
                created_lte=created_lte,
                limit=100,
                starting_after=starting_after,
            )

            if not customers:
                break

            for cust in customers:
                yield StripeCustomer(
                    id=cust.id,
                    email=cust.email,
                    name=cust.name,
                    created_at=datetime.fromtimestamp(cust.created),
                    metadata=dict(cust.metadata) if cust.metadata else {},
                )
                starting_after = cust.id

            has_more = len(customers) == 100
