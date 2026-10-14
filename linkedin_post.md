# LinkedIn Post — EpiNet Visualizer

---

**Why does the same drug work brilliantly for one patient and fail another — even when both carry the "right" variant?**

Because no single gene tells the whole story. Drug metabolism is a network phenomenon. The moment you start modeling gene-gene interactions (epistasis), the picture changes entirely.

That's what drove me to build **EpiNet Visualizer** — a full-stack pharmacogenomics dashboard that takes a CSV of gene expression data, scores every gene pair for non-additive interaction (the extra variance a product term explains in a regression), and returns an interactive gene network showing which pairs interact most strongly to influence drug response.

Here's what's under the hood:

**☕ Spring Boot (Java 17 / 21)**
- Async REST API — upload returns immediately (202), frontend polls every 2 s with automatic timeout
- Spring Data JPA + MySQL 8 for run persistence (with in-memory H2 fallback)
- Global exception handler, file validation (type, size, UTF-8 BOM, required columns), CORS config

**⚛️ React 18 + Vite + Tailwind CSS 3**
- Force-directed gene interaction network (react-force-graph-2d) with memoized D3 layout — edge thickness scales with score, threshold slider, click-to-highlight
- Sortable, filterable biomarker pairs table + Recharts score bar chart
- Full analysis history — reload any past result

**🐍 Python + FastAPI (ML microservice)**
- POST /analyze fits additive vs. interaction regression models for all 45 gene pairs and scores each by ΔR²
- Synthetic sample data with 3 planted interactions (CYP2C9×VKORC1, CYP2D6×CYP3A4, SLCO1B1×ABCB1) to test recovery
- 17 pytest tests covering validation, symmetry, determinism and planted-pair recovery

**Transparency note:** this runs on synthetic data with a classical interaction regression, not a trained attention model. The scoring function is built to be swapped for a graph-attention or transformer model. Honesty in demo projects matters.

🔗 **https://github.com/Midun523/epinet-visualizer** | 📸 **[SCREENSHOT/GIF]**

I'd love feedback from anyone working at the intersection of bioinformatics, AI drug discovery, and clinical informatics — this is the space I want to build in.

#Bioinformatics #ComputationalBiology #SpringBoot #React #AIDrugDiscovery #Pharmacogenomics
