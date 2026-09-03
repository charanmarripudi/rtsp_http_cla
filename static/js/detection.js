const hlsInstances = {};
window.cameraTransitioning = window.cameraTransitioning || {};

function stopSimulatedCanvas(idx, video) {
    if (video && video.srcObject) { video.srcObject = null; }
}

function playHLS(video, url, idx) {
    if (video.dataset.currentUrl === url && hlsInstances[idx]) {
        return; // Stream is already playing this URL — don't interrupt or buffer!
    }
    video.dataset.currentUrl = url;
    if (hlsInstances[idx]) { 
        try {
            hlsInstances[idx].detachMedia();
            hlsInstances[idx].destroy();
        } catch (_) {}
        delete hlsInstances[idx]; 
    }
    
    // Clean reset of the video element to flush hardware decoders and prevent stalling
    try {
        video.pause();
        video.src = "";
        video.removeAttribute("src");
        video.load();
    } catch (_) {}

    const fullUrl = url + "?t=" + Date.now();
    if (typeof Hls === "undefined" || !Hls.isSupported()) { 
        video.src = fullUrl; 
        video.play().catch(() => {}); 
        return; 
    }
    
    const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        startPosition: -1,
        liveSyncDurationCount: 4.0,      // 4.0 segments cushion (absorbs Tailscale Funnel network spikes)
        liveMaxLatencyDurationCount: 8,  // Auto-catchup if delay > 8 segments
        liveDurationInfinity: true,
        liveBackBufferLength: 0,
        backBufferLength: 0,
        maxBufferLength: 10,
        maxMaxBufferLength: 15,
        manifestLoadingTimeOut: 20000,
        manifestLoadingMaxRetry: 10,
        manifestLoadingRetryDelay: 500,
        fragLoadingTimeOut: 20000,
        fragLoadingMaxRetry: 10,
        fragLoadingRetryDelay: 500
    });
    hlsInstances[idx] = hls;

    hls.attachMedia(video);
    hls.loadSource(fullUrl);

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
        stopSimulatedCanvas(idx, video);
        video.muted = true;
        video.playsInline = true;
        video.play().catch(() => {});
    });

    hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.details === 'bufferStalledError') {
            if (video.paused) {
                video.play().catch(() => {});
            }
            return;
        }
        if (data.fatal) { 
            switch(data.type) {
                case Hls.ErrorTypes.NETWORK_ERROR:
                    console.warn("HLS Network Error, attempting recovery...", data);
                    hls.startLoad();
                    break;
                case Hls.ErrorTypes.MEDIA_ERROR:
                    console.warn("HLS Media Error, attempting recovery...", data);
                    hls.recoverMediaError();
                    break;
                default:
                    console.error("Fatal HLS Error, restarting player...", data);
                    hls.destroy(); 
                    delete hlsInstances[idx]; 
                    setTimeout(() => playHLS(video, url, idx), 2000); 
                    break;
            }
        }
    });
}

async function waitAndSwitch(video, meta, idx, box, badge) {
    const start = Date.now();
    console.log(`[CLIENT-TIMER] waitAndSwitch started for Camera ${idx} at ${new Date().toLocaleTimeString()} (elapsed: 0ms)`);
    while (Date.now() - start < 90000) { // Keep waiting up to 90s for PyTorch loading
        if (!box.classList.contains("detecting")) return;
        try {
            const r = await fetch(meta.hls_detected + "?t=" + Date.now());
            if (r.ok) {
                const text = await r.text();
                const tsCount = (text.match(/\.ts/g) || []).length;
                if (tsCount >= 1) {
                    if (!box.classList.contains("detecting")) {
                        window.cameraTransitioning[idx] = false;
                        return;
                    }
                    console.log(`[CLIENT-TIMER] Camera ${idx} segments ready (tsCount: ${tsCount}) at ${new Date().toLocaleTimeString()} (elapsed: ${Date.now() - start}ms). Switching stream...`);
                    playHLS(video, meta.hls_detected, idx);
                    if (badge) badge.textContent = "● AI ACTIVE";
                    fetch("/api/stop-raw", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ camera: idx })
                    }).catch(() => {});
                    window.cameraTransitioning[idx] = false;
                    return;
                }
            }
        } catch (_) {}
        await new Promise(res => setTimeout(res, 400));
    }
    console.warn(`[CLIENT-TIMER] waitAndSwitch timed out for Camera ${idx} after ${Date.now() - start}ms`);
    window.cameraTransitioning[idx] = false;
}

