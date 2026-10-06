// Data Extractor AI Frontend Handling

let currentRecords = [];
let filteredRecords = [];
let originalRecords = [];
let undoSnapshots = [];
let currentFolderName = "";
let currentPage = 1;
let pageSize = 25;
let pollInterval = null;
let currentAuditLogs = [];
let autoScrollAudit = true;
let currentResultObj = null;
let activeAuditReport = null;
let showOnlyVerified = false;

// Live streaming & execution control variables
let currentJobId = null;
let currentCatalog = null;
let previousLiveCount = 0;
let isPollingActive = false;

// Interactive Excel Grid Variables
let activeColumns = [];
let selectedRowIndices = new Set();
let activeCell = null; // { rowIndex, colKey }
let editingCell = null; // { rowIndex, colKey, originalVal }
let dirtyCells = new Set(); // Set of "rowIndex_colKey"
let excelUndoStack = [];
let excelRedoStack = [];
let unsavedEditsCount = 0;
let columnFilters = {}; // { [colKey]: Set of allowed string values }
let currentSort = { colKey: null, dir: null }; // 'asc' | 'desc' | null
let currentFindMatches = [];
let currentFindMatchIdx = -1;
let contextMenuTarget = { rowIndex: null, colKey: null };

document.addEventListener("DOMContentLoaded", () => {
  const initFns = [
    ["initTheme", initTheme],
    ["initAppRouting", initAppRouting],
    ["initDrawerCollapse", initDrawerCollapse],
    ["initStatus", initStatus],
    ["initBatches", initBatches],
    ["initHistory", initHistory],
    ["initUpload", initUpload],
    ["initSearch", initSearch],
    ["initAuditControls", initAuditControls],
    ["initExtractionControls", initExtractionControls],
    ["initChatbox", initChatbox],
    ["initCloudModal", initCloudModal],
    ["initResultsTabs", initResultsTabs],
    ["initAiCrosscheckModal", initAiCrosscheckModal],
    ["initTabularModal", initTabularModal],
    ["initBatchSuite", initBatchSuite],
    ["initDataCleaningUI", initDataCleaningUI],
    ["initInteractiveExcelGrid", initInteractiveExcelGrid],
    ["initSubjectAnalysisUI", initSubjectAnalysisUI],
    ["initSettingsView", initSettingsView]
  ];
  for (const [name, fn] of initFns) {
    try {
      if (typeof fn === "function") fn();
    } catch (err) {
      console.warn(`[Init] ${name} startup warning:`, err);
    }
  }
});

// 0. Theme Toggle & Palette Controller (Day / Night Mode)
function initTheme() {
  const themeToggleBtn = document.getElementById("themeToggleBtn");

  // Retrieve saved preference or default to dark
  const saved = localStorage.getItem("data-extractor-ai-theme") || localStorage.getItem("theme");
  const preferredTheme = saved ? saved : (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");

  applyTheme(preferredTheme, false);

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || (document.body && document.body.classList.contains("light-theme") ? "light" : "dark");
      const nextTheme = current === "light" ? "dark" : "light";
      applyTheme(nextTheme, true);
    });
  }

  // Listen for OS theme changes if user has not explicitly persisted a preference
  if (window.matchMedia) {
    try {
      window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", (e) => {
        if (!localStorage.getItem("data-extractor-ai-theme")) {
          applyTheme(e.matches ? "light" : "dark", false);
        }
      });
    } catch (e) {}
  }
}

function applyTheme(theme, save = true) {
  const isLight = theme === "light";

  // Set html and body theme attributes and classes
  document.documentElement.setAttribute("data-theme", theme);
  if (document.body) {
    document.body.classList.remove("dark-theme", "light-theme");
    document.body.classList.add(isLight ? "light-theme" : "dark-theme");
  }

  // Update theme button UI
  const themeBtn = document.getElementById("themeToggleBtn");
  const themeLabel = document.getElementById("themeLabelText");
  if (themeBtn) {
    themeBtn.setAttribute("data-theme", theme);
    themeBtn.setAttribute("title", isLight ? "Switch to Night mode (Dark)" : "Switch to Day mode (Light)");
    themeBtn.setAttribute("aria-label", isLight ? "Switch to Night mode (Dark)" : "Switch to Day mode (Light)");
  }
  if (themeLabel) {
    themeLabel.textContent = isLight ? "Night" : "Day";
  }

  if (save) {
    localStorage.setItem("data-extractor-ai-theme", theme);
  }

  // Sync Chart.js default colors
  updateChartsTheme(theme);
}

function updateChartsTheme(theme) {
  if (typeof Chart === "undefined") return;
  const isLight = theme === "light";

  Chart.defaults.color = isLight ? "#475569" : "#94A3B8";
  Chart.defaults.borderColor = isLight ? "rgba(0, 0, 0, 0.08)" : "rgba(255, 255, 255, 0.07)";

  const charts = [];
  if (typeof chartInstances !== "undefined" && chartInstances) {
    Object.values(chartInstances).forEach(c => { if (c) charts.push(c); });
  }
  if (typeof batchChartInstances !== "undefined" && batchChartInstances) {
    Object.values(batchChartInstances).forEach(c => { if (c) charts.push(c); });
  }

  charts.forEach(chart => {
    try {
      if (chart.options && chart.options.scales) {
        Object.values(chart.options.scales).forEach(scale => {
          if (scale.ticks) scale.ticks.color = isLight ? "#475569" : "#94A3B8";
          if (scale.grid) scale.grid.color = isLight ? "rgba(0, 0, 0, 0.06)" : "rgba(255, 255, 255, 0.07)";
        });
      }
      if (chart.options?.plugins?.legend?.labels) {
        chart.options.plugins.legend.labels.color = isLight ? "#334155" : "#CBD5E1";
      }
      chart.update();
    } catch (e) {}
  });
}

// ==========================================================================
// PHASE 1: APPLICATION SHELL, PERSISTENT COLLAPSIBLE SIDE DRAWER & ROUTING
// ==========================================================================

const APP_ROUTES = {
  "/extract": { id: "viewExtract", title: "Extract & Audit" },
  "/historical": { id: "viewHistorical", title: "Historical Data" },
  "/analysis": { id: "viewAnalysis", title: "Analysis Overview" },
  "/analysis/subject": { id: "viewSubjectAnalysis", title: "Subject-wise Analysis" },
  "/reports": { id: "viewReports", title: "Generated Reports" },
  "/settings": { id: "viewSettings", title: "Settings" }
};

let currentActiveRoute = "/extract";

function normalizeRoute(path) {
  if (!path || path === "/" || path === "") return "/extract";
  // Strip trailing slash
  const clean = path.replace(/\/+$/, "") || "/";
  if (APP_ROUTES[clean]) return clean;
  if (clean.startsWith("/analysis/")) return "/analysis/subject";
  return "/extract";
}

function initAppRouting() {
  // 1. Intercept drawer navigation item clicks
  document.querySelectorAll(".drawer-nav-item, [data-route]").forEach(link => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const target = link.getAttribute("data-route") || link.getAttribute("href");
      if (target) {
        navigateTo(target, true);
      }
    });
  });

  // 2. Intercept browser Back / Forward events
  window.addEventListener("popstate", () => {
    const route = window.location.pathname;
    navigateTo(route, false);
  });

  // 3. Resolve initial route
  const initialRoute = normalizeRoute(window.location.pathname);
  navigateTo(initialRoute, false);
}

function navigateTo(route, pushState = true) {
  const targetRoute = normalizeRoute(route);
  currentActiveRoute = targetRoute;

  // 1. Synchronize HTML5 History
  if (pushState && window.location.pathname !== targetRoute) {
    history.pushState({ route: targetRoute }, "", targetRoute);
  }

  // 2. Update persistent side drawer active indicator
  document.querySelectorAll(".drawer-nav-item").forEach(item => {
    const itemRoute = item.getAttribute("data-route");
    if (itemRoute === targetRoute) {
      item.classList.add("active");
      item.setAttribute("aria-current", "page");
    } else {
      item.classList.remove("active");
      item.removeAttribute("aria-current");
    }
  });

  // 3. Render ONLY the active Workspace View (completely hide other workspaces)
  const views = document.querySelectorAll(".workspace-view");
  views.forEach(view => {
    const viewRoute = view.getAttribute("data-view");
    if (viewRoute === targetRoute) {
      view.classList.remove("hidden");
      view.classList.add("active");
    } else {
      view.classList.add("hidden");
      view.classList.remove("active");
    }
  });

  // 4. Synchronize Single vs Batch buttons
  const btnSingle = document.getElementById("btnModeSingle");
  const btnBatch = document.getElementById("btnModeBatch");
  if (btnSingle && btnBatch) {
    if (targetRoute === "/extract") {
      btnSingle.classList.add("active");
      btnBatch.classList.remove("active");
    } else if (targetRoute === "/analysis") {
      btnBatch.classList.add("active");
      btnSingle.classList.remove("active");
    }
  }

  // 5. Trigger view-specific data initializations
  if (targetRoute === "/historical") {
    if (typeof initHistory === "function") initHistory();
  } else if (targetRoute === "/analysis") {
    if (typeof loadBatchSuiteDatasets === "function") loadBatchSuiteDatasets();
  } else if (targetRoute === "/analysis/subject") {
    if (typeof initSubjectAnalysisUI === "function") initSubjectAnalysisUI();
  } else if (targetRoute === "/settings") {
    if (typeof initSettingsView === "function") initSettingsView();
  }

  // 6. Reset main workspace scroll position smoothly
  const workspaceContainer = document.getElementById("appWorkspaceContainer");
  if (workspaceContainer) {
    workspaceContainer.scrollTop = 0;
  }
}

// Drawer Collapsible Controller
function initDrawerCollapse() {
  const shell = document.getElementById("appLayoutShell");
  const btnToggleDrawer = document.getElementById("btnToggleDrawer");
  const btnDrawerPin = document.getElementById("btnDrawerPin");
  const btnDrawerToggleSettings = document.getElementById("btnDrawerToggleSettings");

  if (!shell) return;

  // Retrieve saved preference or auto-collapse on small screens (<1024px)
  const saved = localStorage.getItem("data-extractor-drawer-collapsed");
  const shouldCollapse = saved !== null ? saved === "true" : (window.innerWidth < 1024);
  applyDrawerCollapsed(shouldCollapse, false);

  const toggle = () => {
    const isCurrentlyCollapsed = shell.classList.contains("drawer-collapsed");
    applyDrawerCollapsed(!isCurrentlyCollapsed, true);
  };

  if (btnToggleDrawer) btnToggleDrawer.addEventListener("click", toggle);
  if (btnDrawerPin) btnDrawerPin.addEventListener("click", toggle);
  if (btnDrawerToggleSettings) btnDrawerToggleSettings.addEventListener("click", toggle);

  // Global Keyboard Shortcut: Ctrl+B or Cmd+B
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
      const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : "";
      if (tag === "input" || tag === "textarea") return;
      e.preventDefault();
      toggle();
    }
  });

  // Responsive resize watcher: auto-collapse on small viewport if user didn't explicitly store
  window.addEventListener("resize", () => {
    if (localStorage.getItem("data-extractor-drawer-collapsed") === null) {
      if (window.innerWidth < 1024 && !shell.classList.contains("drawer-collapsed")) {
        applyDrawerCollapsed(true, false);
      } else if (window.innerWidth >= 1024 && shell.classList.contains("drawer-collapsed")) {
        applyDrawerCollapsed(false, false);
      }
    }
  });
}

function applyDrawerCollapsed(collapsed, save = true) {
  const shell = document.getElementById("appLayoutShell");
  const btnToggleDrawer = document.getElementById("btnToggleDrawer");
  const btnDrawerPin = document.getElementById("btnDrawerPin");
  const drawer = document.getElementById("appSidebarDrawer");

  if (!shell) return;

  if (collapsed) {
    shell.classList.add("drawer-collapsed");
    if (drawer) drawer.setAttribute("aria-expanded", "false");
    if (btnToggleDrawer) {
      btnToggleDrawer.setAttribute("title", "Expand Sidebar (Ctrl+B)");
      btnToggleDrawer.setAttribute("aria-label", "Expand Sidebar");
    }
    if (btnDrawerPin) {
      btnDrawerPin.setAttribute("title", "Expand Sidebar (Ctrl+B)");
      btnDrawerPin.setAttribute("aria-label", "Expand Sidebar");
    }
  } else {
    shell.classList.remove("drawer-collapsed");
    if (drawer) drawer.setAttribute("aria-expanded", "true");
    if (btnToggleDrawer) {
      btnToggleDrawer.setAttribute("title", "Collapse Sidebar (Ctrl+B)");
      btnToggleDrawer.setAttribute("aria-label", "Collapse Sidebar");
    }
    if (btnDrawerPin) {
      btnDrawerPin.setAttribute("title", "Collapse Sidebar (Ctrl+B)");
      btnDrawerPin.setAttribute("aria-label", "Collapse Sidebar");
    }
  }

  if (save) {
    localStorage.setItem("data-extractor-drawer-collapsed", collapsed ? "true" : "false");
  }
}

// Subject-wise Analysis View Controller
async function initSubjectAnalysisUI() {
  const datasetSelect = document.getElementById("subjectDatasetSelect");
  const semSelect = document.getElementById("subjectSemesterSelect");
  const subjectSelect = document.getElementById("subjectItemSelect");
  if (!datasetSelect) return;

  if (datasetSelect.dataset.initialized === "true") return;
  datasetSelect.dataset.initialized = "true";

  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    const history = data.history || [];

    datasetSelect.innerHTML = `<option value="">-- Choose Dataset --</option>`;
    history.forEach(item => {
      const opt = document.createElement("option");
      opt.value = item.folder_name;
      opt.textContent = `${item.folder_name} (Sem ${item.semester} • ${item.total_students} students)`;
      datasetSelect.appendChild(opt);
    });
  } catch (e) {
    console.warn("Failed to load datasets for subject analysis:", e);
  }

  datasetSelect.addEventListener("change", () => {
    const chosen = datasetSelect.value;
    if (semSelect) semSelect.disabled = !chosen;
    if (subjectSelect) subjectSelect.disabled = !chosen;
  });
}

// Settings View Controller
function initSettingsView() {
  const modelSelect = document.getElementById("settingsModelSelect");
  const topModelSelector = document.getElementById("modelSelector");
  const btnCloudModal = document.getElementById("btnOpenCloudModalFromSettings");
  const btnThemeToggle = document.getElementById("btnThemeToggleSettings");

  if (modelSelect && topModelSelector && topModelSelector.options && topModelSelector.options.length > 0) {
    modelSelect.innerHTML = topModelSelector.innerHTML;
    modelSelect.value = topModelSelector.value;

    if (!modelSelect.dataset.bound) {
      modelSelect.dataset.bound = "true";
      modelSelect.addEventListener("change", () => {
        topModelSelector.value = modelSelect.value;
        const event = new Event("change");
        topModelSelector.dispatchEvent(event);
      });
    }
  }

  if (btnCloudModal && !btnCloudModal.dataset.bound) {
    btnCloudModal.dataset.bound = "true";
    btnCloudModal.addEventListener("click", () => {
      const modal = document.getElementById("cloudModalOverlay");
      if (modal) modal.classList.remove("hidden");
    });
  }

  if (btnThemeToggle && !btnThemeToggle.dataset.bound) {
    btnThemeToggle.dataset.bound = "true";
    btnThemeToggle.addEventListener("click", () => {
      const themeBtn = document.getElementById("themeToggleBtn");
      if (themeBtn) themeBtn.click();
    });
  }
}

// 1. Status Polling & Model Switcher
async function initStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();

    const badge = document.getElementById("aiStatusBadge");
    const statusText = document.getElementById("aiStatusText");
    const modelTag = document.getElementById("activeModelTag");
    const chatModelTag = document.getElementById("chatModelTag");
    const auditGpuBadge = document.getElementById("auditGpuBadge");
    const auditGpuText = document.getElementById("auditGpuText");
    const telemEngine = document.getElementById("telemEngine");
    const pickerContainer = document.getElementById("modelPickerContainer");
    const modelSelector = document.getElementById("modelSelector");

    // Cloud AI Status Pill
    const cloudBadge = document.getElementById("cloudStatusBadge");
    const cloudText = document.getElementById("cloudStatusText");
    if (cloudBadge && cloudText && data.cloud_ai) {
      if (data.cloud_ai.is_configured) {
        cloudBadge.className = "status-pill status-cloud-pill";
        cloudBadge.querySelector(".status-dot").className = "status-dot green";
        cloudText.textContent = `${data.cloud_ai.provider.toUpperCase()} (${data.cloud_ai.model})`;
      } else {
        cloudBadge.className = "status-pill status-cloud-pill";
        cloudBadge.querySelector(".status-dot").className = "status-dot gray";
        cloudText.textContent = "Cloud AI: Not Set";
      }
    }

    if (data.ollama_available) {
      if (badge) {
        badge.className = "status-pill status-trained";
        const dot = badge.querySelector(".status-dot");
        if (dot) dot.className = "status-dot green";
      }
      if (statusText) statusText.textContent = `Local AI Active (${data.active_model})`;
      if (modelTag) modelTag.textContent = data.active_model;
      if (chatModelTag) chatModelTag.textContent = data.active_model;
      if (auditGpuBadge) {
        const dot = auditGpuBadge.querySelector(".status-dot");
        if (dot) dot.className = "status-dot green";
      }
      if (auditGpuText) auditGpuText.textContent = `Ollama Online (${data.active_model} on GPU)`;
      if (telemEngine) telemEngine.textContent = `${data.active_model} on Local GPU`;

      // Populate Model Picker Dropdown
      if (pickerContainer && modelSelector && Array.isArray(data.available_models) && data.available_models.length > 0) {
        pickerContainer.style.display = "flex";
        modelSelector.innerHTML = "";
        data.available_models.forEach(m => {
          const opt = document.createElement("option");
          opt.value = m;
          opt.textContent = m;
          if (m === data.active_model) opt.selected = true;
          modelSelector.appendChild(opt);
        });

        // Add change listener if not already attached
        if (!modelSelector.dataset.initialized) {
          modelSelector.dataset.initialized = "true";
          modelSelector.addEventListener("change", async (e) => {
            const selected = e.target.value;
            try {
              const swRes = await fetch("/api/model/switch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ model: selected })
              });
              const swData = await swRes.json();
              if (swData.success) {
                statusText.textContent = `Local AI Active (${swData.active_model})`;
                if (modelTag) modelTag.textContent = swData.active_model;
                if (chatModelTag) chatModelTag.textContent = swData.active_model;
                if (auditGpuText) auditGpuText.textContent = `Ollama Online (${swData.active_model} on GPU)`;
                if (telemEngine) telemEngine.textContent = `${swData.active_model} on Local GPU`;
                appendChatMessage("assistant", `⚡ Switched active local model to <strong>${escapeHtml(swData.active_model)}</strong>.`);
              }
            } catch (err) {
              console.error("Failed to switch model:", err);
            }
          });
        }
      }
    } else {
      if (badge) {
        badge.className = "status-pill status-trained";
        const dot = badge.querySelector(".status-dot");
        if (dot) dot.className = "status-dot green";
      }
      if (statusText) statusText.textContent = "Possibility Engine Active (Fast Offline)";
      if (modelTag) modelTag.textContent = "Pre-Trained KB";
      if (pickerContainer) pickerContainer.style.display = "none";
      if (auditGpuBadge) {
        const dot = auditGpuBadge.querySelector(".status-dot");
        if (dot) dot.className = "status-dot amber";
      }
      if (auditGpuText) auditGpuText.textContent = "Possibility Engine (Fast Offline)";
      if (telemEngine) telemEngine.textContent = "Deterministic Archetype Engine";
    }
  } catch (e) {
    console.error("Status fetch failed:", e);
  }
}

// 2. Load Batch Files
async function initBatches() {
  try {
    const res = await fetch("/api/batches");
    const data = await res.json();
    const select = document.getElementById("batchSelect");

    for (const [batchName, files] of Object.entries(data.batches || {})) {
      const optGroup = document.createElement("optgroup");
      optGroup.label = batchName;
      files.forEach(f => {
        const opt = document.createElement("option");
        opt.value = f.rel_path;
        opt.textContent = `${f.filename}`;
        optGroup.appendChild(opt);
      });
      select.appendChild(optGroup);
    }

    select.addEventListener("change", (e) => {
      const btn = document.getElementById("extractBtn");
      btn.disabled = !e.target.value;
      if (e.target.value) {
        document.getElementById("fileInput").value = "";
        window.currentSelectedFile = null;
        resetFileUploadDisplay();

        const val = e.target.value.trim();
        const ext = val.split('.').pop().toLowerCase();
        if (["xlsx", "xls", "csv"].includes(ext)) {
          btn.innerHTML = `<span>✨ Ingest &amp; Fix Spreadsheet</span>`;
          inspectTabularBatchFile(val);
        } else {
          btn.innerHTML = `<span>⚡ Extract Data</span>`;
        }
      } else {
        btn.innerHTML = `<span>⚡ Extract Data</span>`;
      }
      updateExtractionHint();
    });
  } catch (e) {
    console.error("Failed to load batches:", e);
  }
}

// Helper: Dynamically update extraction hint based on selection and active pipeline
function updateExtractionHint() {
  const hint = document.getElementById("extractBtnHint");
  if (!hint) return;
  const fileInput = document.getElementById("fileInput");
  const batchSelect = document.getElementById("batchSelect");
  const selectedFile = (fileInput && fileInput.files && fileInput.files[0]) || window.currentSelectedFile;
  const selectedBatch = batchSelect && batchSelect.value;

  const pipelineRadio = document.querySelector('input[name="extractionPipeline"]:checked');
  const pipelineVal = pipelineRadio ? pipelineRadio.value : "local";
  let engineName = "Standard Local AI";
  if (pipelineVal === "cloud") engineName = "Pure Cloud AI";
  else if (pipelineVal === "visual_blueprint") engineName = "Vision AI";

  if (selectedFile) {
    hint.textContent = `Ready: ${selectedFile.name} (${engineName})`;
  } else if (selectedBatch) {
    const filename = selectedBatch.split(/[/\\]/).pop();
    hint.textContent = `Ready: ${filename} (${engineName})`;
  } else {
    hint.textContent = "Select a source file or historical batch to start";
  }
}

// Helper: Reset file upload display to prompt state
function resetFileUploadDisplay() {
  const uploadPrompt = document.getElementById("uploadPrompt");
  const selectedFileBadge = document.getElementById("selectedFileBadge");
  if (uploadPrompt) uploadPrompt.classList.remove("hidden");
  if (selectedFileBadge) selectedFileBadge.classList.add("hidden");
  updateExtractionHint();
}

// 3. File Upload & Drop Area
function initUpload() {
  const dropArea = document.getElementById("dropArea");
  const fileInput = document.getElementById("fileInput");
  const extractBtn = document.getElementById("extractBtn");

  if (dropArea && fileInput) {
    // Unified click listener on dropArea (and browseBtn / change-file-pill inside it)
    dropArea.addEventListener("click", () => {
      fileInput.value = "";
      fileInput.click();
    });

    // Keyboard navigation support for accessible dropArea
    dropArea.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        fileInput.value = "";
        fileInput.click();
      }
    });

    dropArea.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropArea.classList.add("dragover");
    });

    dropArea.addEventListener("dragleave", () => {
      dropArea.classList.remove("dragover");
    });

    dropArea.addEventListener("drop", (e) => {
      e.preventDefault();
      dropArea.classList.remove("dragover");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        handleFileSelected(e.dataTransfer.files[0]);
      }
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files[0]) {
        handleFileSelected(e.target.files[0]);
      }
    });
  }

  // Pipeline Radio Toggle Listeners
  const pipeLocal = document.getElementById("pipelineLocal");
  const pipeCloud = document.getElementById("pipelineCloud");
  const pipeVisual = document.getElementById("pipelineVisual");
  const pipeLocalLabel = document.getElementById("pipelineLocalLabel");
  const pipeCloudLabel = document.getElementById("pipelineCloudLabel");
  const pipeVisualLabel = document.getElementById("pipelineVisualLabel");

  function setPipelineActive(activeType) {
    if (pipeLocal) pipeLocal.checked = (activeType === "local");
    if (pipeCloud) pipeCloud.checked = (activeType === "cloud");
    if (pipeVisual) pipeVisual.checked = (activeType === "visual_blueprint");

    if (pipeLocalLabel) pipeLocalLabel.classList.toggle("active", activeType === "local");
    if (pipeCloudLabel) pipeCloudLabel.classList.toggle("active", activeType === "cloud");
    if (pipeVisualLabel) pipeVisualLabel.classList.toggle("active", activeType === "visual_blueprint");

    const activeEngineTag = document.getElementById("activeEngineTag");
    const auditCollapsedEngine = document.getElementById("auditCollapsedEngine");
    const telemEngine = document.getElementById("telemEngine");
    const auditGpuText = document.getElementById("auditGpuText");

    if (activeType === "local") {
      if (activeEngineTag) activeEngineTag.textContent = "Local AI Active";
      if (auditCollapsedEngine) auditCollapsedEngine.textContent = "Engine: Qwen 2.5 7B";
      if (telemEngine) telemEngine.textContent = "Qwen 2.5 (7B) on RTX 4060 GPU";
      if (auditGpuText) auditGpuText.textContent = "Ollama Ready";
    } else if (activeType === "cloud") {
      if (activeEngineTag) activeEngineTag.textContent = "Cloud AI Active";
      if (auditCollapsedEngine) auditCollapsedEngine.textContent = "Engine: Cloud AI (Gemini/Groq)";
      if (telemEngine) telemEngine.textContent = "Cloud AI (Multi-Provider Orchestrator: Gemini / Groq)";
      if (auditGpuText) auditGpuText.textContent = "Cloud AI Ready";
    } else if (activeType === "visual_blueprint") {
      if (activeEngineTag) activeEngineTag.textContent = "Vision AI Active";
      if (auditCollapsedEngine) auditCollapsedEngine.textContent = "Engine: Multimodal Vision AI";
      if (telemEngine) telemEngine.textContent = "Visual Blueprint & Sliced Stream (Multimodal Vision AI)";
      if (auditGpuText) auditGpuText.textContent = "Vision AI Ready";
    }
    updateExtractionHint();
  }

  [
    { radio: pipeLocal, label: pipeLocalLabel, val: "local" },
    { radio: pipeCloud, label: pipeCloudLabel, val: "cloud" },
    { radio: pipeVisual, label: pipeVisualLabel, val: "visual_blueprint" }
  ].forEach(item => {
    if (item.radio) {
      item.radio.addEventListener("change", (e) => {
        if (e.target.checked) setPipelineActive(item.val);
      });
    }
    if (item.label) {
      item.label.addEventListener("click", (e) => {
        if (e.target.tagName !== "INPUT") {
          setPipelineActive(item.val);
        }
      });
    }
  });

  // Toggle Manual Metadata Override Drawer
  const btnToggleMeta = document.getElementById("btnToggleMetaOverride");
  const metaDrawer = document.getElementById("metaOverrideDrawer");
  if (btnToggleMeta && metaDrawer && !btnToggleMeta.dataset.bound) {
    btnToggleMeta.dataset.bound = "true";
    btnToggleMeta.addEventListener("click", (e) => {
      e.preventDefault();
      metaDrawer.classList.toggle("hidden");
    });
  }

  if (extractBtn && !extractBtn.dataset.bound) {
    extractBtn.dataset.bound = "true";
    extractBtn.addEventListener("click", startExtraction);
  }
}

function handleFileSelected(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  const validExts = ["pdf", "xlsx", "xls", "csv"];
  if (!validExts.includes(ext)) {
    alert("Please select a PDF (.pdf), Excel (.xlsx, .xls), or CSV (.csv) file.");
    return;
  }
  window.currentSelectedFile = file;

  const batchSelect = document.getElementById("batchSelect");
  if (batchSelect) batchSelect.value = "";

  const extractBtn = document.getElementById("extractBtn");
  if (extractBtn) extractBtn.disabled = false;

  const uploadPrompt = document.getElementById("uploadPrompt");
  const selectedFileBadge = document.getElementById("selectedFileBadge");
  const selectedFileName = document.getElementById("selectedFileName");
  const selectedFileSize = document.getElementById("selectedFileSize");
  const selectedFileIcon = document.getElementById("selectedFileIcon");

  if (uploadPrompt) uploadPrompt.classList.add("hidden");
  if (selectedFileBadge) {
    selectedFileBadge.classList.remove("hidden");
    if (selectedFileName) selectedFileName.textContent = file.name;
    const mbSize = (file.size / (1024 * 1024)).toFixed(2);
    if (selectedFileSize) selectedFileSize.textContent = `(${mbSize} MB)`;
    if (selectedFileIcon) {
      if (ext === "pdf") selectedFileIcon.textContent = "📄";
      else if (ext === "csv") selectedFileIcon.textContent = "📑";
      else selectedFileIcon.textContent = "📊";
    }
  }

  updateExtractionHint();

  // If PDF file, trigger quick classification probe
  if (ext === "pdf") {
    window.currentClassification = null;
    const clfData = new FormData();
    clfData.append("pdf_file", file);
    fetch("/api/classify", { method: "POST", body: clfData })
      .then(r => r.json())
      .then(d => {
        if (d.success) {
          window.currentClassification = d;
          if (selectedFileSize) {
            const dupWarning = d.is_duplicate ? " ⚠️ [Existing Dataset]" : "";
            selectedFileSize.textContent = `(${mbSize} MB) • 🏷️ ${d.department} | ${d.batch} | ${d.semester}${dupWarning}`;
          }

          // Automatically sync detected values into the manual override inputs
          const deptInp = document.getElementById("overrideDeptInput");
          const batchInp = document.getElementById("overrideBatchInput");
          const semInp = document.getElementById("overrideSemInput");
          const statusBadge = document.getElementById("metaOverrideStatusBadge");

          if (deptInp && d.department) deptInp.value = d.department;
          if (batchInp && d.batch) batchInp.value = d.batch;
          if (semInp && d.semester) semInp.value = d.semester;
          if (statusBadge) statusBadge.textContent = "AI Classified";

          const resDept = document.getElementById("resultsDeptInput");
          const resBatch = document.getElementById("resultsBatchInput");
          const resSem = document.getElementById("resultsSemInput");
          if (resDept && d.department) resDept.value = d.department;
          if (resBatch && d.batch) resBatch.value = d.batch;
          if (resSem && d.semester) resSem.value = d.semester;

          const resultsMetaBadge = document.getElementById("resultsMetaBadge");
          if (resultsMetaBadge) resultsMetaBadge.textContent = "AI Classified";

          const dStr = d.department || "<Department>";
          const bStr = d.batch || "<Batch>";
          const sStr = d.semester || "<Semester>";
          const pdfPathStr = `departments_data/pdf/${dStr}/${bStr}/${sStr}.pdf`;
          const excelPathStr = `departments_data/excel/${dStr}/${bStr}/${sStr}.xlsx`;

          const resultsExcelPreview = document.getElementById("resultsMirrorPathPreview");
          if (resultsExcelPreview) resultsExcelPreview.textContent = excelPathStr;

          const resultsPdfPreview = document.getElementById("resultsPdfMirrorPathPreview");
          if (resultsPdfPreview) resultsPdfPreview.textContent = pdfPathStr;

          const stepPdfPreview = document.getElementById("stepPdfMirrorPreview");
          if (stepPdfPreview) stepPdfPreview.textContent = pdfPathStr;

          const stepExcelPreview = document.getElementById("stepExcelMirrorPreview");
          if (stepExcelPreview) stepExcelPreview.textContent = excelPathStr;
        }
      })
      .catch(err => console.warn("Background classification probe warning:", err));
  }

  // If tabular file, launch Tabular Inspection Modal immediately
  if (["xlsx", "xls", "csv"].includes(ext)) {
    inspectTabularFile(file);
  }
}

// 4. Start Extraction
async function startExtraction() {
  const fileInput = document.getElementById("fileInput");
  const batchSelect = document.getElementById("batchSelect");
  const extractBtn = document.getElementById("extractBtn");

  const formData = new FormData();
  const selectedFile = (fileInput.files && fileInput.files[0]) || window.currentSelectedFile;
  if (selectedFile) {
    const ext = selectedFile.name.split('.').pop().toLowerCase();
    if (["xlsx", "xls", "csv"].includes(ext)) {
      inspectTabularFile(selectedFile);
      return;
    }
    formData.append("pdf_file", selectedFile);
  } else if (batchSelect && batchSelect.value) {
    const batchVal = batchSelect.value.trim();
    const ext = batchVal.split('.').pop().toLowerCase();
    if (["xlsx", "xls", "csv"].includes(ext)) {
      inspectTabularBatchFile(batchVal);
      return;
    }
    formData.append("batch_file", batchVal);
  } else {
    alert("Please choose or upload an academic document or spreadsheet.");
    return;
  }

  // Check manual overrides first, fallback to AI classification
  const manualDept = document.getElementById("overrideDeptInput")?.value?.trim();
  const manualBatch = document.getElementById("overrideBatchInput")?.value?.trim();
  const manualSem = document.getElementById("overrideSemInput")?.value?.trim();

  const deptToSend = manualDept || (window.currentClassification && window.currentClassification.department);
  const batchToSend = manualBatch || (window.currentClassification && window.currentClassification.batch);
  const semToSend = manualSem || (window.currentClassification && window.currentClassification.semester);

  if (deptToSend) formData.append("department", deptToSend);
  if (batchToSend) formData.append("batch", batchToSend);
  if (semToSend) formData.append("semester", semToSend);

  // Pass Extraction Pipeline choice
  const selectedPipeline = document.querySelector('input[name="extractionPipeline"]:checked')?.value || 'local';
  formData.append("pipeline_mode", selectedPipeline);
  formData.append("cloud_crosscheck", (selectedPipeline === 'cloud' || selectedPipeline === 'visual_blueprint') ? "1" : "0");

  // Reset audit console
  currentAuditLogs = [];
  document.getElementById("auditLogStream").innerHTML = "";
  document.getElementById("auditEmptyMsg").style.display = "block";
  document.getElementById("telemEventCount").textContent = "0";
  document.getElementById("telemStatus").textContent = "STARTING";

  // Reset live streaming data structures
  currentRecords = [];
  filteredRecords = [];
  previousLiveCount = 0;
  currentCatalog = null;
  currentPage = 1;

  // Reset live controls & status badges
  const btnPause = document.getElementById("btnPauseExtraction");
  const btnResume = document.getElementById("btnResumeExtraction");
  const btnStop = document.getElementById("btnStopExtraction");
  const liveStatusBadge = document.getElementById("liveStatusBadge");
  const liveTableBadge = document.getElementById("liveTableBadge");
  const liveTableBadgeText = document.getElementById("liveTableBadgeText");
  const liveStreamPill = document.getElementById("liveStreamPill");
  const liveStreamPillText = document.getElementById("liveStreamPillText");

  if (btnPause) { btnPause.classList.remove("hidden"); btnPause.disabled = false; }
  if (btnResume) { btnResume.classList.add("hidden"); btnResume.disabled = false; }
  if (btnStop) { btnStop.classList.remove("hidden"); btnStop.disabled = false; }
  if (liveStatusBadge) liveStatusBadge.classList.add("hidden");
  if (liveTableBadge) {
    liveTableBadge.classList.add("hidden");
    liveTableBadge.classList.remove("paused");
  }
  if (liveTableBadgeText) liveTableBadgeText.textContent = "LIVE STREAMING";
  if (liveStreamPill) liveStreamPill.classList.add("hidden");
  if (liveStreamPillText) liveStreamPillText.textContent = "Streaming Live";

  // Lock export actions during active streaming
  setExportButtonsLiveState(true);

  extractBtn.disabled = true;
  showProgress("Initializing AI extraction pipeline...", 2);

  const progCurrInit = document.getElementById("progCurrentPage");
  const progProcInit = document.getElementById("progPagesProcessed");
  const progRemInit = document.getElementById("progPagesRemaining");
  const progRecsInit = document.getElementById("progRecordsExtracted");
  const progVerifInit = document.getElementById("progVerificationStatus");
  if (progCurrInit) progCurrInit.textContent = "Initializing...";
  if (progProcInit) progProcInit.textContent = "0 pages processed";
  if (progRemInit) progRemInit.textContent = "Calculating...";
  if (progRecsInit) progRecsInit.textContent = "0 records extracted";
  if (progVerifInit) progVerifInit.textContent = "Standby";

  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      body: formData
    });
    const data = await safeParseJson(res);

    if (!data.success) {
      if (data.duplicate) {
        const replaceConfirm = confirm(
          `Dataset already exists for:\nDepartment: ${data.department}\nBatch: ${data.batch}\nSemester: ${data.semester}\n\nDo you want to REPLACE / OVERWRITE the existing dataset?`
        );
        if (replaceConfirm) {
          formData.append("overwrite", "1");
          formData.append("department", data.department);
          formData.append("batch", data.batch);
          formData.append("semester", data.semester);
          const retryRes = await fetch("/api/extract", {
            method: "POST",
            body: formData
          });
          const retryData = await safeParseJson(retryRes);
          if (retryData.success) {
            if (retryData.dataset_meta) {
              window.currentDatasetMeta = retryData.dataset_meta;
            }
            currentJobId = retryData.job_id;
            pollJob(retryData.job_id);
            return;
          } else {
            alert("Error starting extraction: " + (retryData.error || retryData.message || "Unknown error"));
          }
        } else {
          alert("Extraction cancelled. Existing dataset was kept unchanged.");
        }
      } else {
        alert("Error starting extraction: " + (data.error || data.message || "Unknown error"));
      }
      hideProgress();
      extractBtn.disabled = false;
      document.getElementById("telemStatus").textContent = "STANDBY";
      setExportButtonsLiveState(false);
      return;
    }

    if (data.dataset_meta) {
      window.currentDatasetMeta = data.dataset_meta;
    }
    currentJobId = data.job_id;
    pollJob(data.job_id);
  } catch (e) {
    alert("Network error starting extraction: " + e.message);
    hideProgress();
    extractBtn.disabled = false;
    document.getElementById("telemStatus").textContent = "OFFLINE";
    setExportButtonsLiveState(false);
  }
}

