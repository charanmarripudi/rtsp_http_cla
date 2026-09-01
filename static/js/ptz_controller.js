// Ultra-Responsive Multi-Camera ONVIF PTZ Controller Module
(function () {
  let isControlsBound = false;
  let currentMode = "continuous";
  let currentSpeed = 0.5;
  let currentDuration = 0.35;
  let isMoving = false;
  let ptzCameras = [];
  let activePtzCamera = null;
  let currentStreamType = "main"; // "main" (HD) or "sub" (SD/Fast)
  let availableStreams = null;
  let ptzHls = null;

  // Called when tab is activated or page loaded
  window.initPtzController = async function () {
    if (!isControlsBound) {
      bindPtzControls();
      isControlsBound = true;
    }
    await loadPtzCameras();
    startTelemetryPolling();
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

    renderPtzCamerasConfigList();
  }

  function renderPtzCamerasConfigList() {
    const listEl = document.getElementById("ptz-cameras-edit-list");
    if (!listEl) return;
    listEl.innerHTML = "";
    if (ptzCameras.length === 0) {
      listEl.innerHTML = '<div style="color:var(--muted); font-size:0.75rem; text-align:center; padding:10px;">No PTZ cameras configured. Click "+ Add Camera" below.</div>';
      return;
    }
    ptzCameras.forEach((cam, idx) => {
      const div = document.createElement("div");
      div.className = "ptz-edit-row";
      div.style.cssText = "display:flex; gap:8px; align-items:center; margin-bottom:8px;";
      div.innerHTML = `
        <div style="flex:2; display:flex; flex-direction:column; gap:2px;">
          <label style="font-size:0.68rem; color:var(--muted);">RTSP Stream URL</label>
          <input type="text" class="ptz-rtsp-input" value="${escapeHtml(cam.rtsp || '')}" placeholder="rtsp://admin:@192.168.96.30:554/ch0_0.264" style="background:var(--bg); border:1px solid var(--border); color:var(--text); padding:6px 10px; border-radius:6px; font-size:0.78rem; font-family:var(--mono);" />
        </div>
        <div style="flex:1; display:flex; flex-direction:column; gap:2px;">
          <label style="font-size:0.68rem; color:var(--muted);">Label</label>
          <input type="text" class="ptz-label-input" value="${escapeHtml(cam.label || '')}" placeholder="PTZ Camera ${idx + 1}" style="background:var(--bg); border:1px solid var(--border); color:var(--text); padding:6px 10px; border-radius:6px; font-size:0.78rem;" />
        </div>
        <button class="btn-danger ptz-remove-row" data-index="${idx}" style="align-self:flex-end; padding:6px 10px; font-size:0.75rem; border-radius:6px; height:32px;">✕</button>
      `;
      listEl.appendChild(div);
    });

    listEl.querySelectorAll(".ptz-remove-row").forEach(btn => {
      btn.onclick = () => {
        const i = parseInt(btn.getAttribute("data-index"), 10);
        ptzCameras.splice(i, 1);
        renderPtzCamerasConfigList();
      };
    });
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

    renderPtzCamerasConfigList();

    if (ptzCameras.length > 0) {
      switchActivePtzCamera(0);
    }

    select.onchange = (e) => {
      const idx = parseInt(e.target.value, 10);
      switchActivePtzCamera(idx);
    };
  }

  async function switchActivePtzCamera(index) {
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

    // Fetch dynamic main/sub streams from ONVIF in background
    fetchDynamicStreams();

    // Start video stream for this PTZ camera
    playPtzStream(index);

    // Load presets for this specific PTZ camera
    loadPtzPresets();
  }

  async function fetchDynamicStreams() {
    if (!activePtzCamera) return;
    try {
      const res = await fetch(
        `/api/ptz/streams?ip=${activePtzCamera.ip}&port=${activePtzCamera.port || 8888}&username=${encodeURIComponent(
          activePtzCamera.username || "admin"
        )}&password=${encodeURIComponent(activePtzCamera.password || "")}`
      );
      const data = await res.json();
      if (data.status === "success" && data.streams) {
        availableStreams = data.streams;
      }
    } catch (e) {
      console.warn("Could not query dynamic streams via ONVIF:", e);
    }
  }

  window.reloadPtzStream = function () {
    const select = document.getElementById("ptzCameraSelect");
    const idx = select ? parseInt(select.value, 10) : 0;
    playPtzStream(idx);
    showStatusToast("Reloading Live Feed...");
  };

  function playPtzStream(camIndex) {
    const video = document.getElementById("ptzLiveVideo");
    if (!video || !activePtzCamera) return;

    const cid = `ptz${camIndex}`;
    
    // Use exact RTSP URL entered for this camera
    let rtspUrl = activePtzCamera.rtsp;

    const hlsUrl = `/hls/stream${cid}_raw/playlist.m3u8`;

    // Ensure backend raw stream is running for this RTSP URL
    fetch("/api/ptz/cameras/start-stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cid: cid, rtsp: rtspUrl }),
    }).catch(() => {});

    // Ensure video element properties
    delete video.dataset.currentUrl;
    video.muted = true;
    video.defaultMuted = true;
    video.playsInline = true;
    video.autoplay = true;
    video.setAttribute("muted", "");
    video.setAttribute("playsinline", "");
    video.setAttribute("autoplay", "");

    // Destroy existing HLS instance on this video
    if (ptzHls) {
      try {
        ptzHls.detachMedia();
        ptzHls.destroy();
      } catch (_) {}
      ptzHls = null;
    }

    if (typeof playHLS === "function") {
      playHLS(video, hlsUrl, "ptz_" + camIndex);
      setTimeout(() => {
        if (video && video.paused) {
          video.play().catch(() => {});
        }
      }, 500);
      return;
    }

    const streamUrl = hlsUrl + "?t=" + Date.now();

    if (typeof Hls !== "undefined" && Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        startPosition: -1,
        liveSyncDurationCount: 2.0,
        liveMaxLatencyDurationCount: 6,
        liveDurationInfinity: true,
        manifestLoadingTimeOut: 15000,
        manifestLoadingMaxRetry: 10,
        manifestLoadingRetryDelay: 600,
      });
      ptzHls = hls;

      hls.attachMedia(video);
      hls.on(Hls.Events.MEDIA_ATTACHED, () => {
        hls.loadSource(streamUrl);
      });

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {});
      });

      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) {
          if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
            setTimeout(() => {
              if (ptzHls === hls) hls.loadSource(streamUrl);
            }, 1000);
          } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
            hls.recoverMediaError();
          } else {
            hls.destroy();
          }
        }
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = streamUrl;
      video.play().catch(() => {});
    }
  }

  function showStatusToast(msg, isError = false) {
    let toast = document.getElementById("ptzStatusToast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "ptzStatusToast";
      toast.style.cssText = "position:fixed; bottom:20px; right:20px; background:rgba(18,24,38,0.95); border:1px solid var(--accent); color:var(--text); padding:8px 16px; border-radius:8px; font-size:0.78rem; z-index:99999; box-shadow:0 4px 16px rgba(0,0,0,0.5); pointer-events:none; transition:opacity 0.3s ease;";
      document.body.appendChild(toast);
    }
    toast.style.borderColor = isError ? "var(--danger)" : "var(--accent)";
    toast.innerHTML = (isError ? "⚠️ " : "⚡ ") + msg;
    toast.style.opacity = "1";
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      toast.style.opacity = "0";
    }, 2000);
  }

  function bindPtzControls() {
    const speedSlider = document.getElementById("ptzSpeedSlider");
    const speedVal = document.getElementById("ptzSpeedVal");
    if (speedSlider) {
      speedSlider.addEventListener("input", (e) => {
        currentSpeed = parseFloat(e.target.value);
        if (speedVal) speedVal.textContent = `${Math.round(currentSpeed * 100)}%`;
      });
    }

    // Direction Mode Switch (Hold vs Step)
    document.querySelectorAll(".ptz-mode-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".ptz-mode-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentMode = btn.getAttribute("data-mode");
        showStatusToast(`Control mode: ${currentMode === "continuous" ? "Hold to Move" : "Single Click (Nudge)"}`);
      });
    });

    // Stream Quality Switcher (Main 1080p vs Sub SD)
    document.querySelectorAll(".ptz-stream-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        document.querySelectorAll(".ptz-stream-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        currentStreamType = btn.getAttribute("data-stream");
        showStatusToast(`Switching to ${currentStreamType === "main" ? "HD 1080p Main Stream" : "SD Fast Sub Stream"}...`);
        const select = document.getElementById("ptzCameraSelect");
        const idx = select ? parseInt(select.value, 10) : 0;
        playPtzStream(idx);
      });
    });

    // D-Pad Direction Buttons
    document.querySelectorAll(".ptz-dpad-btn[data-dir]").forEach((btn) => {
      const dir = btn.getAttribute("data-dir");
      bindAction(btn, () => ptzMove(dir), () => ptzStop());
    });

    const stopBtn = document.getElementById("ptzBtnStop");
    if (stopBtn) stopBtn.addEventListener("click", () => {
      ptzStop();
      showStatusToast("Camera Stopped");
    });

    const btnZoomIn = document.getElementById("ptzBtnZoomIn");
    const btnZoomOut = document.getElementById("ptzBtnZoomOut");
    if (btnZoomIn) bindAction(btnZoomIn, () => ptzZoom("in"), () => ptzStop());
    if (btnZoomOut) bindAction(btnZoomOut, () => ptzZoom("out"), () => ptzStop());

    const btnAddPreset = document.getElementById("ptzBtnAddPreset");
    if (btnAddPreset) {
      btnAddPreset.addEventListener("click", async () => {
        const input = document.getElementById("ptzPresetNameInput");
        const name = input.value.trim();
        if (!name || !activePtzCamera) {
          showStatusToast("Please enter a preset name", true);
          return;
        }
        try {
          showStatusToast(`Saving preset "${name}"...`);
          const res = await fetch("/api/ptz/presets/set", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              preset_name: name,
              ip: activePtzCamera.ip,
              port: activePtzCamera.port,
              username: activePtzCamera.username || "admin",
              password: activePtzCamera.password || "",
            }),
          });
          if (res.ok) {
            input.value = "";
            showStatusToast(`Preset "${name}" Saved!`);
            await loadPtzPresets();
          } else {
            showStatusToast("Failed to save preset", true);
          }
        } catch (e) {
          showStatusToast("Preset error: " + e.message, true);
        }
      });
    }

    // Add New Camera Modal (Only RTSP URL needed!)
    const btnOpenAddCam = document.getElementById("ptzBtnOpenAddCam");
    if (btnOpenAddCam) {
      btnOpenAddCam.addEventListener("click", () => {
        document.getElementById("ptzAddCamModal").style.display = "flex";
      });
    }

    const btnSaveNewCam = document.getElementById("ptzBtnSaveNewCam");
    if (btnSaveNewCam) {
      btnSaveNewCam.addEventListener("click", async () => {
        const rtsp = document.getElementById("newPtzRtsp").value.trim();
        const label = document.getElementById("newPtzLabel").value.trim();

        if (!rtsp) {
          alert("Please enter the RTSP Stream URL");
          return;
        }

        try {
          showStatusToast("Adding camera & connecting to ONVIF...");
          const res = await fetch("/api/ptz/cameras/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rtsp: rtsp, label: label }),
          });
          const data = await res.json();
          if (data.status === "success" && data.cameras) {
            ptzCameras = data.cameras;
            document.getElementById("newPtzRtsp").value = "";
            document.getElementById("newPtzLabel").value = "";
            document.getElementById("ptzAddCamModal").style.display = "none";
            renderPtzCameraSelect();
            switchActivePtzCamera(ptzCameras.length - 1);
            const select = document.getElementById("ptzCameraSelect");
            if (select) select.value = ptzCameras.length - 1;
            showStatusToast(`Camera added successfully!`);
          }
        } catch (e) {
          showStatusToast("Failed to add camera: " + e.message, true);
        }
      });
    }

    const btnSaveCams = document.getElementById("ptzBtnSaveCameras");
    if (btnSaveCams) {
      btnSaveCams.addEventListener("click", async () => {
        try {
          showStatusToast("Saving PTZ Cameras...");
          const res = await fetch("/api/ptz/cameras", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(ptzCameras),
          });
          const data = await res.json();
          if (data.status === "success") {
            showStatusToast("PTZ Cameras Saved Successfully!");
          }
        } catch (e) {
          showStatusToast("Error saving PTZ cameras: " + e.message, true);
        }
      });
    }

    // Inline Manage PTZ Cameras Handlers (Location-Style)
    const btnAddRow = document.getElementById("ptz-btn-add-row");
    if (btnAddRow) {
      btnAddRow.onclick = () => {
        ptzCameras.push({
          rtsp: "",
          label: `PTZ Camera ${ptzCameras.length + 1}`
        });
        renderPtzCamerasConfigList();
      };
    }

    const btnSaveList = document.getElementById("ptz-btn-save-list");
    if (btnSaveList) {
      btnSaveList.onclick = async () => {
        const rows = document.querySelectorAll("#ptz-cameras-edit-list .ptz-edit-row");
        const payload = [];
        rows.forEach((row, idx) => {
          const rtsp = row.querySelector(".ptz-rtsp-input").value.trim();
          const label = row.querySelector(".ptz-label-input").value.trim();
          if (rtsp) {
            payload.push({
              rtsp: rtsp,
              label: label || `PTZ Camera ${idx + 1}`
            });
          }
        });

        if (payload.length === 0) {
          showStatusToast("Please enter at least one RTSP Stream URL", true);
          return;
        }

        try {
          showStatusToast("Saving PTZ Cameras configuration...");
          btnSaveList.disabled = true;
          btnSaveList.textContent = "Saving...";
          const res = await fetch("/api/ptz/cameras", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          });
          const data = await res.json();
          btnSaveList.disabled = false;
          btnSaveList.textContent = "Save Cameras";
          if (data.status === "success" && data.cameras) {
            ptzCameras = data.cameras;
            renderPtzCameraSelect();
            switchActivePtzCamera(0);
            showStatusToast("PTZ Cameras Saved & Connected Successfully!");
          } else {
            showStatusToast("Failed to save PTZ cameras", true);
          }
        } catch (e) {
          btnSaveList.disabled = false;
          btnSaveList.textContent = "Save Cameras";
          showStatusToast("Save error: " + e.message, true);
        }
      };
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
    showStatusToast(`Moving ${direction.toUpperCase()} (${Math.round(currentSpeed * 100)}%)...`);
    const payload = {
      direction: direction,
      speed: currentSpeed,
      ip: activePtzCamera.ip,
      port: activePtzCamera.port,
      username: activePtzCamera.username || "admin",
      password: activePtzCamera.password || "",
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
    showStatusToast(`Zoom ${direction.toUpperCase()}...`);
    const payload = {
      direction: direction,
      speed: currentSpeed,
      ip: activePtzCamera.ip,
      port: activePtzCamera.port,
      username: activePtzCamera.username || "admin",
      password: activePtzCamera.password || "",
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
          username: activePtzCamera.username || "admin",
          password: activePtzCamera.password || "",
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
          activePtzCamera.username || "admin"
        )}&password=${encodeURIComponent(activePtzCamera.password || "")}`
      );
      const data = await res.json();
      let presets = data.presets || [];
      
      const customPresets = presets.filter(p => p.name && !p.name.startsWith("PRESET_"));
      const displayPresets = customPresets.length > 0 ? customPresets : presets.slice(0, 6);

      if (displayPresets.length === 0) {
        listEl.innerHTML =
          '<div style="color: var(--muted); font-size: 0.75rem; text-align: center; padding: 8px;">No custom presets saved. Click "Save" to add one.</div>';
        return;
      }
      listEl.innerHTML = displayPresets
        .map(
          (p) => `
        <div class="preset-item" style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.3); border:1px solid var(--border); padding:5px 10px; border-radius:6px; font-size:0.75rem;">
          <span style="font-weight:500;">📍 ${escapeHtml(p.name || "Preset " + p.token)}</span>
          <div style="display:flex; gap:6px;">
            <button class="btn-secondary" style="padding:2px 8px; font-size:0.68rem; color:var(--accent);" onclick="window.ptzGotoPreset('${p.token}')">Goto</button>
            <button class="btn-danger" style="padding:2px 6px; font-size:0.68rem;" onclick="window.ptzDeletePreset('${p.token}')">✕</button>
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
      showStatusToast(`Moving to Preset ${token}...`);
      await fetch("/api/ptz/presets/goto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preset_token: token,
          speed: 1.0,
          ip: activePtzCamera.ip,
          port: activePtzCamera.port,
          username: activePtzCamera.username || "admin",
          password: activePtzCamera.password || "",
        }),
      });
    } catch (e) {
      console.error("Goto Preset Error:", e);
    }
  };

  window.ptzDeletePreset = async function (token) {
    if (!activePtzCamera) return;
    try {
      showStatusToast(`Deleting preset ${token}...`);
      await fetch(
        `/api/ptz/presets/${token}?ip=${activePtzCamera.ip}&port=${activePtzCamera.port}&username=${encodeURIComponent(
          activePtzCamera.username || "admin"
        )}&password=${encodeURIComponent(activePtzCamera.password || "")}`,
        { method: "DELETE" }
      );
      await loadPtzPresets();
      showStatusToast("Preset deleted");
    } catch (e) {
      console.error("Delete Preset Error:", e);
    }
  };

  let telemetryTimer = null;
  function startTelemetryPolling() {
    if (telemetryTimer) return;
    async function poll() {
      const tabPtz = document.getElementById("tab-ptz");
      if (tabPtz && tabPtz.style.display !== "none" && activePtzCamera) {
        try {
          const res = await fetch(
            `/api/ptz/status?ip=${activePtzCamera.ip}&port=${activePtzCamera.port}&username=${encodeURIComponent(
              activePtzCamera.username || "admin"
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
    telemetryTimer = setInterval(poll, 2000);
    poll();
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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      if (!isControlsBound) bindPtzControls();
    });
  } else {
    bindPtzControls();
  }
})();
