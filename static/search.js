
/* Page Recherche + tests des clés dans Paramètres */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  const post = (url, body) => {
    const csrf =
      $('meta[name="csrf-token"]')?.content ||
      $('input[name="csrf_token"]')?.value ||
      '';

    const headers = { 'Content-Type': 'application/json' };
    if (csrf) headers['X-CSRF-Token'] = csrf;

    return fetch(url, {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: JSON.stringify(body || {})
    });
  };

  const esc = s =>
    String(s ?? '').replace(/[&<>"]/g, c => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;'
    }[c]));

  const readJson = async response => {
    try {
      return await response.json();
    } catch (err) {
      console.error('Réponse serveur non JSON :', err);
      throw new Error(`Réponse invalide du serveur (${response.status})`);
    }
  };

  /* ---- Paramètres : tester une clé ---- */
  $$('.js-test').forEach(btn => {
    btn.addEventListener('click', async () => {
      const out = $(`.msg[data-for="${btn.dataset.what}"]`);
      if (!out) return;

      out.className = 'msg';
      out.textContent = 'Test en cours…';

      try {
        const r = await post('/api/settings/test', {
          what: btn.dataset.what
        });
        const d = await readJson(r);

        out.textContent = d.message || `Erreur HTTP ${r.status}`;
        out.className = 'msg ' + (r.ok && d.ok ? 'ok' : 'error');
      } catch (err) {
        console.error('Erreur test paramètres :', err);
        out.textContent = 'Erreur de connexion. Consultez la Console (F12).';
        out.className = 'msg error';
      }
    });
  });

  /* ---- Jeton API n8n : conservé si les anciens boutons existent ---- */
  $('#token-new')?.addEventListener('click', async () => {
    if (
      $('#token-revoke') &&
      !confirm('Remplacer le jeton actuel ? Les workflows n8n qui l’utilisent devront être mis à jour.')
    ) return;

    try {
      const r = await post('/api/settings/n8n_token');
      const d = await readJson(r);
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);

      const box = $('#token-box');
      if (!box) return;

      box.hidden = false;
      box.textContent = 'Copiez-le maintenant, il ne sera plus affiché : ' + (d.token || '');
    } catch (err) {
      console.error('Erreur jeton API :', err);
      alert('Impossible de créer le jeton API. Consultez la Console (F12).');
    }
  });

  $('#token-revoke')?.addEventListener('click', async () => {
    if (!confirm('Révoquer le jeton ? n8n ne pourra plus lire vos données.')) return;

    try {
      const r = await post('/api/settings/n8n_token/revoke');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      location.reload();
    } catch (err) {
      console.error('Erreur révocation jeton :', err);
      alert('Impossible de révoquer le jeton API.');
    }
  });

  const form = $('#search-form');
  if (!form) return;

  /* ---- Recherche ---- */
  let kind = $('.seg button.on')?.dataset.kind || 'offers';
  let token = null;

  const placeholders = {
    offers: 'Mot-clé, métier, ou lien de recherche Rekrute (ex. Data Analyst)',
    companies: 'ex. agence intelligence artificielle Marrakech'
  };

  const msg = (text, type = '') => {
    const element = $('#s-msg');
    if (!element) return;
    element.textContent = text;
    element.className = 'msg ' + type;
  };

  const setKind = selectedKind => {
    kind = selectedKind;

    $$('.seg button').forEach(button => {
      button.classList.toggle('on', button.dataset.kind === selectedKind);
    });

    $('#s-query').placeholder = placeholders[selectedKind];
    $('#results').hidden = true;
    msg('');

    const warning = $('#google-warn');
    if (warning) warning.hidden = selectedKind !== 'companies';
  };

  $$('.seg button').forEach(button => {
    button.addEventListener('click', () => setKind(button.dataset.kind));
  });

  setKind(kind);

  const count = () => $$('#r-list input:checked').length;

  const refreshAdd = () => {
    $('#r-add').textContent = `Ajouter la sélection (${count()})`;
    $('#r-add').disabled = !count();
  };

  form.addEventListener('submit', async event => {
    event.preventDefault();

    const go = $('#s-go');
    go.disabled = true;
    msg('Recherche en cours…');
    $('#results').hidden = true;

    try {
      const response = await post('/api/search', {
        kind,
        query: $('#s-query').value
      });

      const data = await readJson(response);

      if (!response.ok) {
        msg(data.error || `Recherche impossible (HTTP ${response.status}).`, 'error');
        console.error('Erreur API recherche :', response.status, data);
        return;
      }

      token = data.token;

      const items = Array.isArray(data.items) ? data.items : [];

      $('#r-title').textContent =
        `${items.length} nouveau(x) résultat(s)` +
        (data.known ? ` · ${data.known} déjà dans votre base` : '');

      $('#r-list').innerHTML = items.map(item => `
        <label class="card result">
          <input type="checkbox" value="${Number(item.id)}" checked>
          <span class="rt">
            <strong>${esc(item.title)}</strong>
            <span class="muted">${esc(item.sub)}</span>
            <span><span class="chip">${esc(item.chip)}</span></span>
            ${item.link
              ? `<a href="${esc(item.link)}" target="_blank" rel="noopener">Ouvrir ↗</a>`
              : ''}
          </span>
        </label>
      `).join('');

      $('#results').hidden = false;
      $('#r-all').checked = true;
      refreshAdd();

      msg(
        items.length
          ? ''
          : 'Aucun nouveau résultat : tout ce qui a été trouvé est déjà dans votre base.'
      );
    } catch (error) {
      console.error('Erreur /api/search :', error);
      msg('Erreur de connexion ou réponse invalide. Consultez la Console (F12).', 'error');
    } finally {
      go.disabled = false;
    }
  });

  $('#r-list').addEventListener('change', refreshAdd);

  $('#r-all').addEventListener('change', event => {
    $$('#r-list input').forEach(input => {
      input.checked = event.target.checked;
    });
    refreshAdd();
  });

  $('#r-add').addEventListener('click', async () => {
    const ids = $$('#r-list input:checked').map(input => Number(input.value));
    if (!ids.length) return;

    try {
      const response = await post('/api/search/add', { token, ids });
      const data = await readJson(response);

      if (!response.ok) {
        msg(data.error || `Ajout impossible (HTTP ${response.status}).`, 'error');
        return;
      }

      $('#results').hidden = true;
      msg(`${data.added} élément(s) ajouté(s). Les graphiques et les listes sont à jour.`, 'ok');
    } catch (error) {
      console.error('Erreur ajout résultats :', error);
      msg('Impossible d’ajouter les résultats. Consultez la Console (F12).', 'error');
    }
  });

  /* ---- Recherches enregistrées ---- */
  $('#s-save').addEventListener('click', async () => {
    const query = $('#s-query').value.trim();

    if (query.length < 2) {
      msg('Saisissez une recherche à enregistrer.', 'error');
      return;
    }

    try {
      const response = await post('/api/saved', { kind, query });
      const data = await response.json().catch(() => ({}));

      if (response.ok) {
        location.reload();
      } else {
        msg(data.error || `Enregistrement impossible (HTTP ${response.status}).`, 'error');
      }
    } catch (error) {
      console.error('Erreur enregistrement recherche :', error);
      msg('Impossible d’enregistrer la recherche. Consultez la Console (F12).', 'error');
    }
  });

  document.addEventListener('click', async event => {
    const row = event.target.closest('tr[data-id]');
    if (!row) return;

    const id = row.dataset.id;

    if (event.target.closest('.js-del')) {
      if (!confirm('Supprimer cette recherche enregistrée ?')) return;

      try {
        const response = await post(`/api/saved/${id}/delete`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        row.remove();
      } catch (error) {
        console.error('Erreur suppression recherche :', error);
        msg('Impossible de supprimer cette recherche.', 'error');
      }
    }

    if (event.target.closest('.js-run')) {
      const button = event.target.closest('.js-run');
      button.disabled = true;
      button.textContent = 'En cours…';

      try {
        const response = await post(`/api/saved/${id}/run`);
        const data = await readJson(response);

        msg(
          data.status === 'OK'
            ? `${data.added} nouveauté(s) ajoutée(s).`
            : (data.status || `Erreur HTTP ${response.status}`),
          data.status === 'OK' ? 'ok' : 'error'
        );

        setTimeout(() => location.reload(), 1200);
      } catch (error) {
        console.error('Erreur lancement recherche enregistrée :', error);
        msg('Impossible de lancer la recherche. Consultez la Console (F12).', 'error');
        button.disabled = false;
        button.textContent = 'Lancer';
      }
    }
  });

  document.addEventListener('change', async event => {
    const toggle = event.target.closest('.js-toggle');
    if (!toggle) return;

    try {
      const response = await post(`/api/saved/${toggle.closest('tr').dataset.id}/toggle`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (error) {
      console.error('Erreur modification recherche automatique :', error);
      toggle.checked = !toggle.checked;
      msg('Impossible de modifier cette recherche.', 'error');
    }
  });
})();
