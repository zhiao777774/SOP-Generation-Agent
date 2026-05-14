# SOP Generation Agent 重構計畫

## 0. 文件目的

本文件是給 Codex / 開發代理使用的工程規格與實作計畫。

本專案目標是重構並重做一個既有的 SOP Generation pipeline。舊版系統是一個簡單的 Gradio 工具，可以上傳來源 PDF、維修紀錄 Excel、SOP DOCX 模板，並產生最終 SOP DOCX。新版希望把它升級成一個更泛化、更穩定、更好用的 **SOP Generation Agent**。

此系統不是單純摘要工具，也不是一般 RAG QA 系統，而是：

> A template-aware, source-grounded, reference-augmented, section-constrained SOP generation agent.

中文描述：

> 一個以 SOP 模板為結構約束、以廠商來源文件為主要依據、以參考文件為補充資料，並能自動產出 DOCX SOP 的文件生成 Agent。

---

## 1. Core Invariant

整個系統必須滿足以下核心不變式：

```text
Vendor/source PDFs define the authoritative content to be covered.
The DOCX template defines where content is allowed to appear.
Reference documents provide optional supporting evidence only.
```

中文說法：

```text
廠商來源 PDF 決定主要內容與必須覆蓋的資訊。
DOCX SOP 模板決定內容可以出現在哪些章節。
參考文件只能提供補充證據，不可取代來源 PDF。
```

重要規則：

```text
If source content cannot be confidently mapped to any template section,
do not force it into the final SOP.
Record it in the coverage report instead.
```

也就是：

```text
如果來源 PDF 的某段內容找不到合適模板章節，
不要硬塞進最終 SOP，
而是記錄在 coverage report。
```

---

## 2. Current System Overview

目前系統是一個簡單的 Gradio-based SOP generation pipeline。

### 2.1 Current Inputs

```text
- 一份來源文件 PDF
- 可選的多份 Excel 維修紀錄
- 一份 DOCX SOP 模板
  - 模板裡需要使用者預先寫入類似 Jinja2 的 placeholder
- 模型選擇
```

### 2.2 Current Flow

目前流程大致如下：

```text
1. 使用者透過 Gradio UI 上傳：
   - source PDF
   - optional Excel maintenance records
   - SOP template DOCX
   - selected model

2. 透過 DeepSeek-OCR model 解析來源 PDF。

3. 將上傳的維修紀錄 Excel 解析成序列字串。
   - 目前 Excel 欄位名稱有點寫死。
   - 泛化性不足。

4. 將維修紀錄序列字串轉成 embeddings。

5. 將 embedding / index 資料存成 PKL。

6. 利用 LLM 根據解析後的來源文件生成多組查詢語句。

7. 透過自定義 IR 方法，針對每個 query 檢索相關維修紀錄。

8. 將檢索結果去重，形成一組 retrieved maintenance records。

9. 使用固定的章節名稱與 Jinja placeholder mapping。

10. 針對每個章節進行生成：
    - 每個章節都看同一份 parsed source document
    - 每個章節都看同一組 retrieved maintenance records
    - 產生該章節的 Markdown 內容

11. 使用 python-docx 複製 DOCX 模板。

12. 根據固定 mapping，把模板裡的 Jinja-like placeholder 替換成生成內容。

13. 將 Markdown 轉成 DOCX elements，例如：
    - heading
    - paragraph
    - bullet list
    - numbered list
    - table

14. 最終產出完整 DOCX。

15. Gradio 顯示下載按鈕，使用者下載 final SOP DOCX。
```

---

## 3. Current Pain Points

目前系統的主要問題如下。

### 3.1 UI 問題

```text
- Gradio UI 只是基本元件堆疊。
- 沒有好看的 layout / style。
- 不適合做成較正式的企業內部工具。
- 多檔上傳、進度顯示、中間結果預覽、prompt 設定、debug report 等功能會越做越卡。
```

### 3.2 輸入限制

```text
- 目前只支援一份 source PDF。
- 新系統需要支援多份 source PDFs。
```

### 3.3 Reference 文件限制

```text
- 目前 reference / maintenance data 基本上只支援 Excel。
- 新系統需要支援：
  - Excel
  - PDF
  - Markdown
  - TXT
```

### 3.4 Excel 欄位寫死