// 4b. Live Extraction Controls (Pause, Resume, Stop)
function initExtractionControls() {
  const btnPause = document.getElementById("btnPauseExtraction");
  const btnResume = document.getElementById("btnResumeExtraction");
  const btnStop = document.getElementById("btnStopExtraction");

  if (btnPause) {
    btnPause.addEventListener("click", async () => {
      if (!currentJobId) return;
      btnPause.disabled = true;
      try {
        const res = await fetch(`/api/jobs/${currentJobId}/pause`, { method: "POST" });
        const data = await res.json();
        if (data.success) {
          btnPause.classList.add("hidden");
          if (btnResume) {
            btnResume.classList.remove("hidden");
            btnResume.disabled = false;
          }
          const liveTableBadge = document.getElementById("liveTableBadge");
          if (liveTableBadge) {
            liveTableBadge.classList.add("paused");
            const txt = document.getElementById("liveTableBadgeText");
            if (txt) txt.textContent = "PAUSED";
          }
          const liveStreamPillText = document.getElementById("liveStreamPillText");
          if (liveStreamPillText) liveStreamPillText.textContent = "Stream Paused";
          const progMsg = document.getElementById("progressMessage");
          if (progMsg) progMsg.textContent = "Extraction paused by user. Inspect the live table or click Resume.";
          const telemStatus = document.getElementById("telemStatus");
          if (telemStatus) {
            telemStatus.textContent = "PAUSED";
            telemStatus.className = "telemetry-val text-warning";
          }
        } else {
          alert("Could not pause extraction: " + (data.error || "Unknown error"));
          btnPause.disabled = false;
        }
      } catch (e) {
        console.error("Pause request error:", e);
        btnPause.disabled = false;
      }
    });
  }

  if (btnResume) {
    btnResume.addEventListener("click", async () => {
      if (!currentJobId) return;
      btnResume.disabled = true;
      try {
        const res = await fetch(`/api/jobs/${currentJobId}/resume`, { method: "POST" });
        const data = await res.json();
        if (data.success) {
          btnResume.classList.add("hidden");
          if (btnPause) {
            btnPause.classList.remove("hidden");
            btnPause.disabled = false;
          }
          const liveTableBadge = document.getElementById("liveTableBadge");
          if (liveTableBadge) {
            liveTableBadge.classList.remove("paused");
            const txt = document.getElementById("liveTableBadgeText");
            if (txt) txt.textContent = "LIVE STREAMING";
          }
          const liveStreamPillText = document.getElementById("liveStreamPillText");
          if (liveStreamPillText) liveStreamPillText.textContent = `Streaming Live (${currentRecords.length} records)`;
          const progMsg = document.getElementById("progressMessage");
          if (progMsg) progMsg.textContent = "Resuming extraction...";
          const telemStatus = document.getElementById("telemStatus");
          if (telemStatus) {
            telemStatus.textContent = "PROCESSING";
            telemStatus.className = "telemetry-val text-success";
          }
        } else {
          alert("Could not resume extraction: " + (data.error || "Unknown error"));
          btnResume.disabled = false;
        }
      } catch (e) {
        console.error("Resume request error:", e);
        btnResume.disabled = false;
      }
    });
  }

  if (btnStop) {
    btnStop.addEventListener("click", async () => {
      if (!currentJobId) return;
      const countMsg = currentRecords.length > 0
        ? `Preserve and finalize the ${currentRecords.length} student records captured so far?`
        : "Stop the extraction process?";
      if (!confirm(`Are you sure you want to stop extraction? ${countMsg}`)) {
        return;
      }
      btnStop.disabled = true;
      if (btnPause) btnPause.disabled = true;
      if (btnResume) btnResume.disabled = true;

      const progMsg = document.getElementById("progressMessage");
      if (progMsg) progMsg.textContent = "Stop requested. Finalizing and saving records to CSV/Excel...";

      const telemStatus = document.getElementById("telemStatus");
      if (telemStatus) {
        telemStatus.textContent = "STOPPING";
        telemStatus.className = "telemetry-val text-warning";
      }

      try {
        const res = await fetch(`/api/jobs/${currentJobId}/stop`, { method: "POST" });
        const data = await res.json();
        if (!data.success) {
          alert("Could not stop extraction: " + (data.error || "Unknown error"));
          btnStop.disabled = false;
          if (btnPause) btnPause.disabled = false;
        }
      } catch (e) {
        console.error("Stop request error:", e);
        btnStop.disabled = false;
        if (btnPause) btnPause.disabled = false;
      }
    });
  }
}

// Helper: Toggle export action button interactivity during live streaming
function setExportButtonsLiveState(isLive) {
  const exportActions = document.querySelector(".export-actions");
  if (!exportActions) return;
  const items = exportActions.querySelectorAll("a, button");
  items.forEach(el => {
    if (isLive) {
      el.style.opacity = "0.45";
      el.style.pointerEvents = "none";
      el.setAttribute("data-live-locked", "true");
    } else {
      el.style.opacity = "";
      el.style.pointerEvents = "";
      el.removeAttribute("data-live-locked");
    }
  });
}

// Helper: Extract consistent, logical column order for data table
function extractRecordKeys(records, catalog) {
  if (!records || records.length === 0) return [];

  const priorityKeys = ["Seat_No", "seat_no", "Name", "name", "Student_Name", "PRN", "prn", "Candidate_Type", "candidate_type", "College_Name", "college_name", "College_Code", "college_code"];
  const summaryKeys = ["Total_Marks", "total_marks", "Percentage", "percentage", "SGPI", "sgpi", "CGPA", "cgpa", "Result_Status", "result_status", "Result", "result", "Remark", "remark"];

  const seen = new Set();
  const ordered = [];

  // 1. Check priority identity keys
  priorityKeys.forEach(pk => {
    if (records.some(r => r[pk] !== undefined) && !seen.has(pk)) {
      seen.add(pk);
      ordered.push(pk);
    }
  });

  // 2. Course marks and grade fields from first 50 records
  const sample = records.slice(0, 50);
  sample.forEach(r => {
    Object.keys(r).forEach(k => {
      if (!seen.has(k) && !summaryKeys.includes(k) && !priorityKeys.includes(k)) {
        seen.add(k);
        ordered.push(k);
      }
    });
  });

  // 3. Summary metrics at end
  summaryKeys.forEach(sk => {
    if (records.some(r => r[sk] !== undefined) && !seen.has(sk)) {
      seen.add(sk);
      ordered.push(sk);
    }
  });

  if (ordered.length === 0) {
    return Object.keys(records[0] || {});
  }

  return ordered;
}

// 4c. Update Data Table Incrementally in Real Time
function updateLiveTable(liveStudents, catalog) {
  if (!Array.isArray(liveStudents) || liveStudents.length === 0) return;

  if (catalog) {
    currentCatalog = catalog;
  }

  const newCount = liveStudents.length;
  const newSeats = new Set();
  if (newCount > previousLiveCount) {
    for (let i = previousLiveCount; i < newCount; i++) {
      const st = liveStudents[i];
      if (st) {
        const sVal = String(st.Seat_No || st.seat_no || `row_${i}`);
        newSeats.add(sVal);
      }
    }
  }
  previousLiveCount = newCount;

  currentRecords = liveStudents;

  // Search filter support during streaming
  const searchInput = document.getElementById("searchInput");
  const term = searchInput ? searchInput.value.toLowerCase().trim() : "";
  if (!term) {
    filteredRecords = [...currentRecords];
  } else {
    filteredRecords = currentRecords.filter(r => {
      return Object.values(r).some(val => String(val).toLowerCase().includes(term));
    });
  }

  // Display table page with soft pulsing highlight on newly added records
  displayTablePage(newSeats);

  // Unhide results section and streaming indicators
  const sec = document.getElementById("resultsSection");
  if (sec) sec.classList.remove("hidden");

  const liveTableBadge = document.getElementById("liveTableBadge");
  if (liveTableBadge) liveTableBadge.classList.remove("hidden");

  const liveStreamPill = document.getElementById("liveStreamPill");
  if (liveStreamPill) liveStreamPill.classList.remove("hidden");

  const auditPassedCount = document.getElementById("auditPassedCount");
  if (auditPassedCount) auditPassedCount.textContent = `${newCount}`;

  const extractedTotalCount = document.getElementById("extractedTotalCount");
  if (extractedTotalCount) extractedTotalCount.textContent = `${newCount}`;

  const liveStreamPillText = document.getElementById("liveStreamPillText");
  if (liveStreamPillText && (!liveTableBadge || !liveTableBadge.classList.contains("paused"))) {
    liveStreamPillText.textContent = `Streaming Live (${newCount} record${newCount === 1 ? '' : 's'})`;
  }

  const liveCountText = document.getElementById("liveCountText");
  if (liveCountText) {
    liveCountText.textContent = `${newCount} record${newCount === 1 ? '' : 's'} fetched live`;
  }
  const liveStatusBadge = document.getElementById("liveStatusBadge");
  if (liveStatusBadge) liveStatusBadge.classList.remove("hidden");

  const resultMeta = document.getElementById("resultMeta");
  if (resultMeta) {
    resultMeta.textContent = `⚡ Live Streaming Active • Fetched ${newCount} student records simultaneously • Updating table live`;
  }
}

// 5. Poll Extraction Progress
function pollJob(jobId) {
  if (pollInterval) clearInterval(pollInterval);
  currentJobId = jobId;
  isPollingActive = false;

  pollInterval = setInterval(async () => {
    if (isPollingActive) return;
    isPollingActive = true;

    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();

      showProgress(job.message || "Extracting records...", job.progress || 0);

      // Real-time extraction progress metrics strip
      const progCurr = document.getElementById("progCurrentPage");
      const progProc = document.getElementById("progPagesProcessed");
      const progRem = document.getElementById("progPagesRemaining");
      const progRecs = document.getElementById("progRecordsExtracted");
      const progVerif = document.getElementById("progVerificationStatus");
      const auditCollapsedStatus = document.getElementById("auditCollapsedStatus");

      if (progCurr) {
        if (job.current_page && job.total_pages) {
          progCurr.textContent = `Page ${job.current_page} / ${job.total_pages}`;
        } else if (job.current_page) {
          progCurr.textContent = `Page ${job.current_page}`;
        } else if (job.message) {
          progCurr.textContent = job.message;
        }
      }
      if (progProc && job.pages_processed != null) {
        progProc.textContent = `${job.pages_processed} page${job.pages_processed === 1 ? '' : 's'} processed`;
      }
      if (progRem && job.pages_remaining != null) {
        progRem.textContent = `${job.pages_remaining} page${job.pages_remaining === 1 ? '' : 's'} remaining`;
      }
      const liveCount = job.live_count || (job.live_students ? job.live_students.length : 0);
      if (progRecs) {
        progRecs.textContent = `${liveCount} record${liveCount === 1 ? '' : 's'} extracted`;
      }
      if (progVerif) {
        if (job.discrepancies && job.discrepancies.length > 0) {
          progVerif.textContent = `${job.discrepancies.length} discrepancy card(s) checked`;
        } else if (job.status === "completed") {
          progVerif.textContent = "Verified & Passed";
        } else {
          progVerif.textContent = "Verification Active";
        }
      }
      if (auditCollapsedStatus) {
        if (job.is_paused) {
          auditCollapsedStatus.textContent = "Status: Paused";
          auditCollapsedStatus.className = "audit-collapsed-chip status-warning";
        } else if (job.is_stopped) {
          auditCollapsedStatus.textContent = "Status: Stopping";
          auditCollapsedStatus.className = "audit-collapsed-chip status-warning";
        } else if (job.status === "completed") {
          auditCollapsedStatus.textContent = "Status: Completed";
          auditCollapsedStatus.className = "audit-collapsed-chip status-ready";
        } else if (job.status === "error") {
          auditCollapsedStatus.textContent = "Status: Failed";
          auditCollapsedStatus.className = "audit-collapsed-chip status-danger";
        } else {
          const pct = Math.round(job.progress || 0);
          auditCollapsedStatus.textContent = `Status: Processing (${pct}%)`;
          auditCollapsedStatus.className = "audit-collapsed-chip status-ready";
        }
      }

      // Handle Live Paused / Resumed button synchronization from backend
      const btnPause = document.getElementById("btnPauseExtraction");
      const btnResume = document.getElementById("btnResumeExtraction");
      const liveTableBadge = document.getElementById("liveTableBadge");
      const liveTableBadgeText = document.getElementById("liveTableBadgeText");
      const liveStreamPillText = document.getElementById("liveStreamPillText");

      if (job.is_paused) {
        if (btnPause) btnPause.classList.add("hidden");
        if (btnResume) {
          btnResume.classList.remove("hidden");
          btnResume.disabled = false;
        }
        if (liveTableBadge) liveTableBadge.classList.add("paused");
        if (liveTableBadgeText) liveTableBadgeText.textContent = "PAUSED";
        if (liveStreamPillText) liveStreamPillText.textContent = "Stream Paused";
      } else if (job.status === "processing" || job.status === "running") {
        if (btnPause && !job.is_stopped) {
          btnPause.classList.remove("hidden");
          btnPause.disabled = false;
        }
        if (btnResume) btnResume.classList.add("hidden");
        if (liveTableBadge) liveTableBadge.classList.remove("paused");
        if (liveTableBadgeText) liveTableBadgeText.textContent = "LIVE STREAMING";
      }

      // Stream live audit logs
      if (job.audit_logs && job.audit_logs.length > 0) {
        updateAuditLogs(job.audit_logs);
      }

      // Stream live student records simultaneously into the table
      if (job.live_students && Array.isArray(job.live_students) && job.live_students.length > 0) {
        updateLiveTable(job.live_students, job.catalog);
      }

      // Stream live in-flight discrepancy cards
      if (job.discrepancies && Array.isArray(job.discrepancies) && job.discrepancies.length > 0) {
        activeAuditReport = {
          total_students: (job.live_students && job.live_students.length) || currentRecords.length,
          verified_patches: job.discrepancies.filter(d => d.sandbox_status === "VERIFIED").length,
          rejected_patches: job.discrepancies.filter(d => d.sandbox_status === "REJECTED").length,
          applied_patches: job.discrepancies.filter(d => d.applied).length,
          discrepancies: job.discrepancies
        };
        const crossBtn = document.getElementById("crossCheckAiBtn");
        if (crossBtn) {
          const discCount = job.discrepancies.length;
          crossBtn.innerHTML = `⚡ View Live AI Audit (${discCount} Healed Item${discCount === 1 ? '' : 's'})`;
          crossBtn.style.background = "linear-gradient(135deg, #059669, #10b981)";
          crossBtn.title = "View discrepancies and proofreading cards verified and self-healed in-flight against source PDF";
        }
      }

      // Stream live token metrics
      if (job.token_usage) {
        const telemTokens = document.getElementById("telemTokens");
        if (telemTokens) {
          const inTok = job.token_usage.input || 0;
          const outTok = job.token_usage.output || 0;
          const totTok = job.token_usage.total || (inTok + outTok);
          telemTokens.textContent = `In: ${inTok.toLocaleString()} | Out: ${outTok.toLocaleString()} (Tot: ${totTok.toLocaleString()})`;
        }
      }

      const telemStatus = document.getElementById("telemStatus");
      if (telemStatus) {
        if (job.is_paused) {
          telemStatus.textContent = "PAUSED";
          telemStatus.className = "telemetry-val text-warning";
        } else if (job.is_stopped) {
          telemStatus.textContent = "FINALIZING";
          telemStatus.className = "telemetry-val text-warning";
        } else {
          telemStatus.textContent = job.status === "completed" ? "COMPLETED" : (job.status === "error" ? "FAILED" : "PROCESSING");
          telemStatus.className = job.status === "completed" ? "telemetry-val text-success" : (job.status === "error" ? "telemetry-val text-danger" : "telemetry-val text-accent");
        }
      }

      if (job.status === "completed") {
        clearInterval(pollInterval);
        pollInterval = null;
        hideProgress();
        setExportButtonsLiveState(false);
        const extractBtn = document.getElementById("extractBtn");
        if (extractBtn) extractBtn.disabled = false;

        // Reset live badges
        if (liveTableBadge) liveTableBadge.classList.add("hidden");
        const liveStreamPill = document.getElementById("liveStreamPill");
        if (liveStreamPill) liveStreamPill.classList.add("hidden");
        const liveStatusBadge = document.getElementById("liveStatusBadge");
        if (liveStatusBadge) liveStatusBadge.classList.add("hidden");

        renderResults(job.result);
        initHistory();

        if (job.result && job.result.stopped_early) {
          appendChatMessage("assistant", `⏹ <strong>Extraction stopped early by user:</strong> ${job.result.total_students} student records were safely captured, verified, and exported to CSV & Excel.`);
        }
      } else if (job.status === "stopped") {
        clearInterval(pollInterval);
        pollInterval = null;
        hideProgress();
        setExportButtonsLiveState(false);
        const extractBtn = document.getElementById("extractBtn");
        if (extractBtn) extractBtn.disabled = false;

        if (liveTableBadge) liveTableBadge.classList.add("hidden");
        const liveStreamPill = document.getElementById("liveStreamPill");
        if (liveStreamPill) liveStreamPill.classList.add("hidden");
        const liveStatusBadge = document.getElementById("liveStatusBadge");
        if (liveStatusBadge) liveStatusBadge.classList.add("hidden");

        alert("Extraction was stopped before records could be captured.");
      } else if (job.status === "error") {
        clearInterval(pollInterval);
        pollInterval = null;
        hideProgress();
        setExportButtonsLiveState(false);
        const extractBtn = document.getElementById("extractBtn");
        if (extractBtn) extractBtn.disabled = false;

        if (liveTableBadge) liveTableBadge.classList.add("hidden");
        const liveStreamPill = document.getElementById("liveStreamPill");
        if (liveStreamPill) liveStreamPill.classList.add("hidden");

        if (currentRecords && currentRecords.length > 0) {
          alert(`Extraction interrupted with error: ${job.error || 'Process failed'}. Preserved ${currentRecords.length} records in table.`);
        } else {
          alert("Extraction Error: " + (job.error || "Process failed"));
        }
      }
    } catch (e) {
      console.error("Polling error:", e);
    } finally {
      isPollingActive = false;
    }
  }, 450);
}

// 5b. Live AI Audit Controls & Renderer
function initAuditControls() {
  const toggleBtn = document.getElementById("btnToggleAuditConsole");
  const auditSec = document.getElementById("aiAuditSection");
  const toggleIcon = document.getElementById("auditToggleBtnIcon");
  const toggleText = document.getElementById("auditToggleBtnText");

  function toggleAuditConsole() {
    if (!auditSec) return;
    const isCollapsed = auditSec.classList.toggle("audit-console-collapsed");
    if (toggleText) toggleText.textContent = isCollapsed ? "Expand" : "Collapse";
    if (toggleIcon) toggleIcon.textContent = isCollapsed ? "▼" : "▲";
  }

  if (toggleBtn) {
    toggleBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleAuditConsole();
    });
  }

  const auditHeader = document.getElementById("auditHeaderClickable");
  if (auditHeader) {
    auditHeader.addEventListener("click", (e) => {
      if (e.target.closest("button") && e.target.closest("button") !== toggleBtn) return;
      toggleAuditConsole();
    });
  }

  const autoScrollBtn = document.getElementById("auditAutoScrollBtn");
  const copyBtn = document.getElementById("auditCopyBtn");
  const clearBtn = document.getElementById("auditClearBtn");

  if (autoScrollBtn) {
    autoScrollBtn.addEventListener("click", () => {
      autoScrollAudit = !autoScrollAudit;
      autoScrollBtn.textContent = autoScrollAudit ? "Scroll: ON" : "Scroll: OFF";
      autoScrollBtn.classList.toggle("active", autoScrollAudit);
    });
  }

  if (copyBtn) {
    copyBtn.addEventListener("click", () => {
      if (currentAuditLogs.length === 0) {
        alert("No audit logs to copy.");
        return;
      }
      const text = JSON.stringify(currentAuditLogs, null, 2);
      navigator.clipboard.writeText(text).then(() => {
        const orig = copyBtn.textContent;
        copyBtn.textContent = "✓ Copied!";
        setTimeout(() => copyBtn.textContent = orig, 2000);
      });
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      currentAuditLogs = [];
      document.getElementById("auditLogStream").innerHTML = "";
      document.getElementById("auditEmptyMsg").style.display = "block";
      document.getElementById("telemEventCount").textContent = "0";
      const telemTokens = document.getElementById("telemTokens");
      if (telemTokens) telemTokens.textContent = "In: 0 | Out: 0 (Tot: 0)";
    });
  }
}

function updateAuditLogs(logs) {
  if (!logs || logs.length === 0) return;
  const stream = document.getElementById("auditLogStream");
  const emptyMsg = document.getElementById("auditEmptyMsg");
  const eventCount = document.getElementById("telemEventCount");

  if (emptyMsg) emptyMsg.style.display = "none";
  if (eventCount) eventCount.textContent = logs.length;

  // Only render new entries
  const startIdx = currentAuditLogs.length;
  currentAuditLogs = logs;

  for (let i = startIdx; i < logs.length; i++) {
    const entry = logs[i];
    const item = document.createElement("div");

    const typeClass = (entry.type || 'system').toLowerCase();
    const rawBadge = entry.badge || (entry.type || 'LOG').toUpperCase();
    const badgeSlug = rawBadge.toLowerCase().replace(/[^a-z0-9]+/g, '_');

    item.className = `audit-entry type-${typeClass} badge-type-${badgeSlug}`;

    // Dynamic Cloud AI Telemetry & Engine Bar Updates
    const telemEngine = document.getElementById("telemEngine");
    const telemStatus = document.getElementById("telemStatus");
    const auditGpuText = document.getElementById("auditGpuText");

    if (entry.badge === "ACTIVE CLOUD MODEL" || entry.badge === "NEW ACTIVE MODEL" || entry.type === "cloud_ai_active") {
      const p = entry.details?.new_provider || entry.details?.provider;
      const m = entry.details?.new_model || entry.details?.model;
      if (p && m) {
        if (telemEngine) telemEngine.textContent = `Cloud AI: ${p.toUpperCase()} (${m})`;
        if (auditGpuText) auditGpuText.textContent = `Cloud AI: ${p.toUpperCase()}`;
      }
      if (telemStatus) {
        telemStatus.textContent = entry.badge === "NEW ACTIVE MODEL" ? "MODEL SWITCHED" : "AI READY";
        telemStatus.className = "telemetry-val text-success";
      }
    } else if (entry.badge === "MODEL FAILOVER" || entry.type === "ai_error") {
      if (telemStatus) {
        telemStatus.textContent = "FAILOVER / RETRY";
        telemStatus.className = "telemetry-val text-warning";
      }
      if (telemEngine && entry.details?.failed_model) {
        telemEngine.textContent = `Failover from ${entry.details.failed_model}...`;
      }
      if (auditGpuText) auditGpuText.textContent = "Failover Active";
    } else if (entry.badge === "CLOUD AI CALL") {
      if (telemStatus) {
        telemStatus.textContent = `INFERENCE (${entry.details?.model || 'Cloud'})`;
        telemStatus.className = "telemetry-val text-accent";
      }
    } else if (entry.badge === "CLOUD AI SUCCESS") {
      if (telemStatus) {
        const sec = entry.details?.duration_sec !== undefined ? `${entry.details.duration_sec}s` : "OK";
        telemStatus.textContent = `RESPONSE (${sec})`;
        telemStatus.className = "telemetry-val text-success";
      }
    } else if (entry.badge === "STAGE 1 SCHEMA") {
      if (telemStatus) {
        telemStatus.textContent = "SCHEMA AUDIT";
        telemStatus.className = "telemetry-val text-accent";
      }
    } else if (entry.badge === "PROOFREADING CHUNK") {
      if (telemStatus) {
        telemStatus.textContent = "PROOFREADING";
        telemStatus.className = "telemetry-val text-accent";
      }
    } else if (entry.badge === "CROSS-CHECK COMPLETE") {
      if (telemStatus) {
        telemStatus.textContent = "AUDIT COMPLETE";
        telemStatus.className = "telemetry-val text-success";
      }
    }

    // Live Token Usage Update
    if (entry.details && entry.details.tokens) {
      const telemTokens = document.getElementById("telemTokens");
      if (telemTokens) {
        const t = entry.details.tokens;
        const inTok = (t.cumulative_input !== undefined ? t.cumulative_input : t.input) || 0;
        const outTok = (t.cumulative_output !== undefined ? t.cumulative_output : t.output) || 0;
        const totTok = (t.cumulative_total !== undefined ? t.cumulative_total : t.total) || (inTok + outTok);
        telemTokens.textContent = `In: ${inTok.toLocaleString()} | Out: ${outTok.toLocaleString()} (Tot: ${totTok.toLocaleString()})`;
      }
    }

    const hasDetails = entry.details !== undefined && entry.details !== null && entry.details !== "";
    const badgeText = rawBadge;

    item.innerHTML = `
      <div class="audit-line-header">
        <span class="audit-time">${entry.timestamp || ''}</span>
        <span class="audit-badge badge-${typeClass} badge-${badgeSlug}">[${badgeText}]</span>
        <span class="audit-title">${escapeHtml(entry.title || '')}</span>
        ${hasDetails ? '<span class="audit-expand-icon">▶</span>' : ''}
      </div>
      ${hasDetails ? `<div class="audit-details-view hidden"><pre>${escapeHtml(typeof entry.details === 'object' ? JSON.stringify(entry.details, null, 2) : String(entry.details))}</pre></div>` : ''}
    `;

    if (hasDetails) {
      item.querySelector(".audit-line-header").addEventListener("click", () => {
        item.classList.toggle("expanded");
        const view = item.querySelector(".audit-details-view");
        view.classList.toggle("hidden");
      });
    }

    stream.appendChild(item);
  }

  if (autoScrollAudit) {
    const terminal = document.getElementById("auditTerminal");
    if (terminal) terminal.scrollTop = terminal.scrollHeight;
  }
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showProgress(message, percentage) {
  const sec = document.getElementById("progressSection");
  sec.classList.remove("hidden");
  document.getElementById("progressMessage").textContent = message;
  document.getElementById("progressPercentage").textContent = `${Math.round(percentage)}%`;
  document.getElementById("progressBar").style.width = `${percentage}%`;
}

function hideProgress() {
  document.getElementById("progressSection").classList.add("hidden");
}

// 6. Render Results Table
function renderResults(result) {
  const sec = document.getElementById("resultsSection");
  sec.classList.remove("hidden");

  // Re-enable and unlock export action buttons
  setExportButtonsLiveState(false);

  // Show interactive Ollama chatbox
  const chatSec = document.getElementById("chatSection");
  if (chatSec) {
    chatSec.classList.remove("hidden");
  }

  currentResultObj = result;
  currentFolderName = result.folder_name || "";
  originalRecords = JSON.parse(JSON.stringify(result.students || []));
  undoSnapshots = [];
  const undoBtn = document.getElementById("btnUndoChat");
  if (undoBtn) undoBtn.disabled = true;

  if (result.dataset_meta) {
    window.currentDatasetMeta = result.dataset_meta;
  }
  const dsMeta = window.currentDatasetMeta || result.dataset_meta;
  const metaBadgeStr = dsMeta ? ` • Dept: ${dsMeta.department} • Batch: ${dsMeta.batch} • Sem: ${dsMeta.semester}` : "";
  const engineInfo = result.engine_used ? `Engine: ${result.engine_used} • ` : "";
  document.getElementById("resultMeta").textContent =
    `${engineInfo}Archetype: ${result.archetype} • Semester: ${result.semester || (dsMeta ? dsMeta.semester : '--')} • Students: ${result.total_students}${metaBadgeStr} • Processed in ${result.duration_seconds}s`;

  const classifiedMetaPill = document.getElementById("classifiedMetaPill");
  const classifiedMetaText = document.getElementById("classifiedMetaText");
  if (classifiedMetaPill && classifiedMetaText) {
    if (dsMeta) {
      classifiedMetaPill.classList.remove("hidden");
      classifiedMetaText.textContent = `${dsMeta.department} | ${dsMeta.batch} | ${dsMeta.semester}`;

      const deptInp = document.getElementById("overrideDeptInput");
      const batchInp = document.getElementById("overrideBatchInput");
      const semInp = document.getElementById("overrideSemInput");
      if (deptInp && dsMeta.department) deptInp.value = dsMeta.department;
      if (batchInp && dsMeta.batch) batchInp.value = dsMeta.batch;
      if (semInp && dsMeta.semester) semInp.value = dsMeta.semester;

      const resDept = document.getElementById("resultsDeptInput");
      const resBatch = document.getElementById("resultsBatchInput");
      const resSem = document.getElementById("resultsSemInput");
      if (resDept && dsMeta.department) resDept.value = dsMeta.department;
      if (resBatch && dsMeta.batch) resBatch.value = dsMeta.batch;
      if (resSem && dsMeta.semester) resSem.value = dsMeta.semester;

      const resultsMetaBadge = document.getElementById("resultsMetaBadge");
      if (resultsMetaBadge) resultsMetaBadge.textContent = "AI Classified";

      const dStr = dsMeta.department || "<Department>";
      const bStr = dsMeta.batch || "<Batch>";
      const sStr = dsMeta.semester || "<Semester>";
      const pdfPathStr = `departments_data/pdf/${dStr}/${bStr}/${sStr}.pdf`;
      const excelPathStr = `departments_data/excel/${dStr}/${bStr}/${sStr}.xlsx`;

      const resultsExcelPreview = document.getElementById("resultsMirrorPathPreview");
      if (resultsExcelPreview) resultsExcelPreview.textContent = excelPathStr;

      const resultsPdfPreview = document.getElementById("resultsPdfMirrorPathPreview");
      if (resultsPdfPreview) resultsPdfPreview.textContent = pdfPathStr;

      const stepPdfPreview = document.getElementById("stepPdfMirrorPreview");
      if (stepPdfPreview) stepPdfPreview.textContent = pdfPathStr;

      const stepExcelPreview = document.getElementById("stepExcelMirrorPreview");
      if (stepExcelPreview) stepExcelPreview.textContent = excelPathStr;
    } else {
      classifiedMetaPill.classList.add("hidden");
    }
  }

  if (result.audit_logs && result.audit_logs.length > 0) {
    updateAuditLogs(result.audit_logs);
  }

  // Download buttons
  const folder = result.folder_name;
  const academicBtn = document.getElementById("btnDownloadAcademicReport");
  if (academicBtn) {
    academicBtn.href = `/api/export/academic-analysis/${encodeURIComponent(folder)}`;
    if (result.academic_report_file) {
      academicBtn.download = result.academic_report_file;
    }
  }
  document.getElementById("btnDownloadCsv").href = `/api/download/${folder}/${result.csv_file}`;
  document.getElementById("btnDownloadExcel").href = `/api/download/${folder}/${result.excel_file}`;
  document.getElementById("btnDownloadJson").href = `/api/download/${folder}/${result.json_file}`;

  // Update Cross-Check button appearance if simultaneous AI cross-checking was completed
  const crossBtn = document.getElementById("btnCrossCheckCloud");
  if (crossBtn) {
    if (result.ai_crosscheck_summary && result.ai_crosscheck_summary.success) {
      const discCount = (result.ai_crosscheck_summary.discrepancies || []).length;
      crossBtn.innerHTML = `📋 View Cloud AI Audit (${discCount} Discrepanc${discCount === 1 ? 'y' : 'ies'})`;
      crossBtn.style.background = discCount > 0 ? "linear-gradient(135deg, #7c3aed, #4f46e5)" : "linear-gradient(135deg, #059669, #10b981)";
      crossBtn.title = "View discrepancies and proofreading cards verified against source PDF";
    } else {
      crossBtn.innerHTML = "🔍 Cross-Check with Cloud AI";
      crossBtn.style.background = "";
      crossBtn.title = "Cross-Check Extracted Records with Cloud AI";
    }
  }

  // Checksum summary pill & extracted data summary strip
  const sum = result.checksum_summary || {};
  const auditPassedCount = document.getElementById("auditPassedCount");
  if (auditPassedCount) {
    auditPassedCount.textContent = `${sum.all_passed != null ? sum.all_passed : result.total_students} / ${result.total_students}`;
  }
  const extractedTotalCount = document.getElementById("extractedTotalCount");
  if (extractedTotalCount) {
    extractedTotalCount.textContent = result.total_students || (result.students ? result.students.length : 0);
  }
  const extractionStatusBadge = document.getElementById("extractionStatusBadge");
  if (extractionStatusBadge) {
    extractionStatusBadge.textContent = "Completed & Verified";
    extractionStatusBadge.className = "badge-status-tag status-success";
  }
  const auditCollapsedStatus = document.getElementById("auditCollapsedStatus");
  if (auditCollapsedStatus) {
    auditCollapsedStatus.textContent = "Status: Completed";
    auditCollapsedStatus.className = "audit-collapsed-chip status-ready";
  }

  // Token usage summary pill & telemetry update
  const tokenPill = document.getElementById("tokenPill");
  const tokenPillText = document.getElementById("tokenPillText");
  if (result.token_usage && (result.token_usage.total > 0 || result.token_usage.input > 0)) {
    const inTok = result.token_usage.input || 0;
    const outTok = result.token_usage.output || 0;
    const totTok = result.token_usage.total || (inTok + outTok);
    if (tokenPill && tokenPillText) {
      tokenPillText.textContent = `Tokens: ${inTok.toLocaleString()} in / ${outTok.toLocaleString()} out (${totTok.toLocaleString()} tot)`;
      tokenPill.classList.remove("hidden");
    }
    const telemTokens = document.getElementById("telemTokens");
    if (telemTokens) {
      telemTokens.textContent = `In: ${inTok.toLocaleString()} | Out: ${outTok.toLocaleString()} (Tot: ${totTok.toLocaleString()})`;
    }
  } else if (tokenPill) {
    tokenPill.classList.add("hidden");
  }

  currentRecords = result.students || [];
  activeColumns = extractRecordKeys(currentRecords, currentCatalog);
  selectedRowIndices.clear();
  dirtyCells.clear();
  excelUndoStack = [];
  excelRedoStack = [];
  columnFilters = {};
  currentSort = { colKey: null, dir: null };
  unsavedEditsCount = 0;
  updateExcelSaveStatus();
  updateExcelUndoRedoButtons();
  filteredRecords = [...currentRecords];
  currentPage = 1;
  populateFindColSelect();
  displayTablePage();

  currentEdaData = null;
  const viewAnalytics = document.getElementById("tabViewAnalytics");
  if (viewAnalytics && !viewAnalytics.classList.contains("hidden")) {
    computeEdaDashboard();
  }

  sec.scrollIntoView({ behavior: "smooth" });
}

// ==========================================================================
// Interactive Excel Spreadsheet Engine
// ==========================================================================

function applyFiltersAndSearch(resetPage = true) {
  const searchInput = document.getElementById("searchInput");
  const term = (searchInput ? searchInput.value : "").toLowerCase().trim();

  let records = [...currentRecords];

  // 1. Text Search Filter across all fields
  if (term) {
    records = records.filter(r => {
      return Object.values(r).some(val => String(val).toLowerCase().includes(term));
    });
  }

  // 2. Active Column-Specific Filters
  Object.keys(columnFilters).forEach(colKey => {
    const allowed = columnFilters[colKey];
    if (allowed && allowed.size > 0) {
      records = records.filter(r => {
        const val = String(r[colKey] !== undefined && r[colKey] !== null ? r[colKey] : "");
        return allowed.has(val);
      });
    }
  });

  // 3. Sorting
  if (currentSort.colKey && currentSort.dir) {
    const k = currentSort.colKey;
    const isAsc = currentSort.dir === "asc";
    records.sort((a, b) => {
      const valA = a[k] !== undefined && a[k] !== null ? a[k] : "";
      const valB = b[k] !== undefined && b[k] !== null ? b[k] : "";
      const numA = parseFloat(valA);
      const numB = parseFloat(valB);
      if (!isNaN(numA) && !isNaN(numB)) {
        return isAsc ? numA - numB : numB - numA;
      }
      return isAsc ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
    });
  }

  filteredRecords = records;
  if (resetPage) {
    currentPage = 1;
  }
  displayTablePage();
}

function displayTablePage(highlightSeats = null) {
  const tableHeadRow = document.getElementById("tableHeadRow");
  const tableBody = document.getElementById("tableBody");
  if (!tableHeadRow || !tableBody) return;

  tableHeadRow.innerHTML = "";
  tableBody.innerHTML = "";

  if (filteredRecords.length === 0) {
    tableBody.innerHTML = `<tr><td colspan="12" style="text-align: center; padding: 2rem; color: var(--text-muted);">No student records to display. Click "➕ Add Row" or clear filters.</td></tr>`;
    const pagInfo = document.getElementById("paginationInfo");
    if (pagInfo) pagInfo.textContent = "0 records";
    updateExcelStatusBarStats(null);
    return;
  }

  // Derive column order
  const keys = (activeColumns && activeColumns.length > 0)
    ? activeColumns
    : extractRecordKeys(filteredRecords.length > 0 ? filteredRecords : currentRecords, currentCatalog);
  activeColumns = [...keys];

  // 1. Render Table Header
  // Top-left row index / select-all checkbox
  const thNum = document.createElement("th");
  thNum.className = "excel-col-num";
  thNum.innerHTML = `<input type="checkbox" id="excelSelectAllRows" class="excel-row-check" title="Select All Rows on Page">`;
  tableHeadRow.appendChild(thNum);

  keys.forEach(k => {
    const th = document.createElement("th");
    th.className = "excel-th";
    th.dataset.colKey = k;

    const isSorted = currentSort.colKey === k;
    const sortIcon = isSorted ? (currentSort.dir === "asc" ? "▲" : "▼") : "";
    const isFiltered = columnFilters[k] && columnFilters[k].size > 0;

    th.innerHTML = `
      <div class="excel-th-inner">
        <span class="excel-th-label" title="Click to sort">
          ${k}
          <span class="excel-sort-icon ${isSorted ? 'active' : ''}">${sortIcon}</span>
          ${isFiltered ? '<span style="color: #38bdf8; font-size: 0.7rem;" title="Filter active">●</span>' : ''}
        </span>
        <button type="button" class="excel-th-menu-btn" data-col-menu="${k}" title="Column Options">⋮</button>
      </div>
    `;

    // Click label to sort
    const labelSpan = th.querySelector(".excel-th-label");
    if (labelSpan) {
      labelSpan.addEventListener("click", () => {
        if (currentSort.colKey === k) {
          if (currentSort.dir === "asc") currentSort.dir = "desc";
          else if (currentSort.dir === "desc") { currentSort.colKey = null; currentSort.dir = null; }
        } else {
          currentSort.colKey = k;
          currentSort.dir = "asc";
        }
        applyFiltersAndSearch(false);
      });
    }

    // Click menu button
    const menuBtn = th.querySelector(".excel-th-menu-btn");
    if (menuBtn) {
      menuBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openColumnMenu(k, e.clientX, e.clientY);
      });
    }

    tableHeadRow.appendChild(th);
  });

  // Select-All checkbox logic
  const selectAllCb = thNum.querySelector("#excelSelectAllRows");
  if (selectAllCb) {
    selectAllCb.addEventListener("click", (e) => e.stopPropagation());
    selectAllCb.addEventListener("mousedown", (e) => e.stopPropagation());
    selectAllCb.addEventListener("change", (e) => {
      e.stopPropagation();
      const checked = e.target.checked;
      const startIdx = (currentPage - 1) * pageSize;
      const endIdx = Math.min(startIdx + pageSize, filteredRecords.length);
      for (let i = startIdx; i < endIdx; i++) {
        if (checked) selectedRowIndices.add(i);
        else selectedRowIndices.delete(i);
      }
      displayTablePage();
      updateSelectedRowsState();
    });
  }

  // 2. Render Page Rows
  const maxPage = Math.max(1, Math.ceil(filteredRecords.length / pageSize));
  if (currentPage > maxPage) {
    currentPage = maxPage;
  }
  const startIdx = (currentPage - 1) * pageSize;
  const pageSlice = filteredRecords.slice(startIdx, startIdx + pageSize);

  pageSlice.forEach((row, sliceIdx) => {
    const absIdx = startIdx + sliceIdx;
    const tr = document.createElement("tr");
    if (selectedRowIndices.has(absIdx)) {
      tr.classList.add("excel-row-selected");
    }

    const seatVal = String(row["Seat_No"] || row["seat_no"] || "");
    if (highlightSeats && seatVal && highlightSeats.has(seatVal)) {
      tr.classList.add("live-row-highlight");
    }

    // Row Header (#)
    const tdNum = document.createElement("td");
    tdNum.className = "excel-row-num";
    tdNum.dataset.rowIdx = absIdx;
    tdNum.innerHTML = `
      <div class="excel-row-header-content">
        <input type="checkbox" class="excel-row-check" data-row-idx="${absIdx}" ${selectedRowIndices.has(absIdx) ? "checked" : ""}>
        <span>${absIdx + 1}</span>
      </div>
      <div class="excel-row-hover-actions">
        <button type="button" class="excel-row-action-btn" title="Insert row below" data-row-action="insert-below" data-row-idx="${absIdx}">➕</button>
        <button type="button" class="excel-row-action-btn" title="Duplicate row" data-row-action="duplicate" data-row-idx="${absIdx}">📄</button>
        <button type="button" class="excel-row-action-btn" title="Delete row" data-row-action="delete" data-row-idx="${absIdx}" style="color:#f87171;">🗑️</button>
      </div>
    `;

    // Row checkbox change
    const rowCb = tdNum.querySelector(".excel-row-check");
    if (rowCb) {
      rowCb.addEventListener("click", (e) => e.stopPropagation());
      rowCb.addEventListener("mousedown", (e) => e.stopPropagation());
      rowCb.addEventListener("change", (e) => {
        e.stopPropagation();
        if (e.target.checked) selectedRowIndices.add(absIdx);
        else selectedRowIndices.delete(absIdx);
        if (selectedRowIndices.has(absIdx)) tr.classList.add("excel-row-selected");
        else tr.classList.remove("excel-row-selected");
        updateSelectedRowsState();
      });
    }

    // Row hover action buttons
    const btnIns = tdNum.querySelector('[data-row-action="insert-below"]');
    if (btnIns) {
      btnIns.addEventListener("click", (e) => {
        e.stopPropagation();
        insertRowAt(absIdx + 1);
      });
    }
    const btnDup = tdNum.querySelector('[data-row-action="duplicate"]');
    if (btnDup) {
      btnDup.addEventListener("click", (e) => {
        e.stopPropagation();
        duplicateRow(absIdx);
      });
    }
    const btnDel = tdNum.querySelector('[data-row-action="delete"]');
    if (btnDel) {
      btnDel.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteSingleRow(absIdx);
      });
    }

    tr.appendChild(tdNum);

    // Data Cells
    keys.forEach(k => {
      const td = document.createElement("td");
      td.dataset.rowIdx = absIdx;
      td.dataset.colKey = k;

      const rawVal = row[k] !== undefined && row[k] !== null ? row[k] : "";
      td.textContent = rawVal;

      if (dirtyCells.has(absIdx + "_" + k)) {
        td.classList.add("cell-dirty");
      }
      if (activeCell && activeCell.rowIndex === absIdx && activeCell.colKey === k) {
        td.classList.add("excel-cell-selected");
      }
      if (currentFindMatches.some(m => m.rowIndex === absIdx && m.colKey === k)) {
        td.classList.add("search-matched-cell");
      }

      // Single click selects cell
      td.addEventListener("click", (e) => {
        e.stopPropagation();
        selectCell(absIdx, k);
      });

      // Double click begins inline editing
      td.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        startCellEdit(absIdx, k);
      });

      // Right-click context menu
      td.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        selectCell(absIdx, k);
        openContextMenu(absIdx, k, e.clientX, e.clientY);
      });

      tr.appendChild(td);
    });

    tableBody.appendChild(tr);
  });

  // 3. Update Pagination
  const total = filteredRecords.length;
  const endIdx = Math.min(startIdx + pageSize, total);
  const pagInfo = document.getElementById("paginationInfo");
  if (pagInfo) {
    pagInfo.textContent = `Showing records ${startIdx + 1} - ${endIdx} of ${total}`;
  }

  const prevBtn = document.getElementById("prevPageBtn");
  const nextBtn = document.getElementById("nextPageBtn");
  if (prevBtn) prevBtn.disabled = currentPage <= 1;
  if (nextBtn) nextBtn.disabled = endIdx >= total;

  updateSelectedRowsState();
  if (activeCell) {
    updateExcelStatusBarStats(activeCell.colKey);
  }
}

