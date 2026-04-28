<script>
  let { onClose } = $props();

  let entries  = $state([]);
  let loading  = $state(true);
  let filterBy = $state('');

  async function load() {
    loading = true;
    try {
      const res = await fetch('/api/audit');
      entries = await res.json();
    } catch {
      entries = [];
    } finally {
      loading = false;
    }
  }

  load();

  function formatTime(iso) {
    if (!iso) return '';
    return new Date(iso).toLocaleString();
  }

  function shortId(id) {
    return id?.slice(0, 8) ?? '?';
  }

  const actionColors = {
    task_queued:        'badge-primary',
    result_received:    'badge-success',
    implant_registered: 'badge-accent',
  };

  function badgeClass(action) {
    return actionColors[action] ?? 'badge-neutral';
  }

  let filtered = $derived(
    filterBy.trim()
      ? entries.filter(e =>
          e.action.includes(filterBy) ||
          e.implant_id?.includes(filterBy) ||
          JSON.stringify(e.details).includes(filterBy)
        )
      : entries
  );
</script>

<!-- Modal backdrop -->
<div class="modal modal-open animate-fade-in bg-black/60" role="dialog">
  <div class="modal-box w-11/12 max-w-4xl flex flex-col animate-slide-down" style="max-height: 80vh;">
    <!-- Header -->
    <div class="flex items-center justify-between mb-3 shrink-0">
      <h3 class="font-bold text-lg">Audit Log</h3>
      <div class="flex items-center gap-2">
        <input
          class="input input-bordered input-sm w-48"
          placeholder="Filter…"
          bind:value={filterBy}
        />
        <button class="btn btn-sm btn-ghost" onclick={load} title="Refresh">↺</button>
        <button class="btn btn-sm btn-ghost" onclick={onClose}>✕</button>
      </div>
    </div>

    <!-- Body -->
    <div class="overflow-y-auto flex-1 text-xs">
      {#if loading}
        <div class="flex justify-center py-8">
          <span class="loading loading-spinner loading-md"></span>
        </div>
      {:else if filtered.length === 0}
        <p class="text-center text-base-content/30 py-8">No audit entries found.</p>
      {:else}
        <table class="table table-xs table-zebra w-full">
          <thead>
            <tr class="text-base-content/50">
              <th class="w-36">Time</th>
              <th class="w-24">Implant</th>
              <th class="w-36">Action</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {#each filtered as e (e.timestamp + e.action + e.implant_id)}
              {@const aColor = { implant_registered: '#f97316', task_queued: '#00e5b0', result_received: '#3fb950', task_cancelled: '#d29922' }[e.action] ?? '#c9d1d9'}
              <tr style="border-left: 2px solid {aColor}">
                <td class="font-mono text-base-content/50">{formatTime(e.timestamp)}</td>
                <td class="font-mono">{shortId(e.implant_id)}…</td>
                <td><span class="badge badge-sm {badgeClass(e.action)}">{e.action}</span></td>
                <td class="font-mono text-base-content/70 break-all">
                  {JSON.stringify(e.details)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>

    <div class="modal-action shrink-0 mt-2">
      <span class="text-xs text-base-content/30 mr-auto">{filtered.length} entries</span>
      <button class="btn btn-sm" onclick={onClose}>Close</button>
    </div>
  </div>
  <!-- Click outside to close -->
  <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
  <div class="modal-backdrop" onclick={onClose}></div>
</div>
