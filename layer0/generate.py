"""
TraceX — Layer 0 Synthetic Data Generator
Produces CSV files for: src_branch, src_fx_rate, src_customer, src_account, src_transaction

Volumes (tunable via constants):
  - 20 branches
  - ~2 years of daily FX rates for 6 currencies
  - 500 customers
  - ~1.5 accounts per customer (~750 accounts)
  - ~40 transactions per account (~30,000 transactions)

Run:
    pip install faker
    python generate_layer0.py

Output: ./data/layer0/*.csv
"""

import csv
import hashlib
import os
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from faker import Faker

fake = Faker("en_US")
random.seed(42)
Faker.seed(42)

# ── tunables ──────────────────────────────────────────────
N_BRANCHES      = 20
N_CUSTOMERS     = 500
AVG_ACCOUNTS    = 1.5       # per customer
AVG_TXNS        = 40        # per account
START_DATE      = date(2022, 1, 1)
END_DATE        = date(2023, 12, 31)
CURRENCIES      = ["EUR", "GBP", "CAD", "MXN", "JPY", "CHF"]  # all → USD
# ─────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = str(REPO_ROOT / "data" / "layer0")
os.makedirs(OUT_DIR, exist_ok=True)

def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

def random_date(start, end):
    return start + timedelta(days=random.randint(0, (end - start).days))

def fmt_ts(d):
    return datetime(d.year, d.month, d.day,
                    random.randint(6, 22), random.randint(0, 59), random.randint(0, 59)).isoformat(sep=" ")

# ── 1. src_branch ─────────────────────────────────────────
REGIONS = {
    "NORTHEAST": ["NY", "MA", "CT", "NJ", "PA"],
    "SOUTHEAST": ["FL", "GA", "NC", "SC", "VA"],
    "MIDWEST":   ["IL", "OH", "MI", "MN", "WI"],
    "WEST":      ["CA", "WA", "OR", "NV", "AZ"],
    "SOUTHWEST": ["TX", "NM", "CO", "UT", "OK"],
}
STATE_REGION = {state: region for region, states in REGIONS.items() for state in states}

branches = []
for i in range(1, N_BRANCHES + 1):
    region = random.choice(list(REGIONS.keys()))
    state  = random.choice(REGIONS[region])
    branches.append({
        "branch_id":    f"BR{i:04d}",
        "branch_name":  f"{fake.city()} Banking Center",
        "address_line1": fake.street_address(),
        "city":         fake.city(),
        "state":        state,
        "country":      "US",
        "region":       region,
        "active_flag":  "Y",
        "created_at":   fmt_ts(START_DATE - timedelta(days=random.randint(180, 1800))),
    })