// 7. Search & Pagination Controls
function initSearch() {
  const searchInput = document.getElementById("searchInput");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      applyFiltersAndSearch(true);
    });
  }

  const prevBtn = document.getElementById("prevPageBtn");
  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (currentPage > 1) {
        currentPage--;
        displayTablePage();
      }
    });
  }

  const nextBtn = document.getElementById("nextPageBtn");
  if (nextBtn) {
    nextBtn.addEventListener("click", () => {
      if (currentPage * pageSize < filteredRecords.length) {
        currentPage++;
        displayTablePage();
      }
    });
  }

  const sizeSelect = document.getElementById("pageSizeSelect");
  if (sizeSelect) {
    sizeSelect.addEventListener("change", (e) => {
      pageSize = parseInt(e.target.value, 10) || 25;
      currentPage = 1;
      displayTablePage();
    });
  }
}

// ==========================================================================
// Excel Cell Selection, Keyboard Navigation & Inline Editing
// ==========================================================================

function findExcelCellTd(rowIndex, colKey) {
  if (rowIndex == null || colKey == null) return null;
  try {
    const escapedCol = (typeof CSS !== "undefined" && CSS.escape) ? CSS.escape(String(colKey)) : String(colKey).replace(/["\\]/g, "\\$&");
    const el = document.querySelector(`.excel-data-table td[data-row-idx="${rowIndex}"][data-col-key="${escapedCol}"]`);
    if (el) return el;
  } catch (e) {}

  const tds = document.querySelectorAll(`.excel-data-table td[data-row-idx="${rowIndex}"]`);
  for (let i = 0; i < tds.length; i++) {
    if (tds[i].getAttribute("data-col-key") === String(colKey)) {
      return tds[i];
    }
  }
  return null;
}

function selectCell(rowIndex, colKey) {
  if (editingCell) {
    commitCellEdit();
  }

  activeCell = { rowIndex, colKey };

  // Remove previous selection styling
  document.querySelectorAll(".excel-data-table td.excel-cell-selected").forEach(td => {
    td.classList.remove("excel-cell-selected");
  });

  // Apply selection highlight safely
  const targetTd = findExcelCellTd(rowIndex, colKey);
  if (targetTd) {
    targetTd.classList.add("excel-cell-selected");
    targetTd.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  // Update active coordinates badge
  const coordsBadge = document.getElementById("excelActiveCellCoords");
  if (coordsBadge) {
    const val = (filteredRecords[rowIndex] && filteredRecords[rowIndex][colKey] !== undefined)
      ? String(filteredRecords[rowIndex][colKey])
      : "";
    coordsBadge.textContent = `Row ${rowIndex + 1}, Col "${colKey}" [${val.substring(0, 15)}]`;
  }

  // Live Formula / Excel Stats Bar computation
  updateExcelStatusBarStats(colKey);
}

function updateExcelStatusBarStats(colKey) {
  if (!colKey || filteredRecords.length === 0) {
    const setStat = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setStat("valStatCount", "0");
    setStat("valStatSum", "0.00");
    setStat("valStatAvg", "0.00");
    setStat("valStatMin", "0.00");
    setStat("valStatMax", "0.00");
    return;
  }

  let count = 0;
  let numCount = 0;
  let sum = 0;
  let min = Infinity;
  let max = -Infinity;

  filteredRecords.forEach(r => {
    const raw = r[colKey];
    if (raw !== undefined && raw !== null && String(raw).trim() !== "") {
      count++;
      const num = parseFloat(String(raw).replace(/[^\d.-]/g, ""));
      if (!isNaN(num) && isFinite(num)) {
        numCount++;
        sum += num;
        if (num < min) min = num;
        if (num > max) max = num;
      }
    }
  });

  const avg = numCount > 0 ? sum / numCount : 0;
  const setStat = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

  setStat("valStatCount", count.toString());
  setStat("valStatSum", numCount > 0 ? (sum % 1 === 0 ? sum.toString() : sum.toFixed(2)) : "N/A");
  setStat("valStatAvg", numCount > 0 ? avg.toFixed(2) : "N/A");
  setStat("valStatMin", numCount > 0 ? (min % 1 === 0 ? min.toString() : min.toFixed(2)) : "N/A");
  setStat("valStatMax", numCount > 0 ? (max % 1 === 0 ? max.toString() : max.toFixed(2)) : "N/A");
}

function startCellEdit(rowIndex, colKey, initialChar = null) {
  if (editingCell) {
    commitCellEdit();
  }

  const td = document.querySelector(`.excel-data-table td[data-row-idx="${rowIndex}"][data-col-key="${colKey}"]`);
  if (!td) return;

  const currentVal = (filteredRecords[rowIndex] && filteredRecords[rowIndex][colKey] !== undefined)
    ? String(filteredRecords[rowIndex][colKey])
    : "";

  editingCell = { rowIndex, colKey, originalVal: currentVal, tdElement: td };

  td.classList.add("excel-cell-editing");
  td.innerHTML = "";

  const input = document.createElement("input");
  input.type = "text";
  input.className = "excel-cell-editor";
  input.value = initialChar !== null ? initialChar : currentVal;
  td.appendChild(input);

  input.focus();
  if (initialChar === null) {
    input.select();
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commitCellEdit();
      // Move down or up
      const nextRow = e.shiftKey ? Math.max(0, rowIndex - 1) : Math.min(filteredRecords.length - 1, rowIndex + 1);
      selectCell(nextRow, colKey);
    } else if (e.key === "Tab") {
      e.preventDefault();
      commitCellEdit();
      // Move right or left
      const colIdx = activeColumns.indexOf(colKey);
      if (e.shiftKey) {
        if (colIdx > 0) selectCell(rowIndex, activeColumns[colIdx - 1]);
      } else {
        if (colIdx < activeColumns.length - 1) selectCell(rowIndex, activeColumns[colIdx + 1]);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelCellEdit();
    }
  });

  input.addEventListener("blur", () => {
    if (editingCell) {
      commitCellEdit();
    }
  });
}

function commitCellEdit() {
  if (!editingCell) return;
  const { rowIndex, colKey, originalVal, tdElement } = editingCell;
  const input = tdElement.querySelector(".excel-cell-editor");
  const newVal = input ? input.value.trim() : originalVal;

  editingCell = null;
  tdElement.classList.remove("excel-cell-editing");
  tdElement.textContent = newVal;

  if (newVal !== originalVal) {
    pushExcelSnapshot();
    // Update record
    if (filteredRecords[rowIndex]) {
      filteredRecords[rowIndex][colKey] = newVal;
    }
    dirtyCells.add(rowIndex + "_" + colKey);
    tdElement.classList.add("cell-dirty");

    unsavedEditsCount++;
    updateExcelSaveStatus();
    updateExcelStatusBarStats(colKey);
  }
}

function cancelCellEdit() {
  if (!editingCell) return;
  const { originalVal, tdElement } = editingCell;
  editingCell = null;
  tdElement.classList.remove("excel-cell-editing");
  tdElement.textContent = originalVal;
}

// ==========================================================================
// Undo / Redo & Save System
// ==========================================================================

function pushExcelSnapshot() {
  const snapshot = {
    records: JSON.parse(JSON.stringify(currentRecords)),
    columns: [...activeColumns]
  };
  excelUndoStack.push(snapshot);
  if (excelUndoStack.length > 50) {
    excelUndoStack.shift();
  }
  excelRedoStack = [];
  updateExcelUndoRedoButtons();
}

function excelUndo() {
  if (excelUndoStack.length === 0) return;
  if (editingCell) commitCellEdit();

  const currentSnapshot = {
    records: JSON.parse(JSON.stringify(currentRecords)),
    columns: [...activeColumns]
  };
  excelRedoStack.push(currentSnapshot);

  const prev = excelUndoStack.pop();
  currentRecords = prev.records;
  activeColumns = prev.columns;

  unsavedEditsCount = Math.max(0, unsavedEditsCount - 1);
  updateExcelSaveStatus();
  updateExcelUndoRedoButtons();
  applyFiltersAndSearch(false);
  updateMetaCount(currentRecords.length);
}

function excelRedo() {
  if (excelRedoStack.length === 0) return;
  if (editingCell) commitCellEdit();

  const currentSnapshot = {
    records: JSON.parse(JSON.stringify(currentRecords)),
    columns: [...activeColumns]
  };
  excelUndoStack.push(currentSnapshot);

  const next = excelRedoStack.pop();
  currentRecords = next.records;
  activeColumns = next.columns;

  unsavedEditsCount++;
  updateExcelSaveStatus();
  updateExcelUndoRedoButtons();
  applyFiltersAndSearch(false);
  updateMetaCount(currentRecords.length);
}

function updateExcelUndoRedoButtons() {
  const btnUndo = document.getElementById("btnExcelUndo");
  const btnRedo = document.getElementById("btnExcelRedo");
  if (btnUndo) btnUndo.disabled = excelUndoStack.length === 0;
  if (btnRedo) btnRedo.disabled = excelRedoStack.length === 0;
}

function updateExcelSaveStatus() {
  const dot = document.getElementById("excelSaveDot");
  const text = document.getElementById("excelSaveStatusText");
  if (!dot || !text) return;

  if (unsavedEditsCount > 0) {
    dot.className = "status-dot dot-unsaved";
    text.textContent = `${unsavedEditsCount} unsaved edit${unsavedEditsCount === 1 ? '' : 's'}`;
  } else {
    dot.className = "status-dot dot-synced";
    text.textContent = "All changes saved";
  }
}

// Synchronize classification metadata between Step 1 and Extracted Data inputs
function syncDatasetMetadata(source) {
  const stepDept = document.getElementById("overrideDeptInput");
  const stepBatch = document.getElementById("overrideBatchInput");
  const stepSem = document.getElementById("overrideSemInput");

  const resDept = document.getElementById("resultsDeptInput");
  const resBatch = document.getElementById("resultsBatchInput");
  const resSem = document.getElementById("resultsSemInput");

  let deptVal = "";
  let batchVal = "";
  let semVal = "";

  if (source === "step") {
    deptVal = stepDept?.value || "";
    batchVal = stepBatch?.value?.trim() || "";
    semVal = stepSem?.value || "";

    if (resDept && deptVal) resDept.value = deptVal;
    if (resBatch && batchVal) resBatch.value = batchVal;
    if (resSem && semVal) resSem.value = semVal;
  } else if (source === "results") {
    deptVal = resDept?.value || "";
    batchVal = resBatch?.value?.trim() || "";
    semVal = resSem?.value || "";

    if (stepDept && deptVal) stepDept.value = deptVal;
    if (stepBatch && batchVal) stepBatch.value = batchVal;
    if (stepSem && semVal) stepSem.value = semVal;
  } else {
    // Read from results if available, else step
    deptVal = resDept?.value || stepDept?.value || "";
    batchVal = resBatch?.value?.trim() || stepBatch?.value?.trim() || "";
    semVal = resSem?.value || stepSem?.value || "";
  }

  // Update target storage preview (both PDF and Excel mirrors)
  const dStr = deptVal || "<Department>";
  const bStr = batchVal || "<Batch>";
  const sStr = semVal || "<Semester>";
  const pdfPathStr = `departments_data/pdf/${dStr}/${bStr}/${sStr}.pdf`;
  const excelPathStr = `departments_data/excel/${dStr}/${bStr}/${sStr}.xlsx`;

  const resultsExcelPreview = document.getElementById("resultsMirrorPathPreview");
  if (resultsExcelPreview) resultsExcelPreview.textContent = excelPathStr;

  const resultsPdfPreview = document.getElementById("resultsPdfMirrorPathPreview");
  if (resultsPdfPreview) resultsPdfPreview.textContent = pdfPathStr;

  const stepPdfPreview = document.getElementById("stepPdfMirrorPreview");
  if (stepPdfPreview) stepPdfPreview.textContent = pdfPathStr;

  const stepExcelPreview = document.getElementById("stepExcelMirrorPreview");
  if (stepExcelPreview) stepExcelPreview.textContent = excelPathStr;

  // Update active meta badge
  const resultsMetaBadge = document.getElementById("resultsMetaBadge");
  const stepMetaBadge = document.getElementById("metaOverrideStatusBadge");
  if (source === "step" || source === "results") {
    if (resultsMetaBadge) resultsMetaBadge.textContent = "Manually Specified";
    if (stepMetaBadge) stepMetaBadge.textContent = "Manual Override";
  }

  // Persist into window.currentDatasetMeta
  if (deptVal || batchVal || semVal) {
    window.currentDatasetMeta = {
      ...(window.currentDatasetMeta || {}),
      department: deptVal || (window.currentDatasetMeta && window.currentDatasetMeta.department) || "Artificial_Intelligence_and_Data_Science",
      batch: batchVal || (window.currentDatasetMeta && window.currentDatasetMeta.batch) || "22-23",
      semester: semVal || (window.currentDatasetMeta && window.currentDatasetMeta.semester) || "Semester_1",
      pdf_path: pdfPathStr,
      excel_path: excelPathStr
    };
  }
}

async function saveDatasetToServer() {
  if (!currentRecords || currentRecords.length === 0) {
    alert("No records to save.");
    return;
  }

  const saveBtn = document.getElementById("btnExcelSave");
  const saveDbBtn = document.getElementById("btnSaveToDatabase");
  const origText = saveBtn ? saveBtn.innerHTML : "";
  const origDbText = saveDbBtn ? saveDbBtn.innerHTML : "";
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.innerHTML = `<span>⏳ Saving...</span>`;
  }
  if (saveDbBtn) {
    saveDbBtn.disabled = true;
    saveDbBtn.innerHTML = `<span>⏳ Saving...</span>`;
  }

  try {
    // Read directly from results inputs first, fallback to step 1 override inputs
    const manualDept = (document.getElementById("resultsDeptInput")?.value || document.getElementById("overrideDeptInput")?.value)?.trim();
    const manualBatch = (document.getElementById("resultsBatchInput")?.value || document.getElementById("overrideBatchInput")?.value)?.trim();
    const manualSem = (document.getElementById("resultsSemInput")?.value || document.getElementById("overrideSemInput")?.value)?.trim();

    // Validate hyphenated batch format if user provided a batch
    if (manualBatch && !/^\d{2}-\d{2}$/.test(manualBatch)) {
      alert(`Invalid batch format: "${manualBatch}".\nBatch must follow the YY-YY format (e.g. 21-22, 22-23).`);
      if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = origText; }
      if (saveDbBtn) { saveDbBtn.disabled = false; saveDbBtn.innerHTML = origDbText; }
      return;
    }

    let metaToPersist = window.currentDatasetMeta || (currentResultObj && currentResultObj.dataset_meta) || {};
    if (manualDept || manualBatch || manualSem) {
      metaToPersist = {
        ...metaToPersist,
        department: manualDept || metaToPersist.department || "Artificial_Intelligence_and_Data_Science",
        batch: manualBatch || metaToPersist.batch || "22-23",
        semester: manualSem || metaToPersist.semester || "Semester_1"
      };
      window.currentDatasetMeta = metaToPersist;
    }

    const res = await fetch("/api/dataset/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder_name: currentFolderName || "live_edited_results",
        students: currentRecords,
        columns: activeColumns,
        catalog: currentCatalog,
        dataset_meta: metaToPersist
      })
    });
    const data = await res.json();

    if (!data.success) {
      alert("Save failed: " + (data.error || "Unknown server error"));
      return;
    }

    unsavedEditsCount = 0;
    dirtyCells.clear();
    updateExcelSaveStatus();
    displayTablePage();

    // Re-enable download links with fresh data
    const folder = data.folder_name;
    if (folder) {
      currentFolderName = folder;
      const csvBtn = document.getElementById("btnDownloadCsv");
      if (csvBtn) csvBtn.href = `/api/download/${folder}/${data.csv_file}`;
      const xlsxBtn = document.getElementById("btnDownloadExcel");
      if (xlsxBtn) xlsxBtn.href = `/api/download/${folder}/${data.excel_file}`;
      const jsonBtn = document.getElementById("btnDownloadJson");
      if (jsonBtn) jsonBtn.href = `/api/download/${folder}/${data.json_file}`;
      const academicBtn = document.getElementById("btnDownloadAcademicReport");
      if (academicBtn) academicBtn.href = `/api/export/academic-analysis/${encodeURIComponent(folder)}`;
    }

    // Inform user cleanly
    const badge = document.getElementById("excelSaveStatus");
    if (badge) {
      badge.style.transform = "scale(1.08)";
      setTimeout(() => { badge.style.transform = "scale(1)"; }, 300);
    }
    if (data.db_saved) {
      appendChatMessage("assistant", `💾 <strong>Dataset Saved to Database:</strong> Persisted ${data.count} student records to SQLite database (<code>academic_records.db</code>) and generated deterministic Excel mirror at <code>${data.canonical_excel_path || data.excel_file}</code>.`);
    }

    // Prominent alert confirming successful database and mirror persistence
    const savedDept = metaToPersist.department || "Artificial_Intelligence_and_Data_Science";
    const savedBatch = metaToPersist.batch || "22-23";
    const savedSem = metaToPersist.semester || "Semester_1";
    const mirrorPath = data.canonical_excel_path || `departments_data/excel/${savedDept}/${savedBatch}/${savedSem}.xlsx`;
    const pdfMirrorPath = data.canonical_pdf_path || `departments_data/pdf/${savedDept}/${savedBatch}/${savedSem}.pdf`;
    const recordCount = data.count || currentRecords.length;

    // Refresh batch catalog so newly saved PDF and Excel appear immediately in batch selection
    if (typeof initBatches === "function") {
      initBatches();
    }

    alert(
      `✅ Dataset Successfully Saved to Database!\n\n` +
      `• Department: ${savedDept}\n` +
      `• Batch: ${savedBatch}\n` +
      `• Semester: ${savedSem}\n` +
      `• Records Persisted: ${recordCount} students\n` +
      `• Database: SQLite (academic_records.db)\n` +
      `• PDF Storage: ${pdfMirrorPath}\n` +
      `• Excel Mirror: ${mirrorPath}`
    );

  } catch (err) {
    console.error("Error saving dataset:", err);
    alert("Network error occurred while saving: " + err.message);
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.innerHTML = origText;
    }
    if (saveDbBtn) {
      saveDbBtn.disabled = false;
      saveDbBtn.innerHTML = origDbText;
    }
  }
}

// ==========================================================================
// Row & Column Operations (Add, Delete, Rename, Duplicate)
// ==========================================================================

function insertRowAt(targetIndex, initialData = null) {
  pushExcelSnapshot();

  const newRow = {};
  activeColumns.forEach(k => {
    newRow[k] = "";
  });

  if (initialData) {
    Object.assign(newRow, initialData);
  } else {
    // Generate next smart seat number
    let maxSeat = 522000;
    currentRecords.forEach(r => {
      const s = parseInt(r["Seat_No"] || r["seat_no"], 10);
      if (!isNaN(s) && s > maxSeat) maxSeat = s;
    });
    newRow["Seat_No"] = String(maxSeat + 1);
    newRow["Name"] = "New Student";
    newRow["Result"] = "Successful";
  }

  const insertPos = Math.max(0, Math.min(targetIndex, currentRecords.length));
  currentRecords.splice(insertPos, 0, newRow);

  unsavedEditsCount++;
  updateExcelSaveStatus();
  updateMetaCount(currentRecords.length);
  applyFiltersAndSearch(false);

  // Jump page to newly inserted row and select it
  currentPage = Math.ceil((insertPos + 1) / pageSize);
  displayTablePage();
  selectCell(insertPos, activeColumns[0] || "Seat_No");
}

function duplicateRow(rowIndex) {
  const target = filteredRecords[rowIndex];
  if (!target) return;

  pushExcelSnapshot();
  const cloned = JSON.parse(JSON.stringify(target));

  // Modify seat or name to avoid exact duplicate collision
  const originalSeat = parseInt(cloned["Seat_No"] || cloned["seat_no"], 10);
  if (!isNaN(originalSeat)) {
    cloned["Seat_No"] = String(originalSeat + 1);
  }
  if (cloned["Name"]) {
    cloned["Name"] = cloned["Name"] + " (Copy)";
  }

  const originalIdx = currentRecords.indexOf(target);
  const insertPos = originalIdx >= 0 ? originalIdx + 1 : currentRecords.length;
  currentRecords.splice(insertPos, 0, cloned);

  unsavedEditsCount++;
  updateExcelSaveStatus();
  updateMetaCount(currentRecords.length);
  applyFiltersAndSearch(false);
  selectCell(insertPos, activeColumns[0] || "Seat_No");
}

function deleteSingleRow(rowIndex) {
  const target = filteredRecords[rowIndex];
  if (!target) return;

  pushExcelSnapshot();
  const origIdx = currentRecords.indexOf(target);
  if (origIdx >= 0) {
    currentRecords.splice(origIdx, 1);
  }
  selectedRowIndices.delete(rowIndex);

  unsavedEditsCount++;
  updateExcelSaveStatus();
  updateMetaCount(currentRecords.length);
  applyFiltersAndSearch(false);
}

function deleteSelectedRows() {
  if (selectedRowIndices.size === 0) return;

  if (selectedRowIndices.size > 1) {
    if (!confirm(`Are you sure you want to delete ${selectedRowIndices.size} selected records?`)) {
      return;
    }
  }

  pushExcelSnapshot();
  const targets = new Set();
  selectedRowIndices.forEach(idx => {
    if (filteredRecords[idx]) {
      targets.add(filteredRecords[idx]);
    }
  });

  currentRecords = currentRecords.filter(r => !targets.has(r));
  selectedRowIndices.clear();

  unsavedEditsCount++;
  updateExcelSaveStatus();
  updateMetaCount(currentRecords.length);
  applyFiltersAndSearch(false);
}

function updateSelectedRowsState() {
  const btnDel = document.getElementById("btnExcelDeleteRows");
  const badge = document.getElementById("excelSelectedBadge");
  const count = selectedRowIndices.size;

  if (btnDel) {
    btnDel.disabled = count === 0;
  }
  if (badge) {
    if (count > 0) {
      badge.textContent = count.toString();
      badge.classList.remove("hidden");
    } else {
      badge.classList.add("hidden");
    }
  }
}

function addColumn(colName, defaultVal = "", position = "end") {
  const cleanName = colName.trim();
  if (!cleanName) {
    alert("Column name cannot be empty.");
    return;
  }
  if (activeColumns.includes(cleanName)) {
    alert(`Column "${cleanName}" already exists.`);
    return;
  }

  pushExcelSnapshot();

  if (position === "after_active" && activeCell && activeCell.colKey) {
    const idx = activeColumns.indexOf(activeCell.colKey);
    activeColumns.splice(idx + 1, 0, cleanName);
  } else {
    activeColumns.push(cleanName);
  }

  // Populate default value across all current records
  currentRecords.forEach(r => {
    r[cleanName] = defaultVal;
  });

  unsavedEditsCount++;
  updateExcelSaveStatus();
  populateFindColSelect();
  displayTablePage();
}

function deleteColumn(colKey) {
  if (!colKey) return;
  if (!confirm(`Delete column "${colKey}" across all student records? This cannot be undone without Undo.`)) {
    return;
  }

  pushExcelSnapshot();
  activeColumns = activeColumns.filter(c => c !== colKey);
  currentRecords.forEach(r => {
    delete r[colKey];
  });

  if (activeCell && activeCell.colKey === colKey) {
    activeCell = null;
  }

  unsavedEditsCount++;
  updateExcelSaveStatus();
  populateFindColSelect();
  displayTablePage();
}

function renameColumn(oldKey, newKey) {
  const cleanNew = newKey.trim();
  if (!cleanNew) {
    alert("New column name cannot be empty.");
    return;
  }
  if (cleanNew === oldKey) return;
  if (activeColumns.includes(cleanNew)) {
    alert(`Column "${cleanNew}" already exists.`);
    return;
  }

  pushExcelSnapshot();

  const idx = activeColumns.indexOf(oldKey);
  if (idx >= 0) {
    activeColumns[idx] = cleanNew;
  }

  currentRecords.forEach(r => {
    if (r[oldKey] !== undefined) {
      r[cleanNew] = r[oldKey];
      delete r[oldKey];
    }
  });

  if (activeCell && activeCell.colKey === oldKey) {
    activeCell.colKey = cleanNew;
  }

  unsavedEditsCount++;
  updateExcelSaveStatus();
  populateFindColSelect();
  displayTablePage();
}

