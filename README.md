# TraceX

Data-lineage platform for a synthetic banking dataset: generates raw sources,
runs a multi-stage staging + facts pipeline with structured logs and a DQ gate,
parses logs into a property graph (Kuzu locally / JanusGraph for prod), and
serves the whole thing through a FastAPI + Tailwind UI.

## Layout

```text
TraceX/
├── README.md                  this file
├── cli.py                     `python cli.py {up|serve|generate|load|pipeline|ingest|...}`
├── docker-compose.yaml        JanusGraph (Gremlin :8182)
├── requirements.txt           consolidated Python deps
├── start-all.ps1              thin wrapper around `python cli.py up`
│
├── data/                      all persistent artefacts live here
│   ├── layer0/                synthetic raw CSVs
│   ├── tracex_layer0.duckdb   DuckDB (layer 0 + 1 + 2 tables)
│   └── tracex_graph           Kuzu lineage graph (single file)
│
├── layer0/                    raw-source generation + DuckDB load
│   ├── generate.py            Faker → data/layer0/*.csv
│   ├── ddl.sql                authoritative DDL
│   └── load_duckdb.py         CSV → data/tracex_layer0.duckdb
│
├── pipeline/                  layer 1 (staging) + layer 2 (facts)
│   ├── config.py              env-driven paths, run_id, structlog setup
│   ├── run_pipeline.py        orchestrator (subprocess per stage)
│   └── stages/                00..03 staging, 10..11 facts, 99 DQ gate
│
├── lineage/                   parse JSONL logs → property graph
│   ├── parser.py
│   ├── graph_builder.py
│   ├── ingest.py              CLI: --run-id / --log-file / --latest
│   ├── queries.py
│   ├── manifest_builder.py    live post-stage hook (Phase A → G)
│   ├── sql_parser.py          deterministic sqlglot walker
│   ├── agents/                sql_parser, enrichment, impact_analyst, chat
│   └── catalog/               local catalog (DuckDB), client protocol,
│                              merge logic, seeder, tests
│
├── graph/                     JanusGraph integration
│   └── healthcheck.py         wait → bootstrap schema → smoke test
│
├── ui/                        FastAPI backend + static SPA
│   ├── api.py                 reads DuckDB / Kuzu / logs directly
│   ├── serve.py               uvicorn launcher (default :8765)
│   └── static/                hash-routed Themis-styled SPA
│
├── docs/
│   ├── PIPELINE.md            stage-by-stage reference, DQ rules, event taxonomy
│   └── CATALOG.md             catalog-first lineage: provenance, ratify/reject, gating
│
└── logs/                      one {run_id}.jsonl per pipeline run (gitignored)
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## CLI

Every operation runs through `cli.py`. The CLI is the single entrypoint — there is
no separate frontend / backend process: `serve` launches one FastAPI process that
hosts the SPA at `/` and the JSON API at `/api/*`.

```powershell
python cli.py up           # bootstrap + serve (skips steps whose outputs exist)
python cli.py up --force   # re-run generate + load even if outputs exist
python cli.py serve        # launch UI + API only
python cli.py status       # report which artefacts exist on disk
```

| Subcommand     | What it does                                                    |
|----------------|-----------------------------------------------------------------|
| `up`           | generate → load → pipeline → ingest → serve (skips if cached)   |
| `serve`        | FastAPI on `--host`/`--port` (defaults `127.0.0.1:8765`)        |
| `generate`     | Faker → `data/layer0/*.csv`                                     |
| `load`         | CSVs → `data/tracex_layer0.duckdb`                              |
| `pipeline`     | run staging + facts; writes `logs/{run_id}.jsonl`               |
| `ingest`       | `--latest` (default), `--run-id ID`, or `--log-file PATH`       |
| `healthcheck`  | wait for JanusGraph + bootstrap schema + smoke test             |
| `status`       | show which artefacts exist on disk                              |

`.\start-all.ps1` is a one-line wrapper around `python cli.py up`. After it
finishes the bootstrap, the UI is reachable at <http://127.0.0.1:8765>.

## JanusGraph (optional)

For the production-style graph backend instead of Kuzu:

```powershell
docker compose up -d
python graph\healthcheck.py            # wait for Gremlin, bootstrap schema, smoke test
```

See [docs/PIPELINE.md](docs/PIPELINE.md) for the stage-by-stage walkthrough,
DQ rules, and event taxonomy. See [docs/CATALOG.md](docs/CATALOG.md) for
the catalog-first lineage layer (provenance, ratify/reject lifecycle,
profile gating, divergence rule).

## Environment variables

| Variable             | Default                              | Purpose                                     |
|----------------------|--------------------------------------|---------------------------------------------|
| `TRACEX_DB_PATH`     | `data/tracex_layer0.duckdb`          | DuckDB used by pipeline + UI                |
| `TRACEX_GRAPH_PATH`  | `data/tracex_graph`                  | Kuzu graph file used by lineage + UI        |
| `TRACEX_LOG_DIR`     | `logs/`                              | Where `{run_id}.jsonl` files are written    |
| `TRACEX_RUN_ID`      | (fresh UUID per process)             | Set by orchestrator so all stages share one |
| `TRACEX_UI_HOST`     | `127.0.0.1`                          | UI bind host                                |
| `TRACEX_UI_PORT`     | `8765`                               | UI port                                     |
| `TRACEX_CATALOG`     | `on`                                 | Set to `off` to bypass the catalog phase entirely (legacy pre-catalog behaviour: everything tagged `ratified`) |

A `.env` file at the repo root is auto-loaded.
