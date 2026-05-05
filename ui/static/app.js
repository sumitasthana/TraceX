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

      function sourceBorder(source) {
        const palette = {
          catalog:        '#1a7f4b',
          sqlglot:        '#1d4ed8',
          agent_inferred: '#b45309',
          unresolved:     '#9ca3af',
        };
        return palette[String(source || 'unresolved').toLowerCase()] || '#9ca3af';
      }

      function addColumnNode(table, col, parentDsId) {
        const id = colId(table, col.column);
        if (visNodes.get(id)) return id;
        const c = transformPalette(col.transform_type);
        const border = sourceBorder(col.source);
        let baseX = 0, baseY = 0;
        const parentPos = network.getPositions([parentDsId])[parentDsId];
        if (parentPos) { baseX = parentPos.x; baseY = parentPos.y; }
        visNodes.add({
          id,
          label: col.column,
          shape: 'box',
          color: { background: c, border: border, highlight: { background: c, border: '#0c1f3d' } },
          borderWidth: 3,           // visible source-provenance ring
          borderWidthSelected: 4,
          font: { color: '#fff', face: 'DM Mono', size: 10 },
          margin: 5,
          widthConstraint: { maximum: 140 },
          nodeKind: 'Column',
          baseColor: c,
          source: col.source || 'unresolved',
          review_state: col.review_state || 'pending_review',
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
          // (Discover navigation hook intentionally not auto-fired here —
          //  use the toolbar to open Discover with a question instead.)
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
            <span class="flex items-center gap-1.5 shrink-0 flex-wrap">
              ${transformBadge(c.transform_type)}
              ${sourceBadge(c.source)}
              ${reviewStateBadge(c.review_state)}
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
        <div class="flex items-center gap-1.5 flex-wrap">
          ${sourceBadge(d.source)}
          ${reviewStateBadge(d.review_state)}
        </div>
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

  function sourceBadge(source) {
    const palette = {
      catalog:        { bg: '#1a7f4b', label: 'CATALOG' },
      sqlglot:        { bg: '#1d4ed8', label: 'SQLGLOT' },
      agent_inferred: { bg: '#b45309', label: 'AGENT' },
      unresolved:     { bg: '#6b7280', label: 'UNRESOLVED' },
    };
    const key = String(source || 'unresolved').toLowerCase();
    const meta = palette[key] || palette.unresolved;
    return `<span style="display:inline-block;font-family:'DM Mono',monospace;font-size:9.5px;letter-spacing:.04em;background:${meta.bg};color:#fff;padding:1px 5px;border-radius:3px;">${meta.label}</span>`;
  }

  function reviewStateBadge(state) {
    const s = String(state || '').toLowerCase();
    if (s === 'ratified') {
      return `<span style="display:inline-block;font-family:'DM Mono',monospace;font-size:9.5px;background:#e6f5ee;color:#1a7f4b;padding:1px 6px;border-radius:9999px;">✓ ratified</span>`;
    }
    if (s === 'pending_review') {
      return `<span style="display:inline-block;font-family:'DM Mono',monospace;font-size:9.5px;background:#fef3cd;color:#b45309;padding:1px 6px;border-radius:9999px;">⏱ pending</span>`;
    }
    return '';
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

  // ---------- Catalog view ---------------------------------------------

  function profileBadge(p) {
    const palette = {
      P1: { bg: '#fde8e8', fg: '#b91c1c' },
      P2: { bg: '#fef3cd', fg: '#b45309' },
      P3: { bg: '#eff4ff', fg: '#1d4ed8' },
    };
    const m = palette[p] || palette.P3;
    return `<span style="display:inline-block;font-family:'DM Mono',monospace;font-size:10px;font-weight:600;padding:2px 8px;border-radius:9999px;background:${m.bg};color:${m.fg};">${esc(p || '?')}</span>`;
  }

  async function viewCatalog() {
    setLoading();
    try {
      const [status, certs, pending, activity, health] = await Promise.all([
        api('/api/catalog/status'),
        api('/api/catalog/certifications'),
        api('/api/catalog/pending'),
        api('/api/catalog/activity?limit=20'),
        api('/api/catalog/health'),
      ]);

      const disabled = !health.enabled;
      const banner = disabled
        ? `<div class="card p-3 mb-4 animate-fadein" style="background:#fef3cd;border-color:#b45309;color:#b45309;font-size:12px"><strong>Catalog disabled</strong> — set <code class="mono">TRACEX_CATALOG=on</code> and re-run the pipeline to capture entries.</div>`
        : '';

      const metricCard = (label, value, sub, color = '#1f2937') => `
        <div class="metric-card">
          <div class="label">${esc(label)}</div>
          <div class="value" style="color:${color}">${value}</div>
          <div class="sub">${esc(sub)}</div>
        </div>`;

      const hitRatePct = ((status.hit_rate || 0) * 100).toFixed(1) + '%';
      const metrics = `
        <div class="grid grid-cols-2 md:grid-cols-3 gap-4 mb-6 animate-fadein">
          ${metricCard('Catalog hit rate', hitRatePct, 'From most recent pipeline run',
                       (status.hit_rate || 0) > 0 ? '#1a7f4b' : '#6b7280')}
          ${metricCard('Pending reviews', fmtInt(status.pending_count || 0),
                       'AI-inferred edges awaiting steward review',
                       (status.pending_count || 0) > 0 ? '#b45309' : '#6b7280')}
          ${metricCard('Ratified entries', fmtInt(status.ratified_count || 0),
                       `${fmtInt(status.certifications_count || 0)} certifications`,
                       '#1a7f4b')}
        </div>`;

      const certsTbody = (certs.items || []).map(c => `
        <tr class="cert-row cursor-pointer" data-name="${esc(c.table_name)}">
          <td class="mono">${esc(c.table_name)}</td>
          <td>${profileBadge(c.profile)}</td>
          <td class="mono text-g-500">${esc(c.certified_by || '—')}</td>
          <td class="mono text-g-500">${esc((c.certified_at || '').slice(0, 19).replace('T', ' '))}</td>
          <td class="text-g-600">${esc(c.notes || '')}</td>
        </tr>`).join('');
      const certsHtml = `
        <div class="card animate-fadein mb-6">
          <div class="px-4 py-3 border-b border-g-100 flex items-center justify-between">
            <h2 class="text-[14px] font-semibold text-g-800">Certifications</h2>
            <span class="text-[11px] text-g-500">${(certs.items || []).length} table(s)</span>
          </div>
          <table class="tbl">
            <thead><tr><th>Table</th><th>Profile</th><th>Certified by</th><th>Certified at</th><th>Notes</th></tr></thead>
            <tbody>${certsTbody || '<tr><td colspan="5" class="text-g-400">No certifications. Run <code class="mono">python cli.py catalog seed</code>.</td></tr>'}</tbody>
          </table>
        </div>`;

      const pendingItems = pending.items || [];
      const pendingTbody = pendingItems.map(p => {
        const srcText = (p.sources || []).map(s =>
          `<span class="chip-mono">${esc(s.source_table)}.${esc(s.source_column)}</span>`
        ).join(' ') || '<span class="text-g-400 text-[10px]">unresolved</span>';
        return `
          <tr>
            <td class="mono">${esc(p.target_table)}.${esc(p.target_column)}</td>
            <td>${srcText}</td>
            <td class="text-right mono">${p.confidence == null ? '—' : Number(p.confidence).toFixed(2)}</td>
            <td class="mono text-g-500">${esc((p.awaiting_since || '').slice(0,19).replace('T', ' '))}</td>
            <td class="text-right">
              <button type="button" class="btn-secondary cat-action" data-action="ratify" data-table="${esc(p.target_table)}" data-column="${esc(p.target_column)}" style="background:#e6f5ee;color:#1a7f4b;border-color:#1a7f4b;margin-right:4px">Ratify</button>
              <button type="button" class="btn-secondary cat-action" data-action="reject" data-table="${esc(p.target_table)}" data-column="${esc(p.target_column)}" style="background:#fde8e8;color:#b91c1c;border-color:#b91c1c">Reject</button>
            </td>
          </tr>`;
      }).join('');
      const pendingHtml = `
        <div class="card animate-fadein mb-6">
          <div class="px-4 py-3 border-b border-g-100 flex items-center justify-between">
            <h2 class="text-[14px] font-semibold text-g-800">Pending review queue</h2>
            <span class="text-[11px] text-g-500">${pendingItems.length} edge(s) awaiting review</span>
          </div>
          ${pendingItems.length === 0
            ? '<div class="px-4 py-6 text-[12px] text-g-500">No pending reviews. All AI-inferred lineage has been ratified or rejected.</div>'
            : `<table class="tbl">
                 <thead><tr><th>Target</th><th>Source(s)</th><th class="text-right">Confidence</th><th>Awaiting since</th><th class="text-right">Actions</th></tr></thead>
                 <tbody>${pendingTbody}</tbody>
               </table>`}
        </div>`;

      const actTbody = (activity.items || []).map(a => `
        <tr>
          <td class="mono text-g-500">${esc((a.ts || '').slice(0,19).replace('T',' '))}</td>
          <td class="mono">${esc(a.actor || '—')}</td>
          <td><span class="chip-mono">${esc(a.action || '')}</span></td>
          <td class="mono">${esc(a.table_name || '')}${a.column_name ? '.' + esc(a.column_name) : ''}</td>
          <td class="text-g-600">${esc(a.reason || '')}</td>
        </tr>`).join('');
      const activityHtml = `
        <div class="card animate-fadein mb-6">
          <div class="px-4 py-3 border-b border-g-100 flex items-center justify-between">
            <h2 class="text-[14px] font-semibold text-g-800">Recent activity</h2>
            <span class="text-[11px] text-g-500">last 20</span>
          </div>
          <table class="tbl">
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Reason</th></tr></thead>
            <tbody>${actTbody || '<tr><td colspan="5" class="text-g-400">No activity yet.</td></tr>'}</tbody>
          </table>
        </div>`;

      root.innerHTML =
        pageHeader('Catalog',
                   'Certifications, pending-review queue, and activity log for the local catalog layer')
        + banner + metrics + certsHtml + pendingHtml + activityHtml;

      // Wire ratify / reject buttons
      root.querySelectorAll('.cat-action').forEach(btn => {
        btn.addEventListener('click', async (ev) => {
          const action = btn.dataset.action;
          const t = btn.dataset.table;
          const c = btn.dataset.column;
          const reason = window.prompt(
            `Reason for ${action}ing ${t}.${c}? (optional)`, ''
          );
          if (reason === null) return;  // cancelled
          btn.disabled = true;
          try {
            const res = await fetch(`/api/catalog/${action}`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ table: t, column: c, reason }),
            });
            const data = await res.json();
            if (!data.ok) throw new Error(data.error || 'request failed');
          } catch (e) {
            alert(`Failed: ${e.message}`);
          } finally {
            viewCatalog();  // refresh
          }
        });
      });

      // Click a cert row → open Datasets and let the user dive in
      root.querySelectorAll('.cert-row').forEach(tr => {
        tr.addEventListener('click', () => {
          location.hash = '#/datasets';
        });
      });

    } catch (e) { setError(e); }
  }

  // ---------- router ---------------------------------------------------

  const routes = [
    { match: /^#\/dashboard$|^#?$/,                 view: () => viewDashboard(),                    nav: 'dashboard' },
    { match: /^#\/runs$/,                           view: () => viewRuns(),                          nav: 'runs' },
    { match: /^#\/runs\/(.+)$/,                     view: (m) => viewRunDetail(decodeURIComponent(m[1])), nav: 'runs' },
    { match: /^#\/lineage$/,                        view: () => viewLineage(),                       nav: 'lineage' },
    { match: /^#\/datasets$/,                       view: () => viewDatasets(),                      nav: 'datasets' },
    { match: /^#\/catalog$/,                        view: () => viewCatalog(),                       nav: 'catalog' },
    { match: /^#\/dq$/,                             view: () => viewDQ(),                            nav: 'dq' },
    { match: /^#\/discover$/,                       view: () => Discover.render(),                   nav: 'discover' },
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
// Discover — full-page data discovery + impact intelligence
// =====================================================================
const Discover = (() => {
  let conversationId = null;
  let pendingPrefill = '';   // text staged by other views (e.g. Lineage Explorer dataset click)
  let activeStream = null;   // current ThinkingStream container (per turn)
  let convEl = null;         // conversation container
  let refsEl = null;         // references panel
  let messages = [];         // [{role, content}] for in-page render only
  const refIndex = new Map(); // table.column -> {table, column, layer?}
  const tableRefs = new Set();

  function escHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])
    );
  }

  // Other views call this BEFORE navigating to #/discover. The query is
  // staged and surfaces in the search input on the next render().
  function prefill(query) {
    pendingPrefill = String(query || '');
  }

  // Navigate to the Discover page with a pre-filled query.
  function open(query) {
    if (query) prefill(query);
    if (location.hash !== '#/discover') {
      location.hash = '#/discover';
    } else {
      // Already on the page — re-render to surface the prefill.
      Discover.render();
    }
  }

  // ── Empty state: hero + categorised prompt cards ──────────────────
  const CATEGORIES = [
    {
      key: 'discovery',
      icon: 'D',
      title: 'Discovery',
      sub: 'Find tables and columns by business concept.',
      prompts: [
        'Where is customer risk score stored?',
        'Show me everything about KYC status',
        'What does kyc_stale_flag mean?',
        'Where is transaction volume tracked?',
      ],
    },
    {
      key: 'impact',
      icon: 'I',
      title: 'Impact Analysis',
      sub: 'See what breaks if a column changes.',
      prompts: [
        'What breaks if I rename src_customer.ssn_hash?',
        'Impact of dropping stg_fx_resolved.rate',
        'What depends on src_customer.kyc_status?',
        'If I change src_transaction.amount to INTEGER, what breaks?',
      ],
    },
    {
      key: 'explore',
      icon: 'E',
      title: 'Explore by table',
      sub: 'Inspect a table’s columns and definitions.',
      prompts: [
        'What’s in fct_customer_risk_profile?',
        'Show me the FX rate columns',
        'Which columns are in stg_transaction_normalized?',
        'What data do we have about reversals?',
      ],
    },
  ];

  function renderEmptyBody() {
    return `
      <div class="discover-cats">
        ${CATEGORIES.map(c => `
          <div class="discover-cat">
            <div class="cat-head">
              <div class="cat-icon cat-${escHtml(c.key)}">${escHtml(c.icon)}</div>
              <div>
                <div class="cat-title">${escHtml(c.title)}</div>
              </div>
            </div>
            <div class="cat-sub">${escHtml(c.sub)}</div>
            ${c.prompts.map(p => `
              <button type="button" class="cat-prompt" data-prompt="${escHtml(p)}">${escHtml(p)}</button>
            `).join('')}
          </div>
        `).join('')}
      </div>`;
  }

  function attachPromptHandlers(scope) {
    scope.querySelectorAll('[data-prompt]').forEach(btn => {
      btn.addEventListener('click', () => {
        const text = btn.dataset.prompt || '';
        const inp = scope.querySelector('#discover-input');
        if (inp) inp.value = text;
        send(text);
      });
    });
  }

  // ── Page render ───────────────────────────────────────────────────
  function render() {
    const root = document.getElementById('page-root');
    if (!root) return;

    const initialQuery = pendingPrefill;
    pendingPrefill = '';

    root.innerHTML = `
      <div class="discover-shell animate-fadein">
        <div class="discover-hero">
          <div class="lead">Discover</div>
          <div class="sub">Ask about your data lineage in plain language. Powered by the lineage and impact specialists.</div>
          <form id="discover-form" class="discover-search" autocomplete="off">
            <span class="magnifier">⌕</span>
            <input id="discover-input" type="text" placeholder="Ask anything about your data…" />
            <button id="discover-submit" type="submit">Ask</button>
          </form>
        </div>

        <div id="discover-empty">${renderEmptyBody()}</div>

        <div id="discover-active" class="discover-active" style="display:none">
          <div id="discover-conversation" class="discover-conversation"></div>
          <aside id="discover-refs" class="discover-refs">
            <h3>Referenced columns</h3>
            <div id="discover-refs-cols"><div class="empty">Mentioned columns will appear here.</div></div>
            <h3 style="margin-top:14px">Referenced tables</h3>
            <div id="discover-refs-tbls"><div class="empty">Mentioned tables will appear here.</div></div>
          </aside>
        </div>
      </div>
    `;

    convEl = root.querySelector('#discover-conversation');
    refsEl = root.querySelector('#discover-refs');

    const form = root.querySelector('#discover-form');
    const inp  = root.querySelector('#discover-input');

    if (initialQuery) inp.value = initialQuery;

    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const q = (inp.value || '').trim();
      if (!q) return;
      send(q);
    });

    attachPromptHandlers(root);

    // Re-render any prior conversation in the same session if we already
    // had messages (e.g. user navigated away and back without a hard reload).
    if (messages.length > 0) {
      switchToActive();
      messages.forEach(m => {
        if (m.role === 'user') appendUserMessage(m.content);
        else if (m.role === 'assistant') appendAssistantInline(m.content);
      });
      // (Old turns drop their thinking streams; not preserved across renders.)
      renderRefs();
    }

    setTimeout(() => inp.focus(), 50);
  }

  function switchToActive() {
    const empty = document.getElementById('discover-empty');
    const active = document.getElementById('discover-active');
    if (empty) empty.style.display = 'none';
    if (active) active.style.display = '';
  }

  // ── Conversation rendering ────────────────────────────────────────
  function appendUserMessage(text) {
    const el = document.createElement('div');
    el.className = 'disc-msg-user';
    el.textContent = text;
    convEl.appendChild(el);
    convEl.scrollTop = convEl.scrollHeight;
  }

  function appendAssistantInline(text) {
    const el = document.createElement('div');
    el.className = 'disc-msg-assistant';
    el.innerHTML = renderAssistant(text);
    convEl.appendChild(el);
    convEl.scrollTop = convEl.scrollHeight;
    return el;
  }

  // Render assistant message as proper prose: parse the agent's markdown
  // (headers / lists / hr / code blocks / bold) via the `marked` CDN library,
  // then post-process to swap inline-code that looks like `table.column`
  // for our teal chip and turn CRITICAL/HIGH/MEDIUM/LOW words into severity
  // badges. Falls back to a minimal renderer if marked isn't loaded.
  function renderAssistant(text) {
    const raw = String(text || '');
    let html;
    try {
      if (window.marked && typeof window.marked.parse === 'function') {
        html = window.marked.parse(raw, { gfm: true, breaks: false });
      } else {
        html = escHtml(raw)
          .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
          .replace(/\n/g, '<br>');
      }
    } catch {
      html = escHtml(raw).replace(/\n/g, '<br>');
    }

    // Inline `<code>foo.bar</code>` or `<code>kyc_stale_flag</code>` → teal chip.
    html = html.replace(
      /<code>([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?)<\/code>/gi,
      '<span class="col-ref">$1</span>'
    );

    // Severity words at word boundaries — only outside HTML attributes.
    // Lookbehind prevents matching inside class="sev-HIGH" etc.
    html = html.replace(
      /(^|[^<="\w-])(CRITICAL|HIGH|MEDIUM|LOW)\b/g,
      (_, prefix, sev) => `${prefix}<span class="sev sev-${sev}">${sev}</span>`
    );

    return `<div class="disc-prose">${html}</div>`;
  }

  // Per-turn thinking stream — appended to the conversation flow.
  function newThinkingStream() {
    const el = document.createElement('div');
    el.className = 'chat-thinking-stream';
    convEl.appendChild(el);
    convEl.scrollTop = convEl.scrollHeight;
    return el;
  }

  function addThinkingLine(stream, text) {
    if (!stream) return;
    stream.querySelectorAll('.chat-thought-line.latest').forEach(prev => {
      prev.classList.remove('latest');
      prev.classList.add('older');
      const caret = prev.querySelector('.caret');
      if (caret) caret.remove();
    });
    const row = document.createElement('div');
    row.className = 'chat-thought-line latest';
    row.innerHTML = `<span class="dot"></span><span class="text"></span><span class="caret">|</span>`;
    row.querySelector('.text').textContent = text;
    stream.appendChild(row);
    convEl.scrollTop = convEl.scrollHeight;
  }

  function freezeThinkingStream(stream) {
    if (!stream) return;
    stream.classList.add('done');
    stream.querySelectorAll('.chat-thought-line.latest').forEach(prev => {
      prev.classList.remove('latest');
      prev.classList.add('older');
      const caret = prev.querySelector('.caret');
      if (caret) caret.remove();
    });
  }

  // ── References extraction ─────────────────────────────────────────
  // Pull `table.column` and `bare_table_name` patterns out of assistant
  // text and surface them in the right-hand panel as clickable cards.
  const KNOWN_TABLES = [
    'src_branch', 'src_customer', 'src_account', 'src_transaction', 'src_fx_rate',
    'stg_fx_resolved', 'stg_transaction_normalized', 'stg_customer_enriched',
    'fct_customer_risk_profile',
  ];

  function inferLayer(table) {
    if (!table) return '';
    if (table.startsWith('src_')) return 'layer_0';
    if (table.startsWith('stg_')) return 'layer_1';
    if (table.startsWith('fct_') || table.startsWith('dim_')) return 'layer_2';
    return '';
  }

  function harvestReferences(text) {
    if (!text) return;
    const tableColRe = /`([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)`/gi;
    let m;
    while ((m = tableColRe.exec(text)) !== null) {
      const [t, c] = [m[1], m[2]];
      const key = `${t}.${c}`;
      if (!refIndex.has(key)) {
        refIndex.set(key, { table: t, column: c, layer: inferLayer(t) });
      }
    }
    // Bare table names — only surface ones we know about
    KNOWN_TABLES.forEach(t => {
      const re = new RegExp(`\\b${t}\\b`, 'i');
      if (re.test(text)) tableRefs.add(t);
    });
  }

  function renderRefs() {
    if (!refsEl) return;
    const cols = refsEl.querySelector('#discover-refs-cols');
    const tbls = refsEl.querySelector('#discover-refs-tbls');
    if (!cols || !tbls) return;

    if (refIndex.size === 0) {
      cols.innerHTML = `<div class="empty">Mentioned columns will appear here.</div>`;
    } else {
      cols.innerHTML = Array.from(refIndex.values()).map(r => `
        <button type="button" class="ref-card"
                data-table="${escHtml(r.table)}" data-column="${escHtml(r.column)}">
          <div class="ref-mono">${escHtml(r.table)}.${escHtml(r.column)}</div>
          <div class="ref-meta">
            <span class="ref-tag">${escHtml(r.layer || '?')}</span>
            <span>open in Lineage</span>
          </div>
        </button>
      `).join('');
      cols.querySelectorAll('.ref-card').forEach(btn => {
        btn.addEventListener('click', () => {
          location.hash = '#/lineage';
        });
      });
    }

    if (tableRefs.size === 0) {
      tbls.innerHTML = `<div class="empty">Mentioned tables will appear here.</div>`;
    } else {
      tbls.innerHTML = Array.from(tableRefs).map(t => `
        <button type="button" class="ref-card" data-table="${escHtml(t)}">
          <div class="ref-mono">${escHtml(t)}</div>
          <div class="ref-meta">
            <span class="ref-tag">${escHtml(inferLayer(t) || '?')}</span>
            <span>open in Lineage</span>
          </div>
        </button>
      `).join('');
      tbls.querySelectorAll('.ref-card').forEach(btn => {
        btn.addEventListener('click', () => { location.hash = '#/lineage'; });
      });
    }
  }

  // ── Send / SSE consumption ────────────────────────────────────────
  async function send(rawText) {
    const text = String(rawText || '').trim();
    if (!text) return;
    if (!convEl) return; // page not rendered yet

    switchToActive();

    const inp = document.getElementById('discover-input');
    const submitBtn = document.getElementById('discover-submit');
    if (inp) { inp.value = ''; inp.disabled = true; }
    if (submitBtn) submitBtn.disabled = true;

    messages.push({ role: 'user', content: text });
    appendUserMessage(text);

    const stream = newThinkingStream();
    addThinkingLine(stream, 'Thinking…');

    let assistantEl = null;
    let finalContent = '';

    function ensureAssistant() {
      if (assistantEl) return assistantEl;
      assistantEl = document.createElement('div');
      assistantEl.className = 'disc-msg-assistant';
      assistantEl.innerHTML = `<span class="chat-content"></span><span class="chat-typing-caret">|</span>`;
      convEl.appendChild(assistantEl);
      convEl.scrollTop = convEl.scrollHeight;
      return assistantEl;
    }
    function repaintAssistant() {
      if (!assistantEl) return;
      const body = assistantEl.querySelector('.chat-content');
      if (body) body.innerHTML = renderAssistant(finalContent);
    }
    function finalizeAssistant(fallback) {
      ensureAssistant();
      if (!finalContent && fallback) finalContent = fallback;
      const body = assistantEl.querySelector('.chat-content');
      if (body) body.innerHTML = renderAssistant(finalContent || '(empty response)');
      const caret = assistantEl.querySelector('.chat-typing-caret');
      if (caret) caret.remove();
      messages.push({ role: 'assistant', content: finalContent || '' });
      harvestReferences(finalContent);
      renderRefs();
    }

    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let currentEvent = '';
      let finalShown = false;
      let fallbackText = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const rawLine of lines) {
          const line = rawLine.replace(/\r$/, '');
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim();
            continue;
          }
          if (line.startsWith('data: ')) {
            let data;
            try { data = JSON.parse(line.slice(6)); } catch { continue; }

            if (currentEvent === 'tool_start') {
              addThinkingLine(stream, data.label || data.tool || '…');
            } else if (currentEvent === 'tool_result') {
              const label = (data.tool || '').replace('ask_', '').replace('_', ' ');
              addThinkingLine(stream, `Got ${label} reply, synthesising…`);
              fallbackText = data.output || fallbackText;
              harvestReferences(data.output || '');
              renderRefs();
            } else if (currentEvent === 'token') {
              ensureAssistant();
              finalContent += data.token || '';
              repaintAssistant();
              convEl.scrollTop = convEl.scrollHeight;
            } else if (currentEvent === 'final') {
              conversationId = data.conversation_id || conversationId;
              if (data.response && !finalContent) finalContent = data.response;
              finalizeAssistant(data.response);
              freezeThinkingStream(stream);
              finalShown = true;
            } else if (currentEvent === 'error') {
              freezeThinkingStream(stream);
              const errEl = document.createElement('div');
              errEl.className = 'disc-msg-assistant';
              errEl.style.color = 'var(--red)';
              errEl.textContent = `Error: ${data.message || 'unknown'}`;
              convEl.appendChild(errEl);
              finalShown = true;
            }
          }
          if (line === '') currentEvent = '';
        }
      }
      if (!finalShown) {
        freezeThinkingStream(stream);
        finalizeAssistant(fallbackText || '(stream ended without a final response)');
      }
    } catch (err) {
      freezeThinkingStream(stream);
      const errEl = document.createElement('div');
      errEl.className = 'disc-msg-assistant';
      errEl.style.color = 'var(--red)';
      errEl.textContent = `Error: ${err.message}`;
      convEl.appendChild(errEl);
    } finally {
      if (inp) { inp.disabled = false; inp.focus(); }
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  return { render, send, prefill, open };
})();

window.addEventListener('hashchange', App.dispatch);
window.addEventListener('DOMContentLoaded', () => {
  App.bindNav();
  App.dispatch();
});