function clearColumn(colKey) {
  if (!colKey) return;
  if (!confirm(`Clear all values in column "${colKey}"?`)) return;

  pushExcelSnapshot();
  currentRecords.forEach(r => {
    r[colKey] = "";
  });

  unsavedEditsCount++;
  updateExcelSaveStatus();
  displayTablePage();
}

// ==========================================================================
// Find & Replace Drawer Implementation
// ==========================================================================

function populateFindColSelect() {
  const select = document.getElementById("findColSelect");
  if (!select) return;
  const currentVal = select.value;
  select.innerHTML = `<option value="_all_">All Columns</option>`;
  activeColumns.forEach(k => {
    const opt = document.createElement("option");
    opt.value = k;
    opt.textContent = k;
    select.appendChild(opt);
  });
  if (activeColumns.includes(currentVal)) {
    select.value = currentVal;
  }
}

function toggleFindReplace(forceShow = null) {
  const bar = document.getElementById("excelFindReplaceBar");
  if (!bar) return;
  const isHidden = bar.classList.contains("hidden");
  const shouldShow = forceShow !== null ? forceShow : isHidden;

  if (shouldShow) {
    bar.classList.remove("hidden");
    populateFindColSelect();
    const findInp = document.getElementById("findInput");
    if (findInp) {
      findInp.focus();
      findInp.select();
    }
  } else {
    bar.classList.add("hidden");
    currentFindMatches = [];
    currentFindMatchIdx = -1;
    displayTablePage();
  }
}

function performFind(direction = "next") {
  const findInp = document.getElementById("findInput");
  const query = findInp ? findInp.value : "";
  if (!query) {
    currentFindMatches = [];
    currentFindMatchIdx = -1;
    updateFindMatchBadge();
    displayTablePage();
    return;
  }

  const colSelect = document.getElementById("findColSelect");
  const targetCol = colSelect ? colSelect.value : "_all_";
  const matchCase = document.getElementById("findMatchCase") ? document.getElementById("findMatchCase").checked : false;

  const matches = [];
  const searchStr = matchCase ? query : query.toLowerCase();

  const colsToSearch = targetCol === "_all_" ? activeColumns : [targetCol];

  filteredRecords.forEach((row, rowIdx) => {
    colsToSearch.forEach(colKey => {
      const val = row[colKey] !== undefined && row[colKey] !== null ? String(row[colKey]) : "";
      const testVal = matchCase ? val : val.toLowerCase();
      if (testVal.includes(searchStr)) {
        matches.push({ rowIndex: rowIdx, colKey });
      }
    });
  });

  currentFindMatches = matches;

  if (matches.length === 0) {
    currentFindMatchIdx = -1;
    updateFindMatchBadge();
    displayTablePage();
    return;
  }

  if (direction === "next") {
    currentFindMatchIdx = (currentFindMatchIdx + 1) % matches.length;
  } else {
    currentFindMatchIdx = (currentFindMatchIdx - 1 + matches.length) % matches.length;
  }

  updateFindMatchBadge();

  const match = matches[currentFindMatchIdx];
  // Ensure the page with match is active
  currentPage = Math.ceil((match.rowIndex + 1) / pageSize);
  displayTablePage();
  selectCell(match.rowIndex, match.colKey);
}

function updateFindMatchBadge() {
  const badge = document.getElementById("findMatchCount");
  if (!badge) return;
  if (currentFindMatches.length === 0) {
    badge.textContent = "0 matches";
  } else {
    badge.textContent = `${currentFindMatchIdx + 1} of ${currentFindMatches.length} matches`;
  }
}

function replaceCurrentMatch() {
  if (currentFindMatches.length === 0 || currentFindMatchIdx < 0) {
    performFind("next");
    if (currentFindMatches.length === 0) return;
  }

  const match = currentFindMatches[currentFindMatchIdx];
  const replaceInp = document.getElementById("replaceInput");
  const findInp = document.getElementById("findInput");
  const repText = replaceInp ? replaceInp.value : "";
  const findText = findInp ? findInp.value : "";

  const record = filteredRecords[match.rowIndex];
  if (!record) return;

  pushExcelSnapshot();

  const curVal = String(record[match.colKey] || "");
  const matchCase = document.getElementById("findMatchCase") ? document.getElementById("findMatchCase").checked : false;

  let updatedVal;
  if (matchCase) {
    updatedVal = curVal.replace(findText, repText);
  } else {
    const reg = new RegExp(findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
    updatedVal = curVal.replace(reg, repText);
  }

  record[match.colKey] = updatedVal;
  dirtyCells.add(match.rowIndex + "_" + match.colKey);
  unsavedEditsCount++;
  updateExcelSaveStatus();

  performFind("next");
}

function replaceAllMatches() {
  const findInp = document.getElementById("findInput");
  const replaceInp = document.getElementById("replaceInput");
  const findText = findInp ? findInp.value : "";
  const repText = replaceInp ? replaceInp.value : "";

  if (!findText) return;

  const colSelect = document.getElementById("findColSelect");
  const targetCol = colSelect ? colSelect.value : "_all_";
  const matchCase = document.getElementById("findMatchCase") ? document.getElementById("findMatchCase").checked : false;
  const colsToSearch = targetCol === "_all_" ? activeColumns : [targetCol];

  let replaceCount = 0;
  pushExcelSnapshot();

  const reg = new RegExp(findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), matchCase ? 'g' : 'gi');

  currentRecords.forEach((row, rowIdx) => {
    colsToSearch.forEach(colKey => {
      const curVal = String(row[colKey] || "");
      if (reg.test(curVal)) {
        row[colKey] = curVal.replace(reg, repText);
        dirtyCells.add(rowIdx + "_" + colKey);
        replaceCount++;
      }
    });
  });

  if (replaceCount > 0) {
    unsavedEditsCount += replaceCount;
    updateExcelSaveStatus();
    applyFiltersAndSearch(false);
    alert(`Successfully replaced ${replaceCount} occurrences.`);
  } else {
    alert("No matches found to replace.");
  }
}

// ==========================================================================
// Context Menus & Column Operations
// ==========================================================================

function openContextMenu(rowIndex, colKey, clientX, clientY) {
  contextMenuTarget = { rowIndex, colKey };
  closeColumnMenu();

  const menu = document.getElementById("excelContextMenu");
  if (!menu) return;

  menu.style.left = `${Math.min(window.innerWidth - 210, clientX)}px`;
  menu.style.top = `${Math.min(window.innerHeight - 260, clientY)}px`;
  menu.classList.remove("hidden");
}

function closeContextMenu() {
  const menu = document.getElementById("excelContextMenu");
  if (menu) menu.classList.add("hidden");
}

function openColumnMenu(colKey, clientX, clientY) {
  contextMenuTarget = { rowIndex: null, colKey };
  closeContextMenu();

  const menu = document.getElementById("excelColMenu");
  if (!menu) return;

  menu.style.left = `${Math.min(window.innerWidth - 230, clientX)}px`;
  menu.style.top = `${Math.min(window.innerHeight - 280, clientY)}px`;
  menu.classList.remove("hidden");
}

function closeColumnMenu() {
  const menu = document.getElementById("excelColMenu");
  if (menu) menu.classList.add("hidden");
}

// ==========================================================================
// Filter Modal Engine
// ==========================================================================

let filterModalSelectedValues = new Set();
let filterModalTargetCol = null;

function openFilterModal(colKey) {
  filterModalTargetCol = colKey;
  const modal = document.getElementById("excelFilterModal");
  const title = document.getElementById("filterModalTitle");
  const container = document.getElementById("filterValuesList");
  const searchInp = document.getElementById("filterSearchInput");

  if (!modal || !container) return;

  title.textContent = `Filter "${colKey}"`;
  if (searchInp) searchInp.value = "";

  // Collect distinct values & frequencies
  const valCounts = {};
  currentRecords.forEach(r => {
    const v = String(r[colKey] !== undefined && r[colKey] !== null ? r[colKey] : "(Blank)");
    valCounts[v] = (valCounts[v] || 0) + 1;
  });

  const sortedVals = Object.keys(valCounts).sort();

  // If column already filtered, pre-select those, otherwise pre-select all
  if (columnFilters[colKey] && columnFilters[colKey].size > 0) {
    filterModalSelectedValues = new Set(columnFilters[colKey]);
  } else {
    filterModalSelectedValues = new Set(sortedVals);
  }

  renderFilterModalList(sortedVals, valCounts, "");

  if (searchInp) {
    searchInp.oninput = (e) => {
      const q = e.target.value.toLowerCase().trim();
      renderFilterModalList(sortedVals, valCounts, q);
    };
  }

  modal.classList.remove("hidden");
}

function renderFilterModalList(sortedVals, valCounts, query) {
  const container = document.getElementById("filterValuesList");
  if (!container) return;
  container.innerHTML = "";

  const filtered = sortedVals.filter(v => v.toLowerCase().includes(query));

  if (filtered.length === 0) {
    container.innerHTML = `<div style="color: var(--text-muted); padding: 0.5rem; text-align: center;">No matching values</div>`;
    return;
  }

  filtered.forEach(val => {
    const count = valCounts[val];
    const row = document.createElement("label");
    row.style.display = "flex";
    row.style.alignItems = "center";
    row.style.gap = "0.5rem";
    row.style.padding = "0.25rem 0.2rem";
    row.style.fontSize = "0.8rem";
    row.style.cursor = "pointer";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "excel-row-check";
    cb.checked = filterModalSelectedValues.has(val);
    cb.addEventListener("change", (e) => {
      if (e.target.checked) filterModalSelectedValues.add(val);
      else filterModalSelectedValues.delete(val);
    });

    const textSpan = document.createElement("span");
    textSpan.textContent = `${val} (${count})`;
    textSpan.style.flex = "1";

    row.appendChild(cb);
    row.appendChild(textSpan);
    container.appendChild(row);
  });
}

// ==========================================================================
// Initialization of All Interactive Excel Features
// ==========================================================================

function initInteractiveExcelGrid() {
  // Global click closes context menus
  document.addEventListener("click", () => {
    closeContextMenu();
    closeColumnMenu();
  });

  // Table Container Keyboard Listener (Excel Navigation & Shortcuts)
  const container = document.getElementById("excelTableContainer");
  if (container) {
    container.addEventListener("keydown", (e) => {
      if (editingCell) return; // Managed by cell editor
      if (!activeCell) return;

      const { rowIndex, colKey } = activeCell;
      const colIdx = activeColumns.indexOf(colKey);

      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (rowIndex > 0) selectCell(rowIndex - 1, colKey);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        if (rowIndex < filteredRecords.length - 1) selectCell(rowIndex + 1, colKey);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        if (colIdx > 0) selectCell(rowIndex, activeColumns[colIdx - 1]);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        if (colIdx < activeColumns.length - 1) selectCell(rowIndex, activeColumns[colIdx + 1]);
      } else if (e.key === "Enter" || e.key === "F2") {
        e.preventDefault();
        startCellEdit(rowIndex, colKey);
      } else if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        pushExcelSnapshot();
        if (filteredRecords[rowIndex]) {
          filteredRecords[rowIndex][colKey] = "";
        }
        dirtyCells.add(rowIndex + "_" + colKey);
        unsavedEditsCount++;
        updateExcelSaveStatus();
        displayTablePage();
        selectCell(rowIndex, colKey);
      } else if (e.ctrlKey && e.key.toLowerCase() === "c") {
        const val = filteredRecords[rowIndex] ? filteredRecords[rowIndex][colKey] : "";
        if (navigator.clipboard) {
          navigator.clipboard.writeText(String(val || ""));
        }
      } else if (e.ctrlKey && e.key.toLowerCase() === "v") {
        if (navigator.clipboard) {
          navigator.clipboard.readText().then(text => {
            pushExcelSnapshot();
            filteredRecords[rowIndex][colKey] = text;
            dirtyCells.add(rowIndex + "_" + colKey);
            unsavedEditsCount++;
            updateExcelSaveStatus();
            displayTablePage();
            selectCell(rowIndex, colKey);
          });
        }
      } else if (!e.ctrlKey && !e.altKey && !e.metaKey && e.key.length === 1) {
        // Printable character starts edit directly!
        startCellEdit(rowIndex, colKey, e.key);
      }
    });
  }

  // Global Keydown (Ctrl+Z, Ctrl+Y, Ctrl+S, Ctrl+F)
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "z") {
      e.preventDefault();
      excelUndo();
    } else if (e.ctrlKey && (e.key.toLowerCase() === "y" || (e.shiftKey && e.key.toLowerCase() === "z"))) {
      e.preventDefault();
      excelRedo();
    } else if (e.ctrlKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveDatasetToServer();
    } else if (e.ctrlKey && e.key.toLowerCase() === "f") {
      e.preventDefault();
      toggleFindReplace();
    }
  });

  // Ribbon Action Buttons
  const btnAddRow = document.getElementById("btnExcelAddRow");
  if (btnAddRow) {
    btnAddRow.addEventListener("click", () => {
      const modal = document.getElementById("excelAddRowModal");
      if (modal) modal.classList.remove("hidden");
    });
  }

  const btnAddCol = document.getElementById("btnExcelAddCol");
  if (btnAddCol) {
    btnAddCol.addEventListener("click", () => {
      const modal = document.getElementById("excelAddColModal");
      if (modal) modal.classList.remove("hidden");
    });
  }

  const btnDelRows = document.getElementById("btnExcelDeleteRows");
  if (btnDelRows) {
    btnDelRows.addEventListener("click", deleteSelectedRows);
  }

  const btnUndo = document.getElementById("btnExcelUndo");
  if (btnUndo) {
    btnUndo.addEventListener("click", excelUndo);
  }

  const btnRedo = document.getElementById("btnExcelRedo");
  if (btnRedo) {
    btnRedo.addEventListener("click", excelRedo);
  }

  const btnAiFix = document.getElementById("btnExcelAiFix");
  if (btnAiFix) {
    btnAiFix.addEventListener("click", async () => {
      if (!currentRecords || currentRecords.length === 0) {
        showToast("No active records to fix.", "error");
        return;
      }
      const origHtml = btnAiFix.innerHTML;
      btnAiFix.disabled = true;
      btnAiFix.innerHTML = '<span>⏳ Fixing with AI...</span>';
      pushExcelSnapshot();
      try {
        const resp = await fetch("/api/tabular/interactive-fix", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            records: currentRecords,
            folder_name: currentFolderName || ""
          })
        });
        const data = await resp.json();
        if (data.success && data.records) {
          currentRecords = data.records;
          activeColumns = extractRecordKeys(currentRecords, currentCatalog);
          markExcelDirty();
          applyFiltersAndSearch(true);
          const chgCount = data.changes_count || 0;
          showToast(`🪄 AI Fix Complete! ${chgCount} cell(s) repaired & reconciled.`, "success");
        } else {
          showToast(data.error || "AI Fix encountered an issue.", "error");
        }
      } catch (err) {
        showToast("Network error during AI Fix: " + err.message, "error");
      } finally {
        btnAiFix.disabled = false;
        btnAiFix.innerHTML = origHtml;
      }
    });
  }

  const btnToggleFR = document.getElementById("btnToggleFindReplace");
  if (btnToggleFR) {
    btnToggleFR.addEventListener("click", () => toggleFindReplace());
  }

  const btnSave = document.getElementById("btnExcelSave");
  if (btnSave) {
    btnSave.addEventListener("click", saveDatasetToServer);
  }

  const btnSaveDb = document.getElementById("btnSaveToDatabase");
  if (btnSaveDb) {
    btnSaveDb.addEventListener("click", saveDatasetToServer);
  }

  // Toggle Manual Metadata Override Drawer
  const btnToggleMeta = document.getElementById("btnToggleMetaOverride");
  const metaDrawer = document.getElementById("metaOverrideDrawer");
  if (btnToggleMeta && metaDrawer) {
    btnToggleMeta.addEventListener("click", () => {
      metaDrawer.classList.toggle("hidden");
    });
  }

  // Step 1 Override input listeners -> sync to results inputs & mirror preview
  ["overrideDeptInput", "overrideBatchInput", "overrideSemInput"].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("input", () => syncDatasetMetadata("step"));
      el.addEventListener("change", () => syncDatasetMetadata("step"));
    }
  });

  // Results Section Metadata input listeners -> sync to Step 1 & mirror preview
  ["resultsDeptInput", "resultsBatchInput", "resultsSemInput"].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener("input", () => syncDatasetMetadata("results"));
      el.addEventListener("change", () => syncDatasetMetadata("results"));
    }
  });

  // Export dropdown menu toggle
  const exportToggle = document.getElementById("btnExportMenuToggle");
  const exportMenu = document.getElementById("exportDropdownMenu");
  if (exportToggle && exportMenu) {
    exportToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      exportMenu.classList.toggle("hidden");
    });
    document.addEventListener("click", (e) => {
      if (!exportToggle.contains(e.target) && !exportMenu.contains(e.target)) {
        exportMenu.classList.add("hidden");
      }
    });
  }

  const btnReset = document.getElementById("btnExcelReset");
  if (btnReset) {
    btnReset.addEventListener("click", () => {
      if (originalRecords.length === 0) return;
      if (!confirm("Reset dataset back to original extracted state? All unsaved manual edits will be reverted.")) {
        return;
      }
      pushExcelSnapshot();
      currentRecords = JSON.parse(JSON.stringify(originalRecords));
      activeColumns = extractRecordKeys(currentRecords, currentCatalog);
      dirtyCells.clear();
      selectedRowIndices.clear();
      unsavedEditsCount = 0;
      updateExcelSaveStatus();
      applyFiltersAndSearch(true);
      updateMetaCount(currentRecords.length);
    });
  }

  const btnHelp = document.getElementById("btnExcelShortcutsHelp");
  if (btnHelp) {
    btnHelp.addEventListener("click", () => {
      const modal = document.getElementById("excelShortcutsModal");
      if (modal) modal.classList.remove("hidden");
    });
  }

  // Find & Replace Buttons
  const btnFindNext = document.getElementById("btnFindNext");
  if (btnFindNext) btnFindNext.addEventListener("click", () => performFind("next"));

  const btnFindPrev = document.getElementById("btnFindPrev");
  if (btnFindPrev) btnFindPrev.addEventListener("click", () => performFind("prev"));

  const btnRepOne = document.getElementById("btnReplaceOne");
  if (btnRepOne) btnRepOne.addEventListener("click", replaceCurrentMatch);

  const btnRepAll = document.getElementById("btnReplaceAll");
  if (btnRepAll) btnRepAll.addEventListener("click", replaceAllMatches);

  const btnCloseFR = document.getElementById("btnCloseFindReplace");
  if (btnCloseFR) btnCloseFR.addEventListener("click", () => toggleFindReplace(false));

  // Context Menu Item Actions
  const ctxMenu = document.getElementById("excelContextMenu");
  if (ctxMenu) {
    ctxMenu.addEventListener("click", (e) => {
      const item = e.target.closest(".ctx-item");
      if (!item) return;
      const action = item.dataset.action;
      const { rowIndex, colKey } = contextMenuTarget;

      if (action === "edit_cell" && rowIndex !== null && colKey) {
        startCellEdit(rowIndex, colKey);
      } else if (action === "copy_cell" && rowIndex !== null && colKey) {
        const val = filteredRecords[rowIndex] ? filteredRecords[rowIndex][colKey] : "";
        if (navigator.clipboard) navigator.clipboard.writeText(String(val || ""));
      } else if (action === "clear_cell" && rowIndex !== null && colKey) {
        pushExcelSnapshot();
        if (filteredRecords[rowIndex]) filteredRecords[rowIndex][colKey] = "";
        dirtyCells.add(rowIndex + "_" + colKey);
        unsavedEditsCount++;
        updateExcelSaveStatus();
        displayTablePage();
      } else if (action === "insert_row_above" && rowIndex !== null) {
        insertRowAt(rowIndex);
      } else if (action === "insert_row_below" && rowIndex !== null) {
        insertRowAt(rowIndex + 1);
      } else if (action === "duplicate_row" && rowIndex !== null) {
        duplicateRow(rowIndex);
      } else if (action === "delete_row" && rowIndex !== null) {
        deleteSingleRow(rowIndex);
      } else if (action === "add_column") {
        const modal = document.getElementById("excelAddColModal");
        if (modal) modal.classList.remove("hidden");
      } else if (action === "rename_column" && colKey) {
        openRenameColModal(colKey);
      } else if (action === "delete_column" && colKey) {
        deleteColumn(colKey);
      }
    });
  }

  // Column Menu Item Actions
  const colMenu = document.getElementById("excelColMenu");
  if (colMenu) {
    colMenu.addEventListener("click", (e) => {
      const item = e.target.closest(".ctx-item");
      if (!item) return;
      const action = item.dataset.colAction;
      const colKey = contextMenuTarget.colKey;
      if (!colKey) return;

      if (action === "sort_asc") {
        currentSort = { colKey, dir: "asc" };
        applyFiltersAndSearch(false);
      } else if (action === "sort_desc") {
        currentSort = { colKey, dir: "desc" };
        applyFiltersAndSearch(false);
      } else if (action === "filter_col") {
        openFilterModal(colKey);
      } else if (action === "add_col_right") {
        const modal = document.getElementById("excelAddColModal");
        if (modal) {
          const pos = document.getElementById("newColPositionSelect");
          if (pos) pos.value = "after_active";
          activeCell = { rowIndex: 0, colKey };
          modal.classList.remove("hidden");
        }
      } else if (action === "rename_col") {
        openRenameColModal(colKey);
      } else if (action === "clear_col") {
        clearColumn(colKey);
      } else if (action === "delete_col") {
        deleteColumn(colKey);
      }
    });
  }

  // Add Column Modal Handlers
  const btnCloseAddCol = document.getElementById("btnCloseAddColModal");
  const btnCancelAddCol = document.getElementById("btnCancelAddCol");
  const btnSubmitAddCol = document.getElementById("btnSubmitAddCol");
  const modalAddCol = document.getElementById("excelAddColModal");

  [btnCloseAddCol, btnCancelAddCol].forEach(btn => {
    if (btn) btn.addEventListener("click", () => modalAddCol.classList.add("hidden"));
  });

  if (btnSubmitAddCol) {
    btnSubmitAddCol.addEventListener("click", () => {
      const nameInp = document.getElementById("newColNameInput");
      const defInp = document.getElementById("newColDefaultValInput");
      const posSelect = document.getElementById("newColPositionSelect");
      const name = nameInp ? nameInp.value : "";
      const defVal = defInp ? defInp.value : "";
      const pos = posSelect ? posSelect.value : "end";

      if (!name.trim()) {
        alert("Please enter a column name.");
        return;
      }
      addColumn(name, defVal, pos);
      modalAddCol.classList.add("hidden");
      if (nameInp) nameInp.value = "";
      if (defInp) defInp.value = "";
    });
  }

  // Add Row Modal Handlers
  const btnCloseAddRow = document.getElementById("btnCloseAddRowModal");
  const btnCancelAddRow = document.getElementById("btnCancelAddRow");
  const btnSubmitAddRow = document.getElementById("btnSubmitAddRow");
  const modalAddRow = document.getElementById("excelAddRowModal");

  [btnCloseAddRow, btnCancelAddRow].forEach(btn => {
    if (btn) btn.addEventListener("click", () => modalAddRow.classList.add("hidden"));
  });

  if (btnSubmitAddRow) {
    btnSubmitAddRow.addEventListener("click", () => {
      const seat = document.getElementById("addRowSeat").value.trim();
      const name = document.getElementById("addRowName").value.trim();
      const prn = document.getElementById("addRowPrn").value.trim();
      const candType = document.getElementById("addRowCandidateType").value;
      const sgpi = document.getElementById("addRowSgpi").value.trim();
      const resVal = document.getElementById("addRowResult").value;
      const pos = document.getElementById("addRowPositionSelect").value;

      if (!seat || !name) {
        alert("Seat Number and Student Name are required.");
        return;
      }

      const rowObj = {
        Seat_No: seat,
        Name: name,
        PRN: prn,
        Candidate_Type: candType,
        SGPI: sgpi,
        Result: resVal
      };

      let insertIdx = currentRecords.length;
      if (pos === "beginning") insertIdx = 0;
      else if (pos === "after_selected" && activeCell) insertIdx = activeCell.rowIndex + 1;

      insertRowAt(insertIdx, rowObj);
      modalAddRow.classList.add("hidden");

      // Reset form
      document.getElementById("addRowSeat").value = "";
      document.getElementById("addRowName").value = "";
      document.getElementById("addRowPrn").value = "";
      document.getElementById("addRowSgpi").value = "";
    });
  }

  // Rename Column Modal Handlers
  const btnCloseRename = document.getElementById("btnCloseRenameColModal");
  const btnCancelRename = document.getElementById("btnCancelRenameCol");
  const btnSubmitRename = document.getElementById("btnSubmitRenameCol");
  const modalRename = document.getElementById("excelRenameColModal");

  [btnCloseRename, btnCancelRename].forEach(btn => {
    if (btn) btn.addEventListener("click", () => modalRename.classList.add("hidden"));
  });

  if (btnSubmitRename) {
    btnSubmitRename.addEventListener("click", () => {
      const oldKey = document.getElementById("renameColOldKey").value;
      const newKey = document.getElementById("renameColNewInput").value.trim();
      if (!newKey) {
        alert("New column name cannot be empty.");
        return;
      }
      renameColumn(oldKey, newKey);
      modalRename.classList.add("hidden");
    });
  }

  // Filter Modal Handlers
  const btnCloseFilter = document.getElementById("btnCloseFilterModal");
  const btnCancelFilter = document.getElementById("btnCancelFilterModal");
  const btnApplyFilter = document.getElementById("btnApplyFilterModal");
  const btnClearFilter = document.getElementById("btnClearCurrentColFilter");
  const modalFilter = document.getElementById("excelFilterModal");

  [btnCloseFilter, btnCancelFilter].forEach(btn => {
    if (btn) btn.addEventListener("click", () => modalFilter.classList.add("hidden"));
  });

  const linkSelectAll = document.getElementById("filterSelectAllLink");
  if (linkSelectAll) {
    linkSelectAll.addEventListener("click", (e) => {
      e.preventDefault();
      document.querySelectorAll("#filterValuesList input[type='checkbox']").forEach(cb => {
        cb.checked = true;
      });
      // Add all
      currentRecords.forEach(r => {
        const v = String(r[filterModalTargetCol] !== undefined && r[filterModalTargetCol] !== null ? r[filterModalTargetCol] : "(Blank)");
        filterModalSelectedValues.add(v);
      });
    });
  }

  const linkClearAll = document.getElementById("filterClearAllLink");
  if (linkClearAll) {
    linkClearAll.addEventListener("click", (e) => {
      e.preventDefault();
      document.querySelectorAll("#filterValuesList input[type='checkbox']").forEach(cb => {
        cb.checked = false;
      });
      filterModalSelectedValues.clear();
    });
  }

  if (btnApplyFilter) {
    btnApplyFilter.addEventListener("click", () => {
      if (filterModalTargetCol) {
        columnFilters[filterModalTargetCol] = new Set(filterModalSelectedValues);
        applyFiltersAndSearch(true);
      }
      modalFilter.classList.add("hidden");
    });
  }

  if (btnClearFilter) {
    btnClearFilter.addEventListener("click", () => {
      if (filterModalTargetCol) {
        delete columnFilters[filterModalTargetCol];
        applyFiltersAndSearch(true);
      }
      modalFilter.classList.add("hidden");
    });
  }

  // Shortcuts Guide Modal Handlers
  const btnDismissShortcuts = document.getElementById("btnDismissShortcutsModal");
  const btnCloseShortcuts = document.getElementById("btnCloseShortcutsModal");
  const modalShortcuts = document.getElementById("excelShortcutsModal");

  [btnDismissShortcuts, btnCloseShortcuts].forEach(btn => {
    if (btn) btn.addEventListener("click", () => modalShortcuts.classList.add("hidden"));
  });
}

function openRenameColModal(colKey) {
  const modal = document.getElementById("excelRenameColModal");
  const oldLabel = document.getElementById("renameColOldLabel");
  const oldKeyInp = document.getElementById("renameColOldKey");
  const newKeyInp = document.getElementById("renameColNewInput");

  if (!modal) return;
  oldLabel.textContent = colKey;
  oldKeyInp.value = colKey;
  newKeyInp.value = colKey;
  modal.classList.remove("hidden");
  newKeyInp.focus();
  newKeyInp.select();
}

// 8. History Drawer
let cachedHistoryItems = [];

async function initHistory() {
  const list = document.getElementById("historyList");
  const searchInput = document.getElementById("historySearchInput");
  const refreshBtn = document.getElementById("refreshHistoryBtn");

  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    cachedHistoryItems = data.history || [];
    renderHistoryList(cachedHistoryItems);
  } catch (e) {
    console.error("Failed to fetch history:", e);
  }

  if (refreshBtn && !refreshBtn.dataset.bound) {
    refreshBtn.dataset.bound = "true";
    refreshBtn.addEventListener("click", initHistory);
  }

  if (searchInput && !searchInput.dataset.bound) {
    searchInput.dataset.bound = "true";
    searchInput.addEventListener("input", (e) => {
      const q = (e.target.value || "").toLowerCase().trim();
      if (!q) {
        renderHistoryList(cachedHistoryItems);
        return;
      }
      const filtered = cachedHistoryItems.filter(item => {
        const title = (item.folder_name || "").toLowerCase();
        const arch = (item.archetype || "").toLowerCase();
        const sem = String(item.semester || "").toLowerCase();
        const count = String(item.total_students || "").toLowerCase();
        return title.includes(q) || arch.includes(q) || sem.includes(q) || count.includes(q);
      });
      renderHistoryList(filtered);
    });
  }
}

function renderHistoryList(items) {
  const list = document.getElementById("historyList");
  if (!list) return;

  if (!items || items.length === 0) {
    list.innerHTML = `<p class="empty-text">No previous extractions found.</p>`;
    return;
  }

  list.innerHTML = "";
  items.forEach(item => {
    const card = document.createElement("div");
    card.className = "history-item";
    card.innerHTML = `
      <div class="history-title">${item.folder_name}</div>
      <div class="history-meta">Archetype: ${item.archetype} • Sem: ${item.semester} • ${item.total_students} records${item.timestamp ? ' • ' + item.timestamp : ''}</div>
    `;
    card.addEventListener("click", () => loadHistoryItem(item.folder_name));
    list.appendChild(card);
  });
}

async function loadHistoryItem(folderName) {
  try {
    showProgress(`Loading archive '${folderName}'...`, 50);
    const res = await fetch(`/api/history/${folderName}`);
    const data = await res.json();
    hideProgress();

    if (!data.success) {
      alert("Failed to load historical record: " + (data.error || "Unknown"));
      return;
    }

    renderResults({
      folder_name: data.folder_name,
      csv_file: data.csv_file,
      excel_file: data.excel_file,
      json_file: data.json_file,
      archetype: (data.meta && data.meta.archetype) || "tabulation",
      semester: (data.meta && data.meta.semester) || "--",
      college_name: (data.meta && data.meta.college_name) || "",
      exam_title: (data.meta && data.meta.exam_title) || data.folder_name,
      total_students: data.total_students,
      checksum_summary: (data.meta && data.meta.checksum_verification) || {},
      students: data.students,
      duration_seconds: 0.0,
      dataset_meta: data.dataset_meta || (data.meta && data.meta.dataset_meta) || null
    });

    // Navigate to Extract & Audit workspace to view loaded dataset
    navigateTo("/extract");
  } catch (e) {
    hideProgress();
    alert("Error loading historical record: " + e.message);
  }
}

// 9. Interactive Chatbox for Ollama
function initChatbox() {
  const chatForm = document.getElementById("chatForm");
  const chatInput = document.getElementById("chatInput");
  const undoBtn = document.getElementById("btnUndoChat");
  const resetBtn = document.getElementById("btnResetData");
  const suggestionChips = document.querySelectorAll(".chip-btn");

  if (chatForm) {
    chatForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = chatInput.value.trim();
      if (!text) return;
      chatInput.value = "";
      handleChatSubmit(text);
    });
  }

  suggestionChips.forEach(chip => {
    chip.addEventListener("click", () => {
      const prompt = chip.dataset.prompt || chip.textContent;
      if (prompt) {
        handleChatSubmit(prompt);
      }
    });
  });

  if (undoBtn) {
    undoBtn.addEventListener("click", () => {
      if (undoSnapshots.length === 0) return;
      const prev = undoSnapshots.pop();
      currentRecords = prev;
      filteredRecords = [...currentRecords];
      currentPage = 1;
      displayTablePage();
      updateMetaCount(currentRecords.length);
      undoBtn.disabled = undoSnapshots.length === 0;

      appendChatMessage("assistant", `↩ Reverted last action. Restored dataset to ${currentRecords.length} student records.`);
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      if (originalRecords.length === 0) return;
      undoSnapshots.push(JSON.parse(JSON.stringify(currentRecords)));
      if (undoBtn) undoBtn.disabled = false;

      currentRecords = JSON.parse(JSON.stringify(originalRecords));
      filteredRecords = [...currentRecords];
      currentPage = 1;
      displayTablePage();
      updateMetaCount(currentRecords.length);

      appendChatMessage("assistant", `↺ Reset dataset back to initial extracted state (${currentRecords.length} student records).`);
    });
  }
}

async function handleChatSubmit(promptText) {
  if (!currentRecords || currentRecords.length === 0) {
    alert("Please extract or select a dataset first before using the Ollama data assistant.");
    return;
  }

  appendChatMessage("user", promptText);

  const chatInput = document.getElementById("chatInput");
  const sendBtn = document.getElementById("chatSendBtn");
  if (chatInput) chatInput.disabled = true;
  if (sendBtn) sendBtn.disabled = true;

  const thinkingId = appendChatMessage("assistant", "<em>⚡ Asking Ollama to process request...</em>", true);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: promptText,
        students: currentRecords,
        folder_name: currentFolderName,
        session_id: currentFolderName || "active_session"
      })
    });
    const data = await res.json();

    const thinkingElem = document.getElementById(thinkingId);
    if (thinkingElem) thinkingElem.remove();

    if (!data.success) {
      appendChatMessage("assistant", `❌ ${data.error || "Unable to process request."}`);
      return;
    }

    if (data.action && data.action !== "answer" && data.action !== "none" && Array.isArray(data.students)) {
      undoSnapshots.push(JSON.parse(JSON.stringify(currentRecords)));
      const undoBtn = document.getElementById("btnUndoChat");
      if (undoBtn) undoBtn.disabled = false;

      currentRecords = data.students;
      filteredRecords = [...currentRecords];
      currentPage = 1;
      displayTablePage();
      updateMetaCount(currentRecords.length);

      // Also log live into AI Audit terminal
      updateAuditLogs([...currentAuditLogs, {
        timestamp: new Date().toLocaleTimeString(),
        type: "ai_event",
        badge: "CHAT AI",
        title: `[CHAT ACTION] ${data.action.toUpperCase()}: ${data.reply}`,
        details: { prompt: promptText, action: data.action, new_count: currentRecords.length, engine: data.engine_used }
      }]);
    }

    const engineTag = data.engine_used ? `<div style="margin-top:0.4rem; font-size:0.75rem; color:var(--text-muted);">⚡ <em>${escapeHtml(data.engine_used)}</em></div>` : "";
    const formattedReply = escapeHtml(data.reply).replace(/\n/g, '<br>');
    appendChatMessage("assistant", `${formattedReply}${engineTag}`);
  } catch (err) {
    const thinkingElem = document.getElementById(thinkingId);
    if (thinkingElem) thinkingElem.remove();
    appendChatMessage("assistant", `❌ Network or processing error: ${err.message}`);
  } finally {
    if (chatInput) {
      chatInput.disabled = false;
      chatInput.focus();
    }
    if (sendBtn) sendBtn.disabled = false;
  }
}

