<script>
  import { onMount } from 'svelte';
  import ImplantList  from './lib/ImplantList.svelte';
  import CommandPanel from './lib/CommandPanel.svelte';
  import ResultsPanel from './lib/ResultsPanel.svelte';
  import AuditLog     from './lib/AuditLog.svelte';
  import Dashboard    from './lib/Dashboard.svelte';

  let implants  = $state([]);
  let selected  = $state(null);
  let results   = $state([]);
  let toasts    = $state([]);
  let showAudit = $state(false);

  function addToast(msg, type = 'info') {
    const id = Date.now() + Math.random();
    toasts = [...toasts, { id, msg, type }];
    setTimeout(() => { toasts = toasts.filter(t => t.id !== id); }, 4500);
  }

  async function refreshImplants() {
    try {
      const res = await fetch('/api/implants');
      implants = await res.json();
      if (selected) selected = implants.find(i => i.id === selected.id) ?? null;
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

  onMount(() => {
    refreshImplants();

    const es = new EventSource('/api/stream');

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

    es.onerror = () => { /* browser auto-reconnects */ };
    return () => es.close();
  });
</script>

<div data-theme="c2dark" class="flex flex-col h-screen bg-base-100 text-base-content">

  <!-- ── Navbar ──────────────────────────────────────────────────────────── -->
  <nav class="navbar bg-base-300 border-b border-base-200 shrink-0 px-4 min-h-[48px] h-12">
    <div class="flex-1 flex items-center gap-3">
      <!-- Logo / title -->
      <span class="font-bold tracking-[0.2em] text-primary text-sm select-none">
        ◈ RAT C2
      </span>
      <!-- Live status pills -->
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
      <span class="text-base-content/30">{implants.length} implant{implants.length !== 1 ? 's' : ''}</span>
      <div class="divider divider-horizontal mx-0 h-4"></div>
      <button class="btn btn-ghost btn-xs" onclick={() => showAudit = true}>
        Audit Log
      </button>
      <button class="btn btn-ghost btn-xs" onclick={refreshImplants} title="Refresh">↺</button>
    </div>
  </nav>

  <!-- ── Body ────────────────────────────────────────────────────────────── -->
  <div class="flex flex-1 overflow-hidden">

    <!-- Sidebar -->
    <div class="w-64 shrink-0 border-r border-base-300 overflow-y-auto bg-base-200 flex flex-col">
      <ImplantList
        {implants}
        {selected}
        onSelect={selectImplant}
      />
    </div>

    <!-- Main content -->
    <div class="flex flex-col flex-1 overflow-hidden">
      {#if selected}
        <!-- Implant workspace: command panel (top, fixed height) + results (scrolls) -->
        <div class="flex flex-col flex-1 overflow-hidden">
          <!-- Command panel - fixed, scrollable tabs -->
          <div class="shrink-0 border-b border-base-300 bg-base-100">
            <CommandPanel implant={selected} onTaskQueued={refreshImplants} />
          </div>
          <!-- Results panel - takes remaining space -->
          <div class="flex-1 overflow-hidden">
            <ResultsPanel {results} implant={selected} />
          </div>
        </div>
      {:else}
        <!-- Dashboard when nothing selected -->
        <Dashboard {implants} />
      {/if}
    </div>

  </div>

  <!-- ── Toasts ───────────────────────────────────────────────────────────── -->
  <div class="toast toast-end toast-bottom z-50 max-w-xs">
    {#each toasts as t (t.id)}
      <div class="alert alert-{t.type} shadow-lg text-xs py-2 px-4 gap-2">
        <span>{t.msg}</span>
      </div>
    {/each}
  </div>

  <!-- ── Audit log modal ──────────────────────────────────────────────────── -->
  {#if showAudit}
    <AuditLog onClose={() => showAudit = false} />
  {/if}

</div>
