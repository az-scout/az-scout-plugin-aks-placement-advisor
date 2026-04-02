/* ===================================================================
   AKS Placement Advisor – Tab JS
   Delegates to shared components from window.azScout.components
   =================================================================== */
/* global apiFetch, escapeHtml, formatNum, tenantQS, subscriptions,
          regions, showPanel, showError, hideError */

(function () {
    "use strict";

    // ---- State ----
    let lastSkus = null;
    let currentPoolType = "";

    // ---- Shared components ----
    const C = window.azScout && window.azScout.components ? window.azScout.components : {};

    // ---- DOM refs (lazy) ----
    function el(id) { return document.getElementById(id); }

    /**
     * Normalise a recommendation object from the API into the shape the
     * table renderer expects (matching SkuDict + aks eligibility).
     */
    function _normalizeRec(rec) {
        return {
            name: rec.skuName || "",
            family: rec.family || "",
            series: (rec.skuName || "").replace(/^(?:Standard|Basic)_/, "").replace(/[^A-Z].*/i, "").toUpperCase(),
            zones: rec.zones || [],
            restrictions: [],
            capabilities: {
                vCPUs: String(rec.vcpus || ""),
                MemoryGB: String(rec.memoryGb || ""),
            },
            quota: rec.quotaAvailable != null ? { remaining: rec.quotaAvailable } : {},
            confidence: rec.confidence ? { score: rec.score, label: rec.confidence } : null,
            aks: rec.aks || {},
            fallbackSkus: rec.fallbackSkus || [],
        };
    }

    /**
     * Combine the custom status select filter with the shared column filters.
     * A row is visible only if it passes both the status select AND text/numeric filters.
     */
    function _applyAllFilters(tableEl, filterRow) {
        // First let the shared component apply text/numeric filters
        if (C.applyColumnFilters) C.applyColumnFilters(tableEl, filterRow);
        // Then hide rows that don't match the status select
        tableEl.querySelectorAll("tbody tr").forEach(function (row) {
            if (row.dataset.statusHidden === "1") row.style.display = "none";
        });
    }

    // ---- Subscription combobox ----
    function renderSubDropdown(filter) {
        const dropdown = el("aks-sub-dropdown");
        if (!dropdown) return;
        const lc = (filter || "").toLowerCase();
        const matches = lc
            ? subscriptions.filter(function (s) { return s.name.toLowerCase().includes(lc) || s.id.toLowerCase().includes(lc); })
            : subscriptions;
        dropdown.innerHTML = matches.map(function (s) {
            return '<li class="dropdown-item" data-value="' + s.id + '">' + escapeHtml(s.name) + ' <span class="region-name">(' + s.id.slice(0, 8) + '\u2026)</span></li>';
        }).join("");
        dropdown.querySelectorAll("li").forEach(function (li) {
            li.addEventListener("click", function () {
                el("aks-sub-select").value = li.dataset.value;
                el("aks-sub-search").value = li.textContent.trim();
                dropdown.classList.remove("show");
                updateToggleState();
                if (canLoad()) loadSkus();
            });
        });
    }

    function initSubCombobox() {
        const search = el("aks-sub-search");
        const dropdown = el("aks-sub-dropdown");
        if (!search || !dropdown) return;

        search.addEventListener("focus", function () {
            search.select();
            renderSubDropdown(search.value.includes("(") ? "" : search.value);
            dropdown.classList.add("show");
        });
        search.addEventListener("input", function () {
            el("aks-sub-select").value = "";
            renderSubDropdown(search.value);
            dropdown.classList.add("show");
        });
        search.addEventListener("keydown", function (e) {
            const items = dropdown.querySelectorAll("li");
            const active = dropdown.querySelector("li.active");
            let idx = Array.from(items).indexOf(active);
            if (e.key === "ArrowDown") {
                e.preventDefault();
                if (!dropdown.classList.contains("show")) dropdown.classList.add("show");
                if (active) active.classList.remove("active");
                idx = (idx + 1) % items.length;
                if (items[idx]) { items[idx].classList.add("active"); items[idx].scrollIntoView({ block: "nearest" }); }
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                if (active) active.classList.remove("active");
                idx = idx <= 0 ? items.length - 1 : idx - 1;
                if (items[idx]) { items[idx].classList.add("active"); items[idx].scrollIntoView({ block: "nearest" }); }
            } else if (e.key === "Enter") {
                e.preventDefault();
                if (active) active.click();
                else if (items.length === 1) items[0].click();
            } else if (e.key === "Escape") {
                dropdown.classList.remove("show");
                search.blur();
            }
        });
        document.addEventListener("click", function (e) {
            if (!e.target.closest("#aks-sub-combobox")) dropdown.classList.remove("show");
        });
    }

    // ---- Pool type toggle ----
    function updateToggleState() {
        const region = el("region-select") && el("region-select").value;
        const sub = el("aks-sub-select") && el("aks-sub-select").value;
        const disabled = !region || !sub;
        const toggle = document.querySelector(".aks-pool-toggle");
        const btns = document.querySelectorAll('[name="aks-pool-type"]');
        btns.forEach(function (btn) { btn.disabled = disabled; });
        if (toggle) {
            toggle.classList.toggle("aks-pool-toggle-disabled", disabled);
            const missing = [];
            if (!region) missing.push("region");
            if (!sub) missing.push("subscription");
            const tip = disabled ? "Select a " + missing.join(" and ") + " first" : "";
            // Bootstrap tooltip
            const existing = bootstrap.Tooltip.getInstance(toggle);
            if (existing) existing.dispose();
            if (tip) {
                new bootstrap.Tooltip(toggle, { title: tip, placement: "bottom", trigger: "hover" });
            }
        }
    }

    function initPoolToggle() {
        const btns = document.querySelectorAll('[name="aks-pool-type"]');
        btns.forEach(function (btn) {
            btn.addEventListener("change", function () {
                currentPoolType = btn.value;
                document.querySelectorAll('.aks-pool-toggle label').forEach(function (l) {
                    l.classList.remove('active');
                });
                btn.nextElementSibling.classList.add('active');
                loadSkus();
            });
        });
        updateToggleState();
    }

    // ---- Load ----
    function canLoad() {
        const region = el("region-select") && el("region-select").value;
        const sub = el("aks-sub-select") && el("aks-sub-select").value;
        return !!(region && sub && currentPoolType);
    }

    async function loadSkus() {
        if (!canLoad()) return;
        const region = el("region-select").value;
        const sub = el("aks-sub-select").value;

        showPanel("aks-pa", "loading");
        hideError("aks-pa-error");

        try {
            const data = await apiFetch(
                "/plugins/aks-placement-advisor/recommendations?region=" + encodeURIComponent(region) +
                "&subscription_id=" + encodeURIComponent(sub) +
                "&pool_type=" + encodeURIComponent(currentPoolType) +
                "&max_results=200" +
                tenantQS("&")
            );
            lastSkus = (data.recommendations || []).map(_normalizeRec);
            renderResults(lastSkus);
            showPanel("aks-pa", "results");
        } catch (err) {
            showError("aks-pa-error", err.message);
            showPanel("aks-pa", "empty");
        }
    }

    // ---- Render results table ----
    function renderResults(skus) {
        const tableEl = el("aks-pa-table");
        const tbody = el("aks-pa-tbody");
        if (!tbody || !tableEl) return;

        // Remove any existing filter row before re-rendering
        const oldFilter = tableEl.querySelector(".datatable-filter-row");
        if (oldFilter) oldFilter.remove();

        const poolType = currentPoolType;
        const showPricing = skus.some(function (s) { return s.pricing; });
        const priceCurrency = (skus.find(function (s) { return s.pricing; }) || {}).pricing?.currency || "USD";

        // Sort: eligible first, warning second, ineligible last, then by name
        const sorted = skus.slice().sort(function (a, b) {
            const statusOrder = { eligible: 0, warning: 1, ineligible: 2 };
            const sa = statusOrder[(a.aks || {}).status] ?? 2;
            const sb = statusOrder[(b.aks || {}).status] ?? 2;
            if (sa !== sb) return sa - sb;
            return (a.name || "").localeCompare(b.name || "");
        });

        // Build thead
        const headers = ["Status", "SKU Name", "Series", "vCPUs", "Memory (GB)",
            "Quota Limit", "Quota Used", "Quota Rem.", "Confidence"];
        if (showPricing) headers.push("PAYGO " + escapeHtml(priceCurrency) + "/h", "Spot " + escapeHtml(priceCurrency) + "/h");
        headers.push("Zones", "Issues");

        let headHtml = "<tr>";
        headers.forEach(function (h) { headHtml += "<th>" + h + "</th>"; });
        headHtml += "</tr>";
        tableEl.querySelector("thead").innerHTML = headHtml;

        // Build tbody
        let html = "";
        for (const sku of sorted) {
            const aks = sku.aks || {};
            const caps = sku.capabilities || {};
            const series = sku.series || "";
            const status = aks.status || "ineligible";
            const errors = aks.errors || [];
            const warns = aks.warnings || [];
            const quota = sku.quota || {};

            const badges = {
                eligible: '<span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>Eligible</span>',
                warning: '<span class="badge bg-warning text-dark"><i class="bi bi-exclamation-triangle me-1"></i>Warning</span>',
                ineligible: '<span class="badge bg-danger"><i class="bi bi-x-circle me-1"></i>Ineligible</span>',
            };
            const statusBadge = badges[status] || badges.ineligible;

            let issuesHtml = "";
            if (errors.length) {
                issuesHtml += errors.map(function (e) {
                    return '<span class="badge bg-danger me-1 mb-1">' + escapeHtml(e) + '</span>';
                }).join("");
            }
            if (warns.length) {
                issuesHtml += warns.map(function (w) {
                    return '<span class="badge bg-warning text-dark me-1 mb-1">' + escapeHtml(w) + '</span>';
                }).join("");
            }

            // Confidence badge
            let confHtml = "\u2014";
            let confSort = -1;
            if (sku.confidence && sku.confidence.score != null) {
                confSort = sku.confidence.score;
                if (C.renderConfidenceBadge) {
                    confHtml = C.renderConfidenceBadge(sku.confidence);
                } else {
                    confHtml = escapeHtml(sku.confidence.score + " " + (sku.confidence.label || ""));
                }
            }

            // SKU detail button
            const detailBtn = '<button type="button" class="sku-name-btn" data-action="detail" data-sku="' +
                escapeHtml(sku.name) + '">' + escapeHtml(sku.name) + '</button>';

            html += '<tr>';
            html += '<td>' + statusBadge + '</td>';
            html += '<td>' + detailBtn + '</td>';
            html += '<td>' + escapeHtml(series) + '</td>';
            html += '<td>' + escapeHtml(caps.vCPUs || "\u2014") + '</td>';
            html += '<td>' + escapeHtml(caps.MemoryGB || "\u2014") + '</td>';
            html += '<td>' + (quota.limit != null ? quota.limit : "\u2014") + '</td>';
            html += '<td>' + (quota.used != null ? quota.used : "\u2014") + '</td>';
            html += '<td>' + (quota.remaining != null ? quota.remaining : "\u2014") + '</td>';
            html += '<td data-sort="' + confSort + '">' + confHtml + '</td>';
            if (showPricing) {
                const pricing = sku.pricing || {};
                html += '<td class="price-cell">' + (pricing.paygo != null ? formatNum(pricing.paygo, 4) : "\u2014") + '</td>';
                html += '<td class="price-cell">' + (pricing.spot != null ? formatNum(pricing.spot, 4) : "\u2014") + '</td>';
            }
            html += '<td>' + (sku.zones || []).join(", ") + '</td>';
            html += '<td class="aks-issues-cell">' + (issuesHtml || '<span class="text-success">\u2714</span>') + '</td>';
            html += '</tr>';
        }

        tbody.innerHTML = html;

        // Build column filters using shared component
        // Cols: 0=Status, 1=Name, 2=Series, 3=vCPUs, 4=Memory, 5=QLimit, 6=QUsed, 7=QRem, 8=Confidence
        // Status (col 0) uses a custom <select> instead of text filter
        const filterableCols = [1, 2, 3, 4, 5, 6, 7, 8];
        const numericCols = new Set([3, 4, 5, 6, 7, 8]);
        if (showPricing) {
            filterableCols.push(9, 10); // PAYGO, Spot
            numericCols.add(9);
            numericCols.add(10);
        }
        if (C.buildColumnFilters) {
            const filterRow = C.buildColumnFilters(tableEl, filterableCols, numericCols);
            // Replace the empty Status filter cell (col 0) with a <select>
            if (filterRow) {
                const statusTd = filterRow.children[0];
                if (statusTd) {
                    const select = document.createElement("select");
                    select.className = "datatable-column-filter";
                    select.innerHTML =
                        '<option value="">All</option>' +
                        '<option value="eligible">Eligible</option>' +
                        '<option value="warning">Warning</option>' +
                        '<option value="ineligible">Ineligible</option>';
                    select.dataset.col = "0";
                    select.addEventListener("change", function () {
                        const val = select.value.toLowerCase();
                        tableEl.querySelectorAll("tbody tr").forEach(function (row) {
                            if (!val) { row.dataset.statusHidden = ""; return; }
                            const cell = row.querySelector("td");
                            const text = cell ? cell.textContent.trim().toLowerCase() : "";
                            // Exact match to avoid "eligible" matching "ineligible"
                            row.dataset.statusHidden = (text === val) ? "" : "1";
                        });
                        _applyAllFilters(tableEl, filterRow);
                    });
                    statusTd.appendChild(select);
                }
                // Override the shared filter row input handler to also re-apply status filter
                filterRow.addEventListener("input", function () {
                    setTimeout(function () { _applyAllFilters(tableEl, filterRow); }, 250);
                });
            }
        }

        // SKU detail click handler
        tbody.querySelectorAll('[data-action="detail"]').forEach(function (btn) {
            btn.addEventListener("click", function () {
                openSkuDetail(btn.dataset.sku);
            });
        });

        // Update summary
        const countByStatus = { eligible: 0, warning: 0, ineligible: 0 };
        skus.forEach(function (s) { const st = (s.aks || {}).status || "ineligible"; countByStatus[st] = (countByStatus[st] || 0) + 1; });
        const summary = el("aks-pa-summary");
        if (summary) {
            summary.innerHTML =
                '<span class="badge bg-success me-1">' + countByStatus.eligible + ' eligible</span>' +
                '<span class="badge bg-warning text-dark me-1">' + countByStatus.warning + ' warning</span>' +
                '<span class="badge bg-danger me-1">' + countByStatus.ineligible + ' ineligible</span>' +
                ' <span class="text-body-secondary">/ ' + skus.length + ' total (' + escapeHtml(poolType) + ' pool)</span>';
        }

        // CSV button visibility
        const csvBtn = el("aks-pa-csv-btn");
        if (csvBtn) csvBtn.classList.remove("d-none");
    }

    // ---- SKU detail modal (delegates to shared component) ----
    function openSkuDetail(skuName) {
        const enriched = (lastSkus || []).find(function (s) { return s.name === skuName; }) || {};
        C.showSkuDetailModal(skuName, {
            region: el("region-select").value,
            subscriptionId: el("aks-sub-select").value,
            enrichedSku: enriched,
            extraSections: function (_data, _enriched) {
                return _renderAksEligibilitySection(_enriched);
            },
        });
    }

    function _renderAksEligibilitySection(enriched) {
        const aks = enriched.aks;
        if (!aks) return "";
        const statusBadges = {
            eligible: '<span class="badge bg-success"><i class="bi bi-check-circle me-1"></i>Eligible</span>',
            warning: '<span class="badge bg-warning text-dark"><i class="bi bi-exclamation-triangle me-1"></i>Warning</span>',
            ineligible: '<span class="badge bg-danger"><i class="bi bi-x-circle me-1"></i>Ineligible</span>',
        };
        let body = '';
        body += '<div class="vm-profile-row"><span class="vm-profile-label">Status</span><span>' + (statusBadges[aks.status] || "") + '</span></div>';
        body += '<div class="vm-profile-row"><span class="vm-profile-label">Pool Type</span><span>' + escapeHtml(aks.pool_type || "") + '</span></div>';
        if (aks.errors && aks.errors.length) {
            aks.errors.forEach(function (e) {
                body += '<div class="vm-profile-row"><span class="vm-profile-label text-danger">Error</span><span class="small">' + escapeHtml(e) + '</span></div>';
            });
        }
        if (aks.warnings && aks.warnings.length) {
            aks.warnings.forEach(function (w) {
                body += '<div class="vm-profile-row"><span class="vm-profile-label text-warning">Warning</span><span class="small">' + escapeHtml(w) + '</span></div>';
            });
        }
        return _accordion("aksEligibility", "bi-gpu-card", "AKS Node Pool Eligibility", body);
    }

    // Expose _accordion helper locally (mirrors shared component pattern)
    function _accordion(id, icon, title, body) {
        return '<div class="accordion mt-3" id="' + id + 'Accordion">' +
            '<div class="accordion-item">' +
            '<h2 class="accordion-header">' +
            '<button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#' + id + 'Panel" aria-expanded="false">' +
            '<i class="bi ' + icon + ' me-2"></i>' + escapeHtml(title) +
            '</button></h2>' +
            '<div id="' + id + 'Panel" class="accordion-collapse collapse">' +
            '<div class="accordion-body p-2">' + body + '</div></div></div></div>';
    }

    // ---- CSV Export ----
    function exportCsv() {
        if (!lastSkus) return;
        const rows = [["Status", "SKU", "Series", "vCPUs", "Memory (GB)",
            "Quota Limit", "Quota Used", "Quota Remaining",
            "Confidence", "PAYGO/h", "Spot/h", "Zones", "Errors", "Warnings"]];
        for (const sku of lastSkus) {
            const aks = sku.aks || {};
            const caps = sku.capabilities || {};
            const quota = sku.quota || {};
            const pricing = sku.pricing || {};
            const conf = sku.confidence || {};
            rows.push([
                aks.eligible ? "Eligible" : "Ineligible",
                sku.name || "",
                sku.series || "",
                caps.vCPUs || "",
                caps.MemoryGB || "",
                quota.limit != null ? quota.limit : "",
                quota.used != null ? quota.used : "",
                quota.remaining != null ? quota.remaining : "",
                conf.score != null ? conf.score + " (" + (conf.label || "") + ")" : "",
                pricing.paygo != null ? pricing.paygo : "",
                pricing.spot != null ? pricing.spot : "",
                (sku.zones || []).join("; "),
                (aks.errors || []).join("; "),
                (aks.warnings || []).join("; "),
            ]);
        }
        if (typeof downloadCSV === "function") {
            downloadCSV(rows, "aks-pa-eligibility.csv");
        }
    }

    // ---- Init ----
    function init() {
        // Inject tab HTML into the plugin container
        const container = el("plugin-tab-aks-placement-advisor");
        if (container) {
            container.innerHTML = `
                <div class="d-flex flex-wrap align-items-center gap-3 mb-3">
                    <!-- Subscription combobox -->
                    <div id="aks-sub-combobox" class="position-relative" style="min-width:260px;">
                        <input type="hidden" id="aks-sub-select">
                        <input type="text" class="form-control form-control-sm" id="aks-sub-search"
                               placeholder="Select subscription\u2026" autocomplete="off">
                        <ul id="aks-sub-dropdown" class="dropdown-menu w-100" style="max-height:200px;overflow-y:auto;"></ul>
                    </div>

                    <!-- Pool type toggle -->
                    <div class="aks-pool-toggle">
                        <input type="radio" name="aks-pool-type" id="aks-pool-system" value="system">
                        <label for="aks-pool-system">System pool</label>
                        <input type="radio" name="aks-pool-type" id="aks-pool-user" value="user">
                        <label for="aks-pool-user">User pool</label>
                    </div>

                    <button id="aks-pa-csv-btn" class="btn btn-sm btn-outline-secondary d-none">
                        <i class="bi bi-download me-1"></i>CSV
                    </button>

                    <span id="aks-pa-summary"></span>
                </div>

                <div class="alert alert-danger d-none" id="aks-pa-error"></div>

                <!-- Empty state -->
                <div id="aks-pa-empty" class="text-center text-body-secondary py-5">
                    <i class="bi bi-gpu-card" style="font-size:2rem;opacity:0.5"></i>
                    <p class="mt-2">Select a subscription and choose <strong>System pool</strong> or <strong>User pool</strong> to analyse SKU eligibility for Azure Kubernetes Service (AKS) use.</p>
                </div>

                <!-- Loading -->
                <div id="aks-pa-loading" class="d-none text-center py-5">
                    <div class="spinner-border text-primary" role="status"></div>
                    <p class="mt-2 text-body-secondary">Loading SKUs and checking AKS eligibility\u2026</p>
                </div>

                <!-- Results -->
                <div id="aks-pa-results" class="d-none">
                    <div class="table-responsive">
                        <table id="aks-pa-table" class="table table-sm table-hover align-middle sku-table">
                            <thead></thead>
                            <tbody id="aks-pa-tbody"></tbody>
                        </table>
                    </div>
                </div>
            `;
        }

        initSubCombobox();
        initPoolToggle();
        renderSubDropdown("");

        const csvBtn = el("aks-pa-csv-btn");
        if (csvBtn) csvBtn.addEventListener("click", exportCsv);

        // React to context changes
        document.addEventListener("azscout:subscriptions-loaded", function () {
            renderSubDropdown("");
            updateToggleState();
        });
        document.addEventListener("azscout:region-changed", function () {
            updateToggleState();
            if (canLoad()) loadSkus();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