function updateMetaCount(count) {
  const metaElem = document.getElementById("resultMeta");
  if (metaElem) {
    const text = metaElem.textContent;
    metaElem.textContent = text.replace(/Students: \d+/, `Students: ${count}`);
  }
}

function appendChatMessage(sender, htmlContent, isTemp = false) {
  const container = document.getElementById("chatHistory");
  if (!container) return;

  const msgDiv = document.createElement("div");
  const id = `msg-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
  msgDiv.id = id;
  msgDiv.className = `chat-message msg-${sender}`;

  const avatar = sender === "user" ? "👤" : "🤖";
  msgDiv.innerHTML = `
    <div class="msg-avatar">${avatar}</div>
    <div class="msg-body">
      <p>${htmlContent}</p>
    </div>
  `;

  container.appendChild(msgDiv);
  container.scrollTop = container.scrollHeight;
  return id;
}

// 10. Cloud AI & Hybrid Routing Setup Modal
async function initCloudModal() {
  const modalOverlay = document.getElementById("cloudModalOverlay");
  const openBtn = document.getElementById("btnOpenCloudModal");
  const closeBtn = document.getElementById("btnCloseCloudModal");
  const cancelBtn = document.getElementById("btnCancelCloudModal");
  const form = document.getElementById("cloudConfigForm");
  const autoOrchestrateCheck = document.getElementById("cloudAutoOrchestrate");
  const manualOverrideGroup = document.getElementById("manualOverrideGroup");
  const providerSelect = document.getElementById("cloudProviderSelect");
  const customUrlGroup = document.getElementById("customUrlGroup");
  const customUrlInput = document.getElementById("cloudCustomUrl");
  const modelSelect = document.getElementById("cloudModelSelect");
  const modelInput = document.getElementById("cloudModelInput");
  const customModelWrapper = document.getElementById("customModelWrapper");
  const toggleCustomModelBtn = document.getElementById("btnToggleCustomModel");
  const routingSelect = document.getElementById("cloudRoutingMode");
  const testBtn = document.getElementById("btnTestCloudConn");
  const probeAllBtn = document.getElementById("btnProbeAllKeys");
  const testAlert = document.getElementById("cloudTestAlert");

  // Multi-Provider Key Inputs and Badges
  const groqKeyInput = document.getElementById("cloudApiKeyGroq");
  const geminiKeyInput = document.getElementById("cloudApiKeyGemini");
  const openaiKeyInput = document.getElementById("cloudApiKeyOpenAI");
  const anthropicKeyInput = document.getElementById("cloudApiKeyAnthropic");
  const deepseekKeyInput = document.getElementById("cloudApiKeyDeepseek");
  const openrouterKeyInput = document.getElementById("cloudApiKeyOpenrouter");
  const groqKeyBadge = document.getElementById("groqKeyBadge");
  const geminiKeyBadge = document.getElementById("geminiKeyBadge");
  const openaiKeyBadge = document.getElementById("openaiKeyBadge");
  const anthropicKeyBadge = document.getElementById("anthropicKeyBadge");
  const deepseekKeyBadge = document.getElementById("deepseekKeyBadge");
  const openrouterKeyBadge = document.getElementById("openrouterKeyBadge");

  // Active Locked Banner Elements
  const lockedModelTitle = document.getElementById("lockedModelTitle");
  const lockedModelSubtitle = document.getElementById("lockedModelSubtitle");
  const lockedModelBadge = document.getElementById("lockedModelBadge");

  const providerModels = {
    groq: [
      { id: "openai/gpt-oss-120b", label: "openai/gpt-oss-120b (Recommended - 120B Flagship)" },
      { id: "qwen/qwen3.8-27b", label: "qwen/qwen3.8-27b (Fast 27B Reasoning)" },
      { id: "openai/gpt-oss-20b", label: "openai/gpt-oss-20b (Lightweight 20B)" },
      { id: "allam-2-7b", label: "allam-2-7b (7B Multilingual)" }
    ],
    gemini: [
      { id: "gemini-flash-latest", label: "gemini-flash-latest (Recommended - Fast & High Quota)" },
      { id: "gemini-flash-lite-latest", label: "gemini-flash-lite-latest (Ultra Fast Lite)" },
      { id: "gemini-3.8-flash", label: "gemini-3.8-flash (Latest Flash)" },
      { id: "gemini-3.5-flash", label: "gemini-3.5-flash (Balanced)" },
      { id: "gemini-pro-latest", label: "gemini-pro-latest (Deep Reasoning)" }
    ],
    openrouter: [
      { id: "google/gemma-4-31b-it:free", label: "google/gemma-4-31b-it:free (Vision, Recommended Free)" },
      { id: "google/gemma-4-26b-a4b-it:free", label: "google/gemma-4-26b-a4b-it:free (Vision, Gemma 4 Free)" },
      { id: "qwen/qwen3.8-27b:free", label: "qwen/qwen3.8-27b:free (Vision & Fast Reasoning Free)" },
      { id: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", label: "nvidia/nemotron-3-nano-omni:free (Multimodal Free)" }
    ],
    openai: [
      { id: "gpt-4o-mini", label: "gpt-4o-mini (Recommended - Efficient & Fast)" },
      { id: "gpt-4o", label: "gpt-4o (Flagship Multimodal)" },
      { id: "gpt-3.5-turbo", label: "gpt-3.5-turbo (Legacy Fast)" }
    ],
    anthropic: [
      { id: "claude-3-5-sonnet-20241022", label: "claude-3-5-sonnet-20241022 (Recommended - Flagship 3.5)" },
      { id: "claude-3-5-haiku-20241022", label: "claude-3-5-haiku-20241022 (Ultra Fast Haiku)" },
      { id: "claude-3-opus-20240229", label: "claude-3-opus-20240229 (Legacy Opus)" }
    ],
    deepseek: [
      { id: "deepseek-chat", label: "deepseek-chat (Recommended - V3 Flagship)" },
      { id: "deepseek-reasoner", label: "deepseek-reasoner (R1 Deep Reasoning)" }
    ],
    custom: []
  };

  // Cloud Modal Segmented Tabs
  const cloudTabs = document.querySelectorAll(".cloud-tab-btn");
  const cloudPanels = document.querySelectorAll(".cloud-tab-panel");

  function switchToCloudTab(targetPanelId) {
    cloudTabs.forEach(b => {
      if (b.getAttribute("data-target-panel") === targetPanelId) {
        b.classList.add("active");
      } else {
        b.classList.remove("active");
      }
    });
    cloudPanels.forEach(p => {
      if (p.id === targetPanelId) {
        p.classList.remove("hidden");
      } else {
        p.classList.add("hidden");
      }
    });
  }

  cloudTabs.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-target-panel");
      if (targetId) switchToCloudTab(targetId);
    });
  });

  // Escape key to close Cloud Modal
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && modalOverlay && !modalOverlay.classList.contains("hidden")) {
      closeModal();
    }
  });

  // Eye toggle buttons for showing / hiding passwords
  document.querySelectorAll(".btn-toggle-eye").forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-target");
      if (!targetId) return;
      const input = document.getElementById(targetId);
      if (!input) return;
      input.type = input.type === "password" ? "text" : "password";
      btn.textContent = input.type === "password" ? "👁️" : "🔒";
    });
  });

  // Auto-orchestrate toggle: hide / show manual overrides
  if (autoOrchestrateCheck) {
    autoOrchestrateCheck.addEventListener("change", (e) => {
      if (manualOverrideGroup) {
        manualOverrideGroup.style.display = e.target.checked ? "none" : "block";
      }
    });
  }

  function updateLockedBanner(config) {
    if (!lockedModelTitle || !lockedModelBadge) return;
    if (config && config.locked_model && config.is_configured) {
      const prov = (config.locked_model.provider || "").toUpperCase();
      const mod = config.locked_model.model || "";
      lockedModelTitle.textContent = `Locked Engine: ${prov} (${mod})`;
      if (lockedModelSubtitle) {
        lockedModelSubtitle.textContent = "Stickiness Active • Fast & Verified • Switches on error";
      }
      lockedModelBadge.className = "orchestrator-badge active";
      lockedModelBadge.textContent = "LOCKED";
    } else if (config && config.is_configured) {
      lockedModelTitle.textContent = "Multi-Engine Ready (Auto-Lock on query)";
      if (lockedModelSubtitle) {
        lockedModelSubtitle.textContent = "Keys saved • Standby for fastest model lock";
      }
      lockedModelBadge.className = "orchestrator-badge";
      lockedModelBadge.textContent = "READY";
    } else {
      lockedModelTitle.textContent = "No Cloud Engines Configured";
      if (lockedModelSubtitle) {
        lockedModelSubtitle.textContent = "Add at least one API key below to enable Cloud AI";
      }
      lockedModelBadge.className = "orchestrator-badge offline";
      lockedModelBadge.textContent = "OFFLINE";
    }
  }

  function updateKeyBadges(maskedKeys = {}) {
    const list = [
      { id: "groq", badge: groqKeyBadge },
      { id: "gemini", badge: geminiKeyBadge },
      { id: "openrouter", badge: openrouterKeyBadge },
      { id: "openai", badge: openaiKeyBadge },
      { id: "anthropic", badge: anthropicKeyBadge },
      { id: "deepseek", badge: deepseekKeyBadge }
    ];

    let configuredCount = 0;
    list.forEach(item => {
      if (!item.badge) return;
      const mask = maskedKeys[item.id];
      if (mask && mask.trim()) {
        configuredCount++;
        item.badge.className = "key-status-badge configured";
        item.badge.textContent = `🟢 Saved (${mask})`;
      } else {
        item.badge.className = "key-status-badge missing";
        item.badge.textContent = "⚪ Not set";
      }
    });

    const pillCount = document.getElementById("tabPillKeysCount");
    if (pillCount) {
      pillCount.textContent = configuredCount > 0 ? `${configuredCount} Active / 6 Providers` : "6 Providers";
    }
  }

  async function updateProviderUI(provider, targetModel = null) {
    if (provider === "custom") {
      if (customUrlGroup) customUrlGroup.style.display = "block";
      if (customModelWrapper) customModelWrapper.style.display = "block";
    } else {
      if (customUrlGroup) customUrlGroup.style.display = "none";
    }

    const models = providerModels[provider] || [];

    if (modelSelect) {
      modelSelect.innerHTML = "";
      if (models.length === 0) {
        const opt = document.createElement("option");
        opt.value = targetModel || "default";
        opt.textContent = targetModel || "default";
        modelSelect.appendChild(opt);
      } else {
        models.forEach(m => {
          const opt = document.createElement("option");
          opt.value = typeof m === "string" ? m : m.id;
          opt.textContent = typeof m === "string" ? m : (m.label || m.id);
          modelSelect.appendChild(opt);
        });
      }

      const modelIds = models.map(m => typeof m === "string" ? m : m.id);
      if (targetModel && modelIds.includes(targetModel)) {
        modelSelect.value = targetModel;
      } else if (modelIds.length > 0) {
        modelSelect.value = modelIds[0];
      }
      if (modelInput) modelInput.value = modelSelect.value;
    }
  }

  if (modelSelect) {
    modelSelect.addEventListener("change", (e) => {
      if (modelInput) modelInput.value = e.target.value;
    });
  }

  if (toggleCustomModelBtn && customModelWrapper) {
    toggleCustomModelBtn.addEventListener("click", () => {
      const isHidden = customModelWrapper.style.display === "none";
      customModelWrapper.style.display = isHidden ? "block" : "none";
      toggleCustomModelBtn.textContent = isHidden ? "📋 Use Preset Dropdown" : "✏️ Custom Model ID";
      if (isHidden && modelInput) modelInput.focus();
    });
  }

  if (providerSelect) {
    providerSelect.addEventListener("change", (e) => {
      updateProviderUI(e.target.value);
    });
  }

  // Load current cloud config from server
  async function loadCloudConfig() {
    try {
      const res = await fetch("/api/cloud/config");
      const data = await res.json();
      if (!data) return;

      if (autoOrchestrateCheck) {
        autoOrchestrateCheck.checked = data.auto_orchestrate !== false;
        if (manualOverrideGroup) {
          manualOverrideGroup.style.display = autoOrchestrateCheck.checked ? "none" : "block";
        }
      }

      if (routingSelect && data.routing_mode) {
        routingSelect.value = data.routing_mode;
      }
      if (providerSelect && data.provider) {
        providerSelect.value = data.provider;
      }
      if (customUrlInput && data.custom_base_url) {
        customUrlInput.value = data.custom_base_url;
      }

      updateLockedBanner(data);
      updateKeyBadges(data.masked_keys || {});
      await updateProviderUI(providerSelect ? providerSelect.value : "groq", data.model);
    } catch (e) {
      console.error("Failed to fetch cloud config:", e);
    }
  }

  if (openBtn) {
    openBtn.addEventListener("click", () => {
      if (testAlert) {
        testAlert.classList.add("hidden");
        testAlert.textContent = "";
      }
      loadCloudConfig();
      modalOverlay.classList.remove("hidden");
    });
  }

  function closeModal() {
    modalOverlay.classList.add("hidden");
    if (testAlert) testAlert.classList.add("hidden");
  }

  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (cancelBtn) cancelBtn.addEventListener("click", closeModal);
  modalOverlay.addEventListener("click", (e) => {
    if (e.target === modalOverlay) closeModal();
  });

  function getEffectiveModel() {
    if (customModelWrapper && customModelWrapper.style.display !== "none" && modelInput && modelInput.value.trim()) {
      return modelInput.value.trim();
    }
    if (modelSelect && modelSelect.value) {
      return modelSelect.value;
    }
    if (modelInput && modelInput.value.trim()) {
      return modelInput.value.trim();
    }
    return "default";
  }

  // Inline Key Test Buttons (⚡ Test Key per provider without saving)
  const providerInputMap = {
    groq: groqKeyInput,
    gemini: geminiKeyInput,
    openai: openaiKeyInput,
    anthropic: anthropicKeyInput,
    deepseek: deepseekKeyInput,
    openrouter: openrouterKeyInput
  };

  const providerBadgeMap = {
    groq: groqKeyBadge,
    gemini: geminiKeyBadge,
    openai: openaiKeyBadge,
    anthropic: anthropicKeyBadge,
    deepseek: deepseekKeyBadge,
    openrouter: openrouterKeyBadge
  };

  const inlineTestBtns = document.querySelectorAll(".btn-test-key-inline");
  inlineTestBtns.forEach(btn => {
    const prov = btn.getAttribute("data-provider");
    const inputEl = providerInputMap[prov];

    // Reset button state when user edits key
    if (inputEl) {
      inputEl.addEventListener("input", () => {
        btn.className = "btn-test-key-inline";
        btn.disabled = false;
        btn.innerHTML = "<span>⚡ Test Key</span>";
      });
    }

    btn.addEventListener("click", async () => {
      const liveKey = inputEl ? inputEl.value.trim() : "";
      const badge = providerBadgeMap[prov];

      btn.disabled = true;
      btn.className = "btn-test-key-inline testing";
      btn.innerHTML = "<span>⏳ Testing...</span>";

      if (testAlert) {
        testAlert.className = "alert-box info";
        testAlert.textContent = `Testing ${prov.toUpperCase()} connection with live key...`;
        testAlert.classList.remove("hidden");
      }

      try {
        const payload = {
          provider: prov,
          api_key: liveKey
        };
        const res = await fetch("/api/cloud/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const result = await res.json();

        if (result.success) {
          btn.className = "btn-test-key-inline success";
          btn.innerHTML = "<span>✓ Valid</span>";
          if (badge) {
            badge.className = "key-status-badge success";
            badge.textContent = "✓ Verified";
          }
          if (testAlert) {
            testAlert.className = "alert-box success";
            testAlert.textContent = `✓ ${result.message}`;
            testAlert.classList.remove("hidden");
          }
        } else {
          btn.className = "btn-test-key-inline error";
          btn.innerHTML = "<span>✗ Failed</span>";
          if (badge) {
            badge.className = "key-status-badge error";
            badge.textContent = "✗ Failed";
          }
          if (testAlert) {
            testAlert.className = "alert-box error";
            testAlert.textContent = `✗ ${result.message}`;
            testAlert.classList.remove("hidden");
          }
        }
      } catch (err) {
        btn.className = "btn-test-key-inline error";
        btn.innerHTML = "<span>✗ Error</span>";
        if (testAlert) {
          testAlert.className = "alert-box error";
          testAlert.textContent = `✗ Network error testing ${prov}: ${err.message}`;
          testAlert.classList.remove("hidden");
        }
      } finally {
        btn.disabled = false;
        setTimeout(() => {
          if (btn.classList.contains("success") || btn.classList.contains("error")) {
            btn.className = "btn-test-key-inline";
            btn.innerHTML = "<span>⚡ Test Key</span>";
          }
        }, 5000);
      }
    });
  });

  // Probe All Keys Button
  if (probeAllBtn) {
    probeAllBtn.addEventListener("click", async () => {
      switchToCloudTab("cloudTabPanelDiagnostics");
      probeAllBtn.disabled = true;
      const origText = probeAllBtn.textContent;
      probeAllBtn.textContent = "⏳ Probing Keys in Parallel...";

      if (testAlert) {
        testAlert.className = "alert-box info";
        testAlert.textContent = "Probing all configured and entered API keys simultaneously (5s timeout per provider)...";
        testAlert.classList.remove("hidden");
      }

      try {
        const payload = { api_keys: {} };
        if (groqKeyInput && groqKeyInput.value.trim()) payload.api_keys.groq = groqKeyInput.value.trim();
        if (geminiKeyInput && geminiKeyInput.value.trim()) payload.api_keys.gemini = geminiKeyInput.value.trim();
        if (openrouterKeyInput && openrouterKeyInput.value.trim()) payload.api_keys.openrouter = openrouterKeyInput.value.trim();
        if (openaiKeyInput && openaiKeyInput.value.trim()) payload.api_keys.openai = openaiKeyInput.value.trim();
        if (anthropicKeyInput && anthropicKeyInput.value.trim()) payload.api_keys.anthropic = anthropicKeyInput.value.trim();
        if (deepseekKeyInput && deepseekKeyInput.value.trim()) payload.api_keys.deepseek = deepseekKeyInput.value.trim();

        const res = await fetch("/api/cloud/probe-all", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success && data.report) {
          const rep = data.report;
          const updateBadgeHelper = (badge, repProv, provName) => {
            if (!badge || !repProv) return;
            const inlineBtn = document.querySelector(`.btn-test-key-inline[data-provider="${provName}"]`);
            if (repProv.success) {
              badge.className = "key-status-badge success";
              badge.textContent = repProv.key_count > 1
                ? `✓ Active (${repProv.latency_ms}ms, ${repProv.key_count} keys)`
                : `✓ Active (${repProv.latency_ms}ms)`;
              if (inlineBtn) {
                inlineBtn.className = "btn-test-key-inline success";
                inlineBtn.innerHTML = "<span>✓ Valid</span>";
              }
            } else if (repProv.configured) {
              badge.className = "key-status-badge error";
              badge.textContent = `✗ ${repProv.message.slice(0, 24)}`;
              if (inlineBtn) {
                inlineBtn.className = "btn-test-key-inline error";
                inlineBtn.innerHTML = "<span>✗ Failed</span>";
              }
            } else {
              badge.className = "key-status-badge missing";
              badge.textContent = "⚪ No key";
              if (inlineBtn) {
                inlineBtn.className = "btn-test-key-inline";
                inlineBtn.innerHTML = "<span>⚡ Test Key</span>";
              }
            }
          };

          const updateDiagCard = (cardId, statusId, latencyId, repProv) => {
            const card = document.getElementById(cardId);
            const statusEl = document.getElementById(statusId);
            const latEl = document.getElementById(latencyId);
            if (!card || !statusEl || !latEl) return;
            if (!repProv || !repProv.configured) {
              card.className = "diag-card";
              statusEl.textContent = "Not set";
              latEl.textContent = "Standby";
              latEl.style.color = "var(--text-muted)";
            } else if (repProv.success) {
              card.className = "diag-card success";
              statusEl.textContent = "Online";
              latEl.textContent = repProv.key_count > 1
                ? `${repProv.latency_ms} ms (${repProv.key_count} keys)`
                : `${repProv.latency_ms} ms`;
              latEl.style.color = "#34D399";
            } else {
              card.className = "diag-card error";
              statusEl.textContent = "Failed";
              latEl.textContent = "Error";
              latEl.style.color = "#F87171";
            }
          };

          updateBadgeHelper(groqKeyBadge, rep.groq, "groq");
          updateBadgeHelper(geminiKeyBadge, rep.gemini, "gemini");
          updateBadgeHelper(openrouterKeyBadge, rep.openrouter, "openrouter");
          updateBadgeHelper(openaiKeyBadge, rep.openai, "openai");
          updateBadgeHelper(anthropicKeyBadge, rep.anthropic, "anthropic");
          updateBadgeHelper(deepseekKeyBadge, rep.deepseek, "deepseek");

          updateDiagCard("diagGroqCard", "diagGroqStatus", "diagGroqLatency", rep.groq);
          updateDiagCard("diagGeminiCard", "diagGeminiStatus", "diagGeminiLatency", rep.gemini);
          updateDiagCard("diagOpenrouterCard", "diagOpenrouterStatus", "diagOpenrouterLatency", rep.openrouter);
          updateDiagCard("diagAnthropicCard", "diagAnthropicStatus", "diagAnthropicLatency", rep.anthropic);
          updateDiagCard("diagDeepseekCard", "diagDeepseekStatus", "diagDeepseekLatency", rep.deepseek);
          updateDiagCard("diagOpenaiCard", "diagOpenaiStatus", "diagOpenaiLatency", rep.openai);

          if (data.config) {
            updateLockedBanner(data.config);
          }

          if (testAlert) {
            if (rep.best_locked_model) {
              testAlert.className = "alert-box success";
              testAlert.textContent = `✓ Multi-Provider Probe Complete! Locked onto fastest working model: ${rep.best_locked_model.provider.toUpperCase()} (${rep.best_locked_model.model})`;
            } else {
              testAlert.className = "alert-box warning";
              testAlert.textContent = "Probe finished, but no candidate models passed validation. Please verify your keys.";
            }
          }

          setTimeout(() => {
            document.querySelectorAll(".btn-test-key-inline").forEach(b => {
              if (b.classList.contains("success") || b.classList.contains("error")) {
                b.className = "btn-test-key-inline";
                b.innerHTML = "<span>⚡ Test Key</span>";
              }
            });
          }, 6000);
        } else {
          if (testAlert) {
            testAlert.className = "alert-box error";
            testAlert.textContent = `Probe failed: ${data.error || "Unknown server response"}`;
          }
        }
      } catch (err) {
        if (testAlert) {
          testAlert.className = "alert-box error";
          testAlert.textContent = `Network error during probe: ${err.message}`;
        }
      } finally {
        probeAllBtn.disabled = false;
        probeAllBtn.textContent = origText;
      }
    });
  }

  // Test Active AI Button
  if (testBtn) {
    testBtn.addEventListener("click", async () => {
      if (testAlert) {
        testAlert.className = "alert-box hidden";
        testAlert.textContent = "";
      }
      testBtn.disabled = true;
      testBtn.textContent = "⚡ Testing...";

      try {
        const effectiveModel = getEffectiveModel();
        const curProv = providerSelect ? providerSelect.value : "groq";
        let testKey = "";
        if (curProv === "groq" && groqKeyInput) testKey = groqKeyInput.value.trim();
        else if (curProv === "gemini" && geminiKeyInput) testKey = geminiKeyInput.value.trim();
        else if (curProv === "openrouter" && openrouterKeyInput) testKey = openrouterKeyInput.value.trim();
        else if (curProv === "openai" && openaiKeyInput) testKey = openaiKeyInput.value.trim();
        else if (curProv === "anthropic" && anthropicKeyInput) testKey = anthropicKeyInput.value.trim();
        else if (curProv === "deepseek" && deepseekKeyInput) testKey = deepseekKeyInput.value.trim();

        const payload = {
          provider: curProv,
          api_key: testKey,
          model: effectiveModel,
          custom_base_url: customUrlInput ? customUrlInput.value.trim() : ""
        };
        const res = await fetch("/api/cloud/test", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const result = await res.json();

        if (testAlert) {
          testAlert.classList.remove("hidden");
          if (result.success) {
            testAlert.className = "alert-box success";
            testAlert.textContent = `✓ ${result.message}`;
          } else {
            testAlert.className = "alert-box error";
            testAlert.textContent = `✗ ${result.message}`;
          }
        }
      } catch (err) {
        if (testAlert) {
          testAlert.className = "alert-box error";
          testAlert.classList.remove("hidden");
          testAlert.textContent = `✗ Network error during test: ${err.message}`;
        }
      } finally {
        testBtn.disabled = false;
        testBtn.textContent = "⚡ Test Active AI";
      }
    });
  }

  // Save Config Form Submit
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const saveBtn = document.getElementById("btnSaveCloudConfig");
      if (saveBtn) saveBtn.disabled = true;

      try {
        const effectiveModel = getEffectiveModel();
        const payload = {
          auto_orchestrate: autoOrchestrateCheck ? autoOrchestrateCheck.checked : true,
          provider: providerSelect ? providerSelect.value : "groq",
          model: effectiveModel,
          routing_mode: routingSelect ? routingSelect.value : "auto_hybrid",
          custom_base_url: customUrlInput ? customUrlInput.value.trim() : "",
          api_keys: {}
        };

        if (groqKeyInput && groqKeyInput.value.trim()) {
          payload.api_keys.groq = groqKeyInput.value.trim();
        }
        if (geminiKeyInput && geminiKeyInput.value.trim()) {
          payload.api_keys.gemini = geminiKeyInput.value.trim();
        }
        if (openrouterKeyInput && openrouterKeyInput.value.trim()) {
          payload.api_keys.openrouter = openrouterKeyInput.value.trim();
        }
        if (openaiKeyInput && openaiKeyInput.value.trim()) {
          payload.api_keys.openai = openaiKeyInput.value.trim();
        }
        if (anthropicKeyInput && anthropicKeyInput.value.trim()) {
          payload.api_keys.anthropic = anthropicKeyInput.value.trim();
        }
        if (deepseekKeyInput && deepseekKeyInput.value.trim()) {
          payload.api_keys.deepseek = deepseekKeyInput.value.trim();
        }

        const res = await fetch("/api/cloud/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success) {
          if (groqKeyInput) groqKeyInput.value = "";
          if (geminiKeyInput) geminiKeyInput.value = "";
          if (openrouterKeyInput) openrouterKeyInput.value = "";
          if (openaiKeyInput) openaiKeyInput.value = "";
          if (anthropicKeyInput) anthropicKeyInput.value = "";
          if (deepseekKeyInput) deepseekKeyInput.value = "";

          closeModal();
          initStatus(); // refresh header status badge

          const activeInfo = data.config && data.config.locked_model
            ? `${data.config.locked_model.provider.toUpperCase()} (${data.config.locked_model.model})`
            : `${data.config.provider.toUpperCase()} (${data.config.model})`;

          appendChatMessage("assistant", `🌐 **Cloud AI Orchestrator Updated**: Connected to <strong>${escapeHtml(activeInfo)}</strong> with routing strategy <em>${escapeHtml(data.config.routing_mode)}</em>.`);
        } else {
          alert(`Error saving config: ${data.error || "Unknown error"}`);
        }
      } catch (err) {
        alert(`Failed to save cloud config: ${err.message}`);
      } finally {
        if (saveBtn) saveBtn.disabled = false;
      }
    });
  }
}


/* ==========================================================================
   11. AI-Powered Visual EDA Dashboard Engine
   ========================================================================== */

let currentEdaData = null;
const chartInstances = {
  sgpiDist: null,
  gradeClass: null,
  subjectPass: null,
  subjectMarks: null,
  cohortSegments: null,
  genderDist: null
};

function initResultsTabs() {
  const btnTable = document.getElementById("tabBtnTable");
  const btnAnalytics = document.getElementById("tabBtnAnalytics");
  const viewTable = document.getElementById("tabViewTable");
  const viewAnalytics = document.getElementById("tabViewAnalytics");

  if (!btnTable || !btnAnalytics) return;

  btnTable.addEventListener("click", () => {
    btnTable.classList.add("active");
    btnAnalytics.classList.remove("active");
    viewTable.classList.remove("hidden");
    viewAnalytics.classList.add("hidden");
  });

  btnAnalytics.addEventListener("click", () => {
    btnAnalytics.classList.add("active");
    btnTable.classList.remove("active");
    viewAnalytics.classList.remove("hidden");
    viewTable.classList.add("hidden");

    if (!currentEdaData || (currentEdaData.overview && currentEdaData.overview.total_students !== currentRecords.length)) {
      computeEdaDashboard();
    } else {
      // Re-trigger layout resize for Chart.js when unhidden
      setTimeout(() => {
        Object.values(chartInstances).forEach(c => { if (c) c.resize(); });
      }, 50);
    }
  });

  // Action Buttons
  const btnRefresh = document.getElementById("btnRefreshEda");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => computeEdaDashboard(true));
  }

  const btnGenInsights = document.getElementById("btnGenInsights");
  if (btnGenInsights) {
    btnGenInsights.addEventListener("click", () => generateAiInsights());
  }

  const btnExportJson = document.getElementById("btnExportEdaJson");
  if (btnExportJson) {
    btnExportJson.addEventListener("click", () => exportEdaJson());
  }

  const btnToggleCollapse = document.getElementById("btnToggleInsightsCollapse");
  if (btnToggleCollapse) {
    btnToggleCollapse.addEventListener("click", () => {
      const content = document.getElementById("edaInsightsContent");
      if (content) {
        const isHidden = content.style.display === "none";
        content.style.display = isHidden ? "block" : "none";
        btnToggleCollapse.textContent = isHidden ? "Collapse" : "Expand";
      }
    });
  }
}

async function computeEdaDashboard(forceRefresh = false) {
  if (!currentRecords || currentRecords.length === 0) {
    return;
  }

  const btnRefresh = document.getElementById("btnRefreshEda");
  if (btnRefresh) btnRefresh.textContent = "⏳ Computing...";

  try {
    const res = await fetch("/api/eda", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        students: currentRecords,
        folder_name: currentFolderName
      })
    });
    const data = await res.json();

    if (!data.success || !data.eda) {
      console.error("EDA computation error:", data.error);
      return;
    }

    currentEdaData = data.eda;
    renderEdaKPIs(currentEdaData);
    renderEdaCharts(currentEdaData);
    renderEdaTables(currentEdaData);

    const savedPill = document.getElementById("edaTokensSaved");
    if (savedPill && currentEdaData.mapping_meta) {
      const spent = currentEdaData.mapping_meta.token_usage || 0;
      savedPill.textContent = `✨ 99.6% Tokens Saved (${spent} tokens spent on schema mapping, 0 on analytics)`;
    }
  } catch (err) {
    console.error("Failed to execute EDA:", err);
  } finally {
    if (btnRefresh) btnRefresh.textContent = "↻ Re-compute Stats";
  }
}

function renderEdaKPIs(eda) {
  const o = eda.overview || {};

  setText("kpiTotalStudents", o.total_students || 0);
  setText("kpiAppearSub", `${o.total_students || 0} Enrolled Candidates`);

  setText("kpiPassRate", `${o.pass_percentage || 0}%`);
  setText("kpiPassedCount", `${o.passed_count || 0} Passed`);

  setText("kpiAvgSgpi", (o.avg_sgpi || 0).toFixed(2));
  setText("kpiMedianSgpi", `Median: ${(o.median_sgpi || 0).toFixed(2)} | Std: ${(o.std_sgpi || 0).toFixed(2)}`);

  setText("kpiMaxSgpi", (o.max_sgpi || 0).toFixed(2));
  const maxPossible = o.max_possible_marks ? ` / ${Math.round(o.max_possible_marks)}` : "";
  setText("kpiMaxMarks", `Max Marks: ${Math.round(o.max_total_marks || 0)}${maxPossible}`);

  // Lowest Cleared Performer
  const lowP = eda.lowest_performer || o.lowest_sgpi_student || null;
  if (lowP && lowP.sgpi) {
    setText("kpiMinSgpi", (lowP.sgpi || 0).toFixed(2));
    setText("kpiMinStudentName", `${lowP.student_name || 'Student'} (${lowP.seat_no || ''})`);
  } else if (o.min_sgpi) {
    setText("kpiMinSgpi", (o.min_sgpi || 0).toFixed(2));
    setText("kpiMinStudentName", "Lowest Cleared");
  } else {
    setText("kpiMinSgpi", "--");
    setText("kpiMinStudentName", "No cleared students");
  }

  setText("kpiDistinction", o.distinction_count || 0);
  const distPct = o.total_students ? Math.round((o.distinction_count / o.total_students) * 100) : 0;
  setText("kpiDistinctionPct", `${distPct}% of cohort (SGPI ≥ 7.5)`);

  setText("kpiFailCount", o.atkt_fail_count || o.failed_count || 0);
  const ktNull = o.kt_null_gpa_count ? ` (${o.kt_null_gpa_count} Null GPA)` : "";
  setText("kpiFailPct", `${o.fail_percentage || 0}% KT / ATKT${ktNull}`);
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function renderEdaCharts(eda) {
  if (typeof Chart === "undefined") {
    console.warn("Chart.js is not loaded.");
    return;
  }

  // Set global chart theme defaults
  const isLightTheme = document.documentElement.getAttribute("data-theme") === "light";
  Chart.defaults.color = isLightTheme ? "#475569" : "#94A3B8";
  Chart.defaults.borderColor = isLightTheme ? "rgba(0, 0, 0, 0.08)" : "rgba(255, 255, 255, 0.07)";
  Chart.defaults.font.family = "'Inter', sans-serif";

  renderSgpiDistChart(eda.sgpi_distribution || []);
  renderGradeClassChart(eda.overview || {});
  renderSubjectPassChart(eda.subject_analytics || []);
  renderSubjectMarksChart(eda.subject_analytics || []);
  renderCohortSegmentsChart(eda.demographics?.candidate_type || {});
  renderGenderDistChart(eda.demographics?.gender || {});
}

function renderSgpiDistChart(buckets) {
  const ctx = document.getElementById("chartSgpiDist");
  if (!ctx) return;
  if (chartInstances.sgpiDist) chartInstances.sgpiDist.destroy();

  const labels = buckets.map(b => b.label);
  const counts = buckets.map(b => b.count);
  const colors = [
    "#ef4444", // Fail
    "#f97316", // Pass
    "#f59e0b", // Second Class
    "#eab308", // Higher Second
    "#3b82f6", // First Class
    "#6366f1", // Distinction
    "#10b981", // Outstanding
  ];

  chartInstances.sgpiDist = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "Students",
        data: counts,
        backgroundColor: colors.slice(0, counts.length),
        borderRadius: 4,
        borderWidth: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => ` ${ctx.parsed.y} students (${buckets[ctx.dataIndex]?.percentage || 0}%)`
          }
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 }
        }
      }
    }
  });
}

function renderGradeClassChart(overview) {
  const ctx = document.getElementById("chartGradeClass");
  if (!ctx) return;
  if (chartInstances.gradeClass) chartInstances.gradeClass.destroy();

  const dist = overview.distinction_count || 0;
  const first = overview.first_class_count || 0;
  const second = overview.second_class_count || 0;
  const pass = overview.pass_class_count || 0;
  const fail = overview.atkt_fail_count || 0;

  chartInstances.gradeClass = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: ["Distinction (≥7.5)", "First Class (6.75-7.49)", "Second Class (6.0-6.74)", "Pass Class (4.0-5.99)", "KT / Fail (<4.0)"],
      datasets: [{
        data: [dist, first, second, pass, fail],
        backgroundColor: ["#10b981", "#6366f1", "#06b6d4", "#f59e0b", "#ef4444"],
        borderWidth: 2,
        borderColor: "#1e293b"
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 12, padding: 12, font: { size: 10 } }
        }
      },
      cutout: "60%"
    }
  });
}

function renderSubjectPassChart(subjects) {
  const ctx = document.getElementById("chartSubjectPass");
  if (!ctx) return;
  if (chartInstances.subjectPass) chartInstances.subjectPass.destroy();

  // Sort subjects by pass percentage ascending so lowest is at top
  const sorted = [...subjects].sort((a, b) => a.pass_pct - b.pass_pct);
  const labels = sorted.map(s => s.code + (s.name && s.name !== s.code ? ` (${s.name.substring(0, 20)}...)` : ''));
  const passRates = sorted.map(s => s.pass_pct);
  const colors = passRates.map(p => p < 75 ? "#ef4444" : (p < 90 ? "#f59e0b" : "#10b981"));

  chartInstances.subjectPass = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label: "Pass Percentage",
        data: passRates,
        backgroundColor: colors,
        borderRadius: 4
      }]
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => ` Pass Rate: ${ctx.parsed.x}% (${sorted[ctx.dataIndex]?.passed}/${sorted[ctx.dataIndex]?.total_appeared} students)`
          }
        }
      },
      scales: {
        x: {
          min: 0,
          max: 100,
          ticks: { callback: v => `${v}%` }
        }
      }
    }
  });
}

function renderSubjectMarksChart(subjects) {
  const ctx = document.getElementById("chartSubjectMarks");
  if (!ctx) return;
  if (chartInstances.subjectMarks) chartInstances.subjectMarks.destroy();

  const labels = subjects.map(s => s.code);
  const avgTotal = subjects.map(s => s.avg_marks || 0);
  const avgSe = subjects.map(s => s.avg_se || 0);
  const avgIa = subjects.map(s => s.avg_ia || 0);

  const datasets = [
    {
      label: "Avg Total Marks",
      data: avgTotal,
      backgroundColor: "#6366f1",
      borderRadius: 4
    }
  ];

  if (subjects.some(s => s.has_se && s.avg_se)) {
    datasets.push({
      label: "Avg Semester Exam (SE)",
      data: avgSe,
      backgroundColor: "#38bdf8",
      borderRadius: 4
    });
  }

  if (subjects.some(s => s.has_ia && s.avg_ia)) {
    datasets.push({
      label: "Avg Internal Assessment (IA)",
      data: avgIa,
      backgroundColor: "#a855f7",
      borderRadius: 4
    });
  }

  chartInstances.subjectMarks = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: datasets
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 12, padding: 12, font: { size: 10 } }
        }
      },
      scales: {
        y: {
          beginAtZero: true
        }
      }
    }
  });
}

function renderCohortSegmentsChart(candidateType) {
  const ctx = document.getElementById("chartCohortSegments");
  if (!ctx) return;
  if (chartInstances.cohortSegments) chartInstances.cohortSegments.destroy();

  const reg = candidateType.regular || { count: 0, pass_pct: 0, avg_sgpi: 0 };
  const dip = candidateType.diploma || { count: 0, pass_pct: 0, avg_sgpi: 0 };

  chartInstances.cohortSegments = new Chart(ctx, {
    type: "bar",
    data: {
      labels: [`Regular (${reg.count})`, `Diploma / Lateral (${dip.count})`],
      datasets: [
        {
          label: "Pass Rate (%)",
          data: [reg.pass_pct || 0, dip.pass_pct || 0],
          backgroundColor: "#10b981",
          borderRadius: 4
        },
        {
          label: "Average SGPI (x10)",
          data: [(reg.avg_sgpi || 0) * 10, (dip.avg_sgpi || 0) * 10],
          backgroundColor: "#6366f1",
          borderRadius: 4
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 12, font: { size: 10 } }
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              if (ctx.datasetIndex === 0) return ` Pass Rate: ${ctx.parsed.y}%`;
              return ` Avg SGPI: ${(ctx.parsed.y / 10).toFixed(2)}`;
            }
          }
        }
      },
      scales: {
        y: {
          beginAtZero: true,
          max: 100
        }
      }
    }
  });
}

function renderGenderDistChart(gender) {
  const ctx = document.getElementById("chartGenderDist");
  if (!ctx) return;
  if (chartInstances.genderDist) chartInstances.genderDist.destroy();

  const male = gender.male || { count: 0, pass_pct: 0, avg_sgpi: 0 };
  const female = gender.female || { count: 0, pass_pct: 0, avg_sgpi: 0 };

  chartInstances.genderDist = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: [`Male (${male.count} - ${male.pass_pct}% Pass)`, `Female (${female.count} - ${female.pass_pct}% Pass)`],
      datasets: [{
        data: [male.count, female.count],
        backgroundColor: ["#38bdf8", "#ec4899"],
        borderWidth: 2,
        borderColor: "#1e293b"
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 12, padding: 12, font: { size: 10 } }
        },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const obj = ctx.dataIndex === 0 ? male : female;
              return ` ${ctx.label}: ${obj.count} students | Pass: ${obj.pass_pct}% | Avg SGPI: ${(obj.avg_sgpi || 0).toFixed(2)}`;
            }
          }
        }
      },
      cutout: "60%"
    }
  });
}

function renderEdaTables(eda) {
  // Top Performers Table
  const topBody = document.getElementById("topPerformersBody");
  if (topBody) {
    const list = eda.top_performers || [];
    if (list.length === 0) {
      topBody.innerHTML = `<tr><td colspan="5" class="empty-cell">No performers recorded</td></tr>`;
    } else {
      topBody.innerHTML = list.map(p => {
        let medal = "";
        if (p.rank === 1) medal = "🥇 ";
        else if (p.rank === 2) medal = "🥈 ";
        else if (p.rank === 3) medal = "🥉 ";
        return `
          <tr>
            <td><strong>${medal}${p.rank}</strong></td>
            <td>${escapeHtml(p.seat_no)}</td>
            <td><strong>${escapeHtml(p.student_name)}</strong></td>
            <td><span class="text-success font-bold">${(p.sgpi || 0).toFixed(2)}</span></td>
            <td>${p.total_marks || '--'}</td>
          </tr>
        `;
      }).join("");
    }
  }

  // Lowest Cleared Performers Table
  const bottomBody = document.getElementById("bottomPerformersBody");
  if (bottomBody) {
    const list = eda.bottom_performers || [];
    if (list.length === 0) {
      bottomBody.innerHTML = `<tr><td colspan="5" class="empty-cell">No cleared records available</td></tr>`;
    } else {
      bottomBody.innerHTML = list.map(p => `
        <tr>
          <td><strong>#${p.rank}</strong></td>
          <td>${escapeHtml(p.seat_no)}</td>
          <td><strong>${escapeHtml(p.student_name)}</strong></td>
          <td><span class="text-warning font-bold">${(p.sgpi || 0).toFixed(2)}</span></td>
          <td>${p.total_marks || '--'}</td>
        </tr>
      `).join("");
    }
  }

  // Remedial Table
  const remBody = document.getElementById("remedialBody");
  if (remBody) {
    const rem = eda.remedial_alerts || [];
    if (rem.length === 0) {
      remBody.innerHTML = `<tr><td colspan="4" class="empty-cell">🎉 Outstanding! No candidates require remedial intervention.</td></tr>`;
    } else {
      remBody.innerHTML = rem.map(r => `
        <tr>
          <td>${escapeHtml(r.seat_no)}</td>
          <td>${escapeHtml(r.student_name)}</td>
          <td><span class="text-danger">${(r.sgpi || 0).toFixed(2)}</span></td>
          <td><span class="subtable-badge text-danger">${escapeHtml(r.reason)}</span></td>
        </tr>
      `).join("");
    }
  }
}

async function generateAiInsights() {
  if (!currentEdaData) {
    alert("Please calculate EDA first before generating AI executive insights.");
    return;
  }

  const btn = document.getElementById("btnGenInsights");
  const content = document.getElementById("edaInsightsContent");
  const badge = document.getElementById("insightsEngineBadge");

  if (btn) btn.textContent = "⏳ Synthesizing Brief...";
  if (badge) badge.textContent = "Synthesizing...";
  if (content) {
    content.innerHTML = `<div class="eda-insights-placeholder"><span class="spinner"></span> Synthesizing departmental brief & NAAC recommendations with AI (~150 tokens)...</div>`;
  }

  try {
    const res = await fetch("/api/eda/insights", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ eda: currentEdaData })
    });
    const data = await res.json();

    if (!data.success || !data.insights) {
      content.innerHTML = `<p class="text-danger">Failed to generate insights: ${data.error || "Unknown error"}</p>`;
      return;
    }

    const ins = data.insights;
    if (badge) badge.textContent = ins.engine_used || "AI Complete";

    // Format markdown
    content.innerHTML = formatMarkdown(ins.narrative_markdown || "No insights returned.");
  } catch (err) {
    console.error("Failed to generate AI insights:", err);
    if (content) content.innerHTML = `<p class="text-danger">Error: ${err.message}</p>`;
  } finally {
    if (btn) btn.textContent = "🧠 Regenerate AI Insights";
  }
}

function exportEdaJson() {
  if (!currentEdaData) {
    alert("No EDA data available to export.");
    return;
  }
  const jsonStr = JSON.stringify(currentEdaData, null, 2);
  const blob = new Blob([jsonStr], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${currentFolderName || "dataset"}_eda_analytics.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function formatMarkdown(text) {
  let html = escapeHtml(text);

  // Headers
  html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
  html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
  html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');

  // Bold
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

  // Bullet points
  html = html.replace(/^\- (.*$)/gim, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');

  // Numbered lists
  html = html.replace(/^[0-9]+\.\s+(.*$)/gim, '<li>$1</li>');

  // Line breaks to paragraphs
  html = html.split('\n\n').map(p => {
    p = p.trim();
    if (!p) return '';
    if (p.startsWith('<h') || p.startsWith('<ul') || p.startsWith('<ol') || p.startsWith('<li')) return p;
    return `<p>${p}</p>`;
  }).join('');

  return html;
}

// 9. AI Cross-Checker & Self-Healing Modal Handling
function initAiCrosscheckModal() {
  const modal = document.getElementById("aiCrosscheckModal");
  const closeBtn = document.getElementById("aiCrosscheckCloseBtn");
  const dismissBtn = document.getElementById("aiCrosscheckDismissBtn");
  const runBtn = document.getElementById("btnCrossCheckCloud");
  const filterBtn = document.getElementById("filterOnlyVerifiedBtn");
  const applyAllBtn = document.getElementById("applyAllVerifiedPatchesBtn");

  const progressWrap = document.getElementById("aiAuditProgressWrap");
  const progressBar = document.getElementById("aiAuditProgressBar");
  const progressPct = document.getElementById("aiAuditProgressPct");
  const progressMsg = document.getElementById("aiAuditProgressMsg");

  const statAudited = document.getElementById("aiStatAudited");
  const statVerified = document.getElementById("aiStatVerified");
  const statRejected = document.getElementById("aiStatRejected");
  const statApplied = document.getElementById("aiStatApplied");

  const schemaStatusPill = document.getElementById("schemaStatusPill");
  const schemaBadge = document.getElementById("schemaBadge");
  const schemaSummaryText = document.getElementById("schemaSummaryText");

  const cardsContainer = document.getElementById("aiCardsContainer");
  const diffCardsCount = document.getElementById("diffCardsCount");

  function openModal() {
    if (modal) modal.style.display = "flex";
  }

  function closeModal() {
    if (modal) modal.style.display = "none";
  }

  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (dismissBtn) dismissBtn.addEventListener("click", closeModal);

  if (filterBtn) {
    filterBtn.addEventListener("click", () => {
      showOnlyVerified = !showOnlyVerified;
      filterBtn.classList.toggle("active", showOnlyVerified);
      filterBtn.textContent = showOnlyVerified ? "Show All Cards" : "Show Verified Only";
      renderAuditCards();
    });
  }

  if (runBtn) {
    runBtn.addEventListener("click", async () => {
      if (!currentFolderName) {
        alert("Please extract or select a dataset first.");
        return;
      }

      openModal();

      // If simultaneous extraction was used and audit is already present
      if (currentResultObj && currentResultObj.ai_crosscheck_summary && currentResultObj.ai_crosscheck_summary.success) {
        if (progressWrap) progressWrap.style.display = "none";
        renderAuditReport(currentResultObj.ai_crosscheck_summary);
        return;
      }

      // Otherwise, run on-demand post-extraction cross-check
      if (progressWrap) {
        progressWrap.style.display = "block";
        if (progressBar) progressBar.style.width = "5%";
        if (progressPct) progressPct.textContent = "5%";
        if (progressMsg) progressMsg.textContent = "Starting Cloud AI cross-checker...";
      }

      try {
        const res = await fetch("/api/ai/cross-check/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ folder_name: currentFolderName })
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          throw new Error(data.error || "Failed to start AI cross-check.");
        }

        // Poll job progress
        const jobResult = await pollAiCrosscheckJob(data.job_id);
        if (progressWrap) progressWrap.style.display = "none";
        if (currentResultObj) currentResultObj.ai_crosscheck_summary = jobResult;
        renderAuditReport(jobResult);

      } catch (err) {
        console.error("AI cross-check error:", err);
        if (progressWrap) progressWrap.style.display = "none";
        alert("AI Cross-Check failed: " + err.message);
      }
    });
  }

  function pollAiCrosscheckJob(jobId) {
    return new Promise((resolve, reject) => {
      const interval = 500;
      const maxPolls = 600;
      let count = 0;

      const poll = async () => {
        count++;
        if (count > maxPolls) {
          reject(new Error("AI cross-check timed out after 5 minutes."));
          return;
        }

        try {
          const res = await fetch(`/api/ai/cross-check/jobs/${encodeURIComponent(jobId)}`);
          if (!res.ok) {
            setTimeout(poll, interval);
            return;
          }
          const data = await res.json();
          if (!data.success) {
            reject(new Error(data.error || "Cross-check failed"));
            return;
          }

          if (progressBar) progressBar.style.width = `${data.progress}%`;
          if (progressPct) progressPct.textContent = `${data.progress}%`;
          if (progressMsg) progressMsg.textContent = data.message || "Auditing...";

          // Stream live audit logs to terminal console
          if (data.audit_logs && data.audit_logs.length > 0) {
            updateAuditLogs(data.audit_logs);
          }

          // Stream live token metrics
          if (data.token_usage) {
            const inTok = data.token_usage.input || 0;
            const outTok = data.token_usage.output || 0;
            const totTok = data.token_usage.total || (inTok + outTok);
            const aiStatTokens = document.getElementById("aiStatTokens");
            if (aiStatTokens) {
              aiStatTokens.textContent = `${inTok.toLocaleString()} in / ${outTok.toLocaleString()} out (${totTok.toLocaleString()} tot)`;
            }
            const telemTokens = document.getElementById("telemTokens");
            if (telemTokens) {
              telemTokens.textContent = `In: ${inTok.toLocaleString()} | Out: ${outTok.toLocaleString()} (Tot: ${totTok.toLocaleString()})`;
            }
          }

          // Stream discrepancy cards live
          if (data.discrepancies && data.discrepancies.length > 0) {
            activeAuditReport = {
              total_students: currentRecords.length,
              verified_patches: data.discrepancies.filter(d => d.sandbox_status === "VERIFIED").length,
              rejected_patches: data.discrepancies.filter(d => d.sandbox_status === "REJECTED").length,
              applied_patches: data.discrepancies.filter(d => d.applied).length,
              discrepancies: data.discrepancies
            };
            renderAuditCards();
          }

          if (data.status === "completed") {
            resolve(data.result);
          } else if (data.status === "failed") {
            reject(new Error(data.error || "AI cross-check worker failed."));
          } else {
            setTimeout(poll, interval);
          }
        } catch (e) {
          setTimeout(poll, interval);
        }
      };

      setTimeout(poll, interval);
    });
  }

  function renderAuditReport(report) {
    activeAuditReport = report;
    if (!report) return;

    if (statAudited) statAudited.textContent = report.total_students || currentRecords.length;
    if (statVerified) statVerified.textContent = report.verified_patches || 0;
    if (statRejected) statRejected.textContent = report.rejected_patches || 0;
    if (statApplied) statApplied.textContent = report.applied_patches || 0;

    const aiStatTokens = document.getElementById("aiStatTokens");
    if (aiStatTokens) {
      const tu = report.token_usage || (currentResultObj && currentResultObj.token_usage);
      if (tu && (tu.total > 0 || tu.input > 0)) {
        const inTok = tu.input || 0;
        const outTok = tu.output || 0;
        const totTok = tu.total || (inTok + outTok);
        aiStatTokens.textContent = `${inTok.toLocaleString()} in / ${outTok.toLocaleString()} out (${totTok.toLocaleString()} tot)`;
      } else {
        aiStatTokens.textContent = "--";
      }
    }

    // Schema pill
    if (schemaStatusPill && report.schema_audit) {
      schemaStatusPill.style.display = "flex";
      const s = report.schema_audit;
      if (s.status === "ALIGNED") {
        schemaBadge.className = "schema-badge";
        schemaBadge.textContent = "SCHEMA: ALIGNED";
        schemaSummaryText.textContent = s.summary || "All extracted columns match ledger headers.";
      } else if (s.status === "DISCREPANCY_DETECTED") {
        schemaBadge.className = "schema-badge";
        schemaBadge.style.background = "#eab308";
        schemaBadge.textContent = "SCHEMA: DISCREPANCY";
        schemaSummaryText.textContent = s.summary || "Column alignment discrepancy detected.";
      } else {
        schemaStatusPill.style.display = "none";
      }
    }

    const unappliedVerified = (report.discrepancies || []).filter(d => d.sandbox_status === "VERIFIED" && !d.applied);
    if (applyAllBtn) {
      applyAllBtn.disabled = unappliedVerified.length === 0;
      applyAllBtn.textContent = `✓ Accept All Verified Patches (${unappliedVerified.length})`;
    }

    renderAuditCards();
  }

  function renderAuditCards() {
    if (!cardsContainer) return;
    cardsContainer.innerHTML = "";

    const discrepancies = (activeAuditReport && activeAuditReport.discrepancies) ? activeAuditReport.discrepancies : [];
    const filtered = showOnlyVerified ? discrepancies.filter(d => d.sandbox_status === "VERIFIED") : discrepancies;

    if (diffCardsCount) diffCardsCount.textContent = filtered.length;

    if (filtered.length === 0) {
      cardsContainer.innerHTML = `
        <div class="ai-empty-state">
          ${discrepancies.length === 0 
            ? "No discrepancies detected. All student records match the source document text!" 
            : "No cards match the active filter."}
        </div>
      `;
      return;
    }

    filtered.forEach((card, idx) => {
      const el = document.createElement("div");
      const isVerified = card.sandbox_status === "VERIFIED";
      const isApplied = card.applied;
      const isSchema = card.card_type === "schema";
      el.className = `ai-card ${isApplied ? "applied" : (isVerified ? "verified" : "rejected")}${isSchema ? " schema-card" : ""}`;
      el.id = `ai_card_${idx}`;

      const extractedText = card.extracted_val !== null && card.extracted_val !== undefined ? String(card.extracted_val) : "(empty)";
      const suggestedText = card.suggested_val !== null && card.suggested_val !== undefined ? String(card.suggested_val) : "(empty)";
      const locationText = isSchema 
        ? `📍 Location: PDF Page ${card.page_number || 1} (Course Catalog Header Table)` 
        : `📍 Location: PDF Page ${card.page_number || 1}`;

      el.innerHTML = `
        <div class="ai-card-header">
          <div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;">
            ${isSchema 
              ? `<span class="ai-card-seat" style="background:#8b5cf6;color:#fff;">📋 SCHEMA MISMATCH</span>` 
              : `<span class="ai-card-seat">Seat #${escapeHtml(card.seat_no)}</span>`}
            <span class="ai-card-field-tag">${escapeHtml(card.field_name || card.field)}</span>
            <span class="ai-card-location" style="font-size:0.75rem;color:#38bdf8;font-weight:600;">${locationText}</span>
          </div>
          <span class="sandbox-pill ${isVerified ? "verified" : "rejected"}">
            ${isVerified ? (isSchema ? "✓ Verified in PDF Header" : "✓ Ground-Truth Verified") : "✕ Sandbox Rejected"}
          </span>
        </div>
        <div class="ai-card-body">
          <div class="ai-diff-row">
            <span class="ai-diff-val-removed">${escapeHtml(extractedText)}</span>
            <span class="ai-diff-arrow">&rarr;</span>
            <span class="ai-diff-val-added">${escapeHtml(suggestedText)}</span>
          </div>
          <div class="ai-diff-reason"><strong>Error Diagnostic:</strong> ${escapeHtml(card.reason || "Proofreading discrepancy found")}</div>
          ${card.evidence_quote ? `<div class="ai-diff-quote" title="Verbatim quote from PDF source text"><strong>Exact Excerpt in PDF:</strong> &ldquo;${escapeHtml(card.evidence_quote)}&rdquo;</div>` : ""}
          ${!isVerified && card.sandbox_reason ? `<div style="font-size:0.72rem; color:var(--accent-red,#ef4444);">Reason: ${escapeHtml(card.sandbox_reason)}</div>` : ""}
        </div>
        <div class="ai-card-footer">
          <span style="color:var(--text-muted);">Status: ${isApplied ? "<strong style='color:var(--primary);'>Applied to Dataset</strong>" : (isVerified ? (isSchema ? "Documented in Audit" : "Ready to apply") : "Rejected (Hallucination Safe)")}</span>
          <div class="ai-card-actions">
            ${!isApplied && isVerified && !isSchema ? `
              <button type="button" class="btn-card-accept" data-card-idx="${idx}">✓ Accept</button>
            ` : ""}
            ${!isApplied ? `
              <button type="button" class="btn-card-reject" data-card-idx="${idx}">✕ Dismiss</button>
            ` : ""}
          </div>
        </div>
      `;

      const acceptBtn = el.querySelector(".btn-card-accept");
      if (acceptBtn) {
        acceptBtn.addEventListener("click", () => {
          applySinglePatch(card, idx);
        });
      }
      const dismissCardBtn = el.querySelector(".btn-card-reject");
      if (dismissCardBtn) {
        dismissCardBtn.addEventListener("click", () => {
          el.style.display = "none";
        });
      }

      cardsContainer.appendChild(el);
    });
  }

  async function applySinglePatch(card, idx) {
    if (!currentFolderName) return;
    const patch = {
      seat_no: card.seat_no,
      field: card.field_name || card.field,
      suggested_val: card.suggested_val
    };

    try {
      const res = await fetch("/api/ai/cross-check/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          folder_name: currentFolderName,
          patches: [patch]
        })
      });
      const data = await res.json();
      if (data.success) {
        card.applied = true;
        if (activeAuditReport) {
          activeAuditReport.applied_patches = (activeAuditReport.applied_patches || 0) + 1;
        }
        // Update local memory record
        const st = currentRecords.find(s => String(s.Seat_No || s.seat_no) === String(card.seat_no));
        if (st) {
          st[patch.field] = patch.suggested_val;
        }
        filteredRecords = [...currentRecords];
        displayTablePage();
        renderAuditReport(activeAuditReport);
      } else {
        alert("Failed to apply patch: " + (data.error || "Unknown error"));
      }
    } catch (e) {
      alert("Error applying patch: " + e.message);
    }
  }

  if (applyAllBtn) {
    applyAllBtn.addEventListener("click", async () => {
      if (!activeAuditReport || !currentFolderName) return;
      const unapplied = (activeAuditReport.discrepancies || []).filter(d => d.sandbox_status === "VERIFIED" && !d.applied);
      if (unapplied.length === 0) return;

      const patches = unapplied.map(d => ({
        seat_no: d.seat_no,
        field: d.field_name || d.field,
        suggested_val: d.suggested_val
      }));

      applyAllBtn.disabled = true;
      applyAllBtn.textContent = "Applying patches...";

      try {
        const res = await fetch("/api/ai/cross-check/apply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            folder_name: currentFolderName,
            patches: patches
          })
        });
        const data = await res.json();
        if (data.success) {
          unapplied.forEach(d => { d.applied = true; });
          activeAuditReport.applied_patches = (activeAuditReport.applied_patches || 0) + data.applied_count;
          // Update currentRecords in memory
          patches.forEach(p => {
            const st = currentRecords.find(s => String(s.Seat_No || s.seat_no) === String(p.seat_no));
            if (st) st[p.field] = p.suggested_val;
          });
          filteredRecords = [...currentRecords];
          displayTablePage();
          renderAuditReport(activeAuditReport);
        } else {
          alert("Failed to apply patches: " + (data.error || "Unknown error"));
        }
      } catch (e) {
        alert("Error applying patches: " + e.message);
      } finally {
        applyAllBtn.disabled = false;
      }
    });
  }
}

// =========================================================================
// Tabular File (Excel & CSV) Inspection & Ingestion Controller
// =========================================================================

let currentTabularFile = null;
let currentTabularInspection = null;

async function safeParseJson(res) {
  const rawText = await res.text();
  try {
    const sanitized = rawText
      .replace(/:\s*NaN\b/g, ': ""')
      .replace(/:\s*Infinity\b/g, ': null')
      .replace(/:\s*-Infinity\b/g, ': null');
    return JSON.parse(sanitized);
  } catch (err) {
    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}: ${res.statusText || 'Internal Server Error'}`);
    }
    console.error("JSON parse failure on raw text:", rawText);
    throw new Error("Invalid response format: " + err.message);
  }
}

