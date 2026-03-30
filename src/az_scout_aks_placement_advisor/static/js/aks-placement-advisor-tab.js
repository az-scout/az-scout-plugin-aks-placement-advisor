// AKS Placement Advisor tab logic
// Globals from app.js: apiFetch, tenantQS, subscriptions, regions, escapeHtml
(function () {
    const PLUGIN_NAME = "aks-placement-advisor";
    const container = document.getElementById("plugin-tab-" + PLUGIN_NAME);
    if (!container) return;

    // -----------------------------------------------------------------------
    // 1. Load HTML fragment
    // -----------------------------------------------------------------------
    fetch(`/plugins/${PLUGIN_NAME}/static/html/aks-placement-advisor-tab.html`)
        .then(resp => resp.text())
        .then(html => {
            container.innerHTML = html;
            initPlugin();
        })
        .catch(err => {
            container.innerHTML =
                `<div class="alert alert-danger">Failed to load plugin UI: ${err.message}</div>`;
        });

    // -----------------------------------------------------------------------
    // 2. Plugin initialisation
    // -----------------------------------------------------------------------
    function initPlugin() {
        const tenantEl    = document.getElementById("tenant-select");
        const regionEl    = document.getElementById("region-select");
        const subSelect   = document.getElementById("aks-pa-sub-select");
        const btn         = document.getElementById("aks-pa-btn");
        const resultsDiv  = document.getElementById("aks-pa-results");
        const loadingDiv  = document.getElementById("aks-pa-loading");
        const disclaimerDiv = document.getElementById("aks-pa-disclaimer");
        const disclaimerText = document.getElementById("aks-pa-disclaimer-text");

        // Filter inputs
        const minVcpusEl    = document.getElementById("aks-pa-min-vcpus");
        const minMemoryEl   = document.getElementById("aks-pa-min-memory");
        const skuFilterEl   = document.getElementById("aks-pa-sku-filter");
        const requireZonesEl = document.getElementById("aks-pa-require-zones");

        // --- Helpers --------------------------------------------------------
        function getContext() {
            const tenantId = tenantEl?.value || "";
            const region = regionEl?.value || "";
            const tenantOpt = tenantEl?.selectedOptions?.[0];
            const tenantName = tenantOpt?.text || tenantId || "—";
            const regionObj = (typeof regions !== "undefined" ? regions : [])
                .find(r => r.name === region);
            const regionName = regionObj?.displayName || region || "—";
            return { tenantId, tenantName, region, regionName };
        }

        function safeText(str) {
            if (typeof escapeHtml === "function") return escapeHtml(str);
            const div = document.createElement("div");
            div.textContent = str;
            return div.innerHTML;
        }

        // --- Subscription selector ------------------------------------------
        async function refreshSubscriptions() {
            const ctx = getContext();
            subSelect.innerHTML = "";

            if (!ctx.tenantId || !ctx.region) {
                subSelect.innerHTML = '<option value="">Select tenant &amp; region first</option>';
                subSelect.disabled = true;
                btn.disabled = true;
                resultsDiv.innerHTML = "";
                return;
            }

            subSelect.innerHTML = '<option value="">Loading…</option>';
            subSelect.disabled = true;

            try {
                const subs = await apiFetch("/api/subscriptions" + tenantQS("?"));
                subSelect.innerHTML = '<option value="">— choose —</option>';
                subs.forEach(s => {
                    const opt = document.createElement("option");
                    opt.value = s.id;
                    opt.textContent = s.name;
                    subSelect.appendChild(opt);
                });
                subSelect.disabled = false;
            } catch (e) {
                subSelect.innerHTML = `<option value="">Error: ${safeText(e.message)}</option>`;
                subSelect.disabled = true;
            }
        }

        // --- Context events -------------------------------------------------
        function onContextChanged() {
            refreshSubscriptions();
        }

        document.addEventListener("azscout:tenant-changed", onContextChanged);
        document.addEventListener("azscout:region-changed", onContextChanged);

        subSelect.addEventListener("change", () => {
            btn.disabled = !subSelect.value;
        });

        // --- Render results -------------------------------------------------
        function renderResults(data) {
            const recs = data.recommendations || [];
            if (recs.length === 0) {
                resultsDiv.innerHTML =
                    '<div class="aks-pa-empty">No recommendations found for the given filters.</div>';
                return;
            }

            let html = '<table class="aks-pa-table"><thead><tr>';
            html += "<th>SKU</th><th>vCPUs</th><th>Memory</th><th>Zones</th>";
            html += "<th>Score</th><th>Confidence</th><th>Warnings</th>";
            html += "</tr></thead><tbody>";

            for (const rec of recs) {
                const zones = (rec.zones || [])
                    .map(z => `<span class="aks-pa-zone-badge">${safeText(z)}</span>`)
                    .join(" ") || "—";

                const scoreWidth = Math.max(4, Math.min(100, rec.score));
                const scoreBar =
                    `<span class="aks-pa-score-bar" style="width:${scoreWidth}px"></span>` +
                    `${rec.score}`;

                const confClass = `aks-pa-confidence-${rec.confidence}`;
                const confidence =
                    `<span class="${confClass}">${safeText(rec.confidence)}</span>`;

                const warnings = (rec.warnings || []).length > 0
                    ? `<ul class="aks-pa-warnings">${rec.warnings.map(w => `<li>${safeText(w)}</li>`).join("")}</ul>`
                    : "—";

                html += "<tr>";
                html += `<td><strong>${safeText(rec.skuName)}</strong></td>`;
                html += `<td>${rec.vcpus}</td>`;
                html += `<td>${rec.memoryGb} GB</td>`;
                html += `<td>${zones}</td>`;
                html += `<td>${scoreBar}</td>`;
                html += `<td>${confidence}</td>`;
                html += `<td>${warnings}</td>`;
                html += "</tr>";
            }

            html += "</tbody></table>";
            resultsDiv.innerHTML = html;

            // Show disclaimer
            if (data.disclaimer) {
                disclaimerText.textContent = data.disclaimer;
                disclaimerDiv.classList.remove("d-none");
            }
        }

        // --- Fetch recommendations ------------------------------------------
        btn.addEventListener("click", async () => {
            const ctx = getContext();
            const subId = subSelect.value;

            if (!ctx.region || !subId) return;

            resultsDiv.innerHTML = "";
            disclaimerDiv.classList.add("d-none");
            loadingDiv.classList.remove("d-none");
            btn.disabled = true;

            try {
                const params = new URLSearchParams({
                    region: ctx.region,
                    subscription_id: subId,
                    require_vmss: "true",
                });

                if (ctx.tenantId) params.set("tenant_id", ctx.tenantId);

                const minV = minVcpusEl?.value;
                if (minV) params.set("min_vcpus", minV);

                const minM = minMemoryEl?.value;
                if (minM) params.set("min_memory_gb", minM);

                const skuF = skuFilterEl?.value?.trim();
                if (skuF) params.set("sku_name_filter", skuF);

                if (requireZonesEl?.checked) params.set("require_zones", "true");

                const data = await apiFetch(
                    `/plugins/${PLUGIN_NAME}/recommendations?${params}`
                );
                renderResults(data);
            } catch (e) {
                resultsDiv.innerHTML =
                    `<div class="alert alert-danger">${safeText(e.message)}</div>`;
            } finally {
                loadingDiv.classList.add("d-none");
                btn.disabled = false;
            }
        });

        // --- Initial state --------------------------------------------------
        if (getContext().tenantId && getContext().region) {
            refreshSubscriptions();
        }
    }
})();
