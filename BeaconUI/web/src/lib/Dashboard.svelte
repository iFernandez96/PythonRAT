<script>
  import { onMount } from 'svelte';

  let { implants = [] } = $props();

  let auditEntries = $state([]);
  let loadingAudit = $state(true);

  const online  = $derived(implants.filter(i => i.status === 'online').length);
  const idle    = $derived(implants.filter(i => i.status === 'idle').length);
  const offline = $derived(implants.filter(i => i.status === 'offline').length);

  onMount(async () => {
    try {
      const res = await fetch('/api/audit');
      auditEntries = (await res.json()).slice(0, 12);
    } catch { auditEntries = []; }
    finally  { loadingAudit = false; }
  });

  function timeSince(iso) {
    if (!iso) return 'never';
    const diff = Date.now() - new Date(iso).getTime();
    const s = Math.floor(diff / 1000);
    if (s < 60)   return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s/60)}m ago`;
    if (s < 86400)return `${Math.floor(s/3600)}h ago`;
    return `${Math.floor(s/86400)}d ago`;
  }

  const actionBadge = {
    implant_registered: 'badge-accent',
    task_queued:        'badge-primary',
    result_received:    'badge-success',
    task_cancelled:     'badge-warning',
  };
</script>

<div class="p-6 h-full overflow-y-auto space-y-6">

  <!-- Stat cards -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
    <div class="stat bg-base-200 rounded-xl border border-base-300 py-4 px-5">
      <div class="stat-title text-xs text-base-content/40 uppercase tracking-wider">Total</div>
      <div class="stat-value text-3xl text-base-content">{implants.length}</div>
      <div class="stat-desc text-base-content/30">implants ever seen</div>
    </div>
    <div class="stat bg-base-200 rounded-xl border border-success/30 py-4 px-5">
      <div class="stat-title text-xs text-success/70 uppercase tracking-wider">Online</div>
      <div class="stat-value text-3xl text-success">{online}</div>
      <div class="stat-desc text-base-content/30">last check-in &lt; 60s</div>
    </div>
    <div class="stat bg-base-200 rounded-xl border border-warning/30 py-4 px-5">
      <div class="stat-title text-xs text-warning/70 uppercase tracking-wider">Idle</div>
      <div class="stat-value text-3xl text-warning">{idle}</div>
      <div class="stat-desc text-base-content/30">last check-in &lt; 5m</div>
    </div>
    <div class="stat bg-base-200 rounded-xl border border-error/30 py-4 px-5">
      <div class="stat-title text-xs text-error/70 uppercase tracking-wider">Offline</div>
      <div class="stat-value text-3xl text-error">{offline}</div>
      <div class="stat-desc text-base-content/30">last check-in &gt; 5m</div>
    </div>
  </div>

  <!-- Two columns: implant overview + recent events -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">

    <!-- Implant overview table -->
    <div class="card bg-base-200 border border-base-300">
      <div class="card-body p-4">
        <h2 class="card-title text-sm text-base-content/60 uppercase tracking-wider mb-2">
          Implants
        </h2>
        {#if implants.length === 0}
          <p class="text-sm text-base-content/30 py-4 text-center">Waiting for check-in…</p>
        {:else}
          <div class="overflow-x-auto">
            <table class="table table-xs w-full">
              <thead>
                <tr class="text-base-content/40">
                  <th>Status</th>
                  <th>Identity</th>
                  <th>OS</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {#each implants as i (i.id)}
                  {@const s = i.status ?? 'offline'}
                  <tr>
                    <td>
                      <span class="inline-block w-2 h-2 rounded-full
                        {s==='online'?'bg-success':s==='idle'?'bg-warning':'bg-error'}"></span>
                    </td>
                    <td class="font-mono text-xs">{i.user}@{i.hostname}</td>
                    <td class="text-xs text-base-content/50 max-w-[120px] truncate">{i.os}</td>
                    <td class="text-xs text-base-content/40">{timeSince(i.last_seen)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </div>
    </div>

    <!-- Recent audit events -->
    <div class="card bg-base-200 border border-base-300">
      <div class="card-body p-4">
        <h2 class="card-title text-sm text-base-content/60 uppercase tracking-wider mb-2">
          Recent Activity
        </h2>
        {#if loadingAudit}
          <div class="flex justify-center py-6">
            <span class="loading loading-spinner loading-sm"></span>
          </div>
        {:else if auditEntries.length === 0}
          <p class="text-sm text-base-content/30 py-4 text-center">No activity yet.</p>
        {:else}
          <div class="space-y-1.5 text-xs">
            {#each auditEntries as e (e.timestamp + e.action)}
              <div class="flex items-center gap-2 py-1 border-b border-base-300/50">
                <span class="badge badge-xs {actionBadge[e.action] ?? 'badge-neutral'} shrink-0">
                  {e.action.replace(/_/g,' ')}
                </span>
                <span class="font-mono text-base-content/50 truncate flex-1">
                  {e.implant_id?.slice(0,8)}…
                </span>
                <span class="text-base-content/30 shrink-0">
                  {timeSince(e.timestamp)}
                </span>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>

  </div>

  <!-- Getting started hint (shown when no implants) -->
  {#if implants.length === 0}
    <div class="card bg-base-200 border border-primary/20">
      <div class="card-body p-6">
        <h2 class="card-title text-primary text-sm">Getting Started</h2>
        <ol class="list-decimal list-inside space-y-2 text-sm text-base-content/60 mt-2">
          <li>Start the C2 server: <code class="kbd kbd-xs">python3 c2_beacon.py</code></li>
          <li>Run the Python implant: <code class="kbd kbd-xs">python3 implant_beacon.py</code></li>
          <li>Or build and run the C implant: <code class="kbd kbd-xs">make && ./implant_beacon</code></li>
          <li>Implants appear here when they check in.</li>
        </ol>
      </div>
    </div>
  {/if}

</div>
