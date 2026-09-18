/* ==========================================================================
   REFRACT — ASCII field engine

   A character grid used as a design material, not a gimmick. Every field is
   a set of coloured layers drawn over the same grid: each layer is a density
   function sampled per cell and mapped through a ramp, so the prism's four
   exit rays can carry the same four intent-class hues used everywhere else.

   Rules this engine follows:
     · decorative only — every field is aria-hidden and never carries meaning
       that is not also stated in text;
     · the grid is measured from the real rendered character box, so the art
       fits its container exactly rather than approximately;
     · animation stops when off-screen, when the tab is hidden, and entirely
       under prefers-reduced-motion (a single static frame is drawn instead).
   ========================================================================== */

(function (global) {
  "use strict";

  /* Weighted to the light end: a dense ramp reads as a blob at small sizes,
     a sparse one reads as drawing. */
  const RAMP = " .:-=+*#%@";
  const DEFAULT_FPS = 15;

  function charFor(density) {
    if (density <= 0.04) return " ";
    const i = Math.round(Math.min(1, density) * (RAMP.length - 1));
    return RAMP[i < 1 ? 1 : i];
  }

  /* Cheap deterministic value noise — no dependency, stable across frames. */
  function hash(x, y) {
    const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
    return n - Math.floor(n);
  }

  function noise(x, y) {
    const xi = Math.floor(x), yi = Math.floor(y);
    const xf = x - xi, yf = y - yi;
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    return (
      hash(xi, yi) * (1 - u) * (1 - v) +
      hash(xi + 1, yi) * u * (1 - v) +
      hash(xi, yi + 1) * (1 - u) * v +
      hash(xi + 1, yi + 1) * u * v
    );
  }

  /* Distance from point p to segment ab, all in normalised space. */
  function distToSegment(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const cx = ax + t * dx, cy = ay + t * dy;
    return { d: Math.hypot(px - cx, py - cy), t };
  }

  function line(dist, width) {
    return Math.max(0, 1 - dist / width);
  }

  function prefersReducedMotion() {
    return global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  /* ------------------------------------------------------------------
     Field: N stacked <pre> layers over one measured grid
     ------------------------------------------------------------------ */

  function createField(host, options) {
    const layerNames = options.layers;
    const sample = options.sample;          // (u, v, t) -> {layer, density} | null
    const rowsFor = options.rows || (() => 26);
    const maxCols = options.maxCols || 120;
    const fps = options.fps || DEFAULT_FPS;

    const layers = layerNames.map((name) => {
      const pre = document.createElement("pre");
      pre.className = "ascii__layer ascii__layer--" + name;
      pre.setAttribute("aria-hidden", "true");
      host.appendChild(pre);
      return pre;
    });

    let cols = 0, rows = 0, aspect = 0.7, raf = null, last = 0, started = 0;
    let visible = true, running = false;

    /* Measure the real character box rather than assuming a ratio: the grid
       then fills the container exactly at any font size or zoom level. */
    function measure() {
      const probe = document.createElement("pre");
      /* `ascii__probe` opts out of the sibling rule that stretches stacked
         layers to the host box, so this shrinks to fit its own text. */
      probe.className = "ascii__layer ascii__probe";
      probe.textContent = "X".repeat(50);
      host.appendChild(probe);
      const w = probe.getBoundingClientRect().width / 50;
      const h = probe.getBoundingClientRect().height;
      host.removeChild(probe);
      return { w: w || 8, h: h || 14 };
    }

    function layout() {
      const box = host.getBoundingClientRect();
      if (!box.width) return false;
      const cell = measure();
      const nextCols = Math.max(20, Math.min(maxCols, Math.floor(box.width / cell.w)));
      const nextRows = Math.max(1, rowsFor(nextCols, box));
      /* The true pixel aspect of the block. Without it a shape defined in
         normalised coordinates comes out squashed, because a monospace cell
         is roughly 0.6 as wide as it is tall. */
      const nextAspect = (nextRows * cell.h) / (nextCols * cell.w);
      if (nextCols === cols && nextRows === rows) return false;
      cols = nextCols;
      rows = nextRows;
      aspect = nextAspect;
      return true;
    }

    function draw(t) {
      if (!cols || !rows) return;
      const buffers = layers.map(() => []);
      for (let y = 0; y < rows; y++) {
        const lines = layers.map(() => "");
        const v = (y + 0.5) / rows;
        for (let x = 0; x < cols; x++) {
          const u = (x + 0.5) / cols;
          const hit = sample(u, v, t, aspect);
          for (let l = 0; l < layers.length; l++) {
            lines[l] += hit && hit.layer === l ? charFor(hit.density) : " ";
          }
        }
        for (let l = 0; l < layers.length; l++) buffers[l].push(lines[l]);
      }
      for (let l = 0; l < layers.length; l++) {
        layers[l].textContent = buffers[l].join("\n");
      }
    }

    function frame(now) {
      raf = null;
      if (!running) return;
      if (now - last >= 1000 / fps) {
        last = now;
        draw((now - started) / 1000);
      }
      raf = requestAnimationFrame(frame);
    }

    function start() {
      if (running || prefersReducedMotion()) return;
      running = true;
      started = performance.now();
      last = 0;
      raf = requestAnimationFrame(frame);
    }

    function stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    }

    function refresh() {
      const changed = layout();
      if (changed || !running) draw(running ? (performance.now() - started) / 1000 : 0);
    }

    /* Only animate what is actually on screen and in a visible tab. */
    if ("IntersectionObserver" in global) {
      new IntersectionObserver((entries) => {
        visible = entries[0].isIntersecting;
        if (visible && !document.hidden) start(); else stop();
      }, { threshold: 0 }).observe(host);
    } else {
      start();
    }

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else if (visible) start();
    });

    if ("ResizeObserver" in global) {
      new ResizeObserver(() => refresh()).observe(host);
    } else {
      global.addEventListener("resize", refresh);
    }

    layout();
    draw(0);
    if (!prefersReducedMotion()) start();

    return { refresh, start, stop, draw, get cols() { return cols; }, get rows() { return rows; } };
  }

  /* ------------------------------------------------------------------
     The prism: one beam in, four rays out — the product metaphor, drawn
     in characters. Layer 0 is structure; layers 1-4 are the intent
     classes, in the same order as the legend.
     ------------------------------------------------------------------ */

  /* Geometry is expressed in a square space: x runs 0..1 across the block and
     y runs 0..aspect down it, so the prism keeps its proportions at any grid
     size. Everything is filled and dithered rather than stroked — a character
     ramp renders mass far better than it renders a one-cell-wide line. */

  const TRI_X = 0.29;        // centre of the prism, across
  const TRI_HALF_W = 0.135;  // half the base width
  const RAY_SPREAD = [-0.42, -0.14, 0.14, 0.42];  // radians from horizontal

  function prismSample(u, v, t, aspect) {
    const A = aspect || 0.7;
    const x = u;
    const y = v * A;
    const cy = A / 2;
    const halfH = A * 0.33;

    const apex = [TRI_X, cy - halfH];
    const baseL = [TRI_X - TRI_HALF_W, cy + halfH];
    const baseR = [TRI_X + TRI_HALF_W, cy + halfH];

    // The faces meet the mid-line here; the beam enters left and leaves right.
    const entryX = TRI_X - TRI_HALF_W * 0.5;
    const exitX = TRI_X + TRI_HALF_W * 0.5;

    const grain = noise(u * 26, v * 26 * A + 3.7) - 0.5;

    // ---- prism body first: light leaves the glass at the face, so the body
    //      owns its own silhouette and the spectrum starts where it ends ----
    const s1 = side(x, y, apex, baseL);
    const s2 = side(x, y, baseL, baseR);
    const s3 = side(x, y, baseR, apex);
    const inside = (s1 >= 0 && s2 >= 0 && s3 >= 0) || (s1 <= 0 && s2 <= 0 && s3 <= 0);

    if (inside) {
      const edge = Math.min(
        distToSegment(x, y, apex[0], apex[1], baseL[0], baseL[1]).d,
        distToSegment(x, y, baseL[0], baseL[1], baseR[0], baseR[1]).d,
        distToSegment(x, y, baseR[0], baseR[1], apex[0], apex[1]).d
      );
      // bright rim, dimmer core, with a refraction sweep crossing the glass
      const rim = Math.max(0, 1 - edge / 0.022) * 0.42;
      const core = 0.10;
      const sweep = Math.max(0, 1 - Math.abs((x - TRI_X + 0.14) - ((t * 0.26) % 0.46)) * 8) * 0.3;
      const d = core + rim + sweep + grain * 0.14;
      return d > 0.05 ? { layer: 0, density: Math.min(1, d) } : null;
    }

    // ---- outgoing spectrum: four filled bands fanning from the exit face --
    const dx = x - exitX;
    const dy = y - cy;
    if (dx > 0) {
      const ang = Math.atan2(dy, dx);
      const r = Math.hypot(dx, dy);
      const reach = 1 - exitX;
      for (let i = 0; i < RAY_SPREAD.length; i++) {
        const half = 0.105;
        const off = Math.abs(ang - RAY_SPREAD[i]);
        if (off < half) {
          const across = Math.pow(1 - off / half, 0.7);       // soft band edges
          const along = Math.min(1, r / reach);
          const falloff = Math.pow(1 - along, 0.45);          // dissipates outward
          // a crest travels outward, staggered so the four fire in sequence
          const head = ((t * 0.30) + i * 0.1) % 1.45;
          const pulse = Math.max(0, 1 - Math.abs(along - head) * 4.5);
          const d = across * falloff * (0.52 + pulse * 0.48) + grain * 0.08;
          if (d > 0.05) return { layer: i + 1, density: Math.min(1, d) };
        }
      }
    }

    // ---- incoming beam: a dashed bar drifting toward the left face --------
    if (x < entryX && Math.abs(y - cy) < 0.010) {
      const dash = ((x * 22) - t * 1.8) % 2;
      if (dash > 0 && dash < 1.45) {
        const ramp = Math.min(1, x / entryX);
        return { layer: 0, density: 0.30 + ramp * 0.36 };
      }
    }
    return null;
  }

  function side(px, py, a, b) {
    return (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0]);
  }

  /* ------------------------------------------------------------------
     Drifting texture — used behind the sign-in statement. Deliberately
     near-invisible: it should read as paper grain, not as art.
     ------------------------------------------------------------------ */

  function textureSample(u, v, t) {
    const n =
      noise(u * 7 + t * 0.055, v * 11 - t * 0.035) * 0.62 +
      noise(u * 19 - t * 0.09, v * 23) * 0.38;
    const vignette = 1 - Math.pow(Math.abs(v - 0.44) * 1.7, 2);
    const d = (n - 0.52) * 1.5 * Math.max(0, vignette);
    return d > 0.05 ? { layer: 0, density: Math.min(0.55, d) } : null;
  }

  /* ------------------------------------------------------------------
     Collection field — shown while a run is in flight. A wavefront sweeps
     left to right across `progress`, so the wait reads as work.
     ------------------------------------------------------------------ */

  function makeCollectSample(getProgress) {
    return function (u, v, t) {
      const p = getProgress();
      const front = p;
      // Ridged interference, so the trace reads as signal rather than fill.
      const wave =
        Math.sin(u * 21 - t * 2.6 + v * 4.2) * 0.5 +
        Math.sin(u * 9 + t * 1.7 - v * 2.4) * 0.5;
      const ridge = Math.pow(Math.max(0, wave * 0.5 + 0.5), 2.6);
      const body = ridge * 0.46 + noise(u * 30, v * 16 + t * 0.7) * 0.12;
      // a bright, narrow crest rides the leading edge
      const edge = Math.max(0, 1 - Math.abs(u - front) * 26);
      const filled = u <= front ? body : 0;
      const d = Math.min(0.92, filled + Math.pow(edge, 1.4) * 0.8);
      return d > 0.1 ? { layer: 0, density: d } : null;
    };
  }

  global.Ascii = {
    createField,
    prismSample,
    textureSample,
    makeCollectSample,
    prefersReducedMotion
  };
})(window);
