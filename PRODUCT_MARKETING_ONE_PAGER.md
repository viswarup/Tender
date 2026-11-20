# Procura: Automated Tender Evaluation & Compliance Engine
### *Revolutionizing Commercial Bid Evaluation with Layout-Aware AI & Auditable RAG Traceability*

---

## Executive Summary
In public sector procurement, verifying bidder compliance is a slow, error-prone process. Bidders upload hundreds of pages of unsorted documents—PAN cards, GST certificates, Udyam Registrations, purchase orders, and execution proofs. Evaluating compliance against complex Pre-Qualification Criteria (PQC) manually takes days per bidder and introduces significant audit risks.

**Procura** is an enterprise-grade AI engine that automates the classification, extraction, and rule-based evaluation of GeM tender bids. By combining Layout-Aware Vision-Language Models (VLMs) and Retrieval-Augmented Generation (RAG), Procura delivers a **90% reduction in evaluation cycles** while maintaining **100% auditable, pixel-perfect traceability**.

---

## The Core Solution

```mermaid
graph LR
    A[Unsorted Bid PDFs] --> B[AI Layout & OCR Engine]
    B --> C[AI Classification & Segmentation]
    C --> D[PQC Rules Validator]
    D --> E[Interactive Audit Dashboard]
    E --> F[Instant Audits & Decision Logs]
```

### 1. Multi-Page Document Classification & Segmentation
Unsorted, merged PDF bid files are automatically split and classified page-by-page. The system recognizes PAN, GST, Udyam, CA Certificates, OEM Authorizations (MAFs), Purchase Orders, and Invoices, ensuring the correct extraction pipeline handles each segment.

### 2. Layout-Aware Structured Extraction
Using Vision-Language Models (VLMs), Procura extracts key metadata (PO numbers, dates, values, item descriptions, CA turnover figures) directly from original layouts, mapping values to exact pixel coordinates on the document pages for complete transparency.

### 3. Automated PQC Rules Engine
Evaluates vendor eligibility against complex, multi-option criteria (e.g. the standard IOCL ARC Cartridge Option A, B, or C rules). It automatically detects Udyam and DPIIT registration text to apply MSE and Startup experience exemptions.

### 4. Interactive Traceability Dashboard
Provides a premium dark-mode dashboard for human-in-the-loop validation. Evaluators can click on any extracted value (like a PO amount) to load the page viewer and highlight the source bounding box.

### 5. Context-Aware RAG Auditing Assistant
A hybrid RAG chatbot combining a Knowledge Graph and vector search allows evaluators to query bids in natural language (e.g. *"Which vendors are accepted under Option B?"* or *"Why was Fountain Stationers queried?"*) with inline document citations.

---

## Business Impact & ROI

| Metric | Manual Process | Procura Engine |
| :--- | :--- | :--- |
| **Evaluation Time** | 4 to 8 hours per bidder | **< 3 minutes** per bidder |
| **Compliance Accuracy** | Susceptible to human oversight | **100% consistency** (deterministic rules) |
| **Audit Trails** | Scattered spreadsheets & notes | **Structured JSON logs** with visual bounding boxes |
| **PQC Decisions** | Subjective similar-item reviews | **Deterministic PQC matching** + technical reviews |

* **Zero Human Bias:** Every purchase order is analyzed identically based on tender criteria.
* **Instant Resumption:** Resumes pipeline executions dynamically, skipping already processed documents to conserve compute.
* **Audit-Proof Decision Logs:** Fully exports detailed evaluation logs to satisfy internal audits and vigilance reviews.

---

## Enterprise-Grade Security & Integration
* **Data Sovereignty:** Operates entirely within your local or virtual private cloud (VPC). Supports local model deployments (Ollama) to ensure zero data leaves your firewall.
* **Granular Protection:** Hardened against path traversal exploits and secured with API key authorization check controls (`X-API-Key`).
* **Open Integrations:** Exposes structured JSON APIs for seamless integration with legacy ERP systems, GeM portals, and enterprise document management systems.