function initTabularModal() {
  const modalBackdrop = document.getElementById("tabularModalBackdrop");
  const closeBtn = document.getElementById("btnTabModalClose");
  const btnDirect = document.getElementById("btnTabDirectImport");
  const btnAiFix = document.getElementById("btnTabAiFix");
  const sheetSelect = document.getElementById("tabSheetSelect");

  if (!modalBackdrop) return;

  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      modalBackdrop.classList.add("hidden");
    });
  }

  modalBackdrop.addEventListener("click", (e) => {
    if (e.target === modalBackdrop) {
      modalBackdrop.classList.add("hidden");
    }
  });

  if (sheetSelect) {
    sheetSelect.addEventListener("change", async (e) => {
      const selectedSheet = e.target.value;
      if (currentTabularInspection && currentTabularInspection.filename) {
        await reInspectSheet(currentTabularInspection.filename, selectedSheet);
      }
    });
  }

  if (btnDirect) {
    btnDirect.addEventListener("click", async () => {
      if (!currentTabularInspection || !currentTabularInspection.filename) return;
      const originalText = btnDirect.innerHTML;
      btnDirect.disabled = true;
      btnDirect.innerHTML = `<span>⏳ Importing...</span>`;

      try {
        const payload = {
          filename: currentTabularInspection.filename,
          sheet_name: currentTabularInspection.selected_sheet || (sheetSelect ? sheetSelect.value : null),
          header_row: currentTabularInspection.detected_header_row
        };
        const res = await fetch("/api/tabular/import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await safeParseJson(res);
        if (data.success) {
          modalBackdrop.classList.add("hidden");
          renderResults(data);
          appendChatMessage("assistant", `📥 <strong>Direct Import Successful!</strong> Loaded <strong>${data.total_students}</strong> records directly from <em>${escapeHtml(currentTabularInspection.filename)}</em> without consuming any AI tokens. Explore the data table, ask questions, or review the visual EDA analytics!`);
        } else {
          alert(`Direct import failed: ${data.error || "Unknown error"}`);
        }
      } catch (err) {
        console.error("Direct import error:", err);
        alert(`Error importing tabular file: ${err.message}`);
      } finally {
        btnDirect.disabled = false;
        btnDirect.innerHTML = originalText;
      }
    });
  }

  if (btnAiFix) {
    btnAiFix.addEventListener("click", async () => {
      if (!currentTabularInspection || !currentTabularInspection.filename) return;
      const originalText = btnAiFix.innerHTML;
      btnAiFix.disabled = true;
      btnAiFix.innerHTML = `<span>✨ Restructuring with AI...</span>`;

      const pipelineSelect = document.getElementById("tabAiPipelineSelect");
      const pipelineMode = pipelineSelect ? pipelineSelect.value : "local";

      try {
        const payload = {
          filename: currentTabularInspection.filename,
          sheet_name: currentTabularInspection.selected_sheet || (sheetSelect ? sheetSelect.value : null),
          pipeline_mode: pipelineMode
        };
        const res = await fetch("/api/tabular/ai-fix", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await safeParseJson(res);
        if (data.success) {
          modalBackdrop.classList.add("hidden");
          renderResults(data);
          const tokStr = data.token_usage && data.token_usage.total > 0 ? ` (${data.token_usage.total} tokens used)` : "";
          appendChatMessage("assistant", `🪄 <strong>AI Structure Normalization Complete!</strong> Extracted <strong>${data.total_students}</strong> student records from <em>${escapeHtml(currentTabularInspection.filename)}</em> using <strong>${escapeHtml(data.engine_used || 'AI')}</strong>${tokStr}. Multi-tier headers merged and student identities mapped.`);
        } else {
          alert(`AI fix failed: ${data.error || "Unknown error"}`);
        }
      } catch (err) {
        console.error("AI fix error:", err);
        alert(`Error during AI fix: ${err.message}`);
      } finally {
        btnAiFix.disabled = false;
        btnAiFix.innerHTML = originalText;
      }
    });
  }
}

async function inspectTabularBatchFile(batchRelPath) {
  const modalBackdrop = document.getElementById("tabularModalBackdrop");
  if (!modalBackdrop) return;

  const baseName = batchRelPath.split("/").pop().split("\\").pop();
  currentTabularFile = { name: baseName, rel_path: batchRelPath };
  modalBackdrop.classList.remove("hidden");

  const filenameEl = document.getElementById("tabModalFilename");
  const subEl = document.getElementById("tabModalSubtitle");
  const statusTitle = document.getElementById("tabStatusTitle");
  const msgEl = document.getElementById("tabAssessmentMsg");
  const issuesContainer = document.getElementById("tabIssuesContainer");
  const thead = document.getElementById("tabPreviewHeadRow");
  const tbody = document.getElementById("tabPreviewBody");
  const sheetBar = document.getElementById("tabSheetBar");

  if (filenameEl) filenameEl.textContent = baseName;
  if (subEl) subEl.textContent = `Historical Batch: ${batchRelPath} • Ingesting...`;
  if (statusTitle) statusTitle.textContent = "Analyzing historical spreadsheet formatting & layout...";
  if (msgEl) msgEl.textContent = "Inspecting headers, row offsets, composite sub-components, and student markers...";
  if (issuesContainer) issuesContainer.classList.add("hidden");
  if (thead) thead.innerHTML = "";
  if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 1rem;">Reading table preview...</td></tr>`;
  if (sheetBar) sheetBar.classList.add("hidden");

  try {
    const res = await fetch("/api/tabular/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: batchRelPath, batch_file: batchRelPath })
    });
    const data = await safeParseJson(res);
    if (!data.success) {
      alert(`Inspection failed: ${data.error || "Unknown error"}`);
      modalBackdrop.classList.add("hidden");
      return;
    }

    renderInspectionData(data);
  } catch (err) {
    console.error("Historical tabular inspection failed:", err);
    alert(`Could not inspect tabular batch file: ${err.message}`);
    modalBackdrop.classList.add("hidden");
  }
}

async function inspectTabularFile(file) {
  const modalBackdrop = document.getElementById("tabularModalBackdrop");
  if (!modalBackdrop) return;

  currentTabularFile = file;
  modalBackdrop.classList.remove("hidden");

  const filenameEl = document.getElementById("tabModalFilename");
  const subEl = document.getElementById("tabModalSubtitle");
  const statusTitle = document.getElementById("tabStatusTitle");
  const statusPill = document.getElementById("tabStatusPill");
  const banner = document.getElementById("tabAssessmentBanner");
  const scoreVal = document.getElementById("tabScoreVal");
  const scorePill = document.getElementById("tabScorePill");
  const msgEl = document.getElementById("tabAssessmentMsg");
  const issuesContainer = document.getElementById("tabIssuesContainer");
  const statsEl = document.getElementById("tabMetaStats");
  const thead = document.getElementById("tabPreviewHeadRow");
  const tbody = document.getElementById("tabPreviewBody");
  const sheetBar = document.getElementById("tabSheetBar");
  const sheetSelect = document.getElementById("tabSheetSelect");

  if (filenameEl) filenameEl.textContent = file.name;
  if (subEl) subEl.textContent = `Size: ${(file.size / 1024).toFixed(1)} KB • Ingesting...`;
  if (statusTitle) statusTitle.textContent = "Analyzing spreadsheet formatting & layout...";
  if (msgEl) msgEl.textContent = "Inspecting headers, row offsets, composite sub-components, and student markers...";
  if (issuesContainer) issuesContainer.classList.add("hidden");
  if (thead) thead.innerHTML = "";
  if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 1rem;">Reading table preview...</td></tr>`;

  try {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch("/api/tabular/inspect", {
      method: "POST",
      body: formData
    });
    const data = await safeParseJson(res);
    if (!data.success) {
      alert(`Inspection failed: ${data.error || "Unknown error"}`);
      modalBackdrop.classList.add("hidden");
      return;
    }

    renderInspectionData(data);
  } catch (err) {
    console.error("Tabular inspection failed:", err);
    alert(`Could not inspect tabular file: ${err.message}`);
    modalBackdrop.classList.add("hidden");
  }
}

async function reInspectSheet(filename, sheetName) {
  try {
    const res = await fetch("/api/tabular/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: filename, sheet_name: sheetName })
    });
    const data = await safeParseJson(res);
    if (data.success) {
      renderInspectionData(data);
    }
  } catch (err) {
    console.error("Error reinspecting sheet:", err);
  }
}

