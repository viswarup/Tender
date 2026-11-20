/*
 * Offline demo shim.
 *
 * The dashboards were written against a live FastAPI backend. Rather than fork
 * their logic for the demo, this intercepts window.fetch and answers /api/*
 * calls from the inlined snapshot in demo-data.js. Everything else falls
 * through untouched. Keeps one codebase for app and demo.
 */
(function () {
    "use strict";

    var DATA = window.DEMO_DATA || {};
    var FEEDBACK_KEY = "tenderDemoFeedback";
    var realFetch = window.fetch ? window.fetch.bind(window) : null;

    function json(body, status) {
        return new Response(JSON.stringify(body), {
            status: status || 200,
            headers: { "Content-Type": "application/json" }
        });
    }

    function seg(path) {
        return path.split("/").filter(Boolean).map(decodeURIComponent);
    }

    function route(path, method, options) {
        var parts = seg(path);          // ["api", "vendor", "HEAMONS SYSTEMS"]
        var name = parts[1];
        var arg = parts.slice(2).join("/");

        if (method === "POST") {
            if (name === "feedback") {
                var entry = {};
                try { entry = JSON.parse(options && options.body); } catch (e) { entry = {}; }
                entry.timestamp = new Date().toISOString();
                try {
                    var all = JSON.parse(localStorage.getItem(FEEDBACK_KEY) || "[]");
                    all.push(entry);
                    localStorage.setItem(FEEDBACK_KEY, JSON.stringify(all));
                } catch (e) { /* private mode / storage blocked — annotation is best-effort */ }
                return json({
                    status: "success",
                    message: "Annotation saved locally in this browser (offline demo)."
                });
            }
            if (name === "chat") {
                return json({
                    response: "The assistant needs the live server and an LLM backend, "
                        + "so it is disabled in this offline demo. Everything else "
                        + "— classification, OCR, extraction, traceability and the PQC "
                        + "verdict — is fully explorable.",
                    sources: []
                });
            }
            if (name === "run-pipeline") {
                return json({ detail: "Pipeline runs require the live server." }, 501);
            }
        }

        if (name === "config") return json(DATA.config || {});
        if (name === "summary") return json(DATA.summary || {});
        if (name === "vendors") return json(DATA.vendors || []);
        if (name === "vendor") return pick(DATA.vendorDetail, arg, "Vendor");
        if (name === "evaluation") return pick(DATA.evaluations, arg, "Evaluation");
        if (name === "page-images") return json((DATA.pageImages || {})[arg] || []);
        if (name === "ground-truth") return pick(DATA.groundTruth, arg, "Ground truth");

        return json({ detail: "Not available in the offline demo bundle: " + path }, 501);
    }

    function pick(table, key, label) {
        var value = (table || {})[key];
        if (value === undefined) return json({ detail: label + " '" + key + "' not in bundle" }, 404);
        return json(value);
    }

    window.fetch = function (input, options) {
        var url = typeof input === "string" ? input : (input && input.url) || "";
        var method = ((options && options.method) || "GET").toUpperCase();
        var path = url.split("?")[0].replace(/^[a-z]+:\/\/[^/]*/i, "");

        if (path.indexOf("/api/") === 0 || path.indexOf("api/") === 0) {
            if (path.indexOf("api/") === 0) path = "/" + path;
            try {
                return Promise.resolve(route(path, method, options));
            } catch (err) {
                return Promise.resolve(json({ detail: "Demo shim error: " + err.message }, 500));
            }
        }
        if (realFetch) return realFetch(input, options);
        return Promise.reject(new Error("fetch unavailable"));
    };

    window.exportDemoFeedback = function () {
        return JSON.parse(localStorage.getItem(FEEDBACK_KEY) || "[]");
    };
})();
