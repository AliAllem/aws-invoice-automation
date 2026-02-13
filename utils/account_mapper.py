"""
Account Mapper - Maps AWS account IDs to business units, cost centres, and owners.

In theory, AWS Organizations tags should handle this. In practice, every
org I've worked with has account metadata scattered across three different
spreadsheets, a Confluence page that hasn't been updated since 2022, and
someone's head. This keeps it all in one YAML file that's version-controlled
and reviewable.

The validate_mappings() method is useful to run before onboarding a new
payer account — it'll tell you if anything's missing before you get
"Unassigned" showing up in your finance reports.
"""

import yaml
import logging

logger = logging.getLogger(__name__)


class AccountMapper:
    """Maps AWS accounts to organisational metadata."""

    def __init__(self, mapping_file: str = None, mapping_data: dict = None):
        if mapping_file:
            with open(mapping_file) as f:
                self.mappings = yaml.safe_load(f)
        elif mapping_data:
            self.mappings = mapping_data
        else:
            self.mappings = {"payer_accounts": []}

        self._index = self._build_index()

    def _build_index(self) -> dict:
        """Build a lookup index from account ID to metadata."""
        index = {}
        for account in self.mappings.get("payer_accounts", []):
            index[account["id"]] = {
                "name": account.get("name", "Unknown"),
                "business_unit": account.get("business_unit", "Unassigned"),
                "cost_centre": account.get("cost_centre", ""),
                "owner": account.get("owner", ""),
                "environment": account.get("environment", ""),
            }
        return index

    def get_business_unit(self, account_id: str) -> str:
        """Get the business unit for an account ID."""
        meta = self._index.get(account_id)
        if meta:
            return meta["business_unit"]
        logger.warning(f"Unmapped account: {account_id}")
        return "Unassigned"

    def get_metadata(self, account_id: str) -> dict:
        """Get full metadata for an account ID."""
        return self._index.get(account_id, {
            "name": "Unknown",
            "business_unit": "Unassigned",
            "cost_centre": "",
            "owner": "",
            "environment": "",
        })

    def get_unmapped_accounts(self, account_ids: list) -> list:
        """Identify account IDs that have no mapping configured."""
        return [aid for aid in account_ids if aid not in self._index]

    def validate_mappings(self) -> dict:
        """Validate the mapping configuration for completeness."""
        issues = []
        for account in self.mappings.get("payer_accounts", []):
            if not account.get("business_unit"):
                issues.append(f"Account {account['id']}: missing business_unit")
            if not account.get("cost_centre"):
                issues.append(f"Account {account['id']}: missing cost_centre")

        return {
            "valid": len(issues) == 0,
            "total_accounts": len(self.mappings.get("payer_accounts", [])),
            "issues": issues,
        }