with open(f"{OUT_DIR}/src_branch.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=branches[0].keys())
    w.writeheader(); w.writerows(branches)

print(f"src_branch:      {len(branches)} rows")
branch_ids = [b["branch_id"] for b in branches]

# ── 2. src_fx_rate ────────────────────────────────────────
# Simulate rates with random walk, seeded per currency
BASE_RATES = {"EUR": 1.08, "GBP": 1.27, "CAD": 0.74, "MXN": 0.058, "JPY": 0.0071, "CHF": 1.12}
fx_rows = []
rate_id = 1
for currency, base in BASE_RATES.items():
    rate = base
    for d in daterange(START_DATE, END_DATE):
        rate = max(rate * (1 + random.gauss(0, 0.003)), 0.001)
        fx_rows.append({
            "rate_id":       rate_id,
            "rate_date":     d.isoformat(),
            "from_currency": currency,
            "to_currency":   "USD",
            "rate":          round(rate, 8),
            "rate_source":   random.choice(["ECB", "FED"]),
            "created_at":    fmt_ts(d),
        })
        rate_id += 1

with open(f"{OUT_DIR}/src_fx_rate.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fx_rows[0].keys())
    w.writeheader(); w.writerows(fx_rows)

print(f"src_fx_rate:     {len(fx_rows)} rows")

# ── 3. src_customer ───────────────────────────────────────
KYC_STATUSES   = ["APPROVED"] * 70 + ["EXPIRED"] * 15 + ["PENDING"] * 10 + ["REJECTED"] * 5
CITIZENSHIPS   = ["US"] * 80 + ["GB", "CA", "MX", "DE", "FR", "IN", "CN", "BR", "JP", "AU"]

customers = []
for i in range(1, N_CUSTOMERS + 1):
    dob           = random_date(date(1950, 1, 1), date(2000, 12, 31))
    onboarded     = random_date(START_DATE - timedelta(days=730), END_DATE - timedelta(days=30))
    kyc_status    = random.choice(KYC_STATUSES)
    citizenship   = random.choice(CITIZENSHIPS)
    cob           = citizenship if random.random() < 0.85 else random.choice(["US", "GB", "IN", "MX"])
    kyc_reviewed  = (onboarded + timedelta(days=random.randint(1, 60))).isoformat() \
                    if kyc_status != "PENDING" else ""
    raw_ssn       = f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(1000,9999)}"
    ssn_hash      = hashlib.sha256(raw_ssn.encode()).hexdigest()

    customers.append({
        "customer_id":      f"CUST{i:06d}",
        "first_name":       fake.first_name(),
        "last_name":        fake.last_name(),
        "dob":              dob.isoformat(),
        "ssn_hash":         ssn_hash,
        "country_of_birth": cob,
        "citizenship":      citizenship,
        "onboarded_date":   onboarded.isoformat(),
        "kyc_status":       kyc_status,
        "kyc_reviewed_at":  kyc_reviewed,
        "branch_id":        random.choice(branch_ids),
        "created_at":       fmt_ts(onboarded),
        "updated_at":       fmt_ts(onboarded),
    })

with open(f"{OUT_DIR}/src_customer.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=customers[0].keys())
    w.writeheader(); w.writerows(customers)

print(f"src_customer:    {len(customers)} rows")
customer_ids = [c["customer_id"] for c in customers]

# ── 4. src_account ────────────────────────────────────────
ACCOUNT_TYPES = {
    "CHECKING":    {"product_codes": ["CHK_BASIC", "CHK_PREMIUM"], "has_credit": False},
    "SAVINGS":     {"product_codes": ["SAV_BASIC", "SAV_HIYIELD"], "has_credit": False},
    "MONEY_MARKET":{"product_codes": ["MM_STANDARD"],              "has_credit": False},
    "CD":          {"product_codes": ["CD_6MO", "CD_12MO", "CD_24MO"], "has_credit": False},
    "LOAN":        {"product_codes": ["LOAN_PERSONAL", "LOAN_AUTO"], "has_credit": True},
    "CREDIT":      {"product_codes": ["CC_BASIC", "CC_REWARDS"],   "has_credit": True},
}
TYPE_WEIGHTS = ["CHECKING"]*40 + ["SAVINGS"]*30 + ["MONEY_MARKET"]*10 + ["CD"]*8 + ["LOAN"]*7 + ["CREDIT"]*5

accounts = []
account_id_counter = 1
customer_accounts = {}  # customer_id → [account_id]

for cust in customers:
    n_accounts = max(1, int(random.gauss(AVG_ACCOUNTS, 0.7)))
    onboarded  = date.fromisoformat(cust["onboarded_date"])
    acct_list  = []

    for _ in range(n_accounts):
        atype    = random.choice(TYPE_WEIGHTS)
        cfg      = ACCOUNT_TYPES[atype]
        open_dt  = random_date(onboarded, min(onboarded + timedelta(days=365), END_DATE))
        is_closed = random.random() < 0.1
        close_start = open_dt + timedelta(days=30)
        close_dt  = random_date(close_start, END_DATE).isoformat() if (is_closed and close_start < END_DATE) else ""
        status    = "CLOSED" if is_closed else random.choice(["ACTIVE"]*85 + ["FROZEN"]*10 + ["DORMANT"]*5)

        acct_id  = f"ACCT{account_id_counter:08d}"
        account_id_counter += 1
        acct_list.append(acct_id)

        accounts.append({
            "account_id":    acct_id,
            "customer_id":   cust["customer_id"],
            "account_type":  atype,
            "product_code":  random.choice(cfg["product_codes"]),
            "open_date":     open_dt.isoformat(),
            "close_date":    close_dt,
            "status":        status,
            "interest_rate": round(random.uniform(0.001, 0.08), 4) if atype not in ["LOAN"] else round(random.uniform(0.04, 0.24), 4),
            "credit_limit":  round(random.choice([1000,2000,5000,10000,25000,50000]), 2) if cfg["has_credit"] else "",
            "branch_id":     cust["branch_id"],
            "created_at":    fmt_ts(open_dt),
            "updated_at":    fmt_ts(open_dt),
        })

    customer_accounts[cust["customer_id"]] = acct_list

with open(f"{OUT_DIR}/src_account.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=accounts[0].keys())
    w.writeheader(); w.writerows(accounts)

print(f"src_account:     {len(accounts)} rows")
account_ids = [a["account_id"] for a in accounts]
account_map  = {a["account_id"]: a for a in accounts}

# ── 5. src_transaction ────────────────────────────────────
DOMESTIC_BICS  = [f"US{fake.lexify('?????')}XXX" for _ in range(30)]
INTL_BICS      = ["DEUTDEDB", "BARCGB22", "BNPAFRPP", "CHASUS33", "HSBCHKHH",
                   "MXBBMXMM", "TOKYOJPJT", "UBSWCHZH", "RBOSGB2L", "CITIGB2L"]
TXN_TYPES      = ["CREDIT"]*20 + ["DEBIT"]*35 + ["TRANSFER"]*15 + ["FEE"]*10 + ["WIRE"]*10 + ["ACH"]*10
CHANNELS       = ["ONLINE"]*30 + ["MOBILE"]*25 + ["ATM"]*15 + ["BRANCH"]*10 + ["POS"]*10 + ["ACH"]*5 + ["WIRE"]*5
CURR_WEIGHTS   = ["USD"]*60 + ["EUR"]*12 + ["GBP"]*8 + ["CAD"]*8 + ["MXN"]*5 + ["JPY"]*4 + ["CHF"]*3

transactions = []
txn_counter  = 1

for acct in accounts:
    acct_id    = acct["account_id"]
    open_dt    = date.fromisoformat(acct["open_date"])
    close_str  = acct["close_date"]
    close_dt   = date.fromisoformat(close_str) if close_str else END_DATE
    if open_dt >= close_dt:
        continue

    n_txns = max(1, int(random.gauss(AVG_TXNS, 15)))

    # inject a small cluster of suspicious activity for ~5% of accounts
    suspicious = random.random() < 0.05
    if suspicious:
        n_txns += random.randint(10, 30)

    for _ in range(n_txns):
        txn_dt      = random_date(open_dt, close_dt)
        currency    = random.choice(CURR_WEIGHTS)
        txn_type    = random.choice(TXN_TYPES)
        is_intl     = currency != "USD" or (txn_type == "WIRE" and random.random() < 0.4)
        bic         = random.choice(INTL_BICS) if is_intl else (random.choice(DOMESTIC_BICS) if txn_type in ["ACH","WIRE","TRANSFER"] else "")
        amount      = round(random.lognormvariate(5, 1.5), 2)  # log-normal gives realistic spread
        if suspicious:
            amount  = round(amount * random.uniform(3, 10), 2)

        is_reversal = "Y" if txn_type == "REVERSAL" else ("Y" if random.random() < 0.02 else "N")
        status      = "REVERSED" if is_reversal == "Y" else random.choice(["SETTLED"]*90 + ["PENDING"]*7 + ["FAILED"]*3)
        txn_id      = f"TXN{txn_counter:010d}"
        txn_counter += 1

        transactions.append({
            "txn_id":                txn_id,
            "account_id":            acct_id,
            "txn_date":              txn_dt.isoformat(),
            "txn_timestamp":         fmt_ts(txn_dt),
            "txn_type":              txn_type,
            "amount":                amount,
            "currency":              currency,
            "counterparty_account":  fake.bban() if bic else "",
            "counterparty_bank_bic": bic,
            "channel":               random.choice(CHANNELS),
            "status":                status,
            "reversal_flag":         is_reversal,
            "original_txn_id":       f"TXN{random.randint(1, txn_counter-1):010d}" if is_reversal == "Y" else "",
            "created_at":            fmt_ts(txn_dt),
        })

with open(f"{OUT_DIR}/src_transaction.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=transactions[0].keys())
    w.writeheader(); w.writerows(transactions)

print(f"src_transaction: {len(transactions)} rows")
print(f"\nAll CSVs written to {OUT_DIR}/")
