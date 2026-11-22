// ResumeIQ: Engineering Diagnostic Workbench Logic
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const esc = t => String(t || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let currentFile = null;
let currentResumeText = '';
let analysisData = null;
let selectedJobIdx = 0;
let currentTopN = 5;
let currentMode = 'target'; // 'target' or 'predict'
let authConfig = { google_client_id: '', dev_mode: true };
let currentUser = null;

// Default inline SVG avatar generator for users without avatar URLs
function getDefaultAvatar(name) {
  const initial = (name || 'E').charAt(0).toUpperCase();
  return `data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36"><rect width="36" height="36" rx="18" fill="%232563eb"/><text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle" fill="white" font-family="system-ui,-apple-system,sans-serif" font-size="15" font-weight="600">${initial}</text></svg>`;
}

// Validation Banner Helpers
function showValidationError(msg, title = 'Not a Resume File') {
  const banner = $('#validation-error-card');
  const titleEl = $('#validation-error-title');
  const textEl = $('#validation-error-text');
  if (titleEl) titleEl.textContent = title;
  if (textEl) textEl.textContent = msg;
  if (banner) banner.hidden = false;

  const drop = $('#drop');
  if (drop) {
    drop.classList.add('drop-error');
    drop.classList.remove('drop-success');
  }
}

function hideValidationError() {
  const banner = $('#validation-error-card');
  if (banner) banner.hidden = true;

  const drop = $('#drop');
  if (drop) {
    drop.classList.remove('drop-error');
  }
}

// Stale State Prevention: Reset all previous analysis results immediately
function resetPreviousResults() {
  analysisData = null;
  currentResumeText = '';
  selectedJobIdx = 0;

  // Hide analysis output, warnings, summary card, and validation error
  if ($('#out')) $('#out').hidden = true;
  if ($('#resume-warnings-card')) $('#resume-warnings-card').hidden = true;
  const sumCard = $('#diagnostic-summary-card');
  if (sumCard) {
    sumCard.setAttribute('hidden', '');
    sumCard.style.display = 'none';
  }
  hideValidationError();
  updatePhaseStates();

  const drop = $('#drop');
  if (drop) {
    drop.classList.remove('drop-error');
    drop.classList.remove('drop-success');
  }

  // Reset score and radial progress
  if ($('#score')) $('#score').textContent = '--';
  if ($('#jobname')) $('#jobname').textContent = 'Awaiting analysis...';
  if ($('#method')) $('#method').textContent = 'Hybrid Match';
  const arc = $('#arc');
  if (arc) arc.style.strokeDashoffset = 326.7;

  // Clear score breakdown bars
  if ($('#bars')) $('#bars').innerHTML = '';

  // Clear ATS score and tips
  if ($('#ats')) $('#ats').textContent = '--/100';
  if ($('#tips')) $('#tips').innerHTML = '';

  // Clear evidence document
  if ($('#doc')) $('#doc').innerHTML = '';

  // Clear gaps summary in score phase
  if ($('#gaps-count-badge')) $('#gaps-count-badge').textContent = '0 Gaps';
  if ($('#gaps-tags-summary')) $('#gaps-tags-summary').innerHTML = '';

  // Clear study hub
  if ($('#study-links-container')) $('#study-links-container').innerHTML = '';

  // Clear auto-predicted roles
  if ($('#predicted-roles-grid')) $('#predicted-roles-grid').innerHTML = '';

  // Clear open matching roles
  if ($('#open-roles-grid')) $('#open-roles-grid').innerHTML = '';

  // Clear warnings list
  if ($('#resume-warnings-list')) $('#resume-warnings-list').innerHTML = '';

  // Clear bullet rewrite output
  if ($('#rewout')) {
    $('#rewout').hidden = true;
    $('#rewout').textContent = '';
  }
}

// ============================================================================
// Left-Hand Sidebar Navigation & Mobile Drawer
// ============================================================================

function openSidebar() {
  const sidebar = $('#sidebar');
  const backdrop = $('#sidebar-backdrop');
  if (sidebar) sidebar.classList.add('open');
  if (backdrop) backdrop.hidden = false;
}

function closeSidebar() {
  const sidebar = $('#sidebar');
  const backdrop = $('#sidebar-backdrop');
  if (sidebar) sidebar.classList.remove('open');
  if (backdrop) backdrop.hidden = true;
}

if ($('#sidebar-toggle')) $('#sidebar-toggle').onclick = openSidebar;
if ($('#sidebar-close')) $('#sidebar-close').onclick = closeSidebar;
if ($('#sidebar-backdrop')) $('#sidebar-backdrop').onclick = closeSidebar;

// Phase Navigation & Workbench Views
let currentPhase = 'diagnostic';

function updatePhaseStates() {
  const hasAnalysis = !!analysisData;
  const requiresAnalysis = ['score', 'predictions', 'study-hub', 'open-roles'];

  requiresAnalysis.forEach(p => {
    const emptyEl = document.getElementById(`empty-phase-${p}`);
    const contentEl = document.getElementById(`content-phase-${p}`);
    if (emptyEl && contentEl) {
      if (hasAnalysis) {
        emptyEl.setAttribute('hidden', '');
        emptyEl.style.display = 'none';
        contentEl.removeAttribute('hidden');
        contentEl.style.display = '';
      } else {
        emptyEl.removeAttribute('hidden');
        emptyEl.style.display = '';
        contentEl.setAttribute('hidden', '');
        contentEl.style.display = 'none';
      }
    }
  });
}

function switchPhase(phaseId) {
  if (!phaseId) return;
  currentPhase = phaseId;

  // 1. Highlight active nav button
  $$('.sidebar-nav-item').forEach(btn => {
    if (btn.dataset.phase === phaseId) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  // 2. Hide all other phases, show only the target phase
  $$('.app-phase').forEach(phase => {
    if (phase.id === `phase-${phaseId}`) {
      phase.classList.add('active');
      phase.removeAttribute('hidden');
      phase.style.display = 'block';
    } else {
      phase.classList.remove('active');
      phase.setAttribute('hidden', '');
      phase.style.display = 'none';
    }
  });

  // 3. Update topbar title
  const activeBtn = document.querySelector(`.sidebar-nav-item[data-phase="${phaseId}"]`);
  const label = activeBtn && activeBtn.querySelector('span') ? activeBtn.querySelector('span').textContent : 'Diagnostic';
  if ($('#topbar-title')) $('#topbar-title').textContent = label;

  // 4. Update phase empty/content states
  updatePhaseStates();

  // 5. Special phase triggers
  if (phaseId === 'insights') {
    loadInsights();
  }

  // 6. Scroll smoothly to top
  window.scrollTo({ top: 0, behavior: 'smooth' });

  // 7. Auto close mobile sidebar
  closeSidebar();
}

// Sidebar navigation link clicks
$$('.sidebar-nav-item').forEach(btn => {
  btn.onclick = () => {
    const phase = btn.dataset.phase;
    if (phase) switchPhase(phase);
  };
});

// Delegate [data-goto-phase] and sample loading buttons across all phases
document.addEventListener('click', e => {
  const gotoBtn = e.target.closest('[data-goto-phase]');
  if (gotoBtn) {
    const phase = gotoBtn.dataset.gotoPhase;
    if (phase) switchPhase(phase);
    return;
  }

  const sampleInstantBtn = e.target.closest('.btn-load-sample-instant');
  if (sampleInstantBtn) {
    loadSampleAndRun();
    return;
  }
});

// Instant sample resume execution helper
async function loadSampleAndRun() {
  try {
    const res = await fetch('/data/sample_resume.txt');
    if (!res.ok) throw new Error('Sample resume file not found');
    const txt = await res.text();
    currentResumeText = txt;
    selectedFile = null;
    const fname = $('#fname');
    if (fname) fname.textContent = 'sample_resume.txt (Loaded)';
    const drop = $('#drop');
    if (drop) {
      drop.classList.remove('drop-error');
      drop.classList.add('drop-success');
    }
    switchPhase('diagnostic');
    if ($('#go')) $('#go').click();
  } catch (err) {
    say('#msg', 'Error loading sample: ' + err.message, true);
  }
}

// Mode Toggle (Specify Role vs Predict Best Roles)
$('#mode-target').onclick = () => {
  currentMode = 'target';
  $('#mode-target').classList.add('on');
  $('#mode-predict').classList.remove('on');
  $('#target-role-container').hidden = false;
  $('#predict-role-container').hidden = true;
  resetPreviousResults();
};

$('#mode-predict').onclick = () => {
  currentMode = 'predict';
  $('#mode-predict').classList.add('on');
  $('#mode-target').classList.remove('on');
  $('#target-role-container').hidden = true;
  $('#predict-role-container').hidden = false;
  resetPreviousResults();
};

// Clear previous state when target role text is edited
$('#jd').addEventListener('input', () => {
  if (analysisData) {
    resetPreviousResults();
    say('#msg', 'Target role modified. Click "Run Diagnostic" to re-evaluate.');
  }
});

// Predict Selector (Top 5 vs Top 10)
$$('.btn-select').forEach(btn => {
  btn.onclick = () => {
    $$('.btn-select').forEach(b => b.classList.toggle('on', b === btn));
    currentTopN = parseInt(btn.dataset.topn, 10) || 5;
    if (analysisData && currentResumeText) {
      loadPredictedRoles(currentTopN);
    }
  };
});

// Filter buttons in Predicted Roles section
$('#btn-top-5').onclick = () => {
  $('#btn-top-5').classList.add('on');
  $('#btn-top-10').classList.remove('on');
  loadPredictedRoles(5);
};

$('#btn-top-10').onclick = () => {
  $('#btn-top-10').classList.add('on');
  $('#btn-top-5').classList.remove('on');
  loadPredictedRoles(10);
};

// ============================================================================
// File Upload & Instant Non-Resume Detection
// ============================================================================

const setFile = async file => {
  resetPreviousResults();
  if (!file) return;

  const ext = (file.name.slice(file.name.lastIndexOf('.')) || '').toLowerCase();
  const allowed = ['.pdf', '.docx', '.txt'];

  // 1. Extension Guard
  if (!allowed.includes(ext)) {
    currentFile = null;
    $('#fname').textContent = `${file.name} (Not a resume)`;
    say('#msg', `Unsupported file type (${ext || 'unknown'}). Please upload a suitable resume file.`, true);
    showValidationError(
      `The file "${file.name}" is not a supported resume format. Please upload a suitable resume file in PDF, DOCX, or TXT format.`,
      'Unsupported File Type'
    );
    $('#go').disabled = true;
    return;
  }

  // 2. Scan file content immediately
  $('#fname').textContent = file.name;
  say('#msg', 'Scanning uploaded file structure...');
  hideValidationError();

  try {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/validate', { method: 'POST', body: fd });
    const check = await res.json();

    if (!check.valid_resume) {
      currentFile = null;
      $('#fname').textContent = `${file.name} (Not a resume)`;
      const errorMsg = check.message || "This file does not appear to be a resume. Please upload a suitable resume file.";
      say('#msg', 'This file is not a resume. Please upload a suitable file.', true);
      showValidationError(errorMsg, 'Not a Resume File');
      $('#go').disabled = true;
      return;
    }

    // Valid resume verified!
    currentFile = file;
    hideValidationError();
    say('#msg', 'Resume verified. Click "Run Diagnostic" to analyze.', false);
    $('#go').disabled = false;
  } catch (err) {
    currentFile = file;
    say('#msg', 'Resume file ready for diagnostic.', false);
    $('#go').disabled = false;
  }
};

$('#file').onchange = e => e.target.files[0] && setFile(e.target.files[0]);
const dropzone = $('#drop');
['dragover', 'dragenter'].forEach(ev => dropzone.addEventListener(ev, e => {
  e.preventDefault();
  dropzone.classList.add('over');
}));
['dragleave', 'drop'].forEach(ev => dropzone.addEventListener(ev, e => {
  e.preventDefault();
  dropzone.classList.remove('over');
}));
dropzone.addEventListener('drop', e => e.dataTransfer.files[0] && setFile(e.dataTransfer.files[0]));

// Sample Resume Button
$('#sample').onclick = async () => {
  try {
    resetPreviousResults();
    say('#msg', 'Loading verified sample technical resume...');
    const res = await fetch('/api/sample');
    const { text } = await res.json();
    currentFile = new File([text], 'sample_resume.txt', { type: 'text/plain' });
    $('#fname').textContent = 'sample_resume.txt';
    hideValidationError();
    $('#jd').value = 'Backend Engineer with Python, FastAPI, Docker, and SQL experience.';
    runAnalysis();
  } catch (err) {
    say('#msg', 'Failed to load sample resume: ' + err.message, true);
  }
};

// Load Sample Resume from Validation Error Banner
$('#btn-load-sample-from-error').onclick = () => {
  $('#sample').click();
};

$('#go').onclick = runAnalysis;

// API Helper with Structured Error Parsing
async function postForm(url, formData) {
  const resp = await fetch(url, { method: 'POST', body: formData });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    let msg = 'Server responded with an error.';
    let detailPayload = null;
    if (data.detail) {
      if (typeof data.detail === 'object') {
        msg = data.detail.message || JSON.stringify(data.detail);
        detailPayload = data.detail;
      } else {
        msg = String(data.detail);
      }
    } else if (data.message) {
      msg = data.message;
    }
    const err = new Error(msg);
    err.payload = detailPayload || data;
    err.status = resp.status;
    throw err;
  }
  return data;
}

function say(selector, text, isErr = false) {
  const el = $(selector);
  if (!el) return;
  el.textContent = text;
  el.className = isErr ? 'status-indicator err' : 'status-indicator';
}

// Main Diagnostic Execution
async function runAnalysis() {
  if (!currentFile) {
    showValidationError(
      'No resume file selected. Please upload a suitable resume file (PDF, DOCX, or TXT) before running the diagnostic.',
      'Resume Required'
    );
    return say('#msg', 'Please select or upload a resume file first.', true);
  }

  // Clear any existing results immediately before starting new run
  resetPreviousResults();

  const fd = new FormData();
  fd.append('file', currentFile);
  const targetJd = currentMode === 'target' ? $('#jd').value.trim() : '';
  fd.append('jd', targetJd);
  fd.append('top_n', currentTopN);

  $('#go').disabled = true;
  $('#analysis-skeleton').hidden = false;
  say('#msg', 'Evaluating resume against technical taxonomy and structural standards...');

  try {
    analysisData = await postForm('/api/analyze', fd);
    $('#analysis-skeleton').hidden = true;
    currentResumeText = analysisData.text || '';
    selectedJobIdx = analysisData.custom ? -1 : 0;
    say('#msg', '');

    // Surface explicit resume quality warnings if present
    if (analysisData.warnings && analysisData.warnings.length) {
      $('#resume-warnings-list').innerHTML = analysisData.warnings
        .map(w => `<li>${esc(w)}</li>`)
        .join('');
      $('#resume-warnings-card').hidden = false;
    } else {
      $('#resume-warnings-card').hidden = true;
    }

    renderDashboard();
    loadOpenRoles(getActiveRoleTitle());
    loadInsights();

    // Automatically navigate to Match Fit & ATS phase so user sees their detailed results immediately
    switchPhase('score');

    // Refresh user drawer history if signed in
    if (currentUser) {
      fetchUserHistory();
    }
  } catch (err) {
    $('#analysis-skeleton').hidden = true;
    resetPreviousResults();
    say('#msg', err.message, true);

    // Display validation error banner with structured guidance
    const validationMsg = (err.payload && err.payload.message) || err.message;
    showValidationError(validationMsg, 'Not a Resume File');
  } finally {
    $('#go').disabled = false;
  }
}

function getActiveJob() {
  if (!analysisData) return null;
  if (selectedJobIdx === -1 && analysisData.custom) {
    return analysisData.custom;
  }
  return analysisData.matches[selectedJobIdx] || analysisData.matches[0];
}

function getActiveRoleTitle() {
  const activeJob = getActiveJob();
  return activeJob ? activeJob.title : 'Software Engineer';
}

// Render Dashboard View
function renderDashboard() {
  $('#out').hidden = false;
  updatePhaseStates();

  const active = getActiveJob();
  if (!active) return;

  // Update diagnostic summary card
  const sumCard = $('#diagnostic-summary-card');
  if (sumCard) {
    sumCard.removeAttribute('hidden');
    sumCard.style.display = 'block';
  }
  const scoreVal = Math.round(active.score);
  if ($('#summary-score-val')) $('#summary-score-val').textContent = scoreVal;
  if ($('#summary-role-name')) $('#summary-role-name').textContent = active.title || 'Target Role';

  // Update missing skill gaps summary card in phase-score
  const gaps = active.missing || [];
  if ($('#gaps-count-badge')) $('#gaps-count-badge').textContent = `${gaps.length} Missing`;
  if ($('#gaps-tags-summary')) {
    if (gaps.length) {
      $('#gaps-tags-summary').innerHTML = gaps.map(g => `<span class="gap-pill">${esc(g)}</span>`).join('');
    } else {
      $('#gaps-tags-summary').innerHTML = '<span class="status-indicator">All role skills matched in resume!</span>';
    }
  }

  // 1. Score Overview & Radial Progress
  $('#score').textContent = scoreVal;
  $('#jobname').textContent = `${active.title} · ${active.company || 'Industry Standard'}`;
  $('#method').textContent = active.method || 'Hybrid NLP Match';

  // Smooth arc animation
  requestAnimationFrame(() => {
    const arc = $('#arc');
    if (arc) {
      arc.style.strokeDashoffset = 326.7 * (1 - Math.min(100, Math.max(0, scoreVal)) / 100);
    }
  });

  // Score breakdown bars
  const bd = active.breakdown || {};
  const semVal = bd.semantic != null ? bd.semantic : 0;
  const skillVal = bd.skills != null ? bd.skills : 0;
  const expVal = bd.experience != null ? bd.experience : 0;

  $('#bars').innerHTML = `
    <div class="bar-row">
      <div class="bar-meta"><span class="bar-name">Skill Overlap <small>(Taxonomy Match)</small></span><span class="bar-value">${skillVal}%</span></div>
      <div class="bar-track"><div class="bar-fill" style="width: ${skillVal}%"></div></div>
    </div>
    <div class="bar-row">
      <div class="bar-meta"><span class="bar-name">Semantic Relevance <small>(TF-IDF / Embeddings)</small></span><span class="bar-value">${semVal}%</span></div>
      <div class="bar-track"><div class="bar-fill" style="width: ${semVal}%"></div></div>
    </div>
    <div class="bar-row">
      <div class="bar-meta"><span class="bar-name">Experience Match <small>(Seniority Alignment)</small></span><span class="bar-value">${expVal}%</span></div>
      <div class="bar-track"><div class="bar-fill" style="width: ${expVal}%"></div></div>
    </div>
  `;

  // 2. ATS Screening Card
  $('#ats').textContent = `${analysisData.ats.score}/100`;
  const tips = analysisData.ats.tips || [];
  $('#tips').innerHTML = tips.length
    ? tips.map(t => `<li>${esc(t)}</li>`).join('')
    : '<li>Document structure and contact credentials satisfy ATS guidelines.</li>';

  // 3. Extracted Resume Evidence Document
  let docHtml = '';
  let cursor = 0;
  const matchedSet = new Set(active.matched || []);
  const spans = analysisData.spans || [];

  spans.forEach(([start, end, canonical]) => {
    docHtml += esc(currentResumeText.slice(cursor, start));
    const isHit = matchedSet.has(canonical);
    docHtml += `<i class="${isHit ? 'hit' : 'oth'}">${esc(currentResumeText.slice(start, end))}</i>`;
    cursor = end;
  });
  docHtml += esc(currentResumeText.slice(cursor));
  $('#doc').innerHTML = docHtml;

  // 4. Missing Skills Study Hub
  renderStudyHub(analysisData.study_links || []);

  // 5. Auto-Predicted Roles
  renderPredictedRoles(analysisData.predicted_roles || []);
}

// Render Missing Skills YouTube Study Hub
function renderStudyHub(links) {
  const container = $('#study-links-container');
  if (!links || !links.length) {
    container.innerHTML = `<p class="card-hint">No skill gaps found! Your resume covers all required skills for this role.</p>`;
    return;
  }

  container.innerHTML = links.map(item => `
    <div class="study-card">
      <div>
        <div class="study-card-meta">
          <span class="study-skill-badge">${esc(item.skill)}</span>
          <span class="study-source-tag">${esc(item.source === 'curated' ? 'Curated' : 'Verified Tutorial')}</span>
        </div>
        <h4 class="study-title">${esc(item.title)}</h4>
        <div class="study-channel">Channel: ${esc(item.channel)}</div>
      </div>
      <a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" class="study-action-btn">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="5 3 19 12 5 21 5 3"></polygon>
        </svg>
        Watch Tutorial
      </a>
    </div>
  `).join('');
}

// Render Predicted Roles
function renderPredictedRoles(roles) {
  const container = $('#predicted-roles-grid');
  if (!roles || !roles.length) {
    container.innerHTML = `<p class="card-hint">No predicted roles found.</p>`;
    return;
  }

  container.innerHTML = roles.map((r, i) => {
    const isCurrent = (selectedJobIdx === i);
    const gapsHtml = (r.top_gaps || []).map(g => `<span class="gap-pill">${esc(g)}</span>`).join('');

    return `
      <button class="predicted-role-card ${isCurrent ? 'active' : ''}" data-idx="${i}" data-title="${esc(r.title)}" type="button">
        <div class="role-header-row">
          <strong class="role-title">${esc(r.title)}</strong>
          <span class="role-score-badge">${Math.round(r.score)}%</span>
        </div>
        <div class="role-why-text">${esc(r.why_it_fits)}</div>
        <div class="role-gaps-row">
          <span style="font-size:0.72rem; color:var(--ink-muted); margin-right:4px;">Gaps:</span>
          ${gapsHtml || '<span style="font-size:0.72rem; color:var(--pass);">None</span>'}
        </div>
      </button>
    `;
  }).join('');

  // Handle clicking a predicted role
  container.querySelectorAll('.predicted-role-card').forEach(btn => {
    btn.onclick = async () => {
      const idx = parseInt(btn.dataset.idx, 10);
      selectedJobIdx = idx;
      const targetTitle = btn.dataset.title;

      container.querySelectorAll('.predicted-role-card').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      $('#jd').value = targetTitle;
      say('#msg', `Updating diagnostic workbench for: ${targetTitle}...`);

      const targetJob = roles[idx];
      if (targetJob && targetJob.missing && targetJob.missing.length) {
        const fd = new FormData();
        fd.append('skills', targetJob.missing.join(','));
        try {
          const res = await postForm('/api/skills/study', fd);
          renderStudyHub(res.study_links || []);
        } catch (_) {}
      }

      loadOpenRoles(targetTitle);
      renderDashboard();
      switchPhase('score');
      say('#msg', `Evaluating fit for ${targetTitle}`);
    };
  });
}

// Fetch and load Top N predicted roles dynamically
async function loadPredictedRoles(topN) {
  if (!currentResumeText) return;
  const fd = new FormData();
  fd.append('resume_text', currentResumeText);
  fd.append('top_n', topN);
  try {
    const res = await postForm('/api/roles/predict', fd);
    renderPredictedRoles(res.predicted_roles || []);
  } catch (err) {
    console.error('Failed to load predicted roles:', err);
  }
}

// Fetch Matching Open Roles from Free APIs
async function loadOpenRoles(query) {
  const container = $('#open-roles-grid');
  container.innerHTML = `<p class="card-hint">Fetching matching open positions from free job feeds (Remotive / Arbeitnow / Local cache)...</p>`;

  const fd = new FormData();
  fd.append('role_query', query || 'Software Engineer');
  fd.append('resume_text', currentResumeText);

  try {
    const res = await postForm('/api/roles/open', fd);
    renderOpenRoles(res.open_roles || []);
  } catch (err) {
    container.innerHTML = `<p class="card-hint">Could not connect to external job feed. Displaying verified local database roles.</p>`;
  }
}

function renderOpenRoles(roles) {
  const container = $('#open-roles-grid');
  if (!roles || !roles.length) {
    container.innerHTML = `<p class="card-hint">No open roles found matching current filter.</p>`;
    return;
  }

  container.innerHTML = roles.map(j => {
    const matchedPills = (j.matched || []).slice(0, 4).map(s => `<span class="skill-tag-matched">${esc(s)}</span>`).join('');
    const missingPills = (j.missing || []).slice(0, 3).map(s => `<span class="skill-tag-missing">${esc(s)}</span>`).join('');

    return `
      <div class="open-job-card">
        <div>
          <div class="open-job-header">
            <h4 class="open-job-title">${esc(j.title)}</h4>
            <span class="role-score-badge">${Math.round(j.score)}% Match</span>
          </div>
          <div class="open-job-company">${esc(j.company)}</div>
          <div class="open-job-location">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path><circle cx="12" cy="10" r="3"></circle></svg>
            ${esc(j.location || 'Remote')}
          </div>
          <div class="open-job-skills-block">
            <div class="skills-pill-group">
              ${matchedPills}
              ${missingPills}
            </div>
          </div>
        </div>
        <div class="open-job-footer">
          <span class="open-job-source">Source: ${esc(j.source)}</span>
          <a href="${esc(j.url)}" target="_blank" rel="noopener noreferrer" class="open-job-link">
            View Listing
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
          </a>
        </div>
      </div>
    `;
  }).join('');
}

$('#refresh-open-roles').onclick = () => {
  loadOpenRoles(getActiveRoleTitle());
};

// Rewrite Weak Bullets
$('#rew').onclick = async () => {
  if (!currentFile) return;
  const fd = new FormData();
  fd.append('file', currentFile);
  const out = $('#rewout');
  out.hidden = false;
  out.textContent = 'Analyzing bullet impact...';
  try {
    const res = await postForm('/api/rewrite', fd);
    out.textContent = res.suggestions || 'No weak bullets flagged.';
  } catch (err) {
    out.textContent = 'Optimization failed: ' + err.message;
  }
};

// Market Insights View
async function loadInsights() {
  try {
    const res = await fetch('/api/insights');
    const d = await res.json();
    $('#insight-count').textContent = `${d.resumes} Resumes Analyzed`;
    const inDemand = d.in_demand || [];
    const maxCount = inDemand[0] ? inDemand[0].jobs : 1;

    $('#ins').innerHTML = inDemand.map(r => `
      <div class="bar-row">
        <div class="bar-meta">
          <span class="bar-name">${esc(r.name)}</span>
          <span class="bar-value">${r.jobs} positions</span>
        </div>
        <div class="bar-track">
          <div class="bar-fill" style="width: ${(r.jobs / maxCount) * 100}%"></div>
        </div>
      </div>
    `).join('');
  } catch (err) {
    console.error('Insights fetch error:', err);
  }
}

// Recruiter Screening Tab
$('#rfiles').onchange = e => {
  const count = e.target.files.length;
  $('#rname').textContent = `${count} resume(s) selected`;
};

$('#rgo').onclick = async () => {
  const files = $('#rfiles').files;
  const jd = $('#rjd').value.trim();
  if (!files.length || !jd) {
    return say('#rmsg', 'Please upload resumes and enter a job description.', true);
  }

  const fd = new FormData();
  [...files].forEach(f => fd.append('files', f));
  fd.append('jd', jd);
  fd.append('anonymize', $('#anon').checked);

  $('#rgo').disabled = true;
  say('#rmsg', 'Ranking candidate stack against role criteria...');

  try {
    const data = await postForm('/api/recruiter', fd);
    say('#rmsg', '');
    $('#rout').hidden = false;
    const candidates = data.candidates || [];
    const reqSkills = data.required || [];

    $('#rout').innerHTML = `
      <div class="card-title-row">
        <h3>Ranked Candidates (${candidates.length})</h3>
        <span class="badge-neutral">Required: ${reqSkills.map(esc).join(', ')}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Rank</th>
            <th>Candidate</th>
            <th>Match Score</th>
            <th>Skill Coverage</th>
            <th>Experience</th>
            <th>ATS Score</th>
            <th>Missing Skills</th>
          </tr>
        </thead>
        <tbody>
          ${candidates.map((c, i) => `
            <tr>
              <td><strong>#${i + 1}</strong></td>
              <td>${esc(c.name)}</td>
              <td><span class="role-score-badge">${Math.round(c.score)}%</span></td>
              <td>${c.matched.length} / ${reqSkills.length}</td>
              <td>${c.years} yrs</td>
              <td>${c.ats}/100</td>
              <td>${(c.missing || []).map(s => `<span class="gap-pill">${esc(s)}</span>`).join(' ') || '<span style="color:var(--pass)">None</span>'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  } catch (err) {
    say('#rmsg', err.message, true);
  } finally {
    $('#rgo').disabled = false;
  }
};

// ============================================================================
// Sign-In Modal, Google OAuth & Account Management
// ============================================================================

function openAuthModal() {
  const backdrop = $('#auth-modal-backdrop');
  if (backdrop) {
    backdrop.removeAttribute('hidden');
    backdrop.style.display = 'flex';
  }
  const msgEl = $('#modal-auth-msg');
  if (msgEl) {
    msgEl.textContent = '';
    msgEl.className = 'modal-auth-msg';
  }
}

function closeAuthModal() {
  const backdrop = $('#auth-modal-backdrop');
  if (backdrop) {
    backdrop.setAttribute('hidden', '');
    backdrop.style.display = 'none';
  }
}

// Open modal triggers
$$('.btn-open-login').forEach(btn => {
  btn.onclick = openAuthModal;
});

// Close / Cancel modal triggers
if ($('#auth-modal-close')) $('#auth-modal-close').onclick = closeAuthModal;
if ($('#auth-modal-cancel')) $('#auth-modal-cancel').onclick = closeAuthModal;
if ($('#auth-modal-backdrop')) {
  $('#auth-modal-backdrop').onclick = e => {
    if (e.target === $('#auth-modal-backdrop')) closeAuthModal();
  };
}
window.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeAuthModal();
});

// Render Authenticated User State in UI
function renderAuthUser(user, history = []) {
  currentUser = user;
  const avatarSrc = user.avatar_url || getDefaultAvatar(user.name);

  // 1. Sidebar views
  if ($('#sidebar-guest-view')) $('#sidebar-guest-view').hidden = true;
  if ($('#sidebar-user-view')) $('#sidebar-user-view').hidden = false;
  if ($('#sidebar-user-avatar')) $('#sidebar-user-avatar').src = avatarSrc;
  if ($('#sidebar-user-name')) $('#sidebar-user-name').textContent = user.name || 'User';
  if ($('#sidebar-user-email')) $('#sidebar-user-email').textContent = user.email || '';

  // 2. Topbar views
  if ($('#topbar-login-btn')) $('#topbar-login-btn').hidden = true;
  if ($('#topbar-user-chip')) $('#topbar-user-chip').hidden = false;
  if ($('#topbar-user-avatar')) $('#topbar-user-avatar').src = avatarSrc;
  if ($('#topbar-user-name')) $('#topbar-user-name').textContent = user.name || 'User';

  // 3. User Drawer
  if ($('#drawer-avatar')) $('#drawer-avatar').src = avatarSrc;
  if ($('#drawer-user-name')) $('#drawer-user-name').textContent = user.name || 'User';
  if ($('#drawer-user-email')) $('#drawer-user-email').textContent = user.email || '';

  renderDrawerHistory(history);
}

// Render Guest State
function renderGuestUser() {
  currentUser = null;

  if ($('#sidebar-guest-view')) $('#sidebar-guest-view').hidden = false;
  if ($('#sidebar-user-view')) $('#sidebar-user-view').hidden = true;

  if ($('#topbar-login-btn')) $('#topbar-login-btn').hidden = false;
  if ($('#topbar-user-chip')) $('#topbar-user-chip').hidden = true;

  if ($('#user-drawer')) $('#user-drawer').hidden = true;
  if ($('#drawer-backdrop')) $('#drawer-backdrop').hidden = true;
}

function renderDrawerHistory(history) {
  const container = $('#drawer-history-list');
  if (!history || !history.length) {
    container.innerHTML = `<p class="drawer-empty-hint">No resumes analyzed under this account yet.</p>`;
    return;
  }

  container.innerHTML = history.map(item => `
    <div class="drawer-history-item" data-id="${item.id}">
      <div class="history-item-top">
        <strong class="history-item-name">${esc(item.filename)}</strong>
        <span class="history-item-date">${esc(item.created_at ? item.created_at.slice(0, 10) : '')}</span>
      </div>
      <div class="history-item-meta">
        <span>ATS: ${item.ats_score != null ? item.ats_score : '--'}/100</span>
        <span>•</span>
        <span>${item.skills ? item.skills.split(',').length : 0} Skills Detected</span>
      </div>
    </div>
  `).join('');
}

async function fetchUserHistory() {
  try {
    const res = await fetch('/auth/me');
    const data = await res.json();
    if (data.user) {
      renderDrawerHistory(data.history || []);
    }
  } catch (err) {
    console.error('Failed to refresh user history:', err);
  }
}

// Open and Close History Drawer
function openHistoryDrawer() {
  if ($('#user-drawer')) $('#user-drawer').hidden = false;
  if ($('#drawer-backdrop')) $('#drawer-backdrop').hidden = false;
  fetchUserHistory();
}

function closeHistoryDrawer() {
  if ($('#user-drawer')) $('#user-drawer').hidden = true;
  if ($('#drawer-backdrop')) $('#drawer-backdrop').hidden = true;
}

if ($('#btn-sidebar-history')) $('#btn-sidebar-history').onclick = openHistoryDrawer;
if ($('#topbar-user-chip')) $('#topbar-user-chip').onclick = openHistoryDrawer;
if ($('#drawer-close')) $('#drawer-close').onclick = closeHistoryDrawer;
if ($('#drawer-backdrop')) $('#drawer-backdrop').onclick = closeHistoryDrawer;

// Sign Out Action
async function handleLogout() {
  try {
    await fetch('/auth/logout', { method: 'POST' });
    renderGuestUser();
    say('#msg', 'Signed out successfully.');
  } catch (err) {
    console.error('Logout error:', err);
  }
}

if ($('#btn-sidebar-logout')) $('#btn-sidebar-logout').onclick = handleLogout;
if ($('#btn-logout')) $('#btn-logout').onclick = handleLogout;

// Modal Google Login Button
if ($('#modal-btn-google')) {
  $('#modal-btn-google').onclick = async () => {
    const msgEl = $('#modal-auth-msg');
    const btn = $('#modal-btn-google');
    if (btn) btn.disabled = true;

    if (msgEl) {
      msgEl.textContent = 'Connecting...';
      msgEl.className = 'modal-auth-msg';
    }

    try {
      // Local dev mode or instant fallback login
      const res = await fetch('/auth/dev-login', { method: 'POST' });
      const data = await res.json();
      if (data.user) {
        renderAuthUser(data.user, []);
        closeAuthModal();
        say('#msg', `Signed in as ${data.user.name}`);
      }
    } catch (err) {
      if (msgEl) {
        msgEl.textContent = 'Sign in error: ' + err.message;
        msgEl.className = 'modal-auth-msg err';
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  };
}

// Modal Custom Email / Name Login Form
if ($('#custom-login-form')) {
  $('#custom-login-form').onsubmit = async (e) => {
    e.preventDefault();
    const nameInput = $('#login-name');
    const emailInput = $('#login-email');
    const name = (nameInput && nameInput.value.trim()) || 'Ankit Kumar';
    const email = (emailInput && emailInput.value.trim()) || 'ankit@resumeiq.io';
    const msgEl = $('#modal-auth-msg');
    const submitBtn = $('#btn-submit-custom-login');

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Signing in...';
    }

    if (msgEl) {
      msgEl.textContent = 'Authenticating account...';
      msgEl.className = 'modal-auth-msg';
    }

    const fd = new FormData();
    fd.append('name', name);
    fd.append('email', email);

    try {
      const res = await fetch('/auth/custom-login', { method: 'POST', body: fd });
      const data = await res.json();
      if (data.user) {
        renderAuthUser(data.user, []);
        closeAuthModal();
        say('#msg', `Welcome, ${data.user.name}!`);
      } else {
        throw new Error('Authentication failed.');
      }
    } catch (err) {
      if (msgEl) {
        msgEl.textContent = err.message || 'Login failed.';
        msgEl.className = 'modal-auth-msg err';
      }
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Sign In / Register Account';
      }
    }
  };
}

// Modal Instant Demo Login Button
if ($('#modal-btn-dev-login')) {
  $('#modal-btn-dev-login').onclick = async () => {
    const msgEl = $('#modal-auth-msg');
    const btn = $('#modal-btn-dev-login');

    if (btn) btn.disabled = true;
    if (msgEl) {
      msgEl.textContent = 'Logging in instantly...';
      msgEl.className = 'modal-auth-msg';
    }

    try {
      const res = await fetch('/auth/dev-login', { method: 'POST' });
      const data = await res.json();
      if (data.user) {
        renderAuthUser(data.user, []);
        closeAuthModal();
        say('#msg', `Signed in as ${data.user.name} (Instant Access)`);
      }
    } catch (err) {
      if (msgEl) {
        msgEl.textContent = 'Demo login error: ' + err.message;
        msgEl.className = 'modal-auth-msg err';
      }
    } finally {
      if (btn) btn.disabled = false;
    }
  };
}

// Initialize Auth
async function initAuth() {
  try {
    const configRes = await fetch('/auth/config');
    authConfig = await configRes.json();

    if (authConfig.dev_mode) {
      if ($('#modal-google-text')) $('#modal-google-text').textContent = 'Sign In with Google (Local Dev Mode)';
    } else {
      if ($('#modal-google-text')) $('#modal-google-text').textContent = 'Continue with Google';
    }

    const meRes = await fetch('/auth/me');
    const meData = await meRes.json();
    if (meData.user) {
      renderAuthUser(meData.user, meData.history || []);
    } else {
      renderGuestUser();
    }
  } catch (err) {
    console.warn('Auth initialization fallback:', err);
    renderGuestUser();
  }
}

// Initialize on Load
document.addEventListener('DOMContentLoaded', () => {
  updatePhaseStates();
  switchPhase('diagnostic');
  initAuth();
});
if (document.readyState !== 'loading') {
  updatePhaseStates();
  switchPhase('diagnostic');
  initAuth();
}
