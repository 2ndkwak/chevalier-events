/**
 * Table Layout Management -- SVG rendering for a single table.
 *
 * Builds an <g> element for one table: shape, seat markers (with gaps at
 * eliminated seats), and a centered label that stays upright regardless
 * of the table's rotation. Used identically by the interactive editor and
 * the static print view -- one rendering function, so they can't drift
 * apart visually. Requires table_layout_geometry.js to be loaded first.
 *
 * Colors are plain hex, NOT CSS var(--...) references -- CSS custom
 * properties inside SVG fill/stroke presentation attributes are a known
 * trouble spot in browsers' print rendering pipeline specifically (they
 * can render fine on-screen but come out blank/invalid on paper). These
 * hex values match this app's --parchment/--burgundy/--gold/etc palette.
 *
 * Exposes window.TableRender.renderTable(table, opts) -> SVGGElement
 *   table: {table_num, label, shape, seats, eliminated_seats, x, y, rotation}
 *   opts:  {interactive: bool} -- interactive tables get drag/rotate
 *          handles and cursor styling; print tables are plain.
 */
(function (global) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      node.setAttribute(k, v);
    }
    for (const child of children) {
      if (child) node.appendChild(child);
    }
    return node;
  }

  function renderSeat(seatNum, pos, isEliminated) {
    const g = el("g", {
      class: "table-seat" + (isEliminated ? " table-seat-eliminated" : ""),
      transform: `translate(${pos.x}, ${pos.y})`,
    });

    if (isEliminated) {
      // Visible gap, not a rendering bug -- faint dashed outline marking
      // "no seat here", distinct from an actually-missing/broken seat.
      g.appendChild(el("circle", {
        r: 9,
        fill: "none",
        stroke: "#cfc3ac",
        "stroke-width": 1,
        "stroke-dasharray": "2,2",
      }));
      return g;
    }

    g.appendChild(el("circle", {
      r: 9,
      fill: "#fff",
      stroke: "#5c1f2e",
      "stroke-width": 1.25,
    }));
    const text = el("text", {
      "text-anchor": "middle",
      "dominant-baseline": "central",
      "font-size": 8,
      "font-family": "sans-serif",
      fill: "#2a2320",
    });
    text.textContent = String(seatNum);
    g.appendChild(text);
    return g;
  }

  function renderShape(shape, size) {
    const { tableFootprint } = global.TableGeometry;
    const footprint = tableFootprint(shape, size);

    if (shape === "round") {
      return el("circle", {
        r: footprint.radius,
        fill: "#f0ede6",
        stroke: "#5c1f2e",
        "stroke-width": 2,
      });
    }

    return el("rect", {
      x: -footprint.halfLength,
      y: -footprint.halfWidth,
      width: footprint.halfLength * 2,
      height: footprint.halfWidth * 2,
      rx: 6,
      fill: "#f0ede6",
      stroke: "#5c1f2e",
      "stroke-width": 2,
    });
  }

  function renderChangedMarker(shape, size) {
    // Screen-only "changed since last sync" indicator (2.2) -- a dashed
    // gold outline slightly outside the table's own border. Caller is
    // responsible for only adding this in interactive mode; it must
    // never appear on the print view.
    const { tableFootprint } = global.TableGeometry;
    const footprint = tableFootprint(shape, size);
    const pad = 6;

    if (shape === "round") {
      return el("circle", {
        r: footprint.radius + pad,
        fill: "none",
        stroke: "#b08d3f",
        "stroke-width": 2,
        "stroke-dasharray": "5,4",
      });
    }

    return el("rect", {
      x: -footprint.halfLength - pad,
      y: -footprint.halfWidth - pad,
      width: (footprint.halfLength + pad) * 2,
      height: (footprint.halfWidth + pad) * 2,
      rx: 10,
      fill: "none",
      stroke: "#b08d3f",
      "stroke-width": 2,
      "stroke-dasharray": "5,4",
    });
  }

  function renderLabel(label, rotation) {
    // Counter-rotate so the label stays upright on screen regardless of
    // the table's own rotation -- this is the ONLY element in the group
    // that cancels the parent transform.
    const g = el("g", { transform: `rotate(${-rotation})` });
    const text = el("text", {
      "text-anchor": "middle",
      "dominant-baseline": "central",
      "font-size": 12,
      "font-family": "Georgia, serif",
      fill: "#5c1f2e",
      "font-weight": "bold",
    });
    text.textContent = label;
    g.appendChild(text);
    return g;
  }

  function renderTable(table, opts) {
    opts = opts || {};
    const { seatPositions } = global.TableGeometry;
    const eliminated = new Set(table.eliminated_seats || []);
    const positions = seatPositions(table.shape, table.seats);

    const group = el("g", {
      class: "table-group" + (opts.interactive ? " table-group-interactive" : ""),
      "data-table-num": table.table_num,
      transform: `translate(${table.x}, ${table.y}) rotate(${table.rotation})`,
    });

    group.appendChild(renderShape(table.shape, table.seats));

    if (opts.interactive && table.changed_since_sync) {
      group.appendChild(renderChangedMarker(table.shape, table.seats));
    }

    for (const [seatNum, pos] of positions) {
      group.appendChild(renderSeat(seatNum, pos, eliminated.has(seatNum)));
    }

    group.appendChild(renderLabel(table.label, table.rotation));

    if (opts.interactive) {
      group.style.cursor = "move";
    }

    return group;
  }

  global.TableRender = { renderTable };
})(typeof window !== "undefined" ? window : globalThis);
