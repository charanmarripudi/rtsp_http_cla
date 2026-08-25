// Updated: 2026-05-28 10:00:00
class LocationDashboardTemplates {
    static text(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    static locationCard(loc, locIdx, cameras) {
        const deviceStatus = loc.device_status || "offline";
        return `
            <div class="widget-header clickable">
                <div class="widget-title"><h3>${this.text(loc.location || `Location ${locIdx + 1}`)}</h3></div>
                <div class="location-summary">
                    <span class="location-chip">${this.text(loc.device_id || "No device id")}</span>
                    <span>${this.text(loc.device_ip || "No IP")}</span>
                    <span class="status-indicator ${deviceStatus === "online" ? "active" : "offline"}">${this.text(deviceStatus).toUpperCase()}</span>
                    <span>${cameras.length} camera(s)</span>
                </div>
                <span class="expand-arrow">▼</span>
            </div>
            <div class="widget-body">
                <div class="location-table">
                    <div class="location-row header"><span>Camera</span><span>RTSP</span><span>Models</span><span>Action</span></div>
                    <div class="camera-editor"></div>
                </div>
                <div class="widget-actions">
                    <button class="btn-secondary add-camera">+ Add Camera</button>
                    <button class="btn-primary save-cameras">Save Cameras</button>
                </div>
                <div class="camera-live-grid">${cameras.map(stream => this.cameraBox(stream)).join("")}</div>
            </div>`;
    }

    static cameraBox(stream) {
        const id = Number(stream.id);
        return `
            <div class="box" id="cam-box-${id}" data-camera-id="${id}">
                <div class="video-wrap">
                    <div class="mode-badge raw" id="badge-${id}">RAW</div>
                    <video id="v${id}" controls autoplay muted playsinline data-src="${this.text(stream.hls_live)}"></video>
                    <div class="cam-label">${this.text(stream.location || stream.label || `Camera ${id + 1}`)}</div>
                </div>
                <div class="controls">
                    <div class="assignment-panel">
                        <div><span style="font-size:0.65rem;color:var(--muted);display:block;margin-bottom:6px;font-weight:600;letter-spacing:0.5px;">ASSIGNED MODELS</span><div class="assigned-chips-list" id="chips-${id}" style="display:flex;flex-direction:column;gap:8px;"></div></div>
                    </div>
                    <div class="btn-row" style="margin-top:10px;">
                        <button class="start">Start</button>
                        <button class="stop">Stop</button>
                    </div>
                    <button class="btn-remove-camera-card" data-stream-id="${id}" style="width:100%;margin-top:8px;background:rgba(255,65,85,0.12);color:var(--danger);border:1px solid rgba(255,65,85,0.4);border-radius:6px;padding:8px;font-size:0.74rem;font-family:var(--mono);font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;transition:all .2s ease;">
                        ✕ Remove Camera
                    </button>
                </div>
            </div>`;
    }

    static deviceRow(dev) {
        const status = dev.device_status || "offline";
        return `
            <div class="item-row" style="flex-direction:column;align-items:stretch;gap:6px;">
                <div style="display:flex;justify-content:space-between;gap:12px;">
                    <strong>${this.text(dev.location || dev.device_name)}</strong>
                    <span class="status-indicator ${status === "online" ? "active" : "offline"}">${this.text(status).toUpperCase()}</span>
                </div>
                <div style="display:flex;gap:16px;font-size:.62rem;color:var(--muted);">
                    <span>ID: <span style="color:var(--text);">${this.text(dev.device_id)}</span></span>
                    <span>IP: <span style="color:var(--text);">${this.text(dev.device_ip || "N/A")}</span></span>
                </div>
            </div>`;
    }
}

class LocationDashboardApi {
    async loadAll() {
        const [locRes, streamRes, modelRes, cameraModelRes] = await Promise.all([
            fetch("/api/locations"),
            fetch("/api/streams"),
            fetch("/api/models"),
            fetch("/api/camera-models")
        ]);
        return {
            locations: locRes.ok ? (await locRes.json()).locations || [] : [],
            streams: streamRes.ok ? await streamRes.json() : [],
            models: modelRes.ok ? (await modelRes.json()).models || [] : [],
            cameraModels: cameraModelRes.ok ? await cameraModelRes.json() : {}
        };
    }

    async saveLocations(payload) {
        const res = await fetch("/api/locations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (!res.ok) throw new Error(await res.text());
    }

    async saveCameras(streamPayload, cameraModels) {
        await fetch("/api/streams", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(streamPayload)
        });
        await fetch("/api/camera-models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(cameraModels)
        });
    }

