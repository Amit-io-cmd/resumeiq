const $ = s => document.querySelector(s), esc = t => t.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let file = null, R = null, sel = 0;

document.querySelectorAll('.tab').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('on', t === b));
  $('#cand').hidden = b.dataset.tab !== 'cand'; $('#rec').hidden = b.dataset.tab !== 'rec';
});
const setFile = f => { file = f; $('#fname').textContent = f.name; };
$('#file').onchange = e => e.target.files[0] && setFile(e.target.files[0]);
const drop = $('#drop');
['dragover','dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => e.dataTransfer.files[0] && setFile(e.dataTransfer.files[0]));
$('#sample').onclick = async () => {
  const { text } = await (await fetch('/api/sample')).json();
  setFile(new File([text], 'sample_resume.txt', { type: 'text/plain' })); run();
};
$('#go').onclick = run;

async function post(url, fd) {
  const r = await fetch(url, { method: 'POST', body: fd });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || 'Something went wrong. Try again.');
  return j;
}
async function run() {
  if (!file) return say('#msg', 'Choose a resume file first.', true);
  const fd = new FormData(); fd.append('file', file); fd.append('jd', $('#jd').value);
  $('#go').disabled = true; say('#msg', 'Analyzing. The first run loads the language model and can take a minute.');
  try { R = await post('/api/analyze', fd); sel = R.custom ? -1 : 0; say('#msg', ''); show(); loadInsights(); }
  catch (e) { say('#msg', e.message, true); }
  $('#go').disabled = false;
}
function say(id, t, err) { const m = $(id); m.textContent = t; m.className = err ? 'err' : ''; }
const cur = () => sel === -1 ? R.custom : R.matches[sel];
const list = () => R.custom ? [R.custom, ...R.matches] : R.matches;

function show() {
  $('#out').hidden = false; const m = cur();
  $('#score').textContent = Math.round(m.score); $('#jobname').textContent = `${m.title} · ${m.company}`; $('#method').textContent = `Semantic engine: ${m.method}`;
  requestAnimationFrame(() => $('#arc').style.strokeDashoffset = 326.7 * (1 - m.score / 100));
  $('#bars').innerHTML = [['Meaning similarity', m.breakdown.semantic, '40%'], ['Skill overlap', m.breakdown.skills, '40%'], ['Experience fit', m.breakdown.experience, '20%']]
    .map(([n, v, w]) => `<div class="bar"><div><span>${n} <small class="muted">(weight ${w})</small></span><b>${v}%</b></div><div class="t"><div class="f" style="width:${v}%"></div></div></div>`).join('');
  $('#ats').textContent = `${R.ats.score}/100`;
  $('#tips').innerHTML = R.ats.tips.length ? R.ats.tips.map(t => `<li>${esc(t)}</li>`).join('') : '<li>Structure looks ATS-friendly.</li>';
  let html = '', p = 0; const want = new Set(m.matched);
  R.spans.forEach(([a, b, s]) => { html += esc(R.text.slice(p, a)) + `<i class="${want.has(s) ? 'hit' : 'oth'}">${esc(R.text.slice(a, b))}</i>`; p = b; });
  $('#doc').innerHTML = html + esc(R.text.slice(p));
  $('#gaps').innerHTML = m.missing.length ? m.missing.map(s => `<div class="gap"><b>${esc(s)}</b><a target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${encodeURIComponent('learn ' + s + ' for beginners')}">Find a tutorial</a></div>`).join('') : '<p>No gaps: you cover every skill this job lists.</p>';
  $('#jobs').innerHTML = list().map((j, i) => { const idx = R.custom ? i - 1 : i;
    return `<button class="job ${idx === sel ? 'on' : ''}" data-i="${idx}"><b>${esc(j.title)}</b><span class="s">${Math.round(j.score)}</span><span class="muted small">${esc(j.company)} ${j.location ? '· ' + esc(j.location) : ''}</span></button>`; }).join('');
  document.querySelectorAll('.job').forEach(b => b.onclick = () => { sel = +b.dataset.i; show(); });
}
$('#rew').onclick = async () => {
  const fd = new FormData(); fd.append('file', file); const out = $('#rewout'); out.hidden = false; out.textContent = 'Rewriting…';
  try { out.textContent = (await post('/api/rewrite', fd)).suggestions; } catch (e) { out.textContent = e.message; }
};
async function loadInsights() {
  const d = await (await fetch('/api/insights')).json();
  $('#ins').innerHTML = `<p class="muted">Most requested skills across ${d.in_demand.length ? 'all listed jobs' : ''} (${d.resumes} resumes analyzed so far):</p>` +
    d.in_demand.map(r => `<div class="bar"><div><span>${esc(r.name)}</span><b>${r.jobs} jobs</b></div><div class="t"><div class="f" style="width:${r.jobs / d.in_demand[0].jobs * 100}%"></div></div></div>`).join('');
}

/* recruiter */
$('#rfiles').onchange = e => $('#rname').textContent = `${e.target.files.length} resume(s) selected`;
$('#rgo').onclick = async () => {
  const fs = $('#rfiles').files; if (!fs.length || !$('#rjd').value.trim()) return say('#rmsg', 'Add resumes and a job description.', true);
  const fd = new FormData(); [...fs].forEach(f => fd.append('files', f)); fd.append('jd', $('#rjd').value); fd.append('anonymize', $('#anon').checked);
  $('#rgo').disabled = true; say('#rmsg', 'Ranking…');
  try {
    const d = await post('/api/recruiter', fd); say('#rmsg', ''); $('#rout').hidden = false;
    $('#rout').innerHTML = `<h3>Ranking for: ${d.required.map(esc).join(', ')}</h3><div style="overflow-x:auto"><table><tr><th>#</th><th>Candidate</th><th>Match</th><th>Skills</th><th>Years</th><th>ATS</th><th>Missing</th></tr>` +
      d.candidates.map((c, i) => `<tr><td>${i + 1}</td><td>${esc(c.name)}</td><td><b>${Math.round(c.score)}</b></td><td>${c.matched.length}/${d.required.length}</td><td>${c.years}</td><td>${c.ats}</td><td>${c.missing.map(s => `<span class="pill">${esc(s)}</span>`).join('') || '-'}</td></tr>`).join('') + '</table></div>';
  } catch (e) { say('#rmsg', e.message, true); }
  $('#rgo').disabled = false;
};
