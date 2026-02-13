(() => {
  const FAVICON_URL = "/public/fermilink_mini_bright.svg";
  const LOGO_URL = "/public/fermilink_wordmark.svg";
  const LOGIN_ACTIVE_CLASS = "cl-login-active";
  const CHAT_ACTIVE_CLASS = "cl-chat-active";
  const LOGIN_THEME_DARK_CLASS = "cl-login-theme-dark";
  const LOGIN_THEME_LIGHT_CLASS = "cl-login-theme-light";
  const LOGIN_HERO_ATTR = "data-cl-login-hero";
  const LOGIN_SUBTITLE_ATTR = "data-cl-login-subtitle";
  const LOGIN_SIGNUP_ENHANCED_ATTR = "data-cl-signup-enhanced";
  const LOGIN_SIGNUP_ROW_ATTR = "data-cl-signup-row";
  const LOGIN_SIGNUP_VIEW_ATTR = "data-cl-signup-view";
  const SIGNUP_STATUS_URL = "api/auth/signup/status";
  const SIGNUP_URL = "api/auth/signup";
  const SIGNUP_STATUS_TTL_MS = 30000;
  const ASSISTANT_THINKING_MESSAGE_TYPE = "assistant_thinking";
  const ASSISTANT_THINKING_CLASS = "cl-assistant-thinking";
  const RUNNING_THREAD_ATTR = "data-cl-thread-running";
  const STOP_ACTIVE_RUN_MESSAGE_TYPE = "stop_active_run";
  const PROBE_ACTIVE_RUN_MESSAGE_TYPE = "probe_active_run";
  const STOP_FALLBACK_ATTR = "data-cl-stop-fallback";
  const RUNNING_INPUT_LOCK_ATTR = "data-cl-running-input-lock";
  const ABS_FAVICON_URL = new URL(FAVICON_URL, window.location.origin).toString();
  const ABS_LOGO_URL = new URL(LOGO_URL, window.location.origin).toString();
  const LOGO_SELECTORS = ["img.logo", "img[alt=\"logo\"]"];
  const THEME_STORAGE_KEYS = [
    "theme",
    "chainlit-theme",
    "chainlit_theme",
    "color-theme",
  ];
  let signupStatusCache = null;
  let signupStatusFetchedAt = 0;
  let signupStatusPromise = null;
  const runningThreadIds = new Set();
  let lastActiveRunProbeAt = 0;

  function normalizeTheme(rawValue) {
    if (!rawValue) return null;
    const value = String(rawValue).toLowerCase().trim();
    if (value === "dark" || value.includes("dark")) return "dark";
    if (value === "light" || value.includes("light")) return "light";
    return null;
  }

  function readStoredTheme() {
    let storage = null;
    try {
      storage = window.localStorage;
    } catch (_error) {
      return null;
    }
    if (!storage) return null;
    for (const key of THEME_STORAGE_KEYS) {
      let rawValue = null;
      try {
        rawValue = storage.getItem(key);
      } catch (_error) {
        continue;
      }
      const theme = normalizeTheme(rawValue);
      if (theme) return theme;
    }
    return null;
  }

  function resolveThemeMode() {
    const root = document.documentElement;
    const body = document.body;
    const darkClassActive =
      root.classList.contains("dark") || Boolean(body && body.classList.contains("dark"));
    const lightClassActive =
      root.classList.contains("light") || Boolean(body && body.classList.contains("light"));
    if (darkClassActive) return "dark";
    if (lightClassActive) return "light";

    const explicitTheme =
      normalizeTheme(root.getAttribute("data-theme")) ||
      normalizeTheme(root.getAttribute("data-mode")) ||
      normalizeTheme(root.getAttribute("data-color-mode")) ||
      normalizeTheme(body && body.getAttribute("data-theme"));
    if (explicitTheme) return explicitTheme;

    const storedTheme = readStoredTheme();
    if (storedTheme) return storedTheme;

    if (window.matchMedia) {
      return window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    }
    return "dark";
  }

  function applyThemeClasses() {
    const themeMode = resolveThemeMode();
    const useDark = themeMode === "dark";
    const root = document.documentElement;
    root.classList.toggle(LOGIN_THEME_DARK_CLASS, useDark);
    root.classList.toggle(LOGIN_THEME_LIGHT_CLASS, !useDark);
    if (document.body) {
      document.body.classList.toggle(LOGIN_THEME_DARK_CLASS, useDark);
      document.body.classList.toggle(LOGIN_THEME_LIGHT_CLASS, !useDark);
    }
  }

  function upsertLink(rel, href, sizes) {
    let link = document.querySelector(`link[rel="${rel}"]`);
    if (!link) {
      link = document.createElement("link");
      link.rel = rel;
      document.head.appendChild(link);
    }
    link.href = href;
    if (sizes) link.sizes = sizes;
  }

  function ensureFavicons() {
    upsertLink("icon", ABS_FAVICON_URL);
    upsertLink("shortcut icon", ABS_FAVICON_URL);
    upsertLink("apple-touch-icon", ABS_FAVICON_URL, "180x180");
  }

  function updateLogos(onLoginPage) {
    if (!onLoginPage) return;

    LOGO_SELECTORS.forEach((selector) => {
      document.querySelectorAll(selector).forEach((img) => {
        if (img && img.src !== ABS_LOGO_URL) {
          img.src = ABS_LOGO_URL;
        }
      });
    });
  }

  function isLoginRoute() {
    return /(^|\/)login(?:\/|$)/.test(window.location.pathname);
  }

  function findLoginForm() {
    const passwordInput = document.querySelector('input[type="password"]');
    if (!passwordInput) return null;
    return passwordInput.closest("form");
  }

  function findLoginShell(form) {
    const byClass = form.closest("div.lg\\:grid-cols-2");
    if (byClass) return byClass;

    let current = form.parentElement;
    while (current && current !== document.body) {
      if (current.querySelector('img[alt="Image"]')) {
        return current;
      }
      current = current.parentElement;
    }

    return null;
  }

  function buildHeroPanel() {
    const panel = document.createElement("section");
    panel.setAttribute(LOGIN_HERO_ATTR, "true");
    panel.className = "cl-login-hero-content";

    const title = document.createElement("h2");
    title.textContent = "Scientific simulations, simplified.";

    const description = document.createElement("p");
    description.className = "cl-login-hero-description";
    description.textContent =
      "Research-grade AI agents for computational science workflows.";

    const featureList = document.createElement("ul");
    featureList.className = "cl-login-feature-list";

    [
      {
        title: "Prompt-routed package loading",
        detail: "Selects the most relevant scientific package for each request.",
      },
      {
        title: "Skill and documentation grounding",
        detail: "Grounds responses in package docs and verified agent skills.",
      },
      {
        title: "Scientific accuracy and reproducibility",
        detail: "Preserves simulation reasoning for reproducibility and review.",
      },
    ].forEach((entry) => {
      const item = document.createElement("li");
      const featureTitle = document.createElement("span");
      featureTitle.className = "cl-login-feature-title";
      featureTitle.textContent = entry.title;
      const featureDetail = document.createElement("span");
      featureDetail.className = "cl-login-feature-detail";
      featureDetail.textContent = entry.detail;
      item.append(featureTitle, featureDetail);
      featureList.appendChild(item);
    });

    panel.append(title, description, featureList);
    return panel;
  }

  function findLoginIdentifierInput(form) {
    if (!form) return null;
    return (
      form.querySelector('input[type="email"]') ||
      form.querySelector('input[name="username"]') ||
      form.querySelector('input[type="text"]')
    );
  }

  function findTemplateInputClassName(form) {
    if (!form) return "";
    const templateInput =
      form.querySelector('input[type="text"]') ||
      form.querySelector('input[type="email"]') ||
      form.querySelector('input[type="password"]') ||
      form.querySelector("input");
    if (!templateInput) return "";
    return (templateInput.getAttribute("class") || "").trim();
  }

  function findTemplateSubmitButtonClassName(form) {
    if (!form) return "";
    const submitButton = form.querySelector('button[type="submit"]');
    if (!submitButton) return "";
    return (submitButton.getAttribute("class") || "").trim();
  }

  function applyTemplateClasses(form, signupView) {
    if (!form || !signupView) return;

    const inputClassName = findTemplateInputClassName(form);
    if (inputClassName) {
      [
        signupView.emailInput,
        signupView.passwordInput,
        signupView.confirmInput,
      ].forEach((input) => {
        input.className = inputClassName;
        input.classList.add("cl-signup-input");
      });
    }

    const submitClassName = findTemplateSubmitButtonClassName(form);
    if (submitClassName) {
      signupView.submitButton.className = submitClassName;
      signupView.submitButton.classList.add("cl-signup-submit-button");
    } else {
      signupView.submitButton.classList.add("cl-signup-submit-button");
    }
  }

  function normalizeSignupStatus(payload) {
    const source =
      payload && typeof payload === "object" ? payload : {};
    const enabled = Boolean(source.enabled);
    const minPasswordLengthRaw = Number(source.min_password_length);
    const minPasswordLength =
      Number.isFinite(minPasswordLengthRaw) && minPasswordLengthRaw > 0
        ? Math.floor(minPasswordLengthRaw)
        : 8;
    const userCountRaw = Number(source.user_count);
    const userCount = Number.isFinite(userCountRaw) ? Math.floor(userCountRaw) : null;
    const maxUsersRaw = Number(source.max_users);
    const maxUsers =
      Number.isFinite(maxUsersRaw) && maxUsersRaw > 0
        ? Math.floor(maxUsersRaw)
        : null;
    const message =
      typeof source.message === "string" && source.message.trim()
        ? source.message.trim()
        : enabled
          ? "Self sign-up is available."
          : "Sign up is currently unavailable.";
    return {
      enabled,
      message,
      minPasswordLength,
      userCount,
      maxUsers,
    };
  }

  function fetchSignupStatus(force = false) {
    const now = Date.now();
    if (
      !force &&
      signupStatusCache &&
      now - signupStatusFetchedAt < SIGNUP_STATUS_TTL_MS
    ) {
      return Promise.resolve(signupStatusCache);
    }
    if (!force && signupStatusPromise) {
      return signupStatusPromise;
    }

    signupStatusPromise = fetch(SIGNUP_STATUS_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(async (resp) => {
        if (!resp.ok) throw new Error(`signup status ${resp.status}`);
        const payload = await resp.json();
        return normalizeSignupStatus(payload);
      })
      .catch(() =>
        normalizeSignupStatus({
          enabled: true,
          message: "Sign up status check is unavailable. You can still try signing up.",
          min_password_length: 8,
        })
      )
      .then((status) => {
        signupStatusCache = status;
        signupStatusFetchedAt = Date.now();
        return status;
      })
      .finally(() => {
        signupStatusPromise = null;
      });
    return signupStatusPromise;
  }

  function setSignupFeedback(node, text, kind) {
    if (!node) return;
    node.textContent = text || "";
    if (kind) {
      node.setAttribute("data-kind", kind);
    } else {
      node.removeAttribute("data-kind");
    }
  }

  function buildSignupField(labelText, inputType, autocomplete, minLength = 0) {
    const field = document.createElement("label");
    field.className = "cl-signup-field";
    field.appendChild(document.createTextNode(labelText));

    const input = document.createElement("input");
    input.type = inputType;
    input.required = true;
    input.autocomplete = autocomplete;
    if (minLength > 0) input.minLength = minLength;

    field.append(input);
    return { field, input };
  }

  function buildSignupView(minPasswordLength) {
    const view = document.createElement("section");
    view.setAttribute(LOGIN_SIGNUP_VIEW_ATTR, "true");
    view.className = "cl-login-form cl-signup-view";
    view.hidden = true;

    const title = document.createElement("h1");
    title.textContent = "Create account";

    const policy = document.createElement("p");
    policy.className = "cl-signup-policy";
    policy.textContent = `Use your email and a password with at least ${minPasswordLength} characters.`;

    const form = document.createElement("form");
    form.className = "cl-signup-form";
    form.noValidate = true;

    const emailField = buildSignupField("Email", "email", "email");
    const passwordField = buildSignupField(
      "Password",
      "password",
      "new-password",
      minPasswordLength
    );
    const confirmField = buildSignupField(
      "Confirm password",
      "password",
      "new-password",
      minPasswordLength
    );

    const actions = document.createElement("div");
    actions.className = "cl-signup-actions";

    const submitButton = document.createElement("button");
    submitButton.type = "submit";
    submitButton.textContent = "Sign up";

    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "cl-login-signup-button cl-signup-back-button";
    backButton.textContent = "Back to sign in";

    const status = document.createElement("p");
    status.className = "cl-signup-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    actions.append(submitButton, backButton);
    form.append(
      emailField.field,
      passwordField.field,
      confirmField.field,
      actions,
      status
    );
    view.append(title, policy, form);
    return {
      view,
      form,
      policy,
      emailInput: emailField.input,
      passwordInput: passwordField.input,
      confirmInput: confirmField.input,
      submitButton,
      backButton,
      statusNode: status,
    };
  }

  function ensureSignupControls(form) {
    if (!form) return;
    if (form.getAttribute(LOGIN_SIGNUP_ENHANCED_ATTR) === "true") return;
    const formShell = form.parentElement;
    if (!formShell || !(formShell instanceof HTMLElement)) return;

    form.setAttribute(LOGIN_SIGNUP_ENHANCED_ATTR, "true");

    const signupRow = document.createElement("div");
    signupRow.className = "cl-login-signup-row";
    signupRow.setAttribute(LOGIN_SIGNUP_ROW_ATTR, "true");

    const separator = document.createElement("p");
    separator.className = "cl-login-signup-or";
    separator.textContent = "or";

    const signupButton = document.createElement("button");
    signupButton.type = "button";
    signupButton.className = "cl-login-signup-button cl-login-signup-launch-button";
    signupButton.textContent = "Sign up";

    const signupNote = document.createElement("p");
    signupNote.className = "cl-login-signup-note";
    signupNote.textContent = "Checking sign-up availability...";

    signupRow.append(separator, signupButton, signupNote);
    form.append(signupRow);

    const signupView = buildSignupView(8);
    formShell.append(signupView.view);
    applyTemplateClasses(form, signupView);

    let signupOpen = false;
    let status = normalizeSignupStatus({ enabled: true });

    const applyStatus = (nextStatus) => {
      status = nextStatus;
      signupButton.disabled = !nextStatus.enabled;
      signupButton.setAttribute("aria-disabled", String(!nextStatus.enabled));
      signupView.submitButton.disabled = !nextStatus.enabled;
      signupNote.textContent = nextStatus.enabled
        ? "Create an account with email + password."
        : nextStatus.message;
      signupView.policy.textContent = `Use your email and a password with at least ${nextStatus.minPasswordLength} characters.`;
      signupView.passwordInput.minLength = nextStatus.minPasswordLength;
      signupView.confirmInput.minLength = nextStatus.minPasswordLength;
      if (!nextStatus.enabled && signupOpen) {
        showLoginView();
      }
    };

    const showSignupView = () => {
      if (!status.enabled) return;
      signupOpen = true;
      form.hidden = true;
      signupView.view.hidden = false;
      setSignupFeedback(signupView.statusNode, "", null);
      signupView.emailInput.focus();
    };

    const showLoginView = () => {
      signupOpen = false;
      signupView.view.hidden = true;
      form.hidden = false;
      setSignupFeedback(signupView.statusNode, "", null);
    };

    signupButton.addEventListener("click", () => {
      if (signupButton.disabled) return;
      showSignupView();
    });

    signupView.backButton.addEventListener("click", () => {
      showLoginView();
    });

    signupView.form.addEventListener("submit", async (event) => {
      event.preventDefault();

      if (!status.enabled) {
        setSignupFeedback(signupView.statusNode, status.message, "error");
        return;
      }

      const email = signupView.emailInput.value.trim().toLowerCase();
      const password = signupView.passwordInput.value;
      const confirmPassword = signupView.confirmInput.value;
      if (!email || !password || !confirmPassword) {
        setSignupFeedback(
          signupView.statusNode,
          "Email, password, and confirmation are required.",
          "error"
        );
        return;
      }
      if (password !== confirmPassword) {
        setSignupFeedback(signupView.statusNode, "Passwords do not match.", "error");
        return;
      }
      if (password.length < status.minPasswordLength) {
        setSignupFeedback(
          signupView.statusNode,
          `Password must be at least ${status.minPasswordLength} characters long.`,
          "error"
        );
        return;
      }

      signupView.submitButton.disabled = true;
      setSignupFeedback(signupView.statusNode, "Creating account...", null);
      try {
        const resp = await fetch(SIGNUP_URL, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({
            email,
            password,
            confirm_password: confirmPassword,
          }),
        });

        let payload = {};
        try {
          payload = await resp.json();
        } catch (_error) {
          payload = {};
        }

        if (!resp.ok) {
          const detail =
            typeof payload.detail === "string" && payload.detail
              ? payload.detail
              : "Unable to create account.";
          setSignupFeedback(signupView.statusNode, detail, "error");
          if (resp.status === 403) {
            const latestStatus = await fetchSignupStatus(true);
            applyStatus(latestStatus);
          }
          return;
        }

        const successMessage =
          typeof payload.message === "string" && payload.message
            ? payload.message
            : "Account created. Please sign in.";
        setSignupFeedback(signupView.statusNode, successMessage, "success");

        const loginIdentifierInput = findLoginIdentifierInput(form);
        if (loginIdentifierInput) {
          loginIdentifierInput.value = email;
          loginIdentifierInput.dispatchEvent(new Event("input", { bubbles: true }));
        }

        signupView.passwordInput.value = "";
        signupView.confirmInput.value = "";
        const latestStatus = await fetchSignupStatus(true);
        applyStatus(latestStatus);
        window.setTimeout(showLoginView, 900);
      } catch (_error) {
        setSignupFeedback(
          signupView.statusNode,
          "Sign up request failed. Please try again.",
          "error"
        );
      } finally {
        signupView.submitButton.disabled = !status.enabled;
      }
    });

    fetchSignupStatus().then((initialStatus) => {
      applyStatus(initialStatus);
    });
  }

  function ensureLoginDecoration() {
    const form = findLoginForm();
    const onLoginRoute = isLoginRoute();
    const onLoginPage = Boolean(form) && onLoginRoute;
    const onChatPage = !onLoginRoute;

    document.documentElement.classList.toggle(LOGIN_ACTIVE_CLASS, onLoginPage);
    document.documentElement.classList.toggle(CHAT_ACTIVE_CLASS, onChatPage);
    if (document.body) {
      document.body.classList.toggle(LOGIN_ACTIVE_CLASS, onLoginPage);
      document.body.classList.toggle(CHAT_ACTIVE_CLASS, onChatPage);
    }

    if (!onLoginPage || !form) return onLoginPage;

    const shell = findLoginShell(form);
    if (!shell) return onLoginPage;
    shell.classList.add("cl-login-shell");

    const shellChildren = Array.from(shell.children).filter(
      (node) => node instanceof HTMLElement
    );
    const formPane = shellChildren.find((node) => node.contains(form)) || null;
    const heroPane =
      shellChildren.find((node) => node.querySelector('img[alt="Image"]')) || null;

    if (formPane) {
      formPane.classList.add("cl-login-panel");

      const logoHost = formPane.querySelector('img.logo, img[alt="logo"]');
      const logoStrip = logoHost ? logoHost.closest("div") : null;
      if (logoStrip) logoStrip.classList.add("cl-login-brand-strip");

      const formShell = form.parentElement;
      if (formShell && formShell instanceof HTMLElement) {
        formShell.classList.add("cl-login-form-shell");

        if (!formShell.querySelector(`[${LOGIN_SUBTITLE_ATTR}]`)) {
          const subtitle = document.createElement("p");
          subtitle.setAttribute(LOGIN_SUBTITLE_ATTR, "true");
          subtitle.className = "cl-login-subtitle";
          subtitle.textContent =
            "A journey for experiencing automated scientific simulations.";
          formShell.insertBefore(subtitle, formShell.firstChild);
        }
      }

      form.classList.add("cl-login-form");
      ensureSignupControls(form);
    }

    if (heroPane) {
      shell.classList.remove("cl-login-shell-solo");
      heroPane.classList.add("cl-login-hero");
      const heroImage = heroPane.querySelector('img[alt="Image"]');
      if (heroImage) heroImage.classList.add("cl-login-hero-image");

      if (!heroPane.querySelector(`[${LOGIN_HERO_ATTR}]`)) {
        heroPane.appendChild(buildHeroPanel());
      }
    } else {
      shell.classList.add("cl-login-shell-solo");
    }

    return onLoginPage;
  }

  function extractThreadIdFromUrl(urlValue) {
    if (!urlValue) return null;
    let parsedUrl;
    try {
      parsedUrl = new URL(urlValue, window.location.origin);
    } catch (_error) {
      return null;
    }
    const match = parsedUrl.pathname.match(/\/thread\/([^/?#]+)/);
    if (!match || !match[1]) return null;
    const threadId = decodeURIComponent(match[1]).trim();
    return threadId || null;
  }

  function getCurrentThreadId() {
    return extractThreadIdFromUrl(window.location.href);
  }

  function getWindowMessageThreadId(payload) {
    if (!payload || typeof payload !== "object") {
      return getCurrentThreadId();
    }
    if (typeof payload.thread_id === "string" && payload.thread_id.trim()) {
      return payload.thread_id.trim();
    }
    if (typeof payload.threadId === "string" && payload.threadId.trim()) {
      return payload.threadId.trim();
    }
    return getCurrentThreadId();
  }

  function isCurrentThreadRunning() {
    const threadId = getCurrentThreadId();
    return Boolean(threadId && runningThreadIds.has(threadId));
  }

  function sendWindowControlMessage(payload) {
    if (!payload || typeof payload !== "object") return;
    window.postMessage(payload, window.location.origin);
  }

  function probeActiveRunState() {
    const threadId = getCurrentThreadId();
    if (!threadId) return;
    if (runningThreadIds.has(threadId)) return;
    const now = Date.now();
    if (now - lastActiveRunProbeAt < 1500) return;
    lastActiveRunProbeAt = now;
    sendWindowControlMessage({
      type: PROBE_ACTIVE_RUN_MESSAGE_TYPE,
      thread_id: threadId,
    });
  }

  function requestStopForCurrentThread() {
    const threadId = getCurrentThreadId();
    sendWindowControlMessage({
      type: STOP_ACTIVE_RUN_MESSAGE_TYPE,
      thread_id: threadId || undefined,
    });
  }

  function syncComposerRunningState() {
    const nativeStopButton = document.getElementById("stop-button");
    const submitButton = document.getElementById("chat-submit");
    const chatInput = document.getElementById("chat-input");
    const running = isCurrentThreadRunning();

    if (chatInput instanceof HTMLTextAreaElement) {
      if (running) {
        chatInput.readOnly = true;
        chatInput.setAttribute(RUNNING_INPUT_LOCK_ATTR, "true");
      } else if (chatInput.getAttribute(RUNNING_INPUT_LOCK_ATTR) === "true") {
        chatInput.readOnly = false;
        chatInput.removeAttribute(RUNNING_INPUT_LOCK_ATTR);
      }
    }

    if (nativeStopButton) {
      if (
        submitButton instanceof HTMLButtonElement &&
        submitButton.getAttribute(STOP_FALLBACK_ATTR) === "true"
      ) {
        submitButton.removeAttribute(STOP_FALLBACK_ATTR);
        submitButton.removeAttribute("aria-label");
        submitButton.removeAttribute("title");
      }
      return;
    }

    if (!(submitButton instanceof HTMLButtonElement)) return;

    if (running) {
      submitButton.setAttribute(STOP_FALLBACK_ATTR, "true");
      submitButton.setAttribute("aria-label", "Stop current response");
      submitButton.setAttribute("title", "Stop current response");
      submitButton.disabled = false;
    } else if (submitButton.getAttribute(STOP_FALLBACK_ATTR) === "true") {
      submitButton.removeAttribute(STOP_FALLBACK_ATTR);
      submitButton.removeAttribute("aria-label");
      submitButton.removeAttribute("title");
    }
  }

  function collectSidebarThreadLinks() {
    return Array.from(
      document.querySelectorAll('aside a[href*="/thread/"], [data-sidebar] a[href*="/thread/"]')
    );
  }

  function syncAssistantThinkingClass() {
    const hasRunningThreads = runningThreadIds.size > 0;
    document.documentElement.classList.toggle(
      ASSISTANT_THINKING_CLASS,
      hasRunningThreads
    );
    if (document.body) {
      document.body.classList.toggle(ASSISTANT_THINKING_CLASS, hasRunningThreads);
    }
  }

  function refreshRunningThreadIndicators() {
    syncAssistantThinkingClass();
    const links = collectSidebarThreadLinks();
    links.forEach((link) => {
      const href = link.getAttribute("href") || link.href;
      const threadId = extractThreadIdFromUrl(href);
      const isRunning = Boolean(threadId && runningThreadIds.has(threadId));
      const row =
        link.closest('[data-testid="thread-item"]') ||
        link.closest(".group\\/thread") ||
        link.parentElement;
      if (row) {
        row.toggleAttribute(RUNNING_THREAD_ATTR, isRunning);
        link.removeAttribute(RUNNING_THREAD_ATTR);
      } else {
        link.toggleAttribute(RUNNING_THREAD_ATTR, isRunning);
      }
    });
    syncComposerRunningState();
  }

  function handleAssistantThinkingWindowMessage(payload) {
    if (!payload || typeof payload !== "object") return;
    if (payload.type !== ASSISTANT_THINKING_MESSAGE_TYPE) return;
    const status = typeof payload.status === "string" ? payload.status.trim() : "";
    const threadId = getWindowMessageThreadId(payload);
    if (!threadId) return;
    if (status === "start") {
      runningThreadIds.add(threadId);
    } else if (status === "end") {
      runningThreadIds.delete(threadId);
    } else {
      return;
    }
    refreshRunningThreadIndicators();
  }

  function refresh() {
    ensureFavicons();
    applyThemeClasses();
    const onLoginPage = ensureLoginDecoration();
    updateLogos(onLoginPage);
    refreshRunningThreadIndicators();
    probeActiveRunState();
  }

  let refreshPending = false;
  function scheduleRefresh() {
    if (refreshPending) return;
    refreshPending = true;
    requestAnimationFrame(() => {
      refreshPending = false;
      refresh();
    });
  }

  const observer = new MutationObserver(() => scheduleRefresh());
  observer.observe(document.documentElement, { childList: true, subtree: true });
  const themeObserver = new MutationObserver(() => scheduleRefresh());
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class", "data-theme", "data-mode", "data-color-mode"],
  });
  if (document.body) {
    themeObserver.observe(document.body, {
      attributes: true,
      attributeFilter: ["class", "data-theme", "data-mode", "data-color-mode"],
    });
  }

  if (window.matchMedia) {
    const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
    if (typeof colorScheme.addEventListener === "function") {
      colorScheme.addEventListener("change", scheduleRefresh);
    } else if (typeof colorScheme.addListener === "function") {
      colorScheme.addListener(scheduleRefresh);
    }
  }

  window.addEventListener("storage", (event) => {
    if (!event.key || THEME_STORAGE_KEYS.includes(event.key)) {
      scheduleRefresh();
    }
  });
  window.addEventListener("popstate", scheduleRefresh);
  window.addEventListener("message", (event) => {
    handleAssistantThinkingWindowMessage(event.data);
  });
  document.addEventListener(
    "click",
    (event) => {
      const target =
        event.target instanceof Element
          ? event.target.closest(`button#chat-submit[${STOP_FALLBACK_ATTR}="true"]`)
          : null;
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      requestStopForCurrentThread();
    },
    true
  );
  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
        return;
      }
      const target = event.target;
      if (!(target instanceof HTMLElement) || target.id !== "chat-input") {
        return;
      }
      if (!isCurrentThreadRunning()) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
    },
    true
  );

  if (document.readyState === "complete" || document.readyState === "interactive") {
    refresh();
  } else {
    window.addEventListener("load", () => refresh());
  }

})();
