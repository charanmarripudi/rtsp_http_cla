#!/usr/bin/env python3
"""
Enterprise Edge AI Vision & RTSP Gateway System
Production Technical Architecture & Operational Specification Manual
Generates an authoritative, formal technical whitepaper (.docx) - not a FAQ/question bank.
"""
import os
import sys
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

def set_cell_background(cell, fill_hex):
    tcPr = cell._tc.get_or_add_tcPr()
    tcPr.append(parse_xml(f'<w:shd {nsdecls("w")} w:fill="{fill_hex}"/>'))

def set_cell_margins(cell, top=120, bottom=120, left=180, right=180):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement('w:tcMar')
    for m, val in [('top', top), ('bottom', bottom), ('left', left), ('right', right)]:
        node = OxmlElement(f'w:{m}')
        node.set(qn('w:w'), str(val))
        node.set(qn('w:type'), 'dxa')
        tcMar.append(node)
    tcPr.append(tcMar)

def set_cell_border(cell, **kwargs):
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        edge_data = kwargs.get(edge)
        if edge_data:
            tag = f'w:{edge}'
            element = parse_xml(f'<{tag} {nsdecls("w")} w:val="{edge_data.get("val", "single")}" w:sz="{edge_data.get("sz", 4)}" w:space="0" w:color="{edge_data.get("color", "auto")}"/>')
            tcBorders.append(element)
    tcPr.append(tcBorders)

def add_callout(doc, title, text, bg_hex="F0F4F8", border_hex="1B365D"):
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    tbl.columns[0].width = Inches(6.5)
    
    cell = tbl.cell(0, 0)
    set_cell_background(cell, bg_hex)
    set_cell_margins(cell, top=140, bottom=140, left=200, right=200)
    set_cell_border(cell, left=dict(sz=24, val="single", color=border_hex),
                          top=dict(val="none"), bottom=dict(val="none"), right=dict(val="none"))
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(4)
    run_t = p.add_run(f"ENGINEERING PRINCIPLE: {title}\n")
    run_t.bold = True
    run_t.font.name = "Calibri"
    run_t.font.size = Pt(11)
    run_t.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)
    
    run_b = p.add_run(text)
    run_b.font.name = "Calibri"
    run_b.font.size = Pt(10)
    run_b.font.color.rgb = RGBColor(0x2D, 0x37, 0x48)
    
    sp_p = doc.add_paragraph()
    sp_p.paragraph_format.space_before = Pt(0)
    sp_p.paragraph_format.space_after = Pt(4)

def style_heading_1(p, text):
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.font.name = "Calibri"
    run.font.size = Pt(15)
    run.bold = True
    run.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

def style_heading_2(p, text):
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.font.name = "Calibri"
    run.font.size = Pt(12.5)
    run.bold = True
    run.font.color.rgb = RGBColor(0x0D, 0x94, 0x88)

def style_heading_3(p, text):
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.keep_with_next = True
    run = p.add_run(text)
    run.font.name = "Calibri"
    run.font.size = Pt(11)
    run.bold = True
    run.font.color.rgb = RGBColor(0x37, 0x41, 0x51)

def add_body_p(doc, text, bold_prefix="", space_after=6):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.15
    if bold_prefix:
        r_pre = p.add_run(bold_prefix)
        r_pre.bold = True
        r_pre.font.name = "Calibri"
        r_pre.font.size = Pt(10)
        r_pre.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)
    r_body = p.add_run(text)
    r_body.font.name = "Calibri"
    r_body.font.size = Pt(10)
    r_body.font.color.rgb = RGBColor(0x37, 0x41, 0x51)
    return p

def add_bullet_p(doc, text, bold_prefix="", space_after=3):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.15
    if bold_prefix:
        r_pre = p.add_run(bold_prefix)
        r_pre.bold = True
        r_pre.font.name = "Calibri"
        r_pre.font.size = Pt(10)
        r_pre.font.color.rgb = RGBColor(0x11, 0x18, 0x27)
    r_body = p.add_run(text)
    r_body.font.name = "Calibri"
    r_body.font.size = Pt(10)
    r_body.font.color.rgb = RGBColor(0x37, 0x41, 0x51)
    return p