async function waitAndSwitchRaw(video, meta, idx, box, badge) {
    const start = Date.now();
    while (Date.now() - start < 90000) { // Keep waiting up to 90s for raw stream to recover
        if (box.classList.contains("detecting")) {
            window.cameraTransitioning[idx] = false;
            return;
        }
        try {
            const r = await fetch(meta.hls_raw + "?t=" + Date.now());
            if (r.ok) {
                const text = await r.text();
                if (text.includes(".ts")) {
                    if (box.classList.contains("detecting")) {
                        window.cameraTransitioning[idx] = false;
                        return;
                    }
                    playHLS(video, meta.hls_raw, idx);
                    if (badge) badge.textContent = "○ RAW";
                    window.cameraTransitioning[idx] = false;
                    return;
                }
            }
        } catch (_) {}
        await new Promise(res => setTimeout(res, 400));
    }
    window.cameraTransitioning[idx] = false;
}

function renderUI(box, i, meta, status, cameraModelsMap) {
    const video = box.querySelector("video");
    const camStr = String(i);
    const badge = box.querySelector(".mode-badge");
    const assigned = cameraModelsMap[camStr] || [];
    const assignedText = assigned.map(m => m.replace(".pt", "")).join(", ");
    
    const updateBadge = (isActive) => {
        if (!badge) return;
        if (isActive) {
            badge.className = "mode-badge active";
            badge.textContent = assignedText ? "● AI: " + assignedText : "● AI ACTIVE";
        } else {
            badge.className = "mode-badge raw";
            badge.textContent = "○ RAW";
        }
    };

    // Restore state
    if (status.active && status.active.includes(camStr)) {
        box.classList.add("detecting");
        updateBadge(true);
        playHLS(video, meta.hls_detected, i);
    } else {
        box.classList.remove("detecting");
        updateBadge(false);
        playHLS(video, meta.hls_raw, i);
    }

    // Render Assigned Models with Per-Model Conf & IoU Sliders (Matching UI Screenshot, Smooth Dragging!)
    const chipsList = box.querySelector(".assigned-chips-list");
    let thresholdSyncTimer = null;
    if (chipsList) {
        const existingCards = chipsList.querySelectorAll(".model-card-box");
        const currentModelKeys = assigned.map(m => m.replace(".pt", "")).join(",");
        const existingModelKeys = Array.from(existingCards).map(c => (c.getAttribute("data-model") || "").replace(".pt", "")).join(",");

        if (existingCards.length === 0 || currentModelKeys !== existingModelKeys) {
            chipsList.innerHTML = "";
            const modelConfigs = meta.model_configs || {};
            const uniqueAssigned = [];
            const seenCleanNames = new Set();
            (assigned || []).forEach(m => {
                const clean = m.replace(".pt", "");
                if (!seenCleanNames.has(clean)) {
                    seenCleanNames.add(clean);
                    uniqueAssigned.push(m);
                }
            });

            uniqueAssigned.forEach(m => {
                const cleanName = m.replace(".pt", "");
                const mCfg = modelConfigs[m] || modelConfigs[cleanName] || {};
                const enabled = Array.from(new Set(mCfg.enabled_classes || []));
                if (enabled.length === 0) {
                    return;
                }

                if (enabled.length > 0) {
                    enabled.forEach(cls => {
                        const existingCard = chipsList.querySelector(`[data-model-clean="${cleanName}"][data-class="${cls}"]`);
                        if (existingCard) return;

                        const cCfg = (mCfg.class_configs && mCfg.class_configs[cls]) || {};
                        const cVal = cCfg.conf !== undefined ? parseFloat(cCfg.conf).toFixed(2) : parseFloat(mCfg.conf || meta.conf || 0.40).toFixed(2);
                        const iVal = cCfg.iou !== undefined ? parseFloat(cCfg.iou).toFixed(2) : parseFloat(mCfg.iou || meta.iou || 0.45).toFixed(2);

                        const card = document.createElement("div");
                        card.className = "model-card-box";
                        card.setAttribute("data-model", m);
                        card.setAttribute("data-model-clean", cleanName);
                        card.setAttribute("data-class", cls);
                        card.style.cssText = "width:100%;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:8px 10px;margin-bottom:6px;";
                        card.innerHTML = `
                            <div class="model-chip-header" style="margin-bottom:6px;">
                                <span class="model-chip checked" style="font-size:0.75rem;padding:3px 10px;border-radius:12px;background:rgba(0,255,170,0.12);color:#00ffaa;border:1px solid rgba(0,255,170,0.3);font-family:var(--mono);font-weight:600;display:inline-flex;align-items:center;gap:6px;"><span style="width:8px;height:8px;background:#f5a623;border-radius:2px;display:inline-block;"></span>${cleanName} - ${cls}</span>
                            </div>
                            <div class="thresholds compact" style="display:flex;gap:12px;">
                                <div class="thresh-row" style="flex:1;"><label style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--muted);">Conf <span class="model-conf-val" style="color:#00ffaa;font-weight:700;">${cVal}</span></label><input class="model-conf-slider" type="range" min="0.05" max="0.95" step="0.01" value="${cVal}"></div>
                                <div class="thresh-row" style="flex:1;"><label style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--muted);">IoU <span class="model-iou-val" style="color:#00ffaa;font-weight:700;">${iVal}</span></label><input class="model-iou-slider" type="range" min="0.05" max="0.95" step="0.01" value="${iVal}"></div>
                            </div>`;
                        chipsList.appendChild(card);

                        const cSlider = card.querySelector(".model-conf-slider");
                        const iSlider = card.querySelector(".model-iou-slider");
                        const cSpan = card.querySelector(".model-conf-val");
                        const iSpan = card.querySelector(".model-iou-val");

                        const syncClassThreshold = () => {
                            clearTimeout(thresholdSyncTimer);
                            thresholdSyncTimer = setTimeout(() => {
                                const cNum = parseFloat(cSlider.value);
                                const iNum = parseFloat(iSlider.value);
                                
                                meta.model_configs = meta.model_configs || {};
                                const normM = m.endsWith(".pt") ? m : `${m}.pt`;
                                meta.model_configs[normM] = meta.model_configs[normM] || {};
                                meta.model_configs[normM].class_configs = meta.model_configs[normM].class_configs || {};
                                meta.model_configs[normM].class_configs[cls] = { conf: cNum, iou: iNum };

                                if (window.roiDrawStates && window.roiDrawStates[i] && window.roiDrawStates[i].vertices && window.roiDrawStates[i].vertices.length === 2) {
                                    meta.model_configs.roi_polygon = window.roiDrawStates[i].vertices;
                                }

                                fetch("/api/update-thresholds", {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ camera: i, model: m, conf: cNum, iou: iNum, model_configs: meta.model_configs })
                                }).catch(() => {});
                            }, 200);
                        };

                        if (cSlider) {
                            cSlider.oninput = () => {
                                if (cSpan) cSpan.textContent = parseFloat(cSlider.value).toFixed(2);
                                syncClassThreshold();
                            };
                        }
                        if (iSlider) {
                            iSlider.oninput = () => {
                                if (iSpan) iSpan.textContent = parseFloat(iSlider.value).toFixed(2);
                                syncClassThreshold();
                            };
                        }
                    });
                } else {
                    const cVal = mCfg.conf !== undefined ? parseFloat(mCfg.conf).toFixed(2) : (meta.conf !== undefined ? parseFloat(meta.conf).toFixed(2) : "0.40");
                    const iVal = mCfg.iou !== undefined ? parseFloat(mCfg.iou).toFixed(2) : (meta.iou !== undefined ? parseFloat(meta.iou).toFixed(2) : "0.45");

                    const card = document.createElement("div");
                    card.className = "model-card-box";
                    card.setAttribute("data-model", m);
                    card.style.cssText = "width:100%;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:8px 10px;margin-bottom:6px;";
                    card.innerHTML = `
                        <div class="model-chip-header" style="margin-bottom:6px;">
                            <span class="model-chip checked" style="font-size:0.75rem;padding:3px 10px;border-radius:12px;background:rgba(0,255,170,0.12);color:#00ffaa;border:1px solid rgba(0,255,170,0.3);font-family:var(--mono);font-weight:600;display:inline-flex;align-items:center;gap:6px;"><span style="width:8px;height:8px;background:#f5a623;border-radius:2px;display:inline-block;"></span>${cleanName}</span>
                        </div>
                        <div class="thresholds compact" style="display:flex;gap:12px;">
                            <div class="thresh-row" style="flex:1;"><label style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--muted);">Conf <span class="model-conf-val" style="color:#00ffaa;font-weight:700;">${cVal}</span></label><input class="model-conf-slider" type="range" min="0.05" max="0.95" step="0.01" value="${cVal}"></div>
                            <div class="thresh-row" style="flex:1;"><label style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--muted);">IoU <span class="model-iou-val" style="color:#00ffaa;font-weight:700;">${iVal}</span></label><input class="model-iou-slider" type="range" min="0.05" max="0.95" step="0.01" value="${iVal}"></div>
                        </div>`;
                    chipsList.appendChild(card);

                    const cSlider = card.querySelector(".model-conf-slider");
                    const iSlider = card.querySelector(".model-iou-slider");
                    const cSpan = card.querySelector(".model-conf-val");
                    const iSpan = card.querySelector(".model-iou-val");

                    const syncModelThreshold = () => {
                        clearTimeout(thresholdSyncTimer);
                        thresholdSyncTimer = setTimeout(() => {
                            const cNum = parseFloat(cSlider.value);
                            const iNum = parseFloat(iSlider.value);
                            meta.model_configs = meta.model_configs || {};
                            const normM = m.endsWith(".pt") ? m : `${m}.pt`;
                            meta.model_configs[normM] = { conf: cNum, iou: iNum };

                            if (window.roiDrawStates && window.roiDrawStates[i] && window.roiDrawStates[i].vertices && window.roiDrawStates[i].vertices.length === 2) {
                                meta.model_configs.roi_polygon = window.roiDrawStates[i].vertices;
                            }

                            fetch("/api/update-thresholds", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ camera: i, model: m, conf: cNum, iou: iNum, model_configs: meta.model_configs })
                            }).catch(() => {});
                        }, 200);
                    };

                    if (cSlider) {
                        cSlider.oninput = () => {
                            if (cSpan) cSpan.textContent = parseFloat(cSlider.value).toFixed(2);
                            syncModelThreshold();
                        };
                    }
                    if (iSlider) {
                        iSlider.oninput = () => {
                            if (iSpan) iSpan.textContent = parseFloat(iSlider.value).toFixed(2);
                            syncModelThreshold();
                        };
                    }
                }
            });
        }
    }

    box.querySelector(".start").onclick = async () => {
        const cm = await (await fetch("/api/camera-models")).json();
        const models = cm[camStr] || [];
        if (!models.length) return alert("No models assigned");
        
        const domModelConfigs = JSON.parse(JSON.stringify(meta.model_configs || {}));
        if (window.roiDrawStates && window.roiDrawStates[i] && window.roiDrawStates[i].vertices && window.roiDrawStates[i].vertices.length === 2) {
            domModelConfigs.roi_polygon = window.roiDrawStates[i].vertices;
        }
        box.querySelectorAll(".model-card-box").forEach(card => {
            const mName = card.getAttribute("data-model");
            const cls = card.getAttribute("data-class");
            const cSlider = card.querySelector(".model-conf-slider");
            const iSlider = card.querySelector(".model-iou-slider");
            if (mName && cSlider && iSlider) {
                const cVal = parseFloat(cSlider.value);
                const iVal = parseFloat(iSlider.value);
                const clean = mName.replace(".pt", "");
                
                if (cls) {
                    domModelConfigs[mName] = domModelConfigs[mName] || {};
                    domModelConfigs[mName].class_configs = domModelConfigs[mName].class_configs || {};
                    domModelConfigs[mName].class_configs[cls] = { conf: cVal, iou: iVal };

                    domModelConfigs[clean] = domModelConfigs[clean] || {};
                    domModelConfigs[clean].class_configs = domModelConfigs[clean].class_configs || {};
                    domModelConfigs[clean].class_configs[cls] = { conf: cVal, iou: iVal };
                } else {
                    domModelConfigs[mName] = Object.assign({}, domModelConfigs[mName], { conf: cVal, iou: iVal });
                    domModelConfigs[clean] = Object.assign({}, domModelConfigs[clean], { conf: cVal, iou: iVal });
                }
            }
        });

        const conf = meta.conf || 0.40;
        const iou = meta.iou || 0.45;
        const location = meta.location || meta.label || "Camera " + (i+1);
        
        console.log(`[CLIENT-TIMER] Start button clicked for Camera ${i} at ${new Date().toLocaleTimeString()}`);
        box.classList.add("detecting");
        updateBadge(true);
        if (badge) badge.textContent = "● AI: STARTING...";
        
        window.cameraTransitioning[camStr] = true;
        const apiStart = Date.now();
        console.log(`[CLIENT-TIMER] Sending /api/start for Camera ${i} at ${new Date().toLocaleTimeString()}...`);
        await fetch("/api/start", { 
            method: "POST", 
            headers: { "Content-Type": "application/json" }, 
            body: JSON.stringify({ camera: i, models, rtsp: meta.rtsp, conf, iou, location, model_configs: domModelConfigs }) 
        });
        console.log(`[CLIENT-TIMER] /api/start response received for Camera ${i} at ${new Date().toLocaleTimeString()} (duration: ${Date.now() - apiStart}ms)`);
        waitAndSwitch(video, meta, i, box, badge);
    };

    box.querySelector(".stop").onclick = async () => {
        box.classList.remove("detecting");
        updateBadge(false);
        if (badge) badge.textContent = "○ RAW: SWITCHING...";

        window.cameraTransitioning[camStr] = true;
        await fetch("/api/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ camera: i }) });
        waitAndSwitchRaw(video, meta, i, box, badge);
    };
}

