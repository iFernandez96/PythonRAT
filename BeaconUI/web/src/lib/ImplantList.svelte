<script>
  let { implants, selected, onSelect } = $props();

  let search = $state('');

  function shortId(id) { return id?.slice(0, 8) ?? '?'; }

  function timeSince(iso) {
    if (!iso) return 'never';
    const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60)    return `${s}s`;
    if (s < 3600)  return `${Math.floor(s/60)}m`;
    if (s < 86400) return `${Math.floor(s/3600)}h`;
    return `${Math.floor(s/86400)}d`;
  }

  function matchesSearch(i) {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      i.user?.toLowerCase().includes(q) ||
      i.hostname?.toLowerCase().includes(q) ||
      i.os?.toLowerCase().includes(q) ||
      i.id?.toLowerCase().includes(q) ||
      (i.tags ?? []).some(t => t.toLowerCase().includes(q)) ||
      (i.notes ?? '').toLowerCase().includes(q)
    );
  }

  const groups = $derived(() => {
    const filtered = implants.filter(matchesSearch);
    return {
      online:  filtered.filter(i => (i.status ?? 'offline') === 'online'),
      idle:    filtered.filter(i => (i.status ?? 'offline') === 'idle'),
      offline: filtered.filter(i => (i.status ?? 'offline') === 'offline'),
    };
  });
</script>

<div class="flex flex-col h-full">

  <!-- Search box -->
  <div class="px-2 pt-2 pb-1 shrink-0">
    <input
      class="input input-bordered input-xs w-full bg-base-300 font-mono"
      placeholder="Search implants…"
      bind:value={search}
    />
  </div>

  <!-- Implant groups -->
  <div class="flex-1 overflow-y-auto px-2 pb-2">

    {#each [
      { key: 'online',  label: 'Online',  dot: 'bg-success',  items: groups().online  },
      { key: 'idle',    label: 'Idle',    dot: 'bg-warning',  items: groups().idle    },
      { key: 'offline', label: 'Offline', dot: 'bg-error',    items: groups().offline },
    ] as group}
      {#if group.items.length > 0}
        <!-- Group header -->
        <div class="flex items-center gap-2 mt-3 mb-1 px-1">
          <span class="w-1.5 h-1.5 rounded-full shrink-0 {group.dot}"></span>
          <span class="text-xs text-base-content/30 uppercase tracking-widest font-semibold">
            {group.label}
          </span>
          <span class="text-xs text-base-content/20">({group.items.length})</span>
        </div>

        <!-- Implant items -->
        {#each group.items as implant (implant.id)}
          <button
            class="w-full text-left rounded-lg px-2.5 py-2 mb-0.5 transition-all
                   {selected?.id === implant.id
                     ? 'bg-primary/10 border border-primary/30 text-base-content'
                     : 'hover:bg-base-300 border border-transparent text-base-content/80'}"
            onclick={() => onSelect(implant)}
          >
            <!-- Row 1: user@host + badges -->
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium truncate flex-1 {selected?.id === implant.id ? 'text-primary' : ''}">
                {implant.user}@{implant.hostname}
              </span>
              {#if implant.pending_results > 0}
                <span class="badge badge-success badge-xs shrink-0">{implant.pending_results}</span>
              {/if}
              {#if implant.pending_tasks > 0}
                <span class="badge badge-warning badge-xs shrink-0">{implant.pending_tasks}↑</span>
              {/if}
            </div>
            <!-- Row 2: OS + last seen -->
            <div class="flex items-center gap-1 mt-0.5">
              <span class="text-xs text-base-content/30 truncate flex-1">{implant.os}</span>
              <span class="text-xs text-base-content/20 shrink-0">{timeSince(implant.last_seen)}</span>
            </div>
            <!-- Row 3: ID + tags -->
            <div class="flex items-center gap-1 mt-0.5 flex-wrap">
              <span class="font-mono text-xs text-base-content/20">{shortId(implant.id)}…</span>
              {#each (implant.tags ?? []).slice(0,3) as tag}
                <span class="badge badge-ghost badge-xs text-base-content/40">{tag}</span>
              {/each}
            </div>
          </button>
        {/each}
      {/if}
    {/each}

    {#if implants.length === 0}
      <div class="flex flex-col items-center justify-center h-32 gap-2">
        <span class="text-2xl opacity-20">◉</span>
        <p class="text-xs text-base-content/25 text-center">Waiting for implants…</p>
      </div>
    {:else if groups().online.length === 0 && groups().idle.length === 0 && groups().offline.length === 0}
      <p class="text-xs text-base-content/25 text-center py-4">No matches</p>
    {/if}

  </div>
</div>
