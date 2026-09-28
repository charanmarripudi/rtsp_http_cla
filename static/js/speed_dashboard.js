/**
 * Plug-and-Play Vehicle Speed & Count Dashboard Module
 * Dedicated Tab for Real-Time Tank Truck Speed Enforcement & Telemetry
 * Supports: Custom RTSP URLs, Video Uploads, Model & Class Checkboxes, Dual-Line Calibration
 */

class SpeedDashboard {
    constructor() {
        this.activeCameraId = "speed";
        this.sourceType = "rtsp"; // "rtsp" or "video"
        this.uploadedVideoPath = "";
        this.pollInterval = null;
        this.hlsPlayer = null;
        this.initialized = false;
        this.availableModels = [];
        this.modelClassesCache = {};
        this.hlsRetryTimer = null;
    }

    init() {
        if (this.initialized) return;
        this.initialized = true;

        this.bindEvents();
        this.loadModelsAndClasses();
        this.loadConfig();
        this.startPolling();
        this.setSourceType("rtsp");
    }

    bindEvents() {
        // RTSP / Video Toggle Buttons
        const tabRtsp = document.getElementById("speed-src-tab-rtsp");
        const tabVideo = document.getElementById("speed-src-tab-video");
        if (tabRtsp) tabRtsp.addEventListener("click", () => this.setSourceType("rtsp"));
        if (tabVideo) tabVideo.addEventListener("click", () => this.setSourceType("video"));

        // File Input Upload
        const fileInput = document.getElementById("speed-video-file-input");
        if (fileInput) {
            fileInput.addEventListener("change", (e) => {
                if (e.target.files && e.target.files.length > 0) {
                    this.handleVideoFileUpload(e.target.files[0]);
                }
            });
        }

        // Apply Calibration Button
        const applyBtn = document.getElementById("speed-apply-calibration-btn");
        if (applyBtn) {
            applyBtn.addEventListener("click", () => this.saveConfig());
        }
    }

    setSourceType(type) {
        this.sourceType = type;
        const cRtsp = document.getElementById("speed-src-container-rtsp");
        const cVideo = document.getElementById("speed-src-container-video");
        const tabRtsp = document.getElementById("speed-src-tab-rtsp");
        const tabVideo = document.getElementById("speed-src-tab-video");

        if (type === "rtsp") {
            if (cRtsp) cRtsp.style.display = "flex";
            if (cVideo) cVideo.style.display = "none";
            if (tabRtsp) {
                tabRtsp.style.background = "rgba(56,189,248,0.15)";
                tabRtsp.style.borderColor = "#38bdf8";
                tabRtsp.style.color = "#38bdf8";
            }
            if (tabVideo) {
                tabVideo.style.background = "transparent";
                tabVideo.style.borderColor = "var(--border)";
                tabVideo.style.color = "var(--muted)";
            }
        } else {
            if (cRtsp) cRtsp.style.display = "none";
            if (cVideo) cVideo.style.display = "flex";
            if (tabRtsp) {
                tabRtsp.style.background = "transparent";
                tabRtsp.style.borderColor = "var(--border)";
                tabRtsp.style.color = "var(--muted)";
            }
            if (tabVideo) {
                tabVideo.style.background = "rgba(0,255,170,0.15)";
                tabVideo.style.borderColor = "#00ffaa";
                tabVideo.style.color = "#00ffaa";
            }
        }
    }

    async handleVideoFileUpload(file) {
        const nameSpan = document.getElementById("speed-upload-filename");
        const statusSpan = document.getElementById("speed-monitor-status");
        if (nameSpan) nameSpan.textContent = `Uploading ${file.name}...`;

        const formData = new FormData();
        formData.append("file", file);

        try {
            const res = await fetch(`/api/speed/upload?filename=${encodeURIComponent(file.name)}`, {
                method: "POST",
                body: file
            });
            const data = await res.json();
            if (res.ok && data.filepath) {
                this.uploadedVideoPath = data.filepath;
                if (nameSpan) {
                    nameSpan.textContent = `✓ ${file.name} (Ready)`;
                    nameSpan.style.color = "#00ffaa";
                }
                const serverInput = document.getElementById("speed-server-video-input");
                if (serverInput) serverInput.value = data.filepath;
            } else {
                if (nameSpan) {
                    nameSpan.textContent = "Upload failed";
                    nameSpan.style.color = "#ff4444";
                }
            }
        } catch (e) {
            console.error("[SPEED-DASH] Upload failed:", e);
            if (nameSpan) {
                nameSpan.textContent = "Upload error";
                nameSpan.style.color = "#ff4444";
            }
        }
    }