async function init() {
    const [streams, status, cameraModels] = await Promise.all([
        fetch("/api/streams").then(r => r.json()),
        fetch("/api/status").then(r => r.json()),
        fetch("/api/camera-models").then(r => r.json())
    ]);

    const streamsById = new Map(streams.map(stream => [String(stream.id), stream]));
    document.querySelectorAll(".box").forEach((box) => {
        const camId = box.dataset.cameraId;
        const meta = streamsById.get(String(camId));
        if (meta) renderUI(box, Number(camId), meta, status, cameraModels);
        else box.style.display = "none";
    });
}

// Background sync for thresholds and status across all client devices
let pollInterval = null;
function startClientSync() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(async () => {
        try {
            const [streams, status] = await Promise.all([
                fetch("/api/streams").then(r => r.json()),
                fetch("/api/status").then(r => r.json())
            ]);
            const streamsById = new Map(streams.map(s => [String(s.id), s]));
            document.querySelectorAll(".box").forEach((box) => {
                const camId = box.dataset.cameraId;
                if (window.cameraTransitioning[camId]) return;
                const meta = streamsById.get(String(camId));
                if (!meta) return;
                
                const confSlider = box.querySelector(".conf-slider");
                const iouSlider = box.querySelector(".iou-slider");
                
                // Update sliders only if user is not actively dragging them
                if (confSlider && document.activeElement !== confSlider && meta.conf !== undefined) {
                    if (Math.abs(parseFloat(confSlider.value) - parseFloat(meta.conf)) > 0.005) {
                        confSlider.value = meta.conf;
                        const span = box.querySelector(".conf-val");
                        if (span) span.textContent = parseFloat(meta.conf).toFixed(2);
                    }
                }
                if (iouSlider && document.activeElement !== iouSlider && meta.iou !== undefined) {
                    if (Math.abs(parseFloat(iouSlider.value) - parseFloat(meta.iou)) > 0.005) {
                        iouSlider.value = meta.iou;
                        const span = box.querySelector(".iou-val");
                        if (span) span.textContent = parseFloat(meta.iou).toFixed(2);
                    }
                }

                // Synchronize detection state across different browser tabs without restarting video
                const isDetecting = status.active && status.active.includes(String(camId));
                const badge = box.querySelector(".mode-badge");
                const video = box.querySelector("video");
                
                if (isDetecting && !box.classList.contains("detecting")) {
                    box.classList.add("detecting");
                    if (badge) { badge.className = "mode-badge active"; badge.textContent = "● AI: STARTING..."; }
                    window.cameraTransitioning[camId] = true;
                    waitAndSwitch(video, meta, Number(camId), box, badge);
                } else if (!isDetecting && box.classList.contains("detecting")) {
                    box.classList.remove("detecting");
                    if (badge) { badge.className = "mode-badge raw"; badge.textContent = "○ RAW: SWITCHING..."; }
                    window.cameraTransitioning[camId] = true;
                    waitAndSwitchRaw(video, meta, Number(camId), box, badge);
                }
            });
        } catch (_) {}
    }, 4000);
}

