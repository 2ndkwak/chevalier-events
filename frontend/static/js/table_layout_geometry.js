/**
 * Table Layout Management -- seat geometry.
 *
 * Mirrors backend/table_geometry.py's perimeter-walk numbering convention
 * exactly. Keep both in sync if this ever changes -- see that file for the
 * full explanation of why the second end seat on a rectangular table is
 * NOT seat `size`.
 *
 * Exposes window.TableGeometry with:
 *   seatSpacing              -- px between adjacent seat centers (shared
 *                                constant so editor and print match)
 *   rectangularSideSplit(size)      -> {sideA, sideB}
 *   rectangularEndSeatNumbers(size) -> {end1, end2}
 *   seatPositions(shape, size)      -> Map<seatNum, {x, y}> in local
 *                                       (un-rotated, un-translated)
 *                                       coordinates centered on the table
 *   tableFootprint(shape, size)     -> {halfLength, halfWidth} (rect) or
 *                                       {radius} (round) -- the physical
 *                                       table surface, seats sit outside it
 */
(function (global) {
  "use strict";

  const SEAT_SPACING = 34;   // px between adjacent seat centers
  const SEAT_OFFSET = 16;    // px seats sit outside the table edge
  const RECT_HALF_WIDTH = 25; // px, fixed short-dimension half-width

  function rectangularSideSplit(size) {
    if (size < 2) {
      throw new Error("rectangular table size must be >= 2, got " + size);
    }
    const k = size - 2;
    const sideA = Math.ceil(k / 2);  // extra seat (if any) goes here
    const sideB = Math.floor(k / 2);
    return { sideA, sideB };
  }

  function rectangularEndSeatNumbers(size) {
    const { sideA } = rectangularSideSplit(size);
    return { end1: 1, end2: 2 + sideA };
  }

  function tableFootprint(shape, size) {
    if (shape === "round") {
      // Circumference roughly seatSpacing per seat; derive radius from it.
      const radius = Math.max(40, (size * SEAT_SPACING) / (2 * Math.PI));
      return { radius };
    }
    // rectangular
    const { sideA, sideB } = rectangularSideSplit(size);
    const maxSide = Math.max(sideA, sideB, 1);
    const halfLength = ((maxSide + 1) * SEAT_SPACING) / 2;
    return { halfLength, halfWidth: RECT_HALF_WIDTH };
  }

  /**
   * Returns a Map from seat number (1..size) to {x, y} in local
   * coordinates (table center at origin, no rotation/translation applied).
   */
  function seatPositions(shape, size) {
    const positions = new Map();

    if (shape === "round") {
      const { radius } = tableFootprint(shape, size);
      const seatRadius = radius + SEAT_OFFSET;
      for (let i = 0; i < size; i++) {
        const angle = (i * 2 * Math.PI) / size - Math.PI / 2; // seat 1 at top
        const seatNum = i + 1;
        positions.set(seatNum, {
          x: seatRadius * Math.cos(angle),
          y: seatRadius * Math.sin(angle),
        });
      }
      return positions;
    }

    // rectangular
    const { sideA, sideB } = rectangularSideSplit(size);
    const { halfLength, halfWidth } = tableFootprint(shape, size);
    const { end1, end2 } = rectangularEndSeatNumbers(size);

    positions.set(end1, { x: -(halfLength + SEAT_OFFSET), y: 0 });
    positions.set(end2, { x: halfLength + SEAT_OFFSET, y: 0 });

    // Side A: walked first, left-to-right, just after end1.
    for (let i = 0; i < sideA; i++) {
      const x = -halfLength + ((i + 1) * (2 * halfLength)) / (sideA + 1);
      positions.set(2 + i, { x, y: -(halfWidth + SEAT_OFFSET) });
    }

    // Side B: walked after end2, right-to-left, closing the loop back
    // next to seat 1 (last side-B seat lands near the -halfLength end).
    for (let i = 0; i < sideB; i++) {
      const x = halfLength - ((i + 1) * (2 * halfLength)) / (sideB + 1);
      positions.set(end2 + 1 + i, { x, y: halfWidth + SEAT_OFFSET });
    }

    return positions;
  }

  global.TableGeometry = {
    seatSpacing: SEAT_SPACING,
    seatOffset: SEAT_OFFSET,
    rectHalfWidth: RECT_HALF_WIDTH,
    rectangularSideSplit,
    rectangularEndSeatNumbers,
    tableFootprint,
    seatPositions,
  };
})(typeof window !== "undefined" ? window : globalThis);
