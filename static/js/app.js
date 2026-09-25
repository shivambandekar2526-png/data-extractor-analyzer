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

document.addEventListener("DOMContentLoaded", () => {
  initStatus();
  initBatches();
  initHistory();
  initUpload();
  initSearch();
  initAuditControls();
  initChatbox();
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
    item.className = `audit-entry type-${entry.type || 'system'}`;

    const hasDetails = entry.details !== undefined && entry.details !== null && entry.details !== "";
    const badgeText = entry.badge || (entry.type || 'LOG').toUpperCase();

    item.innerHTML = `
      <div class="audit-line-header">
        <span class="audit-time">${entry.timestamp || ''}</span>
        <span class="audit-badge badge-${entry.type || 'system'}">[${badgeText}]</span>
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
  document.getElementById("btnDownloadCsv").href = `/api/download/${folder}/${result.csv_file}`;
  document.getElementById("btnDownloadExcel").href = `/api/download/${folder}/${result.excel_file}`;
  document.getElementById("btnDownloadJson").href = `/api/download/${folder}/${result.json_file}`;

  // Checksum summary pill
  const sum = result.checksum_summary || {};
  document.getElementById("auditPassedCount").textContent = `${sum.all_passed || result.total_students}/${result.total_students}`;

  currentRecords = result.students || [];
  filteredRecords = [...currentRecords];
  currentPage = 1;
  displayTablePage();

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
        folder_name: currentFolderName
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

    const engineTag = data.engine_used ? ` <small class="text-muted">(${data.engine_used})</small>` : "";
    appendChatMessage("assistant", `${escapeHtml(data.reply)}${engineTag}`);
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

