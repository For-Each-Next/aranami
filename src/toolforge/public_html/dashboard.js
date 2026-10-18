/** Update quality filters in place while leaving homepage history intact. */
(() => {
  "use strict";

  const status = document.querySelector("[data-quality-status]");
  if (!status || !document.querySelector(".project-layout")) return;

  let controller = null;
  let renderedUrl = new URL(window.location.href);
  let requestNumber = 0;
  window.history.scrollRestoration = "manual";
  const pageSize = 25;
  const numbers = new Intl.NumberFormat(document.documentElement.lang || "en");

  /** Update the visible-record count and the next batch label. */
  function updatePagination(table) {
    const panel = table.closest("#quality");
    const button = panel?.querySelector("[data-quality-show-more]");
    const progress = panel?.querySelector("[data-quality-progress]");
    if (!button || !progress) return;

    const rows = Array.from(table.tBodies[0]?.rows || []);
    const shown = rows.filter((row) => !row.hidden).length;
    const remaining = rows.length - shown;
    progress.textContent = progress.dataset.template
      .replaceAll("{shown}", numbers.format(shown))
      .replaceAll("{total}", numbers.format(rows.length));
    button.hidden = remaining === 0;
    button.textContent = button.dataset.template.replaceAll(
      "{count}", numbers.format(Math.min(pageSize, remaining)),
    );
  }

  /** Enhance a complete server-rendered list with local reveal controls. */
  function initializePagination(scope) {
    for (const table of scope.querySelectorAll("[data-quality-table]")) {
      Array.from(table.tBodies[0]?.rows || []).forEach((row, index) => {
        row.hidden = index >= pageSize;
      });
      updatePagination(table);
    }
  }

  initializePagination(document);

  /** Fetch and replace only the quality layout for a local filter URL. */
  async function updateQuality(destination, pushHistory) {
    controller?.abort();
    controller = new AbortController();
    const activeController = controller;
    const currentRequest = ++requestNumber;
    const layout = document.querySelector(".project-layout");
    if (!layout) return;

    status.hidden = true;
    status.textContent = "";
    layout.setAttribute("aria-busy", "true");

    try {
      const response = await fetch(destination.href, {
        method: "GET",
        headers: { Accept: "text/html" },
        credentials: "same-origin",
        cache: "no-store",
        signal: activeController.signal,
      });
      const finalUrl = new URL(response.url);
      if (
        !response.ok ||
        finalUrl.origin !== window.location.origin ||
        !response.headers.get("Content-Type")?.toLowerCase().startsWith("text/html")
      ) {
        throw new Error("The quality response is unavailable.");
      }

      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = page.querySelector(".project-layout");
      if (!replacement) throw new Error("The quality response has no layout.");
      if (currentRequest !== requestNumber || activeController.signal.aborted) return;

      const scrollX = window.scrollX;
      const scrollY = window.scrollY;
      finalUrl.hash = destination.hash;
      if (pushHistory && finalUrl.href !== window.location.href) {
        window.history.pushState(null, "", finalUrl.href);
      }
      const nextLayout = document.importNode(replacement, true);
      initializePagination(nextLayout);
      layout.replaceWith(nextLayout);
      renderedUrl = finalUrl;
      window.scrollTo(scrollX, scrollY);
    } catch (error) {
      if (error.name === "AbortError" || currentRequest !== requestNumber) return;
      status.textContent = status.dataset.error || "This view could not be updated.";
      status.hidden = false;
    } finally {
      if (currentRequest === requestNumber) {
        document.querySelector(".project-layout")?.removeAttribute("aria-busy");
      }
    }
  }

  document.addEventListener("click", (event) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      !(event.target instanceof Element)
    ) return;

    const moreButton = event.target.closest("button[data-quality-show-more]");
    if (moreButton?.closest(".project-layout")) {
      event.preventDefault();
      const table = moreButton.closest("#quality")?.querySelector("[data-quality-table]");
      if (!table) return;
      const revealed = Array.from(table.tBodies[0]?.rows || [])
        .filter((row) => row.hidden).slice(0, pageSize);
      revealed.forEach((row) => { row.hidden = false; });
      updatePagination(table);
      if (moreButton.hidden && document.activeElement === moreButton) {
        revealed[0]?.querySelector("a")?.focus({ preventScroll: true });
      }
      return;
    }

    const link = event.target.closest("a[data-quality-filter]");
    if (
      !link ||
      !link.closest(".project-layout") ||
      link.hasAttribute("download") ||
      (link.target && link.target !== "_self")
    ) return;

    const destination = new URL(link.href, window.location.href);
    if (destination.origin !== window.location.origin) return;

    event.preventDefault();
    void updateQuality(destination, true);
  });

  window.addEventListener("popstate", () => {
    const destination = new URL(window.location.href);
    if (destination.origin === renderedUrl.origin) {
      if (
        destination.pathname !== renderedUrl.pathname ||
        destination.search !== renderedUrl.search
      ) {
        void updateQuality(destination, false);
      } else {
        controller?.abort();
        ++requestNumber;
        document.querySelector(".project-layout")?.removeAttribute("aria-busy");
        status.hidden = true;
        status.textContent = "";
      }
    }
  });
})();
