'use strict';
let gatewayPublicUrl = '', authMode = 'oidc', csrf = '', token = '', records = [], revision = '', config = null, editing = false, publishEnabled = false;
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => { const el = document.createElement(tag); el.textContent = text; if (cls) el.className = cls; return el; };
async function api(path, method = 'GET', data) {
  const headers = {'Content-Type': 'application/json'};
  if (authMode === 'token') headers.Authorization = 'Bearer ' + token;
  if (csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf;
  const response = await fetch('/api/' + path, {method, credentials: 'same-origin', headers, body: data === undefined ? undefined : JSON.stringify(data)});
  const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Request failed'); return value;
}
function message(text) { $('message').textContent = text; }
function endpoint(resource) { return '/' + resource.type + '/' + resource.id; }
async function refresh() {
  const [registry, status] = await Promise.all([api('resources'), api('status')]);
  records = registry.resources; gatewayPublicUrl = registry.public_url; $('gateway-url').textContent = gatewayPublicUrl;
  $('resource-count').textContent = records.length; $('mcp-count').textContent = records.filter(r => r.type === 'mcp').length;
  $('agent-count').textContent = records.filter(r => r.type === 'a2a').length; $('exchange-count').textContent = records.filter(r => r.token_exchange?.enabled).length;
  $('publish-state').textContent = status.published?.revision === status.draft_revision ? 'Published configuration matches this draft' : 'Draft configuration · unpublished changes';
  publishEnabled = status.publish_enabled; $('publish').disabled = !publishEnabled;
  $('publish').title = publishEnabled ? '' : 'Publishing requires Kubernetes configuration. Download the config instead.';
  $('resources').replaceChildren();
  if (!records.length) $('resources').append(node('p', 'No connections registered. Add an MCP server or A2A agent to begin.', 'empty'));
  for (const resource of records) {
    const card = node('article', '', 'card'), kind = resource.type === 'a2a' ? 'A2A AGENT' : 'MCP SERVER';
    card.append(node('span', kind + ' · DRAFT', 'eyebrow'), node('h2', resource.name), node('p', endpoint(resource), 'endpoint'), node('p', resource.upstream_url, 'muted small'), node('p', 'Incoming scopes: ' + resource.required_scopes.join(', '), 'small'));
    card.append(node('p', resource.token_exchange?.enabled ? 'Token exchange → ' + resource.token_exchange.resource + ' · ' + resource.token_exchange.scopes.join(', ') : 'Token exchange disabled', resource.token_exchange?.enabled ? 'policy' : 'small muted'));
    const actions = node('div', '', 'actions'), edit = node('button', 'Edit connection'), remove = node('button', 'Remove', 'danger'); edit.onclick = () => openEditor(resource);
    remove.onclick = async () => { if (!confirm('Remove ' + resource.name + ' from the draft?')) return; try { await api('resources/' + resource.id, 'DELETE'); await refresh(); message('Connection removed from draft.'); } catch (err) { message(err.message); } };
    actions.append(edit, remove); card.append(actions); $('resources').append(card);
  }
  $('events').replaceChildren();
  for (const event of status.events) { const subject = event.details.resource || event.details.server || event.details.revision?.slice(0, 12) || ''; $('events').append(node('p', new Date(event.timestamp * 1000).toLocaleString() + ' · ' + event.action + ' · ' + subject + (event.details.actor ? ' · ' + event.details.actor : ''), 'small muted')); }
  if (!status.events.length) $('events').append(node('p', 'No registry changes yet.', 'muted small'));
}
function syncForm() {
  const form = $('resource-form'), type = form.elements.type.value, exchange = form.elements.exchange_enabled.checked;
  $('mcp-options').hidden = type !== 'mcp'; $('a2a-options').hidden = type !== 'a2a'; $('exchange-options').hidden = !exchange;
  if (!editing) form.elements.audience.value = gatewayPublicUrl + '/' + type + '/' + form.elements.id.value;
  for (const name of ['token_endpoint', 'exchange_resource', 'client_id', 'client_secret_file', 'exchange_scopes', 'timeout_ms']) form.elements[name].required = exchange;
}
function openEditor(resource) {
  editing = !!resource; const form = $('resource-form'); form.reset(); $('editor-error').textContent = ''; $('editor-title').textContent = editing ? 'Edit connection' : 'Add connection'; form.elements.id.disabled = editing; form.elements.type.disabled = editing;
  if (resource) {
    for (const [key, value] of Object.entries(resource)) { if (!form.elements[key] || key === 'token_exchange') continue; if (form.elements[key].type === 'checkbox') form.elements[key].checked = value; else form.elements[key].value = Array.isArray(value) ? value.join(key === 'allowed_origins' ? '\n' : ' ') : value; }
    const exchange = resource.token_exchange; form.elements.exchange_enabled.checked = !!exchange?.enabled;
    if (exchange?.enabled) { form.elements.token_endpoint.value = exchange.token_endpoint; form.elements.exchange_resource.value = exchange.resource; form.elements.client_id.value = exchange.client_id; form.elements.client_secret_file.value = exchange.client_secret_file; form.elements.exchange_scopes.value = exchange.scopes.join(' '); form.elements.timeout_ms.value = exchange.timeout_ms; }
  }
  syncForm(); $('editor').showModal();
}
function readForm() {
  const form = $('resource-form'), data = {};
  for (const key of ['id', 'type', 'name', 'upstream_url', 'issuer', 'discovery_url', 'audience']) data[key] = form.elements[key].value.trim();
  data.required_scopes = form.elements.required_scopes.value.trim().split(/\s+/).filter(Boolean); data.allowed_origins = form.elements.allowed_origins.value.split('\n').map(v => v.trim()).filter(Boolean);
  if (data.type === 'mcp') data.legacy_enabled = form.elements.legacy_enabled.checked;
  else { data.rest_enabled = form.elements.rest_enabled.checked; data.public_card = form.elements.public_card.checked; data.protocol_versions = form.elements.protocol_versions.value.trim().split(/\s+/).filter(Boolean); }
  if (form.elements.exchange_enabled.checked) data.token_exchange = {enabled: true, token_endpoint: form.elements.token_endpoint.value.trim(), resource: form.elements.exchange_resource.value.trim(), client_id: form.elements.client_id.value.trim(), client_secret_file: form.elements.client_secret_file.value.trim(), client_auth_method: 'client_secret_basic', scopes: form.elements.exchange_scopes.value.trim().split(/\s+/).filter(Boolean), timeout_ms: Number(form.elements.timeout_ms.value)};
  return data;
}
$('resource-form').elements.type.onchange = syncForm; $('resource-form').elements.id.oninput = syncForm; $('resource-form').elements.exchange_enabled.onchange = syncForm;
$('login-form').onsubmit = async event => { event.preventDefault(); token = $('token').value; try { const identity = await api('session'); await refresh(); $('token').value = ''; showWorkspace(identity); message(''); } catch (err) { token = ''; message(err.message); } };
function showWorkspace(identity) { csrf = identity.csrf || ''; $('identity').textContent = identity.name; $('login').hidden = true; $('workspace').hidden = false; $('logout').hidden = false; }
function clearSession() { token = ''; csrf = ''; records = []; config = null; revision = ''; $('identity').textContent = ''; $('resources').replaceChildren(); $('events').replaceChildren(); $('config').textContent = ''; $('resource-form').reset(); $('workspace').hidden = true; $('login').hidden = false; $('logout').hidden = true; }
$('logout').onclick = async () => { if (authMode === 'oidc') { try { const response = await fetch('/auth/logout', {method: 'POST', credentials: 'same-origin', headers: {'X-CSRF-Token': csrf}}); const result = await response.json(); if (!response.ok) throw new Error(result.error); clearSession(); window.location.assign(result.logout_url); } catch (err) { message(err.message); } } else { clearSession(); message('Signed out.'); } };
$('new-resource').onclick = () => openEditor(); $('close-editor').onclick = () => $('editor').close(); $('close-preview').onclick = () => $('preview').close();
$('resource-form').onsubmit = async event => { event.preventDefault(); const data = readForm(); try { await api('resources' + (editing ? '/' + data.id : ''), editing ? 'PUT' : 'POST', data); $('editor').close(); await refresh(); message('Draft saved. Review and publish to update the gateway.'); } catch (err) { $('editor-error').textContent = err.message; } };
$('review').onclick = async () => { try { const snapshot = await api('preview'); revision = snapshot.revision; config = snapshot.config; $('config').textContent = JSON.stringify(config, null, 2); $('publish-error').textContent = ''; $('preview').showModal(); } catch (err) { message(err.message); } };
$('download').onclick = () => { const link = document.createElement('a'), objectUrl = URL.createObjectURL(new Blob([JSON.stringify(config, null, 2)], {type: 'application/json'})); link.href = objectUrl; link.download = 'kong.json'; link.click(); URL.revokeObjectURL(objectUrl); };
$('publish').onclick = async () => { if (!confirm('Replace the gateway configuration with this reviewed draft and start a rollout?')) return; $('publish').disabled = true; try { await api('publish', 'POST', {revision}); $('preview').close(); await refresh(); message('Rollout requested. Check deployment readiness before considering this revision live.'); } catch (err) { $('publish-error').textContent = err.message; } finally { $('publish').disabled = !publishEnabled; } };
async function bootstrap() { try { const response = await fetch('/auth/config'); if (!response.ok) throw new Error('Unable to load login configuration'); const settings = await response.json(); authMode = settings.mode; $('oidc-login').hidden = authMode !== 'oidc'; $('oidc-note').hidden = authMode !== 'oidc'; $('login-form').hidden = authMode !== 'token'; $('token-note').hidden = authMode !== 'token'; if (authMode === 'oidc') { const sessionResponse = await fetch('/api/session', {credentials: 'same-origin'}); if (sessionResponse.status === 401) return; const identity = await sessionResponse.json(); if (!sessionResponse.ok) throw new Error(identity.error); showWorkspace(identity); await refresh(); } } catch (err) { message(err.message); } }
bootstrap();
