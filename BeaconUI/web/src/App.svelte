<script>
  import { onMount } from 'svelte';
  import ImplantList  from './lib/ImplantList.svelte';
  import CommandPanel from './lib/CommandPanel.svelte';
  import ResultsPanel from './lib/ResultsPanel.svelte';
  import AuditLog     from './lib/AuditLog.svelte';
  import Dashboard    from './lib/Dashboard.svelte';
  import Login        from './lib/Login.svelte';

  let implants    = $state([]);
  let selected    = $state(null);
  let results     = $state([]);
  let toasts      = $state([]);
  let showAudit   = $state(false);
  let authed      = $state(false);
  let authChecked = $state(false);
  let connected   = $state(false);
  let username    = $state('');

  function addToast(msg, type = 'info') {
    const id = Date.now() + Math.random();
    toasts = [...toasts, { id, msg, type }];
    setTimeout(() => { toasts = toasts.filter(t => t.id !== id); }, 4500);
  }

  async function checkAuth() {
    try {
      const res = await fetch('/api/auth/status');
      if (res.ok) {
        const data = await res.json();
        authed   = data.authed;
        username = data.username ?? 'admin';
      }
    } catch { authed = false; }
    authChecked = true;
  }

  async function logout() {
    await fetch('/logout', { method: 'POST' });
    authed   = false;
    implants = [];
    selected = null;
    results  = [];
    if (esRef) { esRef.close(); esRef = null; }
    connected = false;
  }

  async function refreshImplants() {
    try {
      const res = await fetch('/api/implants');
      if (res.ok) {
        implants = await res.json();
        if (selected) selected = implants.find(i => i.id === selected.id) ?? null;
      }
    } catch { /* SSE handles reconnect */ }
  }

  async function fetchResults(implantId) {
    const res  = await fetch(`/api/results/${implantId}`);
    const data = await res.json();
    if (data.length) results = [...data, ...results];
  }

  function selectImplant(implant) {
    selected = implant;
    results  = [];
  }

  const counts = $derived(implants.reduce(
    (acc, i) => { acc[i.status ?? 'offline'] = (acc[i.status ?? 'offline'] ?? 0) + 1; return acc; },
    { online: 0, idle: 0, offline: 0 }
  ));

  let sidebarWidth = $state(256);
  let isResizing   = $state(false);

  function startResize(e) {
    isResizing = true;
    const startX = e.clientX;
    const startW = sidebarWidth;
    const onMove = (e) => {
      sidebarWidth = Math.max(160, Math.min(420, startW + (e.clientX - startX)));
    };
    const onUp = () => {
      isResizing = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  let esRef = null;

  function startSSE() {
    if (esRef) esRef.close();
    const es = new EventSource('/api/stream');
    esRef = es;

    es.onopen = () => { connected = true; };
    es.onerror = () => { connected = false; };

    es.addEventListener('implant_registered', (e) => {
      const imp = JSON.parse(e.data);
      implants = [
        ...implants.filter(i => i.id !== imp.id),
        { ...imp, pending_results: 0, pending_tasks: 0, notes: '', tags: [] },
      ];
      addToast(`New implant: ${imp.user}@${imp.hostname}`, 'success');
    });

    es.addEventListener('new_results', async (e) => {
      const data = JSON.parse(e.data);
      implants = implants.map(i =>
        i.id === data.implant_id ? { ...i, pending_results: data.count } : i
      );
      if (selected?.id === data.implant_id) {
        await fetchResults(data.implant_id);
        implants = implants.map(i =>
          i.id === data.implant_id ? { ...i, pending_results: 0 } : i
        );
      } else {
        addToast(`${data.count} result(s) from ${data.user}@${data.hostname}`, 'info');
      }
    });
  }

  onMount(async () => {
    await checkAuth();
    if (authed) {
      await refreshImplants();
      startSSE();
    }
    return () => { if (esRef) esRef.close(); };
  });

  async function onLogin() {
    authed = true;
    await refreshImplants();
    startSSE();
  }
</script>

<div data-theme="c2dark" class="flex flex-col h-screen bg-base-100 text-base-content">

  {#if !authChecked}
    <!-- Loading splash -->
    <div class="flex-1 flex items-center justify-center">
      <span class="loading loading-spinner loading-lg text-primary"></span>
    </div>

  {:else if !authed}
    <Login {onLogin} />

  {:else}
    <!-- ── Navbar ──────────────────────────────────────────────────────────── -->
    <nav class="navbar bg-base-300 shrink-0 px-4 min-h-[48px] h-12"
         style="border-bottom: 1px solid rgba(0,229,176,0.15)">
      <div class="flex-1 flex items-center gap-3">
        <span class="font-bold tracking-[0.2em] text-primary text-sm select-none"
              style="text-shadow: 0 0 12px rgba(0,229,176,0.4)">◈ RAT C2</span>
        <div class="flex gap-1">
          {#if counts.online  > 0}
            <span class="badge badge-success  badge-xs">{counts.online} online</span>
          {/if}
          {#if counts.idle    > 0}
            <span class="badge badge-warning  badge-xs">{counts.idle} idle</span>
          {/if}
          {#if counts.offline > 0}
            <span class="badge badge-error    badge-xs">{counts.offline} offline</span>
          {/if}
        </div>
      </div>
      <div class="flex-none flex items-center gap-2 text-xs">
        <!-- SSE connection indicator -->
        <div class="flex items-center gap-1.5" title={connected ? 'Stream connected' : 'Stream disconnected'}>
          <span class="w-1.5 h-1.5 rounded-full {connected ? 'bg-success animate-pulse' : 'bg-error'}"
                style={connected ? 'box-shadow: 0 0 6px #3fb950' : ''}></span>
          <span class="text-base-content/30 hidden sm:inline">{connected ? 'live' : 'offline'}</span>
        </div>
        <div class="divider divider-horizontal mx-0 h-4"></div>
        <span class="text-base-content/30">{implants.length} implant{implants.length !== 1 ? 's' : ''}</span>
        <div class="divider divider-horizontal mx-0 h-4"></div>
        <button class="btn btn-ghost btn-xs" onclick={() => showAudit = true}>Audit Log</button>
        <button class="btn btn-ghost btn-xs" onclick={refreshImplants} title="Refresh">↺</button>
        <div class="divider divider-horizontal mx-0 h-4"></div>
        <div class="dropdown dropdown-end">
          <button tabindex="0" class="btn btn-ghost btn-xs gap-1">
            <span class="text-primary">◉</span>
            <span class="font-mono">{username}</span>
          </button>
          <ul tabindex="0" class="dropdown-content menu menu-sm bg-base-300 rounded-box z-50 w-36 p-1 shadow border border-base-200 mt-1">
            <li><button onclick={logout} class="text-error hover:bg-error/10">Logout</button></li>
          </ul>
        </div>
      </div>
    </nav>

    <!-- ── Body ──────────────────────────────────────────────────────────────── -->
    <div class="flex flex-1 overflow-hidden" class:select-none={isResizing} class:cursor-col-resize={isResizing}>
      <div class="shrink-0 border-r border-base-300 overflow-y-auto bg-base-200 flex flex-col"
           style="width: {sidebarWidth}px">
        <ImplantList {implants} {selected} onSelect={selectImplant} />
      </div>
      <!-- Resize handle -->
      <div class="w-1 shrink-0 bg-base-300 hover:bg-primary/35 transition-colors cursor-col-resize
                  {isResizing ? 'bg-primary/50' : ''}"
           onmousedown={startResize}
           role="separator" aria-orientation="vertical">
      </div>
      <div class="flex flex-col flex-1 overflow-hidden min-w-0">
        {#if selected}
          <div class="flex flex-col flex-1 overflow-hidden">
            <div class="shrink-0 border-b border-base-300 bg-base-100">
              <CommandPanel implant={selected} onTaskQueued={refreshImplants} />
            </div>
            <div class="flex-1 overflow-hidden">
              <ResultsPanel {results} implant={selected} />
            </div>
          </div>
        {:else}
          <Dashboard {implants} onSelectImplant={selectImplant} />
        {/if}
      </div>
    </div>

    <!-- ── Toasts ─────────────────────────────────────────────────────────────── -->
    <div class="toast toast-end toast-bottom z-50 max-w-xs">
      {#each toasts as t (t.id)}
        <div class="alert alert-{t.type} shadow-lg text-xs py-2 px-4 gap-2">
          <span>{t.msg}</span>
        </div>
      {/each}
    </div>

    {#if showAudit}
      <AuditLog onClose={() => showAudit = false} />
    {/if}
  {/if}
</div>