```text
- 現在 Excel 欄位名稱有 hard-coded assumption。
- 不同客戶或不同資料來源的欄位名稱可能不同。
- 新系統應該要有 schema inference / alias mapping / raw field preservation。
```

### 3.5 Template 需要使用者懂 Jinja

```text
- 舊系統要求使用者在 DOCX 模板中手寫 Jinja-like placeholder。
- 這對一般使用者不友善。
- 新系統應該不要求使用者懂 Jinja。
- 新系統應該自動解析 DOCX 模板章節。
```

### 3.6 Section Mapping 寫死

```text
- 舊系統使用固定的 section name -> placeholder mapping。
- 新系統應該以 DOCX template 本身為主，解析出章節結構。
```

### 3.7 每個章節看到同一批 evidence

```text
- 舊系統每個章節都拿同一份 source document + 同一組 retrieved records。
- 這容易造成：
  - 內容重複
  - reference 被亂塞
  - 不同章節 evidence 不精準
- 新系統應該要 section-aware evidence。
```

### 3.8 Source coverage 沒有明確追蹤

```text
- 舊系統沒有明確記錄來源 PDF 哪些內容被用到。
- 新系統應該要有 source coverage mapping。
- 每個 source chunk 應該被標記為：
  - mapped to section
  - unmapped
  - used in generation
```

### 3.9 Reference 文件可能被過度使用

```text
- 舊流程中 reference retrieved records 可能影響太大。
- 新系統必須 enforce：
  - source-first
  - reference-supplement-only
  - template-constrained
```

### 3.10 中間產物不夠結構化

```text
- 舊系統可能只有 PKL 或零散暫存資料。
- 新系統應該保存清楚的 intermediate artifacts：
  - parsed source docs
  - parsed reference docs
  - template structure
  - section plan
  - source mapping
  - generated sections
  - debug report
  - coverage report
```

### 3.11 DOCX rendering 與 generation 耦合

```text
- 舊系統中 markdown -> docx、placeholder replacement、generation logic 容易混在一起。
- 新系統應該將 rendering 獨立成模組。
```

---

## 4. Migration Intent

這是一個重構 / rebuild，不是完全無關的新產品。

### 4.1 Preserve

應保留的核心能力：

```text
- DeepSeek-OCR based source PDF parsing
- custom IR method if available
- Markdown-based intermediate section generation
- python-docx based final DOCX rendering if still suitable
- final output as one DOCX file
```

### 4.2 Replace or Redesign

應重做或重新設計的部分：

```text
- Gradio default UI
- single source PDF limitation
- Excel-only reference handling
- hard-coded Excel columns
- Jinja-placeholder dependency
- fixed section-placeholder mapping
- same evidence for every section
- unstructured PKL-only embedding cache
- lack of coverage report
- lack of structured debug artifacts
```

---

## 5. Target Product Behavior

新版系統稱為 **SOP Generation Agent**。

### 5.1 Target Inputs

```text
Source Documents:
  - 多份 PDF
  - 廠商文件、原廠手冊、設備說明書、規範文件
  - 是主要權威來源

Reference Documents:
  - 多份 Excel / PDF / Markdown / TXT
  - 維修紀錄、現場經驗、異常處理紀錄、補充文件
  - 只能作為補充

Template:
  - 一份 DOCX
  - SOP 模板
  - 不要求使用者手寫 Jinja placeholder
```

### 5.2 Target Output

```text
- final_sop.docx
- optional coverage_report.json
- optional debug_report.json
```

### 5.3 Target User Flow

```text
1. Upload
   - 上傳多份 source PDFs
   - 上傳多份 reference files: Excel / PDF / MD / TXT
   - 上傳一份 template DOCX

2. Analyze
   - 系統解析 template sections
   - 系統解析 source PDFs
   - 系統解析 reference files
   - 系統預覽 Excel 欄位推測結果
   - 系統預覽偵測到的 SOP 章節

3. Configure
   - 選模型
   - 選語言
   - 選詳細程度
   - 選是否加入 reference cases
   - 設定 optional user instruction

4. Generate
   - 顯示 pipeline 進度
   - 顯示目前處理步驟
   - 顯示 warning / error

5. Download
   - 下載 final SOP DOCX
   - 可選下載 debug report / coverage report
```

