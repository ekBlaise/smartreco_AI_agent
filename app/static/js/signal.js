/*
 * "Your Signal" — the live panel.
 *
 * It shows two streams that arrive by deliberately different routes:
 *
 *   1. What you just did. Rendered from the tracker's own observer hook, so a
 *      chip appears the instant an action is recorded — even though the event
 *      itself leaves in a batch up to five seconds later. No request is made to
 *      draw it, which is the point: the panel costs the page nothing.
 *
 *   2. What the agent did about it. That really does happen server-side, in a
 *      worker, so it arrives over SSE (/api/signal/stream). If that connection
 *      cannot be established — Redis down, proxy eating the stream — we fall
 *      back to polling the ordinary recommendation endpoint. The panel gets
 *      slower, never broken.
 */
(function () {
  "use strict";

  var VISIBLE_CHIPS = 5;    // fixed height — the panel must not grow down the page
  var MAX_CHIPS = 5;        // the feed is a running tail, not a transcript
  var POLL_MS = 6000;
  var RECONNECT_MS = 4000;
  var STORE_KEY = "smartreco.signal.feed";

  /** How each tracked event type reads in the feed. */
  var LABELS = {
    product_view: "Viewed",
    product_click: "Opened",
    reco_click: "Followed",
    enroll_intent: "Added",
    search: "Searched",
    dwell: "Dwell",
    scroll_depth: "Read",
    page_view: "Browsed",
    category_filter: "Filtered"
  };

  /** Resolve a product title from the DOM rather than sending it to the server. */
  function titleFor(productId) {
    if (productId == null) return "";
    var el = document.querySelector('[data-product-id="' + productId + '"][data-track-title]');
    if (el) return el.getAttribute("data-track-title") || "";
    return "";
  }

  function seconds(ms) {
    var s = Math.round((ms || 0) / 1000);
    if (s < 60) return s + "s";
    return Math.round(s / 60) + "m";
  }

  /**
   * What the current page *is*, for events that carry no product of their own
   * (scroll depth, page views). On a course page that is the course title —
   * never the raw URL, which is what the reader least wants to see.
   */
  function pageLabel() {
    var el = document.querySelector("[data-product-id][data-track-title]");
    if (el) return el.getAttribute("data-track-title") || "";

    var path = window.location.pathname;
    if (path === "/") return "the homepage";
    if (path.indexOf("/catalog") === 0) return "the catalog";
    if (path.indexOf("/search") === 0) return "search results";
    if (path.indexOf("/dashboard") === 0) return "your recommendations";
    return path.replace(/^\//, "").slice(0, 28);
  }

  /** Turn a tracked event into the {verb, subject} a chip renders. */
  function describe(event) {
    var verb = LABELS[event.type] || event.type;
    var title = titleFor(event.product_id);
    var meta = event.meta || {};

    switch (event.type) {
      case "search":
        return { verb: verb, subject: '"' + (event.query || "") + '"' };
      case "dwell":
        return {
          verb: verb,
          subject: seconds(meta.cumulative_ms || event.dwell_ms) +
                   " on " + (title || meta.category || pageLabel())
        };
      case "scroll_depth":
        return { verb: verb, subject: (meta.depth || 0) + "% of " + (title || pageLabel()) };
      case "page_view":
        return { verb: verb, subject: meta.category || pageLabel() };
      case "category_filter":
        return { verb: verb, subject: event.query || "" };
      default:
        return { verb: verb, subject: title || meta.category || pageLabel() };
    }
  }

  window.signalPanel = function (initial) {
    initial = initial || {};

    return {
      chips: [],
      //: who this feed belongs to; a change wipes it (see restore)
      owner: initial.owner || "",
      status: "connecting",   // connecting | streaming | thinking | offline
      reco: initial.reco || null,
      events: initial.events || 0,
      score: initial.score || 0,
      needed: initial.needed || 12,
      seq: 0,
      source: null,
      poller: null,
      unsubscribe: null,

      init() {
        // The feed follows the visitor across pages. Starting empty on every
        // navigation made it look like nothing was being remembered, which is
        // the opposite of what the panel is there to show.
        this.restore();
        this.watchBehaviour();
        // Guests get the same stream: the agent runs for them too.
        this.connect();

        // Tabs are cheap to leave open; make sure we do not leave an SSE
        // connection and a poller running after this page is gone.
        window.addEventListener("pagehide", () => this.teardown());
      },

      // --- stream 1: what the user just did ---------------------------------

      watchBehaviour() {
        if (!window.SmartReco || !window.SmartReco.onEvent) return;
        this.unsubscribe = window.SmartReco.onEvent((event) => {
          var described = describe(event);
          if (!described.subject) return;

          this.events += 1;

          // Reading one page produces a run of near-identical signals — seven
          // "Viewed · Agentic Workflows" chips say nothing more than one does,
          // and push the recommendation off the screen. Collapse a repeat into
          // a counter on the chip that is already there.
          var last = this.chips[this.chips.length - 1];
          if (last && last.verb === described.verb && last.subject === described.subject) {
            last.count += 1;
            last.at = Date.now();
            this.persist();
            return;
          }

          this.seq += 1;
          this.chips.push({
            id: this.seq,
            verb: described.verb,
            subject: described.subject,
            kind: event.type,
            count: 1,
            at: Date.now()
          });
          if (this.chips.length > MAX_CHIPS) this.chips.shift();
          this.persist();
        });
      },

      // --- carrying the feed across pages ------------------------------------

      restore() {
        try {
          var raw = sessionStorage.getItem(STORE_KEY);
          if (!raw) return;
          var saved = JSON.parse(raw);

          // The feed belongs to whoever it was recorded for. Signing in, out,
          // or in as someone else must not inherit the previous person's
          // activity — on a shared machine that would show one visitor another
          // visitor's browsing.
          if (saved.owner !== this.owner) {
            sessionStorage.removeItem(STORE_KEY);
            return;
          }
          if (!Array.isArray(saved.chips)) return;

          this.chips = saved.chips.slice(-MAX_CHIPS);
          this.seq = saved.seq || this.chips.length;
        } catch (e) {
          /* private mode, quota, or corrupt entry — start fresh, never break */
        }
      },

      persist() {
        try {
          sessionStorage.setItem(
            STORE_KEY,
            JSON.stringify({ owner: this.owner, chips: this.chips, seq: this.seq })
          );
        } catch (e) {
          /* storage unavailable — the feed just will not survive navigation */
        }
      },

      // --- stream 2: what the agent did about it ----------------------------

      connect() {
        if (typeof window.EventSource === "undefined") return this.fallback();

        try {
          this.source = new EventSource("/api/signal/stream");
        } catch (e) {
          return this.fallback();
        }

        this.source.addEventListener("open", () => {
          if (this.status !== "thinking") this.status = "streaming";
        });

        this.source.addEventListener("snapshot", (e) => {
          var data = this.parse(e);
          if (!data) return;
          this.events = data.events_tracked;
          this.score = data.behavior_score;
          this.needed = data.score_needed || this.needed;
          if (data.recommendation) this.reco = data.recommendation;
          if (this.status === "connecting") this.status = "streaming";
        });

        this.source.addEventListener("agent_state", (e) => {
          var data = this.parse(e);
          if (!data) return;
          this.status = data.state === "thinking" ? "thinking" : "streaming";
        });

        this.source.addEventListener("recommendation", (e) => {
          var data = this.parse(e);
          if (!data) return;
          this.reco = data;
          this.status = "streaming";
        });

        // The server says it is done (Redis unavailable, or shutting down).
        this.source.addEventListener("closed", () => this.fallback());

        // EventSource retries on its own; only give up on repeated failure.
        this.source.addEventListener("error", () => {
          if (this.source && this.source.readyState === EventSource.CLOSED) {
            setTimeout(() => this.fallback(), RECONNECT_MS);
          }
        });
      },

      parse(e) {
        try {
          return JSON.parse(e.data);
        } catch (err) {
          return null;
        }
      },

      /** No live channel — poll the ordinary endpoint instead. */
      fallback() {
        this.closeSource();
        if (this.poller) return;
        this.status = "offline";
        this.poller = setInterval(() => this.load(), POLL_MS);
        this.load();
      },

      async load() {
        try {
          var res = await fetch("/api/recommendations", { credentials: "same-origin" });
          if (!res.ok) return;
          var data = await res.json();
          this.events = data.events_tracked;
          this.score = data.behavior_score;
          this.needed = data.score_needed || this.needed;
          if (data.recommendation) this.reco = data.recommendation;
        } catch (e) {
          /* offline; the next tick can try again */
        }
      },

      closeSource() {
        if (this.source) {
          try { this.source.close(); } catch (e) { /* already gone */ }
          this.source = null;
        }
      },

      teardown() {
        this.closeSource();
        clearInterval(this.poller);
        this.poller = null;
        if (this.unsubscribe) this.unsubscribe();
      },

      // --- view helpers ------------------------------------------------------

      get statusLabel() {
        if (this.status === "thinking") return "agent thinking";
        if (this.status === "offline") return "polling";
        if (this.status === "connecting") return "connecting";
        return "streaming";
      },

      get progress() {
        return Math.min(100, Math.round((this.score / (this.needed || 1)) * 100));
      },

      /** Newest first. A fixed-length tail, so the panel never grows. */
      get visibleChips() {
        return this.chips.slice().reverse().slice(0, VISIBLE_CHIPS);
      },

      money(item) {
        var value = Number(item.price || 0);
        var symbol = item.currency === "USD" || !item.currency ? "$" : item.currency + " ";
        return symbol + value.toFixed(0);
      }
    };
  };
})();
