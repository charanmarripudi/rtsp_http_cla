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
                    <div class="camera-editor" style="display: flex; flex-direction: column; gap: 16px;"></div>
                </div>
                <div class="widget-actions" style="margin-top: 14px;">
                    <button class="btn-secondary add-camera">+ Add Camera</button>
                    <button class="btn-primary save-cameras">Save Cameras</button>
                </div>
            </div>`;
    }

    static cameraBox(stream) {
        const id = Number(stream.id);
        return `
            <div class="box" id="cam-box-${id}" data-camera-id="${id}">
                <div class="video-wrap" style="position: relative; width: 100%; aspect-ratio: 16/9; overflow: hidden; background: #000; border-radius: 6px;">
                    <div class="mode-badge raw" id="badge-${id}">RAW</div>
                    <video id="v${id}" autoplay muted playsinline data-src="${this.text(stream.hls_live)}" ondblclick="toggleCardMaximize(${id})"></video>
                    <svg class="roi-draw-canvas" id="roi-canvas-${id}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; cursor: crosshair; user-select: none; z-index: 15; pointer-events: none; background: transparent;">
                        <rect id="roi-rect-${id}" x="0" y="0" width="0" height="0" style="fill: none; stroke: #00ffaa; stroke-width: 2.5;"></rect>
                        <g id="roi-badge-${id}" style="display: none;">
                            <rect id="roi-badge-bg-${id}" x="0" y="0" width="0" height="0" rx="3" style="fill: #00ffaa;"></rect>
                            <text id="roi-badge-text-${id}" x="0" y="0" style="fill: #000; font-family: monospace; font-size: 11px; font-weight: 700; dominant-baseline: middle;"></text>
                        </g>
                    </svg>
                    <div class="cam-label">${this.text(stream.location || stream.label || `Camera ${id + 1}`)}</div>
                </div>
                <div class="controls">
                    <div class="assignment-panel">
                        <div><span style="font-size:0.65rem;color:var(--muted);display:block;margin-bottom:6px;font-weight:600;letter-spacing:0.5px;">ASSIGNED MODELS</span><div class="assigned-chips-list" id="chips-${id}" style="display:flex;flex-direction:column;gap:8px;"></div></div>
                    </div>
                    <div class="btn-row" style="margin-top:10px;display:flex;gap:8px;">
                        <button class="start" style="flex:1;">Start</button>
                        <button class="stop" style="flex:1;">Stop</button>
                        <button class="draw-roi-btn" id="roi-btn-${id}" onclick="handleRoiButtonClick(${id})" style="flex:1;background:rgba(0,240,255,0.1);border:1px solid var(--accent);color:var(--accent);border-radius:6px;padding:6px 10px;font-size:0.72rem;cursor:pointer;font-family:var(--mono);font-weight:600;">Draw ROI</button>
                    </div>
                    <button class="btn-remove-camera-card" data-stream-id="${id}" style="width:100%;margin-top:8px;background:rgba(255,65,85,0.12);color:var(--danger);border:1px solid rgba(255,65,85,0.4);border-radius:6px;padding:8px;font-size:0.74rem;font-family:var(--mono);font-weight:600;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px;transition:all .2s ease;">
                        Remove Camera
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
        const t = Date.now();
        const [locRes, streamRes, modelRes, cameraModelRes, modelClassesRes] = await Promise.all([
            fetch(`/api/locations?t=${t}`, { cache: "no-store" }),
            fetch(`/api/streams?t=${t}`, { cache: "no-store" }),
            fetch(`/api/models?t=${t}`, { cache: "no-store" }),
            fetch(`/api/camera-models?t=${t}`, { cache: "no-store" }),
            fetch(`/api/model-classes?t=${t}`, { cache: "no-store" }).catch(() => null)
        ]);
        let modelClassesData = {};
        if (modelClassesRes && modelClassesRes.ok) {
            try {
                const json = await modelClassesRes.json();
                modelClassesData = json.models || json.classes || {};
            } catch (e) {
                console.warn("Failed to parse model-classes:", e);
            }
        }
        return {
            locations: locRes.ok ? (await locRes.json()).locations || [] : [],
            streams: streamRes.ok ? await streamRes.json() : [],
            models: modelRes.ok ? (await modelRes.json()).models || [] : [],
            cameraModels: cameraModelRes.ok ? await cameraModelRes.json() : {},
            modelClasses: modelClassesData
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
        const resStreams = await fetch("/api/streams?replace_all=true", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(streamPayload)
        });
        if (!resStreams.ok) {
            throw new Error(await resStreams.text());
        }
        const resModels = await fetch("/api/camera-models", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(cameraModels)
        });
        if (!resModels.ok) {
            throw new Error(await resModels.text());
        }
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
        if (tabId === "speed" && window.initSpeedTab) window.initSpeedTab();
    }
}

class LocationDashboardStore {
    constructor() {
        this.locations = [];
        this.streams = [];
        this.allModels = [];
        this.cameraModels = {};
        this.modelClasses = {};
    }