    async loadModelsAndClasses() {
        const container = document.getElementById("speed-models-checkboxes");
        const countSpan = document.getElementById("speed-models-count");
        if (!container) return;

        try {
            const res = await fetch("/api/models");
            let models = [];
            if (res.ok) {
                const data = await res.json();
                models = data.models || [];
            }

            // Dedicated Vehicle Speed Tab: Only load and display vehicle_speed.pt
            let speedModels = models.filter(m => m === "vehicle_speed.pt" || m.toLowerCase().includes("vehicle_speed"));
            if (speedModels.length === 0) {
                speedModels = ["vehicle_speed.pt"];
            }

            this.availableModels = speedModels;

            // Fetch classes for vehicle_speed.pt
            await Promise.all(speedModels.map(async (m) => {
                try {
                    const r = await fetch(`/api/model-classes?model=${encodeURIComponent(m)}`);
                    if (r.ok) {
                        const d = await r.json();
                        this.modelClassesCache[m] = d.classes || [];
                    }
                } catch (e) {
                    this.modelClassesCache[m] = [];
                }
            }));

            // If vehicle_speed.pt has no classes returned, provide default vehicle classes
            if (!this.modelClassesCache["vehicle_speed.pt"] || this.modelClassesCache["vehicle_speed.pt"].length === 0) {
                this.modelClassesCache["vehicle_speed.pt"] = ["truck", "car", "pickup truck", "bike", "tank truck", "van", "bus"];
            }

            this.renderModelCards(speedModels);

            if (countSpan) countSpan.textContent = `vehicle_speed.pt loaded`;
        } catch (e) {
            console.error("[SPEED-DASH] Error loading models:", e);
            if (countSpan) countSpan.textContent = "Ready";
        }
    }