    async devices() {
        const res = await fetch("/api/devices");
        if (!res.ok) return [];
        return (await res.json()).devices || [];
    }
}

class LocationDashboardTabs {
    constructor(onDashboardOpen) {
        this.onDashboardOpen = onDashboardOpen;
    }

    bind() {
        document.querySelectorAll(".tab-btn").forEach(btn => {
            btn.addEventListener("click", () => this.activate(btn));
        });
    }

    activate(btn) {
        const tabId = btn.dataset.tab;
        document.querySelectorAll(".tab-btn").forEach(item => item.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".tab-content").forEach(content => {
            content.style.display = content.id === `tab-${tabId}` ? (tabId === "dashboard" ? "flex" : "block") : "none";
        });
        if (tabId === "dashboard") this.onDashboardOpen();
    }
}

class LocationDashboardStore {
    constructor() {
        this.locations = [];
        this.streams = [];
        this.allModels = [];
        this.cameraModels = {};
    }

    load(data) {
        this.locations = Array.isArray(data.locations) ? data.locations : [];
        this.streams = data.streams || [];
        this.allModels = data.models || [];
        this.cameraModels = data.cameraModels || {};
        this.seedLocationsFromStreams();
    }

    loadOffline() {
        this.locations = JSON.parse(localStorage.getItem("offline_locations") || "[]");
        this.streams = JSON.parse(localStorage.getItem("offline_streams") || "[]").map((item, idx) => this.streamView(item, idx));
        this.cameraModels = JSON.parse(localStorage.getItem("offline_camera_models") || "{}");
        this.allModels = ["ppe_new.pt", "nik_ppe_best.pt", "firehose.pt", "sand_ext_chocks.pt", "fire_smoke.pt", "construction.pt", "tape.pt", "tyre.pt", "spillage.pt"];
    }

    seedLocationsFromStreams() {
        if (!this.streams.length) return;
        const seen = new Set(this.locations.map(l => l.location.toLowerCase()));
        const seenIds = new Set(this.locations.map(l => l.id));

        this.streams.forEach((stream, idx) => {
            const name = stream.location || `Location ${idx + 1}`;
            const id = stream.location_id || `loc-${this.locations.length + 1}`;

            if (seen.has(name.toLowerCase()) || seenIds.has(id)) return;

            seen.add(name.toLowerCase());
            seenIds.add(id);
            this.locations.push({
                id: id,
                location: name,
                device_id: stream.device_id || "",
                device_ip: stream.device_ip || "",
                device_status: stream.device_status || "offline"
            });
        });
    }

    streamView(item, idx) {
        return {
            id: idx,
            label: `Camera ${idx + 1}`,
            hls_live: `/hls/camera/${idx}/playlist.m3u8`,
            hls_raw: `/hls/stream${idx}_raw/playlist.m3u8`,
            hls_detected: `/hls/stream${idx}_detected/playlist.m3u8`,
            ...item
        };
    }

    locationId(idx, item) {
        return item.id || `loc-${idx + 1}`;
    }

    locationPayloads() {
        return this.locations.map((item, idx) => ({
            id: this.locationId(idx, item),
            location: item.location || `Location ${idx + 1}`,
            device_id: item.device_id || "",
            device_ip: item.device_ip || "",
            device_status: item.device_status || "offline"
        }));
    }

    cameraPayload(item, idx) {
        const loc = this.locations.find(l => l.id === item.location_id || l.location === item.location) || {};
        return {
            id: idx,
            rtsp: item.rtsp || "",
            location: loc.location || item.location || `Location ${idx + 1}`,
            location_id: loc.id || item.location_id || `loc-${idx + 1}`,
            device_id: loc.device_id || item.device_id || "",
            device_ip: loc.device_ip || item.device_ip || "",
            device_status: loc.device_status || item.device_status || "offline",
            conf: item.conf !== undefined && item.conf !== null ? parseFloat(item.conf) : 0.40,
            iou: item.iou !== undefined && item.iou !== null ? parseFloat(item.iou) : 0.45,
            model_configs: item.model_configs || {}
        };
    }
}

class LocationDashboard {
    constructor() {
        this.api = new LocationDashboardApi();
        this.store = new LocationDashboardStore();
        this.tabs = new LocationDashboardTabs(() => {
            this.renderLocationWidgets();
            this.renderDevices();
        });
        this.dot = document.getElementById("status-dot");
        this.label = document.getElementById("status-label");
        this.loader = document.getElementById("loading");
        this.locationList = document.getElementById("locations-edit-list");
        this.locationWidgets = document.getElementById("location-widgets");
        this.alertList = document.getElementById("alert-list");
        this.expandedLocs = new Set();
    }

