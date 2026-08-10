"""Browser behavior for the focused New Note workspace."""

from __future__ import annotations

from .browser_common import browser_common_script

def new_note_page_script() -> str:
    return r"""  <script>
  (() => {
""" + browser_common_script() + r"""

    fetch("/whoami", {cache: "no-store"}).then((response) => response.ok ? response.json() : null).then((identity) => {
      if (!identity?.version) return;
      document.querySelectorAll("[data-version-badge]").forEach((node) => { node.textContent = identity.version; });
    }).catch(() => {});

    function setStatus(message, type = "") {
      const status = $("#ingest-status");
      status.textContent = message;
      status.dataset.type = type;
    }

    function toast(message, type = "info", duration = 3200) {
      const container = $("#toast-container");
      const element = document.createElement("div");
      element.className = `toast toast-${type}`;
      element.textContent = String(message);
      container.appendChild(element);
      setTimeout(() => element.remove(), duration);
    }

    const tabs = [...document.querySelectorAll('[role="tab"][data-source]')];
    const panels = [...document.querySelectorAll('[role="tabpanel"][data-source-panel]')];
    const submitButton = $("#ingest-submit");
    const acquisitionNotes = $("#acquire-notes");
    const acquisitionNotesHint = $("#acquire-notes-hint");
    let activeSource = "url";
    let droppedFiles = [];

    const sourceConfig = {
      url: {label: "Import link", focus: "#ingest-url"},
      file: {label: "Import files", focus: "#browse-files"},
      text: {label: "Publish note", focus: "#ingest-content"},
    };

    function activateSource(source, {focus = true} = {}) {
      if (!sourceConfig[source]) return;
      activeSource = source;
      tabs.forEach((tab) => {
        const selected = tab.dataset.source === source;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
      });
      panels.forEach((panel) => { panel.hidden = panel.dataset.sourcePanel !== source; });
      submitButton.textContent = sourceConfig[source].label;
      acquisitionNotes.disabled = source === "text";
      acquisitionNotesHint.textContent = source === "text"
        ? "Acquisition notes apply to imported links and files."
        : "Stored with the imported source for future context.";
      setStatus("");
      history.replaceState(null, "", `#${source}`);
      if (focus) $(sourceConfig[source].focus)?.focus();
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateSource(tab.dataset.source));
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        let next = index;
        if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        activateSource(tabs[next].dataset.source);
        tabs[next].focus();
      });
    });

    $("#ingest-url").addEventListener("input", (event) => {
      const value = event.target.value.trim();
      $("#detect-hint").textContent = !value ? "" : value.startsWith("@")
        ? "Detected: YouTube channel"
        : /youtube[.]com|youtu[.]be/i.test(value) ? "Detected: YouTube source" : "Detected: web page";
    });

    const fileInput = $("#ingest-file");
    const dropZone = $("#drop-zone");
    function selectedFiles() { return droppedFiles.length ? droppedFiles : [...fileInput.files]; }
    function renderSelectedFiles() {
      const files = selectedFiles();
      const target = $("#selected-files");
      target.innerHTML = files.length
        ? `<strong>${files.length} file${files.length === 1 ? "" : "s"} ready</strong><ul>${files.map((file) => `<li>${esc(file.name)} <span>${Math.max(1, Math.ceil(file.size / 1024))} KiB</span></li>`).join("")}</ul>`
        : "No files selected.";
      $("#title-hint").textContent = files.length > 1
        ? "Each filename will be used; the optional title is ignored for multi-file imports."
        : "Used only when the source has no explicit title.";
    }
    $("#browse-files").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => { droppedFiles = []; renderSelectedFiles(); });
    ["dragenter", "dragover"].forEach((name) => dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.add("is-dragging");
    }));
    ["dragleave", "drop"].forEach((name) => dropZone.addEventListener(name, (event) => {
      event.preventDefault();
      dropZone.classList.remove("is-dragging");
    }));
    dropZone.addEventListener("drop", (event) => {
      droppedFiles = [...(event.dataTransfer?.files || [])];
      renderSelectedFiles();
      setStatus(`${droppedFiles.length} file${droppedFiles.length === 1 ? "" : "s"} ready to import.`);
    });

    const ingestContent = $("#ingest-content");
    async function embedImages(files) {
      for (const file of files) {
        if (!String(file.type || "").startsWith("image/")) continue;
        const data = new FormData();
        data.append("asset", file, file.name || "image.png");
        const asset = await api("/api/asset/upload", {method: "POST", body: data});
        const caption = (file.name || "image").replace(/[.][^.]+$/, "").replace(/[-_]/g, " ");
        ingestContent.setRangeText(`![${caption}](${asset.url})`, ingestContent.selectionStart, ingestContent.selectionStart, "end");
        toast("Image uploaded", "success");
      }
    }
    ingestContent.addEventListener("paste", (event) => {
      const files = [...(event.clipboardData?.items || [])].filter((item) => item.type.startsWith("image/")).map((item) => item.getAsFile()).filter(Boolean);
      if (!files.length) return;
      event.preventDefault();
      embedImages(files).catch((error) => { setStatus(error.message, "error"); toast(error.message, "error"); });
    });
    ingestContent.addEventListener("drop", (event) => {
      const files = [...(event.dataTransfer?.files || [])].filter((file) => file.type.startsWith("image/"));
      if (!files.length) return;
      event.preventDefault();
      embedImages(files).catch((error) => { setStatus(error.message, "error"); toast(error.message, "error"); });
    });

    function commonAcquisitionForm({includeTitle = true} = {}) {
      const data = new FormData();
      const title = $("#ingest-title").value.trim();
      const notes = acquisitionNotes.value.trim();
      if ($("#keep-original").checked) data.append("keep_original", "true");
      data.append("ocr_mode", $("#ocr-mode").value);
      if (includeTitle && title) data.append("title", title);
      if (notes) data.append("notes", notes);
      return data;
    }

    async function submitKnowledge() {
      submitButton.disabled = true;
      setStatus("Working...");
      try {
        if (activeSource === "url") {
          const url = $("#ingest-url").value.trim();
          if (!url) throw new Error("Enter a web link or YouTube channel.");
          const data = commonAcquisitionForm();
          data.append("url", url);
          const result = await api("/api/acquisition/ingest", {method: "POST", body: data});
          $("#ingest-url").value = "";
          $("#detect-hint").textContent = "";
          if (result.deduplicated) {
            setStatus(result.result_item_id ? "Already imported." : "Already in the import queue.", "success");
            if (result.result_item_id) {
              const link = document.createElement("a");
              link.href = `/item/${encodeURIComponent(result.result_item_id)}`;
              link.textContent = "Open item";
              $("#ingest-status").append(" ", link);
            }
          } else setStatus("Import queued. Progress appears below.", "success");
        } else if (activeSource === "file") {
          const files = selectedFiles();
          if (!files.length) throw new Error("Choose at least one file.");
          for (const file of files) {
            if (file.size > 64 * 1024 * 1024) throw new Error(`${file.name} exceeds the 64 MiB limit.`);
            const data = commonAcquisitionForm({includeTitle: files.length === 1});
            data.append("file", file, file.name);
            await api("/api/acquisition/ingest", {method: "POST", body: data});
          }
          fileInput.value = "";
          droppedFiles = [];
          renderSelectedFiles();
          setStatus(`${files.length} import${files.length === 1 ? "" : "s"} queued. Progress appears below.`, "success");
        } else {
          const content = ingestContent.value;
          const title = $("#ingest-title").value.trim();
          if (!content.trim()) throw new Error("Enter Markdown or text for the note.");
          const result = await api("/api/ingest", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({content, filename: title || "web-ingest"}),
          });
          setStatus(`Published ${result.item_id}.`, "success");
          location.assign(`/item/${encodeURIComponent(result.item_id)}`);
          return;
        }
        toast($("#ingest-status").textContent, "success");
        await refreshQueue();
      } catch (error) {
        setStatus(error.message, "error");
        toast(error.message, "error", 5000);
      } finally {
        submitButton.disabled = false;
      }
    }
    $("#ingest-form").addEventListener("submit", (event) => { event.preventDefault(); submitKnowledge(); });

    function renderQueue(jobs) {
      const target = $("#ingest-queue");
      const visible = (jobs || []).slice(0, 5);
      target.innerHTML = visible.map((job) => `<div class="job-line"><div><strong>#${esc(job.id)} ${esc(job.type)}</strong><span class="job-status" data-status="${esc(job.status)}">${esc(job.status)}</span></div><p>${esc(job.original_name || job.payload || "")}</p>${job.source_identity ? `<small class="quiet">${esc(job.source_identity)}</small>` : ""}${job.result_item_id ? `<a href="/item/${encodeURIComponent(job.result_item_id)}">Open imported note</a>` : ""}</div>`).join("") || '<p class="quiet">No recent imports.</p>';
    }
    async function refreshQueue() {
      try { renderQueue((await api("/api/acquisition/queue")).jobs); }
      catch (error) { $("#queue-status").textContent = error.message; }
    }
    $("#queue-refresh").addEventListener("click", refreshQueue);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) location.href = "/";
    });

    const requestedSource = location.hash.slice(1);
    activateSource(sourceConfig[requestedSource] ? requestedSource : "url", {focus: false});
    renderSelectedFiles();
    refreshQueue();
    setInterval(refreshQueue, 5000);
  })();
  </script>
"""
