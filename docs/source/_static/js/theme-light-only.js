(function () {
  const THEME_KEY = "theme";

  function normalizeTheme(value) {
    return value === "dark" ? "dark" : "light";
  }

  function applyTheme(value, options) {
    const opts = options || {};
    const persist = opts.persist !== false;
    const theme = normalizeTheme(value);
    const body = document.body;
    if (body) {
      body.dataset.theme = theme;
    }
    document.documentElement.style.colorScheme = theme;
    if (!persist) {
      return;
    }
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_) {
      // Ignore storage errors (e.g., private mode).
    }
  }

  function init() {
    const body = document.body;
    const forceLightLanding = !!(
      body && body.classList.contains("mxl-landing-simple")
    );

    if (forceLightLanding) {
      // Keep the index landing in the bright documentation style without changing global preference.
      applyTheme("light", { persist: false });
      return;
    }

    let stored = null;
    try {
      stored = localStorage.getItem(THEME_KEY);
    } catch (_) {
      stored = null;
    }

    const theme = normalizeTheme(stored);
    applyTheme(theme);

    const toggles = document.getElementsByClassName("theme-toggle");
    Array.from(toggles).forEach((btn) => {
      btn.addEventListener(
        "click",
        (event) => {
          // Prevent Furo's 3-state (light/dark/auto) handler.
          event.stopImmediatePropagation();
          const current =
            document.body && document.body.dataset.theme === "dark"
              ? "dark"
              : "light";
          const next = current === "dark" ? "light" : "dark";
          applyTheme(next);
        },
        true
      );
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
