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

// Stale State Prevention: Reset all previous analysis results immediately
function resetPreviousResults() {
  analysisData = null;
  currentResumeText = '';
  selectedJobIdx = 0;

  // Hide analysis output, warnings, and validation error
  if ($('#out')) $('#out').hidden = true;
  if ($('#resume-warnings-card')) $('#resume-warnings-card').hidden = true;
  if ($('#validation-error-card')) $('#validation-error-card').hidden = true;

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

// Navigation Tabs
$$('.nav-tab').forEach(btn => {
  btn.onclick = () => {
    $$('.nav-tab').forEach(t => {
      const isCurrent = t === btn;
      t.classList.toggle('on', isCurrent);
      t.setAttribute('aria-selected', isCurrent ? 'true' : 'false');
    });
    $('#cand').hidden = btn.dataset.tab !== 'cand';
    $('#rec').hidden = btn.dataset.tab !== 'rec';
  };
});

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

// File Upload Handling
const setFile = file => {
  resetPreviousResults();
  currentFile = file;
  $('#fname').textContent = file.name;
  say('#msg', '');
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
    return say('#msg', 'Please select or drop a resume file first.', true);
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
    $('#validation-error-text').textContent = validationMsg;
    $('#validation-error-card').hidden = false;
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
  const active = getActiveJob();
  if (!active) return;

  // 1. Score Overview & Radial Progress
  const scoreVal = Math.round(active.score);
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
      say('#msg', '');
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
// Google OAuth & Session Management
// ============================================================================

function renderAuthUser(user, history = []) {
  currentUser = user;
  $('#btn-google-login').hidden = true;
  $('#user-chip').hidden = false;

  const avatarSrc = user.avatar_url || getDefaultAvatar(user.name);
  $('#user-avatar').src = avatarSrc;
  $('#user-name').textContent = user.name || 'User';

  $('#drawer-avatar').src = avatarSrc;
  $('#drawer-user-name').textContent = user.name || 'User';
  $('#drawer-user-email').textContent = user.email || '';

  renderDrawerHistory(history);
}

function renderGuestUser() {
  currentUser = null;
  $('#btn-google-login').hidden = false;
  $('#user-chip').hidden = true;
  $('#user-drawer').hidden = true;
  $('#drawer-backdrop').hidden = true;
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

async function initAuth() {
  try {
    // 1. Fetch server auth configuration
    const configRes = await fetch('/auth/config');
    authConfig = await configRes.json();

    if (authConfig.dev_mode) {
      $('#google-btn-text').textContent = 'Dev Login (Local Mode)';
    } else {
      $('#google-btn-text').textContent = 'Continue with Google';
    }

    // 2. Fetch current session user
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

// Google Login / Dev Login Click Handler
$('#btn-google-login').onclick = async () => {
  if (authConfig.dev_mode || !authConfig.google_client_id) {
    // Local dev mode fallback login
    say('#msg', 'Authenticating with local dev credentials...');
    try {
      const res = await fetch('/auth/dev-login', { method: 'POST' });
      const data = await res.json();
      if (data.user) {
        renderAuthUser(data.user, []);
        say('#msg', `Signed in as ${data.user.name} (Local Dev Mode)`);
      }
    } catch (err) {
      say('#msg', 'Dev login error: ' + err.message, true);
    }
  } else {
    // Production Google OAuth
    say('#msg', 'Connecting to Google OAuth...');
    // If Google Identity Services client is loaded
    if (window.google && window.google.accounts && window.google.accounts.id) {
      window.google.accounts.id.prompt();
    } else {
      say('#msg', 'Google Sign-In API is initializing. Please try again in a moment.');
    }
  }
};

// User Profile Chip Click (Toggle Drawer)
$('#user-chip').onclick = () => {
  const isHidden = $('#user-drawer').hidden;
  $('#user-drawer').hidden = !isHidden;
  $('#drawer-backdrop').hidden = !isHidden;
  if (isHidden) {
    fetchUserHistory();
  }
};

// Close Drawer Buttons
$('#drawer-close').onclick = () => {
  $('#user-drawer').hidden = true;
  $('#drawer-backdrop').hidden = true;
};

$('#drawer-backdrop').onclick = () => {
  $('#user-drawer').hidden = true;
  $('#drawer-backdrop').hidden = true;
};

// Sign Out
$('#btn-logout').onclick = async () => {
  try {
    await fetch('/auth/logout', { method: 'POST' });
    renderGuestUser();
    say('#msg', 'Signed out successfully.');
  } catch (err) {
    console.error('Logout error:', err);
  }
};

// Initialize Auth & App on Page Load
document.addEventListener('DOMContentLoaded', () => {
  initAuth();
});
if (document.readyState !== 'loading') {
  initAuth();
}
