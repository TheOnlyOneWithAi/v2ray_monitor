const nodesEl = document.querySelector('#nodes');
const refreshButton = document.querySelector('#refresh');
const updatedEl = document.querySelector('#updated');

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function latency(value) {
  if (value == null) return '—';
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n)} ms` : '—';
}

function stateClass(status) {
  return status === 'online' ? 'online' : status === 'offline' ? 'offline' : 'unknown';
}

async function load() {
  refreshButton.disabled = true;
  try {
    const response = await fetch('/api/nodes', {cache: 'no-store', headers: {'Accept': 'application/json'}});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const list = Array.isArray(data.nodes) ? data.nodes : [];
    nodesEl.innerHTML = list.map(n => `
      <article class="node">
        <div><strong>${esc(n.name)}</strong><small>${esc(n.protocol)}</small></div>
        <div class="right"><b class="${stateClass(n.status)}">${n.status === 'online' ? '●' : '○'}</b><span>${latency(n.latency_ms)}</span></div>
      </article>`).join('') || '<p class="empty">No nodes.</p>';
    if (updatedEl) updatedEl.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    nodesEl.innerHTML = '<p class="error">Monitor temporarily unavailable.</p>';
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener('click', load);
load();
setInterval(load, 10000);
