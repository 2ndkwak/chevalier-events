/**
 * Generates Avery 5011 name card .docx
 * Avery 5011: 3-3/4" W x 2-7/8" H per card (unfolded), 6 per sheet (2 cols x 3 rows)
 *
 * Avery 5011 Word template measurements:
 *   Top margin:  1.38"   Left margin: 0.75"
 *   Card width:  3.75"   Card height: 2.875"
 *   2 cols x 3 rows = 6 cards, no gap between cells
 */

const { Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell,
        AlignmentType, VerticalAlign, WidthType, BorderStyle } = require('docx');
const fs   = require('fs');
const path = require('path');

const seats    = JSON.parse(process.argv[2]);
const outPath  = process.argv[3];
const logoPath = process.argv[4];    // absolute path to Chevalier_Logo.jpg

const logoData = fs.readFileSync(logoPath);

// DXA units: 1440 DXA = 1 inch
const DXA     = 1440;
const PAGE_W  = Math.round(8.5   * DXA);  // 12240
const PAGE_H  = Math.round(11    * DXA);  // 15840
const TOP_M   = Math.round(1.38  * DXA);  // 1987
const BOT_M   = Math.round(0.62  * DXA);  // 893
const LEFT_M  = Math.round(0.75  * DXA);  // 1080
const RIGHT_M = Math.round(0.25  * DXA);  // 360
const CARD_W  = Math.round(3.75  * DXA);  // 5400
const CARD_H  = Math.round(2.875 * DXA);  // 4140

// Logo: fit into ~0.45" square in top-left of card (EMU: 914400 = 1 inch)
const EMU       = 914400;
const LOGO_SIZE = Math.round(0.42 * EMU); // ~0.42" square

const BURGUNDY = "6B1A2A";
const MUTED    = "7A6650";

const noBorder  = { style: BorderStyle.NIL, size: 0, color: "FFFFFF" };
const noBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };

function makeCard(seat) {
  if (!seat) {
    // Blank placeholder card
    return new TableCell({
      width:         { size: CARD_W, type: WidthType.DXA },
      borders:       noBorders,
      verticalAlign: VerticalAlign.CENTER,
      children:      [new Paragraph({ children: [new TextRun("")] })],
    });
  }

  const locLine = `${seat.table_label}  ·  Seat ${seat.seat_num}`;

  return new TableCell({
    width:         { size: CARD_W, type: WidthType.DXA },
    borders:       noBorders,
    verticalAlign: VerticalAlign.CENTER,
    margins:       { top: 240, bottom: 200, left: 280, right: 280 },
    children: [
      // Row 1: logo left-aligned
      new Paragraph({
        alignment: AlignmentType.LEFT,
        spacing:   { before: 0, after: 80 },
        children: [
          new ImageRun({
            data: logoData,
            type: "jpg",
            transformation: { width: LOGO_SIZE / 9525, height: LOGO_SIZE / 9525 },
            // transformation takes pixels at 96dpi; EMU/9525 = pixels at 96dpi
          }),
        ],
      }),
      // Row 2: name centred, large
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing:   { before: 0, after: 60 },
        children: [new TextRun({
          text:  seat.name,
          font:  "Georgia",
          size:  30,          // 15pt
          color: BURGUNDY,
        })],
      }),
      // Row 3: table + seat, small caps
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing:   { before: 0, after: 0 },
        children: [new TextRun({
          text:             locLine,
          font:             "Arial",
          size:             14,     // 7pt
          color:            MUTED,
          allCaps:          true,
          characterSpacing: 30,
        })],
      }),
    ],
  });
}

// Sort A-Z by last name
const sorted = [...seats].sort((a, b) => {
  const la = a.name.split(' ').pop().toLowerCase();
  const lb = b.name.split(' ').pop().toLowerCase();
  return la < lb ? -1 : la > lb ? 1 : 0;
});

// Pad to multiple of 6
while (sorted.length % 6 !== 0) sorted.push(null);

const sections = [];
const numSheets = sorted.length / 6;

for (let s = 0; s < numSheets; s++) {
  const chunk = sorted.slice(s * 6, s * 6 + 6);
  const rows  = [];
  for (let r = 0; r < 3; r++) {
    rows.push(new TableRow({
      height:   { value: CARD_H, rule: 'exact' },
      children: [makeCard(chunk[r * 2]), makeCard(chunk[r * 2 + 1])],
    }));
  }

  sections.push({
    properties: {
      page: {
        size:   { width: PAGE_W, height: PAGE_H },
        margin: { top: TOP_M, bottom: BOT_M, left: LEFT_M, right: RIGHT_M },
      },
    },
    children: [
      new Table({
        width:        { size: CARD_W * 2, type: WidthType.DXA },
        columnWidths: [CARD_W, CARD_W],
        rows,
      }),
    ],
  });
}

const doc = new Document({ sections });

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(outPath, buf);
  console.log('OK');
}).catch(e => { console.error(e.message); process.exit(1); });
