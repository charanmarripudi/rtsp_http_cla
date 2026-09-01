// PTZ Controller Module for RTSP Dashboard
(function() {
  let currentMode = "continuous";
  let currentSpeed = 0.5;
  let currentDuration = 0.35;
  let isMoving = false;
  let currentCameraIp = "192.168.96.30";
  let currentCameraPort = 8888;

  window.initPtzController = function() {
    initPtzControls();
    loadPtzPresets();
    initPtzTelemetry();
  };

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
        if (!name) return;
        try {
          const res = await fetch("/api/ptz/presets/set", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ preset_name: name, ip: currentCameraIp, port: currentCameraPort }),
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
    isMoving = true;
    if (currentMode === "continuous") {
      try {
        await fetch("/api/ptz/move", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction: direction, speed: currentSpeed, ip: currentCameraIp, port: currentCameraPort }),
        });
      } catch (e) { console.error("PTZ Move Error:", e); }
    } else {
      try {
        await fetch("/api/ptz/step", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction: direction, speed: currentSpeed, duration: currentDuration, ip: currentCameraIp, port: currentCameraPort }),
        });
      } catch (e) { console.error("PTZ Step Error:", e); }
    }
  }

  async function ptzZoom(direction) {
    isMoving = true;
    if (currentMode === "continuous") {
      try {
        await fetch("/api/ptz/zoom", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction: direction, speed: currentSpeed, ip: currentCameraIp, port: currentCameraPort }),
        });
      } catch (e) { console.error("PTZ Zoom Error:", e); }
    } else {
      try {
        await fetch("/api/ptz/zoom-step", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ direction: direction, speed: currentSpeed, duration: currentDuration, ip: currentCameraIp, port: currentCameraPort }),
        });
      } catch (e) { console.error("PTZ Zoom Step Error:", e); }
    }
  }

  async function ptzStop() {
    isMoving = false;
    try {
      await fetch("/api/ptz/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: currentCameraIp, port: currentCameraPort })
      });
    } catch (e) { console.error("PTZ Stop Error:", e); }
  }

  async function loadPtzPresets() {
    const listEl = document.getElementById("ptzPresetList");
    if (!listEl) return;
    try {
      const res = await fetch(`/api/ptz/presets?ip=${currentCameraIp}&port=${currentCameraPort}`);
      const data = await res.json();
      const presets = data.presets || [];
      if (presets.length === 0) {
        listEl.innerHTML = '<div style="color: var(--muted); font-size: 0.75rem; text-align: center; padding: 10px;">No presets saved yet.</div>';
        return;
      }
      listEl.innerHTML = presets.map(p => `
        <div class="preset-item" style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.2); border:1px solid var(--border); padding:6px 10px; border-radius:6px; font-size:0.78rem;">
          <span>📍 ${p.name || ('Preset ' + p.token)}</span>
          <div style="display:flex; gap:6px;">
            <button class="btn-secondary" style="padding:2px 8px; font-size:0.7rem; color:var(--accent);" onclick="window.ptzGotoPreset('${p.token}')">Goto</button>
            <button class="btn-danger" style="padding:2px 6px; font-size:0.7rem;" onclick="window.ptzDeletePreset('${p.token}')">✕</button>
          </div>
        </div>
      `).join("");
    } catch (e) {
      listEl.innerHTML = '<div style="color: var(--danger); font-size: 0.75rem;">Failed to load presets.</div>';
    }
  }

  window.ptzGotoPreset = async function(token) {
    try {
      await fetch("/api/ptz/presets/goto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preset_token: token, speed: 1.0, ip: currentCameraIp, port: currentCameraPort }),
      });
    } catch (e) { console.error("Goto Preset Error:", e); }
  };

  window.ptzDeletePreset = async function(token) {
    try {
      await fetch(`/api/ptz/presets/${token}?ip=${currentCameraIp}&port=${currentCameraPort}`, { method: "DELETE" });
      await loadPtzPresets();
    } catch (e) { console.error("Delete Preset Error:", e); }
  };

  function initPtzTelemetry() {
    async function poll() {
      if (document.getElementById("tab-ptz") && document.getElementById("tab-ptz").style.display !== "none") {
        try {
          const res = await fetch(`/api/ptz/status?ip=${currentCameraIp}&port=${currentCameraPort}`);
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

  window.showPtzApiModal = function() {
    const modal = document.getElementById("ptzApiModal");
    if (modal) modal.style.display = "flex";
  };

  window.closePtzApiModal = function() {
    const modal = document.getElementById("ptzApiModal");
    if (modal) modal.style.display = "none";
  };
})();
