// Multi-Camera ONVIF PTZ Controller Module
(function () {
  let currentMode = "continuous";
  let currentSpeed = 0.5;
  let currentDuration = 0.35;
  let isMoving = false;
  let ptzCameras = [];
  let activePtzCamera = null;
  let ptzHlsInstance = null;

  window.initPtzController = async function () {
    initPtzControls();
    await loadPtzCameras();
  };

  async function loadPtzCameras() {
    try {
      const res = await fetch("/api/ptz/cameras");
      ptzCameras = await res.json();
    } catch (e) {
      console.warn("Failed to fetch PTZ cameras, using default:", e);
      ptzCameras = [
        {
          id: "ptz-0",
          label: "AMBICAM PTZ 1",
          rtsp: "rtsp://admin:@192.168.96.30:554/ch0_0.264",
          ip: "192.168.96.30",
          port: 8888,
          username: "admin",
          password: "",
          hls_raw: "/hls/streamptz0_raw/playlist.m3u8",
        },
      ];
    }

    renderPtzCameraSelect();
  }

  function renderPtzCameraSelect() {
    const select = document.getElementById("ptzCameraSelect");
    if (!select) return;

    select.innerHTML = ptzCameras
      .map(
        (cam, idx) => `
      <option value="${idx}">📹 ${escapeHtml(cam.label || "PTZ Camera " + (idx + 1))} (${cam.ip || "192.168.96.30"})</option>
    `
      )
      .join("");

    if (ptzCameras.length > 0) {
      switchActivePtzCamera(0);
    }

    select.onchange = (e) => {
      const idx = parseInt(e.target.value, 10);
      switchActivePtzCamera(idx);
    };
  }

  function switchActivePtzCamera(index) {
    if (!ptzCameras[index]) return;
    activePtzCamera = ptzCameras[index];

    // Update Header labels
    const ipLabel = document.getElementById("ptzCameraIpLabel");
    if (ipLabel) {
      ipLabel.textContent = `${activePtzCamera.ip}:${activePtzCamera.port || 8888} (ONVIF)`;
    }

    const titleEl = document.getElementById("ptzCameraTitle");
    if (titleEl) {
      titleEl.textContent = activePtzCamera.label || "AMBICAM PTZ Preview";
    }

    // Start video stream for this PTZ camera
    playPtzStream(activePtzCamera, index);

    // Load presets for this specific PTZ camera
    loadPtzPresets();
  }

  function playPtzStream(cam, index) {
    const video = document.getElementById("ptzLiveVideo");
    if (!video) return;

    const overlay = document.getElementById("ptzVideoOverlay");
    if (overlay) {
      overlay.style.display = "block";
      overlay.textContent = "Connecting to live PTZ stream...";
    }

    const cid = `ptz${index}`;
    const hlsUrl = `/hls/stream${cid}_raw/playlist.m3u8?t=${Date.now()}`;

    // Make sure backend has started FFmpeg transcoding for this PTZ RTSP URL
    fetch("/api/ptz/cameras/start-stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cid: cid, rtsp: cam.rtsp }),
    }).catch(() => {});

    if (ptzHlsInstance) {
      ptzHlsInstance.destroy();
      ptzHlsInstance = null;
    }

    // Try playing HLS with retry polling until playlist is ready
    let attempts = 0;
    function tryLoad() {
      attempts++;
      if (Hls.isSupported()) {
        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: true,
          liveSyncDurationCount: 1,
          liveMaxLatencyDurationCount: 3,
        });
        ptzHlsInstance = hls;
        hls.loadSource(hlsUrl);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          video.play().catch(() => {});
          if (overlay) overlay.style.display = "none";
        });
        hls.on(Hls.Events.ERROR, (event, data) => {
          if (data.fatal) {
            if (data.type === Hls.ErrorTypes.NETWORK_ERROR && attempts < 15) {
              setTimeout(tryLoad, 1000);
            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
              hls.recoverMediaError();
            } else {
              hls.destroy();
            }
          }
        });
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = hlsUrl;
        video.addEventListener("loadedmetadata", () => {
          video.play().catch(() => {});
          if (overlay) overlay.style.display = "none";
        });
      }
    }

    // Give FFmpeg 500ms to produce first segment
    setTimeout(tryLoad, 500);
  }

  function initPtzControls() {
    const speedSlider = document.getElementById("ptzSpeedSlider");
    const speedVal = document.getElementById("ptzSpeedVal");
    if (speedSlider) {
      speedSlider.addEventListener("input", (e) => {
        currentSpeed = parseFloat(e.target.value);
        if (speedVal) speedVal.textContent = `${Math.round(currentSpeed * 100)}%`;
      });
    }

    document.querySelectorAll(".ptz-mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".ptz-mode-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentMode = btn.getAttribute("data-mode");
      });
    });

    document.querySelectorAll(".ptz-dpad-btn[data-dir]").forEach((btn) => {
      const dir = btn.getAttribute("data-dir");
      bindAction(btn, () => ptzMove(dir), () => ptzStop());
    });

    const stopBtn = document.getElementById("ptzBtnStop");
    if (stopBtn) stopBtn.addEventListener("click", () => ptzStop());

    const btnZoomIn = document.getElementById("ptzBtnZoomIn");
    const btnZoomOut = document.getElementById("ptzBtnZoomOut");
    if (btnZoomIn) bindAction(btnZoomIn, () => ptzZoom("in"), () => ptzStop());
    if (btnZoomOut) bindAction(btnZoomOut, () => ptzZoom("out"), () => ptzStop());

    const btnAddPreset = document.getElementById("ptzBtnAddPreset");
    if (btnAddPreset) {
      btnAddPreset.addEventListener("click", async () => {
        const input = document.getElementById("ptzPresetNameInput");
        const name = input.value.trim();
        if (!name || !activePtzCamera) return;
        try {
          const res = await fetch("/api/ptz/presets/set", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              preset_name: name,
              ip: activePtzCamera.ip,
              port: activePtzCamera.port,
              username: activePtzCamera.username,
              password: activePtzCamera.password,
            }),
          });
          if (res.ok) {
            input.value = "";
            await loadPtzPresets();
          }
        } catch (e) {
          console.error("Set Preset Error:", e);
        }
      });
    }

    // Add New Camera Button
    const btnOpenAddCam = document.getElementById("ptzBtnOpenAddCam");
    if (btnOpenAddCam) {
      btnOpenAddCam.addEventListener("click", () => {
        document.getElementById("ptzAddCamModal").style.display = "flex";
      });
    }

    const btnSaveNewCam = document.getElementById("ptzBtnSaveNewCam");
    if (btnSaveNewCam) {
      btnSaveNewCam.addEventListener("click", async () => {
        const label = document.getElementById("newPtzLabel").value.trim() || `PTZ Camera ${ptzCameras.length + 1}`;
        const rtsp = document.getElementById("newPtzRtsp").value.trim();
        const ip = document.getElementById("newPtzIp").value.trim();
        const port = parseInt(document.getElementById("newPtzPort").value.trim(), 10) || 8888;
        const user = document.getElementById("newPtzUser").value.trim() || "admin";
        const pass = document.getElementById("newPtzPass").value.trim() || "";

        if (!rtsp || !ip) {
          alert("Please enter both RTSP URL and Camera ONVIF IP");
          return;
        }

        const newCam = {
          id: `ptz-${Date.now()}`,
          label: label,
          rtsp: rtsp,
          ip: ip,
          port: port,
          username: user,
          password: pass,
        };

        ptzCameras.push(newCam);

        try {
          await fetch("/api/ptz/cameras", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(ptzCameras),
          });
        } catch (e) {
          console.error("Failed to save PTZ cameras:", e);
        }

        document.getElementById("ptzAddCamModal").style.display = "none";
        renderPtzCameraSelect();
        switchActivePtzCamera(ptzCameras.length - 1);
        const select = document.getElementById("ptzCameraSelect");
        if (select) select.value = ptzCameras.length - 1;
      });
    }
  }

  function bindAction(el, startFn, stopFn) {
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      if (currentMode === "continuous") {
        startFn();
      } else {
        el.classList.add("active");
        startFn();
        setTimeout(() => el.classList.remove("active"), 300);
      }
    });
    el.addEventListener("mouseup", (e) => {
      e.preventDefault();
      if (currentMode === "continuous") stopFn();
    });
    el.addEventListener("mouseleave", (e) => {
      if (currentMode === "continuous" && isMoving) stopFn();
    });
    el.addEventListener("touchstart", (e) => {
      e.preventDefault();
      if (currentMode === "continuous") {
        startFn();
      } else {
        el.classList.add("active");
        startFn();
        setTimeout(() => el.classList.remove("active"), 300);
      }
    });
    el.addEventListener("touchend", (e) => {
      e.preventDefault();
      if (currentMode === "continuous") stopFn();
    });
  }

  async function ptzMove(direction) {
    if (!activePtzCamera) return;
    isMoving = true;
    const payload = {
      direction: direction,
      speed: currentSpeed,
      ip: activePtzCamera.ip,
      port: activePtzCamera.port,
      username: activePtzCamera.username,
      password: activePtzCamera.password,
    };
    if (currentMode === "continuous") {
      try {
        await fetch("/api/ptz/move", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } catch (e) {
        console.error("PTZ Move Error:", e);
      }
    } else {
      payload.duration = currentDuration;
      try {
        await fetch("/api/ptz/step", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } catch (e) {
        console.error("PTZ Step Error:", e);
      }
    }
  }

  async function ptzZoom(direction) {
    if (!activePtzCamera) return;
    isMoving = true;
    const payload = {
      direction: direction,
      speed: currentSpeed,
      ip: activePtzCamera.ip,
      port: activePtzCamera.port,
      username: activePtzCamera.username,
      password: activePtzCamera.password,
    };
    if (currentMode === "continuous") {
      try {
        await fetch("/api/ptz/zoom", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } catch (e) {
        console.error("PTZ Zoom Error:", e);
      }
    } else {
      payload.duration = currentDuration;
      try {
        await fetch("/api/ptz/zoom-step", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      } catch (e) {
        console.error("PTZ Zoom Step Error:", e);
      }
    }
  }

  async function ptzStop() {
    if (!activePtzCamera) return;
    isMoving = false;
    try {
      await fetch("/api/ptz/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ip: activePtzCamera.ip,
          port: activePtzCamera.port,
          username: activePtzCamera.username,
          password: activePtzCamera.password,
        }),
      });
    } catch (e) {
      console.error("PTZ Stop Error:", e);
    }
  }

  async function loadPtzPresets() {
    const listEl = document.getElementById("ptzPresetList");
    if (!listEl || !activePtzCamera) return;
    try {
      const res = await fetch(
        `/api/ptz/presets?ip=${activePtzCamera.ip}&port=${activePtzCamera.port}&username=${encodeURIComponent(
          activePtzCamera.username || ""
        )}&password=${encodeURIComponent(activePtzCamera.password || "")}`
      );
      const data = await res.json();
      const presets = data.presets || [];
      if (presets.length === 0) {
        listEl.innerHTML =
          '<div style="color: var(--muted); font-size: 0.75rem; text-align: center; padding: 10px;">No presets saved yet.</div>';
        return;
      }
      listEl.innerHTML = presets
        .map(
          (p) => `
        <div class="preset-item" style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.2); border:1px solid var(--border); padding:6px 10px; border-radius:6px; font-size:0.78rem;">
          <span>📍 ${escapeHtml(p.name || "Preset " + p.token)}</span>
          <div style="display:flex; gap:6px;">
            <button class="btn-secondary" style="padding:2px 8px; font-size:0.7rem; color:var(--accent);" onclick="window.ptzGotoPreset('${p.token}')">Goto</button>
            <button class="btn-danger" style="padding:2px 6px; font-size:0.7rem;" onclick="window.ptzDeletePreset('${p.token}')">✕</button>
          </div>
        </div>
      `
        )
        .join("");
    } catch (e) {
      listEl.innerHTML = '<div style="color: var(--danger); font-size: 0.75rem;">Failed to load presets.</div>';
    }
  }

  window.ptzGotoPreset = async function (token) {
    if (!activePtzCamera) return;
    try {
      await fetch("/api/ptz/presets/goto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset_token: token,
          speed: 1.0,
          ip: activePtzCamera.ip,
          port: activePtzCamera.port,
          username: activePtzCamera.username,
          password: activePtzCamera.password,
        }),
      });
    } catch (e) {
      console.error("Goto Preset Error:", e);
    }
  };

  window.ptzDeletePreset = async function (token) {
    if (!activePtzCamera) return;
    try {
      await fetch(
        `/api/ptz/presets/${token}?ip=${activePtzCamera.ip}&port=${activePtzCamera.port}&username=${encodeURIComponent(
          activePtzCamera.username || ""
        )}&password=${encodeURIComponent(activePtzCamera.password || "")}`,
        { method: "DELETE" }
      );
      await loadPtzPresets();
    } catch (e) {
      console.error("Delete Preset Error:", e);
    }
  };

  function initPtzTelemetry() {
    async function poll() {
      if (document.getElementById("tab-ptz") && document.getElementById("tab-ptz").style.display !== "none" && activePtzCamera) {
        try {
          const res = await fetch(
            `/api/ptz/status?ip=${activePtzCamera.ip}&port=${activePtzCamera.port}&username=${encodeURIComponent(
              activePtzCamera.username || ""
            )}&password=${encodeURIComponent(activePtzCamera.password || "")}`
          );
          const data = await res.json();
          if (data.status === "online") {
            const panEl = document.getElementById("ptzTelemetryPan");
            const tiltEl = document.getElementById("ptzTelemetryTilt");
            const zoomEl = document.getElementById("ptzTelemetryZoom");
            if (panEl) panEl.textContent = Number(data.pan).toFixed(2);
            if (tiltEl) tiltEl.textContent = Number(data.tilt).toFixed(2);
            if (zoomEl) zoomEl.textContent = Number(data.zoom).toFixed(2);
          }
        } catch (e) {}
      }
    }
    setInterval(poll, 2000);
  }

  window.showPtzApiModal = function () {
    const modal = document.getElementById("ptzApiModal");
    if (modal) modal.style.display = "flex";
  };

  window.closePtzApiModal = function () {
    const modal = document.getElementById("ptzApiModal");
    if (modal) modal.style.display = "none";
  };

  window.closeAddCamModal = function () {
    const modal = document.getElementById("ptzAddCamModal");
    if (modal) modal.style.display = "none";
  };

  function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
})();
