// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// __creation__ = 2026-09-16
// __author__ = "jndjama (Joy Ndjama)"
// __copyright__ = "Copyright 2026 ALTIKVA."
// __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
// -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
// Description: The interactive graph view rendered by `cgh graph`: a canvas
//              force layout over the whole repo. Repulsion is exact below
//              EXACT_LIMIT visible nodes and Barnes-Hut above it, so a repo
//              with thousands of files still lays out at interactive speed.
//              Hovering a node lights it and its neighbours; clicking opens
//              its detail panel; the settings panel drives colour groups,
//              filters, label density and the forces themselves. No network
//              access: the payload is inlined by codegraph/viz/html.py.

(function (global) {
  'use strict';

  var INK = {
    page: '#0d1117', panel: '#161b22', control: '#21262d', border: '#30363d',
    text: '#c9d1d9', muted: '#8b949e', subtle: '#484f58', accent: '#58a6ff', primary: '#1f6feb'
  };
  var FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  // Categorical slots validated for this dark surface with every pair compared:
  // any two nodes can sit side by side, so only three hues carry identity.
  var SLOTS = ['#3987e5', '#d95926', '#199e70'];
  var NEUTRAL = '#8b949e';
  var EXACT_LIMIT = 900;
  var LINK_DRAW_BUDGET = 4000;
  // Nodes painted per frame while anything moves, most connected first.
  var NODE_DRAW_BUDGET = 4000;
  var LABEL_BUDGET = 60;

  // Languages are coloured by how much of the repo they are, not by a
  // fixed list: a Java repo would otherwise paint every node "Other".
  // Three slots is the cap the palette validates for a node-link
  // diagram, where any two nodes can sit side by side.
  var LANG_LABELS = {
    python: 'Python', java: 'Java', typescript: 'TypeScript',
    javascript: 'JavaScript', vue: 'Vue', go: 'Go', rust: 'Rust',
    ruby: 'Ruby', csharp: 'C#', terraform: 'Terraform',
    markdown: 'Markdown', config: 'TOML, YAML, JSON'
  };
  var ROLE_GROUPS = [
    { key: 'source', label: 'Source', color: SLOTS[0] },
    { key: 'doc', label: 'Docs', color: SLOTS[1] },
    { key: 'test', label: 'Tests', color: SLOTS[2] },
    { key: 'other', label: 'Manifests', color: NEUTRAL }
  ];
  var MODES = [
    { key: 'folder', label: 'Folder' },
    { key: 'lang', label: 'Language' },
    { key: 'role', label: 'Role' }
  ];

  function isTestPath(path, role) {
    return role === 'test' || path.indexOf('tests/') === 0 || path.indexOf('/tests/') !== -1;
  }
  function langOf(lang) {
    if (!lang) return 'other';
    if (lang === 'toml' || lang === 'yaml' || lang === 'json') return 'config';
    return lang;
  }
  function roleOf(role, path) {
    if (isTestPath(path, role)) return 'test';
    if (role === 'doc') return 'doc';
    if (role === 'manifest') return 'other';
    return 'source';
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function shortPath(path) { return path.split('/').slice(-2).join('/'); }
  function num(n) { return n.toLocaleString('en-US'); }
  function plural(n, one, many) { return num(n) + ' ' + (n === 1 ? one : many); }
  function now() { return (typeof performance !== 'undefined' ? performance : Date).now(); }

  // ---- DOM helpers (labels come from the index, so always textContent) ----
  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'text') node.textContent = attrs[k];
        else if (k === 'class') node.className = attrs[k];
        else if (k === 'style') node.setAttribute('style', attrs[k]);
        else if (k.indexOf('on') === 0) node.addEventListener(k.slice(2), attrs[k]);
        else node.setAttribute(k, attrs[k]);
      });
    }
    (kids || []).forEach(function (kid) { if (kid) node.appendChild(kid); });
    return node;
  }
  function svgIcon(path, size) {
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('width', size || 12);
    svg.setAttribute('height', size || 12);
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('style', 'flex: none');
    var p = document.createElementNS(ns, 'path');
    p.setAttribute('d', path);
    p.setAttribute('fill', 'none');
    p.setAttribute('stroke', 'currentColor');
    p.setAttribute('stroke-width', '1.5');
    p.setAttribute('stroke-linecap', 'round');
    p.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(p);
    return svg;
  }
  var ICON = {
    close: 'M4 4l8 8M12 4l-8 8',
    minus: 'M3 8h10',
    plus: 'M3 8h10M8 3v10',
    chevron: 'M6 4l4 4-4 4',
    reload: 'M13 8a5 5 0 1 1-1.46-3.54M13 3v3h-3'
  };

  // ---- graph construction -------------------------------------------------
  function finishGraph(view, nodes, ls, lt, meta) {
    var n = nodes.length;
    nodes.forEach(function (nd) { nd.out = []; nd.inn = []; });
    for (var e = 0; e < ls.length; e++) {
      nodes[ls[e]].out.push(lt[e]);
      nodes[lt[e]].inn.push(ls[e]);
    }
    var tops = {};
    nodes.forEach(function (nd) {
      var parts = nd.path.split('/');
      nd.top = parts.length > 1 ? parts[0] : '(root)';
      tops[nd.top] = (tops[nd.top] || 0) + 1;
    });
    // Folder colours follow the folder, assigned once by size: filtering never repaints.
    var ranked = Object.keys(tops).sort(function (a, b) { return (tops[b] - tops[a]) || (a < b ? -1 : 1); });
    var folderGroups = ranked.slice(0, 3).map(function (key, i) {
      return { key: key, label: key === '(root)' ? 'Repository root' : key + '/', color: SLOTS[i] };
    });
    if (ranked.length > 3) folderGroups.push({ key: 'other', label: 'Other folders', color: NEUTRAL });
    var colored = {};
    folderGroups.slice(0, 3).forEach(function (gr) { colored[gr.key] = true; });
    var langCounts = {};
    nodes.forEach(function (nd) {
      var k = langOf(nd.lang);
      if (k !== 'other') langCounts[k] = (langCounts[k] || 0) + 1;
    });
    var langRanked = Object.keys(langCounts).sort(function (a, b) {
      return (langCounts[b] - langCounts[a]) || (a < b ? -1 : 1);
    });
    var langGroups = langRanked.slice(0, 3).map(function (key, i) {
      return { key: key, label: LANG_LABELS[key] || key, color: SLOTS[i] };
    });
    langGroups.push({ key: 'other', label: 'Other', color: NEUTRAL });
    var langColored = {};
    langGroups.slice(0, 3).forEach(function (gr) { langColored[gr.key] = true; });
    var modes = { folder: folderGroups, lang: langGroups, role: ROLE_GROUPS };
    var colorIdx = {};
    Object.keys(modes).forEach(function (mk) { colorIdx[mk] = new Uint8Array(n); });
    var deg = new Int32Array(n);
    nodes.forEach(function (nd, i) {
      nd.i = i;
      var keys = {
        folder: colored[nd.top] ? nd.top : 'other',
        lang: langColored[langOf(nd.lang)] ? langOf(nd.lang) : 'other',
        role: roleOf(nd.role, nd.path)
      };
      Object.keys(modes).forEach(function (mk) {
        var at = -1;
        modes[mk].forEach(function (gr, gi) { if (gr.key === keys[mk]) at = gi; });
        colorIdx[mk][i] = at >= 0 ? at : modes[mk].length - 1;
      });
      if (nd.out.length && nd.inn.length) {
        var seen = {};
        var all = [];
        nd.out.concat(nd.inn).forEach(function (j) { if (!seen[j]) { seen[j] = 1; all.push(j); } });
        nd.nbrs = all;
      } else {
        nd.nbrs = nd.out.length ? nd.out : nd.inn;
      }
      deg[i] = nd.out.length + nd.inn.length;
      nd.deg = deg[i];
      nd.r = 2.5 + Math.min(Math.sqrt(nd.deg) * 1.4, 22);
    });
    var X = new Float64Array(n);
    var Y = new Float64Array(n);
    for (var k = 0; k < n; k++) {
      // Deterministic spiral seed: the same repo always opens on the same layout.
      var a = k * 2.399963;
      var rad = 14 * Math.sqrt(k + 1);
      X[k] = Math.cos(a) * rad;
      Y[k] = Math.sin(a) * rad;
    }
    var order = [];
    for (var b = 0; b < n; b++) order.push(b);
    order.sort(function (p, q) { return deg[q] - deg[p]; });
    var orphans = 0;
    for (var c = 0; c < n; c++) if (!deg[c]) orphans++;
    var g = {
      view: view, nodes: nodes, ls: ls, lt: lt, deg: deg, X: X, Y: Y,
      VX: new Float64Array(n), VY: new Float64Array(n),
      byDegree: Int32Array.from(order), modes: modes, colorIdx: colorIdx,
      orphans: orphans, fresh: true, alpha: 0, alphaStart: 1
    };
    Object.keys(meta).forEach(function (key) { g[key] = meta[key]; });
    return g;
  }

  // The payload ships one entry per view; a repo rarely has every kind of
  // edge, so the viewer renders whichever views came with nodes.
  var VIEW_ORDER = ['files', 'symbols', 'infra', 'docs'];

  function viewKeys(data) {
    return VIEW_ORDER.filter(function (k) {
      return data.views && data.views[k] && data.views[k].nodes.length;
    });
  }

  function buildView(data, key) {
    var spec = data.views[key];
    var nodes = spec.nodes.map(function (n) {
      return {
        name: n.n, path: n.p, sub: n.g, lang: n.l || '', role: n.r || '', layer: n.y || '',
        fns: n.f || 0, classes: n.c || 0, line: n.s || 0, kind: n.k || 'file'
      };
    });
    var m = spec.edges.length;
    var ls = new Int32Array(m);
    var lt = new Int32Array(m);
    for (var e = 0; e < m; e++) { ls[e] = spec.edges[e][0]; lt[e] = spec.edges[e][1]; }
    return finishGraph(key, nodes, ls, lt, {
      label: spec.label, noun: spec.noun, edge: spec.edge, outTitle: spec.outTitle,
      inTitle: spec.inTitle, cli: spec.cli || '', truncated: spec.truncated || 0
    });
  }

  // A section's "line" field carries its heading level, not a line number.
  function locationOf(nd) {
    if (nd.kind === 'section' || !nd.line) return nd.path;
    return nd.path + ':' + nd.line;
  }

  // ---- Barnes-Hut quadtree ------------------------------------------------
  function makeQuad(cap) {
    return {
      cap: cap, count: 0,
      cx: new Float64Array(cap), cy: new Float64Array(cap), h: new Float64Array(cap),
      mass: new Float64Array(cap), mx: new Float64Array(cap), my: new Float64Array(cap),
      body: new Int32Array(cap), child: new Int32Array(cap * 4)
    };
  }
  function quadGrow(q) {
    var cap = q.cap * 2;
    function grow(arr, size) { var next = new arr.constructor(size); next.set(arr); return next; }
    q.cx = grow(q.cx, cap); q.cy = grow(q.cy, cap); q.h = grow(q.h, cap);
    q.mass = grow(q.mass, cap); q.mx = grow(q.mx, cap); q.my = grow(q.my, cap);
    q.body = grow(q.body, cap); q.child = grow(q.child, cap * 4);
    q.cap = cap;
  }
  function quadCell(q, cx, cy, h) {
    if (q.count >= q.cap) quadGrow(q);
    var c = q.count++;
    q.cx[c] = cx; q.cy[c] = cy; q.h[c] = h;
    q.mass[c] = 0; q.mx[c] = 0; q.my[c] = 0; q.body[c] = -2;
    var o = c * 4;
    q.child[o] = -1; q.child[o + 1] = -1; q.child[o + 2] = -1; q.child[o + 3] = -1;
    return c;
  }
  function quadChild(q, c, quadrant) {
    var ch = q.child[c * 4 + quadrant];
    if (ch < 0) {
      var hh = q.h[c] / 2;
      ch = quadCell(q, q.cx[c] + (quadrant & 1 ? hh : -hh), q.cy[c] + (quadrant & 2 ? hh : -hh), hh);
      q.child[c * 4 + quadrant] = ch;
    }
    return ch;
  }
  function quadInsert(q, root, i, X, Y) {
    var c = root;
    for (var depth = 0; depth < 28; depth++) {
      var b = q.body[c];
      if (b === -2) { q.body[c] = i; return; }
      if (b >= 0) {
        q.body[c] = -1;
        var qb = (X[b] >= q.cx[c] ? 1 : 0) | (Y[b] >= q.cy[c] ? 2 : 0);
        // quadChild may grow the arrays, so read q.body only after it returns.
        var moved = quadChild(q, c, qb);
        q.body[moved] = b;
      }
      c = quadChild(q, c, (X[i] >= q.cx[c] ? 1 : 0) | (Y[i] >= q.cy[c] ? 2 : 0));
    }
  }
  function quadMass(q, c, X, Y) {
    var b = q.body[c];
    if (b >= 0) { q.mass[c] = 1; q.mx[c] = X[b]; q.my[c] = Y[b]; return; }
    if (b === -2) return;
    var m = 0, sx = 0, sy = 0;
    for (var k = 0; k < 4; k++) {
      var ch = q.child[c * 4 + k];
      if (ch < 0) continue;
      quadMass(q, ch, X, Y);
      var cm = q.mass[ch];
      m += cm; sx += q.mx[ch] * cm; sy += q.my[ch] * cm;
    }
    q.mass[c] = m;
    if (m) { q.mx[c] = sx / m; q.my[c] = sy / m; }
  }

  // ---- the view -----------------------------------------------------------
  function GraphView(data) {
    this.data = data;
    this.graphs = {};
    var keys = viewKeys(data);
    var start = keys[0] || 'files';
    keys.forEach(function (k) {
      if (data.views[k].edges.length > data.views[start].edges.length) start = k;
    });
    this.state = {
      view: start, colorBy: 'folder', hidden: {}, showOrphans: true, minLinks: 0, arrows: false,
      textFade: 50, nodeSize: 50, linkWidth: 40, center: 30, repel: 50, linkStrength: 50, linkDist: 40,
      open: { groups: true, filters: true, display: false, forces: false },
      selected: null, localDepth: 0, query: '', table: false
    };
    this.cam = { x: 0, y: 0, k: 1 };
    this.camTo = null;
    this.userCam = false;
    this.alpha = 0;
    this.alphaStart = 1;
    this.ticks = 0;
    this.hover = null;
    this.mouse = null;
    this.pointer = null;
    this.dragIdx = -1;
    this.busyUntil = 0;
    this.dirty = true;
    this.visKey = '';
    this.vis = null;
    this.matchKey = '';
    this.matches = null;
    this.fitAt = 0;
    this.zoomText = '';
    this.layoutText = null;
    this.quad = makeQuad(4096);
    this.stack = new Int32Array(1024);
    this.dpr = 1;
  }

  GraphView.prototype.graph = function () {
    var v = this.state.view;
    if (!this.graphs[v]) this.graphs[v] = buildView(this.data, v);
    return this.graphs[v];
  };

  GraphView.prototype.groupOf = function (i, modeKey) {
    var g = this.graph();
    return g.modes[modeKey][g.colorIdx[modeKey][i]];
  };

  GraphView.prototype.heat = function (value) {
    if (value > this.alpha) { this.alpha = value; this.alphaStart = value; }
  };

  GraphView.prototype.visibility = function () {
    var s = this.state;
    var g = this.graph();
    var localSel = s.localDepth > 0 && s.selected != null ? s.selected : -1;
    var hiddenSig = g.modes[s.colorBy].filter(function (gr) {
      return s.hidden[s.colorBy + ':' + gr.key];
    }).map(function (gr) { return gr.key; }).join(',');
    // The colour mode only affects visibility through the groups it hides.
    var key = [s.view, hiddenSig ? s.colorBy + ':' + hiddenSig : '', s.showOrphans, s.minLinks, s.localDepth, localSel].join('|');
    if (key === this.visKey && this.vis) return this.vis;
    var n = g.nodes.length;
    var groups = g.modes[s.colorBy];
    var cidx = g.colorIdx[s.colorBy];
    var hiddenIdx = groups.map(function (gr) { return !!s.hidden[s.colorBy + ':' + gr.key]; });
    var allowed = new Uint8Array(n);
    for (var i = 0; i < n; i++) {
      allowed[i] = !hiddenIdx[cidx[i]] && (s.showOrphans || g.deg[i] > 0) && g.deg[i] >= s.minLinks ? 1 : 0;
    }
    if (localSel >= 0 && localSel < n) {
      var keep = new Uint8Array(n);
      keep[localSel] = 1;
      var frontier = [localSel];
      for (var d = 0; d < s.localDepth; d++) {
        var next = [];
        frontier.forEach(function (i2) {
          g.nodes[i2].nbrs.forEach(function (j) { if (!keep[j] && allowed[j]) { keep[j] = 1; next.push(j); } });
        });
        frontier = next;
      }
      for (var i3 = 0; i3 < n; i3++) allowed[i3] = i3 === localSel || (allowed[i3] && keep[i3]) ? 1 : 0;
    }
    var idx = [];
    for (var i4 = 0; i4 < n; i4++) if (allowed[i4]) idx.push(i4);
    var ls = [];
    var lt = [];
    for (var e = 0; e < g.ls.length; e++) {
      if (allowed[g.ls[e]] && allowed[g.lt[e]]) { ls.push(g.ls[e]); lt.push(g.lt[e]); }
    }
    var prev = this.vis;
    var sameView = this.visKey !== '' && this.visKey.split('|')[0] === s.view;
    this.visKey = key;
    this.vis = { allowed: allowed, idx: Int32Array.from(idx), ls: Int32Array.from(ls), lt: Int32Array.from(lt) };
    var changed = !prev || prev.idx.length !== idx.length || prev.ls.length !== ls.length
      || idx.some(function (i5, a) { return prev.idx[a] !== i5; });
    if (sameView && changed) this.heat(0.3);
    this.dirty = true;
    return this.vis;
  };

  GraphView.prototype.matchSet = function (queryOverride) {
    var raw = typeof queryOverride === 'string' ? queryOverride : this.state.query;
    var q = raw.trim().toLowerCase();
    var key = this.state.view + '|' + q;
    if (key === this.matchKey) return this.matches;
    this.matchKey = key;
    this.dirty = true;
    if (!q) { this.matches = null; return null; }
    var set = {};
    var size = 0;
    this.graph().nodes.forEach(function (nd, i) {
      if (nd.name.toLowerCase().indexOf(q) !== -1 || nd.path.toLowerCase().indexOf(q) !== -1) { set[i] = 1; size++; }
    });
    this.matches = { has: function (i) { return !!set[i]; }, size: size, keys: Object.keys(set) };
    return this.matches;
  };

  GraphView.prototype.focus = function () {
    var g = this.graph();
    var v = this.vis || this.visibility();
    var core = this.hover != null ? this.hover : this.state.selected;
    // A selection hidden by a filter keeps its detail panel but stops driving the canvas.
    if (core == null || !g.nodes[core] || !v.allowed[core]) return null;
    var set = {};
    g.nodes[core].nbrs.forEach(function (j) { set[j] = 1; });
    set[core] = 1;
    return { core: core, has: function (i) { return !!set[i]; } };
  };

  // ---- simulation ---------------------------------------------------------
  GraphView.prototype.prewarm = function () {
    var v = this.visibility();
    this.alpha = 1;
    this.alphaStart = 1;
    var steps = v.idx.length > 300 ? 170 : 230;
    for (var i = 0; i < steps && this.alpha > 0; i++) this.tick();
    this.alpha = Math.max(this.alpha, 0.1);
    this.alphaStart = 1;
  };

  GraphView.prototype.tick = function () {
    var g = this.graph();
    var v = this.vis || this.visibility();
    var s = this.state;
    var X = g.X, Y = g.Y, VX = g.VX, VY = g.VY;
    var idx = v.idx;
    var n = idx.length;
    if (!n) { this.alpha = 0; return; }
    var alpha = this.alpha;
    var charge = (40 + s.repel * 9) * alpha;
    var linkK = (s.linkStrength / 100) * 0.1 * alpha;
    var dist = 18 + s.linkDist * 1.8;
    var pull = (s.center / 100) * 0.03 * alpha;
    var i, j, dx, dy, d, d2, f, fx, fy;

    if (n <= EXACT_LIMIT) {
      for (var a = 0; a < n; a++) {
        i = idx[a];
        for (var b = a + 1; b < n; b++) {
          j = idx[b];
          dx = X[i] - X[j];
          dy = Y[i] - Y[j];
          d2 = dx * dx + dy * dy;
          if (d2 < 0.25) { dx = ((a - b) % 7) * 0.1 + 0.05; dy = 0.05; d2 = dx * dx + dy * dy; }
          d = Math.sqrt(d2);
          f = charge / Math.max(d2, 16);
          fx = (dx / d) * f; fy = (dy / d) * f;
          VX[i] += fx; VY[i] += fy;
          VX[j] -= fx; VY[j] -= fy;
        }
      }
    } else {
      var q = this.quad;
      q.count = 0;
      var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (var a2 = 0; a2 < n; a2++) {
        i = idx[a2];
        if (X[i] < x0) x0 = X[i];
        if (X[i] > x1) x1 = X[i];
        if (Y[i] < y0) y0 = Y[i];
        if (Y[i] > y1) y1 = Y[i];
      }
      var root = quadCell(q, (x0 + x1) / 2, (y0 + y1) / 2, Math.max(x1 - x0, y1 - y0) / 2 + 1);
      for (var a3 = 0; a3 < n; a3++) quadInsert(q, root, idx[a3], X, Y);
      quadMass(q, root, X, Y);
      var theta2 = 0.81;
      var stack = this.stack;
      for (var a4 = 0; a4 < n; a4++) {
        i = idx[a4];
        var xi = X[i], yi = Y[i];
        fx = 0; fy = 0;
        var sp = 0;
        stack[sp++] = root;
        while (sp) {
          var c = stack[--sp];
          var mass = q.mass[c];
          if (!mass) continue;
          var body = q.body[c];
          if (body === i) continue;
          dx = xi - q.mx[c];
          dy = yi - q.my[c];
          d2 = dx * dx + dy * dy;
          var w = q.h[c] * 2;
          var holdsNode = body < 0 && Math.abs(xi - q.cx[c]) <= q.h[c] && Math.abs(yi - q.cy[c]) <= q.h[c];
          if (body >= 0 || (!holdsNode && w * w < theta2 * d2)) {
            if (d2 < 0.25) {
              // Stacked nodes: push each one its own way so they can separate.
              dx = ((i % 7) - 3) * 0.1 + 0.05;
              dy = ((i % 5) - 2) * 0.1 + 0.05;
              d2 = dx * dx + dy * dy;
            }
            d = Math.sqrt(d2);
            f = (charge * mass) / Math.max(d2, 16);
            fx += (dx / d) * f;
            fy += (dy / d) * f;
          } else {
            var o = c * 4;
            for (var k2 = 0; k2 < 4; k2++) {
              var ch = q.child[o + k2];
              if (ch >= 0 && sp < stack.length) stack[sp++] = ch;
            }
          }
        }
        VX[i] += fx;
        VY[i] += fy;
      }
    }

    var deg = g.deg;
    for (var e = 0; e < v.ls.length; e++) {
      i = v.ls[e];
      j = v.lt[e];
      dx = X[j] - X[i];
      dy = Y[j] - Y[i];
      d = Math.sqrt(dx * dx + dy * dy) || 1;
      // Hubs get softer springs, so a 400-link node does not collapse its neighbours.
      f = ((d - dist) * linkK) / Math.sqrt(Math.min(deg[i], deg[j]) || 1);
      fx = (dx / d) * f; fy = (dy / d) * f;
      VX[i] += fx; VY[i] += fy;
      VX[j] -= fx; VY[j] -= fy;
    }
    for (var a5 = 0; a5 < n; a5++) {
      i = idx[a5];
      if (i === this.dragIdx) { VX[i] = 0; VY[i] = 0; continue; }
      VX[i] = clamp((VX[i] - X[i] * pull) * 0.6, -24, 24);
      VY[i] = clamp((VY[i] - Y[i] * pull) * 0.6, -24, 24);
      X[i] += VX[i];
      Y[i] += VY[i];
    }
    this.ticks++;
    this.alpha *= 0.985;
    if (this.alpha < 0.004) this.alpha = 0;
  };

  // ---- camera -------------------------------------------------------------
  GraphView.prototype.size = function () {
    var c = this.canvas;
    return c ? { W: c.width / this.dpr, H: c.height / this.dpr } : { W: 0, H: 0 };
  };
  GraphView.prototype.toWorld = function (px, py) {
    var s = this.size();
    return { x: (px - s.W / 2 - this.cam.x) / this.cam.k, y: (py - s.H / 2 - this.cam.y) / this.cam.k };
  };
  GraphView.prototype.zoomAt = function (px, py, factor) {
    var s = this.size();
    var k = this.cam.k;
    var k2 = clamp(k * factor, 0.02, 8);
    var wx = (px - s.W / 2 - this.cam.x) / k;
    var wy = (py - s.H / 2 - this.cam.y) / k;
    this.cam = { k: k2, x: px - s.W / 2 - wx * k2, y: py - s.H / 2 - wy * k2 };
    this.camTo = null;
    this.userCam = true;
    this.busyUntil = now() + 180;
    this.dirty = true;
  };
  GraphView.prototype.viewport = function () {
    var s = this.size();
    var left = this.state.selected != null ? 352 : 24;
    var right = 304;
    var bottom = 64;
    return { W: s.W, H: s.H, left: left, right: right, bottom: bottom,
      cx: left + Math.max(120, s.W - left - right) / 2, cy: (s.H - bottom) / 2 };
  };
  GraphView.prototype.fit = function (animate) {
    var g = this.graph();
    var v = this.vis || this.visibility();
    var vp = this.viewport();
    if (!vp.W || !v.idx.length) return;
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (var a = 0; a < v.idx.length; a++) {
      var i = v.idx[a];
      var r = g.nodes[i].r;
      if (g.X[i] - r < x0) x0 = g.X[i] - r;
      if (g.X[i] + r > x1) x1 = g.X[i] + r;
      if (g.Y[i] - r < y0) y0 = g.Y[i] - r;
      if (g.Y[i] + r > y1) y1 = g.Y[i] + r;
    }
    var availW = Math.max(120, vp.W - vp.left - vp.right);
    var availH = Math.max(120, vp.H - vp.bottom - 24);
    var k = clamp(Math.min(availW / Math.max(60, x1 - x0), availH / Math.max(60, y1 - y0)) * 0.9, 0.02, 2.4);
    var mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
    var target = { k: k, x: vp.cx - vp.W / 2 - mx * k, y: vp.cy - vp.H / 2 - my * k };
    if (animate) this.camTo = target; else { this.cam = target; this.camTo = null; }
    this.dirty = true;
  };
  GraphView.prototype.centerOn = function (i) {
    var g = this.graph();
    if (!g.nodes[i]) return;
    var vp = this.viewport();
    var left = 352;
    var cx = left + Math.max(120, vp.W - left - vp.right) / 2;
    var k = Math.max(this.cam.k, 1.1);
    this.camTo = { k: k, x: cx - vp.W / 2 - g.X[i] * k, y: vp.cy - vp.H / 2 - g.Y[i] * k };
    this.userCam = true;
    this.dirty = true;
  };
  GraphView.prototype.stepCam = function () {
    var t = this.camTo, c = this.cam, e = 0.2;
    var next = { x: c.x + (t.x - c.x) * e, y: c.y + (t.y - c.y) * e, k: c.k + (t.k - c.k) * e };
    if (Math.abs(t.x - next.x) < 0.5 && Math.abs(t.y - next.y) < 0.5 && Math.abs(t.k - next.k) < 0.002) {
      this.cam = { x: t.x, y: t.y, k: t.k };
      this.camTo = null;
    } else {
      this.cam = next;
    }
  };

  // ---- drawing ------------------------------------------------------------
  GraphView.prototype.draw = function () {
    var ctx = this.ctx;
    if (!ctx || !this.canvas) return;
    var sz = this.size();
    var W = sz.W, H = sz.H;
    if (!W || !H) return;
    var s = this.state;
    var g = this.graph();
    var v = this.vis || this.visibility();
    var X = g.X, Y = g.Y;
    var self = this;
    var k = this.cam.k;
    var ox = W / 2 + this.cam.x;
    var oy = H / 2 + this.cam.y;
    var f = this.focus();
    var mt = this.matches;
    var sizeF = 0.4 + (s.nodeSize / 50) * 0.6;
    var lw = 0.4 + (s.linkWidth / 100) * 1.6;
    var idx = v.idx;
    var linkCount = v.ls.length;
    var dense = linkCount > 8000;
    var busy = this.alpha > 0 || !!this.pointer || !!this.camTo || now() < this.busyUntil;
    var step = busy && linkCount > LINK_DRAW_BUDGET ? Math.ceil(linkCount / LINK_DRAW_BUDGET) : 1;
    // Rasterising tens of thousands of thin alpha lines is the single most
    // expensive thing on screen. On a big moving graph, skip them entirely
    // and let the node cloud carry the shape until the layout settles.
    var skipLinks = busy && idx.length > 3000 && linkCount > LINK_DRAW_BUDGET * 2;
    function radius(i) { return Math.max(1.5, g.nodes[i].r * sizeF * k); }
    function inView(x, y) { return x > -40 && y > -40 && x < W + 40 && y < H + 40; }
    function lit(i) { return (!f || f.has(i)) && (!mt || mt.has(i)); }

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.globalAlpha = 1;
    ctx.fillStyle = INK.page;
    ctx.fillRect(0, 0, W, H);

    ctx.lineWidth = dense ? Math.max(0.35, lw * 0.6) : lw;
    ctx.strokeStyle = INK.subtle;
    ctx.globalAlpha = (f || mt) ? (dense ? 0.08 : 0.25) : (dense ? 0.24 : 0.7);
    ctx.beginPath();
    for (var e = 0; skipLinks ? false : e < linkCount; e += step) {
      var a = v.ls[e], b = v.lt[e];
      if (f && (a === f.core || b === f.core)) continue;
      var ax = ox + X[a] * k, ay = oy + Y[a] * k;
      var bx = ox + X[b] * k, by = oy + Y[b] * k;
      if ((ax < 0 && bx < 0) || (ax > W && bx > W) || (ay < 0 && by < 0) || (ay > H && by > H)) continue;
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;

    var hot = [];
    if (f) {
      var core = g.nodes[f.core];
      core.out.forEach(function (j) { if (v.allowed[j]) hot.push([f.core, j]); });
      core.inn.forEach(function (j) { if (v.allowed[j]) hot.push([j, f.core]); });
      ctx.strokeStyle = INK.accent;
      ctx.lineWidth = lw + 0.6;
      ctx.beginPath();
      hot.forEach(function (pair) {
        ctx.moveTo(ox + X[pair[0]] * k, oy + Y[pair[0]] * k);
        ctx.lineTo(ox + X[pair[1]] * k, oy + Y[pair[1]] * k);
      });
      ctx.stroke();
    }

    if (s.arrows && k >= 0.3) {
      var asize = clamp(4 * k, 3, 7);
      var arrow = function (a2, b2, color, alpha) {
        var ax2 = ox + X[a2] * k, ay2 = oy + Y[a2] * k;
        var bx2 = ox + X[b2] * k, by2 = oy + Y[b2] * k;
        if (bx2 < -20 || by2 < -20 || bx2 > W + 20 || by2 > H + 20) return;
        var dx = bx2 - ax2, dy = by2 - ay2;
        var len = Math.sqrt(dx * dx + dy * dy);
        if (len < 14) return;
        var ux = dx / len, uy = dy / len;
        var rB = radius(b2) + 2;
        var tx = bx2 - ux * rB, ty = by2 - uy * rB;
        ctx.fillStyle = color;
        ctx.globalAlpha = alpha;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(tx - ux * asize * 1.6 - uy * asize * 0.8, ty - uy * asize * 1.6 + ux * asize * 0.8);
        ctx.lineTo(tx - ux * asize * 1.6 + uy * asize * 0.8, ty - uy * asize * 1.6 - ux * asize * 0.8);
        ctx.closePath();
        ctx.fill();
      };
      if (!dense) {
        for (var e2 = 0; e2 < linkCount; e2++) {
          var a3 = v.ls[e2], b3 = v.lt[e2];
          if (f && (a3 === f.core || b3 === f.core)) continue;
          arrow(a3, b3, INK.subtle, (f || mt) ? 0.35 : 1);
        }
      }
      hot.forEach(function (pair) { arrow(pair[0], pair[1], INK.accent, 1); });
      ctx.globalAlpha = 1;
    }

    var groups = g.modes[s.colorBy];
    var cidx = g.colorIdx[s.colorBy];
    var ring = idx.length <= 3000;
    // One pass over the visible nodes, bucketed by colour. A pass per colour
    // meant walking every node five times per frame, which is what made a
    // large graph crawl. While anything moves only NODE_DRAW_BUDGET nodes are
    // painted, most connected first; the rest land once the layout settles.
    var buckets = groups.map(function () { return []; });
    var dimmed = [];
    var focused = [];
    var pool = busy && idx.length > NODE_DRAW_BUDGET ? g.byDegree : idx;
    var byDeg = pool === g.byDegree;
    var painted = 0;
    for (var a4 = 0; a4 < pool.length; a4++) {
      var i4 = pool[a4];
      if (byDeg && !v.allowed[i4]) continue;
      var x4 = ox + X[i4] * k;
      var y4 = oy + Y[i4] * k;
      if (!inView(x4, y4)) continue;
      var r4 = radius(i4);
      if (!lit(i4)) dimmed.push(x4, y4, r4);
      else if (f && f.has(i4)) focused.push(x4, y4, r4, cidx[i4]);
      else buckets[cidx[i4]].push(x4, y4, r4);
      if (busy && ++painted >= NODE_DRAW_BUDGET) break;
    }
    function paint(flat, color, alpha, stride, onlyColor) {
      if (!flat.length) return;
      ctx.beginPath();
      var any = false;
      for (var p4 = 0; p4 < flat.length; p4 += stride) {
        if (onlyColor !== undefined && flat[p4 + 3] !== onlyColor) continue;
        var x = flat[p4];
        var y = flat[p4 + 1];
        var r = flat[p4 + 2];
        if (r < 2.2) ctx.rect(x - r, y - r, r * 2, r * 2);
        else { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, Math.PI * 2); }
        any = true;
      }
      if (!any) return;
      ctx.globalAlpha = alpha;
      ctx.fillStyle = color;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (ring) { ctx.lineWidth = 2; ctx.strokeStyle = INK.page; ctx.stroke(); }
    }
    paint(dimmed, INK.subtle, 0.5, 3);
    groups.forEach(function (gr, gi) { paint(buckets[gi], gr.color, 1, 3); });
    if (focused.length) groups.forEach(function (gr, gi) { paint(focused, gr.color, 1, 4, gi); });
    function halo(i, extra, width, color) {
      if (i == null || !v.allowed[i]) return;
      ctx.beginPath();
      ctx.arc(ox + X[i] * k, oy + Y[i] * k, radius(i) + extra, 0, Math.PI * 2);
      ctx.lineWidth = width;
      ctx.strokeStyle = color;
      ctx.stroke();
    }
    if (this.hover !== s.selected) halo(this.hover, 3, 1.5, INK.text);
    halo(s.selected, 4, 2, INK.accent);

    var labels = [];
    var taken = {};
    function push(i, strong, isCore) {
      if (taken[i] || !v.allowed[i]) return false;
      var x = ox + X[i] * k, y = oy + Y[i] * k;
      if (!inView(x, y)) return false;
      taken[i] = 1;
      labels.push({ i: i, x: x, y: y, r: radius(i), strong: strong, core: isCore });
      return true;
    }
    var budget;
    if (s.selected != null) push(s.selected, true, !f || f.core === s.selected);
    if (f) {
      push(f.core, true, true);
      budget = 40;
      for (var a6 = 0; a6 < g.byDegree.length && budget > 0; a6++) {
        var i6 = g.byDegree[a6];
        if (f.has(i6) && lit(i6) && push(i6, true, false)) budget--;
      }
    } else if (mt) {
      budget = 40;
      for (var a7 = 0; a7 < g.byDegree.length && budget > 0; a7++) {
        var i7 = g.byDegree[a7];
        if (mt.has(i7) && push(i7, true, false)) budget--;
      }
    } else {
      var labelZoom = 2.4 - (s.textFade / 100) * 2.2;
      budget = LABEL_BUDGET;
      for (var a8 = 0; a8 < g.byDegree.length && budget > 0; a8++) {
        var i8 = g.byDegree[a8];
        if ((k >= labelZoom || (g.deg[i8] >= 10 && k >= labelZoom * 0.55)) && push(i8, false, false)) budget--;
      }
    }
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.lineJoin = 'round';
    labels.forEach(function (L) {
      var name = g.nodes[L.i].name;
      ctx.font = (L.core ? '600 12px ' : '11px ') + FONT;
      var ty = L.y + L.r + 4;
      ctx.lineWidth = 3;
      ctx.strokeStyle = INK.page;
      ctx.strokeText(name, L.x, ty);
      ctx.fillStyle = L.strong ? INK.text : INK.muted;
      ctx.fillText(name, L.x, ty);
    });

    var zt = Math.round(k * 100) + '%';
    if (zt !== this.zoomText) {
      if (self.el.zoom) self.el.zoom.textContent = zt;
      this.zoomText = zt;
    }
  };

  // ---- pointer ------------------------------------------------------------
  GraphView.prototype.nodeAt = function (px, py) {
    var g = this.graph();
    var v = this.vis || this.visibility();
    var sz = this.size();
    var k = this.cam.k;
    var ox = sz.W / 2 + this.cam.x;
    var oy = sz.H / 2 + this.cam.y;
    var sizeF = 0.4 + (this.state.nodeSize / 50) * 0.6;
    var best = null;
    var bestD = Infinity;
    for (var a = 0; a < v.idx.length; a++) {
      var i = v.idx[a];
      var dx = px - (ox + g.X[i] * k);
      var dy = py - (oy + g.Y[i] * k);
      // The hit area is at least 24px across, whatever the drawn size.
      var reach = Math.max(Math.max(1.5, g.nodes[i].r * sizeF * k) + 4, 12);
      if (dx > reach || dx < -reach || dy > reach || dy < -reach) continue;
      var d = Math.sqrt(dx * dx + dy * dy);
      if (d <= reach && d < bestD) { best = i; bestD = d; }
    }
    return best;
  };

  GraphView.prototype.localPoint = function (e) {
    var rect = this.canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  GraphView.prototype.refreshHover = function () {
    if (!this.mouse) return;
    var hit = this.nodeAt(this.mouse.x, this.mouse.y);
    if (hit !== this.hover) {
      this.hover = hit;
      this.dirty = true;
      if (this.canvas) this.canvas.style.cursor = hit != null ? 'pointer' : 'grab';
    }
    if (hit != null) this.showTip(hit, this.mouse); else this.hideTip();
  };

  GraphView.prototype.showTip = function (i, p) {
    var tip = this.el.tip;
    if (!tip) return;
    var nd = this.graph().nodes[i];
    this.el.tipName.textContent = nd.name;
    this.el.tipMeta.textContent = locationOf(nd) + ' · ' + plural(nd.deg, 'link', 'links');
    this.el.tipKey.style.background = this.groupOf(i, this.state.colorBy).color;
    var sz = this.size();
    var tw = tip.offsetWidth || 240;
    var th = tip.offsetHeight || 52;
    var x = p.x + 14, y = p.y + 14;
    if (x + tw > sz.W - 8) x = p.x - tw - 14;
    if (y + th > sz.H - 8) y = p.y - th - 14;
    tip.style.transform = 'translate(' + Math.round(x) + 'px, ' + Math.round(y) + 'px)';
    tip.style.opacity = '1';
  };

  GraphView.prototype.hideTip = function () {
    if (this.el.tip) this.el.tip.style.opacity = '0';
  };

  // ---- state + rendering --------------------------------------------------
  GraphView.prototype.setState = function (patch) {
    var prev = { view: this.state.view, localDepth: this.state.localDepth, selected: this.state.selected,
      hidden: this.state.hidden, minLinks: this.state.minLinks, showOrphans: this.state.showOrphans };
    Object.keys(patch).forEach(function (k) { this.state[k] = patch[k]; }, this);
    var s = this.state;
    if (prev.view !== s.view) {
      // Each view keeps its own layout temperature: leaving mid-layout and
      // coming back resumes it, and a settled view is not shaken by another's.
      var left = this.graphs[prev.view];
      if (left) { left.alpha = this.alpha; left.alphaStart = this.alphaStart; }
      var entered = this.graph();
      this.alpha = entered.alpha || 0;
      this.alphaStart = entered.alphaStart || 1;
      this.hover = null;
      this.mouse = null;
      this.hideTip();
      this.userCam = false;
      this.fitAt = now() + 16;
    } else if (prev.localDepth !== s.localDepth || (s.localDepth > 0 && prev.selected !== s.selected)
      || prev.hidden !== s.hidden || prev.minLinks !== s.minLinks || prev.showOrphans !== s.showOrphans) {
      this.fitAt = now() + 420;
    }
    this.zoomText = '';
    this.layoutText = null;
    this.dirty = true;
    this.render();
  };

  GraphView.prototype.select = function (i, center) {
    if (center) this.centerOn(i);
    this.setState({ selected: i });
  };

  GraphView.prototype.render = function () {
    this.renderHeader();
    this.renderPanels();
    this.renderDetail();
    this.renderTable();
    this.renderStatus();
  };

  GraphView.prototype.renderHeader = function () {
    var self = this;
    var s = this.state;
    var keys = viewKeys(this.data);
    var box = this.el.views;
    box.textContent = '';
    keys.forEach(function (key, i) {
      var vw = { key: key, label: self.data.views[key].label };
      box.appendChild(el('button', {
        class: 'cg-btn' + (s.view === vw.key ? ' is-active' : ''),
        style: i === 0 ? 'border-radius: 6px 0 0 6px'
          : i === keys.length - 1 ? 'border-radius: 0 6px 6px 0; margin-left: -1px'
            : 'border-radius: 0; margin-left: -1px',
        text: vw.label,
        onclick: function () { self.setState({ view: vw.key, selected: null, localDepth: 0, table: false }); }
      }));
    });
    var m = this.matchSet();
    var v = this.visibility();
    var label = '';
    if (m) {
      var shown = 0;
      m.keys.forEach(function (i) { if (v.allowed[i]) shown++; });
      label = plural(shown, 'match', 'matches') + (m.size > shown ? ' · ' + num(m.size - shown) + ' hidden' : '');
    }
    this.el.matches.textContent = label;
  };

  GraphView.prototype.renderPanels = function () {
    var self = this;
    var s = this.state;
    var g = this.graph();
    var host = this.el.panels;
    host.textContent = '';

    function section(key, title, summary, body) {
      var open = !!s.open[key];
      var chev = svgIcon(ICON.chevron, 12);
      chev.setAttribute('style', 'flex: none; color: ' + INK.muted + '; transition: transform 120ms ease; transform: rotate(' + (open ? 90 : 0) + 'deg)');
      var head = el('div', {
        class: 'cg-head',
        style: 'display: flex; align-items: center; gap: 6px; padding: 10px 12px; font-size: 12px; font-weight: 600; color: ' + INK.text,
        onclick: function () {
          var next = {};
          Object.keys(s.open).forEach(function (k) { next[k] = s.open[k]; });
          next[key] = !next[key];
          self.setState({ open: next });
        }
      }, [chev, el('span', { style: 'flex: 1', text: title }),
        el('span', { style: 'font-size: 11px; font-weight: 400; color: ' + INK.muted, text: summary || '' })]);
      var wrap = el('div', { style: 'border-bottom: 1px solid ' + INK.border }, [head]);
      if (open) wrap.appendChild(el('div', { style: 'padding: 0 12px 12px; display: flex; flex-direction: column; gap: 6px' }, body()));
      host.appendChild(wrap);
    }

    function rangeRow(label, key, min, max) {
      var input = el('input', { class: 'cg-range', type: 'range', min: min, max: max, step: 1, 'aria-label': label });
      input.value = String(s[key]);
      input.addEventListener('input', function (ev) {
        var val = Number(ev.target.value);
        if (!isFinite(val)) return;
        if (key === 'center' || key === 'repel' || key === 'linkStrength' || key === 'linkDist') self.heat(0.3);
        var patch = {};
        patch[key] = val;
        self.setState(patch);
      });
      return el('div', { style: 'display: flex; flex-direction: column; gap: 4px; padding: 4px 0' }, [
        el('div', { style: 'display: flex; justify-content: space-between; align-items: baseline' }, [
          el('span', { style: 'font-size: 12px; color: ' + INK.text, text: label }),
          el('span', { class: 'cg-mono', style: 'font-size: 11px; color: ' + INK.muted, text: String(s[key]) })
        ]),
        input
      ]);
    }

    function toggleRow(label, hint, on, onToggle) {
      var knob = el('span', { style: 'position: absolute; top: 2px; left: ' + (on ? 14 : 2) + 'px; width: 12px; height: 12px; border-radius: 50%; background: ' + INK.text + '; transition: left 120ms ease' });
      var track = el('span', { style: 'position: relative; display: block; width: 28px; height: 16px; border-radius: 8px; flex: none; transition: background 120ms ease; background: ' + (on ? INK.primary : INK.border) }, [knob]);
      return el('div', { class: 'cg-row', style: 'display: flex; align-items: center; gap: 8px; padding: 6px; margin: 0 -6px', onclick: onToggle }, [
        el('span', { style: 'flex: 1; font-size: 12px; color: ' + INK.text, text: label }),
        el('span', { class: 'cg-mono', style: 'font-size: 11px; color: ' + INK.muted, text: hint || '' }),
        track
      ]);
    }

    var groupsAll = g.modes[s.colorBy];
    var cidx = g.colorIdx[s.colorBy];
    var counts = groupsAll.map(function () { return 0; });
    for (var i = 0; i < cidx.length; i++) counts[cidx[i]]++;
    var hiddenCount = 0;
    groupsAll.forEach(function (gr, gi) { if (counts[gi] && s.hidden[s.colorBy + ':' + gr.key]) hiddenCount++; });
    var modeLabel = MODES.filter(function (md) { return md.key === s.colorBy; })[0].label;

    section('groups', 'Groups', hiddenCount ? hiddenCount + ' hidden' : modeLabel, function () {
      var out = [];
      var modeRow = el('div', { style: 'display: flex; margin-bottom: 4px' });
      MODES.forEach(function (md, i) {
        modeRow.appendChild(el('button', {
          class: 'cg-btn' + (md.key === s.colorBy ? ' is-active' : ''),
          style: (i === 0 ? 'border-radius: 6px 0 0 6px' : i === MODES.length - 1 ? 'border-radius: 0 6px 6px 0; margin-left: -1px' : 'border-radius: 0; margin-left: -1px') + '; flex: 1',
          text: md.label,
          onclick: function () { self.setState({ colorBy: md.key }); }
        }));
      });
      out.push(modeRow);
      groupsAll.forEach(function (gr, gi) {
        if (!counts[gi]) return;
        var hidden = !!s.hidden[s.colorBy + ':' + gr.key];
        out.push(el('div', {
          class: 'cg-row', title: hidden ? 'Show this group' : 'Hide this group',
          style: 'display: flex; align-items: center; gap: 8px; padding: 5px 6px; margin: 0 -6px',
          onclick: function () {
            var hiddenNext = {};
            Object.keys(s.hidden).forEach(function (k) { hiddenNext[k] = s.hidden[k]; });
            var kk = s.colorBy + ':' + gr.key;
            if (hiddenNext[kk]) delete hiddenNext[kk]; else hiddenNext[kk] = true;
            self.setState({ hidden: hiddenNext });
          }
        }, [
          el('span', { style: 'width: 10px; height: 10px; border-radius: 50%; flex: none; ' + (hidden ? 'background: transparent; box-shadow: inset 0 0 0 1.5px ' + gr.color : 'background: ' + gr.color) }),
          el('span', { style: 'flex: 1; font-size: 12px; color: ' + (hidden ? INK.muted : INK.text), text: gr.label }),
          el('span', { class: 'cg-mono', style: 'font-size: 11px; color: ' + INK.muted, text: num(counts[gi]) })
        ]));
      });
      return out;
    });

    section('filters', 'Filters', '', function () {
      return [
        toggleRow('Orphans', num(g.orphans), s.showOrphans, function () { self.setState({ showOrphans: !s.showOrphans }); }),
        rangeRow('Minimum links', 'minLinks', 0, 25)
      ];
    });

    section('display', 'Display', '', function () {
      return [
        toggleRow('Arrows', '', s.arrows, function () { self.setState({ arrows: !s.arrows }); }),
        rangeRow('Labels', 'textFade', 0, 100),
        rangeRow('Node size', 'nodeSize', 0, 100),
        rangeRow('Link thickness', 'linkWidth', 0, 100)
      ];
    });

    section('forces', 'Forces', '', function () {
      var btn = el('button', { class: 'cg-btn', style: 'margin-top: 4px', onclick: function () { self.heat(0.8); self.dirty = true; } },
        [svgIcon(ICON.reload, 12), el('span', { text: 'Re-run layout' })]);
      return [
        rangeRow('Center force', 'center', 0, 100),
        rangeRow('Repel force', 'repel', 0, 100),
        rangeRow('Link force', 'linkStrength', 0, 100),
        rangeRow('Link distance', 'linkDist', 0, 100),
        btn
      ];
    });
  };

  GraphView.prototype.renderDetail = function () {
    var self = this;
    var s = this.state;
    var g = this.graph();
    var box = this.el.detail;
    var has = s.selected != null && !!g.nodes[s.selected];
    box.style.display = has ? 'flex' : 'none';
    if (!has) return;
    var nd = g.nodes[s.selected];
    var grp = this.groupOf(s.selected, s.colorBy);
    this.el.detailDot.style.background = grp.color;
    this.el.detailName.textContent = nd.name;
    this.el.detailLoc.textContent = locationOf(nd);

    var chips = this.el.detailChips;
    chips.textContent = '';
    var chipList = [];
    if (nd.kind && nd.kind !== 'file') chipList.push(nd.kind);
    if (nd.lang) chipList.push(nd.lang);
    if (nd.role && nd.role !== 'other') chipList.push('role: ' + nd.role);
    if (nd.layer && nd.layer !== 'other' && nd.layer !== nd.role) chipList.push('layer: ' + nd.layer);
    chipList.push(grp.label);
    chipList.forEach(function (label) {
      chips.appendChild(el('span', { style: 'font-size: 11px; color: ' + INK.muted + '; border: 1px solid ' + INK.border + '; border-radius: 12px; padding: 1px 8px', text: label }));
    });

    var stats = this.el.detailStats;
    stats.textContent = '';
    var lead = nd.kind === 'file' ? ['Functions', String(nd.fns)]
      : nd.kind === 'function' ? ['Line', String(nd.line)]
        : nd.kind === 'section' ? ['Level', String(nd.line)]
          : ['Links', String(nd.deg)];
    var rows = [lead, [g.outTitle, String(nd.out.length)], [g.inTitle, String(nd.inn.length)]];
    rows.forEach(function (row) {
      stats.appendChild(el('div', { style: 'background: ' + INK.panel + '; padding: 10px 12px; display: flex; flex-direction: column; gap: 2px' }, [
        el('span', { style: 'font-size: 16px; font-weight: 600; color: ' + INK.text, text: row[1] }),
        el('span', { style: 'font-size: 11px; color: ' + INK.muted, text: row[0] })
      ]));
    });

    var depth = this.el.depth;
    depth.textContent = '';
    [0, 1, 2].forEach(function (d, i) {
      depth.appendChild(el('button', {
        class: 'cg-btn' + (s.localDepth === d ? ' is-active' : ''),
        style: i === 0 ? 'border-radius: 6px 0 0 6px' : i === 2 ? 'border-radius: 0 6px 6px 0; margin-left: -1px' : 'border-radius: 0; margin-left: -1px',
        text: d === 0 ? 'Global' : 'Depth ' + d,
        onclick: function () { self.setState({ localDepth: d }); }
      }));
    });

    var lists = this.el.detailLists;
    lists.textContent = '';
    var LIMIT = 40;
    function listSection(title, arr, emptyText) {
      var sorted = arr.slice().sort(function (a, b) { return g.deg[b] - g.deg[a]; });
      var wrap = el('div', { style: 'display: flex; flex-direction: column; gap: 2px; padding: 6px 0' }, [
        el('div', { style: 'display: flex; justify-content: space-between; padding: 0 6px 4px; font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ' + INK.muted }, [
          el('span', { text: title }),
          el('span', { class: 'cg-mono', text: num(arr.length) })
        ])
      ]);
      sorted.slice(0, LIMIT).forEach(function (j) {
        var o = g.nodes[j];
        wrap.appendChild(el('div', {
          class: 'cg-row', style: 'display: flex; align-items: center; gap: 8px; padding: 5px 6px',
          onclick: function () { self.select(j, true); }
        }, [
          el('span', { style: 'width: 8px; height: 8px; border-radius: 50%; flex: none; background: ' + self.groupOf(j, s.colorBy).color }),
          el('span', { style: 'flex: 1; min-width: 0; font-size: 12px; color: ' + INK.text + '; white-space: nowrap; overflow: hidden; text-overflow: ellipsis', text: o.name }),
          el('span', { class: 'cg-mono', style: 'font-size: 11px; color: ' + INK.muted + '; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 120px', text: g.view === 'symbols' ? shortPath(o.path) : o.sub })
        ]));
      });
      if (!arr.length) wrap.appendChild(el('div', { style: 'padding: 2px 6px; font-size: 12px; color: ' + INK.muted, text: emptyText }));
      if (arr.length > LIMIT) wrap.appendChild(el('div', { style: 'padding: 2px 6px; font-size: 11px; color: ' + INK.muted, text: num(arr.length - LIMIT) + ' more not listed' }));
      return wrap;
    }
    lists.appendChild(listSection(g.outTitle, nd.out, g.view === 'symbols' ? 'Calls none of the functions shown' : 'No outgoing link'));
    lists.appendChild(listSection(g.inTitle, nd.inn, g.view === 'symbols' ? 'No caller among the functions shown' : 'No incoming link'));

    this.el.detailCli.textContent = (g.cli || '')
      .replace('{path}', nd.path).replace('{name}', nd.name) || '—';
  };

  GraphView.prototype.renderTable = function () {
    var self = this;
    var s = this.state;
    var g = this.graph();
    var v = this.visibility();
    this.el.table.style.display = s.table ? 'flex' : 'none';
    this.el.tableBtn.className = 'cg-btn' + (s.table ? ' is-active' : '');
    this.el.table.style.left = (s.selected != null && g.nodes[s.selected] ? 352 : 16) + 'px';
    if (!s.table) return;
    var order = Array.prototype.slice.call(v.idx).sort(function (a, b) {
      return (g.deg[b] - g.deg[a]) || (g.nodes[a].name < g.nodes[b].name ? -1 : 1);
    });
    this.el.tableTitle.textContent = plural(v.idx.length, 'node', 'nodes')
      + (v.idx.length > 250 ? ', the 250 most connected listed' : '') + ', sorted by links';
    var rows = this.el.tableRows;
    rows.textContent = '';
    order.slice(0, 250).forEach(function (i) {
      var nd = g.nodes[i];
      var grp = self.groupOf(i, s.colorBy);
      rows.appendChild(el('div', {
        class: 'cg-row',
        style: 'display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 12px; align-items: center; padding: 6px 8px;'
          + (i === s.selected ? ' background: #1f6feb33' : ''),
        onclick: function () { self.setState({ table: false }); self.select(i, true); }
      }, [
        el('span', { style: 'grid-column: span 4; font-size: 12px; color: ' + INK.text + '; white-space: nowrap; overflow: hidden; text-overflow: ellipsis', text: nd.name }),
        el('span', { class: 'cg-mono', style: 'grid-column: span 5; font-size: 11px; color: ' + INK.muted + '; white-space: nowrap; overflow: hidden; text-overflow: ellipsis', text: locationOf(nd) }),
        el('span', { style: 'grid-column: span 2; display: flex; align-items: center; gap: 6px; font-size: 12px; color: ' + INK.text + '; overflow: hidden' }, [
          el('span', { style: 'width: 8px; height: 8px; border-radius: 50%; flex: none; background: ' + grp.color }),
          el('span', { style: 'overflow: hidden; text-overflow: ellipsis', text: grp.label })
        ]),
        el('span', { class: 'cg-mono', style: 'grid-column: span 1; font-size: 12px; color: ' + INK.text + '; text-align: right', text: num(nd.deg) })
      ]));
    });
  };

  GraphView.prototype.renderStatus = function () {
    var s = this.state;
    var g = this.graph();
    var v = this.visibility();
    var line = plural(v.idx.length, g.noun[0], g.noun[1]) + ' · ' + plural(v.ls.length, g.edge[0], g.edge[1]);
    if (g.truncated) line += ' · ' + num(g.truncated) + ' more not shown';
    if (s.localDepth > 0 && s.selected != null) line += ' · neighborhood';
    this.el.counts.textContent = line;
    this.el.empty.style.display = v.idx.length ? 'none' : 'flex';
    if (!g.ls.length) {
      var best = null;
      var self2 = this;
      viewKeys(this.data).forEach(function (k) {
        var cand = self2.data.views[k];
        if (k !== g.view && cand.edges.length && (!best || cand.edges.length > best.edges.length)) best = cand;
      });
      if (best) {
        this.el.hint.textContent = 'No ' + g.edge[1] + ' in this index · ' + best.label + ' holds this repo\'s edges';
        return;
      }
    }
    this.el.hint.textContent = v.idx.length > 2000
      ? 'Large graph: labels appear as you zoom in · Search or raise Minimum links to focus'
      : 'Scroll to zoom · Drag to pan or move a node · Double-click for its neighborhood';
  };

  GraphView.prototype.updateLayoutLabel = function (v) {
    var text = '';
    if (this.alpha > 0 && v.idx.length >= 1200) {
      var span = Math.log(0.004 / this.alphaStart);
      var pct = span ? clamp(Math.round((Math.log(this.alpha / this.alphaStart) / span) * 100), 0, 99) : 0;
      text = 'Layout ' + pct + '%';
    }
    if (text !== this.layoutText) {
      this.el.layout.textContent = text;
      this.layoutText = text;
    }
  };

  // ---- mount --------------------------------------------------------------
  GraphView.prototype.mount = function () {
    var self = this;
    var pick = function (id) { return document.getElementById(id); };
    this.el = {
      meta: pick('cg-meta'), views: pick('cg-views'), search: pick('cg-search'), matches: pick('cg-matches'),
      stage: pick('cg-stage'), canvas: pick('cg-canvas'), tip: pick('cg-tip'), tipName: pick('cg-tip-name'),
      tipMeta: pick('cg-tip-meta'), tipKey: pick('cg-tip-key'), empty: pick('cg-empty'), reset: pick('cg-reset'),
      detail: pick('cg-detail'), detailDot: pick('cg-detail-dot'), detailName: pick('cg-detail-name'),
      detailClose: pick('cg-detail-close'), detailLoc: pick('cg-detail-loc'), detailChips: pick('cg-detail-chips'),
      detailStats: pick('cg-detail-stats'), depth: pick('cg-depth'), detailLists: pick('cg-detail-lists'),
      detailCli: pick('cg-detail-cli'), panels: pick('cg-panels'), counts: pick('cg-counts'),
      layout: pick('cg-layout'), zoom: pick('cg-zoom'), zoomIn: pick('cg-zoom-in'), zoomOut: pick('cg-zoom-out'),
      fit: pick('cg-fit'), tableBtn: pick('cg-table-btn'), hint: pick('cg-hint'), table: pick('cg-table'),
      tableTitle: pick('cg-table-title'), tableRows: pick('cg-table-rows'), tableClose: pick('cg-table-close')
    };
    this.canvas = this.el.canvas;
    this.ctx = this.canvas.getContext('2d');

    var T = this.data.totals || {};
    this.el.meta.textContent = this.data.repo + ' · ' + plural(T.files || 0, 'file', 'files')
      + ' · ' + plural(T.functions || 0, 'function', 'functions') + ' · ' + plural(T.classes || 0, 'class', 'classes');

    this.canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var p = self.localPoint(e);
      self.zoomAt(p.x, p.y, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
    }, { passive: false });
    this.canvas.addEventListener('pointerdown', function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      var p = self.localPoint(e);
      var hit = self.nodeAt(p.x, p.y);
      self.pointer = { id: e.pointerId, sx: p.x, sy: p.y, lx: p.x, ly: p.y, node: hit, moved: false };
      self.dragIdx = hit == null ? -1 : hit;
      self.camTo = null;
      try { self.canvas.setPointerCapture(e.pointerId); } catch (err) { /* capture is best effort */ }
      self.canvas.style.cursor = 'grabbing';
    });
    this.canvas.addEventListener('pointermove', function (e) {
      var p = self.localPoint(e);
      var P = self.pointer;
      if (P && P.id === e.pointerId) {
        var dx = p.x - P.lx, dy = p.y - P.ly;
        P.lx = p.x; P.ly = p.y;
        if (Math.abs(p.x - P.sx) + Math.abs(p.y - P.sy) > 3) P.moved = true;
        if (P.node != null) {
          if (P.moved) {
            var g = self.graph();
            var w = self.toWorld(p.x, p.y);
            g.X[P.node] = w.x;
            g.Y[P.node] = w.y;
            self.heat(0.2);
            self.userCam = true;
            self.camTo = null;
          }
        } else {
          self.cam = { k: self.cam.k, x: self.cam.x + dx, y: self.cam.y + dy };
          if (P.moved) self.userCam = true;
        }
        self.dirty = true;
        self.hideTip();
        return;
      }
      self.mouse = p;
      self.refreshHover();
    });
    this.canvas.addEventListener('pointerup', function (e) {
      var P = self.pointer;
      if (!P || P.id !== e.pointerId) return;
      self.pointer = null;
      self.dragIdx = -1;
      try { self.canvas.releasePointerCapture(e.pointerId); } catch (err) { /* already released */ }
      self.canvas.style.cursor = self.hover != null ? 'pointer' : 'grab';
      self.dirty = true;
      if (!P.moved) {
        if (P.node != null) self.select(P.node, false);
        else if (self.state.selected != null && self.state.localDepth === 0) self.setState({ selected: null });
      }
    });
    this.canvas.addEventListener('pointercancel', function (e) {
      var P = self.pointer;
      if (!P || P.id !== e.pointerId) return;
      // A cancelled press (OS gesture, focus loss) is never a click.
      self.pointer = null;
      self.dragIdx = -1;
      self.canvas.style.cursor = 'grab';
      self.dirty = true;
    });
    this.canvas.addEventListener('pointerleave', function () {
      if (self.pointer) return;
      self.mouse = null;
      self.hover = null;
      self.hideTip();
      self.dirty = true;
    });
    this.canvas.addEventListener('dblclick', function (e) {
      var p = self.localPoint(e);
      var hit = self.nodeAt(p.x, p.y);
      if (hit != null) self.setState({ selected: hit, localDepth: self.state.localDepth || 1 });
    });

    this.el.search.addEventListener('input', function (e) { self.setState({ query: e.target.value }); });
    this.el.search.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.target.value = ''; self.setState({ query: '' }); return; }
      if (e.key !== 'Enter') return;
      var typed = e.target.value;
      if (typed !== self.state.query) self.setState({ query: typed });
      var m = self.matchSet(typed);
      var v = self.visibility();
      if (!m || !m.size) return;
      var g = self.graph();
      for (var a = 0; a < g.byDegree.length; a++) {
        var i = g.byDegree[a];
        if (m.has(i) && v.allowed[i]) { self.select(i, true); return; }
      }
    });
    this.el.detailClose.addEventListener('click', function () { self.setState({ selected: null, localDepth: 0 }); });
    this.el.tableClose.addEventListener('click', function () { self.setState({ table: false }); });
    this.el.tableBtn.addEventListener('click', function () { self.setState({ table: !self.state.table }); });
    this.el.zoomIn.addEventListener('click', function () { var vp = self.viewport(); self.zoomAt(vp.cx, vp.cy, 1.25); });
    this.el.zoomOut.addEventListener('click', function () { var vp = self.viewport(); self.zoomAt(vp.cx, vp.cy, 0.8); });
    this.el.fit.addEventListener('click', function () { self.userCam = false; self.fit(true); });
    this.el.reset.addEventListener('click', function () {
      self.setState({ hidden: {}, minLinks: 0, showOrphans: true, localDepth: 0 });
    });

    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () { self.resize(); }).observe(this.el.stage);
    } else {
      global.addEventListener('resize', function () { self.resize(); });
    }
    this.resize();
    this.render();
    this.loop();
  };

  GraphView.prototype.resize = function () {
    var c = this.canvas;
    if (!c) return;
    var host = this.el.stage;
    var nodes = this.vis ? this.vis.idx.length : 0;
    // Retina doubles every dimension, so a dense graph pays four times the
    // rasterisation for detail nobody can see at that density.
    var dpr = Math.min(global.devicePixelRatio || 1, nodes > 2000 ? 1 : 2);
    var pw = Math.round(host.clientWidth * dpr);
    var ph = Math.round(host.clientHeight * dpr);
    if (c.width !== pw || c.height !== ph || this.dpr !== dpr) {
      c.width = pw;
      c.height = ph;
      this.dpr = dpr;
      this.dirty = true;
    }
  };

  GraphView.prototype.loop = function () {
    var self = this;
    global.requestAnimationFrame(function () { self.loop(); });
    var g = this.graph();
    var v = this.visibility();
    this.matchSet();
    var sz = this.size();
    if (g.fresh && sz.W > 0) {
      g.fresh = false;
      this.resize();
      this.userCam = false;
      if (v.idx.length <= 1500) this.prewarm(); else this.heat(1);
      this.fit(false);
    }
    if (this.alpha > 0) {
      this.tick();
      this.dirty = true;
      if (!this.userCam && this.ticks % 40 === 0) this.fit(true);
      if (this.alpha === 0 && !this.userCam) this.fit(true);
    }
    if (this.fitAt && now() >= this.fitAt) { this.fitAt = 0; this.fit(true); }
    if (this.camTo) { this.stepCam(); this.dirty = true; }
    if (this.busyUntil && now() >= this.busyUntil) { this.busyUntil = 0; this.dirty = true; }
    if (this.mouse && !this.pointer && (this.alpha > 0 || this.camTo || this.dirty)) this.refreshHover();
    this.updateLayoutLabel(v);
    // While a big graph is still settling, paint every other frame: the layout
    // keeps its full step rate and the canvas costs half as much.
    var heavy = this.alpha > 0 && v.idx.length > 3000;
    if (this.dirty && !(heavy && this.ticks % 2)) { this.dirty = false; this.draw(); }
  };

  global.CGHGraph = {
    mount: function (data) {
      var view = new GraphView(data);
      view.mount();
      return view;
    },
    // exported for the test harness
    internals: { makeQuad: makeQuad, quadCell: quadCell, quadInsert: quadInsert, quadMass: quadMass, GraphView: GraphView }
  };
})(typeof window !== 'undefined' ? window : globalThis);
