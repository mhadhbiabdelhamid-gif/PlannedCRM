/* Planned Real Estate CRM — small, dependency-free interactions. */

(function () {
  "use strict";

  /* ------------------------------------------------- mobile navigation */
  var burger = document.getElementById("burger");
  var rail = document.getElementById("rail");
  if (burger && rail) {
    burger.addEventListener("click", function () { rail.classList.toggle("open"); });
    document.addEventListener("click", function (e) {
      if (window.innerWidth <= 860 && rail.classList.contains("open") &&
          !rail.contains(e.target) && e.target !== burger && !burger.contains(e.target)) {
        rail.classList.remove("open");
      }
    });
  }

  /* ------------------------------------------ one nav section at a time
     The sections in the left rail are <details name="railsec">, and a shared
     name is all a current browser needs to close the others when one opens.
     Older browsers ignore the attribute and would let every section sit open
     at once, which is the pile-up the grouping exists to avoid — so where the
     attribute isn't supported, do the same thing by hand. */
  if (!("name" in document.createElement("details"))) {
    var sections = document.querySelectorAll(".rail-sec[name]");
    sections.forEach(function (sec) {
      sec.addEventListener("toggle", function () {
        if (!sec.open) return;
        sections.forEach(function (other) {
          if (other !== sec) other.open = false;
        });
      });
    });
  }

  /* -------------------------------------------------- metric hairlines */
  var metrics = document.querySelectorAll(".metric");
  metrics.forEach(function (m, i) {
    setTimeout(function () { m.classList.add("lit"); }, 90 * i);
  });

  /* ------------------------------------------------------------ modals */
  window.openModal = function (id, data) {
    var back = document.getElementById(id);
    if (!back) return;
    if (data) {
      Object.keys(data).forEach(function (k) {
        var el = back.querySelector('[name="' + k + '"]');
        if (el) el.value = data[k] === null ? "" : data[k];
      });
    } else {
      var form = back.querySelector("form");
      if (form) form.reset();
      var hidden = back.querySelector('[name="id"]');
      if (hidden) hidden.value = "";
    }
    var title = back.querySelector("[data-title]");
    if (title) title.textContent = data ? title.dataset.editTitle : title.dataset.title;
    back.classList.add("open");
    var first = back.querySelector("input:not([type=hidden]), select, textarea");
    if (first) first.focus();
  };

  window.closeModal = function (id) {
    var back = document.getElementById(id);
    if (back) back.classList.remove("open");
  };

  document.querySelectorAll(".modal-back").forEach(function (back) {
    back.addEventListener("click", function (e) {
      if (e.target === back) back.classList.remove("open");
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal-back.open").forEach(function (b) {
        b.classList.remove("open");
      });
    }
  });

  /* -------------------------------------------- kanban drag and drop */
  var board = document.getElementById("board");
  if (board) {
    var dragged = null;

    board.querySelectorAll(".lead-card").forEach(function (card) {
      card.setAttribute("draggable", "true");

      card.addEventListener("dragstart", function (e) {
        dragged = card;
        card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", card.dataset.id);
      });

      card.addEventListener("dragend", function () {
        card.classList.remove("dragging");
        board.querySelectorAll(".col").forEach(function (c) {
          c.classList.remove("drop-target");
        });
        dragged = null;
      });
    });

    board.querySelectorAll(".col").forEach(function (col) {
      col.addEventListener("dragover", function (e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        col.classList.add("drop-target");
      });

      col.addEventListener("dragleave", function (e) {
        if (!col.contains(e.relatedTarget)) col.classList.remove("drop-target");
      });

      col.addEventListener("drop", function (e) {
        e.preventDefault();
        col.classList.remove("drop-target");
        if (!dragged) return;

        var stage = col.dataset.stage;
        var origin = dragged.closest(".col");
        if (origin === col) return;

        col.querySelector(".col-body").appendChild(dragged);
        recount();

        fetch("/leads/" + dragged.dataset.id + "/stage", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ stage: stage })
        })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
          .then(function (res) {
            if (!res.ok) {
              origin.querySelector(".col-body").appendChild(dragged);
              recount();
              toast(res.d.error || "Couldn't move that lead.");
            }
          })
          .catch(function () {
            origin.querySelector(".col-body").appendChild(dragged);
            recount();
            toast("Connection lost — the lead stayed where it was.");
          });
      });
    });

    function recount() {
      board.querySelectorAll(".col").forEach(function (col) {
        var n = col.querySelectorAll(".lead-card").length;
        var badge = col.querySelector("[data-count]");
        if (badge) badge.textContent = n;
        var empty = col.querySelector(".col-empty");
        if (empty) empty.style.display = n ? "none" : "block";
      });
    }
  }

  /* ------------------------------------------------------------- toast */
  function toast(message) {
    var el = document.createElement("div");
    el.textContent = message;
    el.style.cssText =
      "position:fixed;left:50%;bottom:28px;transform:translateX(-50%);" +
      "background:#0B0B0D;color:#fff;padding:11px 18px;border-radius:3px;" +
      "font-size:13px;z-index:99;box-shadow:0 10px 30px rgba(0,0,0,.3);" +
      "border-left:3px solid #C8A24A";
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 4000);
  }
  window.toast = toast;

  /* ------------------------------- one submit per form, with feedback */
  document.querySelectorAll("form").forEach(function (form) {
    form.addEventListener("submit", function () {
      if (form.dataset.confirm) return;          // handled below
      var btn = form.querySelector('button[type=submit], button:not([type])');
      if (!btn || btn.dataset.busy) return;
      btn.dataset.busy = "1";
      btn.classList.add("is-busy");
      // a slow import should not look frozen, but a failed post must recover
      setTimeout(function () {
        btn.classList.remove("is-busy");
        delete btn.dataset.busy;
      }, 30000);
    });
  });

  /* --------------------------------------------- confirm before delete */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  /* ------------------------------------------------ keyboard shortcut */
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      var s = document.getElementById("globalsearch");
      if (s) { e.preventDefault(); s.focus(); }
    }
  });

  /* ------------------------------------- expand <details> for printing
     A closed <details> (e.g. a client's collapsed notes/deal history on
     the employee report) renders no content at all when printed, with or
     without the CSS fallback in app.css — so before the print dialog
     opens, force every one open, and put back whichever ones the reader
     had closed once printing is done. Fires for Ctrl+P and the browser's
     own print menu too, not just a page's own "Print" button, since
     beforeprint/afterprint fire regardless of how printing was started. */
  var printOpened = [];
  window.addEventListener("beforeprint", function () {
    printOpened = [];
    document.querySelectorAll("details:not([open]):not(.rail-sec)").forEach(function (d) {
      d.open = true;
      printOpened.push(d);
    });
  });
  window.addEventListener("afterprint", function () {
    printOpened.forEach(function (d) { d.open = false; });
    printOpened = [];
  });
})();