    get locations() { return this.store.locations; }
    get streams() { return this.store.streams; }
    set streams(value) { this.store.streams = value; }
    get allModels() { return this.store.allModels; }
    get cameraModels() { return this.store.cameraModels; }

    async init() {
        this.tabs.bind();
        this.bindTopActions();
        await this.loadState();
        this.renderLocationForm();
        this.renderLocationWidgets();
        this.renderDevices();
    }

    escapeHtml(value) {
        return LocationDashboardTemplates.text(value);
    }

    locationId(idx, item) {
        return this.store.locationId(idx, item);
    }

    async loadState() {
        try {
            const data = await this.api.loadAll();
            this.store.load(data);
            this.dot.className = "ok";
            this.label.textContent = `${this.locations.length} location(s), ${this.streams.length} camera(s)`;
        } catch (err) {
            console.warn("Dashboard load failed, using local fallback:", err);
            this.store.loadOffline();
            this.dot.className = "err";
            this.label.textContent = "Offline Mode";
        } finally {
            if (this.loader) this.loader.classList.add("gone");
        }
    }

    bindTopActions() {
        document.getElementById("btn-add-location").addEventListener("click", () => {
            this.locations.push({
                id: `loc-${Date.now()}`,
                location: `Location ${this.locations.length + 1}`,
                device_id: "",
                device_ip: "",
                device_status: "offline"
            });
            this.renderLocationForm();
            this.renderLocationWidgets();
        });
        document.getElementById("btn-save-locations").addEventListener("click", () => this.saveLocations(true));
        document.getElementById("btn-refresh-devices").addEventListener("click", () => this.renderDevices());
    }

    async saveLocations(shouldReload = false) {
        const payload = this.store.locationPayloads();
        localStorage.setItem("offline_locations", JSON.stringify(payload));

        const btn = document.getElementById("btn-save-locations");
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Saving...";
        }