---

## 6. Product Rules

### 6.1 Document Priority

```text
Priority 1: Source PDFs
- 廠商文件 / 原廠文件
- SOP 主體內容
- 必須盡可能完整映射到模板章節
- 不可被 reference 覆蓋

Priority 2: DOCX Template
- 決定最終章節結構
- 內容只能填入合適章節
- 沒有合適章節就不要硬塞

Priority 3: Reference Documents
- 維修紀錄 / 現場經驗 / 補充資料
- 只能補強
- 不可取代 source PDFs
- 與 source PDFs 衝突時，以 source PDFs 為準
```

### 6.2 Allowed Behavior

```text
- 根據模板章節重組 source PDF 內容。
- 對 source PDF 內容進行 SOP 化改寫。
- 將 reference 文件中高度相關的實務經驗補充到合適章節。
- 對於無法映射的 source content，記錄到 coverage report。
- 輸出符合 SOP 文件語氣的 DOCX。
```

### 6.3 Forbidden Behavior

```text
- 不可直接整段複製 source PDF 原文作為唯一處理方式。
- 不可讓 reference documents 取代 source PDFs。
- 不可把不相關內容硬塞進章節。
- 不可編造不存在的原廠規定、維修紀錄或數值。
- 不可因為模板沒有對應章節，就任意新增大量新章節。
- 不可讓使用者自訂 prompt 覆蓋 core policy。
```

---

## 7. Deployment Constraint

限制：

```text
只能產出一個 Docker image。
```

因此採用：

```text
single Docker image + modular monolith
```

不要拆成多個微服務。

### 7.1 Recommended Runtime

```text
Docker Image
  ├── FastAPI backend
  ├── React/Vite frontend build static files
  ├── SOP pipeline modules
  ├── model/API clients
  ├── local artifact storage
  └── job runtime
```

啟動方式：

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 7860
```

React build 後由 FastAPI serve static files。

---

## 8. UI Recommendation

### 8.1 Preferred

```text
React / Vite + Tailwind + FastAPI
```

### 8.2 Acceptable MVP Alternative

```text
FastAPI + server-rendered HTML + HTMX + Tailwind
```

### 8.3 Not Preferred

```text
Basic Gradio UI
```

Gradio 可以做 demo，但不建議當新版正式 UI。

---

## 9. Suggested Project Structure

```text
sop_generation_agent/
  backend/
    app/
      main.py
      api/
        routes_jobs.py
        routes_artifacts.py
        routes_config.py

      services/
        job_service.py
        artifact_service.py

      core/
        config.py
        logging.py
        errors.py

      pipeline/
        pipeline.py
        state.py
        schemas.py

      ingestion/
        source_pdf_loader.py
        reference_loader.py
        excel_loader.py
        pdf_loader.py
        markdown_loader.py
        text_loader.py
        docx_template_loader.py

      normalization/
        excel_schema_inferencer.py
        record_serializer.py
        chunker.py

      planning/
        template_analyzer.py
        section_planner.py
        source_section_mapper.py

      indexing/
        embedder.py
        vector_index.py
        cache.py

      retrieval/
        source_retriever.py
        reference_retriever.py
        evidence_merger.py

      generation/
        prompt_builder.py
        query_generator.py
        section_generator.py
        validators.py

      rendering/
        docx_renderer.py
        markdown_to_docx.py
        style_mapper.py

      reports/
        coverage_report.py
        debug_report.py

  frontend/
    src/
      pages/
      components/
      api/
      types/
      styles/
    package.json
    vite.config.ts

  tests/
    unit/
    integration/
    fixtures/

  Dockerfile
  pyproject.toml
  README.md
```

---

## 10. Core Data Models

Use Pydantic.

### 10.1 SOPGenerationRequest

```python
class SOPGenerationRequest(BaseModel):
    job_id: str
    source_pdf_paths: list[str]
    reference_file_paths: list[str]
    template_docx_path: str
    model_config: ModelConfig
    generation_profile: GenerationProfile
    user_instruction: str | None = None
```

### 10.2 GenerationProfile

```python
class GenerationProfile(BaseModel):
    language: str = "zh-TW"
    tone: str = "professional"
    verbosity: str = "balanced"  # concise | balanced | detailed
    include_reference_cases: bool = True
    prioritize_safety: bool = True
    preserve_vendor_terminology: bool = True
    allow_new_sections: bool = False
