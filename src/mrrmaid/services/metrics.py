"""Metrics calculation engine for MRR, NRR, and Churn."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from mrrmaid.models.database import get_session
from mrrmaid.models.transaction import (
    MRRSnapshot,
    Subscription,
    Transaction,
    TransactionSource,
    TransactionType,
)


@dataclass
class MetricsSummary:
    """Summary of key metrics."""

    # MRR breakdown
    total_mrr: float = 0.0
    shopify_mrr: float = 0.0
    stripe_mrr: float = 0.0

    # MRR movement
    new_mrr: float = 0.0
    expansion_mrr: float = 0.0
    contraction_mrr: float = 0.0
    churned_mrr: float = 0.0
    reactivation_mrr: float = 0.0
    net_new_mrr: float = 0.0

    # Counts
    total_subscriptions: int = 0
    active_subscriptions: int = 0
    trial_subscriptions: int = 0
    canceled_subscriptions: int = 0
    new_subscriptions: int = 0
    churned_subscriptions: int = 0

    # Rates
    churn_rate: Optional[float] = None
    net_revenue_retention: Optional[float] = None
    gross_revenue_retention: Optional[float] = None

    # Period
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    currency: str = "USD"

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "total_mrr": self.total_mrr,
            "shopify_mrr": self.shopify_mrr,
            "stripe_mrr": self.stripe_mrr,
            "new_mrr": self.new_mrr,
            "expansion_mrr": self.expansion_mrr,
            "contraction_mrr": self.contraction_mrr,
            "churned_mrr": self.churned_mrr,
            "reactivation_mrr": self.reactivation_mrr,
            "net_new_mrr": self.net_new_mrr,
            "total_subscriptions": self.total_subscriptions,
            "active_subscriptions": self.active_subscriptions,
            "trial_subscriptions": self.trial_subscriptions,
            "canceled_subscriptions": self.canceled_subscriptions,
            "new_subscriptions": self.new_subscriptions,
            "churned_subscriptions": self.churned_subscriptions,
            "churn_rate": self.churn_rate,
            "net_revenue_retention": self.net_revenue_retention,
            "gross_revenue_retention": self.gross_revenue_retention,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "currency": self.currency,
        }


class MetricsCalculator:
    """Calculator for RevOps metrics."""

    # Stripe statuses that count as active for MRR
    ACTIVE_STATUSES = {"active"}
    TRIAL_STATUSES = {"trialing"}
    CHURNED_STATUSES = {"canceled", "unpaid"}

    def __init__(self, session: Optional[Session] = None):
        """
        Initialize the metrics calculator.

        Args:
            session: Optional SQLAlchemy session (will create one if not provided)
        """
        self._session = session
        self._own_session = session is None

    def _get_session(self) -> Session:
        """Get the session to use."""
        if self._session:
            return self._session
        # This will be called within a context manager
        raise RuntimeError("No session available")

    def calculate_current_mrr(
        self,
        source: Optional[TransactionSource] = None,
    ) -> MetricsSummary:
        """
        Calculate current MRR from subscriptions and recent transactions.

        For fixed-price subscriptions, uses the subscription amount.
        For usage-based/metered billing, calculates from last month's revenue.

        Args:
            source: Optional filter by source (shopify/stripe)

        Returns:
            MetricsSummary with current MRR
        """
        with get_session() as session:
            summary = self._calculate_mrr_from_subscriptions(session, source)

            # Also calculate MRR from recent transactions (last 30 days)
            # This captures usage-based revenue and Shopify app revenue
            last_month = datetime.utcnow() - timedelta(days=30)

            self._add_transaction_based_mrr(session, summary, last_month, source)

            return summary

    def _add_transaction_based_mrr(
        self,
        session: Session,
        summary: MetricsSummary,
        since: datetime,
        source: Optional[TransactionSource] = None,
    ) -> None:
        """Add MRR calculated from recent transactions (for usage-based billing)."""
        # Query for recurring transactions in the period
        query = session.query(
            Transaction.source,
            func.sum(Transaction.net_amount).label("total"),
            func.count(func.distinct(Transaction.customer_id)).label("unique_customers"),
        ).filter(
            Transaction.created_at >= since,
            Transaction.transaction_type.in_([
                TransactionType.APP_SUBSCRIPTION,
                TransactionType.APP_USAGE,
                TransactionType.SUBSCRIPTION_RENEWAL,
            ]),
        )

        if source:
            query = query.filter(Transaction.source == source)

        results = query.group_by(Transaction.source).all()

        for row in results:
            txn_source = row[0]
            total = row[1] or 0
            unique_customers = row[2] or 0

            if txn_source == TransactionSource.SHOPIFY:
                # Only add if not already counted from subscriptions
                if summary.shopify_mrr == 0:
                    summary.shopify_mrr = total
                    summary.total_mrr += total
                # Add Shopify active "subscriptions" (unique shops with recent activity)
                summary.total_subscriptions += unique_customers
                summary.active_subscriptions += unique_customers
            elif txn_source == TransactionSource.STRIPE:
                # For metered subscriptions, use transaction data if subscription MRR is 0
                if summary.stripe_mrr == 0:
                    summary.stripe_mrr = total
                    summary.total_mrr += total

    def _calculate_mrr_from_subscriptions(
        self,
        session: Session,
        source: Optional[TransactionSource] = None,
    ) -> MetricsSummary:
        """Calculate MRR from subscription records."""
        summary = MetricsSummary()

        # Base query for subscriptions
        query = session.query(Subscription)

        if source:
            query = query.filter(Subscription.source == source)

        # Get all subscriptions
        subscriptions = query.all()

        for sub in subscriptions:
            summary.total_subscriptions += 1

            if sub.status in self.ACTIVE_STATUSES:
                summary.active_subscriptions += 1
                mrr = sub.monthly_amount or 0

                if sub.source == TransactionSource.STRIPE:
                    summary.stripe_mrr += mrr
                elif sub.source == TransactionSource.SHOPIFY:
                    summary.shopify_mrr += mrr

                summary.total_mrr += mrr

            elif sub.status in self.TRIAL_STATUSES:
                summary.trial_subscriptions += 1

            elif sub.status in self.CHURNED_STATUSES:
                summary.canceled_subscriptions += 1

        return summary

    def calculate_mrr_for_period(
        self,
        start_date: datetime,
        end_date: datetime,
        source: Optional[TransactionSource] = None,
    ) -> MetricsSummary:
        """
        Calculate MRR and related metrics for a specific period.

        This uses transaction/invoice data to calculate historical MRR.

        Args:
            start_date: Start of the period
            end_date: End of the period
            source: Optional filter by source

        Returns:
            MetricsSummary for the period
        """
        with get_session() as session:
            summary = MetricsSummary(
                period_start=start_date,
                period_end=end_date,
            )

            # Calculate from transactions
            self._calculate_mrr_from_transactions(
                session, summary, start_date, end_date, source
            )

            # Calculate churn metrics
            self._calculate_churn_metrics(
                session, summary, start_date, end_date, source
            )

            # Calculate net new MRR
            summary.net_new_mrr = (
                summary.new_mrr
                + summary.expansion_mrr
                + summary.reactivation_mrr
                - summary.contraction_mrr
                - summary.churned_mrr
            )

            return summary

    def _calculate_mrr_from_transactions(
        self,
        session: Session,
        summary: MetricsSummary,
        start_date: datetime,
        end_date: datetime,
        source: Optional[TransactionSource] = None,
    ) -> None:
        """Calculate MRR metrics from transaction records."""
        # Query for transactions in the period
        query = session.query(Transaction).filter(
            Transaction.created_at >= start_date,
            Transaction.created_at <= end_date,
        )

        if source:
            query = query.filter(Transaction.source == source)

        transactions = query.all()

        for txn in transactions:
            amount = txn.monthly_amount or txn.net_amount or 0

            # Categorize by transaction type
            if txn.transaction_type == TransactionType.SUBSCRIPTION_NEW:
                summary.new_mrr += amount
                summary.new_subscriptions += 1
            elif txn.transaction_type == TransactionType.SUBSCRIPTION_UPGRADE:
                summary.expansion_mrr += amount
            elif txn.transaction_type == TransactionType.SUBSCRIPTION_DOWNGRADE:
                summary.contraction_mrr += amount
            elif txn.transaction_type == TransactionType.SUBSCRIPTION_CANCELED:
                summary.churned_mrr += amount
                summary.churned_subscriptions += 1
            elif txn.transaction_type == TransactionType.APP_SUBSCRIPTION:
                # Shopify app subscriptions contribute to MRR
                summary.shopify_mrr += amount
                summary.total_mrr += amount

            # Add to source-specific totals
            if txn.source == TransactionSource.STRIPE:
                if txn.transaction_type in (
                    TransactionType.SUBSCRIPTION_NEW,
                    TransactionType.SUBSCRIPTION_RENEWAL,
                ):
                    summary.stripe_mrr += amount
                    summary.total_mrr += amount
            elif txn.source == TransactionSource.SHOPIFY:
                pass  # Already handled above

    def _calculate_churn_metrics(
        self,
        session: Session,
        summary: MetricsSummary,
        start_date: datetime,
        end_date: datetime,
        source: Optional[TransactionSource] = None,
    ) -> None:
        """Calculate churn rate and retention metrics using cohort analysis."""
        # Calculate the previous period (same duration before start_date)
        period_length = end_date - start_date
        prev_start = start_date - period_length
        prev_end = start_date

        # Get revenue by customer for previous period (the "starting" cohort)
        prev_query = session.query(
            Transaction.customer_id,
            func.sum(Transaction.net_amount).label("revenue"),
        ).filter(
            Transaction.created_at >= prev_start,
            Transaction.created_at < prev_end,
            Transaction.customer_id.isnot(None),
        )

        if source:
            prev_query = prev_query.filter(Transaction.source == source)

        prev_results = prev_query.group_by(Transaction.customer_id).all()
        prev_revenue_by_customer = {r[0]: r[1] or 0 for r in prev_results}
        starting_mrr = sum(prev_revenue_by_customer.values())
        starting_customers = set(prev_revenue_by_customer.keys())

        # Get revenue by customer for current period
        curr_query = session.query(
            Transaction.customer_id,
            func.sum(Transaction.net_amount).label("revenue"),
        ).filter(
            Transaction.created_at >= start_date,
            Transaction.created_at <= end_date,
            Transaction.customer_id.isnot(None),
        )

        if source:
            curr_query = curr_query.filter(Transaction.source == source)

        curr_results = curr_query.group_by(Transaction.customer_id).all()
        curr_revenue_by_customer = {r[0]: r[1] or 0 for r in curr_results}
        current_customers = set(curr_revenue_by_customer.keys())

        # Calculate NRR components
        # Churned: customers in previous period but not in current
        churned_customers = starting_customers - current_customers
        churned_mrr = sum(prev_revenue_by_customer.get(c, 0) for c in churned_customers)

        # Retained customers: in both periods
        retained_customers = starting_customers & current_customers

        # Expansion: retained customers paying more
        # Contraction: retained customers paying less
        expansion_mrr = 0.0
        contraction_mrr = 0.0
        for customer in retained_customers:
            prev_rev = prev_revenue_by_customer.get(customer, 0)
            curr_rev = curr_revenue_by_customer.get(customer, 0)
            diff = curr_rev - prev_rev
            if diff > 0:
                expansion_mrr += diff
            elif diff < 0:
                contraction_mrr += abs(diff)

        # New customers: in current period but not in previous
        new_customers = current_customers - starting_customers
        new_mrr = sum(curr_revenue_by_customer.get(c, 0) for c in new_customers)

        # Update summary
        summary.churned_mrr = churned_mrr
        summary.churned_subscriptions = len(churned_customers)
        summary.expansion_mrr = expansion_mrr
        summary.contraction_mrr = contraction_mrr
        summary.new_mrr = new_mrr
        summary.new_subscriptions = len(new_customers)

        # Calculate retention rates
        if starting_mrr > 0:
            # Gross Revenue Retention = (Starting - Churn - Contraction) / Starting
            summary.gross_revenue_retention = (
                (starting_mrr - churned_mrr - contraction_mrr) / starting_mrr
            ) * 100

            # Net Revenue Retention = (Starting + Expansion - Contraction - Churn) / Starting
            summary.net_revenue_retention = (
                (starting_mrr + expansion_mrr - contraction_mrr - churned_mrr) / starting_mrr
            ) * 100

            # Churn rate
            summary.churn_rate = (churned_mrr / starting_mrr) * 100

        # Store totals in summary
        summary.total_mrr = sum(curr_revenue_by_customer.values())
        summary.total_subscriptions = len(current_customers)
        summary.active_subscriptions = len(retained_customers) + len(new_customers)

    def get_mrr_trend(
        self,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "month",
        source: Optional[TransactionSource] = None,
    ) -> pd.DataFrame:
        """
        Get MRR trend over time.

        Args:
            start_date: Start of the period
            end_date: End of the period
            granularity: 'day', 'week', or 'month'
            source: Optional filter by source

        Returns:
            DataFrame with MRR trend data
        """
        with get_session() as session:
            # Generate date range
            if granularity == "day":
                freq = "D"
            elif granularity == "week":
                freq = "W"
            else:
                freq = "MS"  # Month start

            date_range = pd.date_range(start=start_date, end=end_date, freq=freq)

            data = []
            for date in date_range:
                if granularity == "day":
                    period_end = date + timedelta(days=1)
                elif granularity == "week":
                    period_end = date + timedelta(weeks=1)
                else:
                    # Next month
                    if date.month == 12:
                        period_end = date.replace(year=date.year + 1, month=1)
                    else:
                        period_end = date.replace(month=date.month + 1)

                # Query transactions for this period
                query = session.query(
                    func.sum(Transaction.net_amount).label("total"),
                    Transaction.source,
                ).filter(
                    Transaction.created_at >= date,
                    Transaction.created_at < period_end,
                    Transaction.transaction_type.in_([
                        TransactionType.SUBSCRIPTION_NEW,
                        TransactionType.SUBSCRIPTION_RENEWAL,
                        TransactionType.APP_SUBSCRIPTION,
                    ]),
                )

                if source:
                    query = query.filter(Transaction.source == source)

                results = query.group_by(Transaction.source).all()

                row = {
                    "date": date,
                    "total_mrr": 0,
                    "shopify_mrr": 0,
                    "stripe_mrr": 0,
                }

                for result in results:
                    amount = result.total or 0
                    if result.source == TransactionSource.SHOPIFY:
                        row["shopify_mrr"] = amount
                    elif result.source == TransactionSource.STRIPE:
                        row["stripe_mrr"] = amount
                    row["total_mrr"] += amount

                data.append(row)

            return pd.DataFrame(data)

    def get_transactions_summary(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        source: Optional[TransactionSource] = None,
        transaction_type: Optional[TransactionType] = None,
    ) -> pd.DataFrame:
        """
        Get a summary of transactions.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter
            source: Optional source filter
            transaction_type: Optional type filter

        Returns:
            DataFrame with transaction data
        """
        with get_session() as session:
            query = session.query(Transaction)

            if start_date:
                query = query.filter(Transaction.created_at >= start_date)
            if end_date:
                query = query.filter(Transaction.created_at <= end_date)
            if source:
                query = query.filter(Transaction.source == source)
            if transaction_type:
                query = query.filter(Transaction.transaction_type == transaction_type)

            transactions = query.order_by(Transaction.created_at.desc()).all()

            data = []
            for txn in transactions:
                data.append({
                    "id": txn.id,
                    "external_id": txn.external_id,
                    "source": txn.source.value if txn.source else None,
                    "type": txn.transaction_type.value if txn.transaction_type else None,
                    "amount": txn.amount,
                    "net_amount": txn.net_amount,
                    "currency": txn.currency,
                    "created_at": txn.created_at,
                    "customer_id": txn.customer_id,
                    "subscription_id": txn.subscription_id,
                    "app_name": txn.app_name,
                    "shop_domain": txn.shop_domain,
                })

            return pd.DataFrame(data)

    def get_subscriptions_summary(
        self,
        source: Optional[TransactionSource] = None,
        status: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Get a summary of subscriptions.

        Args:
            source: Optional source filter
            status: Optional status filter

        Returns:
            DataFrame with subscription data
        """
        with get_session() as session:
            query = session.query(Subscription)

            if source:
                query = query.filter(Subscription.source == source)
            if status:
                query = query.filter(Subscription.status == status)

            subscriptions = query.order_by(Subscription.created_at.desc()).all()

            data = []
            for sub in subscriptions:
                data.append({
                    "id": sub.id,
                    "external_id": sub.external_id,
                    "source": sub.source.value if sub.source else None,
                    "status": sub.status,
                    "monthly_amount": sub.monthly_amount,
                    "currency": sub.currency,
                    "created_at": sub.created_at,
                    "canceled_at": sub.canceled_at,
                    "customer_id": sub.customer_id,
                    "customer_email": sub.customer_email,
                    "app_name": sub.app_name,
                    "shop_domain": sub.shop_domain,
                    "interval": sub.interval,
                })

            return pd.DataFrame(data)

    def save_snapshot(
        self,
        summary: MetricsSummary,
        snapshot_date: Optional[datetime] = None,
        source: Optional[TransactionSource] = None,
    ) -> MRRSnapshot:
        """
        Save a metrics snapshot for historical tracking.

        Args:
            summary: The metrics summary to save
            snapshot_date: Date for the snapshot (defaults to now)
            source: Source for the snapshot (None = combined)

        Returns:
            The created MRRSnapshot
        """
        with get_session() as session:
            snapshot = MRRSnapshot(
                snapshot_date=snapshot_date or datetime.utcnow(),
                source=source,
                total_mrr=summary.total_mrr,
                new_mrr=summary.new_mrr,
                expansion_mrr=summary.expansion_mrr,
                contraction_mrr=summary.contraction_mrr,
                churned_mrr=summary.churned_mrr,
                reactivation_mrr=summary.reactivation_mrr,
                total_subscriptions=summary.total_subscriptions,
                active_subscriptions=summary.active_subscriptions,
                trial_subscriptions=summary.trial_subscriptions,
                canceled_subscriptions=summary.canceled_subscriptions,
                new_subscriptions=summary.new_subscriptions,
                churned_subscriptions=summary.churned_subscriptions,
                churn_rate=summary.churn_rate,
                net_revenue_retention=summary.net_revenue_retention,
                gross_revenue_retention=summary.gross_revenue_retention,
                currency=summary.currency,
            )
            session.add(snapshot)
            session.commit()
            return snapshot

    def get_snapshots(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        source: Optional[TransactionSource] = None,
    ) -> pd.DataFrame:
        """
        Get historical MRR snapshots.

        Args:
            start_date: Optional start date filter
            end_date: Optional end date filter
            source: Optional source filter

        Returns:
            DataFrame with snapshot data
        """
        with get_session() as session:
            query = session.query(MRRSnapshot)

            if start_date:
                query = query.filter(MRRSnapshot.snapshot_date >= start_date)
            if end_date:
                query = query.filter(MRRSnapshot.snapshot_date <= end_date)
            if source:
                query = query.filter(MRRSnapshot.source == source)

            snapshots = query.order_by(MRRSnapshot.snapshot_date.desc()).all()

            data = []
            for snap in snapshots:
                data.append({
                    "date": snap.snapshot_date,
                    "source": snap.source.value if snap.source else "combined",
                    "total_mrr": snap.total_mrr,
                    "new_mrr": snap.new_mrr,
                    "expansion_mrr": snap.expansion_mrr,
                    "contraction_mrr": snap.contraction_mrr,
                    "churned_mrr": snap.churned_mrr,
                    "active_subscriptions": snap.active_subscriptions,
                    "churn_rate": snap.churn_rate,
                    "nrr": snap.net_revenue_retention,
                    "grr": snap.gross_revenue_retention,
                })

            return pd.DataFrame(data)

    def calculate_cohort_analysis(
        self,
        metric: str = "revenue",
        source: Optional[TransactionSource] = None,
        num_periods: int = 12,
    ) -> pd.DataFrame:
        """
        Calculate cohort-based retention analysis.

        Groups customers by their first transaction month and tracks
        their revenue or count retention over subsequent months.

        Args:
            metric: "revenue" for revenue retention, "customers" for customer retention
            source: Optional filter by source
            num_periods: Number of periods to track (default 12 months)

        Returns:
            DataFrame with cohort retention data
        """
        with get_session() as session:
            # Build base query for transactions
            base_query = session.query(
                Transaction.customer_id,
                Transaction.created_at,
                Transaction.net_amount,
            ).filter(
                Transaction.customer_id.isnot(None),
                Transaction.net_amount > 0,
            )

            if source:
                base_query = base_query.filter(Transaction.source == source)

            transactions = base_query.all()

            if not transactions:
                return pd.DataFrame()

            # Convert to DataFrame for easier manipulation
            df = pd.DataFrame([
                {
                    "customer_id": t.customer_id,
                    "created_at": t.created_at,
                    "revenue": float(t.net_amount or 0),
                }
                for t in transactions
            ])

            # Add month column
            df["month"] = pd.to_datetime(df["created_at"]).dt.to_period("M")

            # Find each customer's cohort (first transaction month)
            cohort_df = df.groupby("customer_id")["month"].min().reset_index()
            cohort_df.columns = ["customer_id", "cohort"]

            # Merge cohort back to transactions
            df = df.merge(cohort_df, on="customer_id")

            # Calculate period index (months since cohort)
            df["period_index"] = (df["month"] - df["cohort"]).apply(lambda x: x.n if hasattr(x, 'n') else 0)

            # Filter to requested number of periods
            df = df[df["period_index"] < num_periods]

            if metric == "revenue":
                # Revenue retention: sum revenue by cohort and period
                pivot = df.groupby(["cohort", "period_index"])["revenue"].sum().unstack(fill_value=0)

                # Calculate retention as % of period 0
                retention = pivot.div(pivot[0], axis=0) * 100

            else:  # customers
                # Customer retention: count unique customers by cohort and period
                pivot = df.groupby(["cohort", "period_index"])["customer_id"].nunique().unstack(fill_value=0)

                # Calculate retention as % of period 0
                retention = pivot.div(pivot[0], axis=0) * 100

            # Round to 1 decimal place
            retention = retention.round(1)

            # Convert period index to "M0", "M1", etc.
            retention.columns = [f"M{i}" for i in retention.columns]

            # Convert cohort period to string for display
            retention.index = retention.index.astype(str)

            # Add cohort size info
            cohort_sizes = df.groupby("cohort").agg({
                "customer_id": "nunique",
                "revenue": "sum"
            }).round(2)
            cohort_sizes.index = cohort_sizes.index.astype(str)

            retention["Customers"] = cohort_sizes["customer_id"]
            retention["Revenue"] = cohort_sizes["revenue"]

            # Reorder columns to put Customers and Revenue first
            cols = ["Customers", "Revenue"] + [c for c in retention.columns if c not in ["Customers", "Revenue"]]
            retention = retention[cols]

            return retention

    def get_cohort_summary(
        self,
        source: Optional[TransactionSource] = None,
    ) -> dict:
        """
        Get summary statistics from cohort analysis.

        Returns:
            Dictionary with average retention rates by period
        """
        retention_df = self.calculate_cohort_analysis(metric="revenue", source=source)

        if retention_df.empty:
            return {}

        # Calculate average retention for each period
        period_cols = [c for c in retention_df.columns if c.startswith("M")]
        avg_retention = {}

        for col in period_cols:
            # Exclude NaN values (cohorts that haven't reached this period yet)
            values = retention_df[col].dropna()
            if len(values) > 0:
                avg_retention[col] = round(values.mean(), 1)

        return {
            "average_retention_by_period": avg_retention,
            "total_cohorts": len(retention_df),
            "total_customers": int(retention_df["Customers"].sum()),
            "total_revenue": float(retention_df["Revenue"].sum()),
        }
