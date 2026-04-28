<script>
  let { implants, selected, onSelect } = $props();
  let search = $state('');

  function shortId(id)  { return id?.slice(0, 8) ?? '?'; }
  function platformIcon(os) {
    if (!os) return '◉';
    const o = os.toLowerCase();
    if (o.includes('windows'))              return '🪟';
    if (o.includes('darwin') || o.includes('mac')) return '🍎';
    return '🐧';
  }
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
    const f = implants.filter(matchesSearch);
    return {
      online:  f.filter(i => (i.status ?? 'offline') === 'online'),
      idle:    f.filter(i => (i.status ?? 'offline') === 'idle'),
      offline: f.filter(i => (i.status ?? 'offline') === 'offline'),
    };
  });
</script>

<div class="flex flex-col h-full">
  <!-- Top accent line -->
  <div class="h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent shrink-0"></div>

  <!-- Search -->
  <div class="px-2 pt-2.5 pb-1.5 shrink-0">
    <div class="relative">
      <span class="absolute left-2.5 top-1/2 -translate-y-1/2 text-base-content/30 text-xs pointer-events-none">⌕</span>
      <input
        class="input input-bordered input-xs w-full bg-base-300 font-mono pl-6
               focus:border-primary/60 focus:ring-1 focus:ring-primary/20 transition-all"
        placeholder="Search implants…"
        bind:value={search}
      />
    </div>
  </div>

  <!-- Groups -->
  <div class="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
    {#each [
      { key: 'online',  label: 'Online',  color: '#3fb950',  pulse: true,  items: groups().online },
      { key: 'idle',    label: 'Idle',    color: '#d29922',  pulse: false, items: groups().idle   },
      { key: 'offline', label: 'Offline', color: '#f85149',  pulse: false, items: groups().offline },
    ] as group}
      {#if group.items.length > 0}
        <div class="flex items-center gap-2 mt-3 mb-1 px-1">
          <span class="w-1.5 h-1.5 rounded-full shrink-0 {group.pulse ? 'animate-pulse' : ''}"
                style="background:{group.color}; {group.pulse ? `box-shadow: 0 0 5px ${group.color}` : ''}">
          </span>
          <span class="text-xs text-base-content/35 uppercase tracking-[0.15em] font-semibold">
            {group.label}
          </span>
          <span class="ml-auto font-mono text-xs text-base-content/20">{group.items.length}</span>
        </div>

        {#each group.items as implant (implant.id)}
          {@const isSelected = selected?.id === implant.id}
          <button
            class="w-full text-left rounded-lg px-2.5 py-2 mb-0.5 transition-all duration-150
                   {isSelected
                     ? 'bg-primary/8 border border-primary/30'
                     : 'hover:bg-base-300/60 border border-transparent hover:border-base-content/8'}"
            style={isSelected ? 'box-shadow: inset 3px 0 0 #00e5b0, 0 0 16px rgba(0,229,176,0.06)' : ''}
            onclick={() => onSelect(implant)}
          >
            <div class="flex items-center gap-1.5 min-w-0">
              <span class="text-sm leading-none select-none shrink-0">{platformIcon(implant.os)}</span>
              <span class="text-xs font-mono font-semibold truncate flex-1
                           {isSelected ? 'text-primary' : 'text-base-content/85'}">
                {implant.user}@{implant.hostname}
              </span>
              {#if implant.pending_results > 0}
                <span class="badge badge-success badge-xs shrink-0 font-mono">{implant.pending_results}</span>
              {/if}
              {#if implant.pending_tasks > 0}
                <span class="badge badge-warning badge-xs shrink-0">{implant.pending_tasks}↑</span>
              {/if}
            </div>
            <div class="flex items-center mt-0.5 gap-1 min-w-0">
              <span class="text-xs text-base-content/30 truncate flex-1 leading-tight">{implant.os}</span>
              <span class="text-xs text-base-content/20 shrink-0 font-mono">{timeSince(implant.last_seen)}</span>
            </div>
            <div class="flex items-center gap-1 mt-0.5 flex-wrap">
              <span class="font-mono text-xs text-base-content/15">{shortId(implant.id)}</span>
              {#each (implant.tags ?? []).slice(0,3) as tag}
                <span class="badge badge-xs border border-base-content/15 bg-transparent text-base-content/35">{tag}</span>
              {/each}
            </div>
          </button>
        {/each}
      {/if}
    {/each}

    {#if implants.length === 0}
      <div class="flex flex-col items-center justify-center h-40 gap-2 select-none mt-4">
        <div class="text-3xl opacity-10 text-primary">◈</div>
        <p class="text-xs text-base-content/20 font-semibold tracking-wider">No implants</p>
        <p class="text-xs text-base-content/15">Waiting for check-in…</p>
      </div>
    {:else if groups().online.length === 0 && groups().idle.length === 0 && groups().offline.length === 0}
      <p class="text-xs text-base-content/20 text-center py-8">No matches for "{search}"</p>
    {/if}
  </div>
</div>