```

### 10.3 SourceDocument

```python
class SourceDocument(BaseModel):
    document_id: str
    file_name: str
    raw_text: str
    chunks: list["SourceChunk"]
    metadata: dict = {}
```

### 10.4 SourceChunk

```python
class SourceChunk(BaseModel):
    chunk_id: str
    document_id: str
    file_name: str
    page_start: int | None = None
    page_end: int | None = None
    content: str
    metadata: dict = {}
```

### 10.5 ReferenceDocument

```python
class ReferenceDocument(BaseModel):
    document_id: str
    file_name: str
    file_type: str  # excel | pdf | md | txt
    items: list["ReferenceItem"]
    metadata: dict = {}
```

### 10.6 ReferenceItem

```python
class ReferenceItem(BaseModel):
    item_id: str
    document_id: str
    file_name: str
    item_type: str  # structured_record | unstructured_chunk
    content: str
    metadata: dict = {}
```

### 10.7 TemplateSection

```python
class TemplateSection(BaseModel):
    section_id: str
    title: str
    level: int
    start_block_index: int
    end_block_index: int | None = None
    existing_text: str = ""
    expected_content_type: str | None = None
```

### 10.8 TemplateStructure

```python
class TemplateStructure(BaseModel):
    template_id: str
    file_name: str
    sections: list[TemplateSection]
    metadata: dict = {}
```

### 10.9 SectionEvidencePacket

```python
class SectionEvidencePacket(BaseModel):
    section_id: str
    source_chunks: list[SourceChunk]
    reference_items: list[ReferenceItem]
```

### 10.10 GeneratedSection

```python
class GeneratedSection(BaseModel):
    section_id: str
    title: str
    markdown_content: str
    used_source_chunk_ids: list[str]
    used_reference_item_ids: list[str]
    warnings: list[str] = []
```

### 10.11 SourceCoverageReport

```python
class SourceCoverageReport(BaseModel):
    mapped_chunks: dict[str, str]  # chunk_id -> section_id
    unmapped_chunks: list[str]
    section_to_chunks: dict[str, list[str]]
    warnings: list[str]
```

---

## 11. Pipeline Design

### 11.1 Main Pipeline

```python
class SOPGenerationPipeline:
    def run(self, request: SOPGenerationRequest) -> SOPGenerationResult:
        source_docs = self.source_pdf_loader.load_many(
            request.source_pdf_paths
        )

        reference_docs = self.reference_loader.load_many(
            request.reference_file_paths
        )

        template = self.docx_template_loader.load(
            request.template_docx_path
        )

        section_plan = self.section_planner.plan(
            template=template,
            source_documents=source_docs,
            generation_profile=request.generation_profile,
        )

        source_mapping = self.source_section_mapper.map_source_chunks_to_sections(
            source_docs=source_docs,
            sections=section_plan.sections,
        )

        reference_index = self.vector_index.build_reference_index(
            reference_docs
        )

        generated_sections = []

        for section in section_plan.sections:
            source_chunks = source_mapping.get_chunks_for_section(
                section.section_id
            )

            reference_items = self.reference_retriever.retrieve_for_section(
                section=section,
                source_chunks=source_chunks,
                reference_index=reference_index,
                top_k=10,
            )

            section_output = self.section_generator.generate(
                section=section,
                source_chunks=source_chunks,
                reference_items=reference_items,
                generation_profile=request.generation_profile,
                user_instruction=request.user_instruction,
            )

            validated = self.validators.validate_section(
                section_output=section_output,
                source_chunks=source_chunks,
                reference_items=reference_items,
            )

            generated_sections.append(validated)

        coverage_report = self.coverage_report_builder.build(
            source_mapping=source_mapping,
            generated_sections=generated_sections,
        )

        output_docx_path = self.docx_renderer.render(
            template_path=request.template_docx_path,
            template_structure=template,
            generated_sections=generated_sections,
        )

        debug_report_path = self.debug_report_builder.save(...)

        return SOPGenerationResult(
            output_docx_path=output_docx_path,
            coverage_report_path=coverage_report_path,
            debug_report_path=debug_report_path,
        )
