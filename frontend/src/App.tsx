import { FormEvent, MouseEvent, ReactNode, RefObject, useEffect, useMemo, useRef, useState } from "react";

type GateName = "template_review" | "evidence_review" | "draft_review";

type JobStatus = {
  job_id: string;
  status: string;
  current_step: string;
  progress: number;
  message?: string;
  error?: string;
  pending_gates: GateName[];
  review_settings?: {
    template_review_enabled: boolean;
    evidence_review_enabled: boolean;
    draft_review_enabled: boolean;
  };
};

type JobSummary = {
  job_id: string;
  status: string;
  current_step: string;
  progress: number;
  message?: string;
  updated_at: number;
  source_count: number;
  reference_count: number;
  template_file?: string;
};

type EvidenceRef = {
  evidence_id: string;
  file_name: string;
  evidence_type: string;
  location?: string;
  summary: string;
  excerpt: string;
  score: number;
  reason: string;
};

type EvidenceSection = {
  section_id: string;
  section_title: string;
  source_chunks: EvidenceRef[];
  reference_items: EvidenceRef[];
  warnings: string[];
};

type TemplateSection = {
  section_id: string;
  title: string;
  level: number;
  source_block_ids?: string[];
  confidence?: number;
  operation?: string;
  reason?: string;
  warnings?: string[];
};

type TemplateBlock = {
  block_id: string;
  text: string;
  style_name?: string;
  source_type: string;
  order_index: number;
  metadata?: Record<string, string>;
};

type TemplateSectionResolution = {
  file_name: string;
  sections: TemplateSection[];
  blocks?: TemplateBlock[];
  warnings: string[];
  refinement_suggestions?: { operation: string; title?: string; target_section_id?: string; reason: string }[];
  refinement_mode?: string;
  feedback_intent?: string;
  resolution_id?: string;
};

type DomainTermSuggestion = {
  term: string;
  category: string;
  confidence: number;
  reason: string;
  source_locations: string[];
  suggested_scope: string;
};

type EvidencePlan = {
  template: TemplateSectionResolution;
  sections: EvidenceSection[];
  warnings: string[];
  retrieval_metadata?: {
    retrieval_mode: string;
    chunk_method: string;
    sparse_backend?: string;
    tokenizer?: string;
    script_normalization?: string;
    sparse_fallback: boolean;
    temporary_domain_terms?: string[];
    domain_term_suggestions?: DomainTermSuggestion[];
    source_top_k: number;
    reference_top_k: number;
  };
};

type DraftBlock = {
  block_id: string;
  block_type?: string;
  text: string;
  content_md?: string;
  level?: number;
  items?: DraftListItem[];
  headers?: string[];
  rows?: string[][];
  callout_type?: string;
  source_chunk_ids: string[];
  reference_item_ids: string[];
  claims: string[];
  warnings: string[];
};

type DraftListItem = {
  content_md?: string;
  text?: string;
  source_chunk_ids?: string[];
  reference_item_ids?: string[];
  items?: DraftListItem[];
};

type DraftSection = {
  section_id: string;
  title: string;
  blocks: DraftBlock[];
  warnings: string[];
};

type GenerationResult = { sections: DraftSection[]; warnings: string[] };
type LogEntry = { timestamp: string; level: string; step: string; message: string; technical_detail?: string };
type CatalogModel = { id: string; name: string; provider: string; contextWindow?: number };
type StepState = "locked" | "ready" | "running" | "pending" | "done" | "auto";
type ToastAction = "draft_review" | "downloads";
type ToastMessage = { id: number; title: string; message: string; action?: ToastAction };
type EvidenceFilter = "all" | "warnings" | "no_source" | "reference_heavy" | "needs_feedback";

const API = "";

