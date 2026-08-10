"""Shared browser primitives emitted into page-local scripts."""

from __future__ import annotations


def browser_common_script() -> str:
    """Return the common DOM, fetch, escaping, and toast helpers."""
    return r'''    const $ = (selector) => document.querySelector(selector);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));

    async function api(url, options = {}) {
      const response = await fetch(url, options);
      let payload = {};
      try { payload = await response.json(); }
      catch { payload = {error: await response.text()}; }
      if (!response.ok) throw new Error(payload.error || payload.detail || `Request failed (${response.status})`);
      return payload;
    }

    function toast(message, type = "info", duration = 3000) {
      const container = $("#toast-container");
      if (!container) return;
      const element = document.createElement("div");
      element.className = `toast toast-${type}`;
      element.textContent = String(message);
      container.appendChild(element);
      setTimeout(() => {
        element.style.animation = "toast-out 180ms ease forwards";
        setTimeout(() => element.remove(), 190);
      }, duration);
    }
'''
