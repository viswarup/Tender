/**
 * Dashboard Application — Single-Page App logic
 * Handles navigation, data loading, vendor detail views,
 * source traceability modal, and chatbot interaction.
 */

// ── State ──
let vendors = [];
let config = {};
let currentVendor = null;

// ── API Helpers ──
async function api(endpoint) {
    const headers = {};
    const apiKey = document.querySelector('meta[name="api-key"]')?.getAttribute('content');
    if (apiKey) {
        headers['X-API-Key'] = apiKey;
    }
    const res = await fetch(endpoint, { headers });
    if (!res.ok) throw new Error(`API ${endpoint}: ${res.status}`);
    return res.json();
}

async function apiPost(endpoint, data) {
    const headers = { 'Content-Type': 'application/json' };
    const apiKey = document.querySelector('meta[name="api-key"]')?.getAttribute('content');
    if (apiKey) {
        headers['X-API-Key'] = apiKey;
    }
    const res = await fetch(endpoint, {
        method: 'POST',
        headers,
        body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`API ${endpoint}: ${res.status}`);
    return res.json();
}

// ── Init ──
document.addEventListener('DOMContentLoaded', async () => {
    setupNavigation();
    setupChat();
    setupModal();
    await loadData();
});

async function loadData() {
    const status = document.getElementById('status-bar');
    try {
        status.textContent = 'Loading config...';
        config = await api('/api/config');

        status.textContent = 'Loading vendors...';
        vendors = await api('/api/vendors');

        renderOverview();
        status.textContent = `${vendors.length} vendors loaded`;
    } catch (e) {
        status.textContent = 'Pipeline not run yet — start with: python -m pipeline.run_pipeline';
        renderEmptyOverview();
        console.error(e);
    }
}

// ── Navigation ──
function setupNavigation() {
    document.querySelectorAll('.topbar-nav button').forEach(btn => {
        btn.addEventListener('click', () => switchView(btn.dataset.view));
    });
}

function switchView(viewName) {
    document.querySelectorAll('.topbar-nav button').forEach(b => b.classList.remove('active'));
    document.querySelector(`[data-view="${viewName}"]`)?.classList.add('active');
    document.querySelectorAll('.view-section').forEach(s => s.classList.remove('active'));
    document.getElementById(`view-${viewName}`)?.classList.add('active');
    if (viewName === 'groundtruth') renderGroundTruth();
}

// ── Overview ──
function renderOverview() {
    const title = document.getElementById('tender-title');
    title.textContent = config.subject || 'Tender Evaluation';

    // Stats
    const accepted = vendors.filter(v => v.status === 'ACCEPTED').length;
    const rejected = vendors.filter(v => v.status === 'REJECTED').length;
    const queried = vendors.filter(v => v.status === 'QUERIED').length;
    document.getElementById('stat-total').textContent = vendors.length;
    document.getElementById('stat-accepted').textContent = accepted;
    document.getElementById('stat-rejected').textContent = rejected;
    document.getElementById('stat-queried').textContent = queried;

    // Tender Info
    const infoBody = document.getElementById('tender-info-body');
    infoBody.innerHTML = `
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem 2rem;">
            <div><span style="color: var(--text-dim);">Tender No:</span> <strong>${config.tender_no || '—'}</strong></div>
            <div><span style="color: var(--text-dim);">Bid No:</span> <strong>${config.bid_no || '—'}</strong></div>
            <div><span style="color: var(--text-dim);">Bid End Date:</span> <strong>${config.bid_end_date || '—'}</strong></div>
            <div><span style="color: var(--text-dim);">Organization:</span> <strong>${config.organization || '—'}</strong></div>
            <div style="grid-column: 1/-1;"><span style="color: var(--text-dim);">Similar Item Def:</span> <strong>${config.similar_item_definition || '—'}</strong></div>
            <div style="grid-column: 1/-1;"><span style="color: var(--text-dim);">Criteria Options:</span>
                ${(config.criteria_options || []).map(o => 
                    `<span class="badge badge-purple" style="margin-left: 0.3rem;">${o.label}: ${o.min_orders}× ≥₹${(o.min_value_per_order || o.min_value || 0).toLocaleString('en-IN')}</span>`
                ).join('')}
            </div>
        </div>
    `;

    // Vendor Table
    const tbody = document.getElementById('overview-table-body');
    tbody.innerHTML = vendors.map((v, i) => `
        <tr onclick="openVendor('${v.name}')" data-vendor="${v.name}">
            <td>${i + 1}</td>
            <td><strong>${v.name}</strong></td>
            <td>${statusBadge(v.status)}</td>
            <td>${v.doc_count ?? '—'}</td>
            <td>${v.po_count ?? '—'}</td>
            <td>${v.has_maf ? '✅' : '—'}</td>
            <td>${v.has_udyam ? '✅' : '—'}</td>
            <td style="font-size: 0.78rem; color: var(--text-dim); max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${v.criteria_met || '—'}</td>
        </tr>
    `).join('');
}

function renderEmptyOverview() {
    document.getElementById('overview-table-body').innerHTML = `
        <tr><td colspan="8" style="text-align: center; padding: 2rem; color: var(--text-muted);">
            No data yet. Run the pipeline first:<br>
            <code style="margin-top: 0.5rem; display: inline-block;">.venv/bin/python -m pipeline.run_pipeline</code>
        </td></tr>
    `;
}

function statusBadge(status) {
    const map = {
        ACCEPTED: { cls: 'badge-green', icon: '✅' },
        REJECTED: { cls: 'badge-red', icon: '❌' },
        QUERIED: { cls: 'badge-amber', icon: '⚠️' },
    };
    const s = map[status] || { cls: 'badge-blue', icon: '❓' };
    return `<span class="badge ${s.cls}">${s.icon} ${status || 'UNKNOWN'}</span>`;
}

// ── Vendor Detail ──
async function openVendor(name) {
    currentVendor = name;
    switchView('vendors');

    const container = document.getElementById('vendor-detail-container');
    container.innerHTML = '<div class="loading"><div class="spinner"></div> Loading vendor data...</div>';

    try {
        const [vendorData, evalData] = await Promise.all([
            api(`/api/vendor/${encodeURIComponent(name)}`),
            api(`/api/evaluation/${encodeURIComponent(name)}`).catch(() => null),
        ]);

        renderVendorDetail(vendorData, evalData);
        renderEvaluationDetail(evalData, name);
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><h3>Error loading ${name}</h3><p>${e.message}</p></div>`;
    }
}

function renderVendorDetail(vendor, evalData) {
    const container = document.getElementById('vendor-detail-container');
    const status = evalData?.status || 'UNKNOWN';

    let html = `
        <button class="detail-back" onclick="switchView('overview')">← Back to Overview</button>
        <div class="detail-header">
            <div class="detail-vendor-name">${vendor.vendor_name}</div>
            ${statusBadge(status)}
        </div>
    `;

    // Summary Card
    html += `<div class="card"><div class="card-header"><div class="card-title">Summary</div></div>
        <pre style="font-size: 0.82rem; line-height: 1.6; color: var(--text-dim); white-space: pre-wrap;">${vendor.vendor_summary || 'No summary available'}</pre>
    </div>`;

    // Documents Card
    html += `<div class="card"><div class="card-header"><div class="card-title">Submitted Documents (${vendor.documents?.length || 0})</div></div>`;
    (vendor.documents || []).forEach(doc => {
        const typeIcon = docTypeIcon(doc.document_type);
        html += `
            <div class="doc-item" onclick="viewDocument('${vendor.vendor_name}', '${doc.filename}')">
                <div class="doc-icon">${typeIcon}</div>
                <div class="doc-info">
                    <div class="doc-name">${doc.filename}</div>
                    <div class="doc-meta">${doc.document_type} · ${doc.num_pages} pages · ${formatBytes(doc.file_size_bytes)} · conf: ${(doc.classification_confidence * 100).toFixed(0)}%</div>
                </div>
                <span class="badge badge-blue" style="font-size: 0.68rem;">${doc.document_type}</span>
            </div>
        `;
    });
    html += `</div>`;

    // Purchase Orders
    if (vendor.purchase_orders?.length) {
        html += `<div class="card"><div class="card-header"><div class="card-title">Purchase Orders (${vendor.purchase_orders.length})</div></div>`;
        vendor.purchase_orders.forEach((po, i) => {
            const qualifying = evalData?.po_evaluations?.[i]?.is_qualifying;
            const cardClass = qualifying === true ? 'qualifying' : qualifying === false ? 'not-qualifying' : '';
            html += `
                <div class="card po-card ${cardClass}" style="margin-bottom: 0.75rem;">
                    <div class="po-field"><span class="po-field-label">PO Number</span><span class="po-field-value">${po.po_number?.value || '—'} ${sourceLink(po.po_number?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Organization</span><span class="po-field-value">${po.issuing_organization?.value || '—'} ${sourceLink(po.issuing_organization?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Date</span><span class="po-field-value">${po.po_date?.value || '—'} ${sourceLink(po.po_date?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Value</span><span class="po-field-value"><strong>₹${po.po_value_numeric?.toLocaleString('en-IN') || po.po_value?.value || '—'}</strong> ${sourceLink(po.po_value?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Items</span><span class="po-field-value">${po.items_description?.value || '—'} ${sourceLink(po.items_description?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Execution Proof</span><span class="po-field-value">${po.execution_proof_status === 'SUBMITTED' ? '✅ Submitted' : '❌ Not submitted'}</span></div>
                    ${qualifying !== undefined ? `<div class="po-field"><span class="po-field-label">Qualifying</span><span class="po-field-value">${qualifying ? '<span class="badge badge-green">✓ QUALIFYING</span>' : '<span class="badge badge-red">✗ NOT QUALIFYING</span>'}</span></div>` : ''}
                </div>
            `;
        });
        html += `</div>`;
    }

    // OEM Authorizations
    if (vendor.maf_authorizations?.length) {
        html += `<div class="card"><div class="card-header"><div class="card-title">OEM Authorizations (${vendor.maf_authorizations.length})</div></div>`;
        vendor.maf_authorizations.forEach(maf => {
            html += `<div style="padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem;">
                <div class="po-field"><span class="po-field-label">OEM Name</span><span class="po-field-value"><strong>${maf.oem_name?.value || '—'}</strong> ${sourceLink(maf.oem_name?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Authorized Reseller</span><span class="po-field-value">${maf.authorized_vendor?.value || '—'} ${sourceLink(maf.authorized_vendor?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Product Category</span><span class="po-field-value">${maf.product_category?.value || '—'} ${sourceLink(maf.product_category?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Validity Period</span><span class="po-field-value">${maf.validity_period?.value || '—'} ${sourceLink(maf.validity_period?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Tender Reference</span><span class="po-field-value">${maf.reference_tender?.value || '—'} ${sourceLink(maf.reference_tender?.source)}</span></div>
            </div>`;
        });
        html += `</div>`;
    }

    // Turnover Certificates
    if (vendor.turnover_certificates?.length) {
        html += `<div class="card"><div class="card-header"><div class="card-title">CA Turnover Certificates (${vendor.turnover_certificates.length})</div></div>`;
        vendor.turnover_certificates.forEach(t => {
            let entriesHtml = '';
            if (t.turnover_entries?.length) {
                entriesHtml = t.turnover_entries.map(e => {
                    const parsedVal = parseFloat(e.amount.replace(/,/g, ''));
                    const formatted = isNaN(parsedVal) ? e.amount : `₹${parsedVal.toLocaleString('en-IN')}`;
                    return `
                        <div class="po-field" style="margin-left: 1.5rem; margin-bottom: 0.25rem;">
                            <span class="po-field-label" style="min-width: 100px; color: var(--text-muted);">FY ${e.year}</span>
                            <span class="po-field-value"><strong>${formatted}</strong></span>
                        </div>
                    `;
                }).join('');
            }
            html += `<div style="padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem;">
                <div class="po-field"><span class="po-field-label">CA Firm Name</span><span class="po-field-value"><strong>${t.ca_name?.value || '—'}</strong> ${sourceLink(t.ca_name?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Vendor Certified</span><span class="po-field-value">${t.vendor_name?.value || '—'} ${sourceLink(t.vendor_name?.source)}</span></div>
                <div style="margin: 0.5rem 0 0.35rem 1.5rem; font-size: 0.76rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;">Turnover Entries</div>
                ${entriesHtml}
            </div>`;
        });
        html += `</div>`;
    }

    // Udyam/MSME Registrations
    if (vendor.udyam_registrations?.length) {
        html += `<div class="card"><div class="card-header"><div class="card-title">Udyam/MSME Registrations (${vendor.udyam_registrations.length})</div></div>`;
        vendor.udyam_registrations.forEach(u => {
            html += `<div style="padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem;">
                <div class="po-field"><span class="po-field-label">Udyam Number</span><span class="po-field-value"><strong>${u.udyam_number?.value || '—'}</strong> ${sourceLink(u.udyam_number?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Enterprise Name</span><span class="po-field-value">${u.enterprise_name?.value || '—'} ${sourceLink(u.enterprise_name?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Enterprise Type</span><span class="po-field-value">${u.enterprise_type?.value || '—'} ${sourceLink(u.enterprise_type?.source)}</span></div>
            </div>`;
        });
        html += `</div>`;
    }

    // GST Certificates
    if (vendor.gst_certificates?.length) {
        html += `<div class="card"><div class="card-header"><div class="card-title">GST Certificates (${vendor.gst_certificates.length})</div></div>`;
        vendor.gst_certificates.forEach(g => {
            html += `<div style="padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem;">
                <div class="po-field"><span class="po-field-label">GSTIN</span><span class="po-field-value"><strong>${g.gstin?.value || '—'}</strong> ${sourceLink(g.gstin?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Legal Name</span><span class="po-field-value">${g.legal_name?.value || '—'} ${sourceLink(g.legal_name?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Trade Name</span><span class="po-field-value">${g.trade_name?.value || '—'} ${sourceLink(g.trade_name?.source)}</span></div>
            </div>`;
        });
        html += `</div>`;
    }

    // ISO Certificates
    if (vendor.iso_certificates?.length) {
        html += `<div class="card"><div class="card-header"><div class="card-title">ISO Certificates (${vendor.iso_certificates.length})</div></div>`;
        vendor.iso_certificates.forEach(c => {
            html += `<div style="padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem;">
                <div class="po-field"><span class="po-field-label">Standard</span><span class="po-field-value"><strong>${c.standard?.value || '—'}</strong> ${sourceLink(c.standard?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Certificate No</span><span class="po-field-value">${c.certificate_number?.value || '—'} ${sourceLink(c.certificate_number?.source)}</span></div>
                <div class="po-field"><span class="po-field-label">Validity</span><span class="po-field-value">${c.validity_period?.value || '—'} ${sourceLink(c.validity_period?.source)}</span></div>
            </div>`;
        });
        html += `</div>`;
    }

    // Self Declarations
    if (vendor.declarations?.length) {
        html += `<div class="card"><div class="card-header"><div class="card-title">Self Declarations (${vendor.declarations.length})</div></div>`;
        vendor.declarations.forEach(d => {
            html += `<div style="padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.85rem;">
                <div class="po-field"><span class="po-field-label">Declaration Type</span><span class="po-field-value"><strong>${d.declaration_type || '—'}</strong></span></div>
                <div class="po-field"><span class="po-field-label">Declaration Text</span><span class="po-field-value">${d.declaration_text?.value || '—'} ${sourceLink(d.declaration_text?.source)}</span></div>
            </div>`;
        });
        html += `</div>`;
    }

    container.innerHTML = html;
}

async function renderGroundTruth() {
    const container = document.getElementById('groundtruth-container');
    container.innerHTML = '<div class="loading"><div class="spinner"></div> Loading HEAMONS validation...</div>';

    try {
        const data = await api('/api/ground-truth/HEAMONS%20SYSTEMS');
        const criteria = data.criteria || {};
        const gt = data.ground_truth || {};
        const po = gt.qualifying_po || {};

        let html = `
            <div class="section-header">Ground Truth Validation</div>
            <h2>HEAMONS SYSTEMS</h2>
            <div class="eval-status-banner ${data.passed ? 'ACCEPTED' : 'REJECTED'}">
                <strong>${data.passed ? 'MATCHED' : 'MISMATCH'}</strong>
                <div style="margin-top: 0.5rem; font-size: 0.88rem;">Current pipeline output compared with PQC1.pdf ground truth for HEAMONS only.</div>
            </div>

            <div class="card">
                <div class="card-header"><div class="card-title">PQC1 Criteria</div></div>
                <div class="truth-grid">
                    <div><span class="truth-label">Financial PQC</span><strong>Not Applicable</strong></div>
                    <div><span class="truth-label">Similar Item</span><strong>${criteria.similar_item || '—'}</strong></div>
                    ${(criteria.options || []).map(o => `
                        <div><span class="truth-label">${o.label}</span><strong>${o.requirement}, ≥₹${o.min_value_per_order_inr.toLocaleString('en-IN')}</strong></div>
                    `).join('')}
                </div>
            </div>

            <div class="card">
                <div class="card-header"><div class="card-title">Ground Truth Basis</div></div>
                <div class="truth-grid">
                    <div><span class="truth-label">Expected Status</span><strong>${gt.status || '—'}</strong></div>
                    <div><span class="truth-label">Qualifying PO</span><strong>${po.po_number || '—'}</strong></div>
                    <div><span class="truth-label">PO Date</span><strong>${po.doc_date || '—'}</strong></div>
                    <div><span class="truth-label">PO Value</span><strong>₹${(po.po_value_inr || 0).toLocaleString('en-IN')}</strong></div>
                    <div><span class="truth-label">Execution Proof</span><strong>${po.execution_proof || '—'}</strong></div>
                </div>
                <div class="eval-reasoning" style="margin-top: 1rem;">${gt.basis || ''}</div>
            </div>

            <div class="card">
                <div class="card-header"><div class="card-title">Validation Checks</div></div>
                ${(data.checks || []).map(check => `
                    <div class="truth-check ${check.passed ? 'pass' : 'fail'}">
                        <strong>${check.passed ? 'PASS' : 'FAIL'}</strong>
                        <span>${check.label}</span>
                        <small>Expected: ${check.expected ?? '—'} · Actual: ${check.actual ?? '—'}</small>
                    </div>
                `).join('')}
            </div>
        `;

        if (data.matched_po) {
            html += `
                <div class="card">
                    <div class="card-header"><div class="card-title">Matched Extracted PO</div></div>
                    <div class="po-field"><span class="po-field-label">PO Number</span><span class="po-field-value">${data.matched_po.po_number?.value || '—'} ${sourceLink(data.matched_po.po_number?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Organization</span><span class="po-field-value">${data.matched_po.issuing_organization?.value || '—'} ${sourceLink(data.matched_po.issuing_organization?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Date</span><span class="po-field-value">${data.matched_po.po_date?.value || '—'} ${sourceLink(data.matched_po.po_date?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Value</span><span class="po-field-value">₹${data.matched_po.po_value_numeric?.toLocaleString('en-IN') || '—'} ${sourceLink(data.matched_po.po_value?.source)}</span></div>
                    <div class="po-field"><span class="po-field-label">Items</span><span class="po-field-value">${data.matched_po.items_description?.value || '—'} ${sourceLink(data.matched_po.items_description?.source)}</span></div>
                </div>
            `;
        }

        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="empty-state"><div class="icon">!</div><h3>Ground truth unavailable</h3><p>${e.message}</p></div>`;
    }
}

function renderEvaluationDetail(evalData, vendorName) {
    const container = document.getElementById('eval-detail-container');
    if (!evalData) {
        container.innerHTML = `<div class="empty-state"><div class="icon">⚖️</div><h3>No evaluation for ${vendorName}</h3></div>`;
        return;
    }

    let html = `
        <button class="detail-back" onclick="switchView('overview')">← Back to Overview</button>
        <div class="detail-header">
            <div class="detail-vendor-name">${evalData.vendor_name} — PQC Evaluation</div>
        </div>

        <div class="eval-status-banner ${evalData.status}">
            <strong>${evalData.status}</strong>
            ${evalData.criteria_met ? ` — ${evalData.criteria_met}` : ''}
            <div style="margin-top: 0.5rem; font-size: 0.88rem;">${evalData.recommendation}</div>
        </div>
    `;

    // Queries
    if (evalData.queries?.length) {
        html += `<div class="card" style="border-left: 3px solid var(--amber);"><div class="card-title" style="margin-bottom: 0.5rem;">⚠️ Queries Raised</div>`;
        evalData.queries.forEach(q => {
            html += `<div style="font-size: 0.85rem; padding: 0.35rem 0; color: var(--amber);">• ${q}</div>`;
        });
        html += `</div>`;
    }

    // PO Evaluations Table
    if (evalData.po_evaluations?.length) {
        html += `<div class="card"><div class="card-title" style="margin-bottom: 0.75rem;">Purchase Order Evaluations (${evalData.po_evaluations.length})</div>`;
        evalData.po_evaluations.forEach(pe => {
            const qClass = pe.is_qualifying ? 'qualifying' : 'not-qualifying';
            html += `
                <div class="card po-card ${qClass}" style="margin-bottom: 0.75rem;">
                    <div class="po-field"><span class="po-field-label">PO Number</span><span class="po-field-value"><strong>${pe.po_number}</strong></span></div>
                    <div class="po-field"><span class="po-field-label">Organization</span><span class="po-field-value">${pe.issuing_org}</span></div>
                    <div class="po-field"><span class="po-field-label">Value</span><span class="po-field-value">₹${pe.po_value?.toLocaleString('en-IN')}</span></div>
                    <div class="po-field"><span class="po-field-label">Similar Item</span><span class="po-field-value">${pe.meets_similar_item ? '✅' : '❌'} ${pe.similar_item_reasoning}</span></div>
                    <div class="po-field"><span class="po-field-label">Value Threshold</span><span class="po-field-value">${pe.meets_value_threshold ? '✅' : '❌'} ${pe.threshold_used || 'Below all thresholds'}</span></div>
                    <div class="po-field"><span class="po-field-label">Date in Window</span><span class="po-field-value">${pe.date_within_window ? '✅' : '❌'}</span></div>
                    <div class="po-field"><span class="po-field-label">Execution Proof</span><span class="po-field-value">${pe.execution_proof_available ? '✅' : '❌'}</span></div>
                    ${pe.disqualification_reason ? `<div class="po-field"><span class="po-field-label" style="color: var(--red);">Reason</span><span class="po-field-value" style="color: var(--red);">${pe.disqualification_reason}</span></div>` : ''}
                </div>
            `;
        });
        html += `</div>`;
    }

    // Detailed Reasoning
    if (evalData.detailed_reasoning) {
        html += `<div class="card"><div class="card-title" style="margin-bottom: 0.75rem;">Detailed Reasoning</div>
            <div class="eval-reasoning">${evalData.detailed_reasoning}</div>
        </div>`;
    }

    container.innerHTML = html;
}

// ── Source Viewer Modal ──
function setupModal() {
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('source-modal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) closeModal();
    });
}

function closeModal() {
    document.getElementById('source-modal').classList.remove('active');
}

async function viewDocument(vendorName, filename) {
    // List page images for this document
    try {
        const images = await api(`/api/page-images/${encodeURIComponent(vendorName)}`);
        const docImages = images.filter(img => img.startsWith(filename.replace('.pdf', '')));

        if (docImages.length === 0) {
            alert(`No page images found for ${filename}. The pipeline may not have rendered images for this document.`);
            return;
        }

        const modal = document.getElementById('source-modal');
        const title = document.getElementById('modal-title');
        const body = document.getElementById('modal-body');

        title.textContent = `${filename} — ${docImages.length} pages`;

        const apiKey = document.querySelector('meta[name="api-key"]')?.getAttribute('content') || '';
        const queryParam = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : '';

        body.innerHTML = docImages.map((img, i) => `
            <div style="margin-bottom: 1rem; position: relative;">
                <div style="font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.3rem;">Page ${i + 1}</div>
                <img class="source-image" src="/api/page-image/${encodeURIComponent(vendorName)}/${img}${queryParam}" alt="Page ${i + 1}">
            </div>
        `).join('');

        modal.classList.add('active');
    } catch (e) {
        console.error('Failed to load document images:', e);
    }
}

function showSourceModal(source) {
    if (!source || !source.page_image_path) return;

    let vendorName = currentVendor;
    if (source.page_image_path) {
        const pathParts = source.page_image_path.split(/[/\\]/);
        const idx = pathParts.findIndex(p => p === 'page_images' || p === 'ocr_cache');
        if (idx !== -1 && idx + 1 < pathParts.length) {
            vendorName = pathParts[idx + 1];
        }
    }
    if (!vendorName) {
        vendorName = 'HEAMONS SYSTEMS';
    }

    const modal = document.getElementById('source-modal');
    const title = document.getElementById('modal-title');
    const body = document.getElementById('modal-body');

    const imgFilename = source.page_image_path.split('/').pop();
    title.textContent = `${source.source_file} — Page ${source.source_page}`;

    let bboxHtml = '';
    if (source.bbox) {
        const b = source.bbox;
        bboxHtml = `<div class="bbox-overlay" style="left:${b.x_min}%;top:${b.y_min}%;width:${b.x_max - b.x_min}%;height:${b.y_max - b.y_min}%;"></div>`;
    }

    const apiKey = document.querySelector('meta[name="api-key"]')?.getAttribute('content') || '';
    const queryParam = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : '';

    body.innerHTML = `
        <div style="position: relative; display: inline-block;">
            <img class="source-image" src="/api/page-image/${encodeURIComponent(vendorName)}/${imgFilename}${queryParam}" alt="Source page">
            ${bboxHtml}
        </div>
        ${source.raw_text ? `<div style="margin-top: 1rem; font-size: 0.8rem; color: var(--text-dim);">Raw text: ${source.raw_text}</div>` : ''}
    `;

    modal.classList.add('active');
}

function sourceLink(source) {
    if (!source || !source.page_image_path) return '';
    const label = source.bbox ? 'source + box' : 'source';
    return `<span class="source-link" onclick="event.stopPropagation(); showSourceModal(${JSON.stringify(source).replace(/"/g, '&quot;')})">${label}</span>`;
}

// ── Chat ──
let chatHistory = [];

function setupChat() {
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send');

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
}

async function sendMessage() {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    addChatMessage('user', message);

    const sendBtn = document.getElementById('chat-send');
    sendBtn.disabled = true;

    try {
        const response = await apiPost('/api/chat', {
            message,
            history: chatHistory.slice(-10),
        });

        addChatMessage('assistant', response.response, response.sources);
        chatHistory.push({ role: 'user', content: message });
        chatHistory.push({ role: 'assistant', content: response.response });
    } catch (e) {
        addChatMessage('assistant', `Error: ${e.message}`);
    } finally {
        sendBtn.disabled = false;
        input.focus();
    }
}

function addChatMessage(role, content, sources) {
    const container = document.getElementById('chat-messages');
    const msg = document.createElement('div');
    msg.className = `chat-msg ${role}`;
    msg.innerHTML = content.replace(/\n/g, '<br>');

    if (sources?.length) {
        const srcDiv = document.createElement('div');
        srcDiv.className = 'sources';
        srcDiv.textContent = `Sources: ${sources.join(', ')}`;
        msg.appendChild(srcDiv);
    }

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
}

// ── Utilities ──
function docTypeIcon(type) {
    const icons = {
        PURCHASE_ORDER: '📄', INVOICE: '🧾', EXECUTION_PROOF: '✅',
        MAF_OEM_AUTH: '🏭', UDYAM_MSME: '🏢', GST_CERTIFICATE: '📋',
        PAN_CARD: '🪪', ISO_CERTIFICATE: '🏅', BID_SECURITY: '🔒',
        BOQ_COMPLIANCE: '📊', SELF_DECLARATION: '✍️', TURNOVER_CERT: '💰',
        ATC_ACCEPTANCE: '📝', GEM_BID_COPY: '📑', OTHER: '📎',
    };
    return icons[type] || '📎';
}

function formatBytes(bytes) {
    if (!bytes) return '—';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(0) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}
