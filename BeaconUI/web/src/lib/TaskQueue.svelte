<script>
  let { implant, onCancelled } = $props();

  let queue   = $state([]);
  let loading = $state(false);
  let error   = $state('');

  async function load() {
    loading = true; error = '';
    try {
      const res = await fetch(`/api/task/${implant.id}/queue`);
      queue = await res.json();
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }

  async function cancel(taskId) {
    try {
      const res = await fetch(`/api/task/${implant.id}/${taskId}`, { method: 'DELETE' });
      const data = await res.json();
      if (data.ok) {
        queue = queue.filter(t => t.task_id !== taskId);
        onCancelled?.();
      } else {
        error = data.error ?? 'cancel failed';
      }
    } catch (e) {
      error = e.message;
    }
  }

  load();

  const typeColors = {
    execute: 'badge-primary', upload: 'badge-secondary', download: 'badge-secondary',
    screenshot: 'badge-accent', keylog_start: 'badge-warning', keylog_dump: 'badge-warning',
    keylog_stop: 'badge-warning', clipboard: 'badge-info', persist: 'badge-ghost',
    unpersist: 'badge-ghost', privesc_enum: 'badge-warning', exec_python: 'badge-accent',
    self_update: 'badge-error', self_destruct: 'badge-error', set_interval: 'badge-neutral',
    sysinfo: 'badge-info', ps: 'badge-neutral', ls: 'badge-neutral',
    netstat: 'badge-neutral', kill_process: 'badge-warning',
  };
</script>

<div class="card bg-base-200 border border-base-300">
  <div class="card-body p-3">
    <div class="flex items-center justify-between mb-2">
      <span class="text-xs font-semibold text-base-content/40 uppercase tracking-wider">
        Task Queue
      </span>
      <div class="flex items-center gap-2">
        {#if queue.length > 0}
          <span class="badge badge-warning badge-xs">{queue.length} pending</span>
        {/if}
        <button class="btn btn-ghost btn-xs" onclick={load} title="Refresh">↺</button>
      </div>
    </div>

    {#if loading}
      <div class="flex justify-center py-3">
        <span class="loading loading-spinner loading-xs"></span>
      </div>
    {:else if error}
      <p class="text-error text-xs">{error}</p>
    {:else if queue.length === 0}
      <p class="text-xs text-base-content/30 text-center py-3">No pending tasks</p>
    {:else}
      <div class="space-y-1">
        {#each queue as task (task.task_id)}
          <div class="flex items-center gap-2 bg-base-300/50 rounded px-2 py-1.5">
            <span class="font-mono text-base-content/40 text-xs w-6 shrink-0">#{task.task_id}</span>
            <span class="badge badge-xs {typeColors[task.type] ?? 'badge-neutral'}">{task.type}</span>
            {#if task.command}
              <span class="font-mono text-xs text-base-content/60 truncate flex-1">{task.command}</span>
            {:else if task.filepath}
              <span class="font-mono text-xs text-base-content/60 truncate flex-1">{task.filepath}</span>
            {:else}
              <span class="flex-1"></span>
            {/if}
            <button
              class="btn btn-ghost btn-xs text-error hover:bg-error/10 shrink-0"
              onclick={() => cancel(task.task_id)}
              title="Cancel task"
            >✕</button>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