    load(data) {
        this.locations = Array.isArray(data.locations) ? data.locations : [];
        this.streams = Array.isArray(data.streams) ? data.streams : [];
        const rawModels = Array.isArray(data.models) ? data.models : [];
        this.allModels = rawModels.filter(m => {
            const lc = String(m).toLowerCase();
            return !lc.includes("leakage") && !lc.includes("nik_ppe") && !lc.includes("ppe_new") && !lc.startsWith("fire_smoke");
        });
        this.cameraModels = (data.cameraModels && typeof data.cameraModels === "object") ? data.cameraModels : {};
        this.modelClasses = (data.modelClasses && typeof data.modelClasses === "object") ? data.modelClasses : {};
    }

    loadOffline() {
        this.locations = JSON.parse(localStorage.getItem("offline_locations") || "[]");
        this.streams = JSON.parse(localStorage.getItem("offline_streams") || "[]").map((item, idx) => this.streamView(item, idx));
        this.cameraModels = JSON.parse(localStorage.getItem("offline_camera_models") || "{}");
        this.allModels = ["hansung_ppe_violations.pt", "Final_Fire_smoke.pt", "150_firehose_best.pt", "sand_ext_chocks.pt", "construction.pt", "best_tape.pt", "tyre_final.pt", "120_spillage_best.pt", "Valve_box_best.pt", "dome_open_closed_best.pt", "yolov8n.pt", "intrusion.pt", "non_working_hours.pt", "vehicle_intrusion.pt", "unauthorized_activity.pt", "spark_arrestor.pt"];
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
    set streams(value) {
        this.store.streams = value;
        window.streamsArray = value;
    }
    get allModels() { return this.store.allModels; }
    get modelClasses() { return this.store.modelClasses || {}; }
    get cameraModels() { return this.store.cameraModels; }
    set cameraModels(value) {
        this.store.cameraModels = value;
        window.cameraModelsMap = value;
    }

    async init() {
        this.tabs.bind();
        this.bindTopActions();
        await this.loadState();
        this.renderLocationForm();
        this.renderLocationWidgets();
        this.renderDevices();
        this.startAlertPolling();
    }

    startAlertPolling() {
        if (this._alertInterval) clearInterval(this._alertInterval);
        this.pollAlerts();
        this._alertInterval = setInterval(() => this.pollAlerts(), 3000);
    }

    async pollAlerts() {
        const alertList = this.alertList || document.getElementById("alert-list");
        if (!alertList) return;

        try {
            const r = await fetch(`/api/alerts?t=${Date.now()}`);
            if (!r.ok) return;
            const alerts = await r.json();

            const vioKpi = document.getElementById("kpi-violation-count");
            if (vioKpi && Array.isArray(alerts)) {
                vioKpi.textContent = `${alerts.length} Detected`;
            }

            if (!alerts || !alerts.length) {
                alertList.innerHTML = '<div style="color:var(--muted); font-size:.7rem; text-align:center; padding:30px 10px;">Watching for safety violations...</div>';
                return;
            }

            const latest = alerts[0];
            const latestId = latest.id || latest.time || latest.created_at;
            if (latestId === this._lastAlertTime) return;
            this._lastAlertTime = latestId;

            alertList.innerHTML = "";
            const BASE = window.location.origin;

            alerts.slice(0, 50).forEach((a, idx) => {
                const card = document.createElement("div");
                card.className = "alert-card violation-card";
                card.style.cssText = "display:flex;align-items:center;justify-content:space-between;background:rgba(255,255,255,0.03);border:1px solid rgba(237,28,36,0.3);border-radius:6px;padding:8px 10px;margin-bottom:8px;cursor:pointer;transition:all 0.2s;";
                if (idx === 0) {
                    card.style.boxShadow = "0 0 12px rgba(237, 28, 36, 0.45)";
                    card.style.borderColor = "rgba(237, 28, 36, 0.7)";
                }

                const camId = a.camera_id !== undefined ? a.camera_id : (a.camera !== undefined ? a.camera : "0");
                const eventStr = a.type_of_alert || a.event || "Violation Detected";
                const rawTime = a.created_at || a.time || "";
                const timeStr = rawTime.includes(" ") ? rawTime.split(" ")[1] : rawTime;
                const locStr = a.location || `Camera ${camId}`;

                let imgUrl = a.image || a.image_path || "";
                if (imgUrl && !imgUrl.startsWith("http://") && !imgUrl.startsWith("https://")) {
                    imgUrl = `${BASE}${imgUrl.startsWith("/") ? "" : "/"}${imgUrl}`;
                }

                card.onclick = () => { if (imgUrl) window.open(imgUrl, "_blank"); };

                card.innerHTML = `
                    <div class="alert-info" style="flex:1;min-width:0;padding-right:8px;">
                        <div class="alert-meta" style="display:flex;align-items:center;gap:6px;font-size:0.68rem;margin-bottom:4px;">
                            <span class="badge-violation" style="background:#ed1c24;color:#fff;font-weight:700;font-size:0.6rem;padding:2px 6px;border-radius:3px;">VIOLATION</span>
                            <span style="font-weight:700;color:#FFFFFF;font-family:var(--mono);">CAM ${camId}</span>
                            <span style="font-size:0.62rem;color:var(--muted);margin-left:auto;">${timeStr}</span>
                        </div>
                        <div class="alert-event-title" style="font-size:0.75rem;font-weight:600;color:#f87171;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${this.escapeHtml(eventStr)}</div>
                        <div class="alert-loc-label" style="font-size:0.65rem;color:var(--muted);">${this.escapeHtml(locStr)}</div>
                    </div>
                    ${imgUrl ? `<img src="${imgUrl}" class="alert-thumb" style="width:72px;height:45px;object-fit:cover;border-radius:4px;border:1px solid rgba(255,255,255,0.15);" loading="lazy">` : ""}
                `;
                alertList.appendChild(card);
            });
        } catch (e) {
            console.error("Alert poll error:", e);
        }
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
            streamsArray = this.streams;
            this.dot.className = "ok";
            this.label.textContent = `${this.locations.length} location(s), ${this.streams.length} camera(s)`;
        } catch (err) {
            console.warn("Dashboard load failed, using local fallback:", err);
            this.store.loadOffline();
            streamsArray = this.streams;
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
        // Sync any RTSP inputs currently in DOM to prevent losing unsaved typing
        document.querySelectorAll(".camera-rtsp-input").forEach(input => {
            const camBlock = input.closest(".camera-edit-block");
            if (camBlock) {
                const camId = camBlock.getAttribute("data-camera-id");
                if (camId !== null && camId !== undefined && camId !== "") {
                    const st = this.streams.find(s => String(s.id) === String(camId));
                    if (st) {
                        st.rtsp = input.value.trim();
                    }
                }
            }
        });

        const ppeModelsList = ["ppe_new.pt", "nik_ppe_best.pt", "hf_ppe_detection.pt", "keremberke_ppe_gear.pt", "hansung_ppe_violations.pt"];
        this.streams.forEach((stream, idx) => {
            const key = String(stream.id !== undefined ? stream.id : idx);
            this.cameraModels[key] = [];

            const parentCbs = document.querySelectorAll(`.parent-model-checkbox[data-camera="${stream.id}"]:checked, .parent-model-checkbox[data-camera="${idx}"]:checked, .parent-model-checkbox[data-cam="${stream.id}"]:checked, .parent-model-checkbox[data-cam="${idx}"]:checked`);
            parentCbs.forEach(cb => {
                const m = cb.value;
                if (!this.cameraModels[key].includes(m)) {
                    this.cameraModels[key].push(m);
                }
            });

            const classCbs = document.querySelectorAll(`.class-checkbox[data-camera="${stream.id}"]:checked, .class-checkbox[data-camera="${idx}"]:checked, .class-checkbox[data-cam="${stream.id}"]:checked, .class-checkbox[data-cam="${idx}"]:checked`);
            const enabledClassesPerModel = {};
            classCbs.forEach(cb => {
                const m = cb.getAttribute("data-model");
                const cls = cb.value;
                if (m) {
                    if (!this.cameraModels[key].includes(m)) {
                        this.cameraModels[key].push(m);
                    }
                    enabledClassesPerModel[m] = enabledClassesPerModel[m] || [];
                    if (!enabledClassesPerModel[m].includes(cls)) {
                        enabledClassesPerModel[m].push(cls);
                    }
                }
            });

            stream.model_configs = stream.model_configs || {};
            const cleanModelConfigs = {};

            if (stream.model_configs.roi_polygon) {
                cleanModelConfigs.roi_polygon = stream.model_configs.roi_polygon;
            }

            this.cameraModels[key].forEach(m => {
                const normModel = m.endsWith(".pt") ? m : `${m}.pt`;
                const cleanName = m.replace(".pt", "");
                const existingCfg = stream.model_configs[normModel] || stream.model_configs[cleanName] || {};
                const activeClasses = enabledClassesPerModel[m] || enabledClassesPerModel[normModel] || enabledClassesPerModel[cleanName] || [];

                const updatedCfg = Object.assign({}, existingCfg, {
                    enabled_classes: activeClasses
                });
                cleanModelConfigs[normModel] = updatedCfg;
                cleanModelConfigs[cleanName] = updatedCfg;
            });

            stream.model_configs = cleanModelConfigs;
        });

        const payload = this.streams
            .filter(item => (item.rtsp || "").trim())
            .map((item, idx) => {
                const camBox = document.getElementById(`cam-box-${item.id}`);
                let modelConfigs = JSON.parse(JSON.stringify(item.model_configs || {}));
                if (window.roiDrawStates && window.roiDrawStates[item.id] && window.roiDrawStates[item.id].vertices && window.roiDrawStates[item.id].vertices.length === 2) {
                    modelConfigs.roi_polygon = window.roiDrawStates[item.id].vertices;
                }
                let topConf = item.conf !== undefined && item.conf !== null ? parseFloat(item.conf) : 0.40;
                let topIou = item.iou !== undefined && item.iou !== null ? parseFloat(item.iou) : 0.45;

                if (camBox) {
                    const topConfEl = camBox.querySelector(".conf-slider");
                    const topIouEl = camBox.querySelector(".iou-slider");
                    if (topConfEl) topConf = parseFloat(topConfEl.value);
                    if (topIouEl) topIou = parseFloat(topIouEl.value);

                    camBox.querySelectorAll(".model-card-box").forEach(card => {
                        const mName = card.getAttribute("data-model");
                        const cls = card.getAttribute("data-class");
                        const cSlider = card.querySelector(".model-conf-slider");
                        const iSlider = card.querySelector(".model-iou-slider");
                        const crSlider = card.querySelector(".model-crowd-thresh-slider");
                        if (mName && cSlider && iSlider) {
                            const key = String(item.id !== undefined ? item.id : idx);
                            const assignedForCam = this.cameraModels[key] || [];
                            const normModel = mName.endsWith(".pt") ? mName : `${mName}.pt`;
                            const cleanName = mName.replace(".pt", "");

                            if (!assignedForCam.includes(normModel) && !assignedForCam.includes(cleanName) && !assignedForCam.includes(mName)) {
                                return;
                            }

                            const cVal = parseFloat(cSlider.value);
                            const iVal = parseFloat(iSlider.value);
                            let crVal = crSlider ? parseInt(crSlider.value, 10) : undefined;

                            const origClasses = (modelConfigs[normModel] && modelConfigs[normModel].enabled_classes) ||
                                (modelConfigs[cleanName] && modelConfigs[cleanName].enabled_classes) || [];

                            if (cls) {
                                modelConfigs[normModel] = modelConfigs[normModel] || {};
                                modelConfigs[normModel].enabled_classes = origClasses;
                                modelConfigs[normModel].class_configs = modelConfigs[normModel].class_configs || {};

                                const existingClassCfg = (modelConfigs[normModel].class_configs && modelConfigs[normModel].class_configs[cls]) || {};
                                if ((crVal === undefined || isNaN(crVal)) && existingClassCfg.crowd_threshold !== undefined) {
                                    crVal = existingClassCfg.crowd_threshold;
                                }

                                const clsPayload = { conf: cVal, iou: iVal };
                                if (crVal !== undefined && !isNaN(crVal)) {
                                    clsPayload.crowd_threshold = crVal;
                                    modelConfigs[normModel].crowd_threshold = crVal;
                                }
                                modelConfigs[normModel].class_configs[cls] = Object.assign({}, existingClassCfg, clsPayload);

                                modelConfigs[cleanName] = modelConfigs[cleanName] || {};
                                modelConfigs[cleanName].enabled_classes = origClasses;
                                modelConfigs[cleanName].class_configs = modelConfigs[cleanName].class_configs || {};
                                modelConfigs[cleanName].class_configs[cls] = Object.assign({}, existingClassCfg, clsPayload);
                                if (crVal !== undefined && !isNaN(crVal)) {
                                    modelConfigs[cleanName].crowd_threshold = crVal;
                                }
                            } else {
                                const existingPt = modelConfigs[normModel] || {};
                                const ptPayload = { conf: cVal, iou: iVal, enabled_classes: origClasses };
                                if (crVal !== undefined && !isNaN(crVal)) {
                                    ptPayload.crowd_threshold = crVal;
                                }
                                modelConfigs[normModel] = Object.assign({}, existingPt, ptPayload);
                                modelConfigs[cleanName] = Object.assign({}, existingPt, ptPayload);
                            }
                        }
                    });
                    item.model_configs = modelConfigs;
                    item.conf = topConf;
                    item.iou = topIou;
                }
                const p = this.store.cameraPayload(item, idx);
                p.conf = topConf;
                p.iou = topIou;
                p.model_configs = modelConfigs;
                return p;
            });
        localStorage.setItem("offline_streams", JSON.stringify(payload));
        localStorage.setItem("offline_camera_models", JSON.stringify(this.cameraModels));
        const btns = document.querySelectorAll("#btn-save-cameras, .save-cameras");
        btns.forEach(b => {
            b.disabled = true;
            b.textContent = "Saving...";
        });
        try {
            await this.api.saveCameras(payload, this.cameraModels);
            btns.forEach(b => {
                b.textContent = "Saved!";
            });
            setTimeout(() => {
                if (shouldReload) window.location.reload();
            }, 600);
        } catch (err) {
            console.error("Camera save error:", err);
            alert("Error saving cameras: " + err.message);
            btns.forEach(b => {
                b.disabled = false;
                b.textContent = "Save Cameras";
            });
        }
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

    async removeLocation(item, idx) {
        const id = this.locationId(idx, item);
        const locName = item ? item.location : "";

        // Instant local DOM update
        this.locations.splice(idx, 1);
        this.renderLocationForm();

        // Non-blocking background API call
        try {
            await fetch(`/api/locations?id=${encodeURIComponent(id || '')}&location=${encodeURIComponent(locName || '')}&index=${idx}`, {
                method: "DELETE"
            });
        } catch (err) {
            console.error("Delete location error:", err);
        }

        this.saveLocations(false);
    }

    renderLocationWidgets(force = false) {
        const hasRenderedCards = this.locationWidgets.querySelectorAll(".widget-card").length > 0;
        if (!force && hasRenderedCards) {
            return;
        }
        this.locationWidgets.innerHTML = "";
        if (!this.locations.length) {
            this.seedLocationsFromStreams();
        }
        if (!this.locations.length) {
            this.locationWidgets.innerHTML = '<div style="grid-column:1/-1;color:var(--muted);font-size:.75rem;">Add locations on the first tab to create widgets here.</div>';
            return;
        }
        if (this.expandedLocs.size === 0 && this.locations.length > 0) {
            this.expandedLocs.add(this.locationId(0, this.locations[0]));
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
            card.classList.toggle("expanded", !isCollapsed);
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
                confSlider.setAttribute("tabindex", "0");
                confSlider.addEventListener("pointerdown", () => confSlider.focus());
                confSlider.addEventListener("keydown", (e) => {
                    if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
                        e.preventDefault();
                        const next = Math.max(0.05, Math.round((parseFloat(confSlider.value) - 0.01) * 100) / 100);
                        confSlider.value = next;
                        confSlider.dispatchEvent(new Event("input", { bubbles: true }));
                    } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
                        e.preventDefault();
                        const next = Math.min(0.95, Math.round((parseFloat(confSlider.value) + 0.01) * 100) / 100);
                        confSlider.value = next;
                        confSlider.dispatchEvent(new Event("input", { bubbles: true }));
                    }
                });
                confSlider.addEventListener("input", e => {
                    stream.conf = parseFloat(e.target.value);
                    const valSpan = camBox.querySelector(".conf-val");
                    if (valSpan) valSpan.textContent = parseFloat(e.target.value).toFixed(2);
                });
            }
            if (iouSlider) {
                iouSlider.setAttribute("tabindex", "0");
                iouSlider.addEventListener("pointerdown", () => iouSlider.focus());
                iouSlider.addEventListener("keydown", (e) => {
                    if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
                        e.preventDefault();
                        const next = Math.max(0.05, Math.round((parseFloat(iouSlider.value) - 0.01) * 100) / 100);
                        iouSlider.value = next;
                        iouSlider.dispatchEvent(new Event("input", { bubbles: true }));
                    } else if (e.key === "ArrowRight" || e.key === "ArrowUp") {
                        e.preventDefault();
                        const next = Math.min(0.95, Math.round((parseFloat(iouSlider.value) + 0.01) * 100) / 100);
                        iouSlider.value = next;
                        iouSlider.dispatchEvent(new Event("input", { bubbles: true }));
                    }
                });
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

    getNegativeClasses(modelName) {
        const norm = (modelName || "").toLowerCase().replace(/\.pt$/i, "");
        const map = {
            "hansung_ppe_violations": ["NO-Hardhat", "NO-Mask", "NO-Safety Vest", "No-Gloves", "No-Shoes", "No-Goggles", "No-Safety-Belt"],
            "ppe_new": ["NO-Mask", "No-Cap-Lamp", "No-Gloves", "No-Gum-Boots", "No-Hard-Hat", "No-Saftey-Belt", "No-Saftey-Vest"],
            "nik_ppe_best": ["Fall-Detected", "NO-Mask", "No_Gloves", "No_Goggles", "No_Harness", "No_boots", "No_helmet", "No_safety_vest"],
            "construction": ["no_helmet", "no_goggle", "no_gloves", "no_boots", "none"],
            "hf_ppe_detection": ["no-helmet"],
            "keremberke_ppe_gear": ["no_glove", "no_goggles", "no_helmet", "no_mask", "no_shoes"],
            "final_fire_smoke": ["fire", "smoke"],
            "120_spillage_best": ["spillage"],
            "tyre_final": ["Defective"],
            "valve_box_best": ["Valve_Box_Open"],
            "dome_open_closed_best": ["Dome_Opened"],
            "150_firehose_best": ["fire_hose", "firehose_box", "no-fire_hose"],
            "sand_ext_chocks": ["Fire_Extinguisher", "Sand-Bucket", "wheel-choke", "no-Fire_Extinguisher", "no-Sand-Bucket", "no-wheel-choke"],
            "best_tape": ["reflective_tape_floor", "reflective_tape_person", "reflective_tape_vehicles", "traffic_tape_bollards", "traffic_tape_cone", "no-tape", "no-reflective_tape", "no-traffic_tape"],
            "yolov8n": ["person"],
            "crowd_detection": ["person"],
            "intrusion": ["person", "truck"],
            "non_working_hours": ["person", "truck"],
            "vehicle_intrusion": ["car", "truck", "bus", "motorcycle"],
            "unauthorized_activity": ["cooking", "crowd", "smoking_gesture", "fighting_posture", "lying_down"],
            "spark_arrestor": ["Spark_Arrestor", "no-Spark_Arrestor"],
            "spark_arrested": ["Spark_Arrestor", "no-Spark_Arrestor"]
        };
        if (map[norm]) return map[norm];
        const loaded = this.modelClasses[modelName] || this.modelClasses[norm] || [];
        return loaded.filter(c => {
            const lc = String(c).toLowerCase();
            return lc.startsWith("no-") || lc.startsWith("no_") || lc.includes("fall") || lc.includes("fire") || lc.includes("smoke") || lc.includes("spill") || lc.includes("open") || lc.includes("defective");
        });
    }

    cameraEditorRow(stream) {
        const row = document.createElement("div");
        row.className = "camera-edit-block";
        row.setAttribute("data-camera-id", stream.id !== undefined ? stream.id : "");
        row.style.cssText = "display: flex; flex-direction: column; gap: 14px; align-items: stretch; background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 10px; padding: 16px; font-size: 0.72rem; box-sizing: border-box;";
        row.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-weight: 800; color: #FFFFFF; font-size: 0.88rem;">${this.escapeHtml(stream.location || stream.label || `Camera ${Number(stream.id) + 1}`)}</span>
                    <span style="font-size: 0.62rem; color: var(--accent); background: rgba(10, 116, 218, 0.15); border: 1px solid var(--accent); padding: 2px 6px; border-radius: 4px; font-family: var(--mono); font-weight: 600;">CAM ${Number(stream.id) + 1}</span>
                </div>
                <button class="btn-remove" style="background: transparent; color: var(--danger); border: 1px solid rgba(237, 28, 36, 0.4); padding: 4px 10px; border-radius: 6px; font-size: 0.68rem; cursor: pointer; transition: all 0.2s;">Remove</button>
            </div>
            <div style="display: flex; flex-direction: column; gap: 4px; width: 100%;">
                <span style="font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700;">RTSP Stream URL</span>
                <input class="camera-rtsp-input" value="${this.escapeHtml(stream.rtsp || "")}" placeholder="rtsp://admin:password@ip:port/stream" style="width: 100%; background: var(--bg); border: 1px solid var(--border); color: #FFFFFF; font-family: var(--mono); font-size: 0.72rem; padding: 8px 10px; border-radius: 6px; box-sizing: border-box; outline: none;">
            </div>
            <div style="display: flex; flex-direction: column; gap: 8px; width: 100%;">
                <span style="font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700;">Select Detection Models &amp; Negative Classes</span>
                <div class="model-assign-row" style="margin: 0; display: flex; flex-direction: column; gap: 8px; width: 100%;">${this.modelChips(stream.id, stream)}</div>
            </div>
            <div class="camera-live-player-container" style="width: 100%; margin-top: 6px; border-top: 1px solid var(--border); padding-top: 12px;">
                <span style="font-size: 0.62rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 700; display: block; margin-bottom: 8px;">Live Camera Stream &amp; Inference Control</span>
                ${LocationDashboardTemplates.cameraBox(stream)}
            </div>`;
        row.querySelector(".camera-rtsp-input").addEventListener("input", e => { stream.rtsp = e.target.value; });
        row.querySelectorAll('.parent-model-checkbox').forEach(cb => this.bindModelCheckbox(cb, stream));
        row.querySelectorAll('.class-checkbox').forEach(cb => this.bindClassCheckbox(cb, stream));
        row.querySelector(".btn-remove").addEventListener("click", () => this.removeCamera(stream));
        return row;
    }

    modelChips(cameraIndex, stream) {
        const assigned = this.cameraModels[String(cameraIndex)] || [];
        if (!this.allModels.length) return '<span style="color:var(--muted);font-size:.68rem;">No models available</span>';

        let html = "";
        const modelsToRender = this.allModels.filter(m => !m.toLowerCase().includes("leakage") && !m.toLowerCase().startsWith("fire_smoke"));
        const modelConfigs = (stream && stream.model_configs) || {};

        modelsToRender.forEach(model => {
            const cleanM = model.replace(/\.pt$/i, "");
            const isAssigned = assigned.some(a => {
                const aNorm = String(a).toLowerCase().replace(/\.pt$/i, "");
                return aNorm === cleanM.toLowerCase();
            });

            const mCfg = modelConfigs[model] || modelConfigs[cleanM] || {};
            const savedEnabledClasses = mCfg.enabled_classes || [];
            const negClasses = this.getNegativeClasses(model);

            let displayTitle = cleanM;
            let badgeText = "VIOLATION CLASSES";
            let badgeColor = "var(--danger)";
            let badgeBg = "rgba(237, 28, 36, 0.12)";
            let badgeBorder = "rgba(237, 28, 36, 0.35)";

            if (cleanM.toLowerCase() === "yolov8n" || cleanM.toLowerCase() === "crowd_detection") {
                displayTitle = "Crowd Detection";
                badgeText = "CROWD MONITORING";
                badgeColor = "#38bdf8";
                badgeBg = "rgba(56, 189, 248, 0.12)";
                badgeBorder = "rgba(56, 189, 248, 0.35)";
            } else if (cleanM.toLowerCase() === "intrusion") {
                displayTitle = "Intrusion Detection / Perimeter Intrusion";
                badgeText = "INTRUSION / PERIMETER";
                badgeColor = "#f59e0b";
                badgeBg = "rgba(245, 158, 11, 0.12)";
                badgeBorder = "rgba(245, 158, 11, 0.35)";
            } else if (cleanM.toLowerCase() === "non_working_hours") {
                displayTitle = "Intrusion in Non-Working Hours";
                badgeText = "OFF-HOURS INTRUSION";
                badgeColor = "#e11d48";
                badgeBg = "rgba(225, 29, 72, 0.12)";
                badgeBorder = "rgba(225, 29, 72, 0.35)";
            } else if (cleanM.toLowerCase() === "vehicle_intrusion") {
                displayTitle = "Vehicle Intrusion Detection";
                badgeText = "VEHICLE RESTRICTION";
                badgeColor = "#3b82f6";
                badgeBg = "rgba(59, 130, 246, 0.12)";
                badgeBorder = "rgba(59, 130, 246, 0.35)";
            } else if (cleanM.toLowerCase() === "unauthorized_activity") {
                displayTitle = "Unauthorized Activity (Parking Area)";
                badgeText = "PARKING AREA SAFETY";
                badgeColor = "#f43f5e";
                badgeBg = "rgba(244, 63, 94, 0.12)";
                badgeBorder = "rgba(244, 63, 94, 0.35)";
            } else if (cleanM.toLowerCase().includes("spark_arrest")) {
                displayTitle = "Spark Arrestor Compliance";
                badgeText = "VEHICLE SAFETY";
                badgeColor = "#f59e0b";
                badgeBg = "rgba(245, 158, 11, 0.12)";
                badgeBorder = "rgba(245, 158, 11, 0.35)";
            }

            html += `
            <div class="model-accordion-card" style="width: 100%; background: rgba(14, 26, 51, 0.55); border: 1px solid ${isAssigned ? '#0A74DA' : 'var(--border)'}; border-radius: 8px; padding: 10px 12px; box-sizing: border-box; transition: all 0.2s;">
                <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                    <label class="model-header-label" style="display: inline-flex; align-items: center; gap: 8px; cursor: pointer; margin: 0;">
                        <input type="checkbox" class="parent-model-checkbox" value="${this.escapeHtml(model)}" ${isAssigned ? "checked" : ""} data-camera="${cameraIndex}" style="accent-color: var(--accent); width: 16px; height: 16px; cursor: pointer; margin: 0;">
                        <span style="font-weight: 700; font-size: 0.78rem; color: ${isAssigned ? '#FFFFFF' : 'var(--text)'};">${this.escapeHtml(displayTitle)}</span>
                    </label>
                    <span style="font-size: 0.60rem; color: ${badgeColor}; background: ${badgeBg}; border: 1px solid ${badgeBorder}; padding: 2px 7px; border-radius: 4px; font-weight: 700; letter-spacing: 0.5px;">${badgeText}</span>
                </div>
                <div class="model-classes-wrap" id="classes-wrap-${cameraIndex}-${this.escapeHtml(cleanM)}" style="display: ${isAssigned ? 'flex' : 'none'}; flex-wrap: wrap; gap: 6px; margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.08);">
                    ${negClasses.length > 0 ? negClasses.map(cls => {
                        const isChecked = isAssigned && Array.isArray(savedEnabledClasses) && savedEnabledClasses.includes(cls);
                        return `
                        <label class="class-chip ${isChecked ? 'checked' : ''}" style="margin: 0; display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; background: ${isChecked ? 'rgba(237, 28, 36, 0.16)' : 'rgba(255, 255, 255, 0.03)'}; border: 1px solid ${isChecked ? 'var(--danger)' : 'rgba(255,255,255,0.1)'}; border-radius: 5px; font-size: 0.68rem; cursor: pointer; transition: all 0.2s;">
                            <input type="checkbox" class="class-checkbox" value="${this.escapeHtml(cls)}" data-model="${this.escapeHtml(model)}" data-camera="${cameraIndex}" ${isChecked ? "checked" : ""} style="accent-color: var(--danger); width: 13px; height: 13px; margin: 0; cursor: pointer;">
                            <span style="color: ${isChecked ? '#FFFFFF' : 'var(--muted)'}; font-weight: 600;">${this.escapeHtml(cls)}</span>
                        </label>`;
                    }).join("") : '<span style="font-size:0.62rem; color:var(--muted); font-style:italic;">All detections for this model monitored</span>'}
                </div>
            </div>`;
        });

        return html;
    }

    bindModelCheckbox(cb, stream) {
        cb.addEventListener("change", () => {
            const card = cb.closest(".model-accordion-card");
            const labelSpan = cb.nextElementSibling;
            const model = cb.value;
            const cleanM = model.replace(/\.pt$/i, "");
            const camIdx = cb.getAttribute("data-camera");
            const wrap = document.getElementById(`classes-wrap-${camIdx}-${cleanM}`);

            if (card) {
                card.style.borderColor = cb.checked ? "#0A74DA" : "var(--border)";
            }
            if (labelSpan) {
                labelSpan.style.color = cb.checked ? "#FFFFFF" : "var(--text)";
            }
            if (wrap) {
                wrap.style.display = cb.checked ? "flex" : "none";
                if (!cb.checked) {
                    // When unchecking model, uncheck all classes under it
                    const classCbs = wrap.querySelectorAll(".class-checkbox");
                    classCbs.forEach(classCb => {
                        classCb.checked = false;
                        const chip = classCb.closest(".class-chip");
                        if (chip) {
                            chip.classList.remove("checked");
                            chip.style.borderColor = "rgba(255,255,255,0.1)";
                            chip.style.background = "rgba(255, 255, 255, 0.03)";
                            const sp = chip.querySelector("span");
                            if (sp) sp.style.color = "var(--muted)";
                        }
                    });
                }
                // When selecting a model, classes remain non-selected by default so the user explicitly chooses which classes to monitor
            }
        });
    }

    bindClassCheckbox(cb, stream) {
        cb.addEventListener("change", () => {
            const chip = cb.closest(".class-chip");
            const sp = chip ? chip.querySelector("span") : null;
            if (chip) {
                chip.classList.toggle("checked", cb.checked);
                chip.style.borderColor = cb.checked ? "var(--danger)" : "rgba(255,255,255,0.1)";
                chip.style.background = cb.checked ? "rgba(237, 28, 36, 0.16)" : "rgba(255, 255, 255, 0.03)";
            }
            if (sp) {
                sp.style.color = cb.checked ? "#FFFFFF" : "var(--muted)";
            }
            const model = cb.getAttribute("data-model");
            const camIdx = cb.getAttribute("data-camera");
            if (cb.checked && model) {
                const parentCb = document.querySelector(`.parent-model-checkbox[value="${model}"][data-camera="${camIdx}"]`);
                if (parentCb && !parentCb.checked) {
                    parentCb.checked = true;
                    const card = parentCb.closest(".model-accordion-card");
                    if (card) card.style.borderColor = "#0A74DA";
                    const pSpan = parentCb.nextElementSibling;
                    if (pSpan) pSpan.style.color = "#FFFFFF";
                }
            }
        });
    }

    addCamera(loc, locIdx) {
        const nextId = this.streams.length;

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

        // Ensure all streams have contiguous IDs
        this.streams.forEach((item, idx) => {
            item.id = idx;
            item.hls_live = `/hls/camera/${idx}/playlist.m3u8`;
            item.hls_raw = `/hls/stream${idx}_raw/playlist.m3u8`;
            item.hls_detected = `/hls/stream${idx}_detected/playlist.m3u8`;
        });

        this.expandedLocs.add(this.locationId(locIdx, loc));
        this.renderLocationWidgets(true);
    }

    async removeCamera(stream) {
        if (!stream) return;
        const targetIdx = this.streams.indexOf(stream);
        const targetId = (stream && stream.id !== undefined) ? stream.id : targetIdx;
        const targetRtsp = stream ? (stream.rtsp || "") : "";
        const targetLocId = stream ? (stream.location_id || "") : "";
        const targetLocName = stream ? (stream.location || "") : "";

        // 1. Call backend DELETE API FIRST so server state is updated atomically
        try {
            await fetch(`/api/streams?id=${encodeURIComponent(targetId)}&index=${encodeURIComponent(targetIdx >= 0 ? targetIdx : 0)}&rtsp=${encodeURIComponent(targetRtsp)}&location_id=${encodeURIComponent(targetLocId)}&location=${encodeURIComponent(targetLocName)}`, {
                method: "DELETE"
            });
        } catch (err) {
            console.error("Error removing camera stream from server:", err);
        }

        // 2. Remove from local memory state and re-index contiguous IDs & HLS paths
        if (targetIdx !== -1) {
            this.streams.splice(targetIdx, 1);

            // Re-index remaining streams so frontend IDs strictly match backend contiguous 0, 1, 2...
            this.streams.forEach((item, idx) => {
                item.id = idx;
                item.hls_live = `/hls/camera/${idx}/playlist.m3u8`;
                item.hls_raw = `/hls/stream${idx}_raw/playlist.m3u8`;
                item.hls_detected = `/hls/stream${idx}_detected/playlist.m3u8`;
            });

            // Shift cameraModels keys for all indices > targetIdx
            const newMap = {};
            const keys = Object.keys(this.cameraModels);
            keys.forEach(k => {
                const ik = parseInt(k);
                if (ik < targetIdx) {
                    newMap[String(ik)] = this.cameraModels[k];
                } else if (ik > targetIdx) {
                    newMap[String(ik - 1)] = this.cameraModels[k];
                }
            });
            this.cameraModels = newMap;
            window.cameraModelsMap = this.cameraModels;
            window.streamsArray = this.streams;

            // Destroy stale HLS player instances
            if (typeof hlsInstances !== "undefined" && hlsInstances) {
                Object.keys(hlsInstances).forEach(k => {
                    try {
                        hlsInstances[k].detachMedia();
                        hlsInstances[k].destroy();
                    } catch (_) {}
                    delete hlsInstances[k];
                });
            }

            // Sync localStorage immediately
            const updatedPayload = this.streams.map((item, idx) => this.store.cameraPayload(item, idx));
            localStorage.setItem("offline_streams", JSON.stringify(updatedPayload));
            localStorage.setItem("offline_camera_models", JSON.stringify(this.cameraModels));

            this.renderLocationWidgets(true);
            if (typeof renderCamerasConfig === "function") {
                renderCamerasConfig();
            }
        }
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
    window.dashboardInstance = dashboard;
    window.locationDashboard = dashboard;
    await dashboard.init();
}

window.initLocationDashboard = initLocationDashboard;