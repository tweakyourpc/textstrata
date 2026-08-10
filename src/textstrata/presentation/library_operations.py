"""Operational browser behavior for the library page."""

from __future__ import annotations


def library_operations_script() -> str:
    """Return imports, trash, maintenance, About, and action routing."""
    return r'''
    const syncDialog = $("#sync-dialog");
    function renderImportJobs(jobs) {
      const target = $("#sync-status");
      if (!target) return;
      target.innerHTML = (jobs || []).map((job) => `<div class="job" data-job-row="${esc(job.id)}"><strong>#${esc(job.id)} ${esc(job.type)}</strong> <span>${esc(job.status)}</span><div class="meta">${esc(job.original_name || job.payload || "")}</div>${job.source_identity ? `<div class="meta">Source: ${esc(job.source_identity)}</div>` : ""}${job.error_message ? `<p class="danger-text">${esc(job.error_message)}</p>` : ""}<div class="actions">${["queued", "processing"].includes(job.status) ? `<button data-job-cancel="${esc(job.id)}">Stop</button>` : ""}${job.status !== "processing" ? `<button data-job-delete="${esc(job.id)}">Remove history</button>` : ""}${job.result_item_id ? `<a class="btn" href="/item/${encodeURIComponent(job.result_item_id)}">Open item</a><button data-job-purge="${esc(job.id)}">Move output to trash</button>` : ""}</div></div>`).join("") || '<p class="meta">No import jobs.</p>';
    }
    async function loadImportJobs() { const data = await api("/api/acquisition/queue"); renderImportJobs(data.jobs); }
    async function openImportHistory() { if (syncDialog && !syncDialog.open) syncDialog.showModal(); $("#sync-status").innerHTML = '<p class="meta">Loading...</p>'; try { await loadImportJobs(); } catch (error) { $("#sync-status").innerHTML = `<p class="danger-text">${esc(error.message)}</p>`; } }
    $("#queue-refresh")?.addEventListener("click", loadImportJobs);
    $("#queue-clear")?.addEventListener("click", async () => { if (await confirmed("/api/acquisition/queue/clear-completed", "POST", "Clear finished jobs", "This removes completed, failed, and cancelled history records. Resulting notes remain.")) { toast("Finished jobs cleared", "success"); await loadImportJobs(); } });
    $("#sync-status")?.addEventListener("click", async (event) => {
      const cancel = event.target.closest("[data-job-cancel]");
      const remove = event.target.closest("[data-job-delete]");
      const purge = event.target.closest("[data-job-purge]");
      if (!cancel && !remove && !purge) return;
      try {
        if (cancel) await confirmed(`/api/acquisition/queue/${cancel.dataset.jobCancel}/cancel`, "POST", "Stop import", "Stop this queued or processing import?");
        if (remove) await confirmed(`/api/acquisition/queue/${remove.dataset.jobDelete}`, "DELETE", "Remove history", "This removes only the history record. Resulting notes remain.");
        if (purge) await confirmed(`/api/acquisition/queue/${purge.dataset.jobPurge}/purge-output`, "POST", "Move output to trash", "Move the resulting note to Trash?");
        await loadImportJobs();
      } catch (error) { toast(error.message, "error"); }
    });

    const trashDialog = $("#trash-dialog");
    function renderTrash(items) {
      const target = $("#textstrata-trash");
      target.innerHTML = (items || []).map((item) => `<div class="trash-row"><div><code>${esc(item.item_id)}</code><div class="meta">${esc(item.deleted_at)}</div></div><div class="actions"><button data-trash-restore="${esc(item.trash_name)}">Restore</button><button class="btn btn-danger" data-trash-purge="${esc(item.trash_name)}">Delete forever</button></div></div>`).join("") || '<p class="meta">Trash is empty.</p>';
      $("#textstrata-trash-empty").disabled = !(items || []).length;
    }
    async function loadTrash() { renderTrash((await api("/api/textstrata/trash")).items); }
    async function openTrashDialog() { if (trashDialog && !trashDialog.open) trashDialog.showModal(); try { await loadTrash(); } catch (error) { $("#trash-status").textContent = error.message; } }
    $("#textstrata-trash")?.addEventListener("click", async (event) => {
      const restore = event.target.closest("[data-trash-restore]");
      const purge = event.target.closest("[data-trash-purge]");
      if (!restore && !purge) return;
      try {
        if (restore) { await api(`/api/textstrata/trash/${encodeURIComponent(restore.dataset.trashRestore)}/restore`, {method: "POST"}); toast("Note restored", "success"); }
        if (purge) { const result = await confirmed(`/api/textstrata/trash/${encodeURIComponent(purge.dataset.trashPurge)}`, "DELETE", "Delete forever", "This permanently deletes the selected trashed note."); if (result) toast("Note permanently deleted", "success"); }
        await loadTrash();
      } catch (error) { toast(error.message, "error"); }
    });
    $("#textstrata-trash-empty")?.addEventListener("click", async () => { const result = await confirmed("/api/textstrata/trash/empty", "POST", "Empty trash", "This permanently deletes every trashed note and cannot be undone."); if (result) { toast("Trash emptied", "success"); await loadTrash(); } });

    const maintenanceDialog = $("#maintenance-dialog");
    async function loadMaintenance() {
      const [settings, maintenance] = await Promise.all([api("/api/textstrata/settings"), api("/api/acquisition/maintenance/settings")]);
      $("#retain-default").checked = Boolean(maintenance.retain_original_uploads_default);
      $("#retention-mode").value = maintenance.retained_originals_purge_mode || "days";
      $("#retention-days").value = maintenance.retained_originals_days || 30;
      $("#retention-days").disabled = $("#retention-mode").value === "never";
      $("#path-readout").textContent = Object.entries({...settings.paths, ...maintenance.paths}).map(([key, value]) => `${key}: ${value}`).join("\n");
    }
    async function openMaintenanceDialog() { if (maintenanceDialog && !maintenanceDialog.open) maintenanceDialog.showModal(); try { await loadMaintenance(); } catch (error) { $("#maintenance-status").textContent = error.message; } }
    $("#retention-mode")?.addEventListener("change", () => { $("#retention-days").disabled = $("#retention-mode").value === "never"; });
    $("#retention-save")?.addEventListener("click", async () => {
      const payload = {retain_original_uploads_default: $("#retain-default").checked, retained_originals_purge_mode: $("#retention-mode").value, retained_originals_days: +$("#retention-days").value};
      const result = await confirmed("/api/acquisition/maintenance/settings", "POST", "Save retention", "The new policy may remove retained originals after their retention window.", payload);
      if (result) { toast("Retention settings saved", "success"); await loadMaintenance(); }
    });
    $("#restart-engine")?.addEventListener("click", async () => { const result = await confirmed("/api/acquisition/maintenance/restart", "POST", "Recheck ingestion engine", "Recheck installed acquisition capabilities now?"); if (result) toast("Ingestion capabilities rechecked", "success"); });
    $("#restart-server")?.addEventListener("click", async () => { const result = await confirmed("/api/textstrata/restart", "POST", "Restart server", "This interface will be briefly unavailable."); if (result) document.body.innerHTML = '<main style="padding:4rem;text-align:center"><h1>Server restarting...</h1></main>'; });
    $("#channel-purge")?.addEventListener("click", async () => {
      const handle = $("#purge-channel").value.trim();
      if (!handle) { $("#maintenance-status").textContent = "Enter a channel slug."; return; }
      const result = await confirmed(`/api/acquisition/channel/${encodeURIComponent(handle)}/purge`, "POST", "Purge channel", `Permanently remove ${handle} and its acquisition jobs?`);
      if (result) { $("#purge-channel").value = ""; toast("Channel purged", "success"); }
    });

    async function openAboutDialog(mode) {
      const dialog = $("#about-dialog");
      $("#about-title").textContent = mode === "system-info" ? "System info" : "About";
      $("#about-content").innerHTML = '<p class="meta">Loading...</p>';
      try {
        const info = await api("/api/textstrata/system-info");
        $("#about-content").innerHTML = `<p class="meta">TextStrata is a local knowledge workspace.</p><table><tr><td>Version</td><td>${esc(info.version)}</td></tr><tr><td>Platform</td><td>${esc(`${info.platform} ${info.platform_release} (${info.architecture})`)}</td></tr><tr><td>Install type</td><td>${esc(info.install_type)}</td></tr><tr><td>Process ID</td><td>${esc(info.pid)}</td></tr></table>`;
      } catch (error) { $("#about-content").innerHTML = `<p class="danger-text">${esc(error.message)}</p>`; }
      dialog?.showModal();
    }

    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      if (!button) return;
      const action = button.dataset.action;
      if (action === "new-note") location.href = "/new";
      else if (action === "settings") openSettings();
      else if (action === "focus-mode") document.body.classList.toggle("focus-mode");
      else if (action === "graph") location.href = "/graph";
      else if (action === "media") location.href = "/media";
      else if (action === "review-queue") openReviewDialog();
      else if (action === "trash") openTrashDialog();
      else if (action === "sync") openImportHistory();
      else if (action === "maintenance") openMaintenanceDialog();
      else if (action === "about" || action === "system-info") openAboutDialog(action);
      else if (action === "shortcuts") $("#shortcuts-help").hidden = !$("#shortcuts-help").hidden;
      else if (action === "docs") fetch("/item/system.docs.help-system", {method: "HEAD"}).then((response) => { location.href = response.ok ? "/item/system.docs.help-system" : "/item/system.operations-error-reference"; }).catch(() => { location.href = "/item/system.operations-error-reference"; });
    });

'''
