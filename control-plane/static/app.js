'use strict';
let authMode = 'oidc', csrf = '', token = '', records = [], revision = '', config = null, editing = false;
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => { const el = document.createElement(tag); el.textContent = text; if (cls) el.className = cls; return el; };
async function api(path, method = 'GET', data) {
  const headers = {'Content-Type': 'application/json'};
  if (authMode === 'token') headers.Authorization = 'Bearer ' + token;
  if (csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf;
  const response = await fetch('/api/' + path, {method, credentials: 'same-origin', headers, body: data === undefined ? undefined : JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'Request failed');
  return value;
}
function message(text) { $('message').textContent = text; }
async function refresh() {
  const [registry, status] = await Promise.all([api('servers'), api('status')]);
  records = registry.servers;
  $('gateway-url').textContent = registry.public_url;
  $('publish-state').textContent = status.published?.revision === status.draft_revision ? 'Published configuration matches this draft' : 'Draft configuration · unpublished changes';
  $('publish').disabled = !status.publish_enabled || !records.length;
  $('publish').title = status.publish_enabled ? '' : 'Publishing requires Kubernetes configuration. Download the config instead.';
  $('servers').replaceChildren();
  if (!records.length) $('servers').append(node('p', 'No servers registered. Add your first MCP server to get started.', 'empty'));
  for (const server of records) {
    const card = node('article', '', 'card');
    card.append(node('span', 'REGISTERED · DRAFT', 'eyebrow'), node('h2', server.name), node('p', '/mcp/' + server.id, 'endpoint'), node('p', server.upstream_url, 'muted small'), node('p', 'Audience: ' + server.audience, 'small'), node('p', 'Scopes: ' + server.required_scopes.join(', '), 'small'));
    const actions = node('div', '', 'actions');
    const edit = node('button', 'Edit policy'); edit.onclick = () => openEditor(server);
    const remove = node('button', 'Remove', 'danger'); remove.onclick = async () => {
      if (!confirm('Remove ' + server.name + ' from the draft? Publish afterward to remove its gateway route.')) return;
      try { await api('servers/' + server.id, 'DELETE'); await refresh(); message('Server removed from draft.'); } catch (err) { message(err.message); }
    };
    actions.append(edit, remove); card.append(actions); $('servers').append(card);
  }
  $('events').replaceChildren();
  for (const event of status.events) $('events').append(node('p', new Date(event.timestamp * 1000).toLocaleString() + ' · ' + event.action + ' · ' + (event.details.server || event.details.revision?.slice(0, 12) || '') + (event.details.actor ? ' · ' + event.details.actor : ''), 'small muted'));
  if (!status.events.length) $('events').append(node('p', 'No registry changes yet.', 'muted small'));
}
function openEditor(server) {
  editing = !!server; $('server-form').reset(); $('editor-error').textContent = '';
  $('editor-title').textContent = editing ? 'Edit server policy' : 'Register server';
  const form = $('server-form');
  form.elements.id.disabled = editing;
  if (server) for (const [key, value] of Object.entries(server)) {
    if (key === 'legacy_enabled') form.elements[key].checked = value;
    else form.elements[key].value = Array.isArray(value) ? value.join(key === 'allowed_origins' ? '\n' : ' ') : value;
  }
  $('editor').showModal();
}
$('login-form').onsubmit = async event => {
  event.preventDefault(); token = $('token').value;
  try { const identity = await api('session'); await refresh(); $('token').value = ''; showWorkspace(identity); message(''); }
  catch (err) { token = ''; message(err.message); }
};
function showWorkspace(identity) {
  csrf = identity.csrf || '';
  $('identity').textContent = identity.name;
  $('login').hidden = true; $('workspace').hidden = false; $('logout').hidden = false;
}
function clearSession() {
  token = ''; csrf = ''; records = []; config = null; revision = '';
  $('identity').textContent = ''; $('servers').replaceChildren(); $('events').replaceChildren();
  $('config').textContent = ''; $('server-form').reset(); $('workspace').hidden = true;
  $('login').hidden = false; $('logout').hidden = true;
}
$('logout').onclick = async () => {
  if (authMode === 'oidc') {
    try {
      const response = await fetch('/auth/logout', {method: 'POST', credentials: 'same-origin', headers: {'X-CSRF-Token': csrf}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      clearSession(); window.location.assign(result.logout_url);
    } catch (err) { message(err.message); }
  } else { clearSession(); message('Signed out.'); }
};
$('new-server').onclick = () => openEditor();
$('close-editor').onclick = () => $('editor').close();
$('close-preview').onclick = () => $('preview').close();
$('server-form').onsubmit = async event => {
  event.preventDefault(); const form = $('server-form'), data = {};
  for (const key of ['id', 'name', 'upstream_url', 'issuer', 'discovery_url', 'audience']) data[key] = form.elements[key].value.trim();
  data.required_scopes = form.elements.required_scopes.value.trim().split(/\s+/).filter(Boolean);
  data.allowed_origins = form.elements.allowed_origins.value.split('\n').map(v => v.trim()).filter(Boolean);
  data.legacy_enabled = form.elements.legacy_enabled.checked;
  try { await api('servers' + (editing ? '/' + data.id : ''), editing ? 'PUT' : 'POST', data); $('editor').close(); await refresh(); message('Draft saved. Review and publish to update the gateway.'); }
  catch (err) { $('editor-error').textContent = err.message; }
};
$('review').onclick = async () => {
  try { const snapshot = await api('preview'); revision = snapshot.revision; config = snapshot.config; $('config').textContent = JSON.stringify(config, null, 2); $('publish-error').textContent = ''; $('preview').showModal(); }
  catch (err) { message(err.message); }
};
$('download').onclick = () => { const link = document.createElement('a'), url = URL.createObjectURL(new Blob([JSON.stringify(config, null, 2)], {type: 'application/json'})); link.href = url; link.download = 'kong.json'; link.click(); URL.revokeObjectURL(url); };
$('publish').onclick = async () => {
  if (!confirm('Replace the gateway configuration with this reviewed draft and start a rollout?')) return;
  $('publish').disabled = true;
  try { await api('publish', 'POST', {revision}); $('preview').close(); await refresh(); message('Rollout requested. Check deployment readiness before considering this revision live.'); }
  catch (err) { $('publish-error').textContent = err.message; }
  finally { if (records.length) $('publish').disabled = false; }
};

async function bootstrap() {
  try {
    const response = await fetch('/auth/config');
    if (!response.ok) throw new Error('Unable to load login configuration');
    const settings = await response.json(); authMode = settings.mode;
    $('oidc-login').hidden = authMode !== 'oidc'; $('oidc-note').hidden = authMode !== 'oidc';
    $('login-form').hidden = authMode !== 'token'; $('token-note').hidden = authMode !== 'token';
    if (authMode === 'oidc') {
      const sessionResponse = await fetch('/api/session', {credentials: 'same-origin'});
      if (sessionResponse.status === 401) return;
      const identity = await sessionResponse.json();
      if (!sessionResponse.ok) throw new Error(identity.error);
      showWorkspace(identity); await refresh();
    }
  } catch (err) { message(err.message); }
}
bootstrap();
