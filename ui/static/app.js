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
        <div class="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6 animate-fadein">
          ${metricCard('Pipeline runs',     fmtInt(d.pipeline_runs_total),  'JSONL logs persisted')}
          ${metricCard('Datasets in graph', fmtInt(d.datasets_total),       `${fmtInt(d.columns_total)} columns · ${fmtInt(d.processes_total)} processes`)}
          ${metricCard('DQ checks',         d.dq_pass_rate,                 'Latest run gate', d.latest_run && d.latest_run.dq_passed === d.latest_run.dq_total ? '#1a7f4b' : '#b45309')}
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

      const legend = `
        <div class="flex items-center gap-3 text-[11px] text-g-500">
          <span class="flex items-center gap-1"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#0f766e"></span> L0 raw</span>
          <span class="flex items-center gap-1"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#1d4ed8"></span> L1 staging</span>
          <span class="flex items-center gap-1"><span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:#6d28d9"></span> L2 facts</span>
          <span class="flex items-center gap-1"><span style="display:inline-block;width:9px;height:9px;border-radius:9999px;background:#0c1f3d"></span> Process</span>
          <span class="flex items-center gap-1"><span style="display:inline-block;width:8px;height:8px;border-radius:9999px;background:#94a3b8"></span> Column</span>
        </div>
      `;

      root.innerHTML = pageHeader(
        'Lineage Explorer',
        `${data.nodes.length} nodes · ${data.edges.length} edges · click any DataSet to explode its columns; click any Column to trace its source chain`,
        legend
      ) + `
        <div class="grid grid-cols-1 lg:grid-cols-4 gap-4 animate-fadein">
          <div class="lg:col-span-3">
            <div class="card p-2 relative">
              <div class="absolute top-3 right-3 z-10 flex gap-1">
                <button id="ln-collapse-all" class="btn-secondary text-[10px] px-2 py-1" type="button" title="Collapse all column expansions">Collapse</button>
                <button id="ln-fit"          class="btn-secondary text-[10px] px-2 py-1" type="button" title="Re-fit graph to viewport">Fit</button>
                <button id="ln-stabilize"    class="btn-secondary text-[10px] px-2 py-1" type="button" title="Re-run physics stabilisation">Re-layout</button>
              </div>
              <div id="lineage-canvas" style="height:600px; background:#fafbfd; border-radius:8px;"></div>
            </div>
            <div class="text-[11px] text-g-400 mt-2">Drag any node to reposition · click a DataSet to add its columns to the graph · click a Column to add its upstream chain.</div>
          </div>
          <div>
            <div class="card p-4">
              <div class="text-[14px] font-semibold text-g-800 mb-3" id="ln-detail-title">Inspector</div>
              <div id="dataset-detail" class="text-[12px] text-g-500">Select a node on the graph.</div>
            </div>
          </div>
        </div>
      `;

      // ── Build vis-network ─────────────────────────────────────────────
      const visNodes = new vis.DataSet(data.nodes.map(n => ({
        id: n.id,
        label: n.label,
        shape: n.shape,
        color: { background: n.color, border: n.color, highlight: { background: n.color, border: '#0c1f3d' } },
        font: n.group === 'Process'
          ? { color: '#fff', face: 'DM Mono', size: 11 }
          : { color: '#fff', face: 'DM Sans', size: 12 },
        margin: 8,
        widthConstraint: { maximum: 180 },
        nodeKind: n.group,
        baseColor: n.color,
        layer: n.layer,
      })));
      const visEdges = new vis.DataSet(data.edges.map((e, i) => ({
        id: `base::${i}`,
        from: e.from, to: e.to,
        arrows: 'to',
        color: { color: e.color, opacity: .55 },
        width: 1,
        dashes: !!e.dashes,
      })));

      const canvas = document.getElementById('lineage-canvas');
      const network = new vis.Network(
        canvas,
        { nodes: visNodes, edges: visEdges },
        {
          layout: { hierarchical: { enabled: false } },
          physics: {
            enabled: true,
            solver: 'forceAtlas2Based',
            forceAtlas2Based: {
              gravitationalConstant: -55,
              centralGravity: 0.012,
              springLength: 130,
              springConstant: 0.07,
              damping: 0.65,
              avoidOverlap: 0.85,
            },
            stabilization: { iterations: 220, fit: true },
          },
          interaction: {
            hover: true, dragNodes: true, zoomView: true,
            navigationButtons: false, multiselect: false, hideEdgesOnDrag: true,
          },
          nodes: { borderWidth: 1.5 },
          edges: { smooth: { type: 'continuous', forceDirection: 'none' } },
        }
      );

      // After initial stabilisation, freeze physics so user-drag is sticky.
      // Re-enable temporarily whenever new column nodes are inserted so they
      // settle around the parent dataset, then freeze again.
      network.once('stabilizationIterationsDone', () => {
        network.setOptions({ physics: { enabled: false } });
      });

      const expandedDatasets = new Set();   // ds::<name>
      const expandedColumns  = new Set();   // col::<table>::<column>

      function nudgePhysics(ms = 1500) {
        network.setOptions({ physics: { enabled: true } });
        setTimeout(() => network.setOptions({ physics: { enabled: false } }), ms);
      }

      function colId(table, column) { return `col::${table}::${column}`; }

      function transformPalette(t) {
        const tt = (t || '').toUpperCase();
        return ({
          PASSTHROUGH: '#475569',
          RENAME:      '#475569',
          TRANSFORM:   '#1d4ed8',
          AGGREGATE:   '#6d28d9',
          WINDOW:      '#0f766e',
          CONSTANT:    '#94a3b8',
          AMBIGUOUS:   '#b45309',
        }[tt]) || '#64748b';
      }

      function addColumnNode(table, col, parentDsId) {
        const id = colId(table, col.column);
        if (visNodes.get(id)) return id;
        const c = transformPalette(col.transform_type);
        let baseX = 0, baseY = 0;
        const parentPos = network.getPositions([parentDsId])[parentDsId];
        if (parentPos) { baseX = parentPos.x; baseY = parentPos.y; }
        visNodes.add({
          id,
          label: col.column,
          shape: 'box',
          color: { background: c, border: c, highlight: { background: c, border: '#0c1f3d' } },
          font: { color: '#fff', face: 'DM Mono', size: 10 },
          margin: 5,
          widthConstraint: { maximum: 140 },
          nodeKind: 'Column',
          baseColor: c,
          // small jitter so they fan out instead of stacking on the parent
          x: baseX + (Math.random() - 0.5) * 60,
          y: baseY + 80 + (Math.random() - 0.5) * 80,
        });
        visEdges.add({
          id: `owns::${id}`,
          from: parentDsId, to: id,
          color: { color: '#cbd5e1', opacity: .65 },
          dashes: [2, 4],
          arrows: '',
          width: 1,
          smooth: false,
        });
        return id;
      }

      function removeColumnNode(table, column) {
        const id = colId(table, column);
        // Drop every edge that touches this column, then the node itself.
        const linked = visEdges.get({
          filter: e => e.from === id || e.to === id,
        });
        visEdges.remove(linked.map(e => e.id));
        if (visNodes.get(id)) visNodes.remove(id);
      }

      // ── Dataset expansion ────────────────────────────────────────────
      async function toggleDatasetColumns(dsId, datasetLabel) {
        if (expandedDatasets.has(dsId)) {
          // collapse
          const cols = await api(`/api/lineage/dataset/${encodeURIComponent(datasetLabel)}/columns`);
          (cols.columns || []).forEach(c => {
            // also collapse any expanded chain rooted at this column
            collapseColumnChain(datasetLabel, c.column, /*alsoRemoveColumnNode*/ true);
          });
          expandedDatasets.delete(dsId);
          return;
        }
        const cols = await api(`/api/lineage/dataset/${encodeURIComponent(datasetLabel)}/columns`);
        (cols.columns || []).forEach(col => addColumnNode(datasetLabel, col, dsId));
        expandedDatasets.add(dsId);
        nudgePhysics(2000);
      }

      // ── Column upstream-chain expansion ──────────────────────────────
      async function toggleColumnChain(table, column) {
        const myId = colId(table, column);
        if (expandedColumns.has(myId)) {
          collapseColumnChain(table, column, /*alsoRemoveColumnNode*/ false);
          return;
        }
        const detail = await api(`/api/lineage/column/${encodeURIComponent(table)}/${encodeURIComponent(column)}`);
        const chain = detail.upstream_chain || [];

        // Add direct (hop=1) sources as column nodes; for deeper hops, add the
        // node and an edge from its hop-1 predecessor (best-effort: link to the
        // nearest already-present node by name).
        chain.forEach(h => {
          const srcId = colId(h.source_table, h.source_column);
          if (!visNodes.get(srcId)) {
            const c = transformPalette(h.transform_type);
            const myPos = network.getPositions([myId])[myId] || { x: 0, y: 0 };
            visNodes.add({
              id: srcId,
              label: `${h.source_column}\n${h.source_table}`,
              shape: 'box',
              color: { background: c, border: c, highlight: { background: c, border: '#0c1f3d' } },
              font: { color: '#fff', face: 'DM Mono', size: 9, multi: true },
              margin: 5,
              widthConstraint: { maximum: 150 },
              nodeKind: 'ColumnUpstream',
              ownerTable: h.source_table,
              ownerColumn: h.source_column,
              addedFromColumn: myId,
              x: myPos.x - 140 - (h.hop - 1) * 110 + (Math.random() - 0.5) * 40,
              y: myPos.y + (Math.random() - 0.5) * 120,
            });
          }
          // Edge from current column to its hop-1 source. For hops > 1, the
          // chain query gave us a flat list; we wire the edge based on the
          // shortest-path-like "this hop comes after that earlier hop"
          // heuristic: join hop n to any hop n-1 we already inserted.
          const edgeId = `chain::${myId}::${srcId}::${h.hop}`;
          if (!visEdges.get(edgeId)) {
            const tgtId = h.hop === 1
              ? myId
              : (chain.find(p => p.hop === h.hop - 1
                                  && visNodes.get(colId(p.source_table, p.source_column)))
                  ? colId(chain.find(p => p.hop === h.hop - 1).source_table,
                          chain.find(p => p.hop === h.hop - 1).source_column)
                  : myId);
            visEdges.add({
              id: edgeId,
              from: tgtId, to: srcId,
              arrows: 'to',
              color: { color: '#94a3b8', opacity: .7 },
              width: 1,
              dashes: false,
              smooth: { type: 'continuous' },
              chainOf: myId,
            });
          }
        });
        expandedColumns.add(myId);
        nudgePhysics(2000);
      }

      function collapseColumnChain(table, column, alsoRemoveColumnNode) {
        const myId = colId(table, column);
        // Remove every chain edge anchored on this column.
        const chainEdges = visEdges.get({ filter: e => e.chainOf === myId });
        const dropNodes = new Set();
        chainEdges.forEach(e => { dropNodes.add(e.to); });
        visEdges.remove(chainEdges.map(e => e.id));

        // Drop nodes that were added BY this column's chain expansion AND have
        // no other inbound edges still keeping them alive.
        dropNodes.forEach(id => {
          const node = visNodes.get(id);
          if (!node || node.nodeKind !== 'ColumnUpstream') return;
          if (node.addedFromColumn !== myId) return;
          // safety: ensure no edges still reference this node
          const stillLinked = visEdges.get({ filter: e => e.from === id || e.to === id });
          if (stillLinked.length === 0) {
            visNodes.remove(id);
          }
        });

        expandedColumns.delete(myId);

        if (alsoRemoveColumnNode) {
          // also tear down this column itself if dataset is collapsing
          removeColumnNode(table, column);
        }
      }

      // ── Right-panel inspector ────────────────────────────────────────
      const detailEl = document.getElementById('dataset-detail');
      const titleEl  = document.getElementById('ln-detail-title');

      async function showDataset(node) {
        titleEl.textContent = 'DataSet';
        renderDatasetDetail(node, detailEl);
      }

      async function showColumn(table, column) {
        titleEl.textContent = 'Column';
        detailEl.innerHTML = `<div class="text-[11px] text-g-400">Loading ${esc(table)}.${esc(column)}…</div>`;
        try {
          const detail = await api(`/api/lineage/column/${encodeURIComponent(table)}/${encodeURIComponent(column)}`);
          detailEl.innerHTML = `
            <div class="mb-2">
              <div class="font-mono text-[12px] text-g-800">${esc(table)}.${esc(column)}</div>
              <div class="mt-1 flex items-center gap-1">${transformBadge(detail.transform_type)}${confidenceBadge(detail.confidence)}</div>
            </div>
            ${renderColumnDefinition(detail)}`;
        } catch (e) {
          detailEl.innerHTML = `<div class="text-[12px] text-red-700">${esc(String(e))}</div>`;
        }
      }

      function showProcess(node) {
        titleEl.textContent = 'Process';
        detailEl.innerHTML = `<div class="text-[12px] text-g-700"><span class="mono">${esc(node.label)}</span></div>`;
      }

      // ── Click router ──────────────────────────────────────────────────
      network.on('click', async (params) => {
        if (!params.nodes.length) return;
        const id = params.nodes[0];
        const node = visNodes.get(id);
        if (!node) return;

        if (node.nodeKind === 'DataSet') {
          await showDataset({ label: node.label, layer: node.layer, row_count: 0 });
          await toggleDatasetColumns(id, node.label);
          ChatPanel.suggestDataset(node.label);
        } else if (node.nodeKind === 'Column' || node.nodeKind === 'ColumnUpstream') {
          // recover (table, column) — Column nodes have label = column,
          // and we stashed the parent dataset via the owns:: edge target.
          let table, column;
          if (node.nodeKind === 'Column') {
            // owns edge: from dsNode TO this column
            const owns = visEdges.get({ filter: e => e.id === `owns::${id}` })[0];
            const dsNode = owns ? visNodes.get(owns.from) : null;
            table = dsNode ? dsNode.label : (id.split('::')[1] || '');
            column = node.label;
          } else {
            table = node.ownerTable;
            column = node.ownerColumn;
          }
          await showColumn(table, column);
          await toggleColumnChain(table, column);
        } else {
          showProcess(node);
        }
      });

      // ── Toolbar buttons ───────────────────────────────────────────────
      document.getElementById('ln-fit').addEventListener('click', () => {
        network.fit({ animation: { duration: 350, easingFunction: 'easeInOutQuad' } });
      });
      document.getElementById('ln-stabilize').addEventListener('click', () => {
        network.setOptions({ physics: { enabled: true } });
        network.stabilize(150);
        setTimeout(() => network.setOptions({ physics: { enabled: false } }), 1800);
      });
      document.getElementById('ln-collapse-all').addEventListener('click', () => {
        // Drop every Column / ColumnUpstream node we ever added
        const toRemove = visNodes.get({
          filter: n => n.nodeKind === 'Column' || n.nodeKind === 'ColumnUpstream',
        });
        const toRemoveIds = new Set(toRemove.map(n => n.id));
        const linkedEdges = visEdges.get({
          filter: e => toRemoveIds.has(e.from) || toRemoveIds.has(e.to),
        });
        visEdges.remove(linkedEdges.map(e => e.id));
        visNodes.remove(toRemove.map(n => n.id));
        expandedDatasets.clear();
        expandedColumns.clear();
      });

    } catch (e) { setError(e); }
  }

  // Render the Lineage Explorer side panel for a clicked DataSet node.
  // Loads upstream/downstream + columns in parallel; each column row is
  // click-to-expand into full schema + definition + first upstream hops.
  async function renderDatasetDetail(node, container) {
    container.innerHTML = `<div class="text-[12px] text-g-500">Loading ${esc(node.label)}…</div>`;
    try {
      const [neighbors, cols] = await Promise.all([
        api(`/api/lineage/dataset/${encodeURIComponent(node.label)}`),
        api(`/api/lineage/dataset/${encodeURIComponent(node.label)}/columns`),
      ]);

      const upstream = (neighbors.upstream || []).map(u =>
        `<li class="flex justify-between py-1.5 border-b border-g-100"><span class="font-mono text-[11px] text-g-700">${esc(u.upstream_table)}</span><span class="badge badge-gray">${esc(u.via_process)}</span></li>`
      ).join('') || '<li class="text-g-400 text-[11px] py-1.5">none</li>';
      const downstream = (neighbors.downstream || []).map(d =>
        `<li class="flex justify-between py-1.5 border-b border-g-100"><span class="font-mono text-[11px] text-g-700">${esc(d.downstream_table)}</span><span class="badge badge-gray">${esc(d.via_process)}</span></li>`
      ).join('') || '<li class="text-g-400 text-[11px] py-1.5">none</li>';

      const columnsHtml = renderColumnList(node.label, cols.columns || []);

      container.innerHTML = `
        <div class="mb-3">
          <div class="font-mono text-[12px] text-g-800">${esc(node.label)}</div>
          <div class="mt-1">${layerBadge(node.layer)}</div>
          <div class="text-[11px] text-g-500 mt-1">row_count: ${fmtInt(node.row_count)}</div>
        </div>
        <div class="text-[10px] uppercase tracking-wider font-semibold text-g-400 mt-3 mb-1">Columns (${(cols.columns || []).length})</div>
        ${columnsHtml}
        <div class="text-[10px] uppercase tracking-wider font-semibold text-g-400 mt-4 mb-1">Upstream tables</div>
        <ul>${upstream}</ul>
        <div class="text-[10px] uppercase tracking-wider font-semibold text-g-400 mt-3 mb-1">Downstream tables</div>
        <ul>${downstream}</ul>
      `;

      attachColumnHandlers(container, node.label);
    } catch (e) {
      container.innerHTML = `<div class="text-[12px] text-red-700">${esc(String(e))}</div>`;
    }
  }

  // Compact, click-to-expand row per column. Wires .col-row click to
  // attachColumnHandlers below.
  function renderColumnList(table, columns) {
    if (!columns.length) {
      return '<div class="text-[11px] text-g-400 py-2">No column nodes ingested yet for this dataset.</div>';
    }
    return `<ul class="border border-g-100 rounded-md overflow-hidden">${
      columns.map((c, i) => `
        <li class="col-row border-b border-g-100 last:border-b-0" data-table="${esc(table)}" data-column="${esc(c.column)}" data-idx="${i}">
          <button type="button" class="col-row-head w-full text-left px-2.5 py-1.5 hover:bg-g-50 flex items-center justify-between gap-2">
            <span class="font-mono text-[11px] text-g-800 truncate">${esc(c.column)}</span>
            <span class="flex items-center gap-1.5 shrink-0">
              ${transformBadge(c.transform_type)}
              ${confidenceBadge(c.confidence)}
              <span class="text-[10px] text-g-400">${esc(c.data_type || '')}</span>
            </span>
          </button>
          <div class="col-row-body hidden px-3 pb-3 pt-1 bg-g-50/40"></div>
        </li>
      `).join('')
    }</ul>`;
  }

  function attachColumnHandlers(scope, table) {
    scope.querySelectorAll('.col-row').forEach(li => {
      const head = li.querySelector('.col-row-head');
      const body = li.querySelector('.col-row-body');
      head.addEventListener('click', async () => {
        if (!body.classList.contains('hidden')) {
          body.classList.add('hidden');
          body.innerHTML = '';
          return;
        }
        body.classList.remove('hidden');
        body.innerHTML = '<div class="text-[11px] text-g-400">Loading…</div>';
        const col = li.dataset.column;
        try {
          const detail = await api(`/api/lineage/column/${encodeURIComponent(table)}/${encodeURIComponent(col)}`);
          body.innerHTML = renderColumnDefinition(detail);
        } catch (e) {
          body.innerHTML = `<div class="text-[11px] text-red-700">${esc(String(e))}</div>`;
        }
      });
    });
  }

  // Full per-column inspector: schema fields, definition (expression),
  // semantic description if present, and the first 6 upstream hops.
  function renderColumnDefinition(d) {
    const sd = (d.semantic_description || '').trim();
    const expr = (d.expression || d.derivation || '').trim();
    const chain = (d.upstream_chain || []).slice(0, 8).map(h =>
      `<li class="py-1 border-b border-g-100 last:border-b-0">
        <div class="flex items-center justify-between">
          <span class="font-mono text-[10px] text-g-700">${esc(h.source_table)}.${esc(h.source_column)}</span>
          <span class="flex items-center gap-1">${transformBadge(h.transform_type)}<span class="text-[10px] text-g-400">hop ${h.hop}</span></span>
        </div>
      </li>`
    ).join('');
    const more = (d.upstream_chain || []).length > 8
      ? `<div class="text-[10px] text-g-400 mt-1">+${d.upstream_chain.length - 8} more</div>` : '';

    return `
      <div class="space-y-2">
        ${sd ? `<div class="text-[12px] text-g-800 leading-snug">${esc(sd)}</div>` : `<div class="text-[11px] text-g-400 italic">No semantic description (run with TRACEX_LINEAGE_AGENTS=on to enrich)</div>`}
        <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
          <div><span class="text-g-400 uppercase tracking-wider">Type</span> <span class="mono text-g-700">${esc(d.transform_type || '—')}</span></div>
          <div><span class="text-g-400 uppercase tracking-wider">Confidence</span> <span class="mono text-g-700">${d.confidence == null ? '—' : Number(d.confidence).toFixed(2)}</span></div>
          <div><span class="text-g-400 uppercase tracking-wider">Data type</span> <span class="mono text-g-700">${esc(d.data_type || '—')}</span></div>
          <div><span class="text-g-400 uppercase tracking-wider">SQL hash</span> <span class="mono text-g-500">${esc((d.sql_hash || '').slice(0,12) || '—')}</span></div>
        </div>
        ${expr ? `
          <div>
            <div class="text-[10px] uppercase tracking-wider text-g-400 mb-1">Definition</div>
            <pre class="mono text-[10.5px] text-g-700 bg-white border border-g-100 rounded p-2 whitespace-pre-wrap break-words">${esc(expr)}</pre>
          </div>` : ''}
        ${chain ? `
          <div>
            <div class="text-[10px] uppercase tracking-wider text-g-400 mt-2 mb-1">Upstream chain</div>
            <ul>${chain}</ul>
            ${more}
          </div>` : '<div class="text-[11px] text-g-400 italic">No upstream — source-table column.</div>'}
      </div>`;
  }

  function transformBadge(t) {
    const tt = (t || '').toUpperCase();
    const palette = {
      PASSTHROUGH: '#475569',
      RENAME:      '#475569',
      TRANSFORM:   '#1d4ed8',
      AGGREGATE:   '#6d28d9',
      WINDOW:      '#0f766e',
      CONSTANT:    '#94a3b8',
      AMBIGUOUS:   '#b45309',
    };
    if (!tt) return '<span class="text-[10px] text-g-400">—</span>';
    const c = palette[tt] || '#475569';
    return `<span style="display:inline-block;font-family:'DM Mono',monospace;font-size:9.5px;letter-spacing:.02em;background:${c};color:#fff;padding:1px 5px;border-radius:3px;">${esc(tt)}</span>`;
  }

  function confidenceBadge(v) {
    if (v == null) return '';
    const n = Number(v);
    let bg = '#1a7f4b'; let label = n.toFixed(2);
    if (n < 0.6) bg = '#b45309';
    else if (n < 0.95) bg = '#1d4ed8';
    return `<span style="display:inline-block;font-family:'DM Mono',monospace;font-size:9.5px;background:${bg};color:#fff;padding:1px 5px;border-radius:3px;">conf ${esc(label)}</span>`;
  }

  async function viewDatasets() {
    setLoading();
    try {
      const rows = await api('/api/datasets');
      const tbody = rows.map(d => `
        <tr class="ds-row cursor-pointer hover:bg-g-50" data-name="${esc(d.name)}" data-layer="${esc(d.layer || '')}" data-row-count="${esc(String(d.row_count ?? 0))}">
          <td class="mono"><span class="ds-caret inline-block w-3 text-g-400">▸</span> ${esc(d.name)}</td>
          <td>${layerBadge(d.layer)}</td>
          <td class="text-right mono">${fmtInt(d.row_count)}</td>
          <td class="mono text-g-500">${esc(d.computed_at || '—')}</td>
          <td class="text-right">
            <a href="#/lineage" class="btn-secondary" onclick="event.stopPropagation()">View in graph</a>
          </td>
        </tr>
        <tr class="ds-body hidden" data-for="${esc(d.name)}">
          <td colspan="5" class="bg-g-50/40 p-3"><div class="ds-body-content text-[12px] text-g-500">Click to expand columns…</div></td>
        </tr>
      `).join('');

      root.innerHTML = pageHeader('Datasets', `${rows.length} DataSet vertices · click any row to expand columns`)
        + `<div class="card animate-fadein"><table class="tbl">
            <thead><tr><th>Name</th><th>Layer</th><th class="text-right">Row count</th><th>Computed at</th><th></th></tr></thead>
            <tbody>${tbody || '<tr><td colspan="5" class="text-g-400">no datasets</td></tr>'}</tbody>
          </table></div>`;

      root.querySelectorAll('.ds-row').forEach(tr => {
        tr.addEventListener('click', async () => {
          const name = tr.dataset.name;
          const body = root.querySelector(`.ds-body[data-for="${cssEscape(name)}"]`);
          const caret = tr.querySelector('.ds-caret');
          if (!body) return;
          if (!body.classList.contains('hidden')) {
            body.classList.add('hidden');
            if (caret) caret.textContent = '▸';
            return;
          }
          body.classList.remove('hidden');
          if (caret) caret.textContent = '▾';
          const content = body.querySelector('.ds-body-content');
          content.innerHTML = '<div class="text-[11px] text-g-400">Loading…</div>';
          try {
            const cols = await api(`/api/lineage/dataset/${encodeURIComponent(name)}/columns`);
            content.innerHTML = renderColumnList(name, cols.columns || []);
            attachColumnHandlers(content, name);
          } catch (e) {
            content.innerHTML = `<div class="text-[11px] text-red-700">${esc(String(e))}</div>`;
          }
        });
      });
    } catch (e) { setError(e); }
  }

  function cssEscape(s) {
    if (window.CSS && window.CSS.escape) return window.CSS.escape(s);
    return String(s).replace(/"/g, '\\"');
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

  // ---------- router ---------------------------------------------------

  const routes = [
    { match: /^#\/dashboard$|^#?$/,                 view: () => viewDashboard(),                    nav: 'dashboard' },
    { match: /^#\/runs$/,                           view: () => viewRuns(),                          nav: 'runs' },
    { match: /^#\/runs\/(.+)$/,                     view: (m) => viewRunDetail(decodeURIComponent(m[1])), nav: 'runs' },
    { match: /^#\/lineage$/,                        view: () => viewLineage(),                       nav: 'lineage' },
    { match: /^#\/datasets$/,                       view: () => viewDatasets(),                      nav: 'datasets' },
    { match: /^#\/dq$/,                             view: () => viewDQ(),                            nav: 'dq' },
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

// =====================================================================
// Chat Panel — slide-in drawer wired to /api/chat
// =====================================================================
const ChatPanel = (() => {
  let conversationId = null;
  let isOpen = false;
  let activeThoughts = [];   // currently-rendered thinking bubbles, in order

  function drawer()   { return document.getElementById('chat-drawer'); }
  function messages() { return document.getElementById('chat-messages'); }
  function input()    { return document.getElementById('chat-input'); }
  function sendBtn()  { return document.querySelector('.chat-send-btn'); }

  function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])
    );
  }

  function toggle() {
    isOpen = !isOpen;
    const d = drawer();
    if (!d) return;
    d.classList.toggle('open',   isOpen);
    d.classList.toggle('closed', !isOpen);
    if (isOpen) setTimeout(() => input()?.focus(), 280);
  }

  // Render assistant message: highlight `table.column` patterns as chips,
  // convert **bold** to <strong>, convert newlines to <br>, basic numbered lists.
  function renderAssistant(text) {
    let s = escHtml(text);
    // **bold**
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // `table.column` or `column_name` → teal chip
    s = s.replace(/`([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)`/gi,
      '<span class="col-ref">$1</span>');
    // newlines
    s = s.replace(/\n/g, '<br>');
    return s;
  }

  function appendMessage(text, role) {
    const el = document.createElement('div');
    el.className = `chat-msg chat-msg-${role}`;
    if (role === 'assistant') {
      el.innerHTML = renderAssistant(text);
    } else {
      el.textContent = text;
    }
    messages().appendChild(el);
    scrollBottom();
    return el;
  }

  function appendThinking() {
    const el = document.createElement('div');
    el.className = 'chat-msg-thinking';
    el.textContent = 'Searching lineage graph…';
    messages().appendChild(el);
    scrollBottom();
    return el;
  }

  // Append a new green pulsating "thinking" bubble. Fade any prior bubbles
  // in this turn to gray. Tracks them so we can vanish all at the end.
  function appendThought(text) {
    // Fade all prior active bubbles
    activeThoughts.forEach(el => {
      el.classList.remove('chat-thought-active');
      el.classList.add('chat-thought-fade');
    });
    const el = document.createElement('div');
    el.className = 'chat-thought chat-thought-active';
    el.innerHTML = `<span class="chat-thought-dot"></span><span class="chat-thought-text"></span>`;
    el.querySelector('.chat-thought-text').textContent = text;
    messages().appendChild(el);
    activeThoughts.push(el);
    scrollBottom();
  }

  // Fade out and remove every thinking bubble accumulated this turn.
  function vanishThoughts() {
    const toRemove = activeThoughts.slice();
    activeThoughts = [];
    toRemove.forEach(el => {
      el.classList.add('chat-thought-vanish');
      // delay matches the CSS transition (.35s)
      setTimeout(() => el.remove(), 380);
    });
  }

  function scrollBottom() {
    const m = messages();
    if (m) m.scrollTop = m.scrollHeight;
  }

  async function sendText(text) {
    const inp = input();
    if (inp) inp.value = text;
    await send();
  }

  async function send() {
    const inp = input();
    const btn = sendBtn();
    const text = (inp?.value || '').trim();
    if (!text) return;
    inp.value = '';
    inp.disabled = true;
    if (btn) btn.disabled = true;

    appendMessage(text, 'user');
    appendThought('Thinking…');

    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      });
      if (!res.ok || !res.body) {
        throw new Error(`HTTP ${res.status}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let finalShown = false;

      // Read NDJSON: one JSON object per line
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buffer.indexOf('\n')) >= 0) {
          const raw = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (!raw) continue;
          let evt;
          try { evt = JSON.parse(raw); } catch { continue; }

          if (evt.type === 'thinking') {
            appendThought(evt.text || '…');
          } else if (evt.type === 'final') {
            conversationId = evt.conversation_id || conversationId;
            vanishThoughts();
            appendMessage(evt.response || '(empty response)', 'assistant');
            finalShown = true;
          } else if (evt.type === 'error') {
            vanishThoughts();
            appendMessage(`Error: ${evt.message || 'unknown'}`, 'assistant');
            finalShown = true;
          }
        }
      }
      if (!finalShown) {
        vanishThoughts();
        appendMessage('(stream ended without a final response)', 'assistant');
      }
    } catch (err) {
      vanishThoughts();
      appendMessage(`Error: ${err.message}`, 'assistant');
    } finally {
      inp.disabled = false;
      if (btn) btn.disabled = false;
      inp.focus();
    }
  }

  // Pre-populate the chat from the Lineage Explorer when a DataSet node is
  // clicked. Opens the drawer if closed, fills the input but does NOT send.
  function suggestDataset(datasetName) {
    if (!isOpen) toggle();
    const inp = input();
    if (!inp) return;
    inp.value = `Tell me about the ${datasetName} table`;
    inp.focus();
    inp.setSelectionRange(inp.value.length, inp.value.length);
  }

  return { toggle, send, sendText, suggestDataset };
})();

window.addEventListener('hashchange', App.dispatch);
window.addEventListener('DOMContentLoaded', () => {
  App.bindNav();
  App.dispatch();
});