/* ---------------------------------------------- bulk selection on listings */
(function () {
  "use strict";
  var bar = document.getElementById("bulkbar");
  if (!bar) return;

  var form = document.getElementById("bulkform");

  function boxes() {
    return Array.prototype.slice.call(
      document.querySelectorAll('input[name="ids"][form="bulkform"]'));
  }

  window.bulkChanged = function () {
    var picked = boxes().filter(function (b) { return b.checked; });
    document.getElementById("pick-count").textContent = picked.length;
    bar.hidden = picked.length === 0;
    var all = document.getElementById("pick-all");
    all.checked = picked.length > 0 && picked.length === boxes().length;
    all.indeterminate = picked.length > 0 && picked.length < boxes().length;
    refreshGo();
  };

  window.pickAll = function (state) {
    boxes().forEach(function (b) { b.checked = state; });
    window.bulkChanged();
  };

  function refreshGo() {
    var action = document.getElementById("bulk-action").value;
    var picked = boxes().some(function (b) { return b.checked; });
    var needsValue = ["status", "listing_type", "agent", "owner", "partner"]
      .indexOf(action) !== -1;
    var go = document.getElementById("bulk-go");
    go.disabled = !picked || !action;
    go.classList.toggle("btn-danger", action === "delete");
    go.textContent = action === "delete" ? "Delete" : "Apply";
    // only the select that belongs to the chosen action may submit a value
    document.querySelectorAll(".bulk-value").forEach(function (sel) {
      var mine = sel.id === "bulk-" + action;
      sel.hidden = !mine;
      sel.disabled = !mine;
    });
    if (!needsValue) return;
  }

  window.bulkAction = refreshGo;

  form.addEventListener("submit", function (e) {
    var picked = boxes().filter(function (b) { return b.checked; });
    if (!picked.length) { e.preventDefault(); return; }
    if (document.getElementById("bulk-action").value === "delete") {
      var msg = form.dataset.confirmDelete + "\n\n" + picked.length + " selected.";
      if (!window.confirm(msg)) { e.preventDefault(); }
    }
  });

  refreshGo();
})();
