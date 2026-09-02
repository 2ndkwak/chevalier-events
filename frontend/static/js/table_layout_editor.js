/**
 * Table Layout Management -- interactive editor.
 *
 * Requires table_layout_geometry.js and table_layout_render.js loaded
 * first. Expects a global `TABLE_LAYOUT_CONFIG` object (set inline by the
 * template) with: { tables: [...], saveUrl: string, gridSize, angleStep }.
 *
 * The pure math functions (snapToGrid, snapAngle, computeZoomedViewBox,
 * angleFromPointerOffset) have no DOM dependency and are unit-tested
 * directly. Everything below "-- DOM wiring --" drives the actual canvas
 * and is exercised through browser testing, not unit tests.
 */
(function (global) {
  "use strict";

  // -- Pure math (unit-testable without a DOM) --------------------------

  function snapToGrid(value, gridSize) {
    return Math.round(value / gridSize) * gridSize;
  }

  function snapAngle(deg, angleStep) {
    let snapped = Math.round(deg / angleStep) * angleStep;
    snapped = ((snapped % 360) + 360) % 360;
    return snapped;
  }

  /**
   * Given the current viewBox {x,y,w,h}, a fixed SVG-space point that
   * should stay visually fixed under the cursor, and a zoom factor
   * (< 1 zooms in, > 1 zooms out), return the new viewBox.
   */
  function computeZoomedViewBox(viewBox, fixedPoint, factor, minW, maxW) {
    let newW = viewBox.w * factor;
    let newH = viewBox.h * factor;
    if (newW < minW) { factor = minW / viewBox.w; newW = minW; newH = viewBox.h * factor; }
    if (newW > maxW) { factor = maxW / viewBox.w; newW = maxW; newH = viewBox.h * factor; }
    const newX = viewBox.x + (fixedPoint.x - viewBox.x) * (1 - factor);
    const newY = viewBox.y + (fixedPoint.y - viewBox.y) * (1 - factor);
    return { x: newX, y: newY, w: newW, h: newH };
  }

  /**
   * Given a screen-space offset (dx, dy) from a table's center to the
   * pointer, return the SVG rotate() degrees such that the rotate handle
   * (drawn at local (0, -offset), i.e. "up" at rotation 0) ends up under
   * the pointer. Positive degrees rotate clockwise, matching SVG's
   * rotate() convention.
   */
  function angleFromPointerOffset(dx, dy) {
    let deg = (Math.atan2(dx, -dy) * 180) / Math.PI;
    return ((deg % 360) + 360) % 360;
  }

  /**
   * Bounding radius for a table (rotation-agnostic conservative estimate:
   * half-diagonal of its footprint including seat markers), used for
   * fit-to-view.
   */
  function tableBoundingRadius(table) {
    const footprint = global.TableGeometry.tableFootprint(table.shape, table.seats);
    const seatMargin = global.TableGeometry.seatOffset + 10;
    if (table.shape === "round") {
      return footprint.radius + seatMargin;
    }
    const hl = footprint.halfLength + seatMargin;
    const hw = footprint.halfWidth + seatMargin;
    return Math.hypot(hl, hw);
  }

  function computeFitViewBox(tables, padding) {
    if (tables.length === 0) {
      return { x: -200, y: -200, w: 400, h: 400 };
    }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const t of tables) {
      const r = tableBoundingRadius(t);
      minX = Math.min(minX, t.x - r);
      maxX = Math.max(maxX, t.x + r);
      minY = Math.min(minY, t.y - r);
      maxY = Math.max(maxY, t.y + r);
    }
    minX -= padding; minY -= padding; maxX += padding; maxY += padding;
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  // Exposed for unit testing.
  global.TableLayoutMath = {
    snapToGrid,
    snapAngle,
    computeZoomedViewBox,
    angleFromPointerOffset,
    tableBoundingRadius,
    computeFitViewBox,
  };

  // -- DOM wiring ---------------------------------------------------------

  function init() {
    const config = global.TABLE_LAYOUT_CONFIG;
    if (!config) return; // not on the editor page

    const svg = document.getElementById("layout-canvas");
    const tablesLayer = document.getElementById("tables-layer");
    const statusEl = document.getElementById("save-status");
    const snapGridCheckbox = document.getElementById("snap-grid-toggle");
    const snapAngleCheckbox = document.getElementById("snap-angle-toggle");

    const GRID_SIZE = config.gridSize || 25;
    const ANGLE_STEP = config.angleStep || 15;
    const MIN_VB_W = 200;
    const MAX_VB_W = 6000;

    // Mutable working copy -- table_num -> table state.
    const tables = new Map(config.tables.map((t) => [t.table_num, { ...t }]));
    let dirty = false;

    let viewBox = computeFitViewBox([...tables.values()], 80);
    applyViewBox();

    function applyViewBox() {
      svg.setAttribute("viewBox", `${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`);
    }

    function setStatus(text, isError) {
      statusEl.textContent = text;
      statusEl.style.color = isError ? "var(--danger, #a33)" : "var(--success, #2a7)";
    }

    function markDirty() {
      dirty = true;
      setStatus("Unsaved changes", false);
    }

    function screenToSVG(clientX, clientY) {
      const pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = clientY;
      const ctm = svg.getScreenCTM();
      if (!ctm) return { x: 0, y: 0 };
      const svgPt = pt.matrixTransform(ctm.inverse());
      return { x: svgPt.x, y: svgPt.y };
    }

    function renderAll() {
      tablesLayer.innerHTML = "";
      for (const table of tables.values()) {
        const group = global.TableRender.renderTable(table, { interactive: true });
        addRotateHandle(group, table);
        tablesLayer.appendChild(group);
      }
    }

    function addRotateHandle(group, table) {
      const footprint = global.TableGeometry.tableFootprint(table.shape, table.seats);
      const offset =
        (table.shape === "round" ? footprint.radius : footprint.halfWidth) +
        global.TableGeometry.seatOffset +
        30;

      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", 0);
      line.setAttribute("y1", 0);
      line.setAttribute("x2", 0);
      line.setAttribute("y2", -offset);
      line.setAttribute("stroke", "var(--parchment-md, #cfc3ac)");
      line.setAttribute("stroke-width", 1);
      group.appendChild(line);

      const handle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      handle.setAttribute("class", "rotate-handle");
      handle.setAttribute("cx", 0);
      handle.setAttribute("cy", -offset);
      handle.setAttribute("r", 7);
      handle.setAttribute("fill", "var(--gold, #b08d3f)");
      handle.style.cursor = "grab";
      group.appendChild(handle);
    }

    // -- Drag state machine --

    let drag = null; // { type: 'move'|'rotate'|'pan', ... }

    svg.addEventListener("mousedown", (e) => {
      const handle = e.target.closest(".rotate-handle");
      const tableGroup = e.target.closest(".table-group-interactive");

      if (handle && tableGroup) {
        const tableNum = parseInt(tableGroup.getAttribute("data-table-num"), 10);
        drag = { type: "rotate", tableNum };
      } else if (tableGroup) {
        const tableNum = parseInt(tableGroup.getAttribute("data-table-num"), 10);
        const table = tables.get(tableNum);
        const start = screenToSVG(e.clientX, e.clientY);
        drag = {
          type: "move",
          tableNum,
          startPointer: start,
          startX: table.x,
          startY: table.y,
        };
      } else {
        const start = screenToSVG(e.clientX, e.clientY);
        drag = {
          type: "pan",
          startPointer: start,
          startViewBox: { ...viewBox },
        };
      }
      e.preventDefault();
    });

    svg.addEventListener("mousemove", (e) => {
      if (!drag) return;
      const current = screenToSVG(e.clientX, e.clientY);

      if (drag.type === "move") {
        const table = tables.get(drag.tableNum);
        let newX = drag.startX + (current.x - drag.startPointer.x);
        let newY = drag.startY + (current.y - drag.startPointer.y);
        if (snapGridCheckbox.checked) {
          newX = snapToGrid(newX, GRID_SIZE);
          newY = snapToGrid(newY, GRID_SIZE);
        }
        table.x = newX;
        table.y = newY;
        const group = tablesLayer.querySelector(`[data-table-num="${drag.tableNum}"]`);
        group.setAttribute("transform", `translate(${newX}, ${newY}) rotate(${table.rotation})`);
        markDirty();
      } else if (drag.type === "rotate") {
        const table = tables.get(drag.tableNum);
        const dx = current.x - table.x;
        const dy = current.y - table.y;
        let newRotation = angleFromPointerOffset(dx, dy);
        if (snapAngleCheckbox.checked) {
          newRotation = snapAngle(newRotation, ANGLE_STEP);
        }
        table.rotation = newRotation;
        const group = tablesLayer.querySelector(`[data-table-num="${drag.tableNum}"]`);
        group.setAttribute("transform", `translate(${table.x}, ${table.y}) rotate(${newRotation})`);
        // Label must stay upright -- re-render just this table so its
        // counter-rotation updates too (cheap: one table, not the canvas).
        const fresh = global.TableRender.renderTable(table, { interactive: true });
        addRotateHandle(fresh, table);
        group.replaceWith(fresh);
        markDirty();
      } else if (drag.type === "pan") {
        const dx = current.x - drag.startPointer.x;
        const dy = current.y - drag.startPointer.y;
        viewBox = {
          ...drag.startViewBox,
          x: drag.startViewBox.x - dx,
          y: drag.startViewBox.y - dy,
        };
        applyViewBox();
      }
    });

    global.addEventListener("mouseup", () => {
      drag = null;
    });

    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const point = screenToSVG(e.clientX, e.clientY);
      const factor = e.deltaY < 0 ? 0.9 : 1 / 0.9;
      viewBox = computeZoomedViewBox(viewBox, point, factor, MIN_VB_W, MAX_VB_W);
      applyViewBox();
    });

    document.getElementById("fit-to-view-btn").addEventListener("click", () => {
      viewBox = computeFitViewBox([...tables.values()], 80);
      applyViewBox();
    });
    document.getElementById("zoom-in-btn").addEventListener("click", () => {
      const center = { x: viewBox.x + viewBox.w / 2, y: viewBox.y + viewBox.h / 2 };
      viewBox = computeZoomedViewBox(viewBox, center, 0.8, MIN_VB_W, MAX_VB_W);
      applyViewBox();
    });
    document.getElementById("zoom-out-btn").addEventListener("click", () => {
      const center = { x: viewBox.x + viewBox.w / 2, y: viewBox.y + viewBox.h / 2 };
      viewBox = computeZoomedViewBox(viewBox, center, 1 / 0.8, MIN_VB_W, MAX_VB_W);
      applyViewBox();
    });

    document.getElementById("save-btn").addEventListener("click", async () => {
      const positions = [...tables.values()].map((t) => ({
        table_num: t.table_num,
        x: t.x,
        y: t.y,
        rotation: t.rotation,
      }));
      setStatus("Saving…", false);
      try {
        const resp = await fetch(config.saveUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ positions }),
        });
        if (!resp.ok) throw new Error("save failed: " + resp.status);
        dirty = false;
        // Save acknowledges any pending "changed since sync" markers --
        // matches the server clearing the same flag (see table_layout.py
        // save()). Re-render so the gold outline disappears immediately
        // rather than waiting for a reload.
        for (const table of tables.values()) {
          table.changed_since_sync = false;
        }
        renderAll();
        setStatus("Saved", false);
      } catch (err) {
        setStatus("Save failed -- try again", true);
      }
    });

    global.addEventListener("beforeunload", (e) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    });

    renderAll();
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", init);
  }
})(typeof window !== "undefined" ? window : globalThis);
