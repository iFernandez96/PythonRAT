<script>
  let { onLogin } = $props();

  let username = $state('admin');
  let password = $state('');
  let error    = $state('');
  let loading  = $state(false);
  let showPw   = $state(false);

  async function submit(e) {
    e.preventDefault();
    loading = true; error = '';
    try {
      const res = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) { onLogin(); }
      else { const d = await res.json(); error = d.error || 'Login failed'; }
    } catch { error = 'Connection error'; }
    finally { loading = false; }
  }
</script>

<div class="min-h-screen bg-base-100 flex items-center justify-center p-4 relative overflow-hidden">
  <!-- Grid background -->
  <div class="absolute inset-0 pointer-events-none"
       style="background-image: linear-gradient(rgba(0,229,176,0.04) 1px, transparent 1px),
              linear-gradient(90deg, rgba(0,229,176,0.04) 1px, transparent 1px);
              background-size: 48px 48px; opacity: 0.8;">
  </div>
  <!-- Radial glow from center -->
  <div class="absolute inset-0 pointer-events-none"
       style="background: radial-gradient(ellipse 60% 50% at 50% 50%, rgba(0,229,176,0.05) 0%, transparent 70%);">
  </div>

  <div class="relative z-10 w-full max-w-sm animate-fade-in">
    <!-- Logo -->
    <div class="text-center mb-8 select-none">
      <div class="text-5xl mb-3 text-primary" style="text-shadow: 0 0 24px rgba(0,229,176,0.6)">◈</div>
      <h1 class="text-2xl font-bold tracking-[0.35em] text-primary uppercase inline-flex items-center gap-1"
          style="text-shadow: 0 0 20px rgba(0,229,176,0.4)">
        RAT C2<span class="animate-blink text-primary ml-0.5">_</span>
      </h1>
      <p class="text-xs text-base-content/25 mt-2 tracking-[0.3em] uppercase">Command &amp; Control</p>
    </div>

    <!-- Card -->
    <div class="card bg-base-200 border border-base-300"
         style="box-shadow: 0 0 0 1px rgba(0,229,176,0.08), 0 24px 48px rgba(0,0,0,0.5)">
      <div class="card-body p-6 gap-4">
        <form onsubmit={submit} class="flex flex-col gap-4">

          <label class="form-control">
            <div class="label pb-1">
              <span class="label-text text-xs text-base-content/40 tracking-widest uppercase font-semibold">Username</span>
            </div>
            <input type="text"
                   class="input input-bordered bg-base-300 font-mono text-sm
                          focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all"
                   bind:value={username} autocomplete="username" required />
          </label>

          <label class="form-control">
            <div class="label pb-1">
              <span class="label-text text-xs text-base-content/40 tracking-widest uppercase font-semibold">Password</span>
            </div>
            <div class="relative">
              <input type={showPw ? 'text' : 'password'}
                     class="input input-bordered bg-base-300 font-mono text-sm w-full pr-10
                            focus:border-primary focus:ring-1 focus:ring-primary/30 transition-all"
                     bind:value={password} autocomplete="current-password" required />
              <button type="button"
                      class="absolute right-3 top-1/2 -translate-y-1/2 text-base-content/30
                             hover:text-base-content/60 transition-colors text-sm select-none"
                      onclick={() => showPw = !showPw}>
                {showPw ? '🙈' : '👁'}
              </button>
            </div>
          </label>

          {#if error}
            <div class="alert alert-error py-2 text-sm animate-slide-down gap-2">
              <span class="text-xs">⚠ {error}</span>
            </div>
          {/if}

          <button type="submit"
                  class="btn btn-primary mt-1 tracking-[0.2em] font-bold"
                  style={loading ? '' : 'box-shadow: 0 0 20px rgba(0,229,176,0.25)'}
                  disabled={loading}>
            {#if loading}
              <span class="loading loading-spinner loading-sm"></span>
            {:else}
              AUTHENTICATE
            {/if}
          </button>

        </form>
      </div>
    </div>

    <p class="text-center text-xs text-base-content/15 mt-4 tracking-wider">
      Set credentials via ADMIN_PASSWORD env var
    </p>
  </div>
</div>