    renderModelCards(models) {
        const container = document.getElementById("speed-models-checkboxes");
        if (!container) return;
        container.innerHTML = "";

        models.forEach((m, idx) => {
            const normName = m.replace(".pt", "");
            const isSpeedDefault = normName.includes("speed") || normName.includes("vehicle") || idx === 0;
            const classes = this.modelClassesCache[m] || ["truck", "car", "pickup truck", "bike", "tank truck", "van", "bus"];

            const card = document.createElement("div");
            card.className = "speed-model-card";
            card.id = `speed-model-card-${normName}`;
            card.style.cssText = `
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid ${isSpeedDefault ? '#38bdf8' : 'var(--border)'};
                border-radius: 8px;
                padding: 10px 14px;
                display: flex;
                flex-direction: column;
                gap: 8px;
                transition: all 0.2s ease;
            `;

            // 1. Header row (Model Checkbox, Name, Select All/None, Thresholds)
            const headerRow = document.createElement("div");
            headerRow.style.cssText = "display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;";

            const leftHeader = document.createElement("label");
            leftHeader.style.cssText = "display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;";

            const mChk = document.createElement("input");
            mChk.type = "checkbox";
            mChk.className = "speed-model-checkbox";
            mChk.value = m;
            mChk.checked = isSpeedDefault;
            mChk.id = `chk-model-${normName}`;
            mChk.style.cursor = "pointer";

            const nameBadge = document.createElement("span");
            nameBadge.style.cssText = `
                font-family: var(--mono);
                font-size: 0.80rem;
                font-weight: 700;
                color: ${isSpeedDefault ? '#00ffaa' : 'var(--text)'};
            `;
            nameBadge.textContent = m;

            leftHeader.appendChild(mChk);
            leftHeader.appendChild(nameBadge);

            // Right header: Quick class selection & Thresholds
            const rightHeader = document.createElement("div");
            rightHeader.style.cssText = "display: flex; align-items: center; gap: 10px; flex-wrap: wrap;";

            const quickAllBtn = document.createElement("button");
            quickAllBtn.type = "button";
            quickAllBtn.textContent = "All Classes";
            quickAllBtn.style.cssText = "background: rgba(255,255,255,0.06); border: 1px solid var(--border); color: var(--muted); font-size: 0.65rem; padding: 2px 6px; border-radius: 4px; cursor: pointer;";
            quickAllBtn.onclick = (e) => {
                e.stopPropagation();
                card.querySelectorAll(`.class-chk-${normName}`).forEach(cb => { cb.checked = true; cb.parentElement.style.borderColor = '#00ffaa'; cb.parentElement.style.background = 'rgba(0,255,170,0.12)'; });
            };

            const quickNoneBtn = document.createElement("button");
            quickNoneBtn.type = "button";
            quickNoneBtn.textContent = "Clear";
            quickNoneBtn.style.cssText = "background: rgba(255,255,255,0.06); border: 1px solid var(--border); color: var(--muted); font-size: 0.65rem; padding: 2px 6px; border-radius: 4px; cursor: pointer;";
            quickNoneBtn.onclick = (e) => {
                e.stopPropagation();
                card.querySelectorAll(`.class-chk-${normName}`).forEach(cb => { cb.checked = false; cb.parentElement.style.borderColor = 'var(--border)'; cb.parentElement.style.background = 'rgba(0,0,0,0.3)'; });
            };

            // Conf & IoU mini sliders
            const threshDiv = document.createElement("div");
            threshDiv.style.cssText = "display: flex; align-items: center; gap: 8px; font-size: 0.68rem; color: var(--muted);";
            threshDiv.innerHTML = `
                <span>Conf: <strong id="speed-conf-val-${normName}" style="color: #00ffaa; font-family: var(--mono);">0.25</strong></span>
                <input type="range" id="speed-conf-${normName}" min="0.05" max="0.95" step="0.05" value="0.25" style="width: 60px; cursor: pointer;">
                <span>IoU: <strong id="speed-iou-val-${normName}" style="color: #38bdf8; font-family: var(--mono);">0.45</strong></span>
                <input type="range" id="speed-iou-${normName}" min="0.05" max="0.95" step="0.05" value="0.45" style="width: 60px; cursor: pointer;">
            `;

            rightHeader.appendChild(quickAllBtn);
            rightHeader.appendChild(quickNoneBtn);
            rightHeader.appendChild(threshDiv);

            headerRow.appendChild(leftHeader);
            headerRow.appendChild(rightHeader);

            // 2. Class Checkboxes Container
            const classContainer = document.createElement("div");
            classContainer.id = `speed-classes-container-${normName}`;
            classContainer.style.cssText = `
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                padding-top: 4px;
                border-top: 1px solid rgba(255,255,255,0.06);
            `;

            classes.forEach(c => {
                const isClsDefault = isSpeedDefault || ["truck", "car", "pickup truck", "bike", "tank truck", "van", "bus"].includes(c.toLowerCase());
                const cLabel = document.createElement("label");
                cLabel.style.cssText = `
                    display: inline-flex;
                    align-items: center;
                    gap: 5px;
                    background: ${isClsDefault ? 'rgba(0,255,170,0.12)' : 'rgba(0,0,0,0.3)'};
                    border: 1px solid ${isClsDefault ? '#00ffaa' : 'var(--border)'};
                    padding: 3px 8px;
                    border-radius: 4px;
                    cursor: pointer;
                    user-select: none;
                    font-size: 0.72rem;
                    font-family: var(--mono);
                    transition: all 0.15s ease;
                `;

                const cChk = document.createElement("input");
                cChk.type = "checkbox";
                cChk.className = `speed-class-checkbox class-chk-${normName}`;
                cChk.value = c;
                cChk.checked = isClsDefault;
                cChk.style.cursor = "pointer";

                cChk.addEventListener("change", () => {
                    cLabel.style.borderColor = cChk.checked ? "#00ffaa" : "var(--border)";
                    cLabel.style.background = cChk.checked ? "rgba(0,255,170,0.12)" : "rgba(0,0,0,0.3)";
                    if (cChk.checked && !mChk.checked) {
                        mChk.checked = true;
                        mChk.dispatchEvent(new Event("change"));
                    }
                });

                const cSpan = document.createElement("span");
                cSpan.textContent = c;

                cLabel.appendChild(cChk);
                cLabel.appendChild(cSpan);
                classContainer.appendChild(cLabel);
            });

            // Handle Model Checkbox changes
            mChk.addEventListener("change", () => {
                const isChecked = mChk.checked;
                card.style.borderColor = isChecked ? "#38bdf8" : "var(--border)";
                nameBadge.style.color = isChecked ? "#00ffaa" : "var(--text)";
                classContainer.style.opacity = isChecked ? "1.0" : "0.45";
            });

            // Wire Conf / IoU slider changes
            card.appendChild(headerRow);
            card.appendChild(classContainer);
            container.appendChild(card);

            const confSlider = document.getElementById(`speed-conf-${normName}`);
            const confVal = document.getElementById(`speed-conf-val-${normName}`);
            if (confSlider && confVal) {
                confSlider.addEventListener("input", (e) => confVal.textContent = e.target.value);
            }
            const iouSlider = document.getElementById(`speed-iou-${normName}`);
            const iouVal = document.getElementById(`speed-iou-val-${normName}`);
            if (iouSlider && iouVal) {
                iouSlider.addEventListener("input", (e) => iouVal.textContent = e.target.value);
            }
        });
    }