export default function App() {
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [evidencePlan, setEvidencePlan] = useState<EvidencePlan | null>(null);
  const [templateResolution, setTemplateResolution] = useState<TemplateSectionResolution | null>(null);
  const [draft, setDraft] = useState<GenerationResult | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobRetentionDays, setJobRetentionDays] = useState<number | null>(null);
  const [sourceFiles, setSourceFiles] = useState<FileList | null>(null);
  const [referenceFiles, setReferenceFiles] = useState<FileList | null>(null);
  const [templateFile, setTemplateFile] = useState<File | null>(null);
  const [uploadFormKey, setUploadFormKey] = useState(0);
  const [reviewSettings, setReviewSettings] = useState({
    template_review_enabled: true,
    evidence_review_enabled: true,
    draft_review_enabled: true
  });
  const [models, setModels] = useState<CatalogModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [generationLanguage, setGenerationLanguage] = useState("zh-TW");
  const [templateFeedback, setTemplateFeedback] = useState("");
  const [evidenceFeedback, setEvidenceFeedback] = useState("");
  const [sectionFeedback, setSectionFeedback] = useState<Record<string, string>>({});
  const [regenerationFeedback, setRegenerationFeedback] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [activeAction, setActiveAction] = useState("");
  const [error, setError] = useState("");
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const draftReviewRef = useRef<HTMLElement | null>(null);
  const downloadLinksRef = useRef<HTMLDivElement | null>(null);
  const lastStatusStepRef = useRef("");
  const autoGenerationStartedRef = useRef("");
  const toastIdRef = useRef(1);

  const evidenceById = useMemo(() => {
    const lookup: Record<string, EvidenceRef> = {};
    evidencePlan?.sections.forEach((section) => {
      section.source_chunks.concat(section.reference_items).forEach((item) => {
        lookup[item.evidence_id] = item;
      });
    });
    return lookup;
  }, [evidencePlan]);

  useEffect(() => {
    apiFetch("/api/models")
      .then((response) => (response.ok ? response.json() : { models: [] }))
      .then((payload) => {
        const nextModels = payload.models || [];
        setModels(nextModels);
        if (!selectedModelId && nextModels[0]) setSelectedModelId(nextModels[0].id);
      })
      .catch(() => setModels([]));
    loadJobs();
  }, []);

  useEffect(() => {
    if (!jobId) return;
    const shouldPoll = busy || ["analyzing", "generating", "pending", "uploaded"].includes(status?.status || "");
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      refresh(jobId);
      loadJobs();
    }, 1500);
    return () => window.clearInterval(timer);
  }, [busy, jobId, status?.status]);

  useEffect(() => {
    if (!status || status.current_step === lastStatusStepRef.current) return;
    lastStatusStepRef.current = status.current_step;
    if (status.current_step === "analysis_ready") {
      showToast("Evidence planning complete", "Review the planned evidence before generating the draft.");
    }
    if (status.current_step === "template_review_ready") {
      showToast("Template sections ready", "Review the section proposal, apply feedback if needed, then approve it.");
    }
    if (status.current_step === "draft_ready") {
      showToast("Draft generated", "Review the draft below, inspect provenance if needed, then approve the draft.", "draft_review");
    }
    if (status.current_step === "completed") {
      showToast("Draft approved", "The final DOCX and reports are ready to download.", "downloads");
    }
  }, [status?.current_step]);

  async function refresh(nextJobId = jobId) {
    if (!nextJobId) return;
    const [statusResponse, logsResponse] = await Promise.all([
      apiFetch(`/api/jobs/${nextJobId}/status`),
      apiFetch(`/api/jobs/${nextJobId}/logs`)
    ]);
    if (statusResponse.status === 404) {
      handleJobAccessLost();
      return;
    }
    let nextStatus: JobStatus | null = null;
    if (statusResponse.ok) {
      nextStatus = await statusResponse.json();
      setStatus(nextStatus);
    }
    if (logsResponse.ok) setLogs((await logsResponse.json()).logs);
    await loadArtifacts(nextJobId, nextStatus);
  }

  async function loadJobs() {
    const response = await apiFetch("/api/jobs");
    if (response.ok) {
      const payload = await response.json();
      setJobs(payload.jobs || []);
      setJobRetentionDays(typeof payload.retention_days === "number" ? payload.retention_days : null);
    }
  }

  async function loadArtifacts(nextJobId = jobId, nextStatus: JobStatus | null = status) {
    if (shouldLoadTemplateArtifact(nextStatus)) {
      const templateResponse = await apiFetch(`/api/jobs/${nextJobId}/artifacts/template_section_resolution`);
      setTemplateResolution(templateResponse.ok ? await templateResponse.json() : null);
    } else {
      setTemplateResolution(null);
    }

    if (shouldLoadEvidenceArtifact(nextStatus)) {
      const evidenceResponse = await apiFetch(`/api/jobs/${nextJobId}/artifacts/evidence_plan`);
      setEvidencePlan(evidenceResponse.ok ? await evidenceResponse.json() : null);
    } else {
      setEvidencePlan(null);
    }

    if (shouldLoadDraftArtifact(nextStatus)) {
      const draftResponse = await apiFetch(`/api/jobs/${nextJobId}/artifacts/generated_sections`);
      setDraft(draftResponse.ok ? await draftResponse.json() : null);
    } else {
      setDraft(null);
    }
  }

  async function resumeJob(nextJobId: string) {
    setJobId(nextJobId);
    setEvidencePlan(null);
    setTemplateResolution(null);
    setDraft(null);
    setError("");
    showToast("Job resumed", `Loaded job ${nextJobId.slice(0, 8)} and refreshed its progress.`);
    await refresh(nextJobId);
  }

  function startNewJob() {
    setJobId("");
    setStatus(null);
    setEvidencePlan(null);
    setTemplateResolution(null);
    setDraft(null);
    setLogs([]);
    setSourceFiles(null);
    setReferenceFiles(null);
    setTemplateFile(null);
    setTemplateFeedback("");
    setEvidenceFeedback("");
    setSectionFeedback({});
    setRegenerationFeedback({});
    setError("");
    setActiveAction("");
    lastStatusStepRef.current = "";
    setUploadFormKey((value) => value + 1);
    showToast("Ready for a new job", "The previous job is still saved in Job History. Upload new files to start another run.");
  }

  function handleJobAccessLost() {
    setJobId("");
    setStatus(null);
    setEvidencePlan(null);
    setTemplateResolution(null);
    setDraft(null);
    setLogs([]);
    setSourceFiles(null);
    setReferenceFiles(null);
    setTemplateFile(null);
    setError("");
    setActiveAction("");
    lastStatusStepRef.current = "";
    setUploadFormKey((value) => value + 1);
    showToast("Job history reset", "This browser no longer has access to that job. Upload files to start a new run.");
    loadJobs();
  }

  async function deleteJob(targetJobId: string, event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    const shouldDelete = window.confirm(`Delete job ${targetJobId.slice(0, 8)} and all of its artifacts?`);
    if (!shouldDelete) return;

    await run("Deleting job", async () => {
      const response = await apiFetch(`/api/jobs/${targetJobId}`, { method: "DELETE" });
      if (!response.ok) throw new ApiError(response.status, await response.text());
      if (targetJobId === jobId) {
        setJobId("");
        setStatus(null);
        setEvidencePlan(null);
        setTemplateResolution(null);
        setDraft(null);
        setLogs([]);
      }
      showToast("Job deleted", `Removed job ${targetJobId.slice(0, 8)} and its saved artifacts.`);
    });
  }

  async function createAndUpload(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setActiveAction("Uploading files and starting analysis");
    setError("");
    showToast("Upload started", "Creating the job and uploading source, reference, and template files.");
    try {
      const created = await postJson("/api/jobs", {
        review_settings: reviewSettings,
        generation_profile: { language: generationLanguage, tone: "professional", verbosity: "balanced" },
        model_config: { llm_model_id: selectedModelId || null }
      });
      const nextJobId = created.job_id;
      setJobId(nextJobId);
      setEvidencePlan(null);
      setTemplateResolution(null);
      setDraft(null);
      setTemplateFeedback("");
      setEvidenceFeedback("");
      setSectionFeedback({});
      setRegenerationFeedback({});
      setStatus(created.status);
      const form = new FormData();
      Array.from(sourceFiles || []).forEach((file) => form.append("source_files", file));
      Array.from(referenceFiles || []).forEach((file) => form.append("reference_files", file));
      if (templateFile) form.append("template_file", templateFile);
      const upload = await apiFetch(`/api/jobs/${nextJobId}/upload`, { method: "POST", body: form });
      if (!upload.ok) throw new ApiError(upload.status, await upload.text());
      showToast("Files uploaded", "Starting analysis automatically.");
      const nextStatus = await postJson(`/api/jobs/${nextJobId}/analyze?background=true`, {});
      setStatus(nextStatus);
      showToast("Analysis started", "The page will update when review materials are ready.");
      await refresh(nextJobId);
      await loadJobs();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        handleJobAccessLost();
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
      setActiveAction("");
    }
  }

  async function analyze() {
    await run("Analyzing uploaded files and resolving template sections", async () => {
      const nextStatus = await postJson(`/api/jobs/${jobId}/analyze?background=true`, {});
      setStatus(nextStatus);
      showToast("Analysis started", "The system is preparing the template section proposal for review.");
      await refresh();
    });
  }

  async function refineTemplateSections() {
    await run("Refining template sections", async () => {
      showToast("Refining sections", "The model is applying your feedback to the section proposal.");
      const result = await postJson(`/api/jobs/${jobId}/template/refine`, { feedback: templateFeedback });
      setTemplateResolution(result);
      setEvidencePlan(null);
      setDraft(null);
      await refresh();
      showToast("Sections updated", "Review the updated proposal, then approve the section plan.");
    });
  }

  async function replanEvidence() {
    await run("Re-planning evidence", async () => {
      showToast("Evidence re-plan started", "The system is applying your evidence feedback and rebuilding candidate mappings.");
      const result = await postJson(`/api/jobs/${jobId}/evidence/replan`, {
        global_feedback: evidenceFeedback,
        per_section_feedback: sectionFeedback
      });
      setEvidencePlan(result);
      setDraft(null);
      await refresh();
      showToast("Evidence updated", "Review the updated evidence plan before approval.");
    });
  }

  async function approve(gate: GateName) {
    await run(`Recording ${gate.replace("_", " ")} decision`, async () => {
      if (gate === "template_review") {
        showToast("Approving template", "Approving the current section proposal and planning evidence.");
      }
      if (gate === "evidence_review") showToast("Approving evidence", "Recording your evidence plan decision, then generating the draft.");
      if (gate === "draft_review") showToast("Approving draft", "Finalizing the job and preparing downloads.");
      await postJson(`/api/jobs/${jobId}/review/${gate}`, {
        global_feedback: gate === "evidence_review" ? evidenceFeedback : "",
        per_section_feedback: gate === "evidence_review" ? sectionFeedback : {}
      });
      await refresh();
      if (gate === "template_review") {
        showToast("Template approved", "Evidence has been planned from the approved section proposal.");
      } else if (gate === "evidence_review") {
        setActiveAction("Generating SOP draft");
        await generateDraft();
      } else {
        showToast("Draft approved", "The final DOCX and reports are ready to download.", "downloads");
      }
    });
  }

  async function generateDraft() {
    showToast("Draft generation started", "The system is generating SOP sections from the approved evidence.");
    const result = await postJson(`/api/jobs/${jobId}/generate`, {
      generation_profile: { language: generationLanguage, tone: "professional", verbosity: "balanced" },
      global_feedback: [templateFeedback, evidenceFeedback].filter(Boolean).join(" ")
    });
    setDraft(result);
    await refresh();
    showToast("Draft generated", "Review the draft below, then approve it when ready.", "draft_review");
    window.setTimeout(() => scrollToDraftReview(), 60);
  }

  async function generate() {
    await run("Generating SOP draft", async () => {
      await generateDraft();
    });
  }

  async function regenerate(sectionId: string) {
    await run("Regenerating section", async () => {
      showToast("Section regeneration started", `Regenerating ${sectionId} from your feedback.`);
      const result = await postJson(`/api/jobs/${jobId}/sections/${sectionId}/regenerate`, {
        feedback: regenerationFeedback[sectionId] || "",
        generation_profile: { language: generationLanguage, tone: "professional", verbosity: "balanced" }
      });
      setDraft(result);
      await refresh();
      showToast("Section regenerated", "Review the updated section and approve the draft when ready.", "draft_review");
    });
  }

  async function run(label: string, action: () => Promise<void>) {
    setBusy(true);
    setActiveAction(label);
    setError("");
    try {
      await action();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        handleJobAccessLost();
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
      setActiveAction("");
      await loadJobs();
    }
  }

  function showToast(title: string, message: string, action?: ToastAction) {
    const id = toastIdRef.current++;
    setToasts((items) => [...items.slice(-3), { id, title, message, action }]);
    window.setTimeout(() => {
      setToasts((items) => items.filter((item) => item.id !== id));
    }, action ? 10000 : 6500);
  }

  function dismissToast(id: number) {
    setToasts((items) => items.filter((item) => item.id !== id));
  }

  function scrollToDraftReview() {
    draftReviewRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function scrollToDownloads() {
    downloadLinksRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function handleToastAction(toast: ToastMessage) {
    const action = toast.action;
    if (!action) return;
    if (action === "draft_review") scrollToDraftReview();
    if (action === "downloads") scrollToDownloads();
    dismissToast(toast.id);
  }

  const pendingGates = status?.pending_gates || [];
  const analysisRunning = status?.status === "analyzing";
  const generationRunning = status?.status === "generating";
  const templateReviewPending = pendingGates.includes("template_review");
  const evidenceReviewPending = pendingGates.includes("evidence_review");
  const evidenceReviewUnlocked = Boolean(evidencePlan && !templateReviewPending);
  const preGenerationPending = pendingGates.includes("template_review") || evidenceReviewPending;
  const canAnalyze = Boolean(jobId) && !analysisRunning && !generationRunning;
  const canGenerate = Boolean(jobId && evidencePlan && !preGenerationPending && !generationRunning);
  const templateGateState = gateState("template_review", status, templateResolution, evidencePlan, draft);
  const evidenceGateState = templateReviewPending ? "locked" : gateState("evidence_review", status, evidencePlan, draft);
  const draftGateState = gateState("draft_review", status, evidencePlan, draft);
  const draftPanelState = draft ? draftGateState : generationRunning ? "running" : canGenerate ? "ready" : "locked";
  const currentTask = getCurrentTask(status, templateResolution, evidencePlan, draft);

  useEffect(() => {
    if (!canGenerate || busy || draft || status?.status === "failed" || status?.status === "completed") return;
    if (autoGenerationStartedRef.current === jobId) return;
    autoGenerationStartedRef.current = jobId;
    void generate();
  }, [busy, canGenerate, draft, jobId, status?.status]);

  return (
    <main className="app-shell">
      <JobSidebar
        jobs={jobs}
        jobId={jobId}
        jobRetentionDays={jobRetentionDays}
        status={status}
        logs={logs}
        resumeJob={resumeJob}
        deleteJob={deleteJob}
      />

      <div className="app-main">
        <header className="topbar">
          <div>
            <h1>SOP Generation Agent</h1>
            <p>Reviewable SOP drafts with section evidence, staged approvals, and clean DOCX output.</p>
          </div>
          <div className="topbar-actions">
            <div className="job-chip">{jobId ? `Job ${jobId.slice(0, 8)}` : "No active job"}</div>
            {jobId && (
              <button className="secondary-action" type="button" onClick={startNewJob}>
                Start new job
              </button>
            )}
          </div>
        </header>

        {error && <div className="error">{error}</div>}
        {toasts.length > 0 && (
          <div className="toast-stack" role="status" aria-live="polite">
            {toasts.map((toast) => (
              <div className="toast" key={toast.id}>
                <button className="toast-close" onClick={() => dismissToast(toast.id)} aria-label="Dismiss notification">x</button>
                <strong>{toast.title}</strong>
                <p>{toast.message}</p>
                {toast.action && (
                  <button className="toast-action" onClick={() => handleToastAction(toast)}>
                    {toast.action === "draft_review" ? "Go to Draft Review" : "View downloads"}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
        {(busy || status?.status === "analyzing" || status?.status === "generating") && (
          <div className="run-banner">
            <span className="spinner" />
            <div>
              <strong>{activeAction || status?.message || "Job is running"}</strong>
              <p>{getUserStatusLabel(status)} · keep this page open or resume the job later from Job history.</p>
            </div>
          </div>
        )}

        <div className="workspace">
          <section className="panel upload-panel">
          <h2>1. Upload</h2>
          <form key={uploadFormKey} onSubmit={createAndUpload} className="stack">
            <label>
              Source PDFs
              <input type="file" multiple accept=".pdf,.txt" onChange={(e) => setSourceFiles(e.target.files)} />
            </label>
            <label>
              Reference files
              <input type="file" multiple accept=".pdf,.txt,.md,.xlsx,.xls,.csv" onChange={(e) => setReferenceFiles(e.target.files)} />
            </label>
            <label>
              Template DOCX
              <input type="file" accept=".docx,.txt" onChange={(e) => setTemplateFile(e.target.files?.[0] || null)} />
            </label>
            <fieldset>
              <legend>Human review gates</legend>
              <Toggle label="Template review" checked={reviewSettings.template_review_enabled} onChange={(value) => setReviewSettings({ ...reviewSettings, template_review_enabled: value })} />
              <Toggle label="Evidence review" checked={reviewSettings.evidence_review_enabled} onChange={(value) => setReviewSettings({ ...reviewSettings, evidence_review_enabled: value })} />
              <Toggle label="Draft review" checked={reviewSettings.draft_review_enabled} onChange={(value) => setReviewSettings({ ...reviewSettings, draft_review_enabled: value })} />
            </fieldset>
            <fieldset>
              <legend>Model</legend>
              <label>
                Generation model
                <select value={selectedModelId} onChange={(event) => setSelectedModelId(event.target.value)}>
                  {models.map((model) => (
                    <option key={`${model.provider}:${model.id}`} value={model.id}>
                      {model.name} ({model.provider})
                    </option>
                  ))}
                </select>
              </label>
            </fieldset>
            <fieldset>
              <legend>Output language</legend>
              <label>
                SOP body language
                <select value={generationLanguage} onChange={(event) => setGenerationLanguage(event.target.value)}>
                  <option value="zh-TW">Traditional Chinese</option>
                  <option value="zh-CN">Simplified Chinese</option>
                  <option value="en">English</option>
                </select>
              </label>
            </fieldset>
            <button disabled={busy || !sourceFiles || !templateFile}>Create job, upload, and analyze</button>
          </form>
          </section>

          <section className="panel flow-panel">
          <div className="panel-heading">
            <div>
              <h2>2. Analyze and Review</h2>
              <p className="muted">Run analysis first, review what the system found, then approve the gates needed before draft generation.</p>
            </div>
          </div>

          <div className="current-task">
            <span className={`task-dot ${analysisRunning || generationRunning ? "running" : ""}`} />
            <div>
              <strong>{currentTask.title}</strong>
              <p>{currentTask.detail}</p>
            </div>
          </div>

          <div className="workflow-steps">
            <WorkflowStep
              number="1"
              title="Analyze uploaded files"
              state={analysisRunning ? "running" : templateResolution ? "done" : jobId ? "ready" : "locked"}
              description="Parse the PDF/template/reference files and create a reviewable section proposal."
            >
              <button disabled={busy || !canAnalyze} onClick={analyze}>
                {templateResolution ? "Run analysis again" : analysisRunning ? "Analysis running" : "Start analysis manually"}
              </button>
            </WorkflowStep>

            <WorkflowStep
              number="2"
              title="Review detected template sections"
              state={templateGateState}
              description="Confirm these are the sections that should be filled in the SOP template."
            >
              {templateResolution ? (
                <>
                  <TemplateResolutionSummary resolution={templateResolution} />
                  {evidenceReviewUnlocked ? (
                    <ApprovedTemplateSectionsSummary
                      sections={templateResolution.sections}
                      blocks={templateResolution.blocks || []}
                    />
                  ) : (
                    <>
                      <TemplateSectionList sections={templateResolution.sections} blocks={templateResolution.blocks || []} />
                      <TemplateSuggestionList suggestions={templateResolution.refinement_suggestions || []} />
                      <label className="wide-label">
                        Template section correction feedback
                        <textarea
                          value={templateFeedback}
                          onChange={(e) => setTemplateFeedback(e.target.value)}
                          placeholder="Example: split Fault Symptoms and Initial Diagnosis. If you want replacement, say only keep/fill these sections."
                        />
                      </label>
                      <div className="button-row">
                        <button disabled={busy || !templateFeedback.trim() || !pendingGates.includes("template_review")} onClick={refineTemplateSections}>
                          Apply feedback to sections
                        </button>
                        <button disabled={busy || !pendingGates.includes("template_review")} onClick={() => approve("template_review")}>
                          Approve current section plan
                        </button>
                      </div>
                    </>
                  )}
                </>
              ) : (
                <p className="empty">Run analysis to detect template sections.</p>
              )}
            </WorkflowStep>

            <WorkflowStep
              number="3"
              title="Review planned evidence"
              state={evidenceGateState}
              description="Check which source chunks and reference records will be used for each SOP section."
            >
              {!evidencePlan ? (
                <p className="empty">Evidence appears after analysis finishes.</p>
              ) : !evidenceReviewUnlocked ? (
                <p className="empty">
                  Approve the detected template sections first. If you add template feedback, the evidence plan will be rebuilt before this step unlocks.
                </p>
              ) : (
                <>
                  <EvidenceSummary sections={evidencePlan.sections} warnings={evidencePlan.warnings} />
                  <EvidencePlanWarnings warnings={evidencePlan.warnings} />
                  <DomainTermSuggestionsPanel metadata={evidencePlan.retrieval_metadata} />
                  <EvidenceReviewGuide />
                  <GlobalEvidencePolicy value={evidenceFeedback} onChange={setEvidenceFeedback} />
                  <EvidencePlanReviewList
                    sections={evidencePlan.sections}
                    metadata={evidencePlan.retrieval_metadata}
                    sectionFeedback={sectionFeedback}
                    setSectionFeedback={setSectionFeedback}
                  />
                  <div className="evidence-action-bar">
                    <EvidenceActionSummary globalFeedback={evidenceFeedback} sectionFeedback={sectionFeedback} />
                    <button disabled={busy || !evidenceReviewPending || (!evidenceFeedback.trim() && Object.values(sectionFeedback).every((value) => !value.trim()))} onClick={replanEvidence}>
                      {evidenceReviewPending ? "Apply evidence feedback and re-plan" : "Evidence plan approved"}
                    </button>
                    <button disabled={busy || !evidenceReviewPending} onClick={() => approve("evidence_review")}>
                      Approve current evidence plan
                    </button>
                  </div>
                </>
              )}
            </WorkflowStep>

          </div>

          {canGenerate && !draft && (
            <div className="draft-generation-fallback">
              <div>
                <strong>Draft generation is ready</strong>
                <p>Evidence is approved, but this job does not have a draft yet. This can happen after resuming an older job.</p>
              </div>
              <button disabled={busy || !canGenerate} onClick={generate}>Generate draft now</button>
            </div>
          )}

          </section>

          <section className={`panel draft-panel ${draftPanelState}`} ref={draftReviewRef}>
            <div className="panel-heading">
              <div>
                <h2>3. Draft Review</h2>
                <p className="muted">Review generated paragraphs, inspect provenance, regenerate sections, then approve the final draft.</p>
              </div>
              {draft && (
                <div className="panel-heading-actions">
                  <GateBadge state={draftGateState} label="Draft review" />
                  <button disabled={busy || !pendingGates.includes("draft_review")} onClick={() => approve("draft_review")}>Approve draft</button>
                </div>
              )}
            </div>
            {!draft && <p className="empty">Generate a draft to review paragraph-level provenance and regenerate sections.</p>}
            {draft && jobId && status?.status === "completed" && (
              <DownloadPanel
                jobId={jobId}
                downloadLinksRef={downloadLinksRef}
              />
            )}
            {draft?.sections.map((section) => (
              <article key={section.section_id} className="draft-section">
                <div className="draft-section-header">
                  <h3>{section.title}</h3>
                  <span>
                    {section.blocks.length} block{section.blocks.length === 1 ? "" : "s"}
                    {section.warnings.length ? ` · ${section.warnings.length} warning${section.warnings.length === 1 ? "" : "s"}` : ""}
                  </span>
                </div>
                {section.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
                {section.blocks.length === 0 && (
                  <p className="empty">No generated content for this section. Review the section warning or regenerate after adding guidance.</p>
                )}
                {section.blocks.map((block, index) => (
                  <DraftBlockView
                    key={block.block_id}
                    block={block}
                    index={index}
                    evidenceById={evidenceById}
                  />
                ))}
                <label className="wide-label draft-feedback-label">
                  Section regeneration feedback
                  <textarea value={regenerationFeedback[section.section_id] || ""} onChange={(e) => setRegenerationFeedback({ ...regenerationFeedback, [section.section_id]: e.target.value })} />
                </label>
                <button className="secondary-action draft-regenerate-action" disabled={busy} onClick={() => regenerate(section.section_id)}>Regenerate this section</button>
              </article>
            ))}
          </section>
        </div>
      </div>
    </main>
  );
}

function JobSidebar({
  jobs,
  jobId,
  jobRetentionDays,
  status,
  logs,
  resumeJob,
  deleteJob
}: {
  jobs: JobSummary[];
  jobId: string;
  jobRetentionDays: number | null;
  status: JobStatus | null;
  logs: LogEntry[];
  resumeJob: (jobId: string) => Promise<void>;
  deleteJob: (jobId: string, event: MouseEvent<HTMLButtonElement>) => Promise<void>;
}) {
  return (
    <aside className="panel status-panel" aria-label="Background jobs and progress">
      <div className="job-history-header">
        <div>
          <h2>Job History</h2>
          <p className="muted">
            {jobs.length} saved {jobs.length === 1 ? "job" : "jobs"}
            {jobRetentionDays && jobRetentionDays > 0 ? ` · auto-clean after ${formatRetention(jobRetentionDays)}` : ""}
          </p>
          <p className="muted sidebar-cookie-note">Saved for this browser. Clearing browser data starts a new history.</p>
        </div>
      </div>

      <div className="job-list">
        {jobs.length === 0 && <p className="empty">No jobs yet.</p>}
        {jobs.map((job) => (
          <div className={`job-row ${job.job_id === jobId ? "selected" : ""}`} key={job.job_id}>
            <button className="job-resume" onClick={() => resumeJob(job.job_id)}>
              <span className="job-id">{job.job_id.slice(0, 8)}</span>
              <small>
                <span className="job-progress-chip">{Math.round((job.progress || 0) * 100)}%</span>
                {getUserStatusLabel(job)}
              </small>
              <small>{formatJobUpdatedAt(job.updated_at)}</small>
            </button>
            <button className="job-delete" onClick={(event) => deleteJob(job.job_id, event)} aria-label={`Delete job ${job.job_id.slice(0, 8)}`}>
              Delete
            </button>
          </div>
        ))}
      </div>

      <div className="sidebar-section">
        <h2>Progress</h2>
        <Progress status={status} />
      </div>
      <LogPanel logs={logs} />
    </aside>
  );
}

function WorkflowStep({
  number,
  title,
  state,
  description,
  children
}: {
  number: string;
  title: string;
  state: StepState;
  description: string;
  children: ReactNode;
}) {
  return (
    <article className={`workflow-step ${state}`}>
      <div className="step-header">
        <span className="step-number">{number}</span>
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        <GateBadge state={state} />
      </div>
      <div className="step-body">{children}</div>
    </article>
  );
}

function GateBadge({ state, label }: { state: StepState; label?: string }) {
  const text = {
    locked: "Not ready",
    ready: "Ready",
    running: "Running",
    pending: "Needs review",
    done: "Approved",
    auto: "Auto-approved"
  }[state];
  return <span className={`gate-badge ${state}`}>{label ? `${label}: ${text}` : text}</span>;
}

function TemplateResolutionSummary({ resolution }: { resolution: TemplateSectionResolution }) {
  return (
    <div className="retrieval-metadata">
      Section proposal: {resolution.refinement_mode || "rules"} · feedback intent: {resolution.feedback_intent || "guidance"} · {resolution.sections.length} sections
      {resolution.warnings?.length ? ` · ${resolution.warnings.length} warnings` : ""}
    </div>
  );
}

function ApprovedTemplateSectionsSummary({
  sections,
  blocks
}: {
  sections: TemplateSection[];
  blocks: TemplateBlock[];
}) {
  return (
    <details className="approved-template-summary">
      <summary>
        <div>
          <strong>Template section plan approved</strong>
          <span>{sections.length} sections are now used for evidence planning. Expand to inspect the approved section list.</span>
        </div>
      </summary>
      <TemplateSectionList sections={sections} blocks={blocks} />
    </details>
  );
}

function TemplateSectionList({ sections, blocks }: { sections: TemplateSection[]; blocks: TemplateBlock[] }) {
  const blockById = Object.fromEntries(blocks.map((block) => [block.block_id, block]));
  return (
    <div className="template-section-list">
      {sections.map((section, index) => (
        <div key={section.section_id} className="template-section-row">
          <span>{index + 1}</span>
          <div className="template-section-main">
            <strong>{section.title}</strong>
            {section.reason && (
              <small className="template-section-meta">
                {section.operation || "keep"} · {(section.confidence ?? 1).toFixed(2)} · {section.reason}
              </small>
            )}
            {section.source_block_ids?.length ? (
              <details>
                <summary>Source template blocks</summary>
                {section.source_block_ids.map((blockId) => {
                  const block = blockById[blockId];
                  return block ? <pre key={blockId}>{block.source_type} · {block.style_name || "no style"} · {block.text}</pre> : <p key={blockId}>{blockId}</p>;
                })}
              </details>
            ) : null}
          </div>
          <small className="template-section-level">Level {section.level}</small>
        </div>
      ))}
    </div>
  );
}

function TemplateSuggestionList({ suggestions }: { suggestions: NonNullable<EvidencePlan["template"]["refinement_suggestions"]> }) {
  if (!suggestions.length) return null;
  return (
    <div className="template-suggestions">
      <strong>Section refinement suggestions</strong>
      {suggestions.map((suggestion, index) => (
        <p key={`${suggestion.operation}-${suggestion.target_section_id || index}`}>
          {suggestion.operation}: {suggestion.title || suggestion.target_section_id || "section"} - {suggestion.reason}
        </p>
      ))}
    </div>
  );
}

function EvidenceSummary({ sections, warnings }: { sections: EvidenceSection[]; warnings: string[] }) {
  const sourceCount = sections.reduce((total, section) => total + section.source_chunks.length, 0);
  const referenceCount = sections.reduce((total, section) => total + section.reference_items.length, 0);
  const sectionWarnings = sections.reduce((total, section) => total + section.warnings.length, 0);
  return (
    <div className="evidence-summary">
      <div><strong>{sections.length}</strong><span>sections</span></div>
      <div><strong>{sourceCount}</strong><span>source chunks</span></div>
      <div><strong>{referenceCount}</strong><span>reference records</span></div>
      <div><strong>{warnings.length + sectionWarnings}</strong><span>warnings</span></div>
    </div>
  );
}

function EvidencePlanWarnings({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <div className="plan-warnings">
      {warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
    </div>
  );
}

function EvidenceReviewGuide() {
  return (
    <div className="review-guide" aria-label="Evidence review checklist">
      <strong>Review focus</strong>
      <ul>
        <li>Each SOP section has enough source evidence to support the draft.</li>
        <li>Reference records are only used as supplementary field experience.</li>
        <li>Warnings or source/reference conflicts are acceptable before generation.</li>
        <li>All planned candidates are shown below with their location, reason, and excerpt.</li>
      </ul>
    </div>
  );
}

function DomainTermSuggestionsPanel({ metadata }: { metadata?: EvidencePlan["retrieval_metadata"] }) {
  const suggestions = metadata?.domain_term_suggestions || [];
  const temporaryTerms = metadata?.temporary_domain_terms || [];
  if (!suggestions.length && !temporaryTerms.length) return null;
  return (
    <details className="review-detail domain-term-panel">
      <summary>Domain terms detected</summary>
      {temporaryTerms.length > 0 && (
        <p className="muted">Temporary retrieval terms for this job: {temporaryTerms.slice(0, 40).join(", ")}</p>
      )}
      {suggestions.length > 0 && (
        <div className="domain-term-list">
          {suggestions.map((suggestion) => (
            <div className="domain-term-row" key={`${suggestion.term}-${suggestion.category}`}>
              <strong>{suggestion.term}</strong>
              <span>{suggestion.category} · {(suggestion.confidence * 100).toFixed(0)}% · {suggestion.suggested_scope}</span>
              {suggestion.reason && <p>{suggestion.reason}</p>}
            </div>
          ))}
        </div>
      )}
    </details>
  );
}

function GlobalEvidencePolicy({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <details className="global-evidence-policy" open={Boolean(value.trim())}>
      <summary>
        <div>
          <strong>Overall evidence policy</strong>
          <span>Optional global guidance applied to every SOP section.</span>
        </div>
        <em>{value.trim() ? "Has global note" : "No global note"}</em>
      </summary>
      <label className="wide-label compact-label">
        Global evidence and generation guidance
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Example: source PDF is authoritative; references can only fill missing repair experience; do not present field cases as vendor requirements."
        />
      </label>
    </details>
  );
}

function EvidenceActionSummary({
  globalFeedback,
  sectionFeedback
}: {
  globalFeedback: string;
  sectionFeedback: Record<string, string>;
}) {
  const sectionNotes = Object.values(sectionFeedback).filter((value) => value.trim()).length;
  return (
    <div className="evidence-action-summary">
      <span>
        Global note: <strong>{globalFeedback.trim() ? "yes" : "no"}</strong>
      </span>
      <span>
        Section notes: <strong>{sectionNotes}</strong>
      </span>
    </div>
  );
}

function EvidencePlanReviewList({
  sections,
  metadata,
  sectionFeedback,
  setSectionFeedback
}: {
  sections: EvidenceSection[];
  metadata?: EvidencePlan["retrieval_metadata"];
  sectionFeedback: Record<string, string>;
  setSectionFeedback: (value: Record<string, string>) => void;
}) {
  const [filter, setFilter] = useState<EvidenceFilter>("all");
  const [query, setQuery] = useState("");
  const filteredSections = useMemo(
    () => sections.filter((section) => evidenceSectionMatches(section, filter, query, sectionFeedback)),
    [sections, filter, query, sectionFeedback]
  );

  return (
    <div className="evidence-plan-list">
      {metadata && (
        <div className="retrieval-metadata">
          Retrieval: {metadata.retrieval_mode} · tokenizer: {metadata.tokenizer || "auto"} · normalization: {metadata.script_normalization || "dual"} · chunks: {metadata.chunk_method} · top-k: {metadata.source_top_k}/{metadata.reference_top_k}
        </div>
      )}
      <div className="evidence-review-toolbar">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search section, file, location, or evidence text"
        />
        <div className="evidence-filter-pills" aria-label="Evidence filters">
          {[
            ["all", "All"],
            ["warnings", "Warnings"],
            ["no_source", "No source"],
            ["reference_heavy", "Reference-heavy"],
            ["needs_feedback", "With notes"]
          ].map(([value, label]) => (
            <button
              className={filter === value ? "active" : ""}
              key={value}
              type="button"
              onClick={() => setFilter(value as EvidenceFilter)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {filteredSections.length === 0 && <p className="empty">No sections match the current evidence filter.</p>}
      {filteredSections.map((section, index) => (
        <EvidenceSectionReviewCard
          key={section.section_id}
          section={section}
          openByDefault={index === 0}
          feedback={sectionFeedback[section.section_id] || ""}
          setFeedback={(value) => setSectionFeedback({ ...sectionFeedback, [section.section_id]: value })}
        />
      ))}
    </div>
  );
}

function EvidenceSectionReviewCard({
  section,
  openByDefault,
  feedback,
  setFeedback
}: {
  section: EvidenceSection;
  openByDefault: boolean;
  feedback: string;
  setFeedback: (value: string) => void;
}) {
  const topSource = section.source_chunks[0];
  const topReference = section.reference_items[0];
  return (
    <details className="evidence-plan-row" open={openByDefault || section.warnings.length > 0 || Boolean(feedback.trim())}>
      <summary className="evidence-plan-summary">
        <div>
          <strong>{section.section_title}</strong>
          <small>
            {section.source_chunks.length} source · {section.reference_items.length} reference · {section.warnings.length} warning{section.warnings.length === 1 ? "" : "s"}
          </small>
        </div>
        <div className="evidence-topline">
          {topSource && <span>Source: {topSource.file_name}{topSource.location ? `, ${topSource.location}` : ""}</span>}
          {topReference && <span>Reference: {topReference.file_name}{topReference.location ? `, ${topReference.location}` : ""}</span>}
          {!topSource && !topReference && <span>No planned candidates.</span>}
        </div>
      </summary>
      <div className="evidence-plan-content">
        {section.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
        <QuickEvidenceFeedback feedback={feedback} setFeedback={setFeedback} />
        <label className="wide-label compact-label">
          Section-specific note
          <textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="Optional: mention missing source, wrong mapping, source/reference conflict, or constraints for this section."
          />
        </label>
        <div className="evidence-candidate-grid">
          <EvidenceCandidateColumn title="Source candidates" items={section.source_chunks} />
          <EvidenceCandidateColumn title="Reference candidates" items={section.reference_items} />
        </div>
      </div>
    </details>
  );
}

function QuickEvidenceFeedback({ feedback, setFeedback }: { feedback: string; setFeedback: (value: string) => void }) {
  const appendFeedback = (value: string) => {
    setFeedback(feedback.trim() ? `${feedback.trim()}\n${value}` : value);
  };
  return (
    <div className="quick-feedback">
      {[
        "Missing source evidence.",
        "Reference conflicts with source; keep source authoritative.",
        "Reference is useful as supplemental field experience.",
        "Candidate mapping looks unrelated."
      ].map((item) => (
        <button key={item} type="button" onClick={() => appendFeedback(item)}>
          {item}
        </button>
      ))}
    </div>
  );
}

function EvidenceCandidateColumn({ title, items }: { title: string; items: EvidenceRef[] }) {
  return (
    <div className="evidence-candidate-column">
      <h4>{title}</h4>
      {items.length === 0 && <p className="muted">No candidates planned.</p>}
      {items.map((item, index) => (
        <EvidenceCandidateCard key={item.evidence_id} item={item} rank={index + 1} />
      ))}
    </div>
  );
}

function EvidenceCandidateCard({ item, rank }: { item: EvidenceRef; rank: number }) {
  const excerpt = item.excerpt || item.summary || "No preview text available.";
  const summaryIsDistinct = item.summary && !isSimilarText(item.summary, excerpt);
  return (
    <article className="evidence-candidate-card">
      <div className="candidate-meta">
        <span>#{rank}</span>
        <div>
          <strong>{item.file_name}</strong>
          <small>{item.location || "location unknown"} · score {item.score.toFixed(2)}</small>
        </div>
      </div>
      <pre className="candidate-excerpt">{excerpt}</pre>
      <details className="candidate-technical">
        <summary>Why selected</summary>
        <p>{item.reason}</p>
        {summaryIsDistinct && <p>Summary: {item.summary}</p>}
      </details>
    </article>
  );
}

function evidenceSectionMatches(
  section: EvidenceSection,
  filter: EvidenceFilter,
  query: string,
  sectionFeedback: Record<string, string>
) {
  if (filter === "warnings" && section.warnings.length === 0) return false;
  if (filter === "no_source" && section.source_chunks.length > 0) return false;
  if (filter === "reference_heavy" && section.reference_items.length <= section.source_chunks.length) return false;
  if (filter === "needs_feedback" && !sectionFeedback[section.section_id]?.trim()) return false;
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  const searchable = [
    section.section_title,
    ...section.warnings,
    ...section.source_chunks.flatMap(evidenceSearchFields),
    ...section.reference_items.flatMap(evidenceSearchFields),
    sectionFeedback[section.section_id] || ""
  ].join("\n").toLowerCase();
  return searchable.includes(normalized);
}

function evidenceSearchFields(item: EvidenceRef) {
  return [item.file_name, item.location || "", item.summary, item.excerpt, item.reason];
}

function isSimilarText(left: string, right: string) {
  const normalizedLeft = normalizePreviewText(left);
  const normalizedRight = normalizePreviewText(right);
  if (!normalizedLeft || !normalizedRight) return false;
  return normalizedRight.includes(normalizedLeft.slice(0, 80)) || normalizedLeft.includes(normalizedRight.slice(0, 80));
}

function normalizePreviewText(value: string) {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function DownloadPanel({
  jobId,
  downloadLinksRef
}: {
  jobId: string;
  downloadLinksRef: RefObject<HTMLDivElement>;
}) {
  return (
    <div className="output-panel" ref={downloadLinksRef}>
      <div className="output-summary">
        <div className="output-status">
          <span>Approved</span>
        </div>
        <div className="output-copy">
          <h3>Final SOP is ready</h3>
          <p className="muted">
            Download the approved SOP document. Supporting reports are available below for audit and review.
          </p>
        </div>
        <a className="primary-download" href={`/api/jobs/${jobId}/download/final_sop.docx`}>
          <span className="download-filetype">DOCX</span>
          <span className="download-copy">
            <strong>Download DOCX</strong>
            <span>Approved SOP document</span>
          </span>
        </a>
      </div>
      <details className="report-downloads">
        <summary>Reviewer reports and technical details</summary>
        <div className="report-links">
          <a href={`/api/jobs/${jobId}/download/coverage_report`}>
            <strong>Evidence coverage</strong>
            <span>See which source/reference content was used or left unmapped.</span>
          </a>
          <a href={`/api/jobs/${jobId}/download/provenance_report`}>
            <strong>Paragraph sources</strong>
            <span>Trace generated paragraphs back to source and reference evidence.</span>
          </a>
          <a href={`/api/jobs/${jobId}/download/debug_report`}>
            <strong>Technical debug report</strong>
            <span>Inspect inputs, settings, warnings, artifacts, and logs.</span>
          </a>
        </div>
      </details>
    </div>
  );
}

function gateState(
  gate: GateName,
  status: JobStatus | null,
  templateResolutionOrEvidencePlan: TemplateSectionResolution | EvidencePlan | null,
  evidencePlanOrDraft?: EvidencePlan | GenerationResult | null,
  draftArg?: GenerationResult | null
): StepState {
  if (status?.pending_gates?.includes(gate)) return "pending";
  const evidencePlan = draftArg === undefined
    ? templateResolutionOrEvidencePlan as EvidencePlan | null
    : evidencePlanOrDraft as EvidencePlan | null;
  const draft = draftArg === undefined ? evidencePlanOrDraft as GenerationResult | null : draftArg;
  if (gate === "draft_review") {
    if (!draft) return "locked";
    if (status?.status === "generating") return "running";
    if (status?.status === "failed") return "locked";
    return status?.review_settings?.draft_review_enabled === false ? "auto" : "done";
  }
  if (gate === "template_review") {
    if (!templateResolutionOrEvidencePlan) return "locked";
    return status?.review_settings?.template_review_enabled === false ? "auto" : "done";
  }
  if (!evidencePlan) return "locked";
  const enabled = status?.review_settings?.evidence_review_enabled;
  return enabled === false ? "auto" : "done";
}

function shouldLoadTemplateArtifact(status: JobStatus | null) {
  if (!status) return false;
  if (["generating", "completed"].includes(status.status)) return true;
  return ["template_review_ready", "analysis_ready", "review_updated", "draft_ready", "completed"].includes(status.current_step);
}

function shouldLoadEvidenceArtifact(status: JobStatus | null) {
  if (!status) return false;
  if (["generating", "completed"].includes(status.status)) return true;
  return ["analysis_ready", "review_updated", "draft_ready"].includes(status.current_step);
}

function shouldLoadDraftArtifact(status: JobStatus | null) {
  if (!status) return false;
  return status.status === "completed" || status.current_step === "draft_ready";
}

function getCurrentTask(
  status: JobStatus | null,
  templateResolution: TemplateSectionResolution | null,
  evidencePlan: EvidencePlan | null,
  draft: GenerationResult | null
) {
  if (!status) return { title: "Create a job first", detail: "Upload at least one source PDF and one DOCX template, then create a job." };
  if (status.status === "analyzing") return { title: "Analysis is running", detail: status.message || "The system is parsing files and planning evidence." };
  if (status.status === "generating") return { title: "Draft generation is running", detail: status.message || "The system is generating SOP sections." };
  if (!templateResolution) return { title: "Next: run analysis", detail: "This creates the template section proposal for review." };
  if (status.pending_gates?.includes("template_review")) return { title: "Next: approve template sections", detail: "Confirm the detected DOCX sections before generation uses them." };
  if (!evidencePlan) return { title: "Next: plan evidence", detail: "Approving template sections starts evidence planning." };
  if (status.pending_gates?.includes("evidence_review")) return { title: "Next: approve evidence plan", detail: "Check source/reference evidence and add feedback if needed." };
  if (!draft) return { title: "Draft generation is ready", detail: "Evidence is approved. Normal runs start generation automatically; use the fallback button if this is a resumed job." };
  if (status.pending_gates?.includes("draft_review")) return { title: "Next: review draft", detail: "Inspect paragraph provenance, regenerate sections if needed, then approve the draft." };
  return { title: "Job is complete", detail: "Final DOCX and reports are available for download." };
}

function getUserStatusLabel(
  status: { status?: string; current_step?: string; pending_gates?: GateName[] } | null | undefined
) {
  if (!status?.status) return "Idle";
  if (status.status === "failed") return "Failed";
  if (status.status === "analyzing") return "Analyzing files";
  if (status.status === "generating") return "Generating draft";
  if (status.status === "completed") return "Completed";

  const pendingGates = status.pending_gates || [];
  if (status.current_step === "draft_ready") {
    return status.status === "needs_review" || pendingGates.includes("draft_review") ? "Draft review needed" : "Draft ready";
  }
  if (pendingGates.includes("template_review")) return "Template review needed";
  if (pendingGates.includes("evidence_review")) return "Evidence review needed";
  if (pendingGates.includes("draft_review")) return "Draft review needed";
  if (status.current_step === "analysis_ready") return "Analysis ready";
  if (status.current_step === "queued_analysis") return "Analysis queued";
  if (status.status === "uploaded") return "Ready to analyze";
  if (status.status === "pending" || status.current_step === "created") return "Created";
  return humanizeStatus(status.status);
}

function getPendingReviewLabel(gates: GateName[]) {
  if (gates.includes("template_review")) return "Waiting for template section approval.";
  if (gates.includes("evidence_review")) return "Waiting for evidence plan approval.";
  if (gates.includes("draft_review")) return "Waiting for draft approval.";
  return "Waiting for review approval.";
}

function humanizeStatus(value: string) {
  return value
    .replace(/_/g, " ")
    .replace(/^\w/, (character) => character.toUpperCase());
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function Progress({ status }: { status: JobStatus | null }) {
  const percent = Math.round((status?.progress || 0) * 100);
  return (
    <div>
      <div className="progress-track"><div style={{ width: `${percent}%` }} /></div>
      <p><strong>{getUserStatusLabel(status)}</strong> · {percent}%</p>
      {status?.message && <p>{status.message}</p>}
      {status?.pending_gates?.length ? <p className="warning">{getPendingReviewLabel(status.pending_gates)}</p> : null}
    </div>
  );
}

function LogPanel({ logs }: { logs: LogEntry[] }) {
  const latest = logs[logs.length - 1];
  return (
    <div className="log-panel">
      <h3>Logs</h3>
      {latest ? (
        <div className="latest-log">
          <span>Latest</span>
          <strong>{latest.step}</strong>
          <p>{latest.message}</p>
        </div>
      ) : (
        <p className="empty">No logs yet.</p>
      )}
      {logs.length > 0 && (
        <details className="technical-logs">
          <summary>Technical logs ({logs.length})</summary>
          <div className="log-list">
            {logs.map((entry, index) => (
              <details key={`${entry.timestamp}-${index}`}>
                <summary>{entry.step}: {entry.message}</summary>
                <pre>{entry.technical_detail || "No technical detail."}</pre>
              </details>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function DraftBlockView({
  block,
  index,
  evidenceById
}: {
  block: DraftBlock;
  index: number;
  evidenceById: Record<string, EvidenceRef>;
}) {
  const sourceIds = collectDraftSourceIds(block);
  const referenceIds = collectDraftReferenceIds(block);
  const evidenceIds = [...sourceIds, ...referenceIds];
  return (
    <article className={`draft-block ${block.block_type || "paragraph"}`}>
      <div className="draft-block-header">
        <strong>{draftBlockLabel(block, index)}</strong>
        <span>
          {sourceIds.length} source · {referenceIds.length} reference
        </span>
      </div>
      <DraftBlockContent block={block} />
      {block.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
      <details className="draft-evidence">
        <summary>Block evidence</summary>
        <EvidenceLinks ids={evidenceIds} evidenceById={evidenceById} />
      </details>
    </article>
  );
}

function DraftBlockContent({ block }: { block: DraftBlock }) {
  const content = block.content_md || block.text || "";
  if (block.block_type === "heading") {
    return <h4 className="draft-rich-heading">{renderInlineMarkdown(content)}</h4>;
  }
  if (block.block_type === "bullet_list" || block.block_type === "bullet") {
    return <DraftList items={block.items || []} ordered={false} fallback={content} />;
  }
  if (block.block_type === "numbered_list" || block.block_type === "numbered") {
    return <DraftList items={block.items || []} ordered fallback={content} />;
  }
  if (block.block_type === "table") {
    return <DraftTable headers={block.headers || []} rows={block.rows || []} />;
  }
  if (block.block_type === "callout") {
    return <div className="draft-callout"><strong>{block.callout_type || "note"}</strong>{renderInlineMarkdown(content)}</div>;
  }
  return <div className="draft-paragraph">{renderInlineMarkdown(content)}</div>;
}

function DraftList({ items, ordered, fallback }: { items: DraftListItem[]; ordered: boolean; fallback: string }) {
  if (!items.length && fallback) return <div className="draft-paragraph">{renderInlineMarkdown(fallback)}</div>;
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className="draft-rich-list">
      {items.map((item, index) => (
        <li key={`${index}-${item.content_md || item.text}`}>
          {renderInlineMarkdown(item.content_md || item.text || "")}
          {item.items?.length ? <DraftList items={item.items} ordered={ordered} fallback="" /> : null}
        </li>
      ))}
    </Tag>
  );
}

function DraftTable({ headers, rows }: { headers: string[]; rows: string[][] }) {
  return (
    <div className="draft-table-wrap">
      <table className="draft-table">
        {headers.length > 0 && (
          <thead>
            <tr>{headers.map((header, index) => <th key={`${index}-${header}`}>{renderInlineMarkdown(header)}</th>)}</tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => <td key={`${rowIndex}-${cellIndex}`}>{renderInlineMarkdown(cell)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderInlineMarkdown(value: string): ReactNode[] {
  const parts = value.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g).filter(Boolean);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index}>{part.slice(1, -1)}</code>;
    if (part.startsWith("*") && part.endsWith("*")) return <em key={index}>{part.slice(1, -1)}</em>;
    return <span key={index}>{part}</span>;
  });
}

function draftBlockLabel(block: DraftBlock, index: number) {
  if (block.block_type === "heading") return `Heading ${index + 1}`;
  if (block.block_type === "bullet_list" || block.block_type === "bullet") return `Bullet list ${index + 1}`;
  if (block.block_type === "numbered_list" || block.block_type === "numbered") return `Numbered list ${index + 1}`;
  if (block.block_type === "table") return `Table ${index + 1}`;
  if (block.block_type === "callout") return `Callout ${index + 1}`;
  return `Paragraph ${index + 1}`;
}

function collectDraftSourceIds(block: DraftBlock) {
  return uniqueIds([...(block.source_chunk_ids || []), ...collectListIds(block.items || [], "source")]);
}

function collectDraftReferenceIds(block: DraftBlock) {
  return uniqueIds([...(block.reference_item_ids || []), ...collectListIds(block.items || [], "reference")]);
}

function collectListIds(items: DraftListItem[], type: "source" | "reference"): string[] {
  return items.flatMap((item) => [
    ...((type === "source" ? item.source_chunk_ids : item.reference_item_ids) || []),
    ...collectListIds(item.items || [], type)
  ]);
}

function uniqueIds(ids: string[]) {
  return Array.from(new Set(ids.filter(Boolean)));
}

function EvidenceLinks({ ids, evidenceById }: { ids: string[]; evidenceById: Record<string, EvidenceRef> }) {
  if (!ids.length) return <p className="muted">No evidence linked to this paragraph.</p>;
  return (
    <div className="linked-evidence">
      {ids.map((id) => {
        const item = evidenceById[id];
        return item ? (
          <details key={id}>
            <summary>{item.evidence_type}: {item.file_name} {item.location ? `· ${item.location}` : ""}</summary>
            <pre>{item.excerpt}</pre>
          </details>
        ) : <p key={id}>{id}</p>;
      })}
    </div>
  );
}

function formatJobUpdatedAt(updatedAt: number) {
  if (!updatedAt) return "Updated time unknown";
  return new Date(updatedAt * 1000).toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false
  });
}

function formatRetention(days: number) {
  if (days < 1) return `${Math.round(days * 24)} hours`;
  if (days === 1) return "1 day";
  return `${days} days`;
}

async function postJson(path: string, body: unknown) {
  const response = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  return response.json();
}

function apiFetch(path: string, init: RequestInit = {}) {
  return fetch(`${API}${path}`, {
    ...init,
    credentials: init.credentials || "same-origin"
  });
}

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}
