const hlsInstances = {};

function stopSimulatedCanvas(idx, video) {
    if (video && video.srcObject) { video.srcObject = null; }
}

function playHLS(video, url, idx) {
    if (video.dataset.currentUrl === url && hlsInstances[idx]) {
        return; // Stream is already playing this URL — don't interrupt or buffer!
    }
    video.dataset.currentUrl = url;
    if (hlsInstances[idx]) { hlsInstances[idx].destroy(); delete hlsInstances[idx]; }
    const fullUrl = url + "?t=" + Date.now();
    if (typeof Hls === "undefined" || !Hls.isSupported()) { 
        video.src = fullUrl; 
        video.play().catch(() => {}); 
        return; 
    }
    
    const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        startPosition: -1,               // Forces player to start at liveSyncPosition (exact 4-5s cushion)
        liveSyncDurationCount: 2.5,      // 2.5 segments (5.0s cushion) — guarantees playhead never hits live edge (1.42/1.42)
        liveMaxLatencyDurationCount: 6,  // Auto-catchup if delay drifts beyond 12s
        liveDurationInfinity: true,      // Continuous rolling live stream across all devices
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
        if (hls.liveSyncPosition && Number.isFinite(hls.liveSyncPosition)) {
            try { video.currentTime = hls.liveSyncPosition; } catch (_) {}
        }
        video.play().catch(() => {});
    });

    hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.details === 'bufferStalledError') {
            if (hls.liveSyncPosition && Number.isFinite(hls.liveSyncPosition)) {
                try { video.currentTime = hls.liveSyncPosition; } catch (_) {}
            }
            if (video.paused) {
                video.play().catch(() => {});
            }
            return;
        }
        if (data.fatal) { 
            hls.destroy(); 
            delete hlsInstances[idx]; 
            setTimeout(() => playHLS(video, url, idx), 2000); 
        }
    });
}

async function waitAndSwitch(video, meta, idx, box, badge) {
    const start = Date.now();
    while (Date.now() - start < 35000) {
        if (!box.classList.contains("detecting")) return;
        try {
            const r = await fetch(meta.hls_detected + "?t=" + Date.now());
            if (r.ok) {
                const text = await r.text();
                const tsCount = (text.match(/\.ts/g) || []).length;
                if (tsCount >= 2) {
                    if (!box.classList.contains("detecting")) return;
                    playHLS(video, meta.hls_detected, idx);
                    if (badge) badge.textContent = "● AI ACTIVE";
                    return;
                }
            }
        } catch (_) {}
        await new Promise(res => setTimeout(res, 500));
    }
}

async function waitAndSwitchRaw(video, meta, idx, box, badge) {
    const start = Date.now();
    while (Date.now() - start < 35000) {
        if (box.classList.contains("detecting")) return;
        try {
            const r = await fetch(meta.hls_raw + "?t=" + Date.now());
            if (r.ok) {
                const text = await r.text();
                if (text.includes(".ts")) {
                    if (box.classList.contains("detecting")) return;
                    playHLS(video, meta.hls_raw, idx);
                    if (badge) badge.textContent = "○ RAW";
                    return;
                }
            }
        } catch (_) {}
        await new Promise(res => setTimeout(res, 500));
    }
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
    if (status.active.includes(camStr)) {
        box.classList.add("detecting");
        updateBadge(true);
        playHLS(video, meta.hls_detected, i);
    } else {
        updateBadge(false);
        playHLS(video, meta.hls_raw, i);
    }

    // Render Assigned Models
    const chipsList = box.querySelector(".assigned-chips-list");
    if (chipsList) {
        chipsList.innerHTML = "";
        assigned.forEach(m => {
            const chip = document.createElement("span");
            chip.className = "model-chip checked";
            chip.textContent = m.replace(".pt", "");
            chipsList.appendChild(chip);
        });
    }

    // Restore Sliders
    const confSlider = box.querySelector(".conf-slider");
    const iouSlider = box.querySelector(".iou-slider");
    if (meta.conf !== undefined && confSlider) {
        confSlider.value = meta.conf;
        const valSpan = box.querySelector(".conf-val");
        if (valSpan) valSpan.textContent = parseFloat(meta.conf).toFixed(2);
    }
    if (meta.iou !== undefined && iouSlider) {
        iouSlider.value = meta.iou;
        const valSpan = box.querySelector(".iou-val");
        if (valSpan) valSpan.textContent = parseFloat(meta.iou).toFixed(2);
    }
    if (confSlider) {
        confSlider.oninput = () => box.querySelector(".conf-val").textContent = parseFloat(confSlider.value).toFixed(2);
    }
    if (iouSlider) {
        iouSlider.oninput = () => box.querySelector(".iou-val").textContent = parseFloat(iouSlider.value).toFixed(2);
    }

    box.querySelector(".start").onclick = async () => {
        const cm = await (await fetch("/api/camera-models")).json();
        const models = cm[camStr] || [];
        if (!models.length) return alert("No models assigned");
        
        const conf = confSlider ? parseFloat(confSlider.value) : (meta.conf || 0.40);
        const iou = iouSlider ? parseFloat(iouSlider.value) : (meta.iou || 0.45);
        const location = meta.location || meta.label || "Camera " + (i+1);
        
        box.classList.add("detecting");
        updateBadge(true);
        if (badge) badge.textContent = "● AI: STARTING...";
        
        await fetch("/api/start", { 
            method: "POST", 
            headers: { "Content-Type": "application/json" }, 
            body: JSON.stringify({ camera: i, models, rtsp: meta.rtsp, conf, iou, location }) 
        });
        waitAndSwitch(video, meta, i, box, badge);
    };

    box.querySelector(".stop").onclick = async () => {
        box.classList.remove("detecting");
        updateBadge(false);
        if (badge) badge.textContent = "○ RAW: SWITCHING...";

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

window.RtspDetection = { init };
window.addEventListener("load", init);
window.addEventListener("rtsp-dashboard-ready", init);




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