window.RtspDetection = { init };
window.addEventListener("load", () => {
    init();
    startClientSync();
});
window.addEventListener("rtsp-dashboard-ready", () => {
    init();
    startClientSync();
});




// const hlsInstances = {};

// function stopSimulatedCanvas(idx, video) {
//     if (video && video.srcObject) { video.srcObject = null; }
// }

// function playHLS(video, url, idx) {
//     if (hlsInstances[idx]) { hlsInstances[idx].destroy(); delete hlsInstances[idx]; }
//     const fullUrl = url + "?t=" + Date.now();
//     if (typeof Hls === "undefined" || !Hls.isSupported()) { video.src = fullUrl; video.play().catch(() => {}); return; }
    
//     const hls = new Hls({
//         enableWorker: true,
//         lowLatencyMode: true,
//         liveSyncDurationCount: 2,
//         liveBackBufferLength: 0,  // Don't keep old segments — prevents stale video on page refresh
//         backBufferLength: 0,      // Clear back-buffer so switching streams always plays from live edge
//         maxBufferLength: 6
//     });
//     hlsInstances[idx] = hls;
//     hls.loadSource(fullUrl);
//     hls.on(Hls.Events.MANIFEST_PARSED, () => {
//         stopSimulatedCanvas(idx, video);
//         hls.attachMedia(video);
//         video.play().catch(() => {});
//     });
//     hls.on(Hls.Events.ERROR, (_, data) => {
//         if (data.fatal) { hls.destroy(); delete hlsInstances[idx]; setTimeout(() => playHLS(video, url, idx), 2000); }
//     });
// }

