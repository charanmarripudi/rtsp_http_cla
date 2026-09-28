/**
 * Plug-and-Play Vehicle Speed & Count Dashboard Module
 * Dedicated Tab for Real-Time Tank Truck Speed Enforcement & Telemetry
 * Supports: Location Cameras, Custom RTSP URLs, and Uploaded/Local Test Videos
 */

class SpeedDashboard {
    constructor() {
        this.activeCameraId = "speed";
        this.sourceType = "rtsp"; // "rtsp", "video"
        this.uploadedVideoPath = "";
        this.pollInterval = null;
        this.hlsPlayer = null;
        this.initialized = false;
        this.availableModels = [];
    }

    init() {
        if (this.initialized) return;
        this.initialized = true;

        this.bindEvents();
        this.loadModels();
        this.loadConfig();
        this.startPolling();
        this.setSourceType("rtsp");
    }

    async loadModels() {
        const container = document.getElementById("speed-models-checkboxes");
        const countSpan = document.getElementById("speed-models-count");
        if (!container) return;

        try {
            const res = await fetch("/api/models");
            if (!res.ok) return;
            const data = await res.json();
            const models = data.models || ["vehicle_speed.pt", "yolov8n.pt"];
            this.availableModels = models;

            container.innerHTML = "";
            models.forEach(m => {
                const normName = m.replace(".pt", "");
                const isDefault = normName.includes("speed") || normName.includes("vehicle") || models.length === 1;

                const card = document.createElement("label");
                card.style.cssText = `
                    display: inline-flex; align-items: center; gap: 6px;
                    background: rgba(0,0,0,0.4); border: 1px solid ${isDefault ? '#38bdf8' : 'var(--border)'};
                    padding: 4px 10px; border-radius: 6px; cursor: pointer; user-select: none;
                    font-family: var(--mono); font-size: 0.72rem; transition: all 0.2s ease;
                `;

                const chk = document.createElement("input");
                chk.type = "checkbox";
                chk.className = "speed-model-checkbox";
                chk.value = m;
                chk.checked = isDefault;
                chk.style.cursor = "pointer";

                chk.addEventListener("change", () => {
                    card.style.borderColor = chk.checked ? "#38bdf8" : "var(--border)";
                    card.style.background = chk.checked ? "rgba(56,189,248,0.12)" : "rgba(0,0,0,0.4)";
                });

                if (isDefault) {
                    card.style.background = "rgba(56,189,248,0.12)";
                }

                const nameText = document.createElement("span");
                nameText.textContent = m;
                nameText.style.color = isDefault ? "#00ffaa" : "var(--text)";

                card.appendChild(chk);
                card.appendChild(nameText);
                container.appendChild(card);
            });

            if (countSpan) countSpan.textContent = `${models.length} available model(s)`;
        } catch (e) {
            console.error("[SPEED-DASH] Error loading models:", e);
            if (countSpan) countSpan.textContent = "Using default vehicle_speed.pt";
        }
    }
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
            if (statusSpan) statusSpan.textContent = "Saving...";
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
        const checkedBoxes = document.querySelectorAll(".speed-model-checkbox:checked");
        const selectedModels = Array.from(checkedBoxes).map(cb => cb.value);

        const payload = {
            camera: "speed",
            source_type: this.sourceType,
            models: selectedModels.length > 0 ? selectedModels : ["vehicle_speed.pt"]
        };

        console.log(`[SPEED-DASH] Starting Speed Monitor (Source: ${this.sourceType}, Models: ${payload.models.join(',')})...`);

        if (this.sourceType === "rtsp") {
            const rtspInput = document.getElementById("speed-custom-rtsp-input");
            payload.rtsp = rtspInput ? (rtspInput.value.trim() || rtspInput.placeholder.trim()) : "";
            if (!payload.rtsp) {
                if (statusSpan) {
                    statusSpan.textContent = "Please enter an RTSP URL";
                    statusSpan.style.color = "#ff4444";
                }
                return;
            }
        } else if (this.sourceType === "video") {
            const serverInput = document.getElementById("speed-server-video-input");
            const manualPath = serverInput ? serverInput.value.trim() : "";
            payload.video_path = manualPath || this.uploadedVideoPath;
            if (!payload.video_path) {
                if (statusSpan) {
                    statusSpan.textContent = "Please upload or specify a video file";
                    statusSpan.style.color = "#ff4444";
                }
                return;
            }
        }

        try {
            if (statusSpan) {
                statusSpan.textContent = "Starting AI Speed Tracker...";
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
                    statusSpan.textContent = "● AI Speed Monitor Running";
                    statusSpan.style.color = "#00ffaa";
                }
                setTimeout(() => this.updatePlayerSource(true), 800);
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

    updatePlayerSource(forcePlay = false) {
        const video = document.getElementById("speed-video-player");
        if (!video) return;

        const streamUrl = `/hls/camera/speed/playlist.m3u8?t=${Date.now()}`;

        if (!forcePlay) {
            const statusSpan = document.getElementById("speed-monitor-status");
            const isRunning = statusSpan && statusSpan.textContent.includes("Running");
            if (!isRunning) return;
        }

        if (window.Hls && window.Hls.isSupported()) {
            if (this.hlsPlayer) {
                this.hlsPlayer.destroy();
            }
            const hls = new window.Hls({
                enableWorker: true,
                lowLatencyMode: true,
                liveSyncDurationCount: 2,
                maxBufferLength: 4
            });
            hls.loadSource(streamUrl);
            hls.attachMedia(video);
            hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
                video.play().catch(() => {});
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
        this.pollInterval = setInterval(() => this.pollStats(), 1500);
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
        window.speedDashboard.updatePlayerSource();
    }
};

// Auto-initialize on load and DOMContentLoaded
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => window.initSpeedTab());
} else {
    window.initSpeedTab();
}