    async loadConfig() {
        try {
            const res = await fetch("/api/speed/config");
            if (!res.ok) return;
            const data = await res.json();
            const cfg = data.config || {};

            const limitInput = document.getElementById("speed-cfg-limit");
            if (limitInput && cfg.speed_limit_kmh !== undefined) limitInput.value = cfg.speed_limit_kmh;

            const distInput = document.getElementById("speed-cfg-dist");
            if (distInput && cfg.road_distance_meters !== undefined) distInput.value = cfg.road_distance_meters;

            const laInput = document.getElementById("speed-cfg-line-a");
            if (laInput && cfg.line_a_ratio !== undefined) laInput.value = Math.round(cfg.line_a_ratio * 100);

            const lbInput = document.getElementById("speed-cfg-line-b");
            if (lbInput && cfg.line_b_ratio !== undefined) lbInput.value = Math.round(cfg.line_b_ratio * 100);

            const limitBadge = document.getElementById("speed-badge-limit");
            if (limitBadge) limitBadge.textContent = `${cfg.speed_limit_kmh || 10} km/h`;

            const distBadge = document.getElementById("speed-badge-dist");
            if (distBadge) distBadge.textContent = `${cfg.road_distance_meters || 20} m`;
        } catch (e) {
            console.error("[SPEED-DASH] Error loading config:", e);
        }
    }

