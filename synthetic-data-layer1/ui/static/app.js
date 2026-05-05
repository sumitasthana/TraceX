/* TraceX UI — single-file vanilla JS app.
 *
 * Routes are encoded in window.location.hash (#/dashboard, #/runs, ...).
 * Each view is a function taking the page-root element and an optional param.
 * Data is fetched fresh per view; no client-side cache — these endpoints are cheap
 * and the data churns whenever the pipeline runs.
 */

const App = (() => {
  const root = document.getElementById('page-root');

  // ---------- helpers --------------------------------------------------

  const fmtInt = (n) => (n == null ? '—' : Number(n).toLocaleString('en-US'));
  const fmtMs  = (n) => (n == null ? '—' : `${Number(n).toLocaleString('en-US')} ms`);
  const fmtUsd = (n) => (n == null ? '—' : `$${Number(n).toLocaleString('en-US', { maximumFractionDigits: 2 })}`);
  const esc    = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const truncate = (s, n) => { s = String(s ?? ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  async function api(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  }

  function setLoading(msg = 'Loading…') {
    root.innerHTML = `<div class="text-g-400 text-[12px] animate-fadein">${esc(msg)}</div>`;
  }

  function setError(err) {
    root.innerHTML = `
      <div class="card p-4 animate-fadein">
        <div class="badge badge-red mb-2">Error</div>
        <div class="text-[13px] text-g-700">${esc(err.message || err)}</div>
      </div>
    `;
  }

  function pageHeader(title, sub, rightHtml = '') {
    return `
      <div class="flex items-end justify-between mb-5 animate-fadein">
        <div>
          <h1 class="page-h1">${esc(title)}</h1>
          ${sub ? `<div class="page-sub">${esc(sub)}</div>` : ''}
        </div>
        <div class="flex items-center gap-2">${rightHtml}</div>
      </div>
    `;
  }

  function statusBadge(status) {
    if (status === 'ok')     return `<span class="badge badge-green">OK</span>`;
    if (status === 'failed') return `<span class="badge badge-red">FAILED</span>`;
    return `<span class="badge badge-gray">${esc(String(status || '—').toUpperCase())}</span>`;
  }

  function riskBadge(priority) {
    const cls = `risk-${priority || 'LOW'}`;
    return `<span class="badge ${cls}">${esc(priority || '—')}</span>`;
  }

  function layerBadge(layer) {
    const map = {
      layer_0: ['badge-teal',   'L0 raw'],
      layer_1: ['badge-blue',   'L1 staging'],
      layer_2: ['badge-purple', 'L2 facts'],
      unknown: ['badge-gray',   'unknown'],
    };
    const [cls, label] = map[layer] || map.unknown;
    return `<span class="badge ${cls}">${esc(label)}</span>`;
  }

  // ---------- views ----------------------------------------------------

  async function viewDashboard() {
    setLoading();
    try {
      const d = await api('/api/dashboard');

      const latest = d.latest_run;
      const latestPill = latest
        ? `${statusBadge(latest.status)} <span class="chip-mono">${esc(latest.run_id.slice(0,8))}</span>`
        : `<span class="badge badge-gray">no runs yet</span>`;

      const metricCard = (label, value, sub, color = '#1f2937') => `
        <div class="metric-card">
          <div class="label">${esc(label)}</div>
          <div class="value" style="color:${color}">${value}</div>
          <div class="sub">${esc(sub)}</div>
        </div>
      `;

      // Top header
      const header = pageHeader(
        'Briefing',
        'Pipeline observability and lineage intelligence for TraceX',
        latestPill
      );

      // Metric strip
      const metrics = `
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6 animate-fadein">
          ${metricCard('Pipeline runs',     fmtInt(d.pipeline_runs_total),  'JSONL logs persisted')}
          ${metricCard('Datasets in graph', fmtInt(d.datasets_total),       `${fmtInt(d.columns_total)} columns · ${fmtInt(d.processes_total)} processes`)}
          ${metricCard('DQ checks',         d.dq_pass_rate,                 'Latest run gate', d.latest_run && d.latest_run.dq_passed === d.latest_run.dq_total ? '#1a7f4b' : '#b45309')}
          ${metricCard('SAR candidates',    fmtInt(d.sar_candidates_total), 'Customers awaiting review', '#b91c1c')}
        </div>
      `;

      // Lineage graph stats card
      const edgeCounts = d.graph_edge_counts || {};
      const nodeCounts = d.graph_node_counts || {};
      const graphStats = `
        <div class="card p-4 animate-fadein">
          <div class="flex items-center justify-between mb-3">
            <div class="text-[14px] font-semibold text-g-800">Lineage Graph</div>
            <a href="#/lineage" class="btn-secondary">Open Explorer</a>
          </div>
          <div class="grid grid-cols-2 gap-2 text-[12px]">
            ${Object.entries(nodeCounts).map(([k, v]) =>
              `<div class="flex justify-between border-b border-g-100 py-1.5"><span class="text-g-500">${esc(k)}</span><span class="font-mono text-g-800">${fmtInt(v)}</span></div>`
            ).join('')}
            ${Object.entries(edgeCounts).map(([k, v]) =>
              `<div class="flex justify-between border-b border-g-100 py-1.5"><span class="text-g-500">${esc(k)}</span><span class="font-mono text-g-800">${fmtInt(v)}</span></div>`
            ).join('')}
          </div>
        </div>
      `;

      // Latest run summary card
      let latestRunCard = `
        <div class="card p-4 animate-fadein">
          <div class="text-[14px] font-semibold text-g-800 mb-3">Latest run</div>
          <div class="text-[12px] text-g-500">No pipeline run logs found in <code>logs/</code>.</div>
        </div>
      `;
      if (latest) {
        const stages = (latest.stages || []).map(s => `
          <tr>
            <td class="mono">${esc(s.stage)}</td>
            <td>${statusBadge(s.status)}</td>
            <td class="text-right mono">${fmtMs(s.duration_ms)}</td>
            <td class="text-right mono">${fmtInt(s.output_row_count)}</td>
          </tr>
        `).join('');
        latestRunCard = `
          <div class="card p-4 animate-fadein">
            <div class="flex items-center justify-between mb-3">
              <div>
                <div class="text-[14px] font-semibold text-g-800">Latest run</div>
                <div class="chip-mono">${esc(latest.run_id)}</div>
              </div>
              <a href="#/runs/${esc(latest.run_id)}" class="btn-secondary">View detail</a>
            </div>
            <table class="tbl">
              <thead><tr><th>Stage</th><th>Status</th><th class="text-right">Duration</th><th class="text-right">Rows</th></tr></thead>
              <tbody>${stages || '<tr><td colspan="4" class="text-g-400">no stage_complete events</td></tr>'}</tbody>
            </table>
          </div>
        `;
      }

      root.innerHTML = header + metrics + `
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
          <div class="lg:col-span-2">${latestRunCard}</div>
          <div>${graphStats}</div>
        </div>
      `;
    } catch (e) { setError(e); }
  }

  async function viewRuns() {
    setLoading();
    try {
      const runs = await api('/api/runs');
      const rows = runs.map(r => `
        <tr class="cursor-pointer" onclick="location.hash='#/runs/${esc(r.run_id)}'">
          <td class="mono">${esc(r.run_id.slice(0, 8))}…</td>
          <td>${statusBadge(r.status)}</td>
          <td class="mono">${esc(r.started_at || '—')}</td>
          <td class="text-right mono">${fmtMs(r.duration_ms)}</td>
          <td class="text-right mono">${fmtInt(r.stage_count)}</td>
          <td class="text-right">
            ${r.dq_total ? `<span class="badge ${r.dq_passed === r.dq_total ? 'badge-green' : 'badge-red'}">${r.dq_passed}/${r.dq_total}</span>` : '<span class="text-g-400">—</span>'}
          </td>
        </tr>
      `).join('');

      root.innerHTML = pageHeader('Pipeline Runs', `${runs.length} run(s) recorded in logs/`)
        + `<div class="card animate-fadein"><table class="tbl">
            <thead><tr>
              <th>Run ID</th><th>Status</th><th>Started</th>
              <th class="text-right">Duration</th><th class="text-right">Stages</th><th class="text-right">DQ</th>
            </tr></thead>
            <tbody>${rows || '<tr><td colspan="6" class="text-g-400">no runs</td></tr>'}</tbody>
          </table></div>`;
    } catch (e) { setError(e); }
  }

  async function viewRunDetail(runId) {
    setLoading();
    try {
      const r = await api(`/api/runs/${encodeURIComponent(runId)}`);
      const stages = (r.stages || []).map(s => `
        <tr>
          <td class="mono">${esc(s.stage)}</td>
          <td>${statusBadge(s.status)}</td>
          <td class="mono">${esc(s.output_table || '—')}</td>
          <td class="text-right mono">${fmtMs(s.duration_ms)}</td>
          <td class="text-right mono">${fmtInt(s.output_row_count)}</td>
        </tr>
      `).join('');

      const checks = (r.dq_checks || []).map(c => `
        <tr>
          <td class="mono">${esc(c.check_name)}</td>
          <td class="mono text-g-500">${esc(c.stage)}</td>
          <td>${c.passed ? '<span class="badge badge-green">PASS</span>' : '<span class="badge badge-red">FAIL</span>'}</td>
          <td class="mono">${esc(c.expected ?? '—')}</td>
          <td class="mono">${esc(c.actual ?? '—')}</td>
          <td class="text-right mono">${fmtInt(c.rows_failed)}</td>
        </tr>
      `).join('');

      const right = `
        <a href="#/runs" class="btn-secondary">← All runs</a>
        ${statusBadge(r.status)}
      `;

      root.innerHTML = pageHeader(`Run ${runId.slice(0, 8)}…`, r.started_at || '', right)
        + `<div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-4 animate-fadein">
            ${[
              ['Run ID', `<span class="chip-mono">${esc(r.run_id)}</span>`, ''],
              ['Stages', fmtInt(r.stage_count), 'completed'],
              ['Duration', fmtMs(r.duration_ms), 'pipeline_complete'],
            ].map(([l,v,s]) => `<div class="metric-card"><div class="label">${l}</div><div class="value" style="font-size:18px">${v}</div><div class="sub">${s}</div></div>`).join('')}
          </div>`
        + `<div class="card mb-4 animate-fadein">
            <div class="px-4 py-3 border-b border-g-100 text-[14px] font-semibold text-g-800">Stages</div>
            <table class="tbl">
              <thead><tr><th>Stage</th><th>Status</th><th>Output table</th><th class="text-right">Duration</th><th class="text-right">Rows</th></tr></thead>
              <tbody>${stages || '<tr><td colspan="5" class="text-g-400">no stages</td></tr>'}</tbody>
            </table>
          </div>`
        + `<div class="card animate-fadein">
            <div class="px-4 py-3 border-b border-g-100 text-[14px] font-semibold text-g-800 flex items-center justify-between">
              Data-quality checks
              <span class="text-[11px] text-g-500">${r.dq_passed}/${r.dq_total} passed</span>
            </div>
            <table class="tbl">
              <thead><tr><th>Check</th><th>Stage</th><th>Status</th><th>Expected</th><th>Actual</th><th class="text-right">Rows failed</th></tr></thead>
              <tbody>${checks || '<tr><td colspan="6" class="text-g-400">no DQ events</td></tr>'}</tbody>
            </table>
          </div>`;
    } catch (e) { setError(e); }
  }

  async function viewLineage() {
    setLoading();
    try {
      const data = await api('/api/lineage/graph');
      const datasets = data.nodes.filter(n => n.group === 'DataSet');

      const legend = `
        <div class="flex items-center gap-3 text-[11px] text-g-500">
          <span class="flex items-center gap-1"><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#0f766e"></span> L0 raw</span>
          <span class="flex items-center gap-1"><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#1d4ed8"></span> L1 staging</span>
          <span class="flex items-center gap-1"><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#6d28d9"></span> L2 facts</span>
          <span class="flex items-center gap-1"><span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:9999px;background:#0c1f3d"></span> Process</span>
        </div>
      `;

      root.innerHTML = pageHeader(
        'Lineage Explorer',
        `${data.nodes.length} nodes · ${data.edges.length} edges in tracex_graph`,
        legend
      ) + `
        <div class="grid grid-cols-1 lg:grid-cols-4 gap-4 animate-fadein">
          <div class="lg:col-span-3">
            <div id="lineage-canvas"></div>
            <div class="text-[11px] text-g-400 mt-2">Click any node to inspect upstream/downstream &rarr;</div>
          </div>
          <div>
            <div class="card p-4">
              <div class="text-[14px] font-semibold text-g-800 mb-3">DataSet</div>
              <div id="dataset-detail" class="text-[12px] text-g-500">Select a node on the graph.</div>
            </div>
          </div>
        </div>
      `;

      // Build vis-network
      const visNodes = new vis.DataSet(data.nodes.map(n => ({
        id: n.id,
        label: n.label,
        shape: n.shape,
        color: { background: n.color, border: n.color },
        font: { color: '#fff', face: 'DM Sans', size: 12 },
        margin: 8,
        widthConstraint: { maximum: 180 },
        ...(n.group === 'Process' ? { font: { color: '#fff', face: 'DM Mono', size: 11 } } : {}),
      })));
      const visEdges = new vis.DataSet(data.edges.map(e => ({
        from: e.from, to: e.to,
        arrows: 'to',
        color: { color: e.color, opacity: .6 },
        width: 1,
        dashes: !!e.dashes,
      })));

      const network = new vis.Network(
        document.getElementById('lineage-canvas'),
        { nodes: visNodes, edges: visEdges },
        {
          layout: {
            hierarchical: {
              enabled: true,
              direction: 'LR',
              sortMethod: 'directed',
              levelSeparation: 200,
              nodeSpacing: 120,
            },
          },
          physics: { enabled: false },
          interaction: { hover: true, dragNodes: true, zoomView: true },
        }
      );

      network.on('click', async (params) => {
        if (!params.nodes.length) return;
        const id = params.nodes[0];
        const node = data.nodes.find(n => n.id === id);
        if (!node || node.group !== 'DataSet') {
          document.getElementById('dataset-detail').innerHTML =
            `<div class="text-[12px] text-g-500">Process: <span class="mono text-g-800">${esc(node.label)}</span></div>`;
          return;
        }
        const res = await api(`/api/lineage/dataset/${encodeURIComponent(node.label)}`);
        const upstream = (res.upstream || []).map(u =>
          `<li class="flex justify-between py-1.5 border-b border-g-100"><span class="font-mono text-[11px] text-g-700">${esc(u.upstream_table)}</span><span class="badge badge-gray">${esc(u.via_process)}</span></li>`
        ).join('') || '<li class="text-g-400 text-[11px] py-1.5">none</li>';
        const downstream = (res.downstream || []).map(d =>
          `<li class="flex justify-between py-1.5 border-b border-g-100"><span class="font-mono text-[11px] text-g-700">${esc(d.downstream_table)}</span><span class="badge badge-gray">${esc(d.via_process)}</span></li>`
        ).join('') || '<li class="text-g-400 text-[11px] py-1.5">none</li>';

        document.getElementById('dataset-detail').innerHTML = `
          <div class="mb-3">
            <div class="font-mono text-[12px] text-g-800">${esc(node.label)}</div>
            <div class="mt-1">${layerBadge(node.layer)}</div>
            <div class="text-[11px] text-g-500 mt-1">row_count: ${fmtInt(node.row_count)}</div>
          </div>
          <div class="text-[10px] uppercase tracking-wider font-semibold text-g-400 mt-3 mb-1">Upstream</div>
          <ul>${upstream}</ul>
          <div class="text-[10px] uppercase tracking-wider font-semibold text-g-400 mt-3 mb-1">Downstream</div>
          <ul>${downstream}</ul>
        `;
      });

    } catch (e) { setError(e); }
  }

  async function viewDatasets() {
    setLoading();
    try {
      const rows = await api('/api/datasets');
      const tbody = rows.map(d => `
        <tr>
          <td class="mono">${esc(d.name)}</td>
          <td>${layerBadge(d.layer)}</td>
          <td class="text-right mono">${fmtInt(d.row_count)}</td>
          <td class="mono text-g-500">${esc(d.computed_at || '—')}</td>
          <td class="text-right">
            <a href="#/lineage" class="btn-secondary">View in graph</a>
          </td>
        </tr>
      `).join('');

      root.innerHTML = pageHeader('Datasets', `${rows.length} DataSet vertices in lineage graph`)
        + `<div class="card animate-fadein"><table class="tbl">
            <thead><tr><th>Name</th><th>Layer</th><th class="text-right">Row count</th><th>Computed at</th><th></th></tr></thead>
            <tbody>${tbody || '<tr><td colspan="5" class="text-g-400">no datasets</td></tr>'}</tbody>
          </table></div>`;
    } catch (e) { setError(e); }
  }

  async function viewDQ() {
    setLoading();
    try {
      const runs = await api('/api/runs');
      if (!runs.length) {
        root.innerHTML = pageHeader('DQ Console', 'No runs found.');
        return;
      }
      const latest = runs[0];
      const detail = await api(`/api/runs/${encodeURIComponent(latest.run_id)}`);
      const checks = (detail.dq_checks || []);
      const passed = checks.filter(c => c.passed).length;
      const failed = checks.length - passed;

      const tbody = checks.map(c => `
        <tr>
          <td class="mono">${esc(c.check_name)}</td>
          <td class="mono text-g-500">${esc(c.stage)}</td>
          <td>${c.passed ? '<span class="badge badge-green">PASS</span>' : '<span class="badge badge-red">FAIL</span>'}</td>
          <td class="mono">${esc(c.expected ?? '—')}</td>
          <td class="mono">${esc(c.actual ?? '—')}</td>
          <td class="text-right mono">${fmtInt(c.rows_failed)}</td>
        </tr>
      `).join('');

      const right = `<span class="chip-mono">${esc(latest.run_id.slice(0,8))}</span>`;
      const summary = `
        <div class="grid grid-cols-3 gap-4 mb-4 animate-fadein">
          <div class="metric-card"><div class="label">Total checks</div><div class="value">${fmtInt(checks.length)}</div><div class="sub">DQ events</div></div>
          <div class="metric-card"><div class="label">Passed</div><div class="value" style="color:#1a7f4b">${fmtInt(passed)}</div><div class="sub">${checks.length ? Math.round(100*passed/checks.length) : 0}% pass rate</div></div>
          <div class="metric-card"><div class="label">Failed</div><div class="value" style="color:${failed ? '#b91c1c' : '#9ca3af'}">${fmtInt(failed)}</div><div class="sub">${failed ? 'Investigate' : 'Clean'}</div></div>
        </div>
      `;

      root.innerHTML = pageHeader('DQ Console', 'Latest run · all data-quality checks', right)
        + summary
        + `<div class="card animate-fadein"><table class="tbl">
            <thead><tr><th>Check</th><th>Stage</th><th>Status</th><th>Expected</th><th>Actual</th><th class="text-right">Rows failed</th></tr></thead>
            <tbody>${tbody}</tbody>
          </table></div>`;
    } catch (e) { setError(e); }
  }

  async function viewSAR() {
    setLoading();
    try {
      const rows = await api('/api/sar');

      const tbody = rows.map(r => {
        const reasons = (r.flagging_reasons || []).map(x => `<span class="chip-mono">${esc(x)}</span>`).join(' ');
        const countries = (r.counterparty_countries || []).slice(0, 6).map(x => `<span class="chip-mono">${esc(x)}</span>`).join(' ');
        const more = (r.counterparty_countries || []).length > 6 ? `<span class="text-g-400 text-[10px]">+${r.counterparty_countries.length - 6}</span>` : '';
        return `
          <tr>
            <td class="mono text-g-700">${esc(r.customer_id)}</td>
            <td class="text-g-800">${esc(r.full_name)}</td>
            <td>${riskBadge(r.sar_priority)}</td>
            <td class="text-right mono">${esc(Number(r.risk_score).toFixed(4))}</td>
            <td class="text-right mono">${fmtUsd(r.total_suspicious_amount_usd)}</td>
            <td class="text-right mono">${fmtInt(r.suspicious_txn_count)}</td>
            <td>${reasons}</td>
            <td>${countries} ${more}</td>
            <td class="mono text-g-500">${esc(r.dominant_channel || '—')}</td>
            <td>${r.kyc_stale_flag ? '<span class="badge badge-amber">stale</span>' : '<span class="text-g-400 text-[11px]">—</span>'}</td>
            <td class="mono text-g-500">${esc(r.branch_region || '—')}</td>
          </tr>
        `;
      }).join('');

      const summary = (() => {
        const c = { CRITICAL: 0, HIGH: 0, MEDIUM: 0 };
        rows.forEach(r => { if (c[r.sar_priority] != null) c[r.sar_priority]++; });
        return `
          <div class="flex items-center gap-2 mb-4">
            <span class="badge risk-CRITICAL">${c.CRITICAL} CRITICAL</span>
            <span class="badge risk-HIGH">${c.HIGH} HIGH</span>
            <span class="badge risk-MEDIUM">${c.MEDIUM} MEDIUM</span>
          </div>
        `;
      })();

      root.innerHTML = pageHeader(
        'SAR Candidates',
        `${rows.length} customers from fct_regulatory_sar_candidates`,
        ''
      ) + summary + `
        <div class="card animate-fadein">
          <table class="tbl">
            <thead><tr>
              <th>Customer</th><th>Name</th><th>Priority</th>
              <th class="text-right">Risk</th>
              <th class="text-right">Suspicious USD</th>
              <th class="text-right">Intl txns</th>
              <th>Reasons</th>
              <th>Counterparty countries</th>
              <th>Channel</th>
              <th>KYC</th>
              <th>Region</th>
            </tr></thead>
            <tbody>${tbody || '<tr><td colspan="11" class="text-g-400">no candidates</td></tr>'}</tbody>
          </table>
        </div>
      `;
    } catch (e) { setError(e); }
  }

  // ---------- router ---------------------------------------------------

  const routes = [
    { match: /^#\/dashboard$|^#?$/,                 view: () => viewDashboard(),                    nav: 'dashboard' },
    { match: /^#\/runs$/,                           view: () => viewRuns(),                          nav: 'runs' },
    { match: /^#\/runs\/(.+)$/,                     view: (m) => viewRunDetail(decodeURIComponent(m[1])), nav: 'runs' },
    { match: /^#\/lineage$/,                        view: () => viewLineage(),                       nav: 'lineage' },
    { match: /^#\/datasets$/,                       view: () => viewDatasets(),                      nav: 'datasets' },
    { match: /^#\/dq$/,                             view: () => viewDQ(),                            nav: 'dq' },
    { match: /^#\/sar$/,                            view: () => viewSAR(),                           nav: 'sar' },
  ];

  function setActiveNav(name) {
    document.querySelectorAll('#sidebar-nav .nav-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.route === name);
    });
  }

  function dispatch() {
    const hash = window.location.hash || '#/dashboard';
    for (const r of routes) {
      const m = hash.match(r.match);
      if (m) {
        setActiveNav(r.nav);
        r.view(m);
        return;
      }
    }
    setError(new Error(`Unknown route: ${hash}`));
  }

  function bindNav() {
    document.querySelectorAll('#sidebar-nav .nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        location.hash = '#/' + btn.dataset.route;
      });
    });
  }

  function refresh() { dispatch(); }

  // Public surface
  return { dispatch, bindNav, refresh };
})();

window.addEventListener('hashchange', App.dispatch);
window.addEventListener('DOMContentLoaded', () => {
  App.bindNav();
  App.dispatch();
});