// async function waitAndSwitch(video, meta, idx) {
//     const start = Date.now();
//     while (Date.now() - start < 30000) {
//         try {
//             const r = await fetch(meta.hls_detected + "?t=" + Date.now());
//             if (r.ok && (await r.text()).includes(".ts")) {
//                 playHLS(video, meta.hls_detected, idx);
//                 return;
//             }
//         } catch (_) {}
//         await new Promise(res => setTimeout(res, 500));
//     }
// }

// function renderUI(box, i, meta, status, cameraModelsMap) {
//     const video = box.querySelector("video");
//     const camStr = String(i);
//     const badge = box.querySelector(".mode-badge");
//     const assigned = cameraModelsMap[camStr] || [];
//     const assignedText = assigned.map(m => m.replace(".pt", "")).join(", ");
    
//     const updateBadge = (isActive) => {
//         if (!badge) return;
//         if (isActive) {
//             badge.className = "mode-badge active";
//             badge.textContent = assignedText ? "● AI: " + assignedText : "● AI ACTIVE";
//         } else {
//             badge.className = "mode-badge raw";
//             badge.textContent = "○ RAW";
//         }
//     };

//     // Restore state
//     if (status.active.includes(camStr)) {
//         box.classList.add("detecting");
//         updateBadge(true);
//         playHLS(video, meta.hls_detected, i);
//     } else {
//         updateBadge(false);
//         playHLS(video, meta.hls_raw, i);
//     }

