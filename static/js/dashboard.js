(() => {
    const listEl = document.getElementById("alert-list");
    const emptyEl = document.getElementById("empty-state");
    const statusEl = document.getElementById("status");
    const form = document.getElementById("alert-form");
    const refreshBtn = document.getElementById("refresh-btn");
    const pollMs = 5000;

    const safeSeverity = (value = "low") => {
        const cleaned = String(value || "low").toLowerCase();
        return ["low", "warning", "critical"].includes(cleaned) ? cleaned : "low";
    };

    const renderAlerts = (alerts) => {
        listEl.innerHTML = "";
        if (!alerts.length) {
            const p = document.createElement("p");
            p.className = "empty";
            p.id = "empty-state";
            p.textContent = "No alerts yet. Waiting for incoming alerts…";
            listEl.appendChild(p);
            return;
        }

        alerts.forEach((alert) => {
            const severity = safeSeverity(alert.severity);
            const card = document.createElement("div");
            card.className = "alert-card";
            card.dataset.id = alert.id;

            const left = document.createElement("div");
            const meta = document.createElement("div");
            meta.className = "alert-meta";
            const pill = document.createElement("span");
            pill.className = `pill severity-${severity}`;
            pill.textContent = severity.charAt(0).toUpperCase() + severity.slice(1);
            const status = document.createElement("span");
            const statusValue = (alert.status || "new").toLowerCase();
            status.className = `pill status-${statusValue}`;
            status.textContent = statusValue.charAt(0).toUpperCase() + statusValue.slice(1);
            const ts = document.createElement("span");
            ts.className = "timestamp";
            ts.textContent = alert.created_at || "Just now";
            meta.appendChild(pill);
            meta.appendChild(status);
            meta.appendChild(ts);

            const title = document.createElement("div");
            title.className = "alert-title";
            title.textContent = alert.title;

            const message = document.createElement("p");
            message.className = "alert-message";
            message.textContent = alert.message || "";

            left.appendChild(meta);
            left.appendChild(title);
            if (alert.message) left.appendChild(message);

            const mediaContainer = document.createElement("div");
            mediaContainer.className = "alert-media";

            async function addFrames(alertId, assetType) {
                try {
                    const res = await fetch(`/api/alerts/${alertId}/assets/${assetType}`);
                    if (!res.ok) return;
                    const blob = await res.blob();
                    const jszip = new JSZip();
                    const zip = await jszip.loadAsync(blob);

                    zip.forEach(async (relPath, file) => {
                        if (file.name.endsWith(".jpg") || file.name.endsWith(".png")) {
                            const imgBlob = await file.async("blob");
                            const imgUrl = URL.createObjectURL(imgBlob);
                            const img = document.createElement("img");
                            img.src = imgUrl;
                            img.style.maxWidth = "150px";
                            img.style.margin = "3px";
                            mediaContainer.appendChild(img);
                        }
                    });
                } catch (err) {
                    console.error(`Failed to load ${assetType} for alert ${alertId}:`, err);
                }
            }

            async function addAudio(alertId) {
                try {
                    const res = await fetch(`/api/alerts/${alertId}/assets/audio`);
                    if (!res.ok) return;
                    const blob = await res.blob();
                    const audioUrl = URL.createObjectURL(blob);
                    const audioEl = document.createElement("audio");
                    audioEl.src = audioUrl;
                    audioEl.controls = true;
                    audioEl.style.display = "block";
                    audioEl.style.margin = "5px 0";
                    mediaContainer.appendChild(audioEl);
                } catch (err) {
                    console.error(`Failed to load audio for alert ${alertId}:`, err);
                }
            }

            addFrames(alert.id, "frames");
            addFrames(alert.id, "annotated_frames");
            addAudio(alert.id);

            left.appendChild(mediaContainer);

            const actions = document.createElement("div");
            actions.className = "action-stack";

            const respondBtn = document.createElement("button");
            respondBtn.className = "button ghost";
            respondBtn.textContent = "Responding";
            respondBtn.dataset.action = "status";
            respondBtn.dataset.status = "responding";
            respondBtn.dataset.id = alert.id;

            const resolveBtn = document.createElement("button");
            resolveBtn.className = "button ghost";
            resolveBtn.textContent = "Resolved";
            resolveBtn.dataset.action = "status";
            resolveBtn.dataset.status = "resolved";
            resolveBtn.dataset.id = alert.id;

            const deleteBtn = document.createElement("button");
            deleteBtn.className = "button secondary";
            deleteBtn.textContent = "Delete";
            deleteBtn.dataset.action = "delete";
            deleteBtn.dataset.id = alert.id;

            actions.appendChild(respondBtn);
            actions.appendChild(resolveBtn);
            actions.appendChild(deleteBtn);

            card.appendChild(left);
            card.appendChild(actions);
            listEl.appendChild(card);
        });
    };

    const setStatus = (text, isError = false) => {
        if (!statusEl) return;
        statusEl.textContent = text;
        statusEl.style.color = isError ? "#ff8a9a" : "#8fa2b7";
    };

    const fetchAlerts = async () => {
        try {
            const res = await fetch("/api/alerts");
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            renderAlerts(Array.isArray(data) ? data : []);
            setStatus("Listening for alerts (auto-refresh every 5s)");
        } catch (err) {
            setStatus(`Connection issue: ${err.message}`, true);
        }
    };

    const submitAlert = async (event) => {
        event.preventDefault();
        const formData = new FormData(form);
        const payload = {
            title: formData.get("title") || "",
            message: formData.get("message") || "",
            severity: formData.get("severity") || "info",
        };

        if (!payload.title.trim()) {
            setStatus("Title is required.", true);
            return; 
        }

        try {
            const res = await fetch("/api/alerts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const error = await res.json().catch(() => ({}));
                throw new Error(error.error || `HTTP ${res.status}`);
            }
            form.reset();
            setStatus("Alert created and sent.");
            fetchAlerts();
        } catch (err) {
            setStatus(`Could not create alert: ${err.message}`, true);
        }
    };

    const deleteAlert = async (id) => {
        const tryDelete = async (method, body, headers = {}) =>
            fetch(`/api/alerts/${id}`, { method, body, headers });

        try {
            // First try DELETE; some deployments block it.
            let res = await tryDelete("DELETE");
            if (!res.ok && res.status === 405) {
                // Fallback to POST
                res = await tryDelete(
                    "POST",
                    JSON.stringify({}),
                    { "Content-Type": "application/json" }
                );
            }
            if (!res.ok) {
                const error = await res.json().catch(() => ({}));
                throw new Error(error.error || `HTTP ${res.status}`);
            }
            setStatus("Alert deleted.");
            fetchAlerts();
        } catch (err) {
            setStatus(`Delete failed: ${err.message}`, true);
        }
    };

    const updateStatus = async (id, status) => {
        try {
            const res = await fetch(`/api/alerts/${id}/status`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ status }),
            });
            if (!res.ok) {
                const error = await res.json().catch(() => ({}));
                throw new Error(error.error || `HTTP ${res.status}`);
            }
            setStatus(`Status set to ${status}.`);
            fetchAlerts();
        } catch (err) {
            setStatus(`Update failed: ${err.message}`, true);
        }
    };

    // Event bindings
    form.addEventListener("submit", submitAlert);
    refreshBtn.addEventListener("click", fetchAlerts);
    listEl.addEventListener("click", (e) => {
        const btn = e.target.closest("button[data-action='delete']");
        if (btn) {
            deleteAlert(btn.dataset.id);
            return;
        }
        const statusBtn = e.target.closest("button[data-action='status']");
        if (statusBtn) {
            updateStatus(statusBtn.dataset.id, statusBtn.dataset.status);
        }
    });
    
    // Kick off polling
    fetchAlerts();
    setInterval(fetchAlerts, pollMs);
})();
