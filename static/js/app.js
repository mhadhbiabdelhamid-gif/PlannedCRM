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
})();
