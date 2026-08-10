"""Shared dialog lifecycle and confirmation behavior."""

from __future__ import annotations


def dialog_lifecycle_script() -> str:
    """Return the common confirmation and dismiss handling."""
    return r'''    function bindDialogDismissals() {
      document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => document.getElementById(button.dataset.close)?.close()));
      document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); }));
    }

    function createConfirmationController() {
      const dialog = $("#confirm-dialog");
      let resolver = null;
      function settle(value) {
        const current = resolver;
        resolver = null;
        if (dialog?.open) dialog.close();
        if (current) current(value);
      }
      function ask(title, warning, reference = "/item/system.operations-error-reference") {
        if (!dialog) return Promise.resolve(false);
        if (resolver) settle(false);
        $("#confirm-title").textContent = title;
        $("#confirm-warning").textContent = warning;
        $("#confirm-reference").href = reference;
        dialog.showModal();
        return new Promise((resolve) => { resolver = resolve; });
      }
      async function confirmed(url, method, title, warning, body) {
        if (!await ask(title, warning)) return null;
        const options = {method, headers: {"X-TextStrata-Confirm": "true"}};
        if (body !== undefined) {
          options.headers["Content-Type"] = "application/json";
          options.body = JSON.stringify(body);
        }
        return api(url, options);
      }
      $("#confirm-cancel")?.addEventListener("click", () => settle(false));
      $("#confirm-accept")?.addEventListener("click", () => settle(true));
      dialog?.addEventListener("cancel", (event) => { event.preventDefault(); settle(false); });
      dialog?.addEventListener("close", () => { if (resolver) settle(false); });
      return {dialog, ask, confirmed};
    }
'''