//     // Render Assigned Models
//     const chipsList = box.querySelector(".assigned-chips-list");
//     if (chipsList) {
//         chipsList.innerHTML = "";
//         assigned.forEach(m => {
//             const chip = document.createElement("span");
//             chip.className = "model-chip checked";
//             chip.textContent = m.replace(".pt", "");
//             chipsList.appendChild(chip);
//         });
//     }

//     // Restore Sliders
//     const confSlider = box.querySelector(".conf-slider");
//     const iouSlider = box.querySelector(".iou-slider");
//     if (confSlider) {
//         confSlider.oninput = () => box.querySelector(".conf-val").textContent = parseFloat(confSlider.value).toFixed(2);
//     }
//     if (iouSlider) {
//         iouSlider.oninput = () => box.querySelector(".iou-val").textContent = parseFloat(iouSlider.value).toFixed(2);
//     }

//     box.querySelector(".start").onclick = async () => {
//         const cm = await (await fetch("/api/camera-models")).json();
//         const models = cm[camStr] || [];
//         if (!models.length) return alert("No models assigned");
        
//         const conf = confSlider ? parseFloat(confSlider.value) : 0.25;
//         const iou = iouSlider ? parseFloat(iouSlider.value) : 0.45;
        
//         const location = meta.location || meta.label || "Camera " + (i+1);
        
