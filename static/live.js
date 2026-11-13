/* Tableau de bord : met à jour les chiffres et le graphique sans recharger la page */
(() => {
  const bars = document.getElementById('live-bars'); if (!bars) return;
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  let last = '';
  async function refresh() {
    if (document.hidden) return;
    try {
      const r = await fetch('/api/stats'); if (!r.ok) return; const d = await r.json();
      const sig = JSON.stringify([d.kpis, d.offers_by_sector]); if (sig === last) return;
      const first = last === ''; last = sig; if (first) return;   // le premier appel ne fait que mémoriser l'état affiché
      document.querySelectorAll('[data-k]').forEach(el => { const v = d.kpis[el.dataset.k]; if (v !== undefined && String(v) !== el.textContent) { el.textContent = v; el.classList.add('flash-up'); } });
      const {labels, values} = d.offers_by_sector, max = Math.max(1, ...values);
      bars.innerHTML = labels.map((l, i) => `<li><span class="lbl">${esc(l)}</span><span class="track"><span class="fill" style="width:${(values[i] / max * 100).toFixed(1)}%"></span></span><span class="num">${values[i]}</span></li>`).join('');
    } catch { /* réseau coupé : on réessaiera au prochain cycle */ }
  }
  refresh(); setInterval(refresh, 30000);
  document.addEventListener('visibilitychange', refresh);
})();
