/* Fenêtre d'envoi du CV, Kanban (glisser-déposer) et Studio CV */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const post = (u, b) => fetch(u, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  /* ---------- Envoi du CV ---------- */
  const am = $('#apply-modal');
  if (am) { 
    let ctx = null;
    const msg = (t, k = '') => { const m = $('#a-msg'); m.textContent = t; m.className = 'msg ' + k; };
    const close = () => { am.hidden = true; };
    document.addEventListener('click', async e => {
      const b = e.target.closest('.js-apply');
      if (b) {
        const card = b.closest('[data-url]');
        const company = b.dataset.kind === 'company';
        ctx = {url: card.dataset.url, poste: card.dataset.poste, entreprise: card.dataset.entreprise,
               kind: company ? 'company' : 'offer', ref: company ? card.dataset.ref : card.dataset.url};
        const name = document.body.dataset.uname, phone = document.body.dataset.uphone;
        $('#a-target').textContent = company ? `Candidature spontanée · ${ctx.entreprise}` : `${ctx.poste}${ctx.entreprise ? ' · ' + ctx.entreprise : ''}`;
        $('#a-subject').value = company ? `Candidature spontanée – ${name}` : `Candidature – ${ctx.poste} – ${name}`;
        $('#a-body').value = `Madame, Monsieur,\n\n` + (company
          ? `Je me permets de vous adresser ma candidature spontanée. Mon profil pourrait correspondre à vos besoins.`
          : `Je me permets de vous adresser ma candidature pour le poste de ${ctx.poste}.`) +
          ` Vous trouverez mon CV en pièce jointe.\n\nJe reste disponible pour un entretien à votre convenance.\n\nCordialement,\n${name}${phone ? '\n' + phone : ''}`;
        $('#a-to').value = ''; $('#a-found').textContent = ''; msg(''); $('#a-mailto').hidden = true;
        $('#a-mark').hidden = company;
        am.hidden = false; $('#a-to').focus();
        return;
      }
      if (e.target.closest('#apply-modal [data-close]') || e.target === am) close();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });

    $('#a-find').addEventListener('click', async () => {
      $('#a-found').textContent = 'Recherche en cours…';
      const r = await fetch(`/api/find_email?kind=${ctx.kind}&ref=${encodeURIComponent(ctx.ref)}`);
      const d = r.ok ? await r.json() : {emails: []};
      if (!d.emails.length) { $('#a-found').textContent = 'Aucun email public trouvé : saisissez-le à la main.'; return; }
      $('#a-to').value = d.emails[0];
      $('#a-found').textContent = d.emails.length > 1 ? 'Autres adresses : ' + d.emails.slice(1).join(', ') : 'Adresse trouvée sur le site : vérifiez-la avant d\'envoyer.';
    });

    $('#a-mark').addEventListener('click', async () => {
      await post('/api/application', {url: ctx.url, poste: ctx.poste, entreprise: ctx.entreprise, statut: 'Envoyé'});
      msg('Ajouté au suivi (colonne Envoyé).', 'ok');
    });

    $('#apply-form').addEventListener('submit', async e => {
      e.preventDefault();
      const btn = $('#a-send'); btn.disabled = true; msg('Envoi en cours…');
      const payload = {to: $('#a-to').value, subject: $('#a-subject').value, body: $('#a-body').value, ...ctx};
      try {
        const r = await post('/api/apply', payload); const d = await r.json();
        if (r.ok) { msg('CV envoyé. La candidature est dans la colonne « Envoyé ».', 'ok'); }
        else {
          msg(d.error || 'Échec de l\'envoi.', 'error');
          if (d.fallback) {
            const a = $('#a-mailto');
            a.href = `mailto:${encodeURIComponent(payload.to)}?subject=${encodeURIComponent(payload.subject)}&body=${encodeURIComponent(payload.body)}`;
            a.hidden = false;
          }
        }
      } catch { msg('Connexion impossible. Réessayez.', 'error'); }
      btn.disabled = false;
    });
  }

  /* ---------- Kanban ---------- */
  const board = $('#board');
  if (board) {
    let dragged = null;
    const recount = () => $$('.col', board).forEach(c => $('.count', c).textContent = $$('.card', c).length);
    board.addEventListener('dragstart', e => {
      dragged = e.target.closest('.card'); if (!dragged) return;
      dragged.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragged.dataset.url);
    });
    board.addEventListener('dragend', () => { dragged?.classList.remove('dragging'); $$('.col.over').forEach(c => c.classList.remove('over')); dragged = null; });
    board.addEventListener('dragover', e => { const c = e.target.closest('.col'); if (c && dragged) { e.preventDefault(); c.classList.add('over'); } });
    board.addEventListener('dragleave', e => { const c = e.target.closest('.col'); if (c && !c.contains(e.relatedTarget)) c.classList.remove('over'); });
    board.addEventListener('drop', async e => {
      const col = e.target.closest('.col'); if (!col || !dragged) return;
      e.preventDefault(); col.classList.remove('over');
      const card = dragged, status = col.dataset.status;
      if (card.closest('.col') === col) return;
      const from = card.parentElement;
      $('.drop', col).prepend(card); $('select.status', card).value = status; recount();
      const r = await post('/api/application', {url: card.dataset.url, poste: card.dataset.poste, entreprise: card.dataset.entreprise, statut: status});
      if (!r.ok) { from.prepend(card); recount(); }   // annule si le serveur refuse
    });
    // liste déroulante (mobile / clavier) : déplace aussi la carte
    board.addEventListener('change', e => {
      const sel = e.target.closest('.js-status'); if (!sel) return;
      const card = sel.closest('.card'), col = $(`.col[data-status="${sel.value}"]`, board);
      if (col) { $('.drop', col).prepend(card); recount(); }
    });
    board.addEventListener('click', async e => {
      const del = e.target.closest('.del'); if (!del || !confirm('Supprimer cette candidature du suivi ?')) return;
      const card = del.closest('.card'); const r = await post('/api/application/delete', {url: card.dataset.url});
      if (r.ok) { card.remove(); recount(); }
    });
    $('#add-app')?.addEventListener('submit', async e => {
      e.preventDefault(); const f = new FormData(e.target);
      const r = await post('/api/application/add', {poste: f.get('poste'), entreprise: f.get('entreprise'), statut: 'Envoyé'});
      if (r.ok) location.reload();
    });
  }

  /* ---------- Studio CV ---------- */
  const form = $('#cv-form');
  if (form) {
    const cv = JSON.parse($('#cv-data').textContent);
    cv.experiences ||= []; cv.education ||= [];
    const fields = $$('[data-f]', form);
    fields.forEach(i => i.value = cv[i.dataset.f] || '');

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
    const addExp = repeater('experiences', $('#exp-list'), [['role', 'Poste'], ['company', 'Entreprise'], ['period', 'Période (ex. 2023 - 2025)'], ['details', 'Missions (une par ligne)', true]]);
    const addEdu = repeater('education', $('#edu-list'), [['degree', 'Diplôme'], ['school', 'Établissement'], ['period', 'Période']]);
    $('#exp-add').onclick = addExp; $('#edu-add').onclick = addEdu;

    const render = () => {
      const meta = ['email', 'phone', 'city', 'links'].map(k => cv[k]).filter(Boolean).map(esc).join(' | ');
      const sec = (t, h) => h ? `<h4>${t}</h4>${h}` : '';
      $('#cv-preview').innerHTML =
        `<h3>${esc(cv.full_name) || 'Votre nom'}</h3>${cv.title ? `<div class="t">${esc(cv.title)}</div>` : ''}<div class="m">${meta}</div>` +
        sec('Profil', cv.summary ? `<div>${esc(cv.summary).replace(/\n/g, '<br>')}</div>` : '') +
        sec('Expérience professionnelle', cv.experiences.map(x => `<div class="it"><b>${esc(x.role)}${x.company ? ' - ' + esc(x.company) : ''}</b> <small>${x.period ? '(' + esc(x.period) + ')' : ''}</small>` +
          `<ul>${(x.details || '').split('\n').filter(l => l.trim()).map(l => `<li>${esc(l.replace(/^[-•\s]+/, ''))}</li>`).join('')}</ul></div>`).join('')) +
        sec('Formation', cv.education.map(x => `<div class="it"><b>${esc(x.degree)}${x.school ? ' - ' + esc(x.school) : ''}</b> <small>${x.period ? '(' + esc(x.period) + ')' : ''}</small></div>`).join('')) +
        sec('Compétences', cv.skills ? `<div>${esc(cv.skills)}</div>` : '') + sec('Langues', cv.languages ? `<div>${esc(cv.languages)}</div>` : '');
    };
    fields.forEach(i => i.addEventListener('input', () => { cv[i.dataset.f] = i.value; render(); }));
    render();

    const msg = (t, k) => { const m = $('#cv-msg'); m.textContent = t; m.className = 'msg ' + k; };
    const save = async () => { const r = await post('/api/cv', cv); msg(r.ok ? 'CV enregistré.' : 'Enregistrement impossible.', r.ok ? 'ok' : 'error'); return r.ok; };
    $('#cv-save').onclick = save;
    $('#cv-pdf').addEventListener('click', async e => { e.preventDefault(); if (await save()) location.href = e.currentTarget.href; });
  }
})();