```

---

## 12. Ingestion Requirements

### 12.1 Source PDF Loader

Input:

```text
多份 source PDFs
```

Output:

```text
SourceDocument[]
```

Requirements:

```text
- 使用 DeepSeek-OCR model 解析 PDF。
- 每份 PDF 保留 file_name。
- 盡可能保留 page number。
- 切 chunk 時保留 document_id / page metadata。
- source PDF 是主體資料，不要與 reference 混在同一個 corpus 中。
```

### 12.2 Reference Loader

支援格式：

```text
- .xlsx / .xls
- .pdf
- .md
- .txt
```

行為：

```text
Excel:
  - 解析每個 row 成 structured_record。
  - 欄位名稱不可寫死。
  - raw_fields 必須保留。
  - 透過 alias matching 做欄位推測。
  - 後續可加 LLM schema inference。

PDF:
  - 解析成 unstructured chunks。

Markdown:
  - 依 heading / paragraph chunk。

TXT:
  - 依段落或 token window chunk。
```

### 12.3 Excel Schema Inference

第一版先做 alias matching。

```python
COLUMN_ALIASES = {
    "date": ["日期", "發生日期", "維修日期", "date"],
    "equipment_name": ["設備", "設備名稱", "機台", "device", "equipment"],
    "symptom": ["異常現象", "故障現象", "問題描述", "symptom", "issue"],
    "root_cause": ["原因", "異常原因", "root cause", "cause"],
    "action_taken": ["處理方式", "改善措施", "維修內容", "action", "solution"],
    "result": ["結果", "處理結果", "result"],
}
```

對於無法分類的欄位：

```text
- 不要丟掉。
- 保留在 raw_fields。
- serialization 時仍可納入完整內容。
```

---

## 13. Template Analyzer

新系統不要求使用者手寫 Jinja placeholder。

第一版採用 heading-based template filling。

### 13.1 DOCX Template Parser

Input:

```text
一份 DOCX SOP template
```

Output:

```text
TemplateStructure
```

Requirements:

```text
- 偵測 Word heading styles：
  - Heading 1
  - Heading 2
  - 標題 1
  - 標題 2

- 如果沒有 heading style，使用文字 pattern 偵測：
  - 一、xxx
  - 二、xxx
  - 1. xxx
  - 1.1 xxx
  - 第x章
  - 第x節

- 每個 section 保留：
  - section_id
  - title
  - level
  - start_block_index
  - end_block_index
  - existing_text
```

### 13.2 MVP Constraint

第一版不需要支援任意複雜 DOCX layout。

MVP 只需要支援：

```text
- heading-based template
- 清楚章節標題
- 標題下方插入或替換內容
```

---

## 14. Section Planning

### 14.1 Section Planner Input

```text
- TemplateStructure
- SourceDocument[]
- GenerationProfile
```

### 14.2 Section Planner Output

```text
SectionPlan
```

### 14.3 Rules

```text
- 以 template sections 為主。
- 不要任意新增章節。
- 如果 allow_new_sections=False，所有內容只能塞進既有章節。
- source chunk 找不到對應 section 時，放到 unmapped。
```

---

## 15. Source Coverage Mapping

這是新版系統的核心能力之一。

### 15.1 Goal

將 source PDFs 的內容最大程度映射到合適模板章節。

注意：不是所有 source content 都必須硬塞進 final SOP。

正確規則：

```text
Source PDF content should be maximally covered when it is relevant to existing template sections.
If source content has no suitable section, it must be reported as unmapped instead of being forced into the output.
```

### 15.2 Mapping Input

```text
- source_chunks
- template_sections
```

### 15.3 Mapping Output

```text
chunk_id -> section_id | unmapped
```

### 15.4 Suggested Method

第一版可以混合：

```text
1. Embedding similarity
   - source chunk embedding
   - section title + existing section text embedding

2. LLM classification for low-confidence chunks
   - 適合哪個 section？
   - 如果都不適合，回傳 unmapped。