    async saveConfig() {
        const limitInput = document.getElementById("speed-cfg-limit");
        const distInput = document.getElementById("speed-cfg-dist");
        const laInput = document.getElementById("speed-cfg-line-a");
        const lbInput = document.getElementById("speed-cfg-line-b");
        const statusSpan = document.getElementById("speed-cfg-status");

        const payload = {
            speed_limit_kmh: parseFloat(limitInput ? limitInput.value : 10.0),
            road_distance_meters: parseFloat(distInput ? distInput.value : 20.0),
            line_a_ratio: parseFloat(laInput ? laInput.value : 40) / 100.0,
            line_b_ratio: parseFloat(lbInput ? lbInput.value : 75) / 100.0,
        };

        try {
            if (statusSpan) statusSpan.textContent = "Applying...";
            const res = await fetch("/api/speed/config", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            if (res.ok) {
                if (statusSpan) {
                    statusSpan.textContent = "Saved & Applied Live!";
                    statusSpan.style.color = "#00ffaa";
                    setTimeout(() => { statusSpan.textContent = ""; }, 3000);
                }
                this.loadConfig();
            }
        } catch (e) {
            if (statusSpan) {
                statusSpan.textContent = "Save failed";
                statusSpan.style.color = "#ff4444";
            }
        }
    }

    async startMonitoring() {
        const statusSpan = document.getElementById("speed-monitor-status");
        
        // 1. Collect selected models and their enabled classes
        const checkedModelBoxes = document.querySelectorAll(".speed-model-checkbox:checked");
        const selectedModels = Array.from(checkedModelBoxes).map(cb => cb.value);

        if (selectedModels.length === 0) {
            if (statusSpan) {
                statusSpan.textContent = "Please select at least 1 model";
                statusSpan.style.color = "#ff4444";
            }
            return;
        }

        const modelConfigs = {};
        let overallConf = 0.25;
        let overallIou = 0.45;

        selectedModels.forEach(m => {
            const normName = m.replace(".pt", "");
            const checkedClassBoxes = document.querySelectorAll(`.class-chk-${normName}:checked`);
            const enabledClasses = Array.from(checkedClassBoxes).map(cb => cb.value);

            const confEl = document.getElementById(`speed-conf-${normName}`);
            const iouEl = document.getElementById(`speed-iou-${normName}`);
            const conf = confEl ? parseFloat(confEl.value) : 0.25;
            const iou = iouEl ? parseFloat(iouEl.value) : 0.45;
            overallConf = conf;
            overallIou = iou;

            modelConfigs[m] = {
                enabled_classes: enabledClasses.length > 0 ? enabledClasses : ["truck", "car", "pickup truck", "bike", "tank truck", "van", "bus"],
                conf: conf,
                iou: iou,
                imgsz: 320
            };
        });

        // 2. Determine RTSP or Video Source
        let streamUrl = "";
        if (this.sourceType === "rtsp") {
            const rtspInput = document.getElementById("speed-custom-rtsp-input");
            streamUrl = rtspInput ? (rtspInput.value.trim() || rtspInput.placeholder.trim()) : "";
            if (!streamUrl) {
                if (statusSpan) {
                    statusSpan.textContent = "Please enter an RTSP URL";
                    statusSpan.style.color = "#ff4444";
                }
                return;
            }
        } else if (this.sourceType === "video") {
            const serverInput = document.getElementById("speed-server-video-input");
            const manualPath = serverInput ? serverInput.value.trim() : "";
            streamUrl = manualPath || this.uploadedVideoPath;
            if (!streamUrl) {
                if (statusSpan) {
                    statusSpan.textContent = "Please upload or specify a video file";
                    statusSpan.style.color = "#ff4444";
                }
                return;
            }
        }

        const payload = {
            camera: "speed",
            location: "Vehicle Speed Monitor",
            source_type: this.sourceType,
            rtsp: streamUrl,
            models: selectedModels,
            conf: overallConf,
            iou: overallIou,
            model_configs: modelConfigs
        };

        console.log("[SPEED-DASH] Starting Speed Monitor with payload:", payload);

        try {
            if (statusSpan) {
                statusSpan.textContent = "⚡ Connecting to RTSP Stream & Launching AI Tracker...";
                statusSpan.style.color = "#38bdf8";
            }
            const res = await fetch("/api/speed/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            console.log("[SPEED-DASH] Start response:", data);

            if (res.ok && (data.status === "started" || data.status === "ok")) {
                if (statusSpan) {
                    statusSpan.textContent = "● AI Speed Monitor Running (Live Detection)";
                    statusSpan.style.color = "#00ffaa";
                }
                // Connect HLS video player with auto-retry
                this.attachHlsStream(true, 1);
            } else {
                if (statusSpan) {
                    statusSpan.textContent = data.message || data.error || "Failed to start";
                    statusSpan.style.color = "#ff4444";
                }
            }
        } catch (e) {
            console.error("[SPEED-DASH] Start failed:", e);
            if (statusSpan) {
                statusSpan.textContent = `Error: ${e.message}`;
                statusSpan.style.color = "#ff4444";
            }
        }
    }

    async stopMonitoring() {
        const statusSpan = document.getElementById("speed-monitor-status");

        if (this.hlsRetryTimer) {
            clearTimeout(this.hlsRetryTimer);
            this.hlsRetryTimer = null;
        }

        try {
            if (statusSpan) statusSpan.textContent = "Stopping...";
            const res = await fetch("/api/speed/stop", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ camera: "speed" })
            });
            const data = await res.json();
            console.log("[SPEED-DASH] Stop response:", data);

            if (res.ok) {
                if (statusSpan) {
                    statusSpan.textContent = "○ Speed Monitor Stopped";
                    statusSpan.style.color = "var(--muted)";
                }
                if (this.hlsPlayer) {
                    this.hlsPlayer.destroy();
                    this.hlsPlayer = null;
                }
                const video = document.getElementById("speed-video-player");
                if (video) {
                    video.pause();
                    video.removeAttribute("src");
                    video.load();
                }
            }
        } catch (e) {
            console.error("[SPEED-DASH] Stop failed:", e);
        }
    }

    attachHlsStream(retry = true, attempt = 1) {
        const video = document.getElementById("speed-video-player");
        if (!video) return;

        if (this.hlsRetryTimer) {
            clearTimeout(this.hlsRetryTimer);
            this.hlsRetryTimer = null;
        }

        const streamUrl = `/hls/camera/speed/playlist.m3u8?t=${Date.now()}`;
        console.log(`[SPEED-DASH] Attaching HLS Stream (Attempt ${attempt}): ${streamUrl}`);

        if (window.Hls && window.Hls.isSupported()) {
            if (this.hlsPlayer) {
                this.hlsPlayer.destroy();
                this.hlsPlayer = null;
            }

            const hls = new window.Hls({
                enableWorker: true,
                lowLatencyMode: true,
                liveSyncDurationCount: 2,
                maxBufferLength: 4,
                manifestLoadingMaxRetry: 10,
                manifestLoadingRetryDelay: 500
            });

            hls.loadSource(streamUrl);
            hls.attachMedia(video);

            hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
                console.log("[SPEED-DASH] HLS Manifest parsed successfully! Playing video...");
                video.play().catch(() => {});
            });

            hls.on(window.Hls.Events.ERROR, (event, data) => {
                if (data.fatal) {
                    switch (data.type) {
                        case window.Hls.ErrorTypes.NETWORK_ERROR:
                            if (retry && attempt < 12) {
                                console.log(`[SPEED-DASH] Stream not ready yet, retrying in 600ms (attempt ${attempt + 1}/12)...`);
                                this.hlsRetryTimer = setTimeout(() => this.attachHlsStream(true, attempt + 1), 600);
                            } else {
                                hls.startLoad();
                            }
                            break;
                        case window.Hls.ErrorTypes.MEDIA_ERROR:
                            hls.recoverMediaError();
                            break;
                        default:
                            hls.destroy();
                            break;
                    }
                }
            });

            this.hlsPlayer = hls;
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = streamUrl;
            video.play().catch(() => {});
        }
    }

    startPolling() {
        if (this.pollInterval) clearInterval(this.pollInterval);
        this.pollStats();
        this.pollInterval = setInterval(() => this.pollStats(), 1200);
    }

    async pollStats() {
        const tab = document.getElementById("tab-speed");
        if (!tab || tab.style.display === "none") return;

        try {
            const res = await fetch("/api/speed/stats");
            if (!res.ok) return;
            const data = await res.json();

            const sum = data.summary || {};
            const trucksEl = document.getElementById("speed-cnt-trucks");
            if (trucksEl) trucksEl.textContent = sum.total_trucks || 0;

            const carsEl = document.getElementById("speed-cnt-cars");
            if (carsEl) carsEl.textContent = sum.total_cars || 0;

            const totalEl = document.getElementById("speed-cnt-total");
            if (totalEl) totalEl.textContent = sum.total_vehicles || 0;

            const overspeedEl = document.getElementById("speed-cnt-overspeed");
            if (overspeedEl) overspeedEl.textContent = sum.total_violations || 0;

            // Render live telemetry table
            const tbody = document.getElementById("speed-telemetry-tbody");
            if (tbody) {
                const camData = data.cameras && data.cameras[String(this.activeCameraId)];
                const recent = (camData && camData.recent_speeds) || sum.recent_violations || [];
                
                if (recent.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:14px;">No vehicles tracked yet in this session</td></tr>`;
                } else {
                    let html = "";
                    const rev = [...recent].reverse().slice(0, 15);
                    rev.forEach(s => {
                        const isOver = s.is_over_speed;
                        const statusBadge = isOver
                            ? `<span style="background:rgba(255,0,0,0.18);color:#ff4444;border:1px solid rgba(255,0,0,0.3);padding:2px 8px;border-radius:10px;font-weight:700;font-size:0.72rem;">🚨 OVER SPEED</span>`
                            : `<span style="background:rgba(0,255,170,0.12);color:#00ffaa;border:1px solid rgba(0,255,170,0.3);padding:2px 8px;border-radius:10px;font-weight:600;font-size:0.72rem;">✓ COMPLIANT</span>`;

                        html += `<tr style="border-bottom: 1px solid rgba(255,255,255,0.04);">
                            <td style="padding: 8px 10px; font-family: var(--mono); font-size: 0.75rem; color: var(--muted);">${s.timestamp || "Live"}</td>
                            <td style="padding: 8px 10px; font-weight: 600; font-size: 0.8rem; text-transform: capitalize;">${s.cls || "Vehicle"}</td>
                            <td style="padding: 8px 10px; font-family: var(--mono); font-size: 0.78rem;">#${s.vehicle_id}</td>
                            <td style="padding: 8px 10px; font-family: var(--mono); font-weight: 700; font-size: 0.85rem; color: ${isOver ? '#ff4444' : '#00ffaa'};">${s.speed_kmh} km/h</td>
                            <td style="padding: 8px 10px;">${statusBadge}</td>
                        </tr>`;
                    });
                    tbody.innerHTML = html;
                }
            }
        } catch (e) {
            // silent fail on network glitch
        }
    }
}

window.speedDashboard = new SpeedDashboard();
window.initSpeedTab = function() {
    if (window.speedDashboard) {
        window.speedDashboard.init();
    }
};

// Auto-initialize on DOM ready
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.initSpeedTab());
} else {
    window.initSpeedTab();
}
