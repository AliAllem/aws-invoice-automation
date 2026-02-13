"""
Cost Extractor - Pulls cost data from AWS Cost Explorer per payer account.

Handles pagination, throttling, and data normalisation. The throttling
bit is important — CE has a 5 requests/second limit and if you blast it
you get throttled hard. Ask me how I know.

Also filters out "dust" (amounts under 0.001) because otherwise your
reports are full of services that charged you a fraction of a penny
and it just adds noise.
"""

import boto3
from datetime import datetime
from calendar import monthrange
import logging
import time

logger = logging.getLogger(__name__)


class CostExtractor:
    """Extracts and normalises cost data from AWS Cost Explorer."""

    def __init__(self, region: str = "us-east-1"):
        self.ce_client = boto3.client("ce", region_name=region)
        self._request_count = 0

    def extract_monthly_costs(
        self,
        payer_account_id: str,
        month: str,
        granularity: str = "DAILY",
    ) -> list:
        """
        Extract cost data for a specific payer account and month.

        Args:
            payer_account_id: The 12-digit payer account ID.
            month: Target month in YYYY-MM format.
            granularity: DAILY or MONTHLY.

        Returns:
            List of normalised cost records.
        """
        year, mon = map(int, month.split("-"))
        _, last_day = monthrange(year, mon)

        start_date = f"{month}-01"
        end_date = f"{month}-{last_day:02d}"

        # If current month, use today as end date
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if end_date > today:
            end_date = today

        costs = self._query_cost_explorer(
            start_date=start_date,
            end_date=end_date,
            granularity=granularity,
            filter_by={
                "Dimensions": {
                    "Key": "LINKED_ACCOUNT",
                    "Values": [payer_account_id],
                    "MatchOptions": ["EQUALS"],
                }
            } if payer_account_id else None,
            group_by="SERVICE",
        )

        return self._normalise_results(costs, payer_account_id)

    def _query_cost_explorer(
        self,
        start_date: str,
        end_date: str,
        granularity: str = "DAILY",
        filter_by: dict = None,
        group_by: str = None,
    ) -> list:
        """Execute a paginated Cost Explorer query with throttle handling."""
        params = {
            "TimePeriod": {"Start": start_date, "End": end_date},
            "Granularity": granularity,
            "Metrics": ["UnblendedCost", "BlendedCost"],
        }

        if filter_by:
            params["Filter"] = filter_by

        if group_by:
            params["GroupBy"] = [{"Type": "DIMENSION", "Key": group_by}]

        results = []
        while True:
            self._throttle()
            response = self.ce_client.get_cost_and_usage(**params)
            results.extend(response["ResultsByTime"])

            if "NextPageToken" in response:
                params["NextPageToken"] = response["NextPageToken"]
            else:
                break

        return results

    def _normalise_results(self, raw_results: list, payer_id: str) -> list:
        """Normalise Cost Explorer results into a flat list of records."""
        records = []

        for period in raw_results:
            date = period["TimePeriod"]["Start"]

            for group in period.get("Groups", []):
                service = group["Keys"][0]
                unblended = float(group["Metrics"]["UnblendedCost"]["Amount"])
                blended = float(group["Metrics"]["BlendedCost"]["Amount"])

                if unblended > 0.001:  # filter dust
                    records.append({
                        "date": date,
                        "payer_account": payer_id,
                        "service": service,
                        "amount": round(unblended, 4),
                        "blended_amount": round(blended, 4),
                        "currency": group["Metrics"]["UnblendedCost"].get(
                            "Unit", "USD"
                        ),
                    })

            # Handle ungrouped totals
            if "Total" in period and not period.get("Groups"):
                total = float(period["Total"]["UnblendedCost"]["Amount"])
                if total > 0.001:
                    records.append({
                        "date": date,
                        "payer_account": payer_id,
                        "service": "Total",
                        "amount": round(total, 4),
                        "currency": "USD",
                    })

        return records

    def _throttle(self):
        """Simple rate limiter for Cost Explorer API (5 requests/sec)."""
        self._request_count += 1
        if self._request_count % 5 == 0:
            time.sleep(1)
