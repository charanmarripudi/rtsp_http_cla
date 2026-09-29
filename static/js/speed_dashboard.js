/**
 * Plug-and-Play Vehicle Speed & Count Dashboard Module
 * Dedicated Tab for Real-Time Tank Truck Speed Enforcement & Telemetry
 * Supports: Custom RTSP URLs, Video Uploads, Model & Class Checkboxes, Any-Angle Dual-Line Calibration
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

        // Any-Angle Line Calibration State
        this.lineA = { x1_pct: 5, y1_pct: 40, x2_pct: 95, y2_pct: 40 };
        this.lineB = { x1_pct: 5, y1_pct: 75, x2_pct: 95, y2_pct: 75 };
        this.drawingMode = null; // 'line_a' | 'line_b' | null
        this.isDragging = false;
        this.dragStart = { x: 0, y: 0 };
    }

    init() {
        if (this.initialized) return;
        this.initialized = true;

        this.bindEvents();
        this.initCanvasOverlay();
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
    }

    initCanvasOverlay() {
        const canvas = document.getElementById("speed-canvas-overlay");
        const container = document.getElementById("speed-video-container");
        if (!canvas || !container) return;

        const resizeCanvas = () => {
            const rect = container.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                canvas.width = rect.width;
                canvas.height = rect.height;
                this.renderCanvas();
            }
        };

        window.addEventListener("resize", resizeCanvas);
        setTimeout(resizeCanvas, 200);

        // Canvas Mouse / Touch Events for Drawing Lines at Any Angle
        const getPctCoords = (e) => {
            const rect = canvas.getBoundingClientRect();
            const clientX = e.touches ? e.touches[0].clientX : e.clientX;
            const clientY = e.touches ? e.touches[0].clientY : e.clientY;
            const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
            const y = Math.max(0, Math.min(rect.height, clientY - rect.top));
            return {
                x_pct: Math.round((x / rect.width) * 1000) / 10.0,
                y_pct: Math.round((y / rect.height) * 1000) / 10.0
            };
        };

        const onDown = (e) => {
            if (!this.drawingMode) return;
            e.preventDefault();
            this.isDragging = true;
            this.dragStart = getPctCoords(e);
            if (this.drawingMode === "line_a") {
                this.lineA = { x1_pct: this.dragStart.x_pct, y1_pct: this.dragStart.y_pct, x2_pct: this.dragStart.x_pct, y2_pct: this.dragStart.y_pct };
            } else if (this.drawingMode === "line_b") {
                this.lineB = { x1_pct: this.dragStart.x_pct, y1_pct: this.dragStart.y_pct, x2_pct: this.dragStart.x_pct, y2_pct: this.dragStart.y_pct };
            }
            this.renderCanvas();
            this.updateLineLabels();
        };

        const onMove = (e) => {
            if (!this.isDragging || !this.drawingMode) return;
            e.preventDefault();
            const curr = getPctCoords(e);
            if (this.drawingMode === "line_a") {
                this.lineA.x2_pct = curr.x_pct;
                this.lineA.y2_pct = curr.y_pct;
            } else if (this.drawingMode === "line_b") {
                this.lineB.x2_pct = curr.x_pct;
                this.lineB.y2_pct = curr.y_pct;
            }
            this.renderCanvas();
            this.updateLineLabels();
        };

        const onUp = (e) => {
            if (!this.isDragging) return;
            this.isDragging = false;
            const hint = document.getElementById("speed-draw-hint");
            const modeName = this.drawingMode === "line_a" ? "Line A (Gate)" : "Line B (Gantry)";
            if (hint) {
                hint.textContent = `✓ ${modeName} Drawn! Click "Save Lines" to apply live.`;
                hint.style.color = "#00ffaa";
            }
            this.setDrawingMode(null);
            this.renderCanvas();
            this.updateLineLabels();
        };

        canvas.addEventListener("mousedown", onDown);
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);

        canvas.addEventListener("touchstart", onDown, { passive: false });
        window.addEventListener("touchmove", onMove, { passive: false });
        window.addEventListener("touchend", onUp);
    }

    setDrawingMode(mode) {
        this.drawingMode = mode;
        const btnA = document.getElementById("speed-btn-draw-a");
        const btnB = document.getElementById("speed-btn-draw-b");
        const hint = document.getElementById("speed-draw-hint");
        const canvas = document.getElementById("speed-canvas-overlay");

        if (btnA) {
            btnA.style.background = mode === "line_a" ? "rgba(255,220,0,0.3)" : "rgba(255,220,0,0.1)";
            btnA.style.borderColor = mode === "line_a" ? "#ffdc00" : "rgba(255,220,0,0.4)";
        }
        if (btnB) {
            btnB.style.background = mode === "line_b" ? "rgba(0,255,170,0.3)" : "rgba(0,255,170,0.1)";
            btnB.style.borderColor = mode === "line_b" ? "#00ffaa" : "rgba(0,255,170,0.4)";
        }

        if (mode === "line_a") {
            if (hint) {
                hint.textContent = "✏️ Click and drag across the video to draw Line A (Gate Entry)";
                hint.style.color = "#ffdc00";
            }
            if (canvas) canvas.style.cursor = "crosshair";
        } else if (mode === "line_b") {
            if (hint) {
                hint.textContent = "✏️ Click and drag across the video to draw Line B (Gantry Road)";
                hint.style.color = "#00ffaa";
            }
            if (canvas) canvas.style.cursor = "crosshair";
        } else {
            if (canvas) canvas.style.cursor = "default";
        }
        this.renderCanvas();
    }

    resetLines() {
        this.lineA = { x1_pct: 5, y1_pct: 40, x2_pct: 95, y2_pct: 40 };
        this.lineB = { x1_pct: 5, y1_pct: 75, x2_pct: 95, y2_pct: 75 };
        this.setDrawingMode(null);
        this.renderCanvas();
        this.updateLineLabels();
        const hint = document.getElementById("speed-draw-hint");
        if (hint) {
            hint.textContent = "Reset to default horizontal lines";
            hint.style.color = "var(--muted)";
        }
    }

    updateLineLabels() {
        const lblA = document.getElementById("speed-lbl-line-a");
        const lblB = document.getElementById("speed-lbl-line-b");
        if (lblA && this.lineA) {
            lblA.textContent = `(${this.lineA.x1_pct}%, ${this.lineA.y1_pct}%) → (${this.lineA.x2_pct}%, ${this.lineA.y2_pct}%)`;
        }
        if (lblB && this.lineB) {
            lblB.textContent = `(${this.lineB.x1_pct}%, ${this.lineB.y1_pct}%) → (${this.lineB.x2_pct}%, ${this.lineB.y2_pct}%)`;
        }
    }

    renderCanvas() {
        const canvas = document.getElementById("speed-canvas-overlay");
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        const w = canvas.width;
        const h = canvas.height;

        ctx.clearRect(0, 0, w, h);

        const drawSegment = (line, color, label) => {
            if (!line) return;
            const x1 = (line.x1_pct / 100.0) * w;
            const y1 = (line.y1_pct / 100.0) * h;
            const x2 = (line.x2_pct / 100.0) * w;
            const y2 = (line.y2_pct / 100.0) * h;

            // Line Shadow
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.strokeStyle = "rgba(0,0,0,0.8)";
            ctx.lineWidth = 4;
            ctx.stroke();

            // Main Line
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.strokeStyle = color;
            ctx.lineWidth = 2.5;
            ctx.setLineDash([]);
            ctx.stroke();

            // Endpoint Handles
            [ [x1, y1], [x2, y2] ].forEach(([px, py]) => {
                ctx.beginPath();
                ctx.arc(px, py, 5, 0, Math.PI * 2);
                ctx.fillStyle = color;
                ctx.fill();
                ctx.strokeStyle = "#000";
                ctx.lineWidth = 1.5;
                ctx.stroke();
            });

            // Midpoint Label
            const midX = (x1 + x2) / 2;
            const midY = (y1 + y2) / 2;

            ctx.font = "bold 11px monospace";
            const textWidth = ctx.measureText(label).width;
            ctx.fillStyle = "rgba(10, 15, 25, 0.85)";
            ctx.fillRect(midX - textWidth / 2 - 5, midY - 18, textWidth + 10, 16);
            ctx.strokeStyle = color;
            ctx.lineWidth = 1;
            ctx.strokeRect(midX - textWidth / 2 - 5, midY - 18, textWidth + 10, 16);

            ctx.fillStyle = color;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(label, midX, midY - 10);
        };

        // Render Line A (Yellow) and Line B (Emerald Green)
        drawSegment(this.lineA, "#ffdc00", "LINE A: GATE ENTRY");
        drawSegment(this.lineB, "#00ffaa", "LINE B: GANTRY ROAD");
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
        if (nameSpan) nameSpan.textContent = `Uploading ${file.name}...`;

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

            // Exclude yolov8n.pt from Speed tab - only include vehicles.pt / vehicle_speed.pt
            let speedModels = models.filter(m => (m === "vehicles.pt" || m === "vehicle_speed.pt" || m.toLowerCase().includes("vehicle")) && m !== "yolov8n.pt");
            if (speedModels.length === 0) {
                speedModels = ["vehicles.pt"];
            }

            this.availableModels = speedModels;

            // Fetch classes for vehicle models
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

            if (!this.modelClassesCache["vehicles.pt"] || this.modelClassesCache["vehicles.pt"].length === 0) {
                this.modelClassesCache["vehicles.pt"] = ["car", "bike", "truck", "pickup truck"];
            }
            if (!this.modelClassesCache["vehicle_speed.pt"] || this.modelClassesCache["vehicle_speed.pt"].length === 0) {
                this.modelClassesCache["vehicle_speed.pt"] = ["truck", "car", "pickup truck", "bike", "tank truck", "van", "bus"];
            }

            this.renderModelCards(speedModels);

            if (countSpan) countSpan.textContent = `${speedModels.length} vehicle model(s) ready`;
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
            const isSpeedDefault = true;
            const classes = this.modelClassesCache[m] || ["truck", "car", "pickup truck", "bike"];

            const card = document.createElement("div");
            card.className = "speed-model-card";
            card.id = `speed-model-card-${normName}`;
            card.style.cssText = `
                background: rgba(15, 23, 42, 0.6);
                border: 1px solid #38bdf8;
                border-radius: 8px;
                padding: 10px 14px;
                display: flex;
                flex-direction: column;
                gap: 8px;
                width: 100%;
                transition: all 0.2s ease;
            `;

            // 1. Header row
            const headerRow = document.createElement("div");
            headerRow.style.cssText = "display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;";

            const leftHeader = document.createElement("label");
            leftHeader.style.cssText = "display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;";

            const mChk = document.createElement("input");
            mChk.type = "checkbox";
            mChk.className = "speed-model-checkbox";
            mChk.value = m;
            mChk.checked = true;
            mChk.id = `chk-model-${normName}`;
            mChk.style.cursor = "pointer";

            const nameBadge = document.createElement("span");
            nameBadge.style.cssText = `
                font-family: var(--mono);
                font-size: 0.82rem;
                font-weight: 700;
                color: #00ffaa;
            `;
            nameBadge.textContent = m;

            leftHeader.appendChild(mChk);
            leftHeader.appendChild(nameBadge);

            // Right header: Select All / None & mini sliders
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

            // 2. Class Checkboxes
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
                const cLabel = document.createElement("label");
                cLabel.style.cssText = `
                    display: inline-flex;
                    align-items: center;
                    gap: 5px;
                    background: rgba(0,255,170,0.12);
                    border: 1px solid #00ffaa;
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
                cChk.checked = true;
                cChk.style.cursor = "pointer";

                cChk.addEventListener("change", () => {
                    cLabel.style.borderColor = cChk.checked ? "#00ffaa" : "var(--border)";
                    cLabel.style.background = cChk.checked ? "rgba(0,255,170,0.12)" : "rgba(0,0,0,0.3)";
                });

                const cSpan = document.createElement("span");
                cSpan.textContent = c;

                cLabel.appendChild(cChk);
                cLabel.appendChild(cSpan);
                classContainer.appendChild(cLabel);
            });

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

            if (cfg.line_a && typeof cfg.line_a === "object") {
                this.lineA = cfg.line_a;
            }
            if (cfg.line_b && typeof cfg.line_b === "object") {
                this.lineB = cfg.line_b;
            }

            this.updateLineLabels();
            this.renderCanvas();
        } catch (e) {
            console.error("[SPEED-DASH] Error loading config:", e);
        }
    }

    async saveConfig() {
        const limitInput = document.getElementById("speed-cfg-limit");
        const distInput = document.getElementById("speed-cfg-dist");
        const statusSpan = document.getElementById("speed-cfg-status");
        const hintSpan = document.getElementById("speed-draw-hint");

        const payload = {
            speed_limit_kmh: parseFloat(limitInput ? limitInput.value : 10.0),
            road_distance_meters: parseFloat(distInput ? distInput.value : 20.0),
            line_a: this.lineA,
            line_b: this.lineB,
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
                if (hintSpan) {
                    hintSpan.textContent = "✓ Calibration lines active!";
                    hintSpan.style.color = "#00ffaa";
                }
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

        // Save active line configuration before starting
        await this.saveConfig();

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

            if (res.ok && (data.status === "started" || data.status === "ok")) {
                if (statusSpan) {
                    statusSpan.textContent = "● AI Speed Monitor Running (Live Detection)";
                    statusSpan.style.color = "#00ffaa";
                }
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
        const loader = document.getElementById("speed-video-loader");
        if (loader) loader.style.display = "none";

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
        const loader = document.getElementById("speed-video-loader");
        if (!video) return;

        if (loader) loader.style.display = "flex";

        if (this.hlsRetryTimer) {
            clearTimeout(this.hlsRetryTimer);
            this.hlsRetryTimer = null;
        }

        const streamUrl = `/hls/camera/speed/playlist.m3u8?t=${Date.now()}`;

        // Verify playlist is available before attaching to prevent black screen / error loop
        fetch(streamUrl, { method: "HEAD" })
            .then(res => {
                if (res.ok) {
                    this._playHls(video, streamUrl, loader);
                } else {
                    if (retry && attempt < 20) {
                        this.hlsRetryTimer = setTimeout(() => this.attachHlsStream(true, attempt + 1), 600);
                    }
                }
            })
            .catch(() => {
                if (retry && attempt < 20) {
                    this.hlsRetryTimer = setTimeout(() => this.attachHlsStream(true, attempt + 1), 600);
                }
            });
    }

    _playHls(video, streamUrl, loader) {
        if (window.Hls && window.Hls.isSupported()) {
            if (this.hlsPlayer) {
                this.hlsPlayer.destroy();
                this.hlsPlayer = null;
            }

            const hls = new window.Hls({
                enableWorker: true,
                lowLatencyMode: true,
                liveSyncDurationCount: 1,
                maxBufferLength: 2,
                liveMaxLatencyDuration: 2.0,
                manifestLoadingMaxRetry: 15,
                manifestLoadingRetryDelay: 400
            });

            hls.loadSource(streamUrl);
            hls.attachMedia(video);

            hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
                if (loader) loader.style.display = "none";
                video.play().catch(() => {});
            });

            hls.on(window.Hls.Events.ERROR, (event, data) => {
                if (data.fatal) {
                    switch (data.type) {
                        case window.Hls.ErrorTypes.NETWORK_ERROR:
                            hls.startLoad();
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
            video.addEventListener("loadedmetadata", () => {
                if (loader) loader.style.display = "none";
                video.play().catch(() => {});
            });
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
