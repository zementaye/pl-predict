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

  function renderFixture() {
    if (state.setup_needed) {
      content.innerHTML = '<div class="empty">No game set up yet.<br>Send <b>/start</b> to the bot in your group to get going.</div>';
      return;
    }

    var gw = state.active_gameweek;
    if (state.players.length < 2) {
      newGwFixtures = null;
      content.innerHTML =
        '<div class="empty">Game paused — an active predictor game needs 2 registered players.</div>' +
        '<div class="status-line">Send <b>/start</b> to the bot to register the second player.</div>';
      return;
    }
    if (!gw) {
      newGwFixtures = null;
      content.innerHTML =
        '<div class="empty">No active fixture right now.</div>' +
        (state.players.length < 2
          ? '<div class="status-line">Need 2 registered players \u2014 send /start to the bot.</div>'
          : '<button class="btn btn-primary" id="startGwBtn">Start a new gameweek</button>') +
        renderPendingResultsHtml();
      var b = document.getElementById("startGwBtn");
      if (b) b.addEventListener("click", startNewGameweek);
      var crb0 = document.getElementById("checkResultBtn");
      if (crb0) crb0.addEventListener("click", checkResult);
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

    var predsHtml = gw.predictions.map(function (p) {
      return '<div class="history-pred"><span>' + esc(p.name) + (p.wildcard ? " \ud83c\udfb4" : "") +
        '</span><span>' + p.home + "-" + p.away + "</span></div>";
    }).join("");

    var html = '<div class="card">';
    html += '<div class="scoreboard">';
    html += '<div class="scoreboard-header"><span class="gw">GW ' + esc(gw.gw_number) + '</span><span>' + esc(fmtKickoff(gw.kickoff)) + '</span></div>';

    if (myTurn) {
      html +=
        '<div class="scoreline">' +
        '<span class="team home">' + esc(gw.home) + '</span>' +
        '<span class="score-box" id="homeScore">' + draft.home + '</span>' +
        '<span class="score-sep">\u2013</span>' +
        '<span class="score-box" id="awayScore">' + draft.away + '</span>' +
        '<span class="team away">' + esc(gw.away) + '</span>' +
        '</div>' +
        '<div class="stepper-row">' +
        '<button class="step-btn" data-adj="h-1">\u2212</button>' +
        '<button class="step-btn" data-adj="h+1">+</button>' +
        '<span class="spacer"></span>' +
        '<button class="step-btn" data-adj="a-1">\u2212</button>' +
        '<button class="step-btn" data-adj="a+1">+</button>' +
        '</div>';
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
    } else if (waitingOn) {
      html += '<div class="turn-banner">Waiting on ' + esc(waitingOn) + '</div>';
      html += '<div style="padding:12px 16px 18px">' + (predsHtml || '<div class="status-line">No predictions yet.</div>') + '</div>';
    }

    html += '</div>'; // .card
    html += renderPendingResultsHtml();

    content.innerHTML = html;

    if (myTurn) {
      Array.prototype.forEach.call(content.querySelectorAll("[data-adj]"), function (btn) {
        btn.addEventListener("click", function () {
          var adj = btn.dataset.adj;
          if (adj === "h-1") draft.home = Math.max(0, draft.home - 1);
          if (adj === "h+1") draft.home = Math.min(state.max_score, draft.home + 1);
          if (adj === "a-1") draft.away = Math.max(0, draft.away - 1);
          if (adj === "a+1") draft.away = Math.min(state.max_score, draft.away + 1);
          document.getElementById("homeScore").textContent = draft.home;
          document.getElementById("awayScore").textContent = draft.away;
        });
      });
      var wc = document.getElementById("wcToggle");
      if (wc) wc.addEventListener("click", function () {
        draft.wildcard = !draft.wildcard;
        wc.classList.toggle("on", draft.wildcard);
      });
      var sb = document.getElementById("submitBtn");
      if (sb) sb.addEventListener("click", submitPrediction);
    }
    var crb = document.getElementById("checkResultBtn");
    if (crb) crb.addEventListener("click", checkResult);
  }

  function renderPendingResultsHtml() {
    var results = state.pending_results || [];
    if (!results.length) return "";
    var cards = results.map(function (pgw) {
      var preds = pgw.predictions.map(function (p) {
        return '<div class="history-pred"><span>' + esc(p.name) + (p.wildcard ? " \ud83c\udfb4" : "") +
          '</span><span>' + p.home + "-" + p.away + "</span></div>";
      }).join("");
      return '<div class="card" style="margin-top:12px">' +
        '<div class="scoreboard-header"><span class="gw">GW ' + esc(pgw.gw_number) + '</span></div>' +
        '<div class="scoreline">' +
        '<span class="team home">' + esc(pgw.home) + '</span>' +
        '<span class="score-sep">vs</span>' +
        '<span class="team away">' + esc(pgw.away) + '</span>' +
        '</div>' +
        '<div style="padding:0 16px 8px">' + preds + '</div>' +
        '</div>';
    }).join("");
    return '<div class="status-line" style="margin-top:16px">Waiting on full time:</div>' +
      cards +
      '<div class="submit-row"><button class="btn btn-ghost" id="checkResultBtn">Check results now</button></div>';
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
