// Печать HTML в PDF через headless Chrome/Edge (Playwright) с отрисовкой Mermaid-диаграмм.
// Вызывается из docs/build_pdf.py: node render.mjs <in.html> <out.pdf> <footer-title> [<dir-for-svg> <svg-prefix>]
import { chromium } from 'playwright-core';
import { pathToFileURL, fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const [, , inHtml, outPdf, footerTitle = 'Взаимно', svgDir = '', svgPrefix = ''] = process.argv;
const here = path.dirname(fileURLToPath(import.meta.url));
const mermaidJs = path.join(here, 'node_modules', 'mermaid', 'dist', 'mermaid.min.js');

async function launch() {
  const attempts = [{ channel: 'chrome' }, { channel: 'msedge' }, {}];
  let lastErr;
  for (const opt of attempts) {
    try {
      return await chromium.launch({ headless: true, ...opt });
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

const browser = await launch();
const page = await browser.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
await page.goto(pathToFileURL(path.resolve(inHtml)).href, { waitUntil: 'load' });

const blocks = await page.locator('pre.mermaid').count();
let failed = 0;
if (blocks > 0) {
  await page.addScriptTag({ path: mermaidJs });
  const res = await page.evaluate(async () => {
    // eslint-disable-next-line no-undef
    mermaid.initialize({
      startOnLoad: false,
      theme: 'base',
      securityLevel: 'strict',
      fontFamily: '"Segoe UI","DejaVu Sans","Liberation Sans",Arial,sans-serif',
      themeVariables: {
        primaryColor: '#e8f0fb',
        primaryBorderColor: '#2f5aa8',
        primaryTextColor: '#14213d',
        lineColor: '#4a5a73',
        secondaryColor: '#f3f6fb',
        tertiaryColor: '#ffffff',
        fontSize: '14px',
      },
      flowchart: { useMaxWidth: true, htmlLabels: true },
      sequence: { useMaxWidth: true },
      er: { useMaxWidth: true },
      c4: { useMaxWidth: true },
    });
    const out = [];
    const nodes = Array.from(document.querySelectorAll('pre.mermaid'));
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      try {
        // eslint-disable-next-line no-undef
        const { svg } = await mermaid.render('mmd' + i, n.textContent);
        n.innerHTML = svg;
        n.classList.add('rendered');
        out.push({ i, ok: true });
      } catch (e) {
        n.classList.add('mermaid-failed');
        out.push({ i, ok: false, err: String(e).slice(0, 300) });
      }
    }
    return out;
  });
  for (const r of res) {
    if (!r.ok) {
      failed++;
      console.error(`mermaid block #${r.i} failed: ${r.err}`);
    }
  }
  if (svgDir) {
    fs.mkdirSync(svgDir, { recursive: true });
    const svgs = await page.$$eval('pre.mermaid.rendered svg', (els) => els.map((e) => e.outerHTML));
    svgs.forEach((s, k) => {
      const head = '<?xml version="1.0" encoding="UTF-8"?>\n';
      fs.writeFileSync(path.join(svgDir, `${svgPrefix}${String(k + 1).padStart(2, '0')}.svg`), head + s, 'utf8');
    });
  }
}
await page.emulateMedia({ media: 'print' });
await page.evaluate(() => document.fonts.ready);

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
await page.pdf({
  path: outPdf,
  format: 'A4',
  printBackground: true,
  displayHeaderFooter: true,
  headerTemplate: '<div></div>',
  footerTemplate: `<div style="width:100%;font-size:8px;color:#667;font-family:'Segoe UI','DejaVu Sans',Arial,sans-serif;padding:0 16mm;display:flex;justify-content:space-between"><span>${esc(footerTitle)}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`,
  margin: { top: '16mm', bottom: '18mm', left: '16mm', right: '16mm' },
  outline: true,
  tagged: true,
});
await browser.close();
console.log(`ok ${path.basename(outPdf)} diagrams=${blocks} failed=${failed} pageerrors=${errors.length}`);
if (failed > 0) process.exit(3);