function renderInspectionData(data) {
  currentTabularInspection = data;

  const filenameEl = document.getElementById("tabModalFilename");
  const subEl = document.getElementById("tabModalSubtitle");
  const statusTitle = document.getElementById("tabStatusTitle");
  const statusPill = document.getElementById("tabStatusPill");
  const banner = document.getElementById("tabAssessmentBanner");
  const scoreVal = document.getElementById("tabScoreVal");
  const scorePill = document.getElementById("tabScorePill");
  const msgEl = document.getElementById("tabAssessmentMsg");
  const issuesContainer = document.getElementById("tabIssuesContainer");
  const statsEl = document.getElementById("tabMetaStats");
  const thead = document.getElementById("tabPreviewHeadRow");
  const tbody = document.getElementById("tabPreviewBody");
  const sheetBar = document.getElementById("tabSheetBar");
  const sheetSelect = document.getElementById("tabSheetSelect");

  const cardDirect = document.getElementById("cardDirectImport");
  const cardAi = document.getElementById("cardAiFix");
  const badgeDirect = document.getElementById("badgeDirectRec");
  const badgeAi = document.getElementById("badgeAiRec");

  if (filenameEl) filenameEl.textContent = data.filename;
  if (subEl) subEl.textContent = `${data.file_type.toUpperCase()} • ${data.total_raw_rows} raw rows • ${data.total_raw_cols} columns`;

  // Worksheets
  if (sheetBar && sheetSelect && data.sheets && data.sheets.length > 1) {
    sheetBar.classList.remove("hidden");
    sheetSelect.innerHTML = "";
    data.sheets.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      if (s === data.selected_sheet) opt.selected = true;
      sheetSelect.appendChild(opt);
    });
  } else if (sheetBar) {
    sheetBar.classList.add("hidden");
  }

  // Cleanliness Score & Assessment
  if (scoreVal) scoreVal.textContent = data.cleanliness_score;
  if (scorePill) {
    scorePill.className = "assessment-score-pill";
    if (data.cleanliness_score >= 80) scorePill.classList.add("score-high");
    else if (data.cleanliness_score >= 50) scorePill.classList.add("score-mid");
    else scorePill.classList.add("score-low");
  }

  if (banner) {
    banner.className = "tabular-assessment-banner " + (data.is_clean ? "banner-clean" : "banner-warning");
  }

  if (statusTitle) {
    statusTitle.textContent = data.is_clean ? "✅ Clean Tabular Data Detected" : "⚠️ Unformatted / Complex Ledger Detected";
  }

  if (msgEl) {
    if (data.is_clean) {
      msgEl.textContent = "All column headers and rows appear clean and rectangular. Direct Import is recommended to save tokens and import instantly.";
    } else {
      msgEl.textContent = "Structural irregularities detected. AI Restructuring is recommended to align headers, resolve multi-tier columns, and clean student records.";
    }
  }

  // Issues Chips
  if (issuesContainer) {
    issuesContainer.innerHTML = "";
    if (data.detected_issues && data.detected_issues.length > 0) {
      issuesContainer.classList.remove("hidden");
      data.detected_issues.forEach(issue => {
        const chip = document.createElement("span");
        chip.className = "issue-chip";
        chip.innerHTML = `⚠️ ${escapeHtml(issue)}`;
        issuesContainer.appendChild(chip);
      });
    } else {
      issuesContainer.classList.add("hidden");
    }
  }

  // Recommendation Highlighting
  if (data.is_clean) {
    if (cardDirect) cardDirect.className = "tabular-action-card card-recommended-direct";
    if (cardAi) cardAi.className = "tabular-action-card";
    if (badgeDirect) badgeDirect.classList.remove("hidden");
    if (badgeAi) badgeAi.classList.add("hidden");
  } else {
    if (cardDirect) cardDirect.className = "tabular-action-card";
    if (cardAi) cardAi.className = "tabular-action-card card-recommended-ai";
    if (badgeDirect) badgeDirect.classList.add("hidden");
    if (badgeAi) badgeAi.classList.remove("hidden");
  }

  // Preview Stats
  if (statsEl) {
    statsEl.textContent = `${data.total_raw_rows} rows • ${data.total_raw_cols} columns`;
  }

  // Render Preview Table
  if (thead) {
    thead.innerHTML = "";
    (data.preview_headers || []).forEach(h => {
      const th = document.createElement("th");
      th.textContent = h;
      thead.appendChild(th);
    });
  }

  if (tbody) {
    tbody.innerHTML = "";
    (data.preview_rows || []).forEach(r => {
      const tr = document.createElement("tr");
      r.forEach(c => {
        const td = document.createElement("td");
        td.textContent = c !== null && c !== undefined ? c : "";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }
}

// ==========================================================================
// BATCH MULTI-SEMESTER ANALYSIS SUITE (SEM 1 TO 8) FRONTEND CONTROLLER
// ==========================================================================

let activeBatchAnalytics = null;
let activeBatchJobId = null;
let batchPollInterval = null;

function initBatchSuite() {
  const btnSingle = document.getElementById("btnModeSingle");
  const btnBatch = document.getElementById("btnModeBatch");
  const singleSec = document.getElementById("singleWorkspaceSection");
  const batchSec = document.getElementById("batchWorkspaceSection");
  const batchProgressSec = document.getElementById("batchProgressSection");
  const batchResultsSec = document.getElementById("batchResultsSection");
  const singleResultsSec = document.getElementById("resultsSection");
  const singleChatSec = document.getElementById("chatSection");
  const singleAuditSec = document.getElementById("aiAuditSection");

  if (!btnSingle || !btnBatch) return;

  // 1. Mode Switching via Router
  btnSingle.addEventListener("click", () => {
    navigateTo("/extract");
    if (singleSec) singleSec.classList.remove("hidden");
    if (batchSec) batchSec.classList.add("hidden");
    if (batchProgressSec) batchProgressSec.classList.add("hidden");
    if (batchResultsSec) batchResultsSec.classList.add("hidden");
    if (singleResultsSec && currentRecords.length > 0) singleResultsSec.classList.remove("hidden");
    if (singleChatSec && currentRecords.length > 0) singleChatSec.classList.remove("hidden");
    if (singleAuditSec) singleAuditSec.classList.remove("hidden");
  });

  btnBatch.addEventListener("click", () => {
    navigateTo("/analysis");
    if (singleSec) singleSec.classList.add("hidden");
    if (batchSec) batchSec.classList.remove("hidden");
    if (singleResultsSec) singleResultsSec.classList.add("hidden");
    if (singleChatSec) singleChatSec.classList.add("hidden");
    if (singleAuditSec) singleAuditSec.classList.add("hidden");
    if (batchResultsSec && activeBatchAnalytics) batchResultsSec.classList.remove("hidden");

    loadBatchSuiteDatasets();
  });

  // 2. Cascading Batch Dropdowns & Inspect Trigger
  let cachedBatchHierarchy = [];

  const branchSelect = document.getElementById("batchBranchSelect");
  const yearSelect = document.getElementById("batchYearSelect");
  const semesterSelect = document.getElementById("batchSemesterSelect");
  const batchSelect = document.getElementById("batchSuiteSelect");
  const branchInput = document.getElementById("batchBranchInput");
  const btnInspect = document.getElementById("btnInspectBatch");
  const btnRun = document.getElementById("btnRunBatchAnalysis");

  if (branchSelect) {
    branchSelect.addEventListener("change", () => {
      onBatchBranchChanged(branchSelect.value);
    });
  }

  if (yearSelect) {
    yearSelect.addEventListener("change", () => {
      onBatchYearChanged(branchSelect ? branchSelect.value : "", yearSelect.value);
    });
  }

  if (semesterSelect) {
    semesterSelect.addEventListener("change", () => {
      onBatchSemesterChanged(semesterSelect.value);
    });
  }

  if (batchSelect) {
    batchSelect.addEventListener("change", () => {
      inspectCurrentBatchSelection();
    });
  }

  if (btnInspect) {
    btnInspect.addEventListener("click", () => {
      inspectCurrentBatchSelection();
    });
  }

  // Sync Excel mode selects if either changes
  const modeSelect1 = document.getElementById("batchExcelModeSelect");
  const modeSelect2 = document.getElementById("batchExcelModeSelectMain");
  if (modeSelect1 && modeSelect2) {
    modeSelect1.addEventListener("change", (e) => { modeSelect2.value = e.target.value; });
    modeSelect2.addEventListener("change", (e) => { modeSelect1.value = e.target.value; });
  }

  // 3. Run Analysis Button
  if (btnRun) {
    btnRun.addEventListener("click", () => {
      runBatchAnalysisAction();
    });
  }

  // 4. Batch Results Tab Navigation
  const batchTabBtns = document.querySelectorAll(".batch-tab-btn");
  batchTabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetId = btn.getAttribute("data-batch-tab");
      batchTabBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");

      document.querySelectorAll(".batch-tab-content").forEach(c => c.classList.add("hidden"));
      const targetEl = document.getElementById(targetId);
      if (targetEl) targetEl.classList.remove("hidden");
      setTimeout(() => {
        if (typeof batchChartInstances !== "undefined") {
          Object.values(batchChartInstances).forEach(ch => {
            if (ch && typeof ch.resize === "function") ch.resize();
          });
        }
      }, 50);
    });
  });

  // 5. Progression Search Filter
  const progSearch = document.getElementById("batchProgressionSearch");
  if (progSearch) {
    progSearch.addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase().trim();
      const rows = document.querySelectorAll("#batchProgressionBody tr");
      rows.forEach(r => {
        const text = r.textContent.toLowerCase();
        r.style.display = text.includes(q) ? "" : "none";
      });
    });
  }

  // 5b. Success Rate Roster Search Filter
  const successSearch = document.getElementById("batchSuccessRosterSearch");
  if (successSearch) {
    successSearch.addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase().trim();
      const rows = document.querySelectorAll("#batchSuccessRosterBody tr");
      rows.forEach(r => {
        const text = r.textContent.toLowerCase();
        r.style.display = text.includes(q) ? "" : "none";
      });
    });
  }

  // 6. Raw Data Semester Dropdown
  const rawSemSelect = document.getElementById("batchRawSemSelect");
  if (rawSemSelect) {
    rawSemSelect.addEventListener("change", (e) => {
      const sem = parseInt(e.target.value, 10);
      renderRawSemesterTable(sem);
    });
  }
}

async function loadBatchSuiteDatasets() {
  const branchSelect = document.getElementById("batchBranchSelect");
  const batchSelect = document.getElementById("batchSuiteSelect");

  try {
    const res = await fetch("/api/batch-analysis/hierarchy");
    const data = await res.json();
    if (data.success && Array.isArray(data.branches)) {
      cachedBatchHierarchy = data.branches;

      // Populate Branch Select
      if (branchSelect) {
        const prevBranch = branchSelect.value;
        branchSelect.innerHTML = '<option value="">-- Select Branch / Department --</option>';
        cachedBatchHierarchy.forEach(b => {
          const opt = document.createElement("option");
          opt.value = b.id;
          opt.textContent = `${b.display_name} (${b.years.length} Academic Years)`;
          branchSelect.appendChild(opt);
        });

        const targetBranch = (prevBranch && cachedBatchHierarchy.some(b => b.id === prevBranch))
          ? prevBranch
          : (cachedBatchHierarchy.length > 0 ? cachedBatchHierarchy[0].id : "");

        if (targetBranch) {
          branchSelect.value = targetBranch;
          onBatchBranchChanged(targetBranch);
        }
      }

      // Populate legacy batchSelect for backward compatibility
      if (batchSelect && Array.isArray(data.batches)) {
        batchSelect.innerHTML = '<option value="">-- Choose Historical Batch Folder --</option>';
        data.batches.forEach(b => {
          const opt = document.createElement("option");
          opt.value = b.batch_name;
          opt.textContent = b.display_name || b.batch_name;
          batchSelect.appendChild(opt);
        });
      }
    }
  } catch (err) {
    console.warn("Failed to load batch hierarchy:", err);
  }
}

function onBatchBranchChanged(branchId) {
  const yearSelect = document.getElementById("batchYearSelect");
  const semesterSelect = document.getElementById("batchSemesterSelect");
  const branchInput = document.getElementById("batchBranchInput");

  if (branchInput) branchInput.value = branchId || "AIDS";

  if (!yearSelect) return;

  if (!branchId) {
    yearSelect.innerHTML = '<option value="">-- Select Academic Year --</option>';
    yearSelect.disabled = true;
    if (semesterSelect) {
      semesterSelect.innerHTML = '<option value="all">⚡ All Semesters (Sem 1–8 Full Suite)</option>';
      semesterSelect.disabled = true;
    }
    return;
  }

  const branchObj = cachedBatchHierarchy.find(b => b.id === branchId);
  if (!branchObj || !Array.isArray(branchObj.years) || branchObj.years.length === 0) {
    yearSelect.innerHTML = '<option value="">-- No Academic Years Found --</option>';
    yearSelect.disabled = true;
    return;
  }

  yearSelect.innerHTML = '<option value="">-- Select Academic Year --</option>';
  branchObj.years.forEach(y => {
    const opt = document.createElement("option");
    opt.value = y.year;
    opt.textContent = `${y.year} (${y.total_semesters} Semesters: ${y.excel_count} Excel, ${y.pdf_count} PDF)`;
    yearSelect.appendChild(opt);
  });

  yearSelect.disabled = false;
  // Select first year by default
  yearSelect.value = branchObj.years[0].year;
  onBatchYearChanged(branchId, branchObj.years[0].year);
}

function onBatchYearChanged(branchId, yearVal) {
  const semesterSelect = document.getElementById("batchSemesterSelect");
  const batchSelect = document.getElementById("batchSuiteSelect");

  if (!yearVal) {
    if (semesterSelect) {
      semesterSelect.innerHTML = '<option value="all">⚡ All Semesters (Sem 1–8 Full Suite)</option>';
      semesterSelect.disabled = true;
    }
    return;
  }

  const branchObj = cachedBatchHierarchy.find(b => b.id === branchId);
  const yearObj = branchObj ? branchObj.years.find(y => y.year === yearVal) : null;

  if (batchSelect && yearObj) {
    batchSelect.value = yearObj.batch_folder;
  }

  if (semesterSelect && yearObj) {
    semesterSelect.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "all";
    allOpt.textContent = `⚡ All Semesters (${yearObj.total_semesters} Detected: Sem ${yearObj.semesters.join(', ')})`;
    semesterSelect.appendChild(allOpt);

    (yearObj.semesters_detected || []).forEach(s => {
      const opt = document.createElement("option");
      opt.value = String(s.semester);
      opt.textContent = `Semester ${s.semester} (${s.file_type.toUpperCase()}: ${s.filename})`;
      semesterSelect.appendChild(opt);
    });

    semesterSelect.disabled = false;
    semesterSelect.value = "all";
  }

  inspectCurrentBatchSelection();
}

function onBatchSemesterChanged(semVal) {
  // Update visual focus in the 8-semester grid
  for (let s = 1; s <= 8; s++) {
    const card = document.getElementById(`semCard${s}`);
    if (!card) continue;
    card.classList.remove("highlighted", "dimmed");
    if (semVal !== "all") {
      if (String(s) === String(semVal)) {
        card.classList.add("highlighted");
      } else {
        card.classList.add("dimmed");
      }
    }
  }
}

async function inspectBatchSuite(batchName) {
  // Alias for backward compatibility
  inspectCurrentBatchSelection();
}

async function inspectCurrentBatchSelection() {
  const branchSelect = document.getElementById("batchBranchSelect");
  const yearSelect = document.getElementById("batchYearSelect");
  const semesterSelect = document.getElementById("batchSemesterSelect");
  const batchSelect = document.getElementById("batchSuiteSelect");
  const branchInput = document.getElementById("batchBranchInput");
  const btnRun = document.getElementById("btnRunBatchAnalysis");
  const hintEl = document.getElementById("batchReadyHint");
  const detectSummary = document.getElementById("batchDetectSummary");
  const promptBanner = document.getElementById("batchExcelPromptBanner");

  const branch = branchSelect ? branchSelect.value : (branchInput ? branchInput.value : "AIDS");
  const year = yearSelect ? yearSelect.value : "";
  const semVal = semesterSelect ? semesterSelect.value : "all";
  let batchName = (branch && year) ? `${branch}/${year}` : (batchSelect ? batchSelect.value : "");

  if (!batchName) return;

  if (hintEl) hintEl.textContent = `Scanning batch ${batchName}...`;

  try {
    const res = await fetch("/api/batch-analysis/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        batch_name: batchName,
        branch: branch,
        academic_year: year,
        semester: semVal,
        target_branch: branch
      })
    });
    const data = await res.json();
    if (data.success) {
      // Update Sem 1-8 cards
      const semsDetected = data.semesters_detected || [];
      const semMap = {};
      semsDetected.forEach(s => { semMap[s.semester] = s; });

      for (let s = 1; s <= 8; s++) {
        const card = document.getElementById(`semCard${s}`);
        if (!card) continue;
        card.className = "sem-card";
        if (semMap[s]) {
          const info = semMap[s];
          card.classList.add("detected");
          card.innerHTML = `
            <span class="sem-num-badge">Sem ${s}</span>
            <span class="sem-file-type ${info.file_type}">${info.file_type.toUpperCase()}</span>
            <span class="sem-filename-text" title="${info.filename}">${info.filename}</span>
          `;
        } else {
          card.classList.add("missing");
          card.innerHTML = `
            <span class="sem-num-badge">Sem ${s}</span>
            <span class="sem-filename-text" style="color: #64748B;">Not Found</span>
          `;
        }

        if (semVal !== "all") {
          if (String(s) === String(semVal)) {
            card.classList.add("highlighted");
          } else {
            card.classList.add("dimmed");
          }
        }
      }

      // Show/Hide Excel Prompt Banner
      if (promptBanner) {
        if (data.has_excel) {
          promptBanner.classList.remove("hidden");
        } else {
          promptBanner.classList.add("hidden");
        }
      }

      if (detectSummary) {
        detectSummary.classList.remove("hidden");
        detectSummary.textContent = `${data.total_semesters} Semesters Identified (${data.excel_count} Excel, ${data.pdf_count} PDF)`;
      }

      if (btnRun) {
        btnRun.disabled = (data.total_semesters === 0);
      }
      if (hintEl) {
        hintEl.textContent = `${data.total_semesters} Semesters ready for ${data.branch || branch} ${data.academic_year || year} analysis.`;
      }
    } else {
      if (hintEl) hintEl.textContent = data.error || "Failed to inspect batch.";
    }
  } catch (err) {
    if (hintEl) hintEl.textContent = `Error inspecting batch: ${err.message}`;
  }
}

async function runBatchAnalysisAction() {
  const branchSelect = document.getElementById("batchBranchSelect");
  const yearSelect = document.getElementById("batchYearSelect");
  const semesterSelect = document.getElementById("batchSemesterSelect");
  const batchSelect = document.getElementById("batchSuiteSelect");
  const branchInput = document.getElementById("batchBranchInput");
  const pdfSelect = document.getElementById("batchPdfPipelineSelect");
  const excelSelect = document.getElementById("batchExcelModeSelectMain");
  const btnRun = document.getElementById("btnRunBatchAnalysis");
  const progressSec = document.getElementById("batchProgressSection");
  const resultsSec = document.getElementById("batchResultsSection");
  const pMsg = document.getElementById("batchProgressMessage");
  const pBar = document.getElementById("batchProgressBar");
  const pPct = document.getElementById("batchProgressPercentage");

  const branch = branchSelect ? branchSelect.value : (branchInput ? branchInput.value.trim() : "AIDS");
  const year = yearSelect ? yearSelect.value : "";
  const semester = semesterSelect ? semesterSelect.value : "all";
  const batchName = (branch && year) ? `${branch}/${year}` : (batchSelect ? batchSelect.value : "");
  const pdfEngine = pdfSelect ? pdfSelect.value : "local";
  const excelMode = excelSelect ? excelSelect.value : "direct";

  if (!batchName) return;

  btnRun.disabled = true;
  if (resultsSec) resultsSec.classList.add("hidden");
  if (progressSec) progressSec.classList.remove("hidden");
  if (pBar) pBar.style.width = "2%";
  if (pPct) pPct.textContent = "2%";
  if (pMsg) pMsg.textContent = `Initiating analysis for ${branch} ${year || batchName}...`;

  try {
    const res = await fetch("/api/batch-analysis/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        batch_name: batchName,
        branch: branch,
        academic_year: year,
        semester: semester,
        target_branch: branch,
        pdf_pipeline: pdfEngine,
        excel_mode: excelMode
      })
    });
    const data = await res.json();
    if (!data.success || !data.job_id) {
      throw new Error(data.error || "Failed to launch batch analysis.");
    }

    const jobId = data.job_id;
    activeBatchJobId = jobId;

    if (batchPollInterval) clearInterval(batchPollInterval);
    batchPollInterval = setInterval(async () => {
      try {
        const jRes = await fetch(`/api/batch-analysis/jobs/${jobId}`);
        const jData = await jRes.json();
        if (jData.success) {
          const prog = jData.progress || 0;
          if (pBar) pBar.style.width = `${prog}%`;
          if (pPct) pPct.textContent = `${prog}%`;
          if (pMsg) pMsg.textContent = jData.message || "Processing...";

          if (jData.status === "completed") {
            clearInterval(batchPollInterval);
            batchPollInterval = null;
            btnRun.disabled = false;
            setTimeout(() => {
              if (progressSec) progressSec.classList.add("hidden");
              activeBatchAnalytics = jData.result;
              renderBatchDashboard(jData.result, jobId);
            }, 600);
          } else if (jData.status === "error") {
            clearInterval(batchPollInterval);
            batchPollInterval = null;
            btnRun.disabled = false;
            if (pMsg) pMsg.textContent = `Batch Analysis Error: ${jData.error}`;
          }
        }
      } catch (pollErr) {
        console.warn("Polling error:", pollErr);
      }
    }, 1200);

  } catch (err) {
    btnRun.disabled = false;
    if (pMsg) pMsg.textContent = `Failed: ${err.message}`;
  }
}

function renderBatchDashboard(analytics, jobId) {
  const resultsSec = document.getElementById("batchResultsSection");
  if (!resultsSec || !analytics) return;

  resultsSec.classList.remove("hidden");
  resultsSec.scrollIntoView({ behavior: "smooth" });

  const batchMeta = analytics.batch_metadata || {};
  const overall = analytics.overall_summary || {};
  const sems = analytics.available_semesters || [];

  // Update Header & Links
  const titleEl = document.getElementById("batchResultTitle");
  const metaEl = document.getElementById("batchResultMeta");
  const btnExcel = document.getElementById("btnDownloadBatchExcel");
  const btnJson = document.getElementById("btnDownloadBatchJson");

  if (titleEl) titleEl.textContent = `${batchMeta.batch_name || "Degree Cohort"} • Complete Academic Analysis`;
  if (metaEl) metaEl.textContent = `${overall.total_students || 0} Distinct Candidates • ${sems.length} Semesters Analyzed • Completed in ${analytics.duration_seconds || 0}s`;

  if (btnExcel) {
    btnExcel.href = `/api/batch-analysis/download/${jobId}`;
    btnExcel.setAttribute("download", `${batchMeta.batch_name || "Batch"}_Complete_Academic_Analysis.xlsx`);
  }
  if (btnJson) {
    btnJson.href = `/api/batch-analysis/download-json/${jobId}`;
    btnJson.setAttribute("download", `${batchMeta.batch_name || "Batch"}_analysis.json`);
  }

  // 0. Success Rate & Intake Analysis (Tab 1)
  const sr = analytics.success_rate_analysis || {};
  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  setEl("kpiBatchN1", sr.regular_intake_n1 || 0);
  setEl("kpiBatchN3", sr.lateral_entry_n3 || 0);
  setEl("kpiBatchIntake", sr.total_admitted_n1_n3 || 0);
  setEl("kpiBatchPassedFinal", sr.passed_final_sem || 0);
  setEl("kpiBatchSuccessRate", `${sr.success_rate_percentage !== undefined ? sr.success_rate_percentage : 0}%`);
  setEl("kpiBatchSuccessRateNoKt", `${sr.success_rate_no_backlog_percentage !== undefined ? sr.success_rate_no_backlog_percentage : 0}%`);
  setEl("kpiBatchNoKtSub", `${sr.passed_without_backlogs || 0} Cleared Zero KT`);
  setEl("kpiBatchSuccessRateWithKt", `${sr.success_rate_with_backlog_percentage !== undefined ? sr.success_rate_with_backlog_percentage : 0}%`);
  setEl("kpiBatchWithKtSub", `${sr.passed_with_backlogs || 0} Cleared Backlogs`);
  setEl("kpiBatchDiploma", sr.diploma_intake_count !== undefined ? sr.diploma_intake_count : (sr.lateral_entry_n3 || 0));
  setEl("kpiBatchMultipleEntry", sr.multiple_entry_count !== undefined ? sr.multiple_entry_count : 0);
  setEl("kpiBatchMultipleExit", sr.multiple_exit_after_y1_count !== undefined ? sr.multiple_exit_after_y1_count : 0);
  setEl("badgeBatchExitCount", `${sr.multiple_exit_after_y1_count || 0} Students`);
  setEl("badgeBatchEntryCount", `${sr.multiple_entry_count || 0} Students`);

  // Cohort Breakdown Table
  const breakdownBody = document.getElementById("batchSuccessBreakdownBody");
  if (breakdownBody) {
    breakdownBody.innerHTML = "";
    (sr.breakdown || []).forEach(b => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${b.category || ""}</strong></td>
        <td>${b.entry_sem || ""}</td>
        <td style="text-align:right;">${b.admitted !== undefined ? b.admitted : 0}</td>
        <td style="text-align:right;"><span class="badge-pass">${b.passed_final !== undefined ? b.passed_final : 0}</span></td>
        <td style="text-align:right;">${b.passed_no_kt !== undefined ? b.passed_no_kt : 0}</td>
        <td style="text-align:right;">${b.passed_with_kt !== undefined ? b.passed_with_kt : 0}</td>
        <td style="text-align:right;"><strong>${b.success_rate !== undefined ? b.success_rate.toFixed(2) : 0}%</strong></td>
      `;
      breakdownBody.appendChild(tr);
    });
  }

  // Multiple Exit Students Table
  const exitBody = document.getElementById("batchMultipleExitBody");
  if (exitBody) {
    exitBody.innerHTML = "";
    const exitList = sr.multiple_exit_students || [];
    if (exitList.length > 0) {
      exitList.forEach((st, idx) => {
        const tr = document.createElement("tr");
        const semsStr = (st.semesters_appeared || []).map(s => `Sem ${s}`).join(", ");
        tr.innerHTML = `
          <td>${idx + 1}</td>
          <td><strong>${st.name}</strong></td>
          <td style="font-family: monospace; font-size:0.75rem;">${st.prn || "N/A"}</td>
          <td>${semsStr}</td>
          <td><span class="badge-na">${st.exit_status || "Multiple Exit (after First Year)"}</span></td>
        `;
        exitBody.appendChild(tr);
      });
    } else {
      exitBody.innerHTML = '<tr><td colspan="5" class="empty-cell">No multiple exit records.</td></tr>';
    }
  }

  // Multiple Entry Students Table
  const entryBody = document.getElementById("batchMultipleEntryBody");
  if (entryBody) {
    entryBody.innerHTML = "";
    const entryList = sr.multiple_entry_students_list || [];
    if (entryList.length > 0) {
      entryList.forEach((st, idx) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${idx + 1}</td>
          <td><strong>${st.name}</strong></td>
          <td style="font-family: monospace; font-size:0.75rem;">${st.prn || "N/A"}</td>
          <td style="text-align:center;">Sem ${st.entry_semester || 3}</td>
          <td><span class="badge-pass">${st.category || "Multiple Entry Student"}</span></td>
        `;
        entryBody.appendChild(tr);
      });
    } else {
      entryBody.innerHTML = '<tr><td colspan="5" class="empty-cell">No multiple entry records.</td></tr>';
    }
  }

  // Student Cross-Checking & Success Verification Roster
  const rosterBody = document.getElementById("batchSuccessRosterBody");
  if (rosterBody) {
    rosterBody.innerHTML = "";
    (sr.student_roster || []).forEach(r => {
      const tr = document.createElement("tr");
      let semsHtml = "";
      for (let s = 1; s <= 8; s++) {
        const status = r.sem_records ? r.sem_records[s] : "—";
        if (status === "PASS") {
          semsHtml += `<td><span class="badge-pass" style="font-size:0.72rem; padding:2px 6px;">P</span></td>`;
        } else if (status === "FAIL") {
          semsHtml += `<td><span class="badge-fail" style="font-size:0.72rem; padding:2px 6px;">F</span></td>`;
        } else if (status === "ABSENT") {
          semsHtml += `<td><span class="badge-na" style="font-size:0.72rem; padding:2px 6px;">AB</span></td>`;
        } else {
          semsHtml += `<td style="color:#64748B; text-align:center;">—</td>`;
        }
      }

      const zeroKtBadge = r.zero_kt
        ? '<span class="badge-pass" style="font-size:0.72rem; padding:2px 6px;">YES</span>'
        : '<span class="badge-fail" style="font-size:0.72rem; padding:2px 6px;">NO</span>';

      const finalBadge = r.final_status === "PASS"
        ? '<span class="badge-pass">PASS</span>'
        : (r.final_status === "FAIL" ? '<span class="badge-fail">FAIL</span>' : `<span class="badge-na">${r.final_status || "N/A"}</span>`);

      tr.innerHTML = `
        <td>${r.sr_no || ""}</td>
        <td><strong>${r.student_id || ""}</strong></td>
        <td>${r.name || ""}</td>
        <td style="font-family: monospace; font-size:0.75rem;">${r.prn || "N/A"}</td>
        <td><span class="sem-file-type ${r.entry_type && r.entry_type.includes('Lateral') ? 'pdf' : 'excel'}">${r.entry_type || "Regular"}</span></td>
        <td style="text-align:center;">${r.entry_sem ? `Sem ${r.entry_sem}` : "—"}</td>
        ${semsHtml}
        <td>${finalBadge}</td>
        <td style="text-align:center;">${zeroKtBadge}</td>
        <td style="text-align:right; font-weight:700;">${r.average_sgpi !== null && r.average_sgpi !== undefined ? r.average_sgpi.toFixed(2) : "N/A"}</td>
      `;
      rosterBody.appendChild(tr);
    });
  }

  // 1. KPI Strip (Tab 3: Overall Summary)
  document.getElementById("kpiBatchTotalStudents").textContent = overall.total_students || 0;
  document.getElementById("kpiBatchClearedAll").textContent = overall.cleared_all_available_semesters || 0;
  document.getElementById("kpiBatchClearedPct").textContent = `${overall.cleared_all_percentage || 0}% Zero Backlogs`;
  document.getElementById("kpiBatchAnyKt").textContent = overall.students_with_any_kt || 0;
  document.getElementById("kpiBatchKtPct").textContent = `${overall.kt_student_percentage || 0}% Backlog Burden`;
  document.getElementById("kpiBatchAvgSgpi").textContent = overall.batch_average_sgpi !== null && overall.batch_average_sgpi !== undefined ? overall.batch_average_sgpi.toFixed(2) : "N/A";
  document.getElementById("kpiBatchMaxSgpi").textContent = overall.batch_highest_sgpi !== null && overall.batch_highest_sgpi !== undefined ? overall.batch_highest_sgpi.toFixed(2) : "N/A";
  document.getElementById("kpiBatchGraduated").textContent = overall.graduated_eligible || 0;

  // Grade Distribution Table
  const gDist = overall.grade_distribution || {};
  const tot = overall.total_students || 1;
  const gBody = document.getElementById("batchGradeDistBody");
  if (gBody) {
    gBody.innerHTML = `
      <tr><td><strong>Distinction (Outstanding)</strong></td><td>SGPI ≥ 7.50</td><td style="text-align:right;">${gDist.distinction || 0}</td><td style="text-align:right;">${(((gDist.distinction || 0)/tot)*100).toFixed(1)}%</td></tr>
      <tr><td><strong>First Class</strong></td><td>6.75 ≤ SGPI < 7.50</td><td style="text-align:right;">${gDist.first_class || 0}</td><td style="text-align:right;">${(((gDist.first_class || 0)/tot)*100).toFixed(1)}%</td></tr>
      <tr><td><strong>Second Class</strong></td><td>6.00 ≤ SGPI < 6.75</td><td style="text-align:right;">${gDist.second_class || 0}</td><td style="text-align:right;">${(((gDist.second_class || 0)/tot)*100).toFixed(1)}%</td></tr>
      <tr><td><strong>Pass Class</strong></td><td>SGPI < 6.00</td><td style="text-align:right;">${gDist.pass_class || 0}</td><td style="text-align:right;">${(((gDist.pass_class || 0)/tot)*100).toFixed(1)}%</td></tr>
    `;
  }

  // 2. Enhanced Semester-Wise Analysis Table (Tab 2)
  const semBody = document.getElementById("batchSemesterBody");
  if (semBody) {
    semBody.innerHTML = "";
    const semList = analytics.semester_wise_analysis || analytics.semester_analysis || [];
    semList.forEach(s => {
      const g = s.grade_distribution || {};
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${s.semester_name}</strong></td>
        <td><span class="sem-file-type ${(s.input_type || 'EXCEL').toLowerCase()}">${s.input_type || 'EXCEL'}</span></td>
        <td style="text-align:right;"><strong>${s.total_students || 0}</strong></td>
        <td style="text-align:right;">${s.regular_students !== undefined ? s.regular_students : "—"}</td>
        <td style="text-align:right;">${s.lateral_students !== undefined ? s.lateral_students : "—"}</td>
        <td style="text-align:right;"><span class="badge-pass">${s.passed || 0}</span></td>
        <td style="text-align:right;"><span class="badge-fail">${s.failed || 0}</span></td>
        <td style="text-align:right;"><strong>${s.pass_percentage !== undefined ? s.pass_percentage.toFixed(1) : 0}%</strong></td>
        <td style="text-align:right; color:#EF4444;">${s.fail_percentage !== undefined ? s.fail_percentage.toFixed(1) : (100 - (s.pass_percentage || 0)).toFixed(1)}%</td>
        <td style="text-align:right;">${s.average_sgpi !== null && s.average_sgpi !== undefined ? s.average_sgpi.toFixed(2) : "N/A"}</td>
        <td style="text-align:right;"><strong style="color:#FBBF24;">${s.highest_sgpi !== null && s.highest_sgpi !== undefined ? s.highest_sgpi.toFixed(2) : "N/A"}</strong></td>
        <td style="text-align:right;">${s.lowest_sgpi !== null && s.lowest_sgpi !== undefined ? s.lowest_sgpi.toFixed(2) : "N/A"}</td>
        <td style="text-align:right;">${g.distinction || 0}</td>
        <td style="text-align:right;">${g.first_class || 0}</td>
        <td style="text-align:right;">${g.second_class || 0}</td>
        <td style="text-align:right;">${g.pass_class || 0}</td>
        <td style="text-align:right;">${s.kt_students_count || 0}</td>
        <td style="text-align:right;">${s.total_kt_subjects !== null && s.total_kt_subjects !== undefined ? s.total_kt_subjects : "N/A"}</td>
      `;
      semBody.appendChild(tr);
    });
  }

  // 3. Student Progression Table
  const progHead = document.getElementById("batchProgressionHeadRow");
  const progBody = document.getElementById("batchProgressionBody");
  if (progHead && progBody) {
    progHead.innerHTML = `
      <th>Student ID</th>
      <th>Canonical Name</th>
      <th>PRN</th>
      ${[1,2,3,4,5,6,7,8].map(s => `<th>Sem ${s} Result</th><th>Sem ${s} SGPI</th>`).join("")}
      <th>Avg SGPI</th>
      <th>Trend</th>
      <th>Final Status</th>
    `;

    progBody.innerHTML = "";
    (analytics.student_progression || []).forEach(p => {
      const tr = document.createElement("tr");

      let semsHtml = "";
      for (let s = 1; s <= 8; s++) {
        const r = p.sem_records[s];
        if (r) {
          const resBadge = r.result === "PASS" ? '<span class="badge-pass">PASS</span>' : '<span class="badge-fail">FAIL</span>';
          const sgpiText = r.sgpi !== null && r.sgpi !== undefined ? r.sgpi.toFixed(2) : "N/A";
          semsHtml += `<td>${resBadge}</td><td style="text-align:right;">${sgpiText}</td>`;
        } else {
          semsHtml += `<td><span class="badge-na">N/A</span></td><td style="text-align:center; color:#64748B;">—</td>`;
        }
      }

      let trendClass = "badge-trend-neutral";
      if (p.progression.includes("Improving")) trendClass = "badge-trend-up";
      else if (p.progression.includes("Declining")) trendClass = "badge-trend-down";
      else if (p.progression.includes("Consistent High")) trendClass = "badge-gold";

      tr.innerHTML = `
        <td><strong>${p.student_id}</strong></td>
        <td>${p.canonical_name}</td>
        <td style="font-family: monospace; font-size:0.75rem;">${p.prn || "N/A"}</td>
        ${semsHtml}
        <td style="text-align:right; font-weight:700;">${p.average_sgpi !== null ? p.average_sgpi.toFixed(2) : "N/A"}</td>
        <td><span class="${trendClass}">${p.progression}</span></td>
        <td><strong>${p.final_status}</strong></td>
      `;
      progBody.appendChild(tr);
    });
  }

  // 4. KT Analysis Tables
  const ktBody = document.getElementById("batchKtBody");
  if (ktBody) {
    ktBody.innerHTML = "";
    (analytics.kt_analysis || []).forEach(k => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${k.semester_name}</strong></td>
        <td style="text-align:right;">${k.total_students}</td>
        <td style="text-align:right;"><span class="badge-fail">${k.kt_students}</span></td>
        <td style="text-align:right;"><strong>${k.kt_percentage.toFixed(1)}%</strong></td>
        <td style="text-align:right;">${k.total_kt_subjects}</td>
        <td style="text-align:right;">${k.avg_kt_per_failed_student}</td>
      `;
      ktBody.appendChild(tr);
    });
  }

  const repeatKtBody = document.getElementById("batchRepeatKtBody");
  if (repeatKtBody) {
    repeatKtBody.innerHTML = "";
    const reps = analytics.repeated_kt_students || [];
    if (reps.length > 0) {
      reps.forEach(rep => {
        const tr = document.createElement("tr");
        const semsStr = (rep.failed_semesters || []).map(s => `Sem ${s}`).join(", ");
        tr.innerHTML = `
          <td><strong>${rep.student_id}</strong></td>
          <td>${rep.canonical_name}</td>
          <td style="text-align:right;"><span class="badge-fail">${rep.repeat_count} Semesters</span></td>
          <td>${semsStr}</td>
          <td><strong>${rep.latest_result} in Sem ${rep.latest_sem}</strong></td>
        `;
        repeatKtBody.appendChild(tr);
      });
    } else {
      repeatKtBody.innerHTML = '<tr><td colspan="5" class="empty-cell">No recurring backlogs recorded across cohort.</td></tr>';
    }
  }

  // 5. Topper Tables
  const multiTopperBody = document.getElementById("batchMultiTopperBody");
  if (multiTopperBody) {
    multiTopperBody.innerHTML = "";
    const multi = analytics.multi_sem_toppers || [];
    if (multi.length > 0) {
      multi.forEach((m, idx) => {
        const tr = document.createElement("tr");
        const semsStr = (m.semesters || []).map(s => `Sem ${s}`).join(", ");
        tr.innerHTML = `
          <td style="text-align:center;"><strong>#${idx + 1}</strong></td>
          <td><strong>${m.canonical_name}</strong></td>
          <td>${m.seat_no}</td>
          <td style="text-align:right;"><span class="badge-gold">${m.appearances} Semesters</span></td>
          <td>${semsStr}</td>
          <td style="text-align:right; font-weight:700; color:#FBBF24;">${m.average_sgpi.toFixed(2)}</td>
        `;
        multiTopperBody.appendChild(tr);
      });
    } else {
      multiTopperBody.innerHTML = '<tr><td colspan="6" class="empty-cell">Insufficient multi-semester merit appearances.</td></tr>';
    }
  }

  const semTopperBody = document.getElementById("batchSemTopperBody");
  if (semTopperBody) {
    semTopperBody.innerHTML = "";
    const topBySem = analytics.topper_by_semester || {};
    Object.keys(topBySem).sort((a,b)=>a-b).forEach(sem => {
      const topList = topBySem[sem] || [];
      topList.slice(0, 5).forEach(item => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td><strong>Semester ${sem}</strong></td>
          <td style="text-align:center;"><span class="badge-gold">Rank ${item.rank}</span></td>
          <td>${item.student_name}</td>
          <td>${item.seat_no}</td>
          <td style="text-align:right; font-weight:700;">${item.sgpi !== null ? item.sgpi.toFixed(2) : "N/A"}</td>
        `;
        semTopperBody.appendChild(tr);
      });
    });
  }

  // 6. Retention Table
  const retBody = document.getElementById("batchRetentionBody");
  if (retBody) {
    retBody.innerHTML = "";
    (analytics.retention_flow || []).forEach(f => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${f.transition}</strong></td>
        <td>Sem ${f.from_semester}</td>
        <td>Sem ${f.to_semester}</td>
        <td style="text-align:right;">${f.initial_count}</td>
        <td style="text-align:right;">${f.next_count}</td>
        <td style="text-align:right;">${f.continuing_students}</td>
        <td style="text-align:right;"><span class="badge-pass">+${f.lateral_new_students}</span></td>
        <td style="text-align:right; font-weight:700;">${f.retention_rate.toFixed(1)}%</td>
      `;
      retBody.appendChild(tr);
    });
  }

  // 7. Performance Categories
  const perfContainer = document.getElementById("batchPerformanceCategories");
  if (perfContainer) {
    perfContainer.innerHTML = "";
    const cats = analytics.student_performance_categorization || {};
    const catDefs = [
      { key: "consistently_strong", title: "🌟 Consistently Strong Performance (Maintained Distinction, Zero Backlogs)" },
      { key: "improving_performance", title: "📈 Improving Performance (Positive Progression Δ ≥ +0.50 SGPI)" },
      { key: "declining_performance", title: "📉 Declining Performance (Noticeable Drop Δ ≤ -0.60 SGPI)" },
      { key: "fluctuating_performance", title: "📊 Fluctuating Performance (High SGPI Variance across Semesters)" },
      { key: "repeated_difficulties", title: "⚠️ Repeated Academic Difficulties (KT in ≥ 2 Semesters)" },
    ];

    catDefs.forEach(cd => {
      const list = cats[cd.key] || [];
      const box = document.createElement("div");
      box.style.marginBottom = "1.5rem";
      box.innerHTML = `
        <h4 style="margin-bottom: 0.6rem;">${cd.title} (${list.length} Students)</h4>
        <div class="batch-table-container">
          <table>
            <thead>
              <tr>
                <th>Student ID</th>
                <th>Canonical Name</th>
                <th>Semesters Appeared</th>
                <th>Average SGPI</th>
                <th>Classification Justification</th>
              </tr>
            </thead>
            <tbody>
              ${list.length > 0 ? list.slice(0, 15).map(it => `
                <tr>
                  <td><strong>${it.student_id}</strong></td>
                  <td>${it.canonical_name}</td>
                  <td style="text-align:center;">${it.appeared_count}</td>
                  <td style="text-align:right; font-weight:700;">${typeof it.average_sgpi === "number" ? it.average_sgpi.toFixed(2) : (it.average_sgpi || "N/A")}</td>
                  <td>${it.classification_reason || "Supported by empirical records"}</td>
                </tr>
              `).join("") : '<tr><td colspan="5" class="empty-cell">No students classified in this category.</td></tr>'}
            </tbody>
          </table>
        </div>
      `;
      perfContainer.appendChild(box);
    });
  }

  // 8. Quality Issues Table
  const qBody = document.getElementById("batchQualityBody");
  if (qBody) {
    qBody.innerHTML = "";
    const issues = analytics.data_quality_issues || [];
    if (issues.length > 0) {
      issues.slice(0, 50).forEach((iss, idx) => {
        const tr = document.createElement("tr");
        let sevClass = "badge-severity-info";
        if (iss.severity === "High") sevClass = "badge-severity-high";
        else if (iss.severity === "Medium") sevClass = "badge-severity-med";

        tr.innerHTML = `
          <td style="text-align:center;">${idx + 1}</td>
          <td><strong>${iss.category}</strong></td>
          <td style="text-align:center;">${iss.semester}</td>
          <td style="font-family: monospace;">${iss.student_id || "N/A"}</td>
          <td>${iss.student_name || "N/A"}</td>
          <td>${iss.description}</td>
          <td><span class="${sevClass}">${iss.severity}</span></td>
          <td>${iss.action_taken}</td>
        `;
        qBody.appendChild(tr);
      });
    } else {
      qBody.innerHTML = '<tr><td colspan="8" class="empty-cell">Zero data quality anomalies detected.</td></tr>';
    }
  }

  // 9. Raw Semester Select & Table
  const rawSelect = document.getElementById("batchRawSemSelect");
  if (rawSelect) {
    rawSelect.innerHTML = "";
    sems.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = `Semester ${s}`;
      rawSelect.appendChild(opt);
    });
    if (sems.length > 0) {
      renderRawSemesterTable(sems[0]);
    }
  }

  // 10. Render Interactive Multi-Semester Batch Charts
  renderAllBatchCharts(analytics);
}