//         box.classList.add("detecting");
//         updateBadge(true);
        
//         await fetch("/api/start", { 
//             method: "POST", 
//             headers: { "Content-Type": "application/json" }, 
//             body: JSON.stringify({ camera: i, models, rtsp: meta.rtsp, conf, iou, location }) 
//         });
//         waitAndSwitch(video, meta, i);
//     };

//     box.querySelector(".stop").onclick = async () => {
//         box.classList.remove("detecting");
//         updateBadge(false);
//         await fetch("/api/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ camera: i }) });
//         // Wait for server to: (1) kill detector process, (2) clean stale detected segments,
//         // (3) start fresh raw FFmpeg and write initial HLS segments.
//         // Without this delay the player switches to raw before fresh segments exist → black screen.
//         await new Promise(res => setTimeout(res, 1500));
//         playHLS(video, meta.hls_raw, i);
//     };
// }

// async function init() {
//     const [streams, status, cameraModels] = await Promise.all([
//         fetch("/api/streams").then(r => r.json()),
//         fetch("/api/status").then(r => r.json()),
//         fetch("/api/camera-models").then(r => r.json())
//     ]);

//     const streamsById = new Map(streams.map(stream => [String(stream.id), stream]));
//     document.querySelectorAll(".box").forEach((box) => {
//         const camId = box.dataset.cameraId;
//         const meta = streamsById.get(String(camId));
//         if (meta) renderUI(box, Number(camId), meta, status, cameraModels);
//         else box.style.display = "none";
//     });
// }

// window.RtspDetection = { init };
// window.addEventListener("load", init);
// window.addEventListener("rtsp-dashboard-ready", init);
