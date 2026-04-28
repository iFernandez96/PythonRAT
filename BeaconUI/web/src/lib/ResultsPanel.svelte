<script>
  let { results, implant } = $props();

  let search        = $state('');
  let filterType    = $state('all');
  let loadingHist   = $state(false);
  let histPage      = $state(1);
  let histTotal     = $state(null);
  let histLoaded    = $state(false);
  let collapsed     = $state(new Set());

  // Auto-expand newest result when new results arrive
  let prevResultCount = $state(0);
  $effect(() => {
    const total = results.length;
    if (total > prevResultCount && results.length > 0) {
      const key = resultKey(results[0]);
      const s = new Set(collapsed);
      s.delete(key);
      collapsed = s;
    }
    prevResultCount = total;
  });

  // Derive unique types from results for filter dropdown
  const allTypes = $derived([...new Set(results.map(r => r.type))].sort());

  const filtered = $derived(results.filter(r => {
    const matchType   = filterType === 'all' || r.type === filterType;
    const matchSearch = !search.trim() ||
      (r.output ?? '').toLowerCase().includes(search.toLowerCase()) ||
      (r.command ?? '').toLowerCase().includes(search.toLowerCase()) ||
      (r.filepath ?? '').toLowerCase().includes(search.toLowerCase()) ||
      (r.error ?? '').toLowerCase().includes(search.toLowerCase()) ||
      String(r.task_id).includes(search) ||
      r.type.includes(search.toLowerCase());
    return matchType && matchSearch;
  }));

  function toggleCollapse(key) {
    const s = new Set(collapsed);
    if (s.has(key)) s.delete(key); else s.add(key);
    collapsed = s;
  }

  function resultKey(r) { return `${r.task_id}-${r.received_at}`; }
  function isCollapsed(r) { return collapsed.has(resultKey(r)); }

  // ── History loader ───────────────────────────────────────────────────────
  async function loadHistory() {
    loadingHist = true;
    try {
      const res  = await fetch(`/api/results/${implant.id}/history?page=${histPage}&per_page=50`);
      const data = await res.json();
      histTotal = data.total;
      // Merge: avoid duplicates by task_id
      const existing = new Set(results.map(r => r.task_id));
      const newItems = data.results.filter(r => !existing.has(r.task_id));
      // results is a prop — we emit it back via a new array
      // Since results is read-only from parent, append to display list locally
      histLoaded = true;
      _extraResults = [..._extraResults, ...newItems];
      histPage++;
    } catch { /* ignore */ }
    finally  { loadingHist = false; }
  }

  // Local extra results loaded from history (not pushed by SSE)
  let _extraResults = $state([]);
  const allResults  = $derived([...results, ..._extraResults]);
  const allFiltered = $derived(allResults.filter(r => {
    const matchType   = filterType === 'all' || r.type === filterType;
    const matchSearch = !search.trim() ||
      (r.output ?? '').toLowerCase().includes(search.toLowerCase()) ||
      (r.command ?? '').toLowerCase().includes(search.toLowerCase()) ||
      (r.filepath ?? '').toLowerCase().includes(search.toLowerCase()) ||
      (r.error ?? '').toLowerCase().includes(search.toLowerCase()) ||
      String(r.task_id).includes(search) ||
      r.type.includes(search.toLowerCase());
    return matchType && matchSearch;
  }));

  // ── File download helper ─────────────────────────────────────────────────
  function saveFile(r) {
    const bytes = Uint8Array.from(atob(r.data), c => c.charCodeAt(0));
    const url   = URL.createObjectURL(new Blob([bytes]));
    const a     = document.createElement('a');
    a.href      = url;
    a.download  = r.filepath?.split('/').pop() ?? `task_${r.task_id}`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function savePng(r) {
    const a    = document.createElement('a');
    a.href     = `data:image/png;base64,${r.data}`;
    a.download = `screenshot_${r.task_id}.png`;
    a.click();
  }

  function openScreenshot(r) {
    const w = window.open('', '_blank');
    w?.document.write(`<body style="margin:0;background:#000">
      <img src="data:image/png;base64,${r.data}" style="max-width:100%;display:block">
    </body>`);
  }

  function saveJpeg(r) {
    const a    = document.createElement('a');
    a.href     = `data:image/jpeg;base64,${r.data}`;
    a.download = `webcam_${r.task_id}.jpg`;
    a.click();
  }

  function openJpeg(r) {
    const w = window.open('', '_blank');
    w?.document.write(`<body style="margin:0;background:#000">
      <img src="data:image/jpeg;base64,${r.data}" style="max-width:100%;display:block">
    </body>`);
  }

  function saveWav(r) {
    const bytes = atob(r.data);
    const arr   = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
    const blob  = new Blob([arr], { type: 'audio/wav' });
    const url   = URL.createObjectURL(blob);
    const a     = document.createElement('a');
    a.href      = url;
    a.download  = `mic_${r.task_id}.wav`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── Export ───────────────────────────────────────────────────────────────
  function exportJson() {
    const blob = new Blob([JSON.stringify(allResults, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `results_${implant.id.slice(0,8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── Formatters ────────────────────────────────────────────────────────────
  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 60)   return `${s}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    return d.toLocaleTimeString();
  }

  const typeColor = {
    execute: 'badge-primary', upload: 'badge-secondary', download: 'badge-secondary',
    screenshot: 'badge-accent', webcam_snap: 'badge-accent', mic_record: 'badge-accent',
    keylog_start: 'badge-warning', keylog_dump: 'badge-warning',
    keylog_stop: 'badge-warning', clipboard: 'badge-info', persist: 'badge-ghost',
    unpersist: 'badge-ghost', privesc_enum: 'badge-warning', exec_python: 'badge-accent',
    self_update: 'badge-error', self_destruct: 'badge-error', set_interval: 'badge-neutral',
    sysinfo: 'badge-info', ps: 'badge-neutral', ls: 'badge-neutral',
    netstat: 'badge-neutral', kill_process: 'badge-warning',
  };
  function badge(t) { return typeColor[t] ?? 'badge-neutral'; }

  // ── Type accent colors ───────────────────────────────────────────────────
  const typeAccentMap = {
    execute: '#00e5b0', upload: '#6366f1', download: '#6366f1',
    screenshot: '#f97316', webcam_snap: '#f97316', mic_record: '#f97316',
    keylog_start: '#d29922', keylog_dump: '#d29922', keylog_stop: '#d29922',
    clipboard: '#58a6ff', persist: '#c9d1d9', unpersist: '#c9d1d9',
    privesc_enum: '#d29922', exec_python: '#f97316',
    self_update: '#f85149', self_destruct: '#f85149',
    set_interval: '#c9d1d9', sysinfo: '#58a6ff',
    ps: '#c9d1d9', ls: '#c9d1d9', netstat: '#c9d1d9', kill_process: '#d29922',
    shell_open: '#00e5b0', shell_send: '#00e5b0', shell_close: '#c9d1d9',
    socks_start: '#6366f1', socks_stop: '#6366f1',
  };
  function typeAccent(t) { return typeAccentMap[t] ?? '#c9d1d9'; }

  // ── Process table parser ──────────────────────────────────────────────────
  function parsePsText(output) {
    if (!output) return null;
    const lines = output.trim().split('\n');
    if (lines.length < 3) return null;
    // Header is first line; entries start after separator line
    return lines.slice(2).map(ln => {
      const parts = ln.split(/\s+/);
      return {
        user: parts[0] ?? '', pid: parts[1] ?? '',
        ppid: parts[2] ?? '', stat: parts[3] ?? '',
        cmd:  parts.slice(4).join(' '),
      };
    }).filter(p => p.pid);
  }
</script>

<div class="flex flex-col h-full bg-base-100">

  <!-- Top accent line -->
  <div class="h-px bg-gradient-to-r from-transparent via-primary/20 to-transparent shrink-0"></div>

  <!-- Toolbar -->
  <div class="flex items-center gap-2 px-3 py-2 border-b border-base-300 bg-base-200 shrink-0">
    <span class="text-xs font-semibold text-base-content/40 uppercase tracking-wider shrink-0">
      Results
    </span>
    {#if allResults.length > 0}
      <span class="badge badge-neutral badge-xs">{allFiltered.length}/{allResults.length}</span>
    {/if}

    <!-- Search -->
    <input
      class="input input-bordered input-xs flex-1 max-w-48 bg-base-100 font-mono"
      placeholder="Search…"
      bind:value={search}
    />

    <!-- Type filter -->
    <select class="select select-bordered select-xs bg-base-100 max-w-36"
            bind:value={filterType}>
      <option value="all">All types</option>
      {#each allTypes as t}
        <option value={t}>{t}</option>
      {/each}
    </select>

    <div class="flex-1"></div>

    <!-- History loader -->
    {#if histTotal === null || (histLoaded && allResults.length < histTotal)}
      <button class="btn btn-ghost btn-xs text-base-content/50"
              onclick={loadHistory} disabled={loadingHist}>
        {#if loadingHist}
          <span class="loading loading-spinner loading-xs"></span>
        {:else}
          ⟳ History
        {/if}
      </button>
    {:else if histLoaded}
      <span class="text-xs text-base-content/30">All {histTotal} loaded</span>
    {/if}

    <!-- Export -->
    {#if allResults.length > 0}
      <button class="btn btn-ghost btn-xs text-base-content/50" onclick={exportJson}
              title="Export all results as JSON">
        ↓ JSON
      </button>
    {/if}

    <!-- Clear (visual only) -->
    {#if search || filterType !== 'all'}
      <button class="btn btn-ghost btn-xs text-base-content/30"
              onclick={() => { search = ''; filterType = 'all'; }}>
        ✕ Clear
      </button>
    {/if}
  </div>

  <!-- Result list -->
  <div class="flex-1 overflow-y-auto">
    {#if allFiltered.length === 0}
      <div class="flex flex-col items-center justify-center h-full gap-3 select-none animate-fade-in">
        <div class="text-3xl opacity-10 text-primary">◈</div>
        <p class="text-sm text-base-content/20">
          {allResults.length === 0 ? 'No results yet' : 'No matches'}
        </p>
        {#if allResults.length === 0}
          <p class="text-xs text-base-content/15">Queue a task to see results</p>
        {/if}
      </div>
    {:else}
      <div class="divide-y divide-base-300">
        {#each allFiltered as r (resultKey(r))}
          {@const collapsed_ = isCollapsed(r)}

          <!-- Result card -->
          <div class="group border-b border-base-300/50">
            <!-- Header row (always visible) -->
            <button
              class="w-full flex items-center gap-2 px-3 py-1.5 bg-base-200/60
                     hover:bg-base-200 text-xs text-left cursor-pointer transition-colors duration-100"
              style="border-left: 3px solid {r.ok ? typeAccent(r.type) : '#f85149'}"
              onclick={() => toggleCollapse(r)}
            >
              <span class="font-mono text-base-content/40 w-8 shrink-0">#{r.task_id}</span>
              <span class="badge badge-xs {r.ok ? badge(r.type) : 'badge-error'}">{r.type}</span>

              <!-- Context snippet -->
              {#if r.command}
                <span class="font-mono text-base-content/60 truncate flex-1">{r.command}</span>
              {:else if r.filepath}
                <span class="font-mono text-base-content/60 truncate flex-1">{r.filepath}</span>
              {:else if !r.ok && r.error}
                <span class="text-error truncate flex-1">{r.error}</span>
              {:else}
                <span class="flex-1"></span>
              {/if}

              <time class="text-base-content/25 shrink-0 ml-2 font-mono"
                    title={r.received_at ? new Date(r.received_at).toLocaleString() : ''}
                    datetime={r.received_at}>
                {fmtTime(r.received_at)}
              </time>
              <span class="text-base-content/20 shrink-0">{collapsed_ ? '▸' : '▾'}</span>
            </button>

            <!-- Collapsible body -->
            {#if !collapsed_}
              <div class="px-3 py-2 bg-base-100">

                {#if !r.ok}
                  <!-- Error -->
                  <pre class="text-error text-xs font-mono whitespace-pre-wrap break-words">{r.error ?? 'unknown error'}</pre>

                {:else if r.format === 'png' && r.data}
                  <!-- Screenshot -->
                  <div class="flex flex-col gap-2">
                    <!-- svelte-ignore a11y_no_noninteractive_element_interactions a11y_click_events_have_key_events -->
                    <img
                      src="data:image/png;base64,{r.data}"
                      alt="screenshot"
                      class="rounded border border-base-300 cursor-zoom-in max-w-full"
                      style="max-height:360px;object-fit:contain;"
                      onclick={() => openScreenshot(r)}
                    />
                    <div class="flex gap-2">
                      <button class="btn btn-xs btn-ghost" onclick={() => openScreenshot(r)}>⬡ Full size</button>
                      <button class="btn btn-xs btn-ghost" onclick={() => savePng(r)}>↓ Save PNG</button>
                    </div>
                  </div>

                {:else if r.format === 'jpeg' && r.data}
                  <!-- Webcam snapshot -->
                  <div class="flex flex-col gap-2">
                    <!-- svelte-ignore a11y_no_noninteractive_element_interactions a11y_click_events_have_key_events -->
                    <img
                      src="data:image/jpeg;base64,{r.data}"
                      alt="webcam"
                      class="rounded border border-base-300 cursor-zoom-in max-w-full"
                      style="max-height:360px;object-fit:contain;"
                      onclick={() => openJpeg(r)}
                    />
                    <div class="flex gap-2">
                      <button class="btn btn-xs btn-ghost" onclick={() => openJpeg(r)}>⬡ Full size</button>
                      <button class="btn btn-xs btn-ghost" onclick={() => saveJpeg(r)}>↓ Save JPEG</button>
                    </div>
                  </div>

                {:else if r.format === 'wav' && r.data}
                  <!-- Mic recording -->
                  <div class="flex flex-col gap-2">
                    <audio controls class="w-full"
                           src="data:audio/wav;base64,{r.data}">
                    </audio>
                    {#if r.duration}
                      <span class="text-xs text-base-content/40">{r.duration}s recording</span>
                    {/if}
                    <button class="btn btn-xs btn-ghost self-start" onclick={() => saveWav(r)}>
                      ↓ Save WAV
                    </button>
                  </div>

                {:else if r.data && r.type === 'download'}
                  <!-- File download -->
                  <div class="flex items-center gap-3 py-1">
                    <span class="font-mono text-xs text-base-content/60 truncate">{r.filepath}</span>
                    <button class="btn btn-xs btn-success ml-auto shrink-0" onclick={() => saveFile(r)}>
                      ↓ Save file
                    </button>
                  </div>

                {:else if r.format === 'ls' && r.entries}
                  <!-- Directory listing table -->
                  <div class="overflow-x-auto">
                    <table class="table table-xs w-full font-mono">
                      <thead>
                        <tr class="text-base-content/40">
                          <th>Type</th><th>Permissions</th><th>Size</th><th>Name</th><th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {#each r.entries as entry (entry.path)}
                          <tr class="hover:bg-base-200/50">
                            <td class="text-base-content/50">
                              {entry.type === 'd' ? '📁' : entry.type === 'l' ? '↗' : '📄'}
                            </td>
                            <td class="text-base-content/50 text-xs">{entry.perms}</td>
                            <td class="text-base-content/50 text-xs">{entry.size}</td>
                            <td class="{entry.type==='d'?'text-info font-semibold':'text-base-content'}">
                              {entry.name}
                            </td>
                            <td>
                              {#if entry.type === 'f'}
                                <!-- Note: downloading requires the implant being online -->
                                <span class="text-base-content/20 text-xs">{entry.path}</span>
                              {/if}
                            </td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  </div>

                {:else if r.format === 'ps' && r.entries}
                  <!-- Process table with structured data (Python implant) -->
                  <div class="overflow-x-auto">
                    <table class="table table-xs w-full font-mono">
                      <thead>
                        <tr class="text-base-content/40">
                          <th>USER</th><th>PID</th><th>%CPU</th><th>%MEM</th>
                          <th>STAT</th><th class="w-48">COMMAND</th>
                        </tr>
                      </thead>
                      <tbody>
                        {#each r.entries as p (p.pid)}
                          <tr class="hover:bg-base-200/50">
                            <td class="text-base-content/60">{p.user}</td>
                            <td class="text-primary">{p.pid}</td>
                            <td>{p.cpu}</td>
                            <td>{p.mem}</td>
                            <td class="text-base-content/50">{p.stat}</td>
                            <td class="truncate max-w-[12rem] text-base-content/80" title={p.cmd}>{p.cmd}</td>
                          </tr>
                        {/each}
                      </tbody>
                    </table>
                  </div>

                {:else if (r.type === 'ps' || r.type === 'netstat') && r.output}
                  <!-- Plain-text process or netstat output (C implant) -->
                  {@const parsed = r.type === 'ps' ? parsePsText(r.output) : null}
                  {#if parsed && parsed.length > 0}
                    <div class="overflow-x-auto">
                      <table class="table table-xs w-full font-mono">
                        <thead>
                          <tr class="text-base-content/40">
                            <th>USER</th><th>PID</th><th>PPID</th><th>STAT</th><th>COMMAND</th>
                          </tr>
                        </thead>
                        <tbody>
                          {#each parsed as p (p.pid)}
                            <tr class="hover:bg-base-200/50">
                              <td class="text-base-content/60">{p.user}</td>
                              <td class="text-primary">{p.pid}</td>
                              <td class="text-base-content/40">{p.ppid}</td>
                              <td class="text-base-content/50">{p.stat}</td>
                              <td class="truncate max-w-[16rem]" title={p.cmd}>{p.cmd}</td>
                            </tr>
                          {/each}
                        </tbody>
                      </table>
                    </div>
                  {:else}
                    <pre class="text-xs font-mono whitespace-pre-wrap break-words text-base-content/80
                                max-h-72 overflow-y-auto">{r.output}</pre>
                  {/if}

                {:else if r.output !== undefined && r.output !== null}
                  <!-- Generic text output -->
                  <div class="relative group/code">
                    <button
                      class="absolute top-1 right-1 btn btn-xs btn-ghost opacity-0 group-hover/code:opacity-100
                             transition-opacity text-base-content/40"
                      onclick={() => navigator.clipboard?.writeText(r.output)}>
                      ⎘
                    </button>
                    <pre class="text-xs font-mono whitespace-pre-wrap break-words text-base-content/80
                                max-h-72 overflow-y-auto leading-relaxed pr-6">{r.output}</pre>
                  </div>

                {:else}
                  <p class="text-success text-xs font-mono">OK</p>
                {/if}

              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>

</div>