function renderRawSemesterTable(sem) {
  if (!activeBatchAnalytics) return;
  const rawData = activeBatchAnalytics.semester_raw_data || {};
  const recs = rawData[sem] || [];
  const tbody = document.getElementById("batchRawDataBody");
  if (!tbody) return;

  tbody.innerHTML = "";
  if (recs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-cell">No records for this semester.</td></tr>';
    return;
  }

  recs.forEach((r, idx) => {
    const tr = document.createElement("tr");
    const resBadge = r.result === "PASS" ? '<span class="badge-pass">PASS</span>' : '<span class="badge-fail">FAIL</span>';
    const sgpiStr = r.sgpi !== null && r.sgpi !== undefined ? r.sgpi.toFixed(2) : "N/A";
    tr.innerHTML = `
      <td style="text-align:center;">${idx + 1}</td>
      <td style="font-weight:700;">${r.seat_no || "N/A"}</td>
      <td>${r.name || "N/A"}</td>
      <td style="font-family: monospace;">${r.prn || "N/A"}</td>
      <td>${resBadge}</td>
      <td style="text-align:right; font-weight:700;">${sgpiStr}</td>
      <td style="text-align:right;">${r.kt_count || 0}</td>
      <td style="text-align:right;">${r.total_marks !== null && r.total_marks !== undefined ? r.total_marks : "N/A"}</td>
      <td style="color:#64748B;">${r.source_file || "N/A"}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ==========================================================================
// Batch Suite Interactive Charts (Chart.js Engine)
// ==========================================================================
let batchChartInstances = {};

function destroyBatchCharts() {
  Object.keys(batchChartInstances).forEach(k => {
    if (batchChartInstances[k]) {
      try { batchChartInstances[k].destroy(); } catch (e) {}
      delete batchChartInstances[k];
    }
  });
}

function renderAllBatchCharts(analytics) {
  if (typeof Chart === "undefined") return;
  destroyBatchCharts();

  const sr = analytics.success_rate_analysis || {};
  const overall = analytics.overall_summary || {};
  const semList = analytics.semester_wise_analysis || analytics.semester_analysis || [];
  const ktList = analytics.kt_analysis || [];
  const retList = analytics.retention_flow || [];

  // 1. Chart: Admission Intake vs Final Graduation by Category
  const ctxIntake = document.getElementById("chartBatchIntakeGrad");
  if (ctxIntake) {
    const bd = sr.breakdown || [];
    const labels = bd.length > 0 ? bd.map(b => b.category) : ["Regular Intake (Sem 1)", "Lateral Entry (DSE)"];
    const admitted = bd.length > 0 ? bd.map(b => b.admitted) : [sr.regular_intake_n1 || 0, sr.lateral_entry_n3 || 0];
    const passed = bd.length > 0 ? bd.map(b => b.passed_final) : [sr.P_final_regular || 0, sr.P_final_lateral || 0];
    const noKt = bd.length > 0 ? bd.map(b => b.passed_no_kt) : [sr.P_no_kt_regular || 0, sr.P_no_kt_lateral || 0];

    batchChartInstances.intakeGrad = new Chart(ctxIntake, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          { label: "Admitted Intake", data: admitted, backgroundColor: "#1F4E78", borderRadius: 4 },
          { label: "Passed Final Sem", data: passed, backgroundColor: "#10b981", borderRadius: 4 },
          { label: "Cleared Zero KT", data: noKt, backgroundColor: "#3b82f6", borderRadius: 4 }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "top", labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: { mode: "index", intersect: false }
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 10 } } },
          y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.05)" } }
        }
      }
    });
  }

  // 2. Chart: Final Graduation & Backlog Status Split
  const ctxDonut = document.getElementById("chartBatchBacklogSplit");
  if (ctxDonut) {
    const pNoKt = sr.passed_without_backlogs || sr.P_no_kt_passed || 0;
    const pWithKt = sr.passed_with_backlogs || sr.P_with_kt_passed || 0;
    const totAdm = sr.total_admitted_n1_n3 || sr.total_cohort_intake || 0;
    const pFinal = sr.passed_final_sem || sr.P_final_passed || 0;
    const pending = Math.max(0, totAdm - pFinal);

    batchChartInstances.backlogSplit = new Chart(ctxDonut, {
      type: "doughnut",
      data: {
        labels: ["Cleared Zero KT", "Cleared With Backlogs", "Pending / Incomplete"],
        datasets: [{
          data: [pNoKt, pWithKt, pending],
          backgroundColor: ["#10b981", "#f59e0b", "#94a3b8"],
          borderWidth: 2,
          borderColor: "#ffffff"
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } }
        },
        cutout: "60%"
      }
    });
  }

  // 3. Chart: Semester Pass Rate (%) Progression Trend
  const ctxSemPass = document.getElementById("chartBatchSemPassTrend");
  if (ctxSemPass && semList.length > 0) {
    batchChartInstances.semPass = new Chart(ctxSemPass, {
      type: "bar",
      data: {
        labels: semList.map(s => s.semester_name),
        datasets: [{
          label: "Pass Rate (%)",
          data: semList.map(s => s.pass_percentage || 0),
          backgroundColor: semList.map(s => (s.pass_percentage >= 75 ? "#10b981" : (s.pass_percentage >= 50 ? "#3b82f6" : "#ef4444"))),
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `${ctx.raw}% Passed` } }
        },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, max: 100, ticks: { callback: v => v + "%" } }
        }
      }
    });
  }

  // 4. Chart: Average SGPI Trajectory Trend
  const ctxSemSgpi = document.getElementById("chartBatchSemSgpiTrend");
  if (ctxSemSgpi && semList.length > 0) {
    batchChartInstances.semSgpi = new Chart(ctxSemSgpi, {
      type: "line",
      data: {
        labels: semList.map(s => s.semester_name),
        datasets: [
          {
            label: "Average SGPI",
            data: semList.map(s => s.average_sgpi || null),
            borderColor: "#3b82f6",
            backgroundColor: "rgba(59, 130, 246, 0.12)",
            tension: 0.35,
            fill: true,
            pointRadius: 4,
            pointBackgroundColor: "#3b82f6"
          },
          {
            label: "Highest SGPI",
            data: semList.map(s => s.highest_sgpi || null),
            borderColor: "#f59e0b",
            borderDash: [5, 4],
            tension: 0.2,
            pointRadius: 3,
            fill: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "top", labels: { boxWidth: 12 } }
        },
        scales: {
          x: { grid: { display: false } },
          y: { min: 4, max: 10 }
        }
      }
    });
  }

  // 5. Chart: Cohort Grade Classification Distribution
  const ctxGrade = document.getElementById("chartBatchGradeDist");
  if (ctxGrade) {
    const g = overall.grade_distribution || {};
    batchChartInstances.gradeDist = new Chart(ctxGrade, {
      type: "doughnut",
      data: {
        labels: ["Distinction (≥7.5)", "First Class (6.75-7.5)", "Second Class (6.0-6.75)", "Pass Class (<6.0)"],
        datasets: [{
          data: [g.distinction || 0, g.first_class || 0, g.second_class || 0, g.pass_class || 0],
          backgroundColor: ["#10b981", "#3b82f6", "#f59e0b", "#94a3b8"],
          borderWidth: 2
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "right", labels: { boxWidth: 12 } } },
        cutout: "60%"
      }
    });
  }

  // 6. Chart: Cohort Clearance & Backlog Distribution
  const ctxComp = document.getElementById("chartBatchCompletionRatio");
  if (ctxComp) {
    batchChartInstances.completionRatio = new Chart(ctxComp, {
      type: "bar",
      data: {
        labels: ["Cleared All (Zero KT)", "Encountered KT", "Graduated (Sem 8)"],
        datasets: [{
          label: "Students",
          data: [
            overall.cleared_all_available_semesters || 0,
            overall.students_with_any_kt || 0,
            overall.graduated_eligible || 0
          ],
          backgroundColor: ["#10b981", "#ef4444", "#3b82f6"],
          borderRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true }
        }
      }
    });
  }

  // 7. Chart: Semester Backlog Burden
  const ctxKt = document.getElementById("chartBatchKtBurden");
  if (ctxKt && ktList.length > 0) {
    batchChartInstances.ktBurden = new Chart(ctxKt, {
      type: "bar",
      data: {
        labels: ktList.map(k => k.semester_name),
        datasets: [
          {
            label: "KT Students",
            data: ktList.map(k => k.kt_students || 0),
            backgroundColor: "#ef4444",
            borderRadius: 4
          },
          {
            label: "KT Subjects Count",
            data: ktList.map(k => (typeof k.total_kt_subjects === "number" ? k.total_kt_subjects : 0)),
            backgroundColor: "#f97316",
            borderRadius: 4
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "top", labels: { boxWidth: 12 } } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true }
        }
      }
    });
  }

  // 8. Chart: Retention & Progression Flow
  const ctxRet = document.getElementById("chartBatchRetentionFlow");
  if (ctxRet && retList.length > 0) {
    batchChartInstances.retentionFlow = new Chart(ctxRet, {
      type: "bar",
      data: {
        labels: retList.map(r => r.transition),
        datasets: [
          {
            label: "Continuing Students",
            data: retList.map(r => r.continuing_students || 0),
            backgroundColor: "#3b82f6",
            borderRadius: 4
          },
          {
            label: "Lateral / New Entry",
            data: retList.map(r => r.lateral_new_students || 0),
            backgroundColor: "#10b981",
            borderRadius: 4
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "top", labels: { boxWidth: 12 } } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true }
        }
      }
    });
  }
}

// ==========================================================================
// Smart Data Cleaning & Audit Diff Review
// ==========================================================================
let activeCleaningData = null;
let activeCleaningFilter = "all";
let selectedCleaningChangeIds = new Set();

function initDataCleaningUI() {
  const btnOpenCleaner = document.getElementById("btnOpenDataCleaner");
  const modalBackdrop = document.getElementById("cleanDataModalBackdrop");
  const btnCloseModal = document.getElementById("btnCloseCleanModal");
  const btnDiscard = document.getElementById("btnDiscardCleanChanges");
  const btnAccept = document.getElementById("btnAcceptCleanChanges");
  const searchInput = document.getElementById("cleanDiffSearch");
  const btnSelectAll = document.getElementById("btnCleanSelectAll");
  const btnDeselectAll = document.getElementById("btnCleanDeselectAll");
  const headerCheck = document.getElementById("cleanDiffSelectAllHeader");

  if (btnOpenCleaner) {
    btnOpenCleaner.addEventListener("click", () => {
      runDataCleaningAudit();
    });
  }

  if (btnCloseModal) {
    btnCloseModal.addEventListener("click", () => {
      if (modalBackdrop) modalBackdrop.classList.add("hidden");
    });
  }

  if (btnDiscard) {
    btnDiscard.addEventListener("click", async () => {
      if (confirm("Discard all proposed cleaning modifications? Your dataset will remain in its original state.")) {
        await discardCleaningChanges();
      }
    });
  }

  if (btnAccept) {
    btnAccept.onclick = async (e) => {
      if (e) e.preventDefault();
      await acceptCleaningChanges();
    };
    btnAccept.addEventListener("click", async (e) => {
      if (e) e.preventDefault();
      await acceptCleaningChanges();
    });
  }

  if (btnSelectAll) {
    btnSelectAll.addEventListener("click", () => {
      const allChanges = activeCleaningData?.changes || [];
      allChanges.forEach(c => selectedCleaningChangeIds.add(String(c.change_id)));
      document.querySelectorAll(".clean-row-check").forEach(chk => {
        chk.checked = true;
        const row = chk.closest("tr");
        if (row) row.style.opacity = "1";
      });
      updateCleanSelectionUI();
    });
  }

  if (btnDeselectAll) {
    btnDeselectAll.addEventListener("click", () => {
      selectedCleaningChangeIds.clear();
      document.querySelectorAll(".clean-row-check").forEach(chk => {
        chk.checked = false;
        const row = chk.closest("tr");
        if (row) row.style.opacity = "0.55";
      });
      updateCleanSelectionUI();
    });
  }

  if (headerCheck) {
    headerCheck.addEventListener("change", (e) => {
      const checked = e.target.checked;
      const visibleRowChecks = document.querySelectorAll(".clean-row-check");
      visibleRowChecks.forEach(chk => {
        chk.checked = checked;
        const cid = chk.getAttribute("data-cid");
        if (checked) {
          selectedCleaningChangeIds.add(cid);
        } else {
          selectedCleaningChangeIds.delete(cid);
        }
        const row = chk.closest("tr");
        if (row) row.style.opacity = checked ? "1" : "0.55";
      });
      updateCleanSelectionUI();
    });
  }

  const filterBtns = document.querySelectorAll(".clean-filter-btn");
  filterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      filterBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeCleaningFilter = btn.getAttribute("data-filter") || "all";
      renderCleaningDiffTable();
    });
  });

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      renderCleaningDiffTable();
    });
  }
}

async function runDataCleaningAudit() {
  if (!currentRecords || currentRecords.length === 0) {
    alert("Please extract or select a dataset first before running data cleaning.");
    return;
  }

  const btnOpen = document.getElementById("btnOpenDataCleaner");
  const origHtml = btnOpen ? btnOpen.innerHTML : "";
  if (btnOpen) {
    btnOpen.disabled = true;
    btnOpen.innerHTML = "⏳ Auditing &amp; Cleaning...";
  }

  try {
    const res = await fetch("/api/clean/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        students: currentRecords,
        folder_name: currentFolderName
      })
    });
    const data = await res.json();

    if (!data.success) {
      alert(`Data cleaning error: ${data.error || "Unknown error"}`);
      return;
    }

    activeCleaningData = data;
    renderCleaningModal(data);

    const modalBackdrop = document.getElementById("cleanDataModalBackdrop");
    if (modalBackdrop) modalBackdrop.classList.remove("hidden");

  } catch (err) {
    console.error("Failed to run data cleaning audit:", err);
    alert(`Data cleaning failed: ${err.message}`);
  } finally {
    if (btnOpen) {
      btnOpen.disabled = false;
      btnOpen.innerHTML = origHtml;
    }
  }
}

function renderCleaningModal(data) {
  const summary = data.summary || {};
  const changes = data.changes || [];

  // Default: All proposed fixes selected
  selectedCleaningChangeIds = new Set(changes.map(c => String(c.change_id)));

  setText("cleanStatTotalRows", summary.total_rows || 0);
  setText("cleanStatTotalChanges", summary.total_changes || 0);
  setText("cleanStatImputed", summary.imputed_missing || 0);
  setText("cleanStatDiscrepancies", summary.fixed_discrepancies || 0);
  setText("cleanStatFormat", summary.format_changes || 0);
  setText("cleanStatDefective", summary.defective_candidates || 0);

  const imputedCount = changes.filter(c => c.change_type === "missing_imputed").length;
  const discrepancyCount = changes.filter(c => c.change_type === "discrepancy_fixed").length;
  const formatCount = changes.filter(c => c.change_type === "format").length;
  const outlierCount = changes.filter(c => c.change_type === "outlier_flagged").length;
  const defectiveCount = changes.filter(c => c.change_type === "integrity_defective").length;

  setText("chipAllCount", changes.length);
  setText("chipImputedCount", imputedCount);
  setText("chipDiscrepancyCount", discrepancyCount);
  setText("chipFormatCount", formatCount);
  setText("chipOutlierCount", outlierCount);
  setText("chipDefectiveCount", defectiveCount);

  renderCleaningDiffTable();
  updateCleanSelectionUI();
}

function updateCleanSelectionUI() {
  const allChanges = activeCleaningData?.changes || [];
  const total = allChanges.length;
  const count = selectedCleaningChangeIds.size;

  const countText = document.getElementById("cleanSelectedCountText");
  if (countText) {
    if (total === 0) {
      countText.textContent = "0 issues detected • Dataset clean";
    } else {
      countText.textContent = `${count} of ${total} fixes selected`;
    }
  }

  const btnAccept = document.getElementById("btnAcceptCleanChanges");
  if (btnAccept) {
    if (total === 0) {
      btnAccept.innerHTML = `✅ Dataset Verified (Close)`;
      btnAccept.disabled = false;
    } else {
      btnAccept.innerHTML = `✅ Apply Selected Fixes (${count})`;
      btnAccept.disabled = count === 0;
    }
  }

  const headerCheck = document.getElementById("cleanDiffSelectAllHeader");
  if (headerCheck) {
    const visibleRowChecks = document.querySelectorAll(".clean-row-check");
    if (visibleRowChecks.length > 0) {
      const allVisibleChecked = Array.from(visibleRowChecks).every(ch => ch.checked);
      const someVisibleChecked = Array.from(visibleRowChecks).some(ch => ch.checked);
      headerCheck.checked = allVisibleChecked;
      headerCheck.indeterminate = !allVisibleChecked && someVisibleChecked;
    } else {
      headerCheck.checked = false;
      headerCheck.indeterminate = false;
    }
  }
}

function renderCleaningDiffTable() {
  if (!activeCleaningData) return;
  const tbody = document.getElementById("cleanDiffBody");
  const emptyMsg = document.getElementById("cleanEmptyMsg");
  const table = document.getElementById("cleanDiffTable");
  const searchVal = (document.getElementById("cleanDiffSearch")?.value || "").toLowerCase().trim();

  if (!tbody) return;
  tbody.innerHTML = "";

  const allChanges = activeCleaningData.changes || [];
  let filtered = allChanges;

  if (activeCleaningFilter !== "all") {
    filtered = filtered.filter(c => c.change_type === activeCleaningFilter);
  }

  if (searchVal) {
    filtered = filtered.filter(c => {
      const name = String(c.student_name || "").toLowerCase();
      const seat = String(c.seat_no || "").toLowerCase();
      const col = String(c.column || "").toLowerCase();
      const reason = String(c.reason || "").toLowerCase();
      return name.includes(searchVal) || seat.includes(searchVal) || col.includes(searchVal) || reason.includes(searchVal);
    });
  }

  if (filtered.length === 0) {
    if (emptyMsg) emptyMsg.classList.remove("hidden");
    if (table) table.classList.add("hidden");
    updateCleanSelectionUI();
    return;
  }

  if (emptyMsg) emptyMsg.classList.add("hidden");
  if (table) table.classList.remove("hidden");

  filtered.slice(0, 200).forEach((c) => {
    const tr = document.createElement("tr");
    const cid = String(c.change_id);
    const isChecked = selectedCleaningChangeIds.has(cid);
    if (!isChecked) {
      tr.style.opacity = "0.55";
    }

    let typeClass = "type-format";
    let typeLabel = "Format";
    if (c.change_type === "missing_imputed") {
      typeClass = "type-missing_imputed";
      typeLabel = "Imputed";
    } else if (c.change_type === "discrepancy_fixed") {
      typeClass = "type-discrepancy_fixed";
      typeLabel = "Discrepancy";
    } else if (c.change_type === "outlier_flagged") {
      typeClass = "type-outlier_flagged";
      typeLabel = "Outlier";
    } else if (c.change_type === "integrity_defective") {
      typeClass = "type-integrity_defective";
      typeLabel = "Corrupted";
    }

    const oldDisplay = (c.old_value !== null && c.old_value !== undefined && c.old_value !== "") ? c.old_value : "null / missing";
    const newDisplay = (c.new_value !== null && c.new_value !== undefined && c.new_value !== "") ? c.new_value : "--";

    tr.innerHTML = `
      <td style="text-align: center; vertical-align: middle;">
        <input type="checkbox" class="clean-row-check" data-cid="${cid}" ${isChecked ? "checked" : ""} style="cursor: pointer; width: 15px; height: 15px;">
      </td>
      <td style="color:#64748B; font-weight:600; text-align:center; vertical-align: middle;">${c.row_index + 1}</td>
      <td>
        <strong style="color:#F8FAFC;">${escapeHtml(c.student_name || "Student")}</strong>
        <div style="font-size:0.75rem; color:#94A3B8; font-family:monospace;">Seat: ${escapeHtml(c.seat_no || "N/A")}</div>
      </td>
      <td>
        <span style="font-family:monospace; font-weight:600; color:#38BDF8;">${escapeHtml(c.column || "")}</span>
      </td>
      <td>
        <span class="diff-old-val">${escapeHtml(String(oldDisplay))}</span>
      </td>
      <td>
        <span class="diff-new-val">${escapeHtml(String(newDisplay))}</span>
      </td>
      <td>
        <span class="badge-clean-type ${typeClass}">${typeLabel}</span>
      </td>
      <td style="color:#CBD5E1; font-size:0.8rem; max-width:280px; line-height:1.4;">
        ${escapeHtml(c.reason || "")}
      </td>
    `;

    const chk = tr.querySelector(".clean-row-check");
    if (chk) {
      chk.addEventListener("change", (e) => {
        if (e.target.checked) {
          selectedCleaningChangeIds.add(cid);
          tr.style.opacity = "1";
        } else {
          selectedCleaningChangeIds.delete(cid);
          tr.style.opacity = "0.55";
        }
        updateCleanSelectionUI();
      });
    }

    tbody.appendChild(tr);
  });

  updateCleanSelectionUI();
}

async function acceptCleaningChanges() {
  if (!activeCleaningData) {
    const modalBackdrop = document.getElementById("cleanDataModalBackdrop");
    if (modalBackdrop) modalBackdrop.classList.add("hidden");
    return;
  }

  const allChanges = activeCleaningData.changes || [];
  if (allChanges.length === 0) {
    const modalBackdrop = document.getElementById("cleanDataModalBackdrop");
    if (modalBackdrop) modalBackdrop.classList.add("hidden");
    activeCleaningData = null;
    alert("Dataset is already verified clean! Zero formatting discrepancies or missing components detected.");
    return;
  }

  const acceptedList = Array.from(selectedCleaningChangeIds);
  if (acceptedList.length === 0) {
    alert("Please select at least one fix to apply, or click 'Discard Changes'.");
    return;
  }

  const btnAccept = document.getElementById("btnAcceptCleanChanges");
  const origText = btnAccept ? btnAccept.textContent : "";
  if (btnAccept) {
    btnAccept.disabled = true;
    btnAccept.textContent = "⏳ Applying Selected Fixes...";
  }

  try {
    const res = await fetch("/api/clean/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder_name: currentFolderName,
        action: "accept",
        accepted_ids: acceptedList,
        original_records: activeCleaningData.original_records || currentRecords,
        changes: activeCleaningData.changes,
        cleaned_students: activeCleaningData.cleaned_records
      })
    });
    const resText = await res.text();
    let data;
    try {
      data = JSON.parse(resText);
    } catch (e) {
      alert(`Server error (${res.status}): ${resText.slice(0, 300)}`);
      return;
    }

    if (!data.success) {
      alert(`Failed to apply changes: ${data.error || "Unknown error"}`);
      return;
    }

    if (Array.isArray(currentRecords) && currentRecords.length > 0) {
      undoSnapshots.push(JSON.parse(JSON.stringify(currentRecords)));
      const undoBtn = document.getElementById("btnUndoChat");
      if (undoBtn) undoBtn.disabled = false;
    }

    currentRecords = data.records;
    if (currentResultObj) {
      currentResultObj.students = currentRecords;
      currentResultObj.total_students = currentRecords.length;
    }
    originalRecords = JSON.parse(JSON.stringify(currentRecords));

    // 1. Re-extract columns so any newly imputed or standardized headers appear immediately
    activeColumns = extractRecordKeys(currentRecords, currentCatalog);

    // 2. Identify changed cells to mark dirty indicators and pulse them
    const acceptedChangeSet = new Set(acceptedList);
    dirtyCells.clear();
    selectedRowIndices.clear();
    excelUndoStack = [];
    excelRedoStack = [];
    columnFilters = {};
    currentSort = { colKey: null, dir: null };
    const searchInput = document.getElementById("searchInput");
    if (searchInput) searchInput.value = "";

    const changedCellKeys = [];
    let firstChangedCell = null;
    (activeCleaningData?.changes || []).forEach(c => {
      if (acceptedChangeSet.has(String(c.change_id))) {
        const cellKey = `${c.row_index}_${c.column}`;
        dirtyCells.add(cellKey);
        changedCellKeys.push({ rowIndex: c.row_index, colKey: c.column });
        if (!firstChangedCell) {
          firstChangedCell = { rowIndex: c.row_index, colKey: c.column };
        }
      }
    });

    unsavedEditsCount = 0;
    updateExcelSaveStatus();
    updateExcelUndoRedoButtons();

    filteredRecords = [...currentRecords];

    // Navigate to page containing the first changed row
    if (firstChangedCell && pageSize > 0) {
      currentPage = Math.floor(firstChangedCell.rowIndex / pageSize) + 1;
    } else {
      currentPage = 1;
    }

    populateFindColSelect();
    displayTablePage();
    updateMetaCount(currentRecords.length);

    // 3. Pulse highlight all changed cells visible on the active page
    changedCellKeys.forEach(info => {
      const td = findExcelCellTd(info.rowIndex, info.colKey);
      if (td) {
        td.classList.add("cell-cleaned-pulse");
      }
    });

    // 4. Scroll to first changed cell and select it
    if (firstChangedCell) {
      const firstTd = findExcelCellTd(firstChangedCell.rowIndex, firstChangedCell.colKey);
      if (firstTd) {
        firstTd.scrollIntoView({ block: "nearest", inline: "nearest" });
        selectCell(firstChangedCell.rowIndex, firstChangedCell.colKey);
      }
    }

    currentEdaData = null;
    const viewAnalytics = document.getElementById("tabViewAnalytics");
    if (viewAnalytics && !viewAnalytics.classList.contains("hidden")) {
      computeEdaDashboard();
    }

    const modalBackdrop = document.getElementById("cleanDataModalBackdrop");
    if (modalBackdrop) modalBackdrop.classList.add("hidden");

    const auditPill = document.getElementById("auditPill");
    if (auditPill) {
      auditPill.innerHTML = `✨ <strong>Cleaned &amp; Verified</strong> (${currentRecords.length} records, ${acceptedList.length} fixes)`;
      auditPill.style.background = "rgba(16, 185, 129, 0.15)";
      auditPill.style.borderColor = "rgba(16, 185, 129, 0.4)";
      auditPill.style.color = "#10B981";
    }

    const extStatusBadge = document.getElementById("extractionStatusBadge");
    if (extStatusBadge) {
      extStatusBadge.textContent = "Cleaned & Verified";
      extStatusBadge.className = "badge-status-tag status-success";
    }

    activeCleaningData = null;

    appendChatMessage("assistant", `✨ <strong>Data Cleaning Applied!</strong> Successfully integrated <strong>${acceptedList.length}</strong> standardized values and repairs into the active dataset (${currentRecords.length} records). Modified cells are highlighted in green.`);
    alert(`✅ Successfully applied ${acceptedList.length} fix(es)! The table and ledger have been updated.`);

  } catch (err) {
    console.error("Error accepting cleaning changes:", err);
    alert(`Could not accept cleaning changes: ${err.message}`);
  } finally {
    if (btnAccept) {
      btnAccept.disabled = false;
      const count = selectedCleaningChangeIds ? selectedCleaningChangeIds.size : 0;
      btnAccept.innerHTML = `✅ Apply Selected Fixes (${count})`;
    }
  }
}

async function discardCleaningChanges() {
  try {
    await fetch("/api/clean/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder_name: currentFolderName,
        action: "discard"
      })
    });
  } catch (err) {
    console.warn("Could not notify server of discard:", err);
  }

  activeCleaningData = null;
  const modalBackdrop = document.getElementById("cleanDataModalBackdrop");
  if (modalBackdrop) modalBackdrop.classList.add("hidden");
  alert("Cleaning modifications discarded. Original dataset is unchanged.");
}
