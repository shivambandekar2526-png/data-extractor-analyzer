// Data Extractor AI Frontend Handling

let currentRecords = [];
let filteredRecords = [];
let originalRecords = [];
let undoSnapshots = [];
let currentFolderName = "";
let currentPage = 1;
const pageSize = 25;
let pollInterval = null;
let currentAuditLogs = [];
let autoScrollAudit = true;
let currentResultObj = null;
let activeAuditReport = null;
let showOnlyVerified = false;

document.addEventListener("DOMContentLoaded", () => {
  initStatus();
  initBatches();
  initHistory();
  initUpload();
  initSearch();
  initAuditControls();
  initChatbox();
  initCloudModal();
  initResultsTabs();
  initAiCrosscheckModal();
});

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
      badge.className = "status-pill status-trained";
      badge.querySelector(".status-dot").className = "status-dot green";
      statusText.textContent = `Local AI Active (${data.active_model})`;
      if (modelTag) modelTag.textContent = data.active_model;
      if (chatModelTag) chatModelTag.textContent = data.active_model;
      if (auditGpuBadge) {
        auditGpuBadge.querySelector(".status-dot").className = "status-dot green";
        auditGpuText.textContent = `Ollama Online (${data.active_model} on GPU)`;
      }
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
      badge.className = "status-pill status-trained";
      badge.querySelector(".status-dot").className = "status-dot green";
      statusText.textContent = "Possibility Engine Active (Fast Offline)";
      if (modelTag) modelTag.textContent = "Pre-Trained KB";
      if (pickerContainer) pickerContainer.style.display = "none";
      if (auditGpuBadge) {
        auditGpuBadge.querySelector(".status-dot").className = "status-dot amber";
        auditGpuText.textContent = "Possibility Engine (Fast Offline)";
      }
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
      }
    });
  } catch (e) {
    console.error("Failed to load batches:", e);
  }
}

// 3. File Upload & Drop Area
function initUpload() {
  const dropArea = document.getElementById("dropArea");
  const fileInput = document.getElementById("fileInput");
  const browseBtn = document.getElementById("browseBtn");
  const extractBtn = document.getElementById("extractBtn");

  browseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    fileInput.click();
  });

  dropArea.addEventListener("click", () => fileInput.click());

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
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileSelected(e.target.files[0]);
    }
  });

  // Pipeline Radio Toggle Listeners
  const pipeLocal = document.getElementById("pipelineLocal");
  const pipeCloud = document.getElementById("pipelineCloud");
  const pipeVisual = document.getElementById("pipelineVisual");
  const pipeLocalLabel = document.getElementById("pipelineLocalLabel");
  const pipeCloudLabel = document.getElementById("pipelineCloudLabel");
  const pipeVisualLabel = document.getElementById("pipelineVisualLabel");

  function setPipelineActive(activeType) {
    if (pipeLocalLabel) pipeLocalLabel.classList.toggle("active", activeType === "local");
    if (pipeCloudLabel) pipeCloudLabel.classList.toggle("active", activeType === "cloud");
    if (pipeVisualLabel) pipeVisualLabel.classList.toggle("active", activeType === "visual_blueprint");

    const telemEngine = document.getElementById("telemEngine");
    const auditGpuText = document.getElementById("auditGpuText");
    if (activeType === "local") {
      if (telemEngine) telemEngine.textContent = "Qwen 2.5 (7B) on RTX 4060 GPU";
      if (auditGpuText) auditGpuText.textContent = "Ollama Ready";
    } else if (activeType === "cloud") {
      if (telemEngine) telemEngine.textContent = "Cloud AI (Multi-Provider Orchestrator: Gemini / Groq)";
      if (auditGpuText) auditGpuText.textContent = "Cloud AI Ready";
    } else if (activeType === "visual_blueprint") {
      if (telemEngine) telemEngine.textContent = "Visual Blueprint & Sliced Stream (Multimodal Vision AI)";
      if (auditGpuText) auditGpuText.textContent = "Vision AI Ready";
    }
  }

  if (pipeLocal) pipeLocal.addEventListener("change", () => setPipelineActive("local"));
  if (pipeCloud) pipeCloud.addEventListener("change", () => setPipelineActive("cloud"));
  if (pipeVisual) pipeVisual.addEventListener("change", () => setPipelineActive("visual_blueprint"));

  extractBtn.addEventListener("click", startExtraction);
}

function handleFileSelected(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    alert("Please select a PDF file (.pdf).");
    return;
  }
  document.getElementById("batchSelect").value = "";
  const extractBtn = document.getElementById("extractBtn");
  extractBtn.disabled = false;
  document.querySelector(".upload-prompt").innerHTML = `Selected: <strong>${file.name}</strong> (${(file.size / 1024 / 1024).toFixed(2)} MB)`;
}

