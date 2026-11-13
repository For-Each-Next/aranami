/** Initialize native controls on server-rendered pages and refreshed fragments. */
import { initializeCopyControls } from "./copy.js";
import { initializeDatasets } from "./datasets.js";
import { initializeHistory } from "./history.js";
import { initializeMaintenance } from "./maintenance.js";
import { initializeQualityAuthorship } from "./quality_authorship.js";

/** Enable native page-size forms once in each new fragment. */
function enhance(root) {
    for (const form of root.querySelectorAll(
        ".page-size:not([data-auto-submit])",
    )) {
        form.querySelector("select")?.addEventListener("change", () =>
            form.requestSubmit(),
        );
        form.dataset.autoSubmit = "";
    }
}

window.aranamiComponents = { enhance };
enhance(document);
initializeCopyControls();
initializeDatasets();
initializeHistory();
initializeMaintenance({ enhance });
initializeQualityAuthorship();
