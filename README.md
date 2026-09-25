# Data Extractor AI (`data_extractor_AI`)

An autonomous, self-learning local AI academic result extraction engine powered by local Vision-Language/LLM models running on your PC (Ollama / RTX 4060 GPU).

---

## 🌟 Key Features

1. **Pre-Trained Knowledge Base**:
   - Ships with pre-indexed layout possibilities and few-shot exemplars across all 4 major academic result archetypes:
     - **Tabulation (Sem III-VI CBCGS)**: Dynamic 31-35 column forward-fill grids.
     - **Gazette Classic (Sem VIII C-Scheme)**: Compact legend header with historical SGPI records.
     - **Gazette Spool (Sem VII)**: Continuous 12-paper two-tier tape spool.
     - **NEP 2020 Scheme (Sem III/IV)**: 5-tier evaluation components (`T1`, `O1`, `E1`, `I1`).
2. **100% Local, Offline & Private**:
   - Runs directly on your NVIDIA RTX 4060 GPU using Ollama (default model: `qwen2.5:7b`).
   - Zero API fees, zero internet requirements, zero data transmission outside your PC.
3. **Deterministic Mathematical Proof Gate**:
   - 4-tier checksum audit: Component Sum ($\text{TH} + \text{IA} = \text{TOT}$), Course CG ($\text{Credits} \times \text{GP} = \text{CG}$), Semester Total CG ($\sum \text{Course CG} = \ddot{a}\text{CG}$), and SGPI ($\ddot{a}\text{CG} / \ddot{a}\text{C} = \text{SGPI}$).
   - Targeted self-healing on any detected anomaly.
4. **Autonomous Incremental Learning**:
   - Automatically adapts when new PDF documents are uploaded and verified, adjusting the few-shot memory without manual retraining.
5. **Dual-Identifier Export**:
   - Generates clean UTF-8 CSV, styled Excel (.xlsx with frozen panes and Course Catalog sheet), and structured `_catalog.json` metadata compatible with downstream analysis scripts.

---

## 🚀 Quick Start Guide

### 1. Prerequisites (Ollama Local Runner)
Ensure Ollama is installed on your Windows system:
1. Download from [ollama.com/download](https://ollama.com/download).
2. Pull the recommended high-accuracy 7B model:
   ```bash
   ollama pull qwen2.5:7b
   ```

### 2. Install Python Dependencies
```bash
python -m pip install -r data_extractor_AI/requirements.txt
```

### 3. Launch the Application
```bash
python data_extractor_AI/app.py
```
*Starts local server at `http://127.0.0.1:5001` and opens your browser automatically.*

---

## 📁 Application Layout

```
data_extractor_AI/
├── app.py                  # Flask Web Server (Port 5001)
├── models.py               # Universal semester-agnostic dataclasses
├── exporter.py             # CSV, Excel (.xlsx), and JSON Catalog exporter
├── requirements.txt        # Dependencies
├── core/
│   ├── ollama_client.py    # Standard-library Ollama HTTP client
│   ├── page_ingester.py    # Native text extraction with RapidOCR fallback
│   ├── possibility_classifier.py # Dynamic archetype and page classifier
│   ├── prompt_builder.py   # Context-aware few-shot prompt generator
│   ├── json_parser.py      # LLM response parser & normalizer
│   ├── checksum_verifier.py# Mathematical verification auditor
│   ├── self_healer.py      # Targeted LLM self-healing & proof reconciliation
│   └── extractor.py        # Master pipeline orchestrator & auto-learner
├── trainer/
│   └── train_kb.py         # Knowledge base builder & re-indexer
├── knowledge_base/
│   ├── archetypes.json     # Learned archetype layout rules
│   ├── schemas.json        # Course catalogs & component heads
│   └── exemplars/          # Few-shot training pairs per archetype
│       ├── tabulation.json
│       ├── gazette_classic.json
│       ├── gazette_spool.json
│       └── gazette_nep.json
├── input/                  # User PDF uploads
├── output/                 # Output directories per extraction
├── templates/
│   └── index.html          # Modern dashboard UI
└── static/
    ├── css/style.css       # Dark theme stylesheet
    └── js/app.js           # Frontend interactivity & real-time polling
```

---

## 🛠 Re-Indexing / Training
The app already ships **pre-trained** on all historical batches in `Batch Data/`.
If you add more historical batches and want to refresh the base knowledge base:
```bash
python data_extractor_AI/trainer/train_kb.py
```