```

### 15.5 Mapping Rules

```text
- 不要為了 coverage 把內容硬塞到不相關 section。
- 若最高分低於 threshold，標記 unmapped。
- 每個 generated section 必須知道自己用了哪些 source chunks。
- coverage report 必須包含 mapped / unmapped 結果。
```

---

## 16. Retrieval Design

### 16.1 Separate Source and Reference

重要：

```text
Source documents and reference documents must be stored and retrieved separately.
```

不要把 source PDFs 與 reference documents 混成同一個 corpus。

原因：

```text
- source 是權威主體。
- reference 是補充資料。
- 如果混在同一個 corpus，reference 可能過度主導生成。
```

### 16.2 Source Evidence

Source evidence 不應該主要靠 query retrieval，而是靠 source coverage mapping。

```text
source_chunks
  -> source-to-section mapping
  -> section source evidence
```

### 16.3 Reference Evidence

Reference evidence 使用 section-aware retrieval。

Query 來源：

```text
- section title
- section expected content type
- mapped source chunks summary
```

### 16.4 Evidence Packet

每個 section 都應該有自己的 evidence packet：

```text
section_id:
  source_chunks:
    - required / primary evidence

  reference_items:
    - optional / supplementary evidence
```

---

## 17. Prompt Design

### 17.1 Prompt Layer

不要把 prompt 寫成一個巨大字串。

使用 PromptBuilder 組裝：

```text
Core System Policy         # 不可修改
Generation Profile         # UI 控制
Section Instruction        # 系統產生
User Extra Instruction     # 使用者可修改
Source Context             # 主體 evidence
Reference Context          # 補充 evidence
Output Format Instruction  # Markdown
```

### 17.2 Core System Policy

固定不可修改：

```text
You are a SOP generation agent.

Rules:
1. The source vendor documents are the primary authority.
2. The DOCX template defines the final structure.
3. Reference documents can only supplement the source documents.
4. If source and reference conflict, follow the source documents.
5. Do not invent facts.
6. Do not force unrelated information into a section.
7. If information does not fit any section, leave it unmapped.
8. Preserve important warnings, constraints, safety notes, procedures, and maintenance requirements from the source documents.
9. Output only content suitable for the current section.
10. Use Markdown formatting only.
```

### 17.3 User Editable Instruction

使用者可以修改的是額外指令，不是核心規則。

允許使用者調整：

```text
- language
- tone
- verbosity
- output style
- extra instruction
```

禁止使用者覆蓋：

```text
- source-first
- template-constrained
- reference-supplement-only
- no hallucination
```

### 17.4 Example User Instruction

```text
請用繁體中文。
請讓內容更適合現場工程師閱讀。
請步驟化描述。
請安全注意事項寫詳細一點。
```

### 17.5 Section Generation Prompt Template

```text
You are generating one section of a SOP document.

Section:
{section_title}

Existing template text:
{existing_text}

Primary source evidence:
{source_chunks}

Supplementary reference evidence:
{reference_items}

Generation profile:
{profile}

User extra instruction:
{user_instruction}

Instructions:
- Use primary source evidence as the main content.
- Use supplementary reference evidence only when clearly relevant.
- Do not override source evidence with reference evidence.
- Do not include unrelated content.
- Do not add content that does not belong to this section.
- If there is not enough information, produce a concise section based only on available evidence.
- Output Markdown only.
```

---

## 18. Generation Validation

每個 section generation 後應做 basic validation。

### 18.1 Validate Section Output

檢查：

```text
- 是否有 generated markdown content
- 是否記錄 used_source_chunk_ids
- 是否 reference 被過度使用
- 是否出現明顯與 source 衝突的內容
- 是否產生不屬於該 section 的內容
```

### 18.2 Hallucination Control

第一版不需要做完美 factuality checking，但至少要：

```text
- prompt 層強制禁止 invent facts
- output metadata 記錄 used evidence IDs
- debug report 中保留 evidence packet 與 generated content
```

---

## 19. DOCX Rendering

DOCX rendering 是高風險區，必須獨立模組化與測試。

### 19.1 Rendering Strategy

第一版策略：

```text
- 複製 template DOCX。
- 找到每個 section heading。
- 對於該 section：
  - 清掉原本 placeholder / 範例內容，或
  - 插入在 heading 後方。
- 將 generated markdown 轉成 Word paragraphs / tables / lists。
- 盡量保留 template styles。
```

### 19.2 Renderer Interface

```python
class DocxRenderer:
    def render(
        self,
        template_path: str,
        template_structure: TemplateStructure,
        generated_sections: list[GeneratedSection],
    ) -> str:
        ...
