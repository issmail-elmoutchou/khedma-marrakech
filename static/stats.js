/* Page Statistiques : graphiques actualisés automatiquement (sans recharger la page) */
(() => {
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const $ = id => document.getElementById(id);
  const wine = css('--wine'), wine2 = css('--wine-2'), gold = css('--saffron'), muted = css('--muted'), line = css('--line');
  const palette = [wine, gold, wine2, '#6C8EE0', '#2F7D5B', '#B3392F', '#8C6BB1', '#4FA3A5', '#C9A227', '#7A6A73', '#A9B6D3'];
  Chart.defaults.font.family = 'Manrope, system-ui, sans-serif'; Chart.defaults.color = muted;
  const grid = {color: line}, noGrid = {display: false};
  const hbar = {indexAxis: 'y', scales: {x: {grid, ticks: {precision: 0}}, y: {grid: noGrid}}};
  const vbar = {scales: {y: {grid, beginAtZero: true, ticks: {precision: 0}}, x: {grid: noGrid}}};
  const lineO = {scales: {y: {grid, beginAtZero: true, ticks: {precision: 0}}, x: {grid: noGrid}}};
  const specs = {
    'c-offers': ['bar', 'offers_by_sector', hbar, gold], 'c-comp': ['doughnut', 'companies_by_sector', {}, null],
    'c-words': ['bar', 'top_words', hbar, wine2], 'c-rating': ['bar', 'ratings', vbar, gold],
    'c-match': ['bar', 'match', vbar, wine2], 'c-top': ['bar', 'top_companies', {indexAxis: 'y', scales: {x: {min: 0, max: 5, grid}, y: {grid: noGrid}}}, gold],
    'c-funnel': ['bar', 'funnel', vbar, wine], 'c-time': ['line', 'timeline', lineO, wine],
  };
  const charts = {};

  function upsert(id, data) {
    const [type, , opts, color] = specs[id], panel = $(id).closest('.panel');
    panel.querySelector('.nodata')?.remove();
    if (!data.values.some(v => v > 0)) { charts[id]?.destroy(); delete charts[id]; $(id).closest('.cbox').hidden = true; panel.insertAdjacentHTML('beforeend', '<p class="empty nodata">Pas encore de données.</p>'); return; }
    $(id).closest('.cbox').hidden = false;
    if (charts[id]) { const c = charts[id]; c.data.labels = data.labels; c.data.datasets[0].data = data.values; c.update(); return; }
    const multi = type === 'doughnut';
    charts[id] = new Chart($(id), {type, data: {labels: data.labels, datasets: [{data: data.values, backgroundColor: multi ? palette : color,
      borderColor: multi ? css('--card') : color, borderWidth: multi ? 2 : 0, borderRadius: multi ? 0 : 6, tension: .3}]},
      options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: multi, position: 'right', labels: {boxWidth: 12}}}, ...opts}});
  }

  let timer = null, last = '';
  const status = $('live-status'), dot = $('live-dot'), toggle = $('live-toggle');

  async function load(manual) {
    if (document.hidden && !manual) return;
    try {
      const r = await fetch('/api/stats'); if (!r.ok) throw 0; const d = await r.json();
      const k = d.kpis;
      $('kpis').innerHTML = [[k.offers, 'offres'], [k.companies, 'entreprises'], [k.avg_rating ?? '–', 'note moyenne Google'], [k.with_site, 'entreprises avec site web']]
        .map(([n, l]) => `<div class="kpi"><b>${n}</b>${l}</div>`).join('');
      Object.entries(specs).forEach(([id, s]) => upsert(id, d[s[1]]));
      const sig = JSON.stringify(d, (key, v) => key === 'generated' ? undefined : v);
      status.textContent = `Mis à jour à ${d.generated}` + (last && sig !== last ? ' · nouvelles données' : '');
      last = sig; dot.classList.remove('off');
    } catch { status.textContent = 'Connexion perdue : nouvelle tentative…'; dot.classList.add('off'); }
  }
  const start = () => { clearInterval(timer); timer = setInterval(load, 30000); dot.classList.remove('off'); toggle.textContent = 'Mettre en pause'; };
  const stop = () => { clearInterval(timer); timer = null; dot.classList.add('off'); toggle.textContent = 'Reprendre'; status.textContent += ' · en pause'; };
  toggle.addEventListener('click', () => timer ? stop() : (load(true), start()));
  $('live-now').addEventListener('click', () => load(true));
  document.addEventListener('visibilitychange', () => { if (!document.hidden && timer) load(); });
  load(true).then(start);
})();
