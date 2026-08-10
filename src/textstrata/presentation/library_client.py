"""Browser behavior for the server-rendered library page."""

from __future__ import annotations

from .browser_common import browser_common_script
from .dialog_client import dialog_lifecycle_script
from .library_navigation import library_navigation_script
from .library_operations import library_operations_script
from .library_preferences import library_preferences_script
from .library_review import library_review_script


def library_page_script() -> str:
    return r"""  <script>
  (() => {
""" + browser_common_script() + dialog_lifecycle_script() + library_navigation_script() + library_preferences_script() + library_review_script() + library_operations_script() + r"""

    function closeMenus() {
      document.querySelectorAll(".menu-dropdown.open").forEach((menu) => menu.classList.remove("open"));
      document.querySelectorAll('.menu-trigger[aria-expanded]').forEach((button) => button.removeAttribute("aria-expanded"));
    }

    document.querySelectorAll(".menu-trigger").forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        event.stopPropagation();
        const menu = document.getElementById(trigger.dataset.menu);
        if (!menu) return;
        const wasOpen = menu.classList.contains("open");
        closeMenus();
        if (!wasOpen) {
          menu.classList.add("open");
          trigger.setAttribute("aria-expanded", "true");
        }
      });
    });
    document.addEventListener("click", closeMenus);

    const confirmation = createConfirmationController();
    const {ask, confirmed} = confirmation;

    bindDialogDismissals();
    document.addEventListener("keydown", (event) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) { if (event.key === "Escape") event.target.blur(); return; }
      if (event.key === "/") { event.preventDefault(); query?.focus(); }
      else if (["b", "B"].includes(event.key)) { event.preventDefault(); toggleSidebar(); }
      else if (["n", "N"].includes(event.key)) { event.preventDefault(); location.href = "/new"; }
      else if (event.key === "?") { event.preventDefault(); $("#shortcuts-help").hidden = !$("#shortcuts-help").hidden; }
      else if (event.key === "Escape") { closeMenus(); closeSidebar(); }
    });

    const params = new URLSearchParams(location.search);
    const openTarget = params.get("open");
    if (params.get("panel") === "new") location.replace("/new");
    if (params.get("panel") === "settings" || openTarget === "settings") openSettings();
    if (openTarget === "review") openReviewDialog();
    if (openTarget === "trash") openTrashDialog();
    if (openTarget === "imports") openImportHistory();
    if (openTarget === "maintenance") openMaintenanceDialog();
    setInterval(() => { if (syncDialog?.open) loadImportJobs(); }, 5000);
  })();
  </script>
"""