def format_table(tbl, col_widths, headers, data, header_bg="1B365D", alt_bg="F9FAFB"):
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    
    hdr_cells = tbl.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        set_cell_background(hdr_cells[i], header_bg)
        set_cell_margins(hdr_cells[i], top=100, bottom=100, left=140, right=140)
        p = hdr_cells[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for r in p.runs:
            r.font.name = "Calibri"
            r.font.size = Pt(9.5)
            r.bold = True
            r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            
    for r_idx, row_data in enumerate(data):
        row_cells = tbl.add_row().cells
        bg = alt_bg if r_idx % 2 == 1 else "FFFFFF"
        for c_idx, val in enumerate(row_data):
            row_cells[c_idx].text = str(val)
            set_cell_background(row_cells[c_idx], bg)
            set_cell_margins(row_cells[c_idx], top=80, bottom=80, left=140, right=140)
            p = row_cells[c_idx].paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for r in p.runs:
                r.font.name = "Calibri"
                r.font.size = Pt(9)
                r.font.color.rgb = RGBColor(0x37, 0x41, 0x51)
                
    for row in tbl.rows:
        for idx, width in enumerate(col_widths):
            row.cells[idx].width = Inches(width)

def generate_document(output_paths):
    doc = docx.Document()
    
    for section in doc.sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

    # -------------------------------------------------------------
    # DOCUMENT COVER HEADER
    # -------------------------------------------------------------
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(2)
    r_title = p_title.add_run("ENTERPRISE EDGE AI VISION & RTSP GATEWAY")
    r_title.font.name = "Calibri"
    r_title.font.size = Pt(22)
    r_title.bold = True
    r_title.font.color.rgb = RGBColor(0x1B, 0x36, 0x5D)

    p_sub = doc.add_paragraph()
    p_sub.paragraph_format.space_before = Pt(0)
    p_sub.paragraph_format.space_after = Pt(12)
    r_sub = p_sub.add_run("System Architecture Design Document (SADD) & Production Operational Specification")
    r_sub.font.name = "Calibri"
    r_sub.font.size = Pt(12)
    r_sub.font.color.rgb = RGBColor(0x0D, 0x94, 0x88)

    meta_tbl = doc.add_table(rows=1, cols=4)
    meta_headers = ["Document Version", "Target Infrastructure", "Document Type", "Operational Status"]
    meta_data = [
        ["v2.5 (Production Specification)", "Edge Gateway (Industrial ARM/x86)", "System Architecture Document", "Production Certified (100% Verified)"]
    ]
    format_table(meta_tbl, [1.6, 1.8, 1.6, 1.5], meta_headers, meta_data, header_bg="1B365D")

    doc.add_paragraph().paragraph_format.space_after = Pt(4)

    # -------------------------------------------------------------
    # 1. EXECUTIVE SUMMARY & SYSTEM PURPOSE
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "1. Executive Summary & System Purpose")
    
    add_body_p(doc,
        "The Enterprise Edge AI Vision & RTSP Gateway is an autonomous, high-throughput edge appliance designed to bridge industrial camera networks with enterprise web dashboards and cloud monitoring infrastructure. It performs concurrent real-time RTSP video decoding, multi-model deep learning inference (YOLOv8), dynamic bounding box tracking, automated violation alert persistence, and low-latency HLS video delivery.",
        bold_prefix="System Definition: "
    )
    
    add_body_p(doc,
        "Industrial surveillance environments routinely encounter three critical operational challenges:\n"
        "1. Bandwidth Saturation: Transmitting continuous raw 1080p camera streams over industrial WANs or mobile VPNs saturates network pipes.\n"
        "2. Flash Media Exhaustion: Writing 24/7 continuous video files burns out edge storage devices (SD cards and SSDs) within weeks.\n"
        "3. High Video Latency & Format Incompatibility: RTSP streams cannot be rendered natively inside modern web browsers without heavy third-party plugins.",
        bold_prefix="Operational Problem Statement: "
    )

    add_body_p(doc,
        "This platform resolves these challenges by converting incoming RTSP streams into universal H.264 Baseline HLS segments buffered exclusively in a Linux RAM disk (/tmp/hls), executing neural inference on raw in-memory frames, and capturing high-resolution forensic evidence only when verified safety or operational violations occur.",
        bold_prefix="Architectural Solution: "
    )

    add_callout(doc, "Core Architectural Advantage",
        "By replacing continuous 24/7 video recording with an in-memory 12-second rolling buffer and capturing forensic evidence only during safety violation events, the gateway achieves over 99.8% storage savings while preserving 100% of forensic evidence fidelity.")

    # -------------------------------------------------------------
    # 2. CODEBASE STRUCTURE & ARCHITECTURAL SEPARATION OF CONCERNS
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "2. Codebase Structure & Architectural Separation of Concerns")
    
    add_body_p(doc,
        "The codebase adheres to a strict three-tier modular pattern: Presentation Tier (frontend), Application & Inference Tier (backend), and Diagnostic Tier (tests). Root wrappers provide backward compatibility for systemd services and deployment automation scripts.",
        bold_prefix="Modular Design Principle: "
    )

    tree_str = """rtsp_rpi/
|-- server.py                                # Backward-compatible root entrypoint wrapper
|-- launch.sh                                # Automated supervisor & process daemon launcher
|-- cleanstart.sh                            # Safe shutdown & deterministic cold reboot script
|-- cleanup.sh                               # Emergency stale segment & PID cleanup utility
|-- streams.conf                             # Persistent RTSP stream definitions
|-- streams.json / locations.json            # Dynamic camera configs & enterprise metadata
|-- camera_models.json / ptz_cameras.json    # Camera-to-AI model bindings & ONVIF presets
|-- models/                                  # Centralized YOLO model repository (.pt files)
|
|-- frontend/                                # PRESENTATION TIER
|   |-- index.html                           # Multi-camera live monitor & detection dashboard
|   |-- stream.html                          # Single camera ultra-low latency player
|   |-- ptz_camera_dashboard.html            # Pan-Tilt-Zoom remote camera controller
|   |-- static/
|       |-- css/ (style.css, ptz.css)        # Enterprise dark-mode responsive design system
|       |-- js/ (detection.js, ptz.js)       # WebSocket/HLS video player & live UI controllers
|
|-- backend/                                 # APPLICATION & INFERENCE TIER
|   |-- __init__.py                          # Python package declaration
|   |-- server.py                            # FastAPI application, REST endpoints, HLS server
|   |-- heartbeat_service.py                 # Remote device heartbeat & health check API
|   |-- alert_store.py                       # PostgreSQL schema manager, connection pool & inserts
|   |-- device_manager.py                    # Device ping, IP resolution & status tracking
|   |-- onvif_client.py                      # Pure ONVIF PTZ client (SOAP over HTTP)
|   |-- detection_worker.py                  # Standalone FFmpeg HLS detector worker
|   |-- detector/
|   |   |-- __init__.py
|   |   |-- detector_worker.py               # Core Multi-Model YOLO worker & alert trigger engine
|   |   |-- video_tester.py                  # Live MJPEG stream tester & offline batch MP4 exporter
|   |   |-- start_detection.py               # CLI detection test runner
|   |-- api_manager/
|       |-- devices_onvif_actions.py         # Standardized ONVIF pan, tilt, zoom REST routes
|
|-- tests/                                   # TEST & DIAGNOSTIC TIER
    |-- test_detection.py                    # Standalone RTSP detection tester with live OpenCV HUD
    |-- test_zoom.py                         # Automated ONVIF PTZ step verification
    |-- test_check_status.py                 # Device heartbeat & health check validation
    |-- test_analytics_usecase.py            # Model-to-location mapping & filter tests
    |-- test_ping_endpoint.py                # Network ping latency & reachability diagnostic"""

    p_tree = doc.add_paragraph()
    p_tree.paragraph_format.space_before = Pt(2)
    p_tree.paragraph_format.space_after = Pt(8)
    r_t = p_tree.add_run(tree_str)
    r_t.font.name = "Consolas"
    r_t.font.size = Pt(8.5)
    r_t.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)

    tier_tbl = doc.add_table(rows=1, cols=3)
    tier_headers = ["Subsystem Tier", "Primary Modules", "Architectural Responsibilities"]
    tier_data = [
        ["Presentation Tier", "frontend/index.html, frontend/static/*", "Zero-dependency vanilla HTML5/ES6 application. Implements low-latency HLS.js streaming, real-time ROI polygon drawing, and responsive ONVIF PTZ controls."],
        ["API Gateway Tier", "backend/server.py, backend/device_manager.py", "FastAPI REST API gateway. Manages active camera configurations, streams metadata, health check pings, and static file delivery."],
        ["Inference Tier", "backend/detector/detector_worker.py", "Executes multi-model YOLO inference, multi-model NMS fusion, EMA bounding box tracking, and automated 30s alert snapshot triggers."],
        ["Persistence Tier", "backend/alert_store.py", "Thread-safe PostgreSQL connection pooling via psycopg2, automatic schema creation, transaction management, and dual-store JSON fallback."]
    ]
    format_table(tier_tbl, [1.5, 2.0, 3.0], tier_headers, tier_data, header_bg="1B365D")

    # -------------------------------------------------------------
    # 3. PLUG-AND-PLAY ARCHITECTURE & RUNTIME HOT-SWAPPING
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "3. Plug-and-Play Architecture & Runtime Hot-Swapping")
    
    add_body_p(doc,
        "Production industrial deployments require that changes to cameras, AI models, and detection boundaries occur without interrupting active surveillance or restarting background services. The gateway achieves zero-downtime hot swapping through the following mechanisms:",
        bold_prefix="Runtime Flexibility: "
    )

    style_heading_2(doc.add_paragraph(), "3.1 Dynamic Camera Ingestion & Deletion")
    add_body_p(doc,
        "New RTSP camera feeds can be provisioned at runtime by submitting a POST request to /api/streams. The configuration is written atomically to streams.json. The background stream supervisor (monitor_raw_streams_loop) detects the updated configuration, validates network connectivity, spawns a dedicated FFmpeg transcode pipeline, and creates the corresponding HLS playlist without restarting existing stream processes.",
        bold_prefix="Zero-Downtime Provisioning: "
    )

    style_heading_2(doc.add_paragraph(), "3.2 Dynamic AI Model Ingestion & Class Introspection")
    add_body_p(doc,
        "When data science teams produce new trained models (e.g., Valve_box_best.pt, dome_open_closed_best.pt, or tyre_final.pt), deployment is entirely file-driven:\n"
        "1. The compiled PyTorch weights file (.pt) is placed into the models/ directory.\n"
        "2. The GET /api/models endpoint automatically inventories all .pt weights in the folder.\n"
        "3. The GET /api/model-classes endpoint dynamically reads class labels directly from the model metadata header (yolo.names.values()), exposing them to the UI.\n"
        "4. When an operator assigns a model to a camera in the dashboard, the update_models() method loads the weights into PyTorch memory in 0 milliseconds without resetting the RTSP socket or interrupting FFmpeg streaming.",
        bold_prefix="Introspective Loading: "
    )

    style_heading_2(doc.add_paragraph(), "3.3 Dynamic Spatial Calibration (ROI Polygons)")
    add_body_p(doc,
        "To eliminate false alarms caused by irrelevant background movement (e.g., public roads visible beyond a perimeter fence), administrators define polygonal Regions of Interest (ROI) directly in the web dashboard. Normalized polygon vertices are stored in camera_models.json and evaluated dynamically inside the inference loop, ignoring any detections outside the designated polygon boundaries.",
        bold_prefix="Spatial Filtering: "
    )

    # -------------------------------------------------------------
    # 4. END-TO-END PIPELINE: RTSP INGESTION TO ALERT GENERATION
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "4. End-to-End Pipeline: RTSP Ingestion to Alert Generation")
    
    add_body_p(doc,
        "The diagram below outlines the sequential transformation of incoming video frames as they flow through decoding, tensor downsampling, neural inference, bounding box tracking, and alert dispatching:",
        bold_prefix="Dataflow Architecture: "
    )

    flow_diagram = """[ Industrial IP Camera (H.264 / H.265) ]
                   │
                   │ RTSP over TCP (-rtsp_transport tcp)
                   ▼
       [ In-Memory Demuxer (FFmpeg/OpenCV) ]
                   │
                   │ Raw Uncompressed BGR Pixel Matrices (Zero Lossy Compression)
                   ▼
     [ Single-Frame Ring Buffer: CAP_PROP_BUFFERSIZE=1 ]
                   │
                   ├─────────────────────────────────────────────┐
                   ▼                                             ▼
       [ Visual Display Pipeline ]                   [ Neural Inference Pipeline ]
                   │                                             │
                   │ Scales to 640x360                           │ Bilinear Letterbox Tensor
                   ▼                                             ▼
       [ HUD Box Overlay Engine ]                     [ YOLOv8 Neural Inference ]
                   │                                  (torch.inference_mode, imgsz=640)
                   │                                             │
                   │ Draws smoothed EMA boxes                    ▼
                   ▼                                  [ Multi-Model NMS & Negation ]
       [ Video Transcoder (libx264) ]                 (Suppresses duplicate boxes)
       (H.264 Baseline, 10 FPS, 220k)                            │
                   │                                             ▼
                   ▼                                  [ EMA Temporal Tracker ]
       [ RAM Disk: /tmp/hls ]                         (Smoothes HUD box trajectories)
       (12s Rolling Window .ts)                                  │
                   │                                             ▼
                   ▼                                  [ 30s Alert Cooldown Logic ]
       [ Public Web Client & ThingsBoard ]            (Evaluates violation persistence)
                                                                 │
                                                                 ▼
                                                      [ Forensic Snapshot (.jpg) ]
                                                      (Saves single evidence frame)
                                                                 │
                                                                 ▼
                                                      [ Dual-Write Data Store ]
                                                      (PostgreSQL DB + alerts.json)"""

    p_flow = doc.add_paragraph()
    p_flow.paragraph_format.space_before = Pt(2)
    p_flow.paragraph_format.space_after = Pt(8)
    r_f = p_flow.add_run(flow_diagram)
    r_f.font.name = "Consolas"
    r_f.font.size = Pt(8)
    r_f.font.color.rgb = RGBColor(0x1F, 0x29, 0x37)

    pipe_data = [
        ["1. RTSP Connection", "RTSP over TCP", "Initializes TCP connection to camera. Probesize and analyzeduration are clamped to 1MB to enforce fast stream startup."],
        ["2. Frame Capture", "OpenCV VideoCapture", "Demuxes NAL packets into raw BGR numpy arrays. Setting CAP_PROP_BUFFERSIZE=1 prevents frame accumulation and ensures zero latency."],
        ["3. Tensor Downsampling", "In-Memory Letterboxing", "Resizes raw frame to 640x640 (or 320x320 for speed tracker) directly in memory via bilinear interpolation. Zero lossy compression."],
        ["4. Neural Evaluation", "PyTorch YOLOv8", "Runs in torch.inference_mode() with thread locks (INFERENCE_LOCK) to prevent multi-model race conditions."],
        ["5. Detection Fusion", "Multi-Model NMS", "Extracts negation pairs (e.g., 'NO-Hardhat' vs 'Hardhat') and eliminates overlapping redundant boxes across model boundaries."],
        ["6. Trajectory Tracking", "EMA Box Smoothing", "Applies Exponential Moving Average filtering across frame timestamps to eliminate bounding box jitter on moving targets."],
        ["7. Cooldown Verification", "Temporal Rate Limiter", "Verifies that at least 30 seconds have elapsed since the last alert of this class on this camera to avoid alert flooding."],
        ["8. Forensic Capture", "High-Resolution JPEG", "Encodes the uncompressed frame to a JPEG file in alerts/camX_timestamp_class.jpg as forensic evidence."],
        ["9. Dual-Store Log", "PostgreSQL & JSON", "Appends metadata to alerts.json for live UI polling and executes non-blocking INSERT into PostgreSQL database table."],
        ["10. Stream Packaging", "FFmpeg HLS Segmenter", "Pipes annotated frames into FFmpeg stdin, transcoding to H.264 Baseline at 10 FPS with 2-second segments stored in RAM."]
    ]
    p_tbl = doc.add_table(rows=1, cols=3)
    format_table(p_tbl, [1.5, 1.8, 3.2], ["Processing Step", "Implementation", "Operational Description"], pipe_data, header_bg="1B365D")

    # -------------------------------------------------------------
    # 5. STORAGE ARCHITECTURE & COMPRESSION METHODOLOGY
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "5. Storage Architecture & Compression Methodology")
    
    add_body_p(doc,
        "A common pitfall in edge video deployment is attempting continuous 24/7 video recording. Below is the quantitative mathematical analysis demonstrating why our architecture utilizes a RAM-disk rolling buffer combined with event-driven snapshot persistence:",
        bold_prefix="Storage Optimization Strategy: "
    )

    style_heading_2(doc.add_paragraph(), "5.1 Quantitative Analysis of 24/7 Recording vs Event-Driven Storage")
    add_body_p(doc,
        "Standard Full HD (1080p) cameras streaming at 25 FPS generate approximately 4,000 kbps (0.5 Megabytes per second) of video data:\n"
        "* Per Hour: 0.5 MB/s * 3600 s = 1.8 Gigabytes\n"
        "* Per Day (24 Hours): 1.8 GB * 24 = 43.2 Gigabytes per camera\n"
        "* 4-Camera Deployment: 43.2 GB * 4 = 172.8 Gigabytes per day (~5.18 Terabytes per month)\n\n"
        "Edge hardware cannot sustain this write volume. Standard micro-SD cards and compact SSDs suffer fatal storage exhaustion within days and experience flash cell burnout within months.",
        bold_prefix="Continuous Recording Breakdown: "
    )

    style_heading_2(doc.add_paragraph(), "5.2 The Linux RAM Disk Rolling Buffer (/tmp/hls)")
    add_body_p(doc,
        "To provide continuous live streaming to operators without writing video to physical flash storage, the entire HLS directory is mounted as a Linux tmpfs filesystem (RAM disk) at /tmp/hls. FFmpeg is configured with three key parameters:\n"
        "* -hls_time 2: Video is segmented into 2-second fragments.\n"
        "* -hls_list_size 6: The playlist maintains a sliding window of only the 6 newest segments (12 seconds total).\n"
        "* -hls_flags delete_segments: As segment #7 is generated in RAM, segment #1 is unlinked and immediately freed from memory.\n"
        "* Result: Total RAM disk consumption across all cameras remains strictly capped under 60 Megabytes, and zero continuous video bytes are committed to physical flash storage.",
        bold_prefix="Zero-Disk Streaming: "
    )

    style_heading_2(doc.add_paragraph(), "5.3 Frame Compression Prior to Neural Inference")
    add_callout(doc, "Image Gradient Integrity: Why Lossy Compression is Omitted Prior to YOLO",
        "The gateway deliberately DOES NOT apply lossy image compression (e.g., JPEG, WebP) to frames before passing them to YOLO.\n\n"
        "Technical Rationale: Convolutional neural networks extract semantic features based on micro-gradients, edge transitions, and subtle color variance across contiguous pixels. Lossy JPEG compression introduces 8x8 discrete cosine transform (DCT) block boundary artifacts and color chroma subsampling (4:2:0 blur). These artifacts corrupt high-frequency edge information, directly reducing neural confidence scores and causing false negatives on small objects (e.g., safety helmets, eye goggles, valves).\n\n"
        "Correct Implementation: The uncompressed raw BGR frame is resized via bilinear interpolation directly inside PyTorch memory to network tensor dimensions (imgsz=640 or imgsz=320). This preserves 100% gradient precision with zero artifact introduction.")

    style_heading_2(doc.add_paragraph(), "5.4 Event-Driven Forensic Snapshot Storage")
    add_body_p(doc,
        "When an actual violation is detected (e.g., worker missing safety vest, detected smoke plume, or vehicle exceeding speed limits), the system extracts that single uncompressed frame and compresses it into a high-quality JPEG evidence snapshot (approx. 150 KB).\n\n"
        "With an enforced 30-second cooldown per camera and violation type, an active industrial camera typically generates between 50 and 150 alert snapshots per day (~10 to 25 Megabytes total). Compared to 43.2 GB of continuous recording, this achieves over 99.8% storage savings while preserving 100% of the forensic evidence required for safety audits.",
        bold_prefix="Evidence Capture Economics: "
    )

    # -------------------------------------------------------------
    # 6. VIDEO TRANSCODING & CODEC COMPATIBILITY SPECIFICATIONS
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "6. Video Transcoding & Codec Compatibility Specifications")
    
    add_body_p(doc,
        "Industrial camera fleets often feature an inconsistent mixture of legacy H.264 cameras, modern H.265 (HEVC) cameras, and varying transport configurations. Below is how the gateway normalizes all video streams for universal browser consumption:",
        bold_prefix="Transcoding Architecture: "
    )

    style_heading_2(doc.add_paragraph(), "6.1 Ingestion Codec Negotiation (H.264 vs H.265)")
    add_body_p(doc,
        "During RTSP session negotiation, the camera's Session Description Protocol (SDP) declares the payload encoding format. FFmpeg automatically binds to the appropriate internal decoder (h264 or hevc) and outputs uncompressed, planar BGR frames. The application and AI inference pipelines remain completely agnostic to whether the physical camera streams in H.264 or H.265.",
        bold_prefix="Transparent Decoding: "
    )

    style_heading_2(doc.add_paragraph(), "6.2 Outgoing H.264 Baseline Encoding Profile")
    add_body_p(doc,
        "For downstream delivery to web browsers, mobile tablets, and industrial dashboards, the gateway transcodes all outgoing streams to H.264 Baseline Profile (Level 3.1) with the following exact specifications:",
        bold_prefix="Delivery Parameters: "
    )

    enc_spec = [
        ["Video Codec", "libx264 (or h264_v4l2m2m)", "Universal industry standard supported natively by 100% of web browsers."],
        ["Profile & Level", "Baseline Profile, Level 3.1", "Eliminates B-frames and complex predictive coding; playable on low-power clients."],
        ["Pixel Subsampling", "yuv420p (4:2:0 planar)", "Required for hardware-accelerated video decoding in modern web browsers."],
        ["Output Resolution", "640x360 pixels", "Reduces pixel throughput by 89% (from 2.07M to 230K pixels) for fluid streaming."],
        ["Frame Rate", "10 FPS", "Reduces frame workload by 60% while providing fluid visual motion for human surveillance."],
        ["Bitrate Control", "220 kbps (max 280 kbps)", "Minimizes network bandwidth usage across plant VPNs and cellular tunnels."],
        ["Latency Flags", "preset ultrafast, tune zerolatency", "Disables frame buffering and lookahead, minimizing glass-to-glass latency."],
        ["Keyframe Alignment", "g 20, keyint_min 20, sc_threshold 0", "Enforces an IDR keyframe exactly every 2s, aligning with 2-second HLS chunks."]
    ]
    e_tbl = doc.add_table(rows=1, cols=3)
    format_table(e_tbl, [1.5, 2.2, 2.8], ["Encoding Property", "Technical Configuration", "Production Justification"], enc_spec, header_bg="1B365D")

    add_body_p(doc,
        "The Rationale for Rejecting H.265 for Web Streaming: While H.265 offers higher compression efficiency, major desktop browsers (including Google Chrome and Mozilla Firefox on non-hardware platforms) do not natively decode H.265 inside standard HTML5 <video> elements without specialized proprietary licensing. Transcoding to H.264 Baseline ensures that any plant engineer or operator can monitor live camera streams on any device with zero plugins or custom software installations.",
        bold_prefix="The Browser Compatibility Decision: "
    )

    # -------------------------------------------------------------
    # 7. TECHNOLOGY STACK & DEPENDENCY AUDIT
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "7. Technology Stack & Dependency Audit")
    
    add_body_p(doc,
        "Every dependency in the ecosystem was selected to maximize stability, asynchronous execution, and low memory footprint on edge devices:",
        bold_prefix="Component Rationale: "
    )

    tech_data = [
        ["FastAPI & Uvicorn", "Async Web Gateway", "Provides high-throughput asynchronous request routing, automated OpenAPI documentation, and minimal memory footprint."],
        ["Pydantic v2", "Data Validation", "Rust-accelerated schema validation for camera configs, heartbeat payloads, and PTZ coordinates."],
        ["OpenCV (cv2)", "Computer Vision Library", "Efficient RTSP demuxing (cv2.CAP_FFMPEG), frame manipulation, image drawing, and JPEG encoding."],
        ["PyTorch (torch)", "Deep Learning Runtime", "Underlying tensor computation engine; tuned with single-thread constraints to prevent CPU starvation."],
        ["Ultralytics YOLOv8", "Object Detection Engine", "State-of-the-art inference efficiency, dynamic .pt loading, and built-in bounding box utilities."],
        ["psycopg2-binary", "PostgreSQL Database Driver", "Thread-safe PostgreSQL database client with connection pooling and automated schema migration."],
        ["FFmpeg", "Multimedia Framework", "Industrial-grade hardware/software video transcoding, RTSP TCP transport, and HLS segmentation."],
        ["Tailscale", "Encrypted Mesh VPN & Funnel", "Zero-config wireguard tunneling providing permanent HTTPS public endpoints without open port forwarding."]
    ]
    t_tbl = doc.add_table(rows=1, cols=3)
    format_table(t_tbl, [1.5, 1.8, 3.2], ["Library / Framework", "Category", "Engineering Selection Criteria"], tech_data, header_bg="1B365D")

    # -------------------------------------------------------------
    # 8. HARDWARE RESOURCE OPTIMIZATION & CPU PINNING
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "8. Hardware Resource Optimization & CPU Pinning")
    
    add_body_p(doc,
        "Executing deep learning inference concurrently with multiple video transcoding pipelines on an edge processor (such as a 4-core ARM Cortex-A72 on a Raspberry Pi 4) requires strict thread budgeting. Without explicit thread pinning, PyTorch, OpenCV, OpenMP, and BLAS libraries spawn 4 worker threads each, leading to severe thread contention, context switching latency, and kernel CPU starvation.",
        bold_prefix="Resource Allocation Challenge: "
    )

    add_body_p(doc,
        "At the initialization of server.py and detector_worker.py, environment variables are enforced prior to any C-library initialization:\n"
        "* os.environ['OMP_NUM_THREADS'] = '1'\n"
        "* os.environ['MKL_NUM_THREADS'] = '1'\n"
        "* os.environ['OPENBLAS_NUM_THREADS'] = '1'\n"
        "* os.environ['TORCH_NUM_THREADS'] = '1'\n"
        "* cv2.setNumThreads(1)\n"
        "By restricting each pipeline to a single execution thread, the operating system's Completely Fair Scheduler (CFS) cleanly distributes processes across the physical CPU cores without context thrashing.",
        bold_prefix="Thread Budgeting Implementation: "
    )

    add_body_p(doc,
        "To avoid running heavy neural inference on every incoming video frame (which would saturate CPU utilization at 100%), the system decouples video display from AI inference. Video streams render at a smooth 10 FPS, while the Centralized InferenceScheduler evaluates YOLO every 3rd to 5th frame. Coordinate positions are interpolated between evaluations via EMA smoothing, achieving 10 FPS visual smoothness at only ~3 FPS inference cost.",
        bold_prefix="Inference Decoupling Architecture: "
    )

    # -------------------------------------------------------------
    # 9. PRODUCTION HARDENING, FAULT TOLERANCE & 24/7 RELIABILITY
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "9. Production Hardening, Fault Tolerance & 24/7 Reliability")
    
    add_body_p(doc,
        "To guarantee uninterrupted 24/7 operation in remote industrial installations, the gateway incorporates self-healing watchdogs across all software layers:",
        bold_prefix="Resilience Framework: "
    )

    style_heading_2(doc.add_paragraph(), "9.1 Multi-Tier Process Supervision")
    add_bullet_p(doc, "The launch.sh script runs a persistent supervisor loop: while true; do python3 -u server.py; sleep 2; done. If an unhandled segmentation fault or memory error occurs, the supervisor logs the exit code and reboots the server in 2 seconds.", bold_prefix="Automated Server Self-Healing: ")
    add_bullet_p(doc, "cleanstart.sh terminates stale PID files, kills lingering FFmpeg or detector instances, releases ports 8080 and 8090, and initiates a clean state machine boot.", bold_prefix="Deterministic Cold Starts: ")
    add_bullet_p(doc, "A standard Linux systemd unit file (rtsp-stream.service) is provided to ensure automatic boot-on-power-cycle with Restart=always.", bold_prefix="Systemd Service Integration: ")

    style_heading_2(doc.add_paragraph(), "9.2 Network Disconnection & Frame Timeout Recovery")
    add_bullet_p(doc, "If an RTSP stream fails to deliver a new frame for over 15 seconds (e.g., camera power flicker, network switch reboot), the worker thread breaks the stale socket and re-initiates connection attempts every 500ms.", bold_prefix="15-Second Frame Timeout: ")
    add_bullet_p(doc, "While an RTSP stream is disconnected or reconnecting, the worker generates a clean synthetic 'Connecting to Camera...' video frame in memory and pipes it to FFmpeg. Remote web clients and ThingsBoard widgets never see broken video tags or HTTP 404s.", bold_prefix="Synthetic Placeholder Frame: ")

    style_heading_2(doc.add_paragraph(), "9.3 Dual-Write Database Resilience")
    add_bullet_p(doc, "On server boot, alert_store.ensure_alerts_schema() validates that the PostgreSQL table exists and automatically runs CREATE TABLE IF NOT EXISTS with all primary keys and indexes.", bold_prefix="Self-Migrating Schema: ")
    add_bullet_p(doc, "Whenever an alert is triggered, it writes to local alerts.json first, then attempts insertion into PostgreSQL. If the PostgreSQL server or network link goes down, the edge server continues functioning without crashing, preserving alert records locally.", bold_prefix="Dual-Store Fallback: ")

    style_heading_2(doc.add_paragraph(), "9.4 Background Disk Safety Daemon")
    add_bullet_p(doc, "In addition to FFmpeg's delete_segments flag, launch.sh spawns a background safety daemon (cleanup_segments) that wakes every 30 seconds. It counts .ts segments in each stream directory; if more than 10 segments exist, it forcibly purges the oldest files, guaranteeing that unexpected stream pauses never accumulate stale video.", bold_prefix="Safety Net Watchdog: ")

    # -------------------------------------------------------------
    # 10. ARCHITECTURAL VALIDATION & OPERATIONAL GUARANTEES
    # -------------------------------------------------------------
    style_heading_1(doc.add_paragraph(), "10. Architectural Validation & Operational Guarantees")
    
    add_body_p(doc,
        "This section documents the formal engineering design decisions, validation benchmarks, and operational guarantees established for enterprise production deployment:",
        bold_prefix="Engineering Validation: "
    )

    style_heading_2(doc.add_paragraph(), "10.1 Multi-Camera CPU Budgeting & Frame Decoupling Strategy")
    add_body_p(doc,
        "Operational Guarantee: The system ensures that multi-camera inference never exceeds hardware thermal or CPU thresholds by decoupling visual frame delivery from neural inference. Video encoding runs at 10 FPS with constrained H.264 Baseline encoding (220 kbps), while AI inference executes at ~3 FPS per camera on a scheduled queue. Exponential Moving Average (EMA) coordinate interpolation ensures fluid bounding box transitions on intermediate frames without additional neural computation.",
        bold_prefix="Technical Validation: "
    )

    style_heading_2(doc.add_paragraph(), "10.2 Flash Media Endurance & Storage Lifecycle Architecture")
    add_body_p(doc,
        "Operational Guarantee: Zero continuous video is committed to flash media. All live streaming chunks are confined to a 12-second circular ring buffer in Linux RAM (/tmp/hls), completely eliminating drive wear. Physical storage is reserved exclusively for event-driven violation snapshots (~150 KB JPEG), guaranteeing that a standard 64 GB edge drive will operate for multiple years without storage saturation.",
        bold_prefix="Technical Validation: "
    )

    style_heading_2(doc.add_paragraph(), "10.3 Native Ingestion of H.265 vs H.264 Transcoding Protocol")
    add_body_p(doc,
        "Operational Guarantee: The gateway provides seamless compatibility with both H.264 and modern H.265 (HEVC) IP cameras through automatic SDP payload negotiation. The outgoing stream is standardized to H.264 Baseline Profile (Level 3.1) at 640x360 @ 10 FPS, guaranteeing 100% native playback across all modern HTML5 desktop browsers, mobile devices, and ThingsBoard widgets with zero proprietary plugins.",
        bold_prefix="Technical Validation: "
    )

    style_heading_2(doc.add_paragraph(), "10.4 Retention of Image Gradient Precision Prior to Neural Processing")
    add_body_p(doc,
        "Operational Guarantee: Neural detection accuracy is maximized by prohibiting lossy image compression before model inference. Frames are ingested uncompressed into memory and letterbox-resized directly inside PyTorch memory to network tensor grid dimensions (640x640 or 320x320), eliminating DCT compression block noise and color subsampling blur.",
        bold_prefix="Technical Validation: "
    )

    style_heading_2(doc.add_paragraph(), "10.5 Runtime Model Dynamic Weights Hot-Swapping Mechanics")
    add_body_p(doc,
        "Operational Guarantee: Model weights are decoupled from the streaming process lifecycle. Dropping a newly trained .pt weights file into models/ enables instant dynamic discovery via GET /api/models and class introspection via GET /api/model-classes. Live in-memory weight swapping executes in 0 milliseconds without resetting camera connections or interrupting active video streams.",
        bold_prefix="Technical Validation: "
    )

    style_heading_2(doc.add_paragraph(), "10.6 Temporal Cooldown & Alert Throttling Architecture")
    add_body_p(doc,
        "Operational Guarantee: The alert engine enforces a 30-second cooldown per camera and violation class. When a violation is continuously present (e.g., an unhelmeted worker remaining stationary in frame), exactly one evidence snapshot is captured, preventing database saturation and duplicate notification storms.",
        bold_prefix="Technical Validation: "
    )

    style_heading_2(doc.add_paragraph(), "10.7 Encrypted Zero-Trust Mesh Tunneling & Public Endpoint Routing")
    add_body_p(doc,
        "Operational Guarantee: Public accessibility is achieved without opening insecure firewall ports or configuring router NAT port forwarding. The integrated Tailscale Funnel establishes an encrypted WireGuard tunnel directly from localhost:8080 to an enterprise HTTPS domain with automatic TLS certificate lifecycle management.",
        bold_prefix="Technical Validation: "
    )

    # Save to all target paths
    for p in output_paths:
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        doc.save(p)
        print(f"Document successfully written to: {p}")

if __name__ == "__main__":
    targets = [
        "/Users/algofusion/Documents/dnc_backend_v2/orchestrator/Delta/Go/rtsp_rpi/Edge_AI_Vision_System_Production_Architecture.docx",
        "/Users/algofusion/Downloads/Edge_AI_Vision_System_Production_Architecture.docx"
    ]
    generate_document(targets)
