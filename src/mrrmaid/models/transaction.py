"""Transaction model for storing revenue data from multiple sources."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SQLEnum,
    Float,
    Index,
    Integer,
    String,
    Text,
)

from mrrmaid.models.database import Base


class TransactionSource(str, Enum):
    """Source of the transaction."""

    SHOPIFY = "shopify"
    STRIPE = "stripe"


class TransactionType(str, Enum):
    """Type of transaction."""

    # Stripe types
    SUBSCRIPTION_NEW = "subscription_new"
    SUBSCRIPTION_RENEWAL = "subscription_renewal"
    SUBSCRIPTION_UPGRADE = "subscription_upgrade"
    SUBSCRIPTION_DOWNGRADE = "subscription_downgrade"
    SUBSCRIPTION_CANCELED = "subscription_canceled"
    ONE_TIME = "one_time"
    REFUND = "refund"

    # Shopify Partner types
    APP_SUBSCRIPTION = "app_subscription"
    APP_USAGE = "app_usage"
    APP_ONE_TIME = "app_one_time"
    APP_CREDIT = "app_credit"
    SERVICE = "service"
    REFERRAL = "referral"

    # Generic
    UNKNOWN = "unknown"


class Transaction(Base):
    """Model for storing transaction data."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(255), nullable=False, index=True)
    source = Column(SQLEnum(TransactionSource), nullable=False, index=True)
    transaction_type = Column(SQLEnum(TransactionType), nullable=False, index=True)

    # Financial data
    amount = Column(Float, nullable=False, default=0.0)
    net_amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), nullable=False, default="USD")

    # Timing
    created_at = Column(DateTime, nullable=False, index=True)
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)

    # Customer/Shop info
    customer_id = Column(String(255), nullable=True, index=True)
    customer_email = Column(String(255), nullable=True)
    customer_name = Column(String(255), nullable=True)

    # For Shopify: shop info
    shop_domain = Column(String(255), nullable=True)
    shop_id = Column(String(255), nullable=True)

    # For Stripe: subscription info
    subscription_id = Column(String(255), nullable=True, index=True)
    subscription_status = Column(String(50), nullable=True)

    # Product/App info
    product_id = Column(String(255), nullable=True)
    product_name = Column(String(255), nullable=True)
    app_id = Column(String(255), nullable=True)
    app_name = Column(String(255), nullable=True)

    # MRR calculation helpers
    monthly_amount = Column(Float, nullable=True)  # Normalized to monthly
    interval = Column(String(20), nullable=True)  # month, year, etc.
    interval_count = Column(Integer, nullable=True)

    # Flags
    is_trial = Column(Boolean, default=False)
    is_canceled = Column(Boolean, default=False)
    cancel_at_period_end = Column(Boolean, default=False)

    # Metadata
    raw_data = Column(Text, nullable=True)  # JSON string of original data
    synced_at = Column(DateTime, default=datetime.utcnow)

    # Indexes for common queries
    __table_args__ = (
        Index("ix_transactions_source_created", "source", "created_at"),
        Index("ix_transactions_type_created", "transaction_type", "created_at"),
        Index("ix_transactions_customer_created", "customer_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Transaction(id={self.id}, source={self.source}, "
            f"type={self.transaction_type}, amount={self.amount})>"
        )


class Subscription(Base):
    """Model for storing subscription state snapshots."""

    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    external_id = Column(String(255), nullable=False, unique=True, index=True)
    source = Column(SQLEnum(TransactionSource), nullable=False, index=True)

    # Customer info
    customer_id = Column(String(255), nullable=True, index=True)
    customer_email = Column(String(255), nullable=True)
    customer_name = Column(String(255), nullable=True)
    shop_domain = Column(String(255), nullable=True)

    # Subscription details
    status = Column(String(50), nullable=False, index=True)
    monthly_amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String(10), nullable=False, default="USD")

    # Product info
    product_id = Column(String(255), nullable=True)
    product_name = Column(String(255), nullable=True)
    app_id = Column(String(255), nullable=True)
    app_name = Column(String(255), nullable=True)

    # Timing
    created_at = Column(DateTime, nullable=False)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    trial_start = Column(DateTime, nullable=True)
    trial_end = Column(DateTime, nullable=True)

    # Billing
    interval = Column(String(20), nullable=True)
    interval_count = Column(Integer, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)

    # Metadata
    raw_data = Column(Text, nullable=True)
    synced_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_subscriptions_source_status", "source", "status"),
        Index("ix_subscriptions_customer", "customer_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Subscription(id={self.id}, external_id={self.external_id}, "
            f"status={self.status}, mrr={self.monthly_amount})>"
        )


class MRRSnapshot(Base):
    """Model for storing daily MRR snapshots for historical tracking."""

    __tablename__ = "mrr_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(DateTime, nullable=False, index=True)
    source = Column(SQLEnum(TransactionSource), nullable=True)  # None = combined

    # MRR breakdown
    total_mrr = Column(Float, nullable=False, default=0.0)
    new_mrr = Column(Float, nullable=False, default=0.0)
    expansion_mrr = Column(Float, nullable=False, default=0.0)
    contraction_mrr = Column(Float, nullable=False, default=0.0)
    churned_mrr = Column(Float, nullable=False, default=0.0)
    reactivation_mrr = Column(Float, nullable=False, default=0.0)

    # Counts
    total_subscriptions = Column(Integer, nullable=False, default=0)
    active_subscriptions = Column(Integer, nullable=False, default=0)
    trial_subscriptions = Column(Integer, nullable=False, default=0)
    canceled_subscriptions = Column(Integer, nullable=False, default=0)
    new_subscriptions = Column(Integer, nullable=False, default=0)
    churned_subscriptions = Column(Integer, nullable=False, default=0)

    # Rates
    churn_rate = Column(Float, nullable=True)
    net_revenue_retention = Column(Float, nullable=True)
    gross_revenue_retention = Column(Float, nullable=True)

    currency = Column(String(10), nullable=False, default="USD")
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_mrr_snapshots_date_source", "snapshot_date", "source"),
    )

    def __repr__(self) -> str:
        return (
            f"<MRRSnapshot(date={self.snapshot_date}, source={self.source}, "
            f"mrr={self.total_mrr})>"
        )
