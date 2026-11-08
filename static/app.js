const $ = (s, r = document) => r.querySelector(s);
const post = (url, body) => fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

function openModal(title, html) {
  $('#m-title').textContent = title; $('#m-body').innerHTML = html;
  $('#modal').hidden = false; $('#modal .close').focus();
}
function closeModal() { $('#modal').hidden = true; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
$('#modal')?.addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

document.addEventListener('click', async e => {
  const fav = e.target.closest('.fav');
  if (fav) {
    const r = await post('/api/favorite', {kind: fav.dataset.kind, ref: fav.dataset.ref});
    if (r.ok) fav.classList.toggle('on', (await r.json()).active);
    return;
  }
  const sum = e.target.closest('.js-summary');
  if (sum) {
    const card = sum.closest('[data-url]');
    openModal(card.dataset.poste, '<p class="loading">Lecture de l\'offre en cours…</p>');
    try {
      const r = await fetch('/api/summary?url=' + encodeURIComponent(card.dataset.url));
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'Résumé indisponible');
      const facts = (d.facts || []).map(f => `<span class="chip">${esc(f)}</span>`).join('');
      $('#m-body').innerHTML = `<ul>${d.points.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` +
        (facts ? `<div class="facts">${facts}</div>` : '') +
        `<p><a class="btn primary sm" href="${esc(card.dataset.url)}" target="_blank" rel="noopener">Voir l'offre complète</a></p>`;
    } catch (err) {
      $('#m-body').innerHTML = `<p>${esc(err.message)}</p>`;
    }
  }
});

document.addEventListener('change', async e => {
  const sel = e.target.closest('.js-status');
  if (!sel || !sel.value) return;
  const card = sel.closest('[data-url]');
  const r = await post('/api/application', {url: card.dataset.url, poste: card.dataset.poste, entreprise: card.dataset.entreprise || '', statut: sel.value});
  if (r.ok && sel.closest('.kanban')) location.reload();
});

document.addEventListener('focusout', e => {
  const t = e.target.closest('.js-notes');
  if (t) post('/api/application/notes', {url: t.closest('[data-url]').dataset.url, notes: t.value});
});
