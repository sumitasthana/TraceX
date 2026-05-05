"""Hand-curated semantic descriptions for every Layer 0 (`src_*`) column.

Source-table columns have no producing stage and no upstream chain — the
agent enrichment loop never fires for them. Without curation they would
sit empty forever and the inspector would render an unhelpful placeholder.
This dict is the catalog's documentation surface for the synthetic banking
dataset; a real catalog would carry the same content as a per-column
`description` ratified by a steward.

Keys are `<table>.<column>` lowercased. Update here whenever a Layer 0
schema changes; the next `cli.py pipeline` run picks up the new text via
`graph_builder._upsert_column_stub`.
"""
from __future__ import annotations


SOURCE_DESCRIPTIONS: dict[str, str] = {
    # ── src_branch ──
    "src_branch.branch_id":      "Unique identifier for the bank branch (primary key).",
    "src_branch.branch_name":    "Branch name as registered with regulatory bodies.",
    "src_branch.address_line1":  "Street address of the branch's physical location.",
    "src_branch.city":           "City where the branch is located.",
    "src_branch.state":          "Two-letter US state code where the branch is located.",
    "src_branch.country":        "ISO country code (defaults to 'US' for this dataset).",
    "src_branch.region":         "Geographic region grouping: NORTHEAST, SOUTHEAST, MIDWEST, WEST, or SOUTHWEST.",
    "src_branch.active_flag":    "Y/N flag indicating whether the branch is currently operating.",
    "src_branch.created_at":     "Timestamp the branch record was first created in the source system.",

    # ── src_customer ──
    "src_customer.customer_id":      "Unique customer identifier (primary key).",
    "src_customer.first_name":       "Customer's legal first name.",
    "src_customer.last_name":        "Customer's legal last name.",
    "src_customer.dob":              "Customer's date of birth.",
    "src_customer.ssn_hash":         "SHA-256 hash of the customer's Social Security Number — opaque to the application; never the raw SSN.",
    "src_customer.country_of_birth": "ISO country code where the customer was born.",
    "src_customer.citizenship":      "ISO country code of the customer's primary citizenship.",
    "src_customer.onboarded_date":   "Calendar date the customer was first onboarded to the bank.",
    "src_customer.kyc_status":       "Current Know-Your-Customer review state: PENDING, APPROVED, EXPIRED, or REJECTED.",
    "src_customer.kyc_reviewed_at":  "Timestamp of the most recent KYC review (nullable until the first review).",
    "src_customer.branch_id":        "Branch where the customer relationship is anchored (FK to src_branch).",
    "src_customer.created_at":       "Timestamp the customer record was created in the source system.",
    "src_customer.updated_at":       "Timestamp the customer record was last updated.",

    # ── src_account ──
    "src_account.account_id":     "Unique account identifier (primary key).",
    "src_account.customer_id":    "Owning customer (FK to src_customer).",
    "src_account.account_type":   "Account product family: CHECKING, SAVINGS, MONEY_MARKET, CD, LOAN, or CREDIT.",
    "src_account.product_code":   "Internal product SKU identifying the specific account product.",
    "src_account.open_date":      "Calendar date the account was opened.",
    "src_account.close_date":     "Calendar date the account was closed (nullable while open).",
    "src_account.status":         "Current account state: ACTIVE, CLOSED, FROZEN, or DORMANT.",
    "src_account.interest_rate":  "Annual interest rate as a decimal (four places of precision).",
    "src_account.credit_limit":   "Maximum credit available on the account (nullable; populated only for credit products).",
    "src_account.branch_id":      "Branch where the account is held (FK to src_branch).",
    "src_account.created_at":     "Timestamp the account record was created.",
    "src_account.updated_at":     "Timestamp the account record was last updated.",

    # ── src_transaction ──
    "src_transaction.txn_id":                "Unique transaction identifier (primary key).",
    "src_transaction.account_id":            "Account on which the transaction posted (FK to src_account).",
    "src_transaction.txn_date":              "Calendar date the transaction posted.",
    "src_transaction.txn_timestamp":         "Exact timestamp the transaction was executed.",
    "src_transaction.txn_type":              "Transaction kind: CREDIT, DEBIT, TRANSFER, FEE, REVERSAL, WIRE, or ACH.",
    "src_transaction.amount":                "Transaction amount in the original currency, before any USD conversion.",
    "src_transaction.currency":              "Three-letter ISO currency code of the original transaction (e.g. USD, EUR, GBP).",
    "src_transaction.counterparty_account":  "Counterparty account number for transfers and wires (nullable).",
    "src_transaction.counterparty_bank_bic": "BIC of the counterparty bank for wires/ACH; non-US BICs are what flag the transaction as international downstream.",
    "src_transaction.channel":               "Originating channel: BRANCH, ATM, ONLINE, MOBILE, WIRE, ACH, or POS.",
    "src_transaction.status":                "Settlement state: SETTLED, PENDING, FAILED, or REVERSED.",
    "src_transaction.reversal_flag":         "'Y' when this transaction reverses an earlier one; 'N' otherwise.",
    "src_transaction.original_txn_id":       "Reference back to the originally-reversed transaction (nullable; populated only when reversal_flag='Y').",
    "src_transaction.created_at":            "Timestamp the transaction record was created.",

    # ── src_fx_rate ──
    "src_fx_rate.rate_id":       "Surrogate primary key for FX rate rows.",
    "src_fx_rate.rate_date":     "Calendar date the rate is effective for.",
    "src_fx_rate.from_currency": "Three-letter ISO currency code being converted FROM.",
    "src_fx_rate.to_currency":   "Three-letter ISO currency code being converted TO (USD throughout this dataset).",
    "src_fx_rate.rate":          "Spot exchange rate from `from_currency` to `to_currency` on `rate_date`.",
    "src_fx_rate.rate_source":   "Provider of the rate: ECB, FED, or MANUAL.",
    "src_fx_rate.created_at":    "Timestamp the rate row was ingested into the source system.",
}


def get_source_description(table: str, column: str) -> str:
    return SOURCE_DESCRIPTIONS.get(f"{table}.{column}".lower(), "")


__all__ = ["SOURCE_DESCRIPTIONS", "get_source_description"]
