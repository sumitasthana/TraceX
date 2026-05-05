# TraceX UI

A web UI for the TraceX lineage platform, styled after the Themis/ReconX design
system (navy topbar, clean white cards on a `g-50` page background, status-coloured
badges, DM Sans / DM Mono typography). FastAPI backend reads directly from the
existing `tracex_layer0.duckdb`, `tracex_graph` (Kuzu), and `logs/{run_id}.jsonl`
artefacts — no separate data store.

## Views

| Route          | Purpose |
|----------------|---------|
| `#/dashboard`  | Briefing — top metrics, latest run breakdown, lineage graph stats |
| `#/runs`       | Pipeline Runs — every JSONL log, click-through to detail |
| `#/runs/{id}`  | Run detail — stages + every DQ check |
| `#/lineage`    | Lineage Explorer — interactive vis-network graph; click any node to inspect |
| `#/datasets`   | Datasets browser (DataSet vertices in the graph) |
| `#/dq`         | DQ Console — latest run's checks with pass/fail |

## Run

```powershell
cd C:\LangChain\TraceX
pip install -r requirements.txt
python ui\serve.py
# UI -> http://127.0.0.1:8765
```

Override host/port via `TRACEX_UI_HOST` / `TRACEX_UI_PORT`. Default port is **8765**
(8000 is reserved for the user's other Themis service on this machine).

## Endpoints

| Method | Path                          | Returns |
|--------|-------------------------------|---------|
| GET    | `/api/dashboard`              | top-level metrics for the briefing view |
| GET    | `/api/runs`                   | compact list of pipeline runs |
| GET    | `/api/runs/{run_id}`          | one run + every stage + every DQ check |
| GET    | `/api/lineage/graph`          | full node+edge payload for vis-network |
| GET    | `/api/lineage/dataset/{name}` | upstream + downstream tables |
| GET    | `/api/datasets`               | every DataSet in the graph |
| GET    | `/api/dq/{run_id}`            | DQ checks grouped by stage |
| GET    | `/healthz`                    | `{ok: true}` |

## Design system mapping

The Themis design system tokens are rendered via Tailwind (CDN with a runtime config)
plus a thin custom `static/styles.css` for the `card`, `nav-btn`, `metric-card`,
`badge*`, `pill`, `running-pill`, `tbl`, and animation rules from the spec.

| Spec token              | Where it lives |
|-------------------------|---------------|
| Navy `#0c1f3d`          | topbar background, primary buttons, active nav text |
| Navy-light `#e8eef7`    | active nav background, active pills |
| Status colors (red/amber/blue/green/teal/purple) | risk badges, layer badges, DQ pass/fail, edge colours |
| `text-[26px] font-medium tracking-tight` | metric-card values |
| DM Sans / DM Mono       | font stacks (loaded from Google Fonts) |
| `border-radius: 10px`   | every card |
| `.animate-fadein`       | view enter |
| `pulse-dot` keyframe    | running-status pill |

Risk-tier badges follow the Themis-specific section verbatim:
`CRITICAL` → red, `HIGH` → amber, `MEDIUM` → blue, `LOW` → green.

## Layer colour coding (TraceX-specific)

For the lineage graph and dataset chips:
| Layer    | Colour                                    |
|----------|-------------------------------------------|
| layer_0  | teal `#0f766e` (raw sources)              |
| layer_1  | blue `#1d4ed8` (staging)                  |
| layer_2  | purple `#6d28d9` (facts)                  |
| Process  | navy `#0c1f3d` (pipeline stage nodes)     |

## Files

```
ui/
  api.py            FastAPI app — endpoints + JSONL summarization
  serve.py          uvicorn launcher (python ui/serve.py)
  static/
    index.html      shell + topbar + sidebar + tailwind config
    styles.css      design-system tokens (cards, badges, tables, animations)
    app.js          hash router + view renderers + vis-network wiring
  README.md
```
