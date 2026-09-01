(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) { try { tg.setHeaderColor("#0A1220"); } catch (e) {} }
    if (tg.setBackgroundColor) { try { tg.setBackgroundColor("#062617"); } catch (e) {} }
  }

  var content = document.getElementById("content");
  var tabbar = document.getElementById("tabbar");
  var activeTab = "fixture";
  var state = null;          // last /api/state payload
  var drafts = {};           // gwId -> { home, away, wildcard } — new predictions being built
  var editDrafts = {};       // gwId -> same shape, for an already-submitted prediction being edited
  var newGwFixtures = null;  // fixtures list while picking a match to add

  function apiFetch(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (tg && tg.initData) headers["X-Telegram-Init-Data"] = tg.initData;
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(path, Object.assign({}, opts, { headers: headers }))
      .then(function (r) {
        return r.json()
          .catch(function () { return { ok: false, message: "Something went wrong on the server (" + r.status + "). Please try again." }; })
          .then(function (j) { return { status: r.status, body: j }; });
      })
      .catch(function () {
        return { status: 0, body: { ok: false, message: "Couldn\u2019t reach the server. Check your connection and try again." } };
      });
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // Small colored initials badge for a team or player name — picked from a
  // fixed palette that matches the pitch/amber theme (not a full color
  // hash) so avatars always feel like they belong on this screen.
  var AVATAR_PALETTE = ["#E29A0E", "#3A8F6B", "#5B7FBF", "#B5654E", "#7C8FA6", "#A6753A"];
  function avatarColor(name) {
    var s = String(name || "");
    var h = 0;
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return AVATAR_PALETTE[h % AVATAR_PALETTE.length];
  }
  function avatarInitials(name) {
    var parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  function avatarHtml(name, size) {
    return '<span class="avatar' + (size === "sm" ? " avatar-sm" : "") + '" style="background:' +
      avatarColor(name) + '">' + esc(avatarInitials(name)) + '</span>';
  }

  function fmtKickoff(iso) {
    try {
      var d = new Date(iso);
      return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    } catch (e) { return iso; }
  }

  // ---------- data loading ----------

  function loadState() {
    return apiFetch("/api/state").then(function (res) {
      state = res.body;
      // Drop drafts for fixtures that are no longer open (finished, or the
      // player navigated away and back) so stale scores don't linger.
      var openIds = {};
      (state.active_gameweeks || []).forEach(function (gw) { openIds[gw.id] = true; });
      Object.keys(drafts).forEach(function (id) { if (!openIds[id]) delete drafts[id]; });
      Object.keys(editDrafts).forEach(function (id) { if (!openIds[id]) delete editDrafts[id]; });
      render();
      renderStatsStrip();
    }).catch(function () {
      content.innerHTML = '<div class="error-banner">Couldn\u2019t reach the server.</div>' +
        '<div class="submit-row"><button class="btn btn-ghost" id="retryBtn">Try again</button></div>';
      var rb = document.getElementById("retryBtn");
      if (rb) rb.addEventListener("click", function () {
        content.innerHTML = '<div class="loading">Reconnecting\u2026</div>';
        loadState();
      });
    });
  }

  var statsStrip = document.getElementById("statsStrip");

  function animateCount(el, to) {
    var from = 0;
    var start = null;
    var dur = 650;
    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(from + (to - from) * eased);
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function renderStatsStrip() {
    if (!statsStrip) return;
    if (state.setup_needed || !state.players.length) { statsStrip.innerHTML = ""; return; }
    var mine = state.me && state.leaderboard.find(function (r) { return r.name === state.me.name; });
    var rank = mine ? state.leaderboard.indexOf(mine) + 1 : null;
    var openCount = (state.active_gameweeks || []).length;
    statsStrip.innerHTML =
      '<div class="stat"><span class="stat-value">' + (rank ? "#" + rank : "\u2013") + '</span><span class="stat-label">Your rank</span></div>' +
      '<div class="stat-sep"></div>' +
      '<div class="stat"><span class="stat-value" id="statPoints">0</span><span class="stat-label">Your points</span></div>' +
      '<div class="stat-sep"></div>' +
      '<div class="stat"><span class="stat-value">' + state.players.length + '</span><span class="stat-label">Players</span></div>' +
      '<div class="stat-sep"></div>' +
      '<div class="stat"><span class="stat-value">' + openCount + '</span><span class="stat-label">Open</span></div>';
    var ptsEl = document.getElementById("statPoints");
    if (ptsEl) animateCount(ptsEl, mine ? mine.total : 0);
  }

  // ---------- tab switching (tap or swipe) ----------

  var TAB_ORDER = ["fixture", "table", "history"];

  function goToTab(tab) {
    if (tab === activeTab) return;
    activeTab = tab;
    Array.prototype.forEach.call(tabbar.querySelectorAll(".tab"), function (t) {
      t.classList.toggle("active", t.dataset.tab === tab);
    });
    content.classList.remove("content-enter");
    void content.offsetWidth; // restart the transition on rapid switches
    content.classList.add("content-enter");
    render();
  }

  tabbar.addEventListener("click", function (e) {
    var btn = e.target.closest(".tab");
    if (!btn) return;
    goToTab(btn.dataset.tab);
  });

  (function setupSwipe() {
    var startX = null, startY = null, tracking = false;
    content.addEventListener("touchstart", function (e) {
      if (e.touches.length !== 1) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      tracking = true;
    }, { passive: true });
    content.addEventListener("touchend", function (e) {
      if (!tracking || startX === null) return;
      tracking = false;
      var dx = e.changedTouches[0].clientX - startX;
      var dy = e.changedTouches[0].clientY - startY;
      if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      var idx = TAB_ORDER.indexOf(activeTab);
      if (dx < 0 && idx < TAB_ORDER.length - 1) goToTab(TAB_ORDER[idx + 1]);
      else if (dx > 0 && idx > 0) goToTab(TAB_ORDER[idx - 1]);
    }, { passive: true });
  })();

  // ---------- render ----------

  function render() {
    if (!state) return;
    if (activeTab === "fixture") renderFixture();
    else if (activeTab === "table") renderTable();
    else renderHistory();
  }

  // Renders one team-per-row score picker: team name on the left, that
  // team's own [-][score][+] on the right, right next to it. `gwId` +
  // `which` ("new" or "edit") namespace the element ids and the
  // data-attributes used for delegated click handling, so the same markup
  // can appear in several fixture cards on screen at once.
  function scoreEntryMarkup(homeName, awayName, home, away, gwId, which) {
    var idBase = which + gwId;
    return '<div class="score-entry">' +
      '<div class="score-team-row">' +
        '<span class="score-team-name">' + esc(homeName) + '</span>' +
        '<div class="score-control">' +
          '<button class="step-btn" data-action="step" data-gw="' + gwId + '" data-which="' + which + '" data-adj="h-1">\u2212</button>' +
          '<span class="score-box" id="' + idBase + 'HomeScore">' + home + '</span>' +
          '<button class="step-btn" data-action="step" data-gw="' + gwId + '" data-which="' + which + '" data-adj="h+1">+</button>' +
        '</div>' +
      '</div>' +
      '<div class="score-team-row">' +
        '<span class="score-team-name">' + esc(awayName) + '</span>' +
        '<div class="score-control">' +
          '<button class="step-btn" data-action="step" data-gw="' + gwId + '" data-which="' + which + '" data-adj="a-1">\u2212</button>' +
          '<span class="score-box" id="' + idBase + 'AwayScore">' + away + '</span>' +
          '<button class="step-btn" data-action="step" data-gw="' + gwId + '" data-which="' + which + '" data-adj="a+1">+</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  // What to show below the predictions once I've already predicted this
  // fixture: a way to ask for an edit, or the state of a pending/approved
  // edit request (mine or the other player's).
  function editControlsHtml(gwId, me, myPred, editReq) {
    if (!me || !myPred) return "";
    if (editReq) {
      if (editReq.status === "pending") {
        if (editReq.requester_id === me.telegram_id) {
          return '<div class="edit-banner">\u23f3 Waiting on the other player to approve your edit request.</div>';
        }
        return '<div class="edit-banner">\u270f\ufe0f The other player wants to change their prediction.</div>' +
          '<div class="edit-row"><button class="btn btn-ghost btn-small" data-action="approve-edit" data-gw="' + gwId + '">Approve edit request</button></div>';
      }
      if (editReq.status === "approved" && editReq.requester_id !== me.telegram_id) {
        return '<div class="edit-banner">\u23f3 Waiting on the other player to submit their new score.</div>';
      }
      return "";
    }
    return '<div class="edit-row"><button class="btn btn-ghost btn-small" data-action="request-edit" data-gw="' + gwId + '">Request to edit my prediction</button></div>';
  }

  // Builds the markup for a single open fixture card.
  function fixtureCardHtml(gw) {
    if (drafts[gw.id] == null) drafts[gw.id] = { home: 0, away: 0, wildcard: false };
    var draft = drafts[gw.id];

    var me = state.me;
    var myTurn = gw.allowed_telegram_id != null && me && me.telegram_id === gw.allowed_telegram_id;
    var waitingOn = null;
    if (gw.allowed_telegram_id != null) {
      var p = state.players.find(function (pl) { return pl.telegram_id === gw.allowed_telegram_id; });
      waitingOn = p ? p.name : null;
    }

    var myPred = me ? gw.predictions.find(function (pr) { return pr.telegram_id === me.telegram_id; }) : null;
    var editReq = gw.edit_request;
    var iAmApprovedEditor = !!(editReq && editReq.status === "approved" && me && editReq.requester_id === me.telegram_id);

    // Once kickoff passes without both predictions in, this fixture is stuck
    // — nobody can submit or edit anymore, no matter whose "turn" it
    // technically was. Show that plainly instead of a stepper that just
    // fails silently (or near-silently) when tapped.
    var kickoffPassed = false;
    try { kickoffPassed = new Date(gw.kickoff).getTime() <= Date.now(); } catch (e) {}
    var missed = kickoffPassed && gw.status === "awaiting_predictions";
    if (missed) { myTurn = false; iAmApprovedEditor = false; }

    if (iAmApprovedEditor) {
      if (editDrafts[gw.id] == null) {
        editDrafts[gw.id] = {
          home: myPred ? myPred.home : 0,
          away: myPred ? myPred.away : 0,
          wildcard: myPred ? myPred.wildcard : false,
        };
      }
    } else {
      delete editDrafts[gw.id];
    }
    var editDraft = editDrafts[gw.id];

    var predsHtml = gw.predictions.map(function (p) {
      return '<div class="history-pred"><span>' + avatarHtml(p.name, "sm") + esc(p.name) + (p.wildcard ? " \ud83c\udfb4" : "") +
        '</span><span>' + p.home + "-" + p.away + "</span></div>";
    }).join("");

    var html = '<div class="card' + (missed ? ' card-missed' : '') + ((myTurn || iAmApprovedEditor) ? ' card-turn' : '') + '">';
    html += '<div class="scoreboard">';
    html += '<div class="scoreboard-header"><span class="gw">GW ' + esc(gw.gw_number) + '</span><span>' +
      (missed ? '<span class="missed-tag">Missed</span> ' : (myTurn || iAmApprovedEditor) ? '<span class="turn-tag">Your move</span> ' : '') +
      esc(fmtKickoff(gw.kickoff)) + '</span></div>';

    if (myTurn) {
      html += scoreEntryMarkup(gw.home, gw.away, draft.home, draft.away, gw.id, "new");
    } else if (iAmApprovedEditor) {
      html += scoreEntryMarkup(gw.home, gw.away, editDraft.home, editDraft.away, gw.id, "edit");
    } else {
      html += '<div class="scoreline">' +
        '<span class="team home">' + esc(gw.home) + avatarHtml(gw.home) + '</span>' +
        '<span class="score-sep">vs</span>' +
        '<span class="team away">' + avatarHtml(gw.away) + esc(gw.away) + '</span>' +
        '</div>';
    }
    html += '</div>'; // .scoreboard

    if (myTurn) {
      html += '<div class="perforation"></div>';
      html += '<div class="wildcard-row"><div class="wildcard-label">\ud83c\udfb2 Wildcard<small>Doubles whatever points you earn</small></div>' +
        '<button class="toggle' + (draft.wildcard ? ' on' : '') + '" data-action="toggle-wc" data-gw="' + gw.id + '" data-which="new"></button></div>';
      html += '<div class="submit-row"><button class="btn btn-primary" data-action="submit" data-gw="' + gw.id + '">Submit prediction</button></div>';
    } else if (iAmApprovedEditor) {
      html += '<div class="perforation"></div>';
      html += '<div class="wildcard-row"><div class="wildcard-label">\ud83c\udfb2 Wildcard<small>Doubles whatever points you earn</small></div>' +
        '<button class="toggle' + (editDraft.wildcard ? ' on' : '') + '" data-action="toggle-wc" data-gw="' + gw.id + '" data-which="edit"></button></div>';
      html += '<div class="submit-row"><button class="btn btn-primary" data-action="submit-edit" data-gw="' + gw.id + '">Save new prediction</button></div>';
    } else if (missed) {
      html += '<div class="turn-banner missed">\u23f1\ufe0f Kickoff has passed \u2014 this fixture can no longer be predicted.</div>';
      html += '<div style="padding:12px 16px 18px">' + (predsHtml || '<div class="status-line">Nobody predicted this one in time.</div>') + '</div>';
      html += '<div class="submit-row"><button class="btn btn-ghost" data-action="resolve-missed" data-gw="' + gw.id + '">Score as missed (0 pts) &amp; close</button></div>';
    } else if (waitingOn) {
      html += '<div class="turn-banner">Waiting on ' + esc(waitingOn) + '</div>';
      html += '<div style="padding:12px 16px 18px">' + (predsHtml || '<div class="status-line">No predictions yet.</div>') + '</div>';
      html += editControlsHtml(gw.id, me, myPred, editReq);
    } else {
      html += '<div class="turn-banner">Both predictions are in \u2014 waiting on full time.</div>';
      html += '<div style="padding:0 16px 8px">' + predsHtml + '</div>';
      html += editControlsHtml(gw.id, me, myPred, editReq);
      html += '<div class="submit-row"><button class="btn btn-ghost" data-action="check-result" data-gw="' + gw.id + '">Check result now</button></div>';
    }

    html += '</div>'; // .card
    return html;
  }

  function renderFixture() {
    if (state.setup_needed) {
      content.innerHTML = '<div class="empty empty-setup">No game set up yet.<br>Send <b>/start</b> to the bot in your group to get going.</div>';
      return;
    }

    if (newGwFixtures) {
      renderFixtureList();
      return;
    }

    var gws = state.active_gameweeks || [];

    if (!gws.length) {
      content.innerHTML =
        '<div class="empty">No active fixture right now.</div>' +
        (state.players.length < 2
          ? '<div class="status-line">Need 2 registered players \u2014 send /start to the bot.</div>'
          : '<button class="btn btn-primary" id="startGwBtn">Start a new gameweek</button>');
      var b = document.getElementById("startGwBtn");
      if (b) b.addEventListener("click", startNewGameweek);
      return;
    }

    // Fixtures still worth acting on come first; ones whose kickoff passed
    // without a full set of predictions sink to the bottom so they don't
    // bury what's actually predictable right now.
    function isMissed(gw) {
      var passed = false;
      try { passed = new Date(gw.kickoff).getTime() <= Date.now(); } catch (e) {}
      return passed && gw.status === "awaiting_predictions";
    }
    var sortedGws = gws.filter(function (g) { return !isMissed(g); })
      .concat(gws.filter(isMissed));

    var openCount = sortedGws.length;
    var head = '<div class="section-head"><span class="label">This gameweek</span><span class="count">' +
      openCount + (openCount === 1 ? ' fixture' : ' fixtures') + '</span></div>';
    var html = head + sortedGws.map(fixtureCardHtml).join("");
    if (state.players.length >= 2) {
      html += '<div class="submit-row"><button class="btn btn-ghost" id="addFixtureBtn">+ Predict another fixture</button></div>';
    }
    content.innerHTML = html;

    var addBtn = document.getElementById("addFixtureBtn");
    if (addBtn) addBtn.addEventListener("click", startNewGameweek);
  }

  // Single delegated listener for every button inside a fixture card,
  // across however many cards are on screen — avoids re-binding a fresh
  // set of handlers per card on every render.
  content.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-action]");
    if (!btn) return;
    var action = btn.dataset.action;
    var gwId = btn.dataset.gw ? Number(btn.dataset.gw) : null;

    if (action === "step") {
      var draftObj = btn.dataset.which === "edit" ? editDrafts[gwId] : drafts[gwId];
      if (!draftObj) return;
      var adj = btn.dataset.adj;
      if (adj === "h-1") draftObj.home = Math.max(0, draftObj.home - 1);
      if (adj === "h+1") draftObj.home = Math.min(state.max_score, draftObj.home + 1);
      if (adj === "a-1") draftObj.away = Math.max(0, draftObj.away - 1);
      if (adj === "a+1") draftObj.away = Math.min(state.max_score, draftObj.away + 1);
      var idBase = btn.dataset.which + gwId;
      var homeEl = document.getElementById(idBase + "HomeScore");
      var awayEl = document.getElementById(idBase + "AwayScore");
      homeEl.textContent = draftObj.home;
      awayEl.textContent = draftObj.away;
      var changed = adj[0] === "h" ? homeEl : awayEl;
      changed.classList.remove("pop");
      void changed.offsetWidth; // restart the animation on repeated taps
      changed.classList.add("pop");
    } else if (action === "toggle-wc") {
      var d = btn.dataset.which === "edit" ? editDrafts[gwId] : drafts[gwId];
      if (!d) return;
      d.wildcard = !d.wildcard;
      btn.classList.toggle("on", d.wildcard);
    } else if (action === "submit") {
      submitPrediction(gwId, btn);
    } else if (action === "submit-edit") {
      submitEdit(gwId, btn);
    } else if (action === "request-edit") {
      requestEdit(gwId, btn);
    } else if (action === "approve-edit") {
      approveEdit(gwId, btn);
    } else if (action === "check-result") {
      checkResult(gwId, btn);
    } else if (action === "resolve-missed") {
      resolveMissed(gwId, btn);
    }
  });

  function submitPrediction(gwId, btn) {
    var draft = drafts[gwId];
    if (!draft) return;
    if (btn) { btn.disabled = true; btn.textContent = "Submitting\u2026"; }
    apiFetch("/api/predict", {
      method: "POST",
      body: JSON.stringify({ gw_id: gwId, home: draft.home, away: draft.away, wildcard: draft.wildcard }),
    }).then(function (res) {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred(res.body.ok ? "success" : "error");
      delete drafts[gwId];
      if (res.body.ok && btn) {
        btn.textContent = "\u2713 Locked in";
        btn.classList.add("btn-success");
        setTimeout(loadState, 420);
      } else {
        loadState();
      }
      if (!res.body.ok && tg) tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message);
    });
  }

  function requestEdit(gwId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Requesting\u2026"; }
    apiFetch("/api/requestedit", { method: "POST", body: JSON.stringify({ gw_id: gwId }) }).then(function (res) {
      if (!res.body.ok && tg) { tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message); }
      loadState();
    });
  }

  function approveEdit(gwId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Approving\u2026"; }
    apiFetch("/api/approveedit", { method: "POST", body: JSON.stringify({ gw_id: gwId }) }).then(function (res) {
      if (!res.body.ok && tg) { tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message); }
      loadState();
    });
  }

  function submitEdit(gwId, btn) {
    var editDraft = editDrafts[gwId];
    if (!editDraft) return;
    if (btn) { btn.disabled = true; btn.textContent = "Saving\u2026"; }
    apiFetch("/api/editpredict", {
      method: "POST",
      body: JSON.stringify({ gw_id: gwId, home: editDraft.home, away: editDraft.away, wildcard: editDraft.wildcard }),
    }).then(function (res) {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred(res.body.ok ? "success" : "error");
      delete editDrafts[gwId];
      if (res.body.ok && btn) {
        btn.textContent = "\u2713 Saved";
        btn.classList.add("btn-success");
        setTimeout(loadState, 420);
      } else {
        loadState();
      }
      if (!res.body.ok && tg) tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message);
    });
  }

  function checkResult(gwId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Checking\u2026"; }
    apiFetch("/api/results", { method: "POST", body: JSON.stringify({ gw_id: gwId }) }).then(function () { loadState(); });
  }

  function resolveMissed(gwId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Closing\u2026"; }
    apiFetch("/api/resolvemissed", { method: "POST", body: JSON.stringify({ gw_id: gwId }) }).then(function (res) {
      if (!res.body.ok) {
        if (tg && tg.showAlert) tg.showAlert(res.body.message); else alert(res.body.message);
      }
      loadState();
    });
  }

  function startNewGameweek() {
    var btn = document.getElementById("startGwBtn") || document.getElementById("addFixtureBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Fetching fixtures\u2026"; }
    apiFetch("/api/newgameweek").then(function (res) {
      if (!res.body.ok) {
        content.innerHTML = '<div class="error-banner">' + esc(res.body.message) + '</div>';
        return;
      }
      newGwFixtures = res.body;
      renderFixtureList();
    });
  }

  function renderFixtureList() {
    if (!newGwFixtures || !newGwFixtures.fixtures.length) {
      content.innerHTML = '<div class="empty empty-setup">No more fixtures to add for that matchday.</div>' +
        '<div class="submit-row"><button class="btn btn-ghost" id="cancelAddBtn">Back</button></div>';
      var cb0 = document.getElementById("cancelAddBtn");
      if (cb0) cb0.addEventListener("click", function () { newGwFixtures = null; render(); });
      return;
    }
    var rows = newGwFixtures.fixtures.map(function (f, i) {
      return '<button class="fixture-row" data-idx="' + i + '">' +
        (i === 0 ? '<span class="next-tag">Next</span>' : "") +
        (f.home_crest ? '<img src="' + esc(f.home_crest) + '">' : "") +
        '<span>' + esc(f.home) + '</span><span class="vs">vs</span><span>' + esc(f.away) + '</span>' +
        (f.away_crest ? '<img src="' + esc(f.away_crest) + '">' : "") +
        '<span class="kickoff">' + esc(fmtKickoff(f.kickoff)) + '</span>' +
        '<span class="row-chevron">\u203a</span>' +
        '</button>';
    }).join("");
    content.innerHTML =
      '<div class="section-head"><span class="label">Matchday ' + esc(newGwFixtures.matchday) +
      '</span><span class="count">tap to lock in</span></div>' +
      '<div class="card">' + rows + '</div>' +
      '<div class="submit-row"><button class="btn btn-ghost" id="cancelAddBtn">Cancel</button></div>';
    Array.prototype.forEach.call(content.querySelectorAll(".fixture-row"), function (btn) {
      btn.addEventListener("click", function () { lockMatch(newGwFixtures.fixtures[+btn.dataset.idx]); });
    });
    var cb = document.getElementById("cancelAddBtn");
    if (cb) cb.addEventListener("click", function () { newGwFixtures = null; render(); });
  }

  function lockMatch(f) {
    apiFetch("/api/lockmatch", {
      method: "POST",
      body: JSON.stringify({
        matchday: newGwFixtures.matchday, match_id: f.match_id,
        home: f.home, away: f.away, kickoff: f.kickoff,
      }),
    }).then(function (res) {
      if (!res.body.ok) {
        if (tg && tg.showAlert) tg.showAlert(res.body.message); else alert(res.body.message);
        return;
      }
      newGwFixtures = null;
      loadState();
    });
  }

  function renderTable() {
    if (!state.leaderboard.length) {
      content.innerHTML = '<div class="section-head"><span class="label">League table</span></div>' +
        '<div class="empty empty-trophy">No points on the board yet.</div>';
      return;
    }
    var top = state.leaderboard[0].total || 1;
    var rows = state.leaderboard.map(function (r, i) {
      var rankClass = i < 3 ? " rank-" + (i + 1) : "";
      var pct = Math.max(4, Math.round((r.total / top) * 100));
      return '<div class="standing-row' + rankClass + '">' +
        '<span class="standing-rank">' + (i + 1) + '</span>' +
        avatarHtml(r.name) +
        '<span class="standing-main"><span class="standing-name">' + esc(r.name) + '</span>' +
        '<span class="standing-bar-track"><span class="standing-bar-fill" data-pct="' + pct + '" style="width:0%"></span></span></span>' +
        '<span class="standing-pts"><span class="pts-count" data-target="' + r.total + '">0</span><small>pts</small></span>' +
        '</div>';
    }).join("");
    content.innerHTML = '<div class="section-head"><span class="label">League table</span></div>' +
      '<div class="card">' + rows + '</div>';
    requestAnimationFrame(function () {
      Array.prototype.forEach.call(content.querySelectorAll(".standing-bar-fill"), function (el) {
        el.style.width = el.dataset.pct + "%";
      });
    });
    Array.prototype.forEach.call(content.querySelectorAll(".pts-count"), function (el) {
      animateCount(el, +el.dataset.target);
    });
  }

  function renderHistory() {
    if (!state.history.length) {
      content.innerHTML = '<div class="section-head"><span class="label">Match log</span></div>' +
        '<div class="empty empty-history">No finished gameweeks yet.</div>';
      return;
    }
    var rows = state.history.map(function (h) {
      var preds = h.predictions.map(function (p) {
        return '<div class="history-pred"><span>' + avatarHtml(p.name, "sm") + esc(p.name) + (p.wildcard ? " \ud83c\udfb4" : "") +
          '</span><span>' + p.home + "-" + p.away + ' <span class="pts">+' + p.points + '</span></span></div>';
      }).join("");
      return '<div class="history-gw">' +
        '<div class="gw-title"><span>GW ' + esc(h.gw_number) + '</span><span class="ft-badge">FT</span></div>' +
        '<div class="gw-score">' + esc(h.home) + " " + h.actual_home + "\u2013" + h.actual_away + " " + esc(h.away) + '</div>' +
        preds +
        '</div>';
    }).join("");
    content.innerHTML = '<div class="section-head"><span class="label">Match log</span><span class="count">' +
      state.history.length + ' finished</span></div><div class="card">' + rows + '</div>';
  }

  loadState();
})();
