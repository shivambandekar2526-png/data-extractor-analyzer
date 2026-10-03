# Data Extractor AI (`data_extractor_AI`)

An autonomous, multimodal AI academic result extraction and analysis engine supporting both Cloud Vision LLMs (Google Gemini, OpenRouter, Groq) and local models (Ollama).

Designed for zero-shift, high-precision tabular extraction across all university examination ledgers (CBCGS & NEP 2020) with guaranteed 100% student recall and algebraic verification.

---

## 🌟 Key Architecture & Capabilities

1. **Visual Blueprint & 2D Matrix Streaming**:
   - **Stage 1 Discovery**: Discovers physical column slots (1..N), decoupled course legends, dynamic assessment heads (`TH`, `IA`, `TW`, `PR`, `TOT`), and credit allocations from Page 1.
   - **Stage 2 2D Matrix**: Streams subsequent pages using an ultra-compact 2D positional matrix, reducing token overhead by ~80% (~4,680 tokens/page) and completing in ~8–12 seconds per page.
   - **In-Flight Rules Registry**: Learns elective course variations, bounds, and credit schemas dynamically, sharing context with subsequent pages without hardcoding.

2. **100% Student Recall Guarantee (Zero Missing Students)**:
   - Digital text anchoring exhaustively identifies all 6–10 digit candidate seat numbers on each page.
   - Page-level recovery automatically parses any student omitted in visual model output.
   - Document-wide reconciliation safety net verifies and recovers any omitted candidates prior to final export.

3. **Guaranteed Metadata & SGPI/CGPI Extraction**:
   - Unconditional population of `Student_Name`, `Seat_No`, `Gender` (`M`/`F` strictly determined by leading `/`), `Candidate_Type`, `PRN`, `Result_Status`, `Total_Marks`, `Credits_Earned`, `Total_CG`, `SGPI`, and `CGPI`.
   - Supports both numeric CGPI (`8.04`) and provisional/held status (`--`).
   - Historical semester metrics preserved across `Sem_I` through `Sem_VII`.

4. **Priority Cloud AI Failover & Anti-Thrashing**:
   - Prioritizes Google Gemini (`gemini-flash-latest`, `gemini-flash-lite-latest`).
   - Instantaneous failover to free OpenRouter vision candidates on rate limits or quota exhaustion.
   - Exponential cooldown backoff (60s $\rightarrow$ 120s $\rightarrow$ 240s $\rightarrow$ 480s) parks cooling models to eliminate thrashing loops.

5. **Dynamic Relation Engine & Spotlight Micro-Healer**:
   - Algebraic invariant verification:
     $$\text{Theory} + \text{Internal} = \text{Total Marks}$$
     $$\text{Credits} \times \text{GP} = \text{Credit Points (CG)}$$
     $$\sum \text{Course Credits} = \text{Semester Credits}$$
     $$\sum \text{Course CG} = \text{Total CG}$$
     $$\frac{\text{Total CG}}{\text{Total Credits}} = \text{SGPI}$$
   - When conflicting numbers are detected, sends focused visual crops to the model to resolve numbers in-flight.

6. **Academic Excel Analysis Generator (6 Sheets)**:
   - Generates university-standard department workbooks:
     1. `Summary`: Overall cohort statistics, pass percentage, appear vs. pass counts.
     2. `Course Statistics`: Course pass rates, averages, and difficulty bottlenecks.
     3. `Component Analytics`: Theory vs. Internal vs. Practical performance.
     4. `Toppers List`: Rank-ordered merit lists based on SGPI and Total Marks.
     5. `SGPI Distribution`: Grade bracket frequency distributions ($\ge 9.0$, $8.0$–$9.0$, etc.).
     6. `Detailed Student Ledger`: Full student dataset with dual-identifier headers.

---

## 🚀 Quick Start Guide

### 1. Install Dependencies
```bash
python -m pip install -r data_extractor_AI/requirements.txt
```

### 2. Launch the Web Application
```bash
python data_extractor_AI/app.py
```
*Starts local server at `http://127.0.0.1:5001` and opens your browser automatically.*

### 3. Configure API Keys
Click **Settings** on the top navigation bar to configure your **Google Gemini API Key** or **OpenRouter API Key**. Settings are stored securely locally.

---

## 📁 Application Layout

```
data_extractor_AI/
├── app.py                  # Flask Web Server (Port 5001) & REST API
├── models.py               # Domain Dataclasses (StudentRecord, CourseCatalog, ExamMeta)
├── exporter.py             # CSV, Excel (.xlsx), and JSON Catalog Exporter
├── requirements.txt        # Python Dependencies
├── core/                   # Core Engine Subsystems
│   ├── visual_extractor.py # Visual Blueprint & 2D Matrix Streaming Pipeline
│   ├── cloud_client.py     # Cloud AI Client (Gemini, OpenRouter, Groq with Failover)
│   ├── document_spec.py    # DocumentExecutionSpec & In-Flight Dynamic Rules Registry
│   ├── dynamic_relation_engine.py # Mathematical Auditor & In-Flight Auto-Healer
│   ├── spotlight_healer.py # Targeted Visual Discrepancy Micro-Healer
│   ├── excel_report_generator.py # 6-Sheet Academic Analysis Workbook Generator
│   ├── eda_engine.py       # AI Exploratory Data Analysis & Statistics Engine
│   ├── chat_assistant.py   # Conversational Ledger AI Assistant
│   ├── layout_mapper.py    # Visual Table Grid & Coordinate Mapper
│   └── page_ingester.py    # PDF Text & OCR Ingestion Layer
├── tests/                  # 88 Unit & Integration Tests (100% Passing)
├── knowledge_base/         # Archetype Schemas & Few-Shot Learning Registry
├── trainer/                # Knowledge Base Indexer & Few-Shot Trainer
├── input/                  # Runtime Upload Directory (Preserved with .gitkeep)
├── output/                 # Runtime Output Directory (Preserved with .gitkeep)
├── templates/
│   └── index.html          # Web Dashboard UI
└── static/
    ├── css/style.css       # Dark/Light Modern Stylesheet
    └── js/app.js           # Live Event Stream & Interactivity
```

---

## 🧪 Running the Test Suite
```bash
python -m unittest discover -s data_extractor_AI/tests -p "test_*.py"
```
*All 88 tests pass cleanly in ~7–15 seconds.*
