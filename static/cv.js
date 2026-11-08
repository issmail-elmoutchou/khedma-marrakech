/* Studio CV : formulaire + aperçu en direct (les mêmes règles d'analyse du texte que le PDF côté serveur) */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const form = $('#cv-editor'); if (!form) return;
  const post = (u, b) => fetch(u, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  /* ---------- analyse du texte (miroir de cv_tools.py) ---------- */
  const trimDash = s => s.replace(/^[\s\-\u2022*]+|[\s\-\u2022*]+$/g, '');
  const splitItems = text => {
    const out = [];
    String(text || '').replace(/\r/g, '').split('\n').forEach(line => {
      line = line.replace(/^[\s\-\u2022*\u00b7\u2013]+/, '').trim(); if (!line) return;
      line.split(/\s+[-\u2013\u2022]\s+/).forEach(p => { p = trimDash(p); if (p) out.push(p); });
    });
    return out;
  };
  const norm = s => String(s).normalize('NFD').toLowerCase().replace(/[^a-z]/g, '');
  const parseGroups = raw => {
    const text = String(raw || '').replace(/\r/g, '').trim(); if (!text) return [];
    let groups = [];
    if (text.includes('#')) {
      text.split(/#{1,6}/).forEach(chunk => {
        chunk = chunk.trim(); if (!chunk) return;
        const m = chunk.match(/^(.+?)(?:\n|\s+[-\u2013\u2022]\s+|$)/);
        const title = m ? m[1].replace(/^[\s:-]+|[\s:-]+$/g, '') : '';
        groups.push({title: title || null, items: splitItems(m ? chunk.slice(m[0].length) : chunk)});
      });
    } else if (/(^|\n)\s*[-\u2022*]/.test(text) || /\s[-\u2013\u2022]\s/.test(text) || text.split('\n')[0].includes(':')) {
      let cur = {title: null, items: []};
      text.split('\n').forEach(line => {
        const r = line.trim(); if (!r) return;
        if (!/^[-\u2022*\u2013]/.test(r) && r.endsWith(':') && r.length < 60) {
          if (cur.items.length || cur.title) groups.push(cur);
          cur = {title: r.replace(/:$/, '').trim(), items: []};
        } else cur.items.push(...splitItems(r));
      });
      groups.push(cur);
    } else groups = [{title: null, items: text.split(/[,;\n]/).map(x => x.trim()).filter(Boolean)}];
    return groups.map(g => (['competences', 'langues', 'skills', 'languages'].includes(norm(g.title || '')) ? {...g, title: null} : g))
                 .filter(g => g.items.length);
  };
  const splitLang = item => { const m = item.match(/^\s*([^:(\-\u2013]+?)\s*(?:[:\-\u2013]|\()\s*(.+?)\)?\s*$/); return m ? [m[1].trim(), m[2].trim()] : [item.trim(), '']; };
  const langLines = text => parseGroups(text).flatMap(g => g.items.map(i => { const [n, l] = splitLang(i); return l ? `${n} - ${l}` : n; }));

  /* ---------- données ---------- */
  const cv = JSON.parse($('#cv-data').textContent);
  cv.experiences ||= []; cv.education ||= []; cv.projects ||= []; cv.style = cv.style === 'classic' ? 'classic' : 'modern';
  $$('[data-f]', form).forEach(i => { i.value = cv[i.dataset.f] || ''; i.addEventListener('input', () => { cv[i.dataset.f] = i.value; render(); }); });
  $$('input[name="cv-style"]', form).forEach(r => { r.checked = r.value === cv.style; r.addEventListener('change', () => { cv.style = r.value; render(); }); });

  const repeater = (key, listEl, spec) => {
    const draw = () => {
      listEl.innerHTML = cv[key].map((it, n) => `<div class="rep" data-n="${n}"><button type="button" class="icon-btn rm" aria-label="Retirer">✕</button>` +
        spec.map(([k, label, area]) => `<label>${label}${area ? `<textarea data-k="${k}" rows="3">${esc(it[k])}</textarea>` : `<input data-k="${k}" value="${esc(it[k])}">`}</label>`).join('') + '</div>').join('');
    };
    draw();
    listEl.addEventListener('input', e => { const r = e.target.closest('.rep'); if (r) { cv[key][+r.dataset.n][e.target.dataset.k] = e.target.value; render(); } });
    listEl.addEventListener('click', e => { if (e.target.closest('.rm')) { cv[key].splice(+e.target.closest('.rep').dataset.n, 1); draw(); render(); } });
    return () => { cv[key].push(Object.fromEntries(spec.map(s => [s[0], '']))); draw(); render(); };
  };
  $('#exp-add').onclick = repeater('experiences', $('#exp-list'), [['role', 'Poste'], ['company', 'Entreprise'], ['period', 'Période (ex. 2025)'], ['details', 'Missions (une par ligne)', true]]);
  $('#edu-add').onclick = repeater('education', $('#edu-list'), [['degree', 'Diplôme'], ['school', 'Établissement'], ['period', 'Période']]);
  $('#prj-add').onclick = repeater('projects', $('#prj-list'), [['name', 'Nom du projet'], ['link', 'Lien (GitHub, démo)'], ['description', 'Description (une idée par ligne)', true]]);

  /* ---------- aperçu ---------- */
  const bullets = items => `<ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>`;
  const sec = (t, h) => h ? `<h4>${t}</h4>${h}` : '';
  function skillsHtml(mode) {
    return parseGroups(cv.skills).map(g => (g.title ? `<div class="gt">${esc(g.title)}</div>` : '') +
      (mode === 'list' ? bullets(g.items) : `<p>${g.items.map(esc).join(' • ')}</p>`)).join('');
  }
  function langsHtml(mode) {
    const l = langLines(cv.languages); if (!l.length) return '';
    return mode === 'list' ? bullets(l) : `<p>${l.map(esc).join(' &nbsp;|&nbsp; ')}</p>`;
  }
  const expHtml = () => cv.experiences.map(x => `<div class="it"><b>${esc(x.role)}${x.company ? ' - ' + esc(x.company) : ''}</b> <small>${x.period ? '(' + esc(x.period) + ')' : ''}</small>${bullets(splitItems(x.details))}</div>`).join('');
  const prjHtml = () => cv.projects.map(x => `<div class="it"><b>${esc(x.name)}</b> <small>${esc(x.link)}</small>${bullets(splitItems(x.description))}</div>`).join('');
  const eduHtml = () => cv.education.map(x => `<div class="it"><b>${esc(x.degree)}${x.school ? ' - ' + esc(x.school) : ''}</b> <small>${x.period ? '(' + esc(x.period) + ')' : ''}</small></div>`).join('');
  const profile = () => cv.summary ? `<p>${esc(cv.summary).replace(/\n/g, '<br>')}</p>` : '';

  function render() {
    const meta = ['email', 'phone', 'city', 'links'].map(k => cv[k]).filter(Boolean).map(esc).join(' | ');
    const head = `<header class="cvp-head"><h3>${esc(cv.full_name) || 'Votre nom'}</h3>${cv.title ? `<div class="t">${esc(cv.title)}</div>` : ''}<div class="m">${meta}</div></header>`;
    const box = $('#cv-preview'); box.className = 'cvp ' + cv.style;
    if (cv.style === 'modern') {
      box.innerHTML = head + `<div class="cvp-body"><aside>${sec('Compétences', skillsHtml('list'))}${sec('Langues', langsHtml('list'))}</aside>` +
        `<div class="cvp-main">${sec('Profil', profile())}${sec('Expérience professionnelle', expHtml())}${sec('Projets', prjHtml())}${sec('Formation', eduHtml())}</div></div>`;
    } else {
      box.innerHTML = head + sec('Profil', profile()) + sec('Formation', eduHtml()) + sec('Expérience professionnelle', expHtml()) +
        sec('Projets', prjHtml()) + sec('Compétences', skillsHtml('inline')) + sec('Langues', langsHtml('inline'));
    }
  }
  render();

  const msg = (t, k) => { const m = $('#cv-msg'); m.textContent = t; m.className = 'msg ' + k; };
  const save = async () => { const r = await post('/api/cv', cv); msg(r.ok ? 'CV enregistré.' : 'Enregistrement impossible.', r.ok ? 'ok' : 'error'); return r.ok; };
  $('#cv-save').onclick = save;
  $('#cv-pdf').addEventListener('click', async e => { e.preventDefault(); if (await save()) location.href = e.currentTarget.href; });
})();