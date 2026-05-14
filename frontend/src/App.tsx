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

type DomainTermSuggestion = {
  term: string;
  category: string;
  confidence: number;
  reason: string;
  source_locations: string[];
  suggested_scope: string;
};

type EvidencePlan = {
  template: {
    file_name: string;
    sections: { section_id: string; title: string; level: number }[];
    refinement_suggestions?: { operation: string; title?: string; target_section_id?: string; reason: string }[];
  };
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
  text: string;
  source_chunk_ids: string[];
  reference_item_ids: string[];
  claims: string[];
  warnings: string[];
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

const API = "";

export default function App() {
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [evidencePlan, setEvidencePlan] = useState<EvidencePlan | null>(null);
  const [draft, setDraft] = useState<GenerationResult | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobRetentionDays, setJobRetentionDays] = useState<number | null>(null);
  const [sourceFiles, setSourceFiles] = useState<FileList | null>(null);
  const [referenceFiles, setReferenceFiles] = useState<FileList | null>(null);
  const [templateFile, setTemplateFile] = useState<File | null>(null);
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
    fetch(`${API}/api/models`)
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
      showToast("Analysis complete", "Review the detected template sections and planned evidence before generating the draft.");
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
      fetch(`${API}/api/jobs/${nextJobId}/status`),
      fetch(`${API}/api/jobs/${nextJobId}/logs`)
    ]);
    if (statusResponse.ok) setStatus(await statusResponse.json());
    if (logsResponse.ok) setLogs((await logsResponse.json()).logs);
    await loadArtifacts(nextJobId);
  }

  async function loadJobs() {
    const response = await fetch(`${API}/api/jobs`);
    if (response.ok) {
      const payload = await response.json();
      setJobs(payload.jobs || []);
      setJobRetentionDays(typeof payload.retention_days === "number" ? payload.retention_days : null);
    }
  }

  async function loadArtifacts(nextJobId = jobId) {
    const evidenceResponse = await fetch(`${API}/api/jobs/${nextJobId}/artifacts/evidence_plan`);
    setEvidencePlan(evidenceResponse.ok ? await evidenceResponse.json() : null);
    const draftResponse = await fetch(`${API}/api/jobs/${nextJobId}/artifacts/generated_sections`);
    setDraft(draftResponse.ok ? await draftResponse.json() : null);
  }

  async function resumeJob(nextJobId: string) {
    setJobId(nextJobId);
    setEvidencePlan(null);
    setDraft(null);
    setError("");
    showToast("Job resumed", `Loaded job ${nextJobId.slice(0, 8)} and refreshed its progress.`);
    await refresh(nextJobId);
  }

  async function deleteJob(targetJobId: string, event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    const shouldDelete = window.confirm(`Delete job ${targetJobId.slice(0, 8)} and all of its artifacts?`);
    if (!shouldDelete) return;

    await run("Deleting job", async () => {
      const response = await fetch(`${API}/api/jobs/${targetJobId}`, { method: "DELETE" });
      if (!response.ok) throw new Error(await response.text());
      if (targetJobId === jobId) {
        setJobId("");
        setStatus(null);
        setEvidencePlan(null);
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
      const upload = await fetch(`${API}/api/jobs/${nextJobId}/upload`, { method: "POST", body: form });
      if (!upload.ok) throw new Error(await upload.text());
      showToast("Files uploaded", "Starting analysis automatically.");
      const nextStatus = await postJson(`/api/jobs/${nextJobId}/analyze?background=true`, {});
      setStatus(nextStatus);
      showToast("Analysis started", "The page will update when review materials are ready.");
      await refresh(nextJobId);
      await loadJobs();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
      setActiveAction("");
    }
  }

  async function analyze() {
    await run("Analyzing uploaded files and planning evidence", async () => {
      const nextStatus = await postJson(`/api/jobs/${jobId}/analyze?background=true`, {});
      setStatus(nextStatus);
      showToast("Analysis started", "The system is preparing template sections and evidence for review.");
      await refresh();
    });
  }

  async function approve(gate: GateName) {
    await run(`Recording ${gate.replace("_", " ")} decision`, async () => {
      if (gate === "template_review") showToast("Approving template", "Recording your template section decision.");
      if (gate === "evidence_review") showToast("Approving evidence", "Recording your evidence plan decision.");
      if (gate === "draft_review") showToast("Approving draft", "Finalizing the job and preparing downloads.");
      await postJson(`/api/jobs/${jobId}/review/${gate}`, {
        global_feedback: gate === "template_review" ? templateFeedback : evidenceFeedback,
        per_section_feedback: gate === "evidence_review" ? sectionFeedback : {}
      });
      await refresh();
      if (gate === "template_review") {
        showToast("Template approved", "Next, review and approve the planned evidence.");
      } else if (gate === "evidence_review") {
        showToast("Evidence approved", "You can now generate the SOP draft.");
      } else {
        showToast("Draft approved", "The final DOCX and reports are ready to download.", "downloads");
      }
    });
  }

  async function generate() {
    await run("Generating SOP draft", async () => {
      showToast("Draft generation started", "The system is generating SOP sections from the approved evidence.");
      const result = await postJson(`/api/jobs/${jobId}/generate`, {
        generation_profile: { language: generationLanguage, tone: "professional", verbosity: "balanced" },
        global_feedback: [templateFeedback, evidenceFeedback].filter(Boolean).join(" ")
      });
      setDraft(result);
      await refresh();
      showToast("Draft generated", "Review the draft below, then approve it when ready.", "draft_review");
      window.setTimeout(() => scrollToDraftReview(), 60);
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
      setError(String(err));
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
  const preGenerationPending = pendingGates.includes("template_review") || pendingGates.includes("evidence_review");
  const evidenceApprovalBlockedByTemplate = pendingGates.includes("template_review") && pendingGates.includes("evidence_review");
  const canAnalyze = Boolean(jobId) && !analysisRunning && !generationRunning;
  const canGenerate = Boolean(jobId && evidencePlan && !preGenerationPending && !generationRunning);
  const templateGateState = gateState("template_review", status, evidencePlan, draft);
  const evidenceGateState = gateState("evidence_review", status, evidencePlan, draft);
  const draftGateState = gateState("draft_review", status, evidencePlan, draft);
  const draftPanelState = draft ? draftGateState : generationRunning ? "running" : canGenerate ? "ready" : "locked";
  const currentTask = getCurrentTask(status, evidencePlan, draft);

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
          <div className="job-chip">{jobId ? `Job ${jobId.slice(0, 8)}` : "No active job"}</div>
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
          <form onSubmit={createAndUpload} className="stack">
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
              state={analysisRunning ? "running" : evidencePlan ? "done" : jobId ? "ready" : "locked"}
              description="Parse the PDF/template/reference files and create a section-level evidence plan."
            >
              <button disabled={busy || !canAnalyze} onClick={analyze}>
                {evidencePlan ? "Run analysis again" : analysisRunning ? "Analysis running" : "Start analysis manually"}
              </button>
            </WorkflowStep>

            <WorkflowStep
              number="2"
              title="Review detected template sections"
              state={templateGateState}
              description="Confirm these are the sections that should be filled in the SOP template."
            >
              {evidencePlan ? (
                <>
                  <TemplateSectionList sections={evidencePlan.template.sections} />
                  <TemplateSuggestionList suggestions={evidencePlan.template.refinement_suggestions || []} />
                  <label className="wide-label">
                    Template section correction feedback
                    <textarea
                      value={templateFeedback}
                      onChange={(e) => setTemplateFeedback(e.target.value)}
                      placeholder="Example: split Fault Symptoms and Initial Diagnosis, remove fixed template sections, or rename a section."
                    />
                  </label>
                  <button disabled={busy || !pendingGates.includes("template_review")} onClick={() => approve("template_review")}>
                    Approve template sections
                  </button>
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
              {evidencePlan ? (
                <>
                  <EvidenceSummary sections={evidencePlan.sections} warnings={evidencePlan.warnings} />
                  <EvidencePlanWarnings warnings={evidencePlan.warnings} />
                  <DomainTermSuggestionsPanel metadata={evidencePlan.retrieval_metadata} />
                  <EvidenceReviewGuide />
                  <EvidencePlanReviewList sections={evidencePlan.sections} metadata={evidencePlan.retrieval_metadata} />
                  <label className="wide-label">
                    Evidence and generation feedback
                    <textarea
                      value={evidenceFeedback}
                      onChange={(e) => setEvidenceFeedback(e.target.value)}
                      placeholder="Example: source PDF is authoritative; references can only fill missing repair experience; do not present field cases as vendor requirements."
                    />
                  </label>
                  <button disabled={busy || !pendingGates.includes("evidence_review") || evidenceApprovalBlockedByTemplate} onClick={() => approve("evidence_review")}>
                    {evidenceApprovalBlockedByTemplate ? "Approve template sections first" : "Approve evidence plan"}
                  </button>
                </>
              ) : (
                <p className="empty">Evidence appears after analysis finishes.</p>
              )}
            </WorkflowStep>

            <WorkflowStep
              number="4"
              title="Generate draft"
              state={generationRunning ? "running" : draft ? "done" : canGenerate ? "ready" : "locked"}
              description={preGenerationPending ? "Template and evidence review must be approved before draft generation." : "Create the first SOP draft from the reviewed evidence plan."}
            >
              <button disabled={busy || !canGenerate} onClick={generate}>Generate SOP draft</button>
            </WorkflowStep>
          </div>

          {evidencePlan && (
            <details className="review-detail">
              <summary>Evidence details by section</summary>
              <div className="section-list">
                {evidencePlan.sections.map((section) => (
                  <EvidenceSectionView
                    key={section.section_id}
                    section={section}
                    feedback={sectionFeedback[section.section_id] || ""}
                    setFeedback={(value) => setSectionFeedback({ ...sectionFeedback, [section.section_id]: value })}
                  />
                ))}
              </div>
            </details>
          )}
          </section>
        </div>

        <section className={`panel draft-panel ${draftPanelState}`} ref={draftReviewRef}>
          <div className="panel-heading">
            <div>
              <h2>3. Draft Review</h2>
              <p className="muted">Review generated paragraphs, inspect provenance, regenerate sections, then approve the final draft.</p>
            </div>
          </div>
          {!draft && <p className="empty">Generate a draft to review paragraph-level provenance and regenerate sections.</p>}
          {draft && (
            <div className="draft-approval">
              <GateBadge state={draftGateState} label="Draft review" />
              <button disabled={busy || !pendingGates.includes("draft_review")} onClick={() => approve("draft_review")}>Approve draft</button>
            </div>
          )}
          {draft && jobId && (
            <DownloadPanel
              jobId={jobId}
              isCompleted={status?.status === "completed"}
              downloadLinksRef={downloadLinksRef}
            />
          )}
          {draft?.sections.map((section) => (
            <article key={section.section_id} className="draft-section">
              <h3>{section.title}</h3>
              {section.blocks.map((block) => (
                <details key={block.block_id} className="draft-block">
                  <summary>{block.text}</summary>
                  <EvidenceLinks ids={block.source_chunk_ids.concat(block.reference_item_ids)} evidenceById={evidenceById} />
                </details>
              ))}
              <label className="wide-label">
                Section regeneration feedback
                <textarea value={regenerationFeedback[section.section_id] || ""} onChange={(e) => setRegenerationFeedback({ ...regenerationFeedback, [section.section_id]: e.target.value })} />
              </label>
              <button disabled={busy} onClick={() => regenerate(section.section_id)}>Regenerate this section</button>
            </article>
          ))}
        </section>
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

function TemplateSectionList({ sections }: { sections: EvidencePlan["template"]["sections"] }) {
  return (
    <div className="template-section-list">
      {sections.map((section, index) => (
        <div key={section.section_id} className="template-section-row">
          <span>{index + 1}</span>
          <strong>{section.title}</strong>
          <small>Level {section.level}</small>
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

function EvidencePlanReviewList({
  sections,
  metadata
}: {
  sections: EvidenceSection[];
  metadata?: EvidencePlan["retrieval_metadata"];
}) {
  return (
    <div className="evidence-plan-list">
      {metadata && (
        <div className="retrieval-metadata">
          Retrieval: {metadata.retrieval_mode} · tokenizer: {metadata.tokenizer || "auto"} · normalization: {metadata.script_normalization || "dual"} · chunks: {metadata.chunk_method} · top-k: {metadata.source_top_k}/{metadata.reference_top_k}
        </div>
      )}
      {sections.map((section) => (
        <article className="evidence-plan-row" key={section.section_id}>
          <div className="evidence-plan-header">
            <strong>{section.section_title}</strong>
            <small>
              {section.source_chunks.length} source candidates · {section.reference_items.length} reference candidates · {section.warnings.length} warnings
            </small>
          </div>
          {section.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
          <div className="evidence-candidate-grid">
            <EvidenceCandidateColumn title="Source candidates" items={section.source_chunks} />
            <EvidenceCandidateColumn title="Reference candidates" items={section.reference_items} />
          </div>
        </article>
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
  return (
    <article className="evidence-candidate-card">
      <div className="candidate-meta">
        <span>#{rank}</span>
        <strong>{item.file_name}</strong>
        <small>{item.location || "location unknown"} · score {item.score.toFixed(2)}</small>
      </div>
      <p className="candidate-reason">{item.reason}</p>
      <p className="candidate-summary">{item.summary}</p>
      <p className="candidate-excerpt">{item.excerpt}</p>
    </article>
  );
}

function DownloadPanel({
  jobId,
  isCompleted,
  downloadLinksRef
}: {
  jobId: string;
  isCompleted: boolean;
  downloadLinksRef: RefObject<HTMLDivElement>;
}) {
  return (
    <div className="output-panel" ref={downloadLinksRef}>
      <div>
        <h3>{isCompleted ? "Final output" : "Draft output"}</h3>
        <p className="muted">
          {isCompleted
            ? "The draft is approved. Download the final SOP document or supporting reports."
            : "The DOCX is available for review. Approve the draft before sharing it as final."}
        </p>
      </div>
      <a className="primary-download" href={`/api/jobs/${jobId}/download/final_sop.docx`}>
        <span className="download-filetype">DOCX</span>
        <span className="download-copy">
          <strong>{isCompleted ? "Download final SOP DOCX" : "Download draft DOCX"}</strong>
          <span>{isCompleted ? "Approved document" : "Review copy"}</span>
        </span>
      </a>
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

function gateState(gate: GateName, status: JobStatus | null, evidencePlan: EvidencePlan | null, draft: GenerationResult | null): StepState {
  if (status?.pending_gates?.includes(gate)) return "pending";
  if (gate === "draft_review") {
    if (!draft) return "locked";
    return status?.review_settings?.draft_review_enabled === false ? "auto" : "done";
  }
  if (!evidencePlan) return "locked";
  const enabled = gate === "template_review"
    ? status?.review_settings?.template_review_enabled
    : status?.review_settings?.evidence_review_enabled;
  return enabled === false ? "auto" : "done";
}

function getCurrentTask(status: JobStatus | null, evidencePlan: EvidencePlan | null, draft: GenerationResult | null) {
  if (!status) return { title: "Create a job first", detail: "Upload at least one source PDF and one DOCX template, then create a job." };
  if (status.status === "analyzing") return { title: "Analysis is running", detail: status.message || "The system is parsing files and planning evidence." };
  if (status.status === "generating") return { title: "Draft generation is running", detail: status.message || "The system is generating SOP sections." };
  if (!evidencePlan) return { title: "Next: run analysis", detail: "This creates the template section list and evidence plan for review." };
  if (status.pending_gates?.includes("template_review")) return { title: "Next: approve template sections", detail: "Confirm the detected DOCX sections before generation uses them." };
  if (status.pending_gates?.includes("evidence_review")) return { title: "Next: approve evidence plan", detail: "Check source/reference evidence and add feedback if needed." };
  if (!draft) return { title: "Next: generate draft", detail: "Template and evidence gates are cleared." };
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

function EvidenceSectionView({ section, feedback, setFeedback }: { section: EvidenceSection; feedback: string; setFeedback: (value: string) => void }) {
  return (
    <article className="evidence-section">
      <h3>{section.section_title}</h3>
      {section.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
      <div className="evidence-grid">
        <EvidenceColumn title="Source chunks" items={section.source_chunks} />
        <EvidenceColumn title="Reference items" items={section.reference_items} />
      </div>
      <label className="wide-label">
        Section feedback
        <textarea value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="Optional guidance for this section." />
      </label>
    </article>
  );
}

function EvidenceColumn({ title, items }: { title: string; items: EvidenceRef[] }) {
  return (
    <div>
      <h4>{title}</h4>
      {items.length === 0 && <p className="muted">No planned evidence.</p>}
      {items.map((item) => (
        <details key={item.evidence_id} className="evidence-item">
          <summary>{item.file_name} {item.location ? `· ${item.location}` : ""} · {item.score.toFixed(2)}</summary>
          <p>{item.summary}</p>
          <pre>{item.excerpt}</pre>
        </details>
      ))}
    </div>
  );
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
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