        try {
            await this.api.saveLocations(payload);
            if (shouldReload) {
                alert("Locations saved and verified successfully.");
                window.location.reload();
            }
        } catch (err) {
            console.error("Location save error:", err);
            alert("Error saving locations: " + err.message);
            if (btn) {
                btn.disabled = false;
                btn.textContent = "Save Locations";
            }
        }
    }

    async saveCameras(shouldReload = true) {
        const payload = this.streams
            .filter(item => (item.rtsp || "").trim())
            .map((item, idx) => {
                const camBox = document.getElementById(`cam-box-${item.id}`);
                let modelConfigs = item.model_configs || {};
                if (camBox) {
                    const domConfigs = {};
                    camBox.querySelectorAll(".model-card-box").forEach(card => {
                        const mName = card.getAttribute("data-model");
                        const cSlider = card.querySelector(".model-conf-slider");
                        const iSlider = card.querySelector(".model-iou-slider");
                        if (mName && cSlider && iSlider) {
                            const cVal = parseFloat(cSlider.value);
                            const iVal = parseFloat(iSlider.value);
                            const clean = mName.replace(".pt", "");
                            domConfigs[mName] = { conf: cVal, iou: iVal };
                            domConfigs[clean] = { conf: cVal, iou: iVal };
                        }
                    });
                    if (Object.keys(domConfigs).length) {
                        modelConfigs = domConfigs;
                        item.model_configs = modelConfigs;
                    }
                }
                const p = this.store.cameraPayload(item, idx);
                p.model_configs = modelConfigs;
                return p;
            });
        localStorage.setItem("offline_streams", JSON.stringify(payload));
        localStorage.setItem("offline_camera_models", JSON.stringify(this.cameraModels));
        await this.api.saveCameras(payload, this.cameraModels);
        if (shouldReload) window.location.reload();
    }

    renderLocationForm() {
        this.locationList.innerHTML = "";
        if (!this.locations.length) {
            this.locationList.innerHTML = '<div style="color:var(--muted); font-size:.7rem;">No locations added. Click "+ Add Location".</div>';
            return;
        }
        this.locations.forEach((item, idx) => this.locationList.appendChild(this.locationFormRow(item, idx)));
    }

    locationFormRow(item, idx) {
        const row = document.createElement("div");
        row.className = "location-config-fields";
        const status = item.device_status || "offline";
        row.innerHTML = `
            <span>Location ${idx + 1}</span>
            <input class="loc-name" value="${this.escapeHtml(item.location)}" placeholder="Bangalore">
            <input class="loc-device-id" value="${this.escapeHtml(item.device_id)}" placeholder="Device ID">
            <input class="loc-device-ip" value="${this.escapeHtml(item.device_ip)}" placeholder="Device IP">
            <span class="status-indicator ${status === "online" ? "active" : "offline"}" style="font-size:0.6rem; min-width:70px; text-align:center;">${status.toUpperCase()}</span>
            <button class="btn-remove">Remove</button>`;
        row.querySelector(".loc-name").addEventListener("input", e => { item.location = e.target.value; });
        row.querySelector(".loc-device-id").addEventListener("input", e => { item.device_id = e.target.value; });
        row.querySelector(".loc-device-ip").addEventListener("input", e => { item.device_ip = e.target.value; });
        row.querySelector(".btn-remove").addEventListener("click", () => this.removeLocation(item, idx));
        return row;
    }

    removeLocation(item, idx) {
        const id = this.locationId(idx, item);
        this.locations.splice(idx, 1);
        // Also remove or unlink cameras associated with this location
        this.streams = this.streams.filter(stream => stream.location_id !== id && stream.location !== item.location);

        // Cleanup camera model mappings for deleted cameras
        const streamIds = this.streams.map(s => String(s.id));
        Object.keys(this.cameraModels).forEach(cid => {
            if (!streamIds.includes(cid)) delete this.cameraModels[cid];
        });

        this.renderLocationForm();
        this.renderLocationWidgets();

        // PERSIST BOTH to prevent re-seeding from old streams.json
        this.saveLocations(false);
        this.saveCameras(false);
    }

    renderLocationWidgets(force = false) {
        const hasRenderedCards = this.locationWidgets.querySelectorAll(".widget-card").length > 0;
        if (!force && hasRenderedCards) {
            // Location cards & video elements are already rendered and playing — keep them alive!
            return;
        }
        this.locationWidgets.innerHTML = "";
        if (!this.locations.length) {
            this.locationWidgets.innerHTML = '<div style="grid-column:1/-1;color:var(--muted);font-size:.75rem;">Add locations on the first tab to create widgets here.</div>';
            return;
        }
        this.locations.forEach((loc, locIdx) => this.locationWidgets.appendChild(this.locationCard(loc, locIdx)));
        window.dispatchEvent(new CustomEvent("rtsp-dashboard-ready"));
    }

    locationCard(loc, locIdx) {
        const locId = this.locationId(locIdx, loc);
        const cameras = this.streams.filter(stream => stream.location_id === locId || stream.location === loc.location);
        const card = document.createElement("div");
        const isExpanded = this.expandedLocs.has(locId);
        card.className = isExpanded ? "widget-card expanded" : "widget-card collapsed";
        card.innerHTML = LocationDashboardTemplates.locationCard(loc, locIdx, cameras);
        const editor = card.querySelector(".camera-editor");
        cameras.forEach(stream => editor.appendChild(this.cameraEditorRow(stream)));
        card.querySelector(".widget-header").addEventListener("click", () => {
            const isCollapsed = card.classList.contains("collapsed");
            card.classList.toggle("collapsed");
            card.classList.toggle("expanded", isCollapsed);
            if (isCollapsed) {
                this.expandedLocs.add(locId);
            } else {
                this.expandedLocs.delete(locId);
            }
        });
        card.querySelector(".add-camera").addEventListener("click", () => this.addCamera(loc, locIdx));

        card.querySelectorAll(".btn-remove-camera-card").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const targetId = Number(btn.getAttribute("data-stream-id"));
                const targetStream = this.streams.find(s => Number(s.id) === targetId);
                if (targetStream) {
                    const camLabel = targetStream.location || targetStream.label || `Camera ${targetId + 1}`;
                    if (confirm(`Remove ${camLabel}?`)) {
                        this.removeCamera(targetStream);
                    }
                }
            });
        });

        card.querySelectorAll(".box").forEach(camBox => {
            const camId = Number(camBox.getAttribute("data-camera-id"));
            const stream = this.streams.find(s => Number(s.id) === camId);
            if (!stream) return;
            const confSlider = camBox.querySelector(".conf-slider");
            const iouSlider = camBox.querySelector(".iou-slider");
            if (confSlider) {
                confSlider.addEventListener("input", e => {
                    stream.conf = parseFloat(e.target.value);
                    const valSpan = camBox.querySelector(".conf-val");
                    if (valSpan) valSpan.textContent = parseFloat(e.target.value).toFixed(2);
                });
            }
            if (iouSlider) {
                iouSlider.addEventListener("input", e => {
                    stream.iou = parseFloat(e.target.value);
                    const valSpan = camBox.querySelector(".iou-val");
                    if (valSpan) valSpan.textContent = parseFloat(e.target.value).toFixed(2);
                });
            }
        });

        card.querySelector(".save-cameras").addEventListener("click", () => this.saveCameras(true));
        return card;
    }

    cameraEditorRow(stream) {
        const row = document.createElement("div");
        row.className = "location-row";
        row.innerHTML = `
            <span>${this.escapeHtml(stream.location || stream.label || `Camera ${Number(stream.id) + 1}`)}</span>
            <input class="camera-rtsp-input" value="${this.escapeHtml(stream.rtsp || "")}" placeholder="rtsp://camera-url" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:var(--mono);font-size:.72rem;padding:7px;border-radius:6px;">
            <div class="model-assign-row" style="margin:0;">${this.modelChips(stream.id)}</div>
            <button class="btn-remove">Remove</button>`;
        row.querySelector(".camera-rtsp-input").addEventListener("input", e => { stream.rtsp = e.target.value; });
        row.querySelectorAll('input[type="checkbox"]').forEach(cb => this.bindModelCheckbox(cb, stream));
        row.querySelector(".btn-remove").addEventListener("click", () => this.removeCamera(stream));
        return row;
    }

    modelChips(cameraIndex) {
        const assigned = this.cameraModels[String(cameraIndex)] || [];
        if (!this.allModels.length) return '<span style="color:var(--muted);font-size:.68rem;">No models available</span>';
        return this.allModels.map(model => {
            const checked = assigned.includes(model);
            return `<label class="${checked ? "model-chip checked" : "model-chip"}">
                <input type="checkbox" value="${this.escapeHtml(model)}" ${checked ? "checked" : ""} data-camera="${cameraIndex}">
                ${this.escapeHtml(model.replace(".pt", ""))}
            </label>`;
        }).join("");
    }

    bindModelCheckbox(cb, stream) {
        cb.addEventListener("change", () => {
            const key = String(stream.id);
            this.cameraModels[key] = this.cameraModels[key] || [];
            if (cb.checked && !this.cameraModels[key].includes(cb.value)) this.cameraModels[key].push(cb.value);
            if (!cb.checked) this.cameraModels[key] = this.cameraModels[key].filter(model => model !== cb.value);
            cb.closest(".model-chip").classList.toggle("checked", cb.checked);
        });
    }

    addCamera(loc, locIdx) {
        const nextId = this.streams.reduce((max, item) => Math.max(max, Number(item.id) || 0), -1) + 1;

        // Start with no models pre-selected (empty list) so the user explicitly chooses models
        this.cameraModels[String(nextId)] = [];

        this.streams.push({
            id: nextId,
            label: loc.location || `Camera ${nextId + 1}`,
            rtsp: "",
            location_id: this.locationId(locIdx, loc),
            location: loc.location,
            device_id: loc.device_id,
            device_ip: loc.device_ip,
            device_status: loc.device_status,
            hls_live: `/hls/camera/${nextId}/playlist.m3u8`,
            hls_raw: `/hls/stream${nextId}_raw/playlist.m3u8`,
            hls_detected: `/hls/stream${nextId}_detected/playlist.m3u8`
        });
        this.expandedLocs.add(this.locationId(locIdx, loc));
        this.renderLocationWidgets(true);
    }

    removeCamera(stream) {
        this.streams = this.streams.filter(item => item !== stream);
        delete this.cameraModels[String(stream.id)];
        this.renderLocationWidgets(true);
        this.saveCameras(false);
    }

    async renderDevices() {
        const container = document.getElementById("devices-list-widget");
        if (!container) return;
        try {
            const devices = await this.api.devices();
            container.innerHTML = devices.length ? '<div class="item-list-widget">' + devices.map(dev => LocationDashboardTemplates.deviceRow(dev)).join("") + '</div>' :
                '<div style="color:var(--muted);font-size:.7rem;">No devices configured.</div>';
        } catch (err) {
            container.innerHTML = '<div style="color:var(--danger);font-size:.7rem;">Failed to load devices.</div>';
        }
    }
}

async function initLocationDashboard() {
    const dashboard = new LocationDashboard();
    await dashboard.init();
}

window.initLocationDashboard = initLocationDashboard;