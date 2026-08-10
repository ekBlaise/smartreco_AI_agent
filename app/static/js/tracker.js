/*
 * SmartReco behavioural tracker.
 *
 * Design rules, in priority order:
 *   1. Never block or break the page. Every path is wrapped; a tracking failure
 *      is swallowed, not surfaced.
 *   2. Never send one request per action. Events queue in memory and leave in
 *      batches — on a size trigger, a timer, or when the page is being hidden.
 *   3. Never fire high-frequency events raw. Scroll is bucketed, search is
 *      debounced, dwell is measured once per page and emitted on exit,
 *      duplicates within a second are dropped.
 *   4. Never delay unload. The exit flush uses sendBeacon, which the browser
 *      delivers after the page is gone.
 */
(function () {
  "use strict";

  var ENDPOINT = "/api/events/batch";
  var MAX_QUEUE = 10;          // flush once this many events are waiting
  // Hard ceiling on unsent events. flush() backs off while a request is in
  // flight, so a hung or slow server would otherwise let the queue grow for as
  // long as the tab stays open. Analytics must never be the reason a page runs
  // out of memory: past this we drop the oldest, because the newest behaviour
  // is the behaviour worth having.
  var MAX_BUFFERED = 200;
  var FLUSH_INTERVAL_MS = 5000;
  var DEDUPE_WINDOW_MS = 1000;
  var SEARCH_DEBOUNCE_MS = 600;
  var SCROLL_BUCKETS = [25, 50, 75, 100];

  // Emit dwell in slices so the live panel has something to show while the user
  // is still reading, instead of one lump after they have already left.
  var DWELL_MILESTONES_MS = [10000, 30000, 60000, 120000, 300000];

  var queue = [];
  var lastSent = {};           // dedupe key -> timestamp
  var firedBuckets = {};
  var flushTimer = null;
  var sending = false;
  var observers = [];          // notified synchronously as events are recorded

  // --- utilities ------------------------------------------------------------

  function now() {
    return new Date().toISOString();
  }

  /** Run work off the critical path when the browser offers idle time. */
  function idle(fn) {
    if (typeof window.requestIdleCallback === "function") {
      window.requestIdleCallback(fn, { timeout: 2000 });
    } else {
      setTimeout(fn, 0);
    }
  }

  function throttle(fn, wait) {
    var last = 0;
    var pending = null;
    return function () {
      var args = arguments;
      var elapsed = Date.now() - last;
      if (elapsed >= wait) {
        last = Date.now();
        fn.apply(null, args);
      } else if (!pending) {
        pending = setTimeout(function () {
          pending = null;
          last = Date.now();
          fn.apply(null, args);
        }, wait - elapsed);
      }
    };
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(null, args);
      }, wait);
    };
  }

  // --- queueing -------------------------------------------------------------

  /**
   * Hand an event to anything watching (the Signal panel), without letting a
   * broken subscriber take tracking down with it.
   */
  function notify(event) {
    for (var i = 0; i < observers.length; i++) {
      try {
        observers[i](event);
      } catch (e) {
        /* a rendering bug in the panel must not stop us recording behaviour */
      }
    }
  }

  /** Queue an already-built event record. Returns the record, or null if dropped. */
  function enqueue(event) {
    queue.push(event);
    if (queue.length > MAX_BUFFERED) queue.splice(0, queue.length - MAX_BUFFERED);
    notify(event);

    if (queue.length >= MAX_QUEUE) {
      flush();
    } else {
      scheduleFlush();
    }
    return event;
  }

  function track(type, payload) {
    try {
      payload = payload || {};

      // Drop an identical signal repeated within the dedupe window — a
      // double-click or a re-render must not become two events.
      var key = type + "|" + (payload.product_id || "") + "|" + (payload.query || "");
      var stamp = Date.now();
      if (lastSent[key] && stamp - lastSent[key] < DEDUPE_WINDOW_MS) return;
      lastSent[key] = stamp;

      enqueue({
        type: type,
        product_id: payload.product_id != null ? Number(payload.product_id) : null,
        query: payload.query || null,
        path: window.location.pathname.slice(0, 300),
        dwell_ms: payload.dwell_ms || 0,
        occurred_at: now(),
        meta: payload.meta || {}
      });
    } catch (e) {
      /* tracking must never throw into the page */
    }
  }

  function scheduleFlush() {
    if (flushTimer) return;
    flushTimer = setTimeout(function () {
      flushTimer = null;
      flush();
    }, FLUSH_INTERVAL_MS);
  }

  /**
   * Send whatever is queued.
   * @param {boolean} isUnload use sendBeacon, which survives page teardown.
   */
  function flush(isUnload) {
    if (!queue.length || (sending && !isUnload)) return;

    var batch = queue.splice(0, queue.length);
    var body = JSON.stringify({ events: batch });

    if (flushTimer) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }

    if (isUnload && navigator.sendBeacon) {
      try {
        navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }));
      } catch (e) {
        /* nothing useful to do while the page is going away */
      }
      return;
    }

    sending = true;
    idle(function () {
      try {
        fetch(ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body,
          keepalive: true,
          credentials: "same-origin"
        })
          .catch(function () {
            /* offline or server down — drop rather than retry forever */
          })
          .finally(function () {
            sending = false;
          });
      } catch (e) {
        sending = false;
      }
    });
  }

  // --- automatic instrumentation -------------------------------------------

  function trackPageView() {
    var el = document.querySelector("[data-track-page]");
    var meta = {};
    if (el) {
      if (el.dataset.trackCategory) meta.category = el.dataset.trackCategory;
      if (el.dataset.trackLevel) meta.level = el.dataset.trackLevel;
    }

    var productEl = document.querySelector("[data-product-id][data-track-view]");
    if (productEl) {
      track("product_view", {
        product_id: productEl.dataset.productId,
        meta: meta
      });
    } else {
      track("page_view", { meta: meta });
    }
  }

  /** Clicks on anything carrying data-product-id, via delegation (one listener). */
  function bindClicks() {
    document.addEventListener(
      "click",
      function (event) {
        var el = event.target.closest("[data-product-id]");
        if (!el) return;
        var type = el.dataset.trackClick || "product_click";
        track(type, {
          product_id: el.dataset.productId,
          meta: {
            category: el.dataset.trackCategory || undefined,
            surface: el.dataset.trackSurface || undefined
          }
        });
      },
      { passive: true }
    );
  }

  /** Search box: debounced, so typing "langgraph" is one event, not nine. */
  function bindSearch() {
    var inputs = document.querySelectorAll("[data-track-search]");
    if (!inputs.length) return;

    var emit = debounce(function (value) {
      if (value && value.trim().length >= 3) {
        track("search", { query: value.trim().slice(0, 300) });
      }
    }, SEARCH_DEBOUNCE_MS);

    Array.prototype.forEach.call(inputs, function (input) {
      input.addEventListener("input", function (e) {
        emit(e.target.value);
      });
      // A submitted search is a stronger signal than a typed one.
      var form = input.closest("form");
      if (form) {
        form.addEventListener("submit", function () {
          var value = (input.value || "").trim();
          if (value) track("search", { query: value.slice(0, 300), meta: { submitted: true } });
          flush(true);
        });
      }
    });
  }

  function bindFilters() {
    document.addEventListener(
      "change",
      function (event) {
        var el = event.target.closest("[data-track-filter]");
        if (!el) return;
        if (!el.value) return;
        track("category_filter", {
          query: String(el.value).slice(0, 300),
          meta: { filter: el.dataset.trackFilter }
        });
      },
      { passive: true }
    );
  }

  /** Scroll depth: at most four events per page, ever. */
  function bindScrollDepth() {
    var handler = throttle(function () {
      var doc = document.documentElement;
      var height = doc.scrollHeight - window.innerHeight;
      if (height <= 0) return;
      var percent = Math.min(100, Math.round(((window.scrollY || 0) / height) * 100));

      for (var i = 0; i < SCROLL_BUCKETS.length; i++) {
        var bucket = SCROLL_BUCKETS[i];
        if (percent >= bucket && !firedBuckets[bucket]) {
          firedBuckets[bucket] = true;
          track("scroll_depth", { meta: { depth: bucket } });
        }
      }
    }, 400);

    window.addEventListener("scroll", handler, { passive: true });
  }

  /**
   * Dwell time, counted only while the tab is actually visible.
   *
   * Reported in slices: one at each milestone while the user is still reading
   * (so the Signal panel can show attention accruing live) and the remainder
   * when they leave. Each slice carries only the time since the previous one,
   * so the slices sum to the true total. Milestone slices are flagged, and the
   * backend scores only the final one — otherwise a long read would be worth
   * more than it should purely for having been cut into more pieces.
   */
  function bindDwell() {
    var accumulated = 0;   // visible time so far, in ms
    var reported = 0;      // how much of it we have already sent
    var startedAt = document.visibilityState === "visible" ? Date.now() : null;
    var nextMilestone = 0;
    var timer = null;

    function visibleMs() {
      return accumulated + (startedAt === null ? 0 : Date.now() - startedAt);
    }

    function pause() {
      if (startedAt !== null) {
        accumulated += Date.now() - startedAt;
        startedAt = null;
      }
    }

    function resume() {
      if (startedAt === null) startedAt = Date.now();
    }

    function productId() {
      var el = document.querySelector("[data-product-id][data-track-view]");
      return el ? Number(el.dataset.productId) : null;
    }

    /** Send the unreported slice. `milestone` marks it as non-final. */
    function emit(milestone) {
      var total = visibleMs();
      var slice = total - reported;
      if (slice < 1000) return;            // nothing worth reporting yet
      if (!milestone && total < 2000) return;  // a bounce is not attention
      reported = total;

      enqueue({
        type: "dwell",
        product_id: productId(),
        query: null,
        path: window.location.pathname.slice(0, 300),
        dwell_ms: slice,
        occurred_at: now(),
        meta: { milestone: !!milestone, cumulative_ms: total }
      });
    }

    function scheduleMilestone() {
      if (nextMilestone >= DWELL_MILESTONES_MS.length) return;
      var due = DWELL_MILESTONES_MS[nextMilestone] - visibleMs();
      clearTimeout(timer);
      timer = setTimeout(function () {
        nextMilestone++;
        emit(true);
        scheduleMilestone();
      }, Math.max(250, due));
    }

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") {
        clearTimeout(timer);
        pause();
        emit(false);
        flush(true);
      } else {
        resume();
        scheduleMilestone();
      }
    });

    window.addEventListener("pagehide", function () {
      clearTimeout(timer);
      emit(false);
      flush(true);
    });

    scheduleMilestone();
  }

  // --- public surface -------------------------------------------------------

  window.SmartReco = {
    track: track,
    flush: function () {
      flush(false);
    },
    queueDepth: function () {
      return queue.length;
    },
    /**
     * Observe events as they are recorded, before they are batched and sent.
     * The Signal panel uses this to render a chip the moment something happens
     * rather than waiting on the next flush. Returns an unsubscribe function.
     */
    onEvent: function (fn) {
      if (typeof fn !== "function") return function () {};
      observers.push(fn);
      return function () {
        var i = observers.indexOf(fn);
        if (i > -1) observers.splice(i, 1);
      };
    }
  };

  function init() {
    try {
      trackPageView();
      bindClicks();
      bindSearch();
      bindFilters();
      bindScrollDepth();
      bindDwell();
    } catch (e) {
      /* an instrumentation failure must not take the page with it */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