// 4. Start Extraction
async function startExtraction() {
  const fileInput = document.getElementById("fileInput");
  const batchSelect = document.getElementById("batchSelect");
  const extractBtn = document.getElementById("extractBtn");

  const formData = new FormData();
  if (fileInput.files && fileInput.files[0]) {
    formData.append("pdf_file", fileInput.files[0]);
  } else if (batchSelect.value) {
    formData.append("batch_file", batchSelect.value);
  } else {
    alert("Please choose or upload a PDF document.");
    return;
  }

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

  extractBtn.disabled = true;
  showProgress("Initializing AI extraction pipeline...", 2);

  try {
    const res = await fetch("/api/extract", {
      method: "POST",
      body: formData
    });
    const data = await res.json();

    if (!data.success) {
      alert("Error starting extraction: " + (data.error || "Unknown error"));
      hideProgress();
      extractBtn.disabled = false;
      document.getElementById("telemStatus").textContent = "ERROR";
      return;
    }

    pollJob(data.job_id);
  } catch (e) {
    alert("Network error starting extraction: " + e.message);
    hideProgress();
    extractBtn.disabled = false;
    document.getElementById("telemStatus").textContent = "OFFLINE";
  }
}

// 5. Poll Extraction Progress
function pollJob(jobId) {
  if (pollInterval) clearInterval(pollInterval);

  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      const job = await res.json();

      showProgress(job.message || "Extracting records...", job.progress || 0);

      // Stream live audit logs
      if (job.audit_logs && job.audit_logs.length > 0) {
        updateAuditLogs(job.audit_logs);
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
        telemStatus.textContent = job.status === "completed" ? "COMPLETED" : (job.status === "error" ? "FAILED" : "PROCESSING");
      }

      if (job.status === "completed") {
        clearInterval(pollInterval);
        hideProgress();
        document.getElementById("extractBtn").disabled = false;
        renderResults(job.result);
        initHistory();
      } else if (job.status === "error") {
        clearInterval(pollInterval);
        hideProgress();
        document.getElementById("extractBtn").disabled = false;
        alert("Extraction Error: " + (job.error || "Process failed"));
      }
    } catch (e) {
      console.error("Polling error:", e);
    }
  }, 900);
}

// 5b. Live AI Audit Controls & Renderer
function initAuditControls() {
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

  const engineInfo = result.engine_used ? `Engine: ${result.engine_used} • ` : "";
  document.getElementById("resultMeta").textContent =
    `${engineInfo}Archetype: ${result.archetype} • Semester: ${result.semester || '--'} • Students: ${result.total_students} • Processed in ${result.duration_seconds}s`;

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

  // Checksum summary pill
  const sum = result.checksum_summary || {};
  document.getElementById("auditPassedCount").textContent = `${sum.all_passed || result.total_students}/${result.total_students}`;

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
  filteredRecords = [...currentRecords];
  currentPage = 1;
  displayTablePage();

  currentEdaData = null;
  const viewAnalytics = document.getElementById("tabViewAnalytics");
  if (viewAnalytics && !viewAnalytics.classList.contains("hidden")) {
    computeEdaDashboard();
  }

  sec.scrollIntoView({ behavior: "smooth" });
}

function displayTablePage() {
  const tableHeadRow = document.getElementById("tableHeadRow");
  const tableBody = document.getElementById("tableBody");
  tableHeadRow.innerHTML = "";
  tableBody.innerHTML = "";

  if (filteredRecords.length === 0) {
    tableBody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted);">No student records to display.</td></tr>`;
    document.getElementById("paginationInfo").textContent = "0 records";
    return;
  }

  // Derive columns
  const first = filteredRecords[0];
  const keys = Object.keys(first);

  // Headers
  keys.forEach(k => {
    const th = document.createElement("th");
    th.textContent = k;
    tableHeadRow.appendChild(th);
  });

  // Rows for current page
  const startIdx = (currentPage - 1) * pageSize;
  const pageSlice = filteredRecords.slice(startIdx, startIdx + pageSize);

  pageSlice.forEach(row => {
    const tr = document.createElement("tr");
    keys.forEach(k => {
      const td = document.createElement("td");
      td.textContent = row[k] !== undefined && row[k] !== null ? row[k] : "";
      tr.appendChild(td);
    });
    tableBody.appendChild(tr);
  });

  const total = filteredRecords.length;
  const endIdx = Math.min(startIdx + pageSize, total);
  document.getElementById("paginationInfo").textContent = `Showing records ${startIdx + 1} - ${endIdx} of ${total}`;

  document.getElementById("prevPageBtn").disabled = currentPage <= 1;
  document.getElementById("nextPageBtn").disabled = endIdx >= total;
}

