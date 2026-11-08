/* Ajoute le jeton CSRF à toutes les requêtes POST : fetch() et formulaires. Chargé dans <head> avant les autres scripts. */
(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : {}));
      headers.set('X-CSRF-Token', token);
      init = {...init, headers};
    }
    return nativeFetch(input, init);
  };
  document.addEventListener('submit', e => {
    const f = e.target;
    if (f.tagName === 'FORM' && (f.method || '').toLowerCase() === 'post' && !f.querySelector('input[name="csrf_token"]')) {
      const i = document.createElement('input'); i.type = 'hidden'; i.name = 'csrf_token'; i.value = token; f.appendChild(i);
    }
  }, true);
})();