```

### 19.3 Markdown to DOCX Support

第一版支援：

```text
- paragraphs
- headings
- bullet lists
- numbered lists
- simple tables
- bold / italic optional
```

先不要過度支援：

```text
- complex nested tables
- advanced Word layout
- images
- charts
- cross references
```

### 19.4 Important DOCX Notes

python-docx 常見風險：

```text
- placeholder 可能被拆成多個 run。
- list numbering 容易延續錯誤。
- table conversion 需要獨立測試。
- style copying / preserving 要小心。
```

因此 rendering 不要與 generation logic 混在一起。

---

## 20. Backend API Design

### 20.1 Jobs API

```text
POST /api/jobs
- create job

POST /api/jobs/{job_id}/upload
- upload files

POST /api/jobs/{job_id}/analyze
- parse template and files
- return detected sections and file summary

POST /api/jobs/{job_id}/generate
- start generation

GET /api/jobs/{job_id}/status
- return progress

GET /api/jobs/{job_id}/events
- optional SSE progress stream

GET /api/jobs/{job_id}/artifacts/final-docx
- download final docx

GET /api/jobs/{job_id}/artifacts/debug-report
- download debug report

GET /api/jobs/{job_id}/artifacts/coverage-report
- download coverage report
```

### 20.2 JobStatus

```python
class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | analyzing | generating | completed | failed
    current_step: str
    progress: float
    message: str | None = None
    error: str | None = None
```

---

## 21. Frontend Design

Recommended:

```text
React + Vite + Tailwind
```

### 21.1 Pages

```text
1. UploadPage
2. AnalyzePreviewPage
3. ConfigurePage
4. GenerateProgressPage
5. DownloadPage
```

### 21.2 Components

```text
FileUploadPanel
- Source PDFs
- Reference files
- Template DOCX

TemplateSectionPreview
- 顯示偵測到的章節
- 顯示每個章節 level

ReferenceFileSummary
- 顯示 reference file type
- Excel 顯示欄位推測結果

GenerationProfileForm
- language
- tone
- verbosity
- include reference cases
- preserve vendor terminology
- user instruction

ProgressTimeline
- 顯示 pipeline step

DownloadPanel
- final docx
- debug report
- coverage report
```

---

## 22. Artifact Storage

單一 Docker image 內先用 local filesystem。

```text
/data/jobs/{job_id}/
  uploads/
    source/
    reference/
    template/

  intermediate/
    source_docs.json
    reference_docs.json
    template_structure.json
    section_plan.json
    source_mapping.json
    generated_sections.json

  outputs/
    final_sop.docx
    debug_report.json
    coverage_report.json
```

---

## 23. Debug Report

debug_report.json 應包含：

```text
- job_id
- input files
- parsed source document summary
- parsed reference document summary
- template sections
- generation profile
- per-section evidence packet
- per-section generated markdown
- warnings
- errors if any
```

---

## 24. Coverage Report

coverage_report.json 應包含：

```text
- total source chunks
- mapped source chunks
- unmapped source chunks
- section_to_source_chunks
- generated_section_to_used_source_chunks
- generated_section_to_used_reference_items
- warnings
```

目的：

```text
讓使用者或工程師知道：
- 哪些廠商文件內容被用到了
- 哪些內容沒有合適章節可放
- 每個章節的依據是什麼
```

---

## 25. Testing Plan

### 25.1 Unit Tests

```text
- Excel schema inference
- TXT chunking
- MD chunking
- DOCX heading detection
- Source chunk to section mapping
- PromptBuilder
- Markdown to DOCX renderer
- Coverage report builder
```

### 25.2 Integration Tests

```text
- one source PDF + one template DOCX
- multiple source PDFs + one template DOCX
- source PDFs + reference Excel
- source PDFs + reference PDF
- source PDFs + reference MD
- source PDFs + reference TXT
- template with heading styles
- template with Chinese numeric headings
- template with no clear headings
```

### 25.3 Golden Fixtures

準備：

```text
tests/fixtures/
  source_manual_1.pdf
  source_manual_2.pdf
  maintenance_records.xlsx
  reference_notes.md
  reference_notes.txt
  template.docx