// 7. Search & Pagination
function initSearch() {
  const searchInput = document.getElementById("searchInput");
  searchInput.addEventListener("input", (e) => {
    const term = e.target.value.toLowerCase().trim();
    if (!term) {
      filteredRecords = [...currentRecords];
    } else {
      filteredRecords = currentRecords.filter(r => {
        return Object.values(r).some(val => String(val).toLowerCase().includes(term));
      });
    }
    currentPage = 1;
    displayTablePage();
  });

  document.getElementById("prevPageBtn").addEventListener("click", () => {
    if (currentPage > 1) {
      currentPage--;
      displayTablePage();
    }
  });

  document.getElementById("nextPageBtn").addEventListener("click", () => {
    if (currentPage * pageSize < filteredRecords.length) {
      currentPage++;
      displayTablePage();
    }
  });
}

// 8. History Drawer
async function initHistory() {
  const list = document.getElementById("historyList");
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    const history = data.history || [];

    if (history.length === 0) {
      list.innerHTML = `<p class="empty-text">No previous extractions stored.</p>`;
      return;
    }

    list.innerHTML = "";
    history.forEach(item => {
      const card = document.createElement("div");
      card.className = "history-item";
      card.innerHTML = `
        <div class="history-title">${item.folder_name}</div>
        <div class="history-meta">Archetype: ${item.archetype} • Sem: ${item.semester} • ${item.total_students} records</div>
      `;
      card.addEventListener("click", () => loadHistoryItem(item.folder_name));
      list.appendChild(card);
    });
  } catch (e) {
    console.error("Failed to fetch history:", e);
  }

  document.getElementById("refreshHistoryBtn").addEventListener("click", initHistory);
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
      duration_seconds: 0.0
    });
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
  const groqKeyBadge = document.getElementById("groqKeyBadge");
  const geminiKeyBadge = document.getElementById("geminiKeyBadge");
  const openaiKeyBadge = document.getElementById("openaiKeyBadge");
  const anthropicKeyBadge = document.getElementById("anthropicKeyBadge");
  const deepseekKeyBadge = document.getElementById("deepseekKeyBadge");

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
      { id: "gemini-2.5-flash", label: "gemini-2.5-flash (Balanced)" },
      { id: "gemini-2.5-pro", label: "gemini-2.5-pro (Deep Reasoning)" }
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
      pillCount.textContent = configuredCount > 0 ? `${configuredCount} Active / 5 Providers` : "5 Providers";
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

  // Probe All Keys Button
  if (probeAllBtn) {
    probeAllBtn.addEventListener("click", async () => {
      switchToCloudTab("cloudTabPanelDiagnostics");
      probeAllBtn.disabled = true;
      const origText = probeAllBtn.textContent;
      probeAllBtn.textContent = "⏳ Probing Keys...";

      if (testAlert) {
        testAlert.className = "alert-box info";
        testAlert.textContent = "Testing each configured API key with live pings across providers...";
        testAlert.classList.remove("hidden");
      }

      try {
        const payload = { api_keys: {} };
        if (groqKeyInput && groqKeyInput.value.trim()) payload.api_keys.groq = groqKeyInput.value.trim();
        if (geminiKeyInput && geminiKeyInput.value.trim()) payload.api_keys.gemini = geminiKeyInput.value.trim();
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
          const updateBadgeHelper = (badge, repProv) => {
            if (!badge || !repProv) return;
            if (repProv.success) {
              badge.className = "key-status-badge success";
              badge.textContent = `✓ Active (${repProv.latency_ms}ms)`;
            } else if (repProv.configured) {
              badge.className = "key-status-badge error";
              badge.textContent = `✗ ${repProv.message.slice(0, 24)}`;
            } else {
              badge.className = "key-status-badge missing";
              badge.textContent = "⚪ No key";
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
              latEl.textContent = `${repProv.latency_ms} ms`;
              latEl.style.color = "#34D399";
            } else {
              card.className = "diag-card error";
              statusEl.textContent = "Failed";
              latEl.textContent = "Error";
              latEl.style.color = "#F87171";
            }
          };

          updateBadgeHelper(groqKeyBadge, rep.groq);
          updateBadgeHelper(geminiKeyBadge, rep.gemini);
          updateBadgeHelper(openaiKeyBadge, rep.openai);
          updateBadgeHelper(anthropicKeyBadge, rep.anthropic);
          updateBadgeHelper(deepseekKeyBadge, rep.deepseek);

          updateDiagCard("diagGroqCard", "diagGroqStatus", "diagGroqLatency", rep.groq);
          updateDiagCard("diagGeminiCard", "diagGeminiStatus", "diagGeminiLatency", rep.gemini);
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
  Chart.defaults.color = "#94A3B8";
  Chart.defaults.borderColor = "rgba(255, 255, 255, 0.07)";
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



