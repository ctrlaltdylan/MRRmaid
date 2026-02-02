"""Shopify Partner API client using GraphQL."""

import time
from datetime import datetime
from typing import Any, Generator, Optional

import requests
from requests.exceptions import HTTPError
from pydantic import BaseModel


class ShopifyTransaction(BaseModel):
    """Represents a transaction from Shopify Partner API."""

    id: str
    transaction_type: str
    created_at: datetime
    net_amount: float
    gross_amount: float
    currency: str
    app_name: Optional[str] = None
    app_id: Optional[str] = None
    shop_domain: Optional[str] = None
    shop_id: Optional[str] = None
    description: Optional[str] = None


class ShopifyPartnerClient:
    """Client for interacting with the Shopify Partner GraphQL API."""

    BASE_URL = "https://partners.shopify.com"
    RATE_LIMIT_DELAY = 0.25  # 4 requests per second max

    def __init__(
        self,
        access_token: str,
        organization_id: str,
        api_version: str = "2026-01",
    ):
        """
        Initialize the Shopify Partner API client.

        Args:
            access_token: Partner API access token
            organization_id: Partner organization ID
            api_version: API version to use (default: 2026-01)
        """
        self.access_token = access_token
        self.organization_id = organization_id
        self.api_version = api_version
        self.endpoint = f"{self.BASE_URL}/{organization_id}/api/{api_version}/graphql.json"
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        """Enforce rate limiting (4 requests/second)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _execute_query(
        self, query: str, variables: Optional[dict] = None, max_retries: int = 3
    ) -> dict[str, Any]:
        """
        Execute a GraphQL query against the Partner API with retry logic.

        Args:
            query: GraphQL query string
            variables: Optional query variables
            max_retries: Maximum number of retries for transient errors

        Returns:
            Query response data

        Raises:
            requests.HTTPError: If the request fails after all retries
        """
        headers = {
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": self.access_token,
        }

        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        last_error = None
        for attempt in range(max_retries):
            self._rate_limit()

            try:
                response = requests.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                response.raise_for_status()

                result = response.json()

                if "errors" in result:
                    error_messages = [e.get("message", str(e)) for e in result["errors"]]
                    raise ValueError(f"GraphQL errors: {'; '.join(error_messages)}")

                return result.get("data", {})

            except HTTPError as e:
                last_error = e
                # Retry on 5xx server errors
                if response.status_code >= 500 and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # Exponential backoff: 2, 4, 6 seconds
                    time.sleep(wait_time)
                    continue
                raise

        raise last_error

    def get_transactions(
        self,
        first: int = 100,
        after: Optional[str] = None,
        created_at_min: Optional[datetime] = None,
        created_at_max: Optional[datetime] = None,
        transaction_types: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Fetch transactions from the Partner API.

        Args:
            first: Number of transactions to fetch (max 100)
            after: Cursor for pagination
            created_at_min: Filter transactions created after this date
            created_at_max: Filter transactions created before this date
            transaction_types: Filter by transaction types

        Returns:
            Dictionary containing transactions and pagination info
        """
        # Build the filter for transaction types
        type_filter = ""
        if transaction_types:
            types_str = ", ".join(transaction_types)
            type_filter = f", types: [{types_str}]"

        # Build date filters
        date_filter = ""
        if created_at_min:
            date_filter += f', createdAtMin: "{created_at_min.isoformat()}"'
        if created_at_max:
            date_filter += f', createdAtMax: "{created_at_max.isoformat()}"'

        # Build pagination
        pagination = f"first: {first}"
        if after:
            pagination += f', after: "{after}"'

        query = f"""
        query {{
            transactions({pagination}{type_filter}{date_filter}) {{
                edges {{
                    cursor
                    node {{
                        id
                        createdAt
                        ... on AppSubscriptionSale {{
                            netAmount {{
                                amount
                                currencyCode
                            }}
                            grossAmount {{
                                amount
                                currencyCode
                            }}
                            app {{
                                id
                                name
                            }}
                            shop {{
                                id
                                myshopifyDomain
                            }}
                            chargeId
                        }}
                        ... on AppUsageSale {{
                            netAmount {{
                                amount
                                currencyCode
                            }}
                            grossAmount {{
                                amount
                                currencyCode
                            }}
                            app {{
                                id
                                name
                            }}
                            shop {{
                                id
                                myshopifyDomain
                            }}
                        }}
                        ... on AppSaleCredit {{
                            netAmount {{
                                amount
                                currencyCode
                            }}
                            grossAmount {{
                                amount
                                currencyCode
                            }}
                            app {{
                                id
                                name
                            }}
                            shop {{
                                id
                                myshopifyDomain
                            }}
                        }}
                        ... on AppOneTimeSale {{
                            netAmount {{
                                amount
                                currencyCode
                            }}
                            grossAmount {{
                                amount
                                currencyCode
                            }}
                            app {{
                                id
                                name
                            }}
                            shop {{
                                id
                                myshopifyDomain
                            }}
                        }}
                        ... on ServiceSale {{
                            netAmount {{
                                amount
                                currencyCode
                            }}
                            grossAmount {{
                                amount
                                currencyCode
                            }}
                            shop {{
                                id
                                myshopifyDomain
                            }}
                        }}
                        ... on ReferralTransaction {{
                            shop {{
                                id
                                myshopifyDomain
                            }}
                            amount {{
                                amount
                                currencyCode
                            }}
                        }}
                    }}
                }}
                pageInfo {{
                    hasNextPage
                    hasPreviousPage
                }}
            }}
        }}
        """

        return self._execute_query(query)

    def iter_all_transactions(
        self,
        created_at_min: Optional[datetime] = None,
        created_at_max: Optional[datetime] = None,
        transaction_types: Optional[list[str]] = None,
    ) -> Generator[ShopifyTransaction, None, None]:
        """
        Iterate through all transactions with automatic pagination.

        Args:
            created_at_min: Filter transactions created after this date
            created_at_max: Filter transactions created before this date
            transaction_types: Filter by transaction types

        Yields:
            ShopifyTransaction objects
        """
        cursor = None
        has_next = True

        while has_next:
            data = self.get_transactions(
                first=100,
                after=cursor,
                created_at_min=created_at_min,
                created_at_max=created_at_max,
                transaction_types=transaction_types,
            )

            transactions = data.get("transactions", {})
            edges = transactions.get("edges", [])
            page_info = transactions.get("pageInfo", {})

            for edge in edges:
                node = edge.get("node", {})
                cursor = edge.get("cursor")

                # Parse the transaction
                transaction = self._parse_transaction(node)
                if transaction:
                    yield transaction

            has_next = page_info.get("hasNextPage", False)

    def _parse_transaction(self, node: dict[str, Any]) -> Optional[ShopifyTransaction]:
        """Parse a transaction node into a ShopifyTransaction object."""
        if not node:
            return None

        # Determine transaction type from the __typename or structure
        transaction_id = node.get("id", "")
        created_at = node.get("createdAt", "")

        # Extract amounts (ReferralTransaction uses 'amount' instead of 'netAmount'/'grossAmount')
        net_amount_data = node.get("netAmount", {})
        gross_amount_data = node.get("grossAmount", {})
        amount_data = node.get("amount", {})  # For ReferralTransaction

        if net_amount_data:
            net_amount = float(net_amount_data.get("amount", 0))
            currency = net_amount_data.get("currencyCode", "USD")
        elif amount_data:
            net_amount = float(amount_data.get("amount", 0))
            currency = amount_data.get("currencyCode", "USD")
        else:
            net_amount = 0.0
            currency = "USD"

        gross_amount = float(gross_amount_data.get("amount", 0)) if gross_amount_data else net_amount

        # Extract app info
        app_data = node.get("app", {})
        app_name = app_data.get("name") if app_data else None
        app_id = app_data.get("id") if app_data else None

        # Extract shop info
        shop_data = node.get("shop", {})
        shop_domain = shop_data.get("myshopifyDomain") if shop_data else None
        shop_id = shop_data.get("id") if shop_data else None

        # Determine transaction type from ID pattern
        transaction_type = "unknown"
        if "AppSubscriptionSale" in transaction_id or "subscription" in transaction_id.lower():
            transaction_type = "app_subscription"
        elif "AppUsageSale" in transaction_id or "usage" in transaction_id.lower():
            transaction_type = "app_usage"
        elif "AppOneTimeSale" in transaction_id or "onetime" in transaction_id.lower():
            transaction_type = "app_one_time"
        elif "AppSaleCredit" in transaction_id or "credit" in transaction_id.lower():
            transaction_type = "app_credit"
        elif "ServiceSale" in transaction_id or "service" in transaction_id.lower():
            transaction_type = "service"
        elif "Referral" in transaction_id or "referral" in transaction_id.lower():
            transaction_type = "referral"

        return ShopifyTransaction(
            id=transaction_id,
            transaction_type=transaction_type,
            created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
            net_amount=net_amount,
            gross_amount=gross_amount,
            currency=currency,
            app_name=app_name,
            app_id=app_id,
            shop_domain=shop_domain,
            shop_id=shop_id,
        )

    def get_apps(self) -> dict[str, Any]:
        """Fetch list of apps from the Partner API."""
        query = """
        query {
            apps(first: 100) {
                edges {
                    node {
                        id
                        name
                        appType
                    }
                }
                pageInfo {
                    hasNextPage
                }
            }
        }
        """
        return self._execute_query(query)

    def test_connection(self) -> bool:
        """Test the API connection by fetching organization info."""
        query = """
        query {
            publicApiVersions {
                handle
                supported
            }
        }
        """
        try:
            self._execute_query(query)
            return True
        except Exception:
            return False