```

驗證：

```text
- final_sop.docx exists
- all detected template sections processed
- generated_sections.json exists
- coverage_report.json exists
- unmapped chunks are reported
- generated sections record used_source_chunk_ids
```

---

## 26. MVP Scope

### 26.1 MVP Must Have

```text
1. FastAPI backend
2. React/Vite frontend or acceptable non-Gradio polished UI
3. Single Docker image
4. Multiple source PDFs
5. Reference files:
   - Excel
   - PDF
   - MD
   - TXT
6. One DOCX template
7. Heading-based template analyzer
8. Source chunk to section mapping
9. Separate source and reference handling
10. Section-aware reference retrieval
11. PromptBuilder with:
    - fixed core system policy
    - generation profile
    - editable user instruction
12. Section markdown generation
13. DOCX rendering
14. Job status
15. Download final DOCX
16. Debug report
17. Coverage report
```

### 26.2 MVP Non-goals

```text
- 完全自由格式 DOCX 理解
- 複雜巢狀表格自動填寫
- 多 agent orchestration
- 多使用者權限
- 真正分散式 queue
- 長期資料庫
- 自動修改模板 layout
- advanced citation UI
- perfect hallucination detection
```

---

## 27. Suggested Implementation Order

### Phase 1: Project Skeleton

```text
- 建立 repo structure
- FastAPI app
- frontend app
- Dockerfile
- health check endpoint
- job directory creation
```

### Phase 2: File Upload + Job API

```text
- create job
- upload source PDFs
- upload reference files
- upload template DOCX
- save files to /data/jobs/{job_id}
- status endpoint
```

### Phase 3: Ingestion

```text
- source PDF parser interface
- reference loader
- Excel loader
- PDF reference loader
- MD loader
- TXT loader
- DOCX template parser
```

### Phase 4: Template Planning

```text
- heading detection
- template section extraction
- section plan creation
- analysis preview endpoint
```

### Phase 5: Source Mapping

```text
- source chunking
- section embedding representation
- source chunk to section mapping
- unmapped chunk handling
- coverage report draft
```

### Phase 6: Reference Retrieval

```text
- reference item serialization
- reference embedding
- simple vector index
- section-aware retrieval
```

### Phase 7: Generation

```text
- PromptBuilder
- GenerationProfile
- user instruction handling
- section generation
- basic validation
- generated_sections.json
```

### Phase 8: DOCX Rendering

```text
- markdown to docx
- insert generated content under headings
- preserve styles as much as possible
- final_sop.docx output
```

### Phase 9: Frontend Polish

```text
- multi-step UI
- analysis preview
- generation config
- progress timeline
- download page
- error display
```

### Phase 10: Tests and Cleanup

```text
- unit tests
- integration tests
- fixture-based golden tests
- README
- example usage
```

---

## 28. Engineering Principles

### 28.1 Do Not Build a Script

不要把所有流程寫成一支巨大 script。

應做成 modular monolith：

```text
- modules are isolated
- deployment is single image
- pipeline is explicit
- artifacts are structured
```

### 28.2 Keep Source and Reference Separate

```text
Source PDFs are primary.
Reference documents are supplementary.
Do not mix them into one undifferentiated corpus.
```

### 28.3 Template-Constrained Generation

```text
The DOCX template decides where content can go.
If no suitable section exists, do not force content into the SOP.
```

### 28.4 Track Provenance

至少在 debug report / coverage report 中保留：

```text
- generated section
- used source chunk IDs
- used reference item IDs
- unmapped source chunks
```

### 28.5 Prefer Structured Artifacts

每個主要階段都應輸出 JSON artifact：

```text
- source_docs.json
- reference_docs.json
- template_structure.json
- section_plan.json
- source_mapping.json
- generated_sections.json
- coverage_report.json
- debug_report.json
```

---

## 29. Final Summary

新版 SOP Generation Agent 的核心不是單純「把文件丟給 LLM 生成 SOP」。

它應該是：

```text
Template-aware
Source-grounded
Reference-augmented
Section-constrained
Configurable
Traceable
```

最重要的行為準則：

```text
Source PDFs decide what should be covered.
DOCX template decides where content can appear.
Reference documents decide what can be added as supporting context.
```

如果 source PDF 的內容沒有合適的 template section：

```text
Do not force it into the SOP.
Report it as unmapped.
```

這是新版系統與舊版 pipeline 最大的差異之一。
