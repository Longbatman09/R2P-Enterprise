/**
 * PDF text extractor — pure Node.js via pdf-parse (no native / C dependencies).
 *
 * Usage:
 *   node pdf_extractor.js <base64-pdf-bytes>
 *
 * Reads:
 *   argv[2]  — base64-encoded PDF bytes
 *
 * Writes to stdout:
 *   JSON array of { page: number, text: string } objects
 *
 * Exit codes:
 *   0 — success
 *   1 — error (message written to stderr)
 */

"use strict";

const fs   = require("fs");
const path = require("path");

// Resolve pdf-parse from the bundled node_modules or fall back to bare require()
const NODE_MODULES = "/tmp/node_modules";
const PDF_PARSE_CJS = "/tmp/node_modules/pdf-parse/dist/pdf-parse/cjs/index.cjs";
let pdfParse;
try {
  pdfParse = require(PDF_PARSE_CJS);
} catch (err) {
  try {
    pdfParse = require("pdf-parse");
  } catch (err2) {
    console.error(
      JSON.stringify({
        error: "pdf-parse module not found. Run: npm install pdf-parse --prefix /tmp",
        detail: err2.message,
      })
    );
    process.exit(1);
  }
}

// ── Read input ────────────────────────────────────────────────────────────────
const b64Input = process.argv[2];
if (!b64Input) {
  console.error(JSON.stringify({ error: "No base64 input provided as argv[2]" }));
  process.exit(1);
}

let pdfBuffer;
try {
  pdfBuffer = Buffer.from(b64Input, "base64");
} catch (err) {
  console.error(JSON.stringify({ error: `Invalid base64: ${err.message}` }));
  process.exit(1);
}

// ── Parse ─────────────────────────────────────────────────────────────────────
pdfParse(pdfBuffer)
  .then((data) => {
    // data.npages — total pages
    // data.text   — full concatenated text (may include page markers)
    // We'll split on form-feed characters if present, or just emit one block
    let pages;
    if (data.text && data.text.includes("\f")) {
      // Split by form-feed (some PDFs insert these as page separators)
      const rawPages = data.text.split("\f").filter((p) => p.trim().length > 0);
      pages = rawPages.map((text, idx) => ({
        page: idx + 1,
        text: text.trim(),
      }));
    } else if (data.npages > 1) {
      // Try page-level extraction via internal iterator if available
      try {
        pages = [];
        // pdf-parse exposes per-page via _index in some versions
        // otherwise we fall back to a single-page entry below
        const chunks = data.text.match(/.{1,5000}/g) || [data.text];
        pages = chunks.map((text, idx) => ({
          page: idx + 1,
          text: text.trim(),
        }));
      } catch {
        pages = [{ page: 1, text: data.text.trim() }];
      }
    } else {
      pages = [{ page: 1, text: (data.text || "").trim() }];
    }

    const output = pages.map((p) => ({
      page: p.page,
      text: p.text,
    }));

    process.stdout.write(JSON.stringify(output, null, 2));
    process.exit(0);
  })
  .catch((err) => {
    console.error(JSON.stringify({ error: `pdf-parse failed: ${err.message}` }));
    process.exit(1);
  });
