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
  var draft = null;          // { gwId, home, away, wildcard } — the score being built
  var editDraft = null;      // same shape, but for an already-submitted prediction being edited
  var newGwFixtures = null;  // fixtures list while picking a match

  function apiFetch(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (tg && tg.initData) headers["X-Telegram-Init-Data"] = tg.initData;
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(path, Object.assign({}, opts, { headers: headers }))
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); });
  }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
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
      render();
    }).catch(function () {
      content.innerHTML = '<div class="error-banner">Couldn\u2019t reach the server. Pull down to retry, or reopen the app.</div>';
    });
  }

  // ---------- tab switching ----------

  tabbar.addEventListener("click", function (e) {
    var btn = e.target.closest(".tab");
    if (!btn) return;
    activeTab = btn.dataset.tab;
    Array.prototype.forEach.call(tabbar.querySelectorAll(".tab"), function (t) {
      t.classList.toggle("active", t === btn);
    });
    render();
  });

  // ---------- render ----------

  function render() {
    if (!state) return;
    if (activeTab === "fixture") renderFixture();
    else if (activeTab === "table") renderTable();
    else renderHistory();
  }

  // Renders one team-per-row score picker: team name on the left, that
  // team's own [-][score][+] on the right, right next to it. Using
  // `prefix` to namespace element ids/data lets the same markup serve both
  // the "new prediction" draft and the "editing an existing one" draft.
  function scoreEntryMarkup(homeName, awayName, home, away, prefix) {
    return '<div class="score-entry">' +
      '<div class="score-team-row">' +
        '<span class="score-team-name">' + esc(homeName) + '</span>' +
        '<div class="score-control">' +
          '<button class="step-btn" data-adj="h-1" data-prefix="' + prefix + '">\u2212</button>' +
          '<span class="score-box" id="' + prefix + 'HomeScore">' + home + '</span>' +
          '<button class="step-btn" data-adj="h+1" data-prefix="' + prefix + '">+</button>' +
        '</div>' +
      '</div>' +
      '<div class="score-team-row">' +
        '<span class="score-team-name">' + esc(awayName) + '</span>' +
        '<div class="score-control">' +
          '<button class="step-btn" data-adj="a-1" data-prefix="' + prefix + '">\u2212</button>' +
          '<span class="score-box" id="' + prefix + 'AwayScore">' + away + '</span>' +
          '<button class="step-btn" data-adj="a+1" data-prefix="' + prefix + '">+</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function attachScoreEntryHandlers(prefix, draftObj) {
    Array.prototype.forEach.call(content.querySelectorAll('[data-prefix="' + prefix + '"]'), function (btn) {
      btn.addEventListener("click", function () {
        var adj = btn.dataset.adj;
        if (adj === "h-1") draftObj.home = Math.max(0, draftObj.home - 1);
        if (adj === "h+1") draftObj.home = Math.min(state.max_score, draftObj.home + 1);
        if (adj === "a-1") draftObj.away = Math.max(0, draftObj.away - 1);
        if (adj === "a+1") draftObj.away = Math.min(state.max_score, draftObj.away + 1);
        document.getElementById(prefix + "HomeScore").textContent = draftObj.home;
        document.getElementById(prefix + "AwayScore").textContent = draftObj.away;
      });
    });
  }

  // What to show below the predictions once I've already predicted this
  // gameweek: a way to ask for an edit, or the state of a pending/approved
  // edit request (mine or the other player's).
  function editControlsHtml(me, myPred, editReq) {
    if (!me || !myPred) return "";
    if (editReq) {
      if (editReq.status === "pending") {
        if (editReq.requester_id === me.telegram_id) {
          return '<div class="edit-banner">Waiting on the other player to approve your edit request.</div>';
        }
        return '<div class="edit-banner">The other player wants to change their prediction.</div>' +
          '<div class="edit-row"><button class="btn btn-ghost btn-small" id="approveEditBtn">Approve edit request</button></div>';
      }
      if (editReq.status === "approved" && editReq.requester_id !== me.telegram_id) {
        return '<div class="edit-banner">Waiting on the other player to submit their new score.</div>';
      }
      return "";
    }
    return '<div class="edit-row"><button class="btn btn-ghost btn-small" id="requestEditBtn">Request to edit my prediction</button></div>';
  }

  function renderFixture() {
    if (state.setup_needed) {
      content.innerHTML = '<div class="empty">No game set up yet.<br>Send <b>/start</b> to the bot in your group to get going.</div>';
      return;
    }

    var gw = state.active_gameweek;
    if (!gw) {
      newGwFixtures = null;
      editDraft = null;
      content.innerHTML =
        '<div class="empty">No active fixture right now.</div>' +
        (state.players.length < 2
          ? '<div class="status-line">Need 2 registered players \u2014 send /start to the bot.</div>'
          : '<button class="btn btn-primary" id="startGwBtn">Start a new gameweek</button>');
      var b = document.getElementById("startGwBtn");
      if (b) b.addEventListener("click", startNewGameweek);
      return;
    }

    if (draft == null || draft.gwId !== gw.id) {
      draft = { gwId: gw.id, home: 0, away: 0, wildcard: false };
    }

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

    if (iAmApprovedEditor) {
      if (editDraft == null || editDraft.gwId !== gw.id) {
        editDraft = {
          gwId: gw.id,
          home: myPred ? myPred.home : 0,
          away: myPred ? myPred.away : 0,
          wildcard: myPred ? myPred.wildcard : false,
        };
      }
    } else {
      editDraft = null;
    }

    var predsHtml = gw.predictions.map(function (p) {
      return '<div class="history-pred"><span>' + esc(p.name) + (p.wildcard ? " \ud83c\udfb4" : "") +
        '</span><span>' + p.home + "-" + p.away + "</span></div>";
    }).join("");

    var html = '<div class="card">';
    html += '<div class="scoreboard">';
    html += '<div class="scoreboard-header"><span class="gw">GW ' + esc(gw.gw_number) + '</span><span>' + esc(fmtKickoff(gw.kickoff)) + '</span></div>';

    if (myTurn) {
      html += scoreEntryMarkup(gw.home, gw.away, draft.home, draft.away, "new");
    } else if (iAmApprovedEditor) {
      html += scoreEntryMarkup(gw.home, gw.away, editDraft.home, editDraft.away, "edit");
    } else {
      html += '<div class="scoreline">' +
        '<span class="team home">' + esc(gw.home) + '</span>' +
        '<span class="score-sep">vs</span>' +
        '<span class="team away">' + esc(gw.away) + '</span>' +
        '</div>';
    }
    html += '</div>'; // .scoreboard

    if (myTurn) {
      html += '<div class="perforation"></div>';
      html += '<div class="wildcard-row"><div class="wildcard-label">Wildcard<small>Doubles whatever points you earn</small></div>' +
        '<button class="toggle' + (draft.wildcard ? ' on' : '') + '" id="wcToggle"></button></div>';
      html += '<div class="submit-row"><button class="btn btn-primary" id="submitBtn">Submit prediction</button></div>';
    } else if (iAmApprovedEditor) {
      html += '<div class="perforation"></div>';
      html += '<div class="wildcard-row"><div class="wildcard-label">Wildcard<small>Doubles whatever points you earn</small></div>' +
        '<button class="toggle' + (editDraft.wildcard ? ' on' : '') + '" id="editWcToggle"></button></div>';
      html += '<div class="submit-row"><button class="btn btn-primary" id="editSubmitBtn">Save new prediction</button></div>';
    } else if (waitingOn) {
      html += '<div class="turn-banner">Waiting on ' + esc(waitingOn) + '</div>';
      html += '<div style="padding:12px 16px 18px">' + (predsHtml || '<div class="status-line">No predictions yet.</div>') + '</div>';
      html += editControlsHtml(me, myPred, editReq);
    } else {
      html += '<div class="turn-banner">Both predictions are in \u2014 waiting on full time.</div>';
      html += '<div style="padding:0 16px 8px">' + predsHtml + '</div>';
      html += editControlsHtml(me, myPred, editReq);
      html += '<div class="submit-row"><button class="btn btn-ghost" id="checkResultBtn">Check result now</button></div>';
    }

    html += '</div>'; // .card

    content.innerHTML = html;

    if (myTurn) {
      attachScoreEntryHandlers("new", draft);
      var wc = document.getElementById("wcToggle");
      if (wc) wc.addEventListener("click", function () {
        draft.wildcard = !draft.wildcard;
        wc.classList.toggle("on", draft.wildcard);
      });
      var sb = document.getElementById("submitBtn");
      if (sb) sb.addEventListener("click", submitPrediction);
    } else if (iAmApprovedEditor) {
      attachScoreEntryHandlers("edit", editDraft);
      var ewc = document.getElementById("editWcToggle");
      if (ewc) ewc.addEventListener("click", function () {
        editDraft.wildcard = !editDraft.wildcard;
        ewc.classList.toggle("on", editDraft.wildcard);
      });
      var esb = document.getElementById("editSubmitBtn");
      if (esb) esb.addEventListener("click", submitEdit);
    }

    var crb = document.getElementById("checkResultBtn");
    if (crb) crb.addEventListener("click", checkResult);
    var reqBtn = document.getElementById("requestEditBtn");
    if (reqBtn) reqBtn.addEventListener("click", requestEdit);
    var appBtn = document.getElementById("approveEditBtn");
    if (appBtn) appBtn.addEventListener("click", approveEdit);
  }

  function submitPrediction() {
    var sb = document.getElementById("submitBtn");
    if (sb) { sb.disabled = true; sb.textContent = "Submitting\u2026"; }
    apiFetch("/api/predict", {
      method: "POST",
      body: JSON.stringify({ home: draft.home, away: draft.away, wildcard: draft.wildcard }),
    }).then(function (res) {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred(res.body.ok ? "success" : "error");
      draft = null;
      loadState();
      if (!res.body.ok && tg) tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message);
    });
  }

  function requestEdit() {
    var btn = document.getElementById("requestEditBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Requesting\u2026"; }
    apiFetch("/api/requestedit", { method: "POST" }).then(function (res) {
      if (!res.body.ok && tg) { tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message); }
      loadState();
    });
  }

  function approveEdit() {
    var btn = document.getElementById("approveEditBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Approving\u2026"; }
    apiFetch("/api/approveedit", { method: "POST" }).then(function (res) {
      if (!res.body.ok && tg) { tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message); }
      loadState();
    });
  }

  function submitEdit() {
    var sb = document.getElementById("editSubmitBtn");
    if (sb) { sb.disabled = true; sb.textContent = "Saving\u2026"; }
    apiFetch("/api/editpredict", {
      method: "POST",
      body: JSON.stringify({ home: editDraft.home, away: editDraft.away, wildcard: editDraft.wildcard }),
    }).then(function (res) {
      if (tg && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred(res.body.ok ? "success" : "error");
      editDraft = null;
      loadState();
      if (!res.body.ok && tg) tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message);
    });
  }

  function checkResult() {
    var crb = document.getElementById("checkResultBtn");
    if (crb) { crb.disabled = true; crb.textContent = "Checking\u2026"; }
    apiFetch("/api/results", { method: "POST" }).then(function () { loadState(); });
  }

  function startNewGameweek() {
    var btn = document.getElementById("startGwBtn");
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
      content.innerHTML = '<div class="empty">No fixtures found for that matchday.</div>';
      return;
    }
    var rows = newGwFixtures.fixtures.map(function (f, i) {
      return '<button class="fixture-row" data-idx="' + i + '">' +
        (f.home_crest ? '<img src="' + esc(f.home_crest) + '">' : "") +
        '<span>' + esc(f.home) + '</span><span class="vs">vs</span><span>' + esc(f.away) + '</span>' +
        (f.away_crest ? '<img src="' + esc(f.away_crest) + '">' : "") +
        '<span class="kickoff">' + esc(fmtKickoff(f.kickoff)) + '</span>' +
        '</button>';
    }).join("");
    content.innerHTML =
      '<div class="status-line">Matchday ' + esc(newGwFixtures.matchday) + ' \u2014 tap a fixture to lock it in</div>' +
      '<div class="card">' + rows + '</div>';
    Array.prototype.forEach.call(content.querySelectorAll(".fixture-row"), function (btn) {
      btn.addEventListener("click", function () { lockMatch(newGwFixtures.fixtures[+btn.dataset.idx]); });
    });
  }

  function lockMatch(f) {
    apiFetch("/api/lockmatch", {
      method: "POST",
      body: JSON.stringify({
        matchday: newGwFixtures.matchday, match_id: f.match_id,
        home: f.home, away: f.away, kickoff: f.kickoff,
      }),
    }).then(function (res) {
      newGwFixtures = null;
      if (!res.body.ok && tg) { tg.showAlert ? tg.showAlert(res.body.message) : alert(res.body.message); }
      loadState();
    });
  }

  function renderTable() {
    if (!state.leaderboard.length) {
      content.innerHTML = '<div class="empty">No points on the board yet.</div>';
      return;
    }
    var rows = state.leaderboard.map(function (r, i) {
      return '<div class="standing-row' + (i === 0 && r.total > 0 ? " leader" : "") + '">' +
        '<span class="standing-rank">' + (i + 1) + '</span>' +
        '<span class="standing-name">' + esc(r.name) + '</span>' +
        '<span class="standing-pts">' + r.total + '<small>pts</small></span>' +
        '</div>';
    }).join("");
    content.innerHTML = '<div class="card">' + rows + '</div>';
  }

  function renderHistory() {
    if (!state.history.length) {
      content.innerHTML = '<div class="empty">No finished gameweeks yet.</div>';
      return;
    }
    var rows = state.history.map(function (h) {
      var preds = h.predictions.map(function (p) {
        return '<div class="history-pred"><span>' + esc(p.name) + (p.wildcard ? " \ud83c\udfb4" : "") +
          '</span><span>' + p.home + "-" + p.away + ' <span class="pts">+' + p.points + '</span></span></div>';
      }).join("");
      return '<div class="history-gw">' +
        '<div class="gw-title">GW ' + esc(h.gw_number) + '</div>' +
        '<div class="gw-score">' + esc(h.home) + " " + h.actual_home + "\u2013" + h.actual_away + " " + esc(h.away) + '</div>' +
        preds +
        '</div>';
    }).join("");
    content.innerHTML = '<div class="card">' + rows + '</div>';
  }

  loadState();
})();
