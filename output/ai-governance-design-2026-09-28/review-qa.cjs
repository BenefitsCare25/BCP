const { chromium, expect } = require('../../frontend/node_modules/@playwright/test');
const { default: AxeBuilder } = require('../../frontend/node_modules/@axe-core/playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const screenImages = {
  claim:'02-claim-decision.png', member:'04-member-claim-journey.png',
  firm:'01-firm-ai-oversight.png', release:'03-release-validation.png',
  evidence:'qa-pdf-page-6.png', suppliers:'qa-pdf-page-7.png',
};
async function awaitScreen(page, screen) {
  // A hash link click completes before hashchange renders the requested screen.
  // Assert the requested state and source before accepting image completion.
  await expect(page.locator(`[data-screen="${screen}"]`)).toHaveAttribute('aria-current','page');
  await expect(page.locator('#screen-image')).toHaveAttribute('src',screenImages[screen]);
  await expect.poll(() => page.locator('#screen-image').evaluate((img, source) =>
    img.complete && img.naturalWidth > 0 && img.currentSrc === new URL(source, location.href).href,
  screenImages[screen])).toBe(true);
  await expect(page.locator('#annotations li')).toHaveCount(3);
  await expect(page.locator('#image-error')).toBeHidden();
}

async function selectScreen(page, screen) {
  await page.locator(`[data-screen="${screen}"]`).click();
  await awaitScreen(page, screen);
}

(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1600,height:1080},deviceScaleFactor:1});
    const page = await context.newPage();
    const errors = [];
    const failures = [];
    page.on('pageerror',error => errors.push(error.message));
    page.on('response',response => {if (response.status() >= 400) failures.push(`${response.status()} ${response.url()}`);});
    const base = process.env.REVIEW_BASE_URL || 'http://127.0.0.1:4178';
    await page.goto(base, {waitUntil:'networkidle'});
    for (let cycle = 0; cycle < 5; cycle++) {
      for (const screen of Object.keys(screenImages)) await selectScreen(page, screen);
    }
    await selectScreen(page, 'claim');
    await page.locator('#zoom').click();
    await expect(page.locator('#zoom')).toHaveAttribute('aria-pressed','true');
    assert.equal(await page.locator('#image-viewport').evaluate(el => el.scrollWidth > el.clientWidth),true);
    await page.locator('#zoom').click();
    await expect(page.locator('#zoom')).toHaveAttribute('aria-pressed','false');
    for (let cycle = 0; cycle < 5; cycle++) {
      await page.locator('[data-screen="member"]').focus();
      await page.keyboard.press('Enter');
      await awaitScreen(page, 'member');
      await selectScreen(page, 'claim');
    }
    await page.screenshot({path:path.join(__dirname,'qa-review-desktop.png'),fullPage:true});
    const axe = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
    assert.deepEqual(axe.violations.map(v => ({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)})),[]);
    for (const width of [320,390,768]) {
      await page.setViewportSize({width,height:844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true,`Gallery overflow at ${width}`);
      await selectScreen(page, 'member');
      if (width === 390) await page.screenshot({path:path.join(__dirname,'qa-review-mobile.png'),fullPage:true});
    }
    for (const document of ['implementation-plan','ux-specification','readiness-assessment']) {
      const response = await page.goto(`${base}/${document}.html`,{waitUntil:'networkidle'});
      assert.equal(response.status(),200);
      assert.equal(await page.locator('article h1').count(),1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true,`Document overflow ${document}`);
      const links = await page.locator('a[href]').evaluateAll(links=>links.map(a=>a.href).filter(h=>h.startsWith(location.origin)));
      for (const href of [...new Set(links)]) assert.equal((await page.request.get(href)).status(),200,href);
    }
    await page.setViewportSize({width:1600,height:1080});
    await page.goto(`${base}/implementation-plan.html`,{waitUntil:'networkidle'});
    await page.screenshot({path:path.join(__dirname,'qa-review-plan.png'),fullPage:false});
    await page.setViewportSize({width:320,height:844});
    for (const document of ['implementation-plan','ux-specification','readiness-assessment']) {
      await page.goto(`${base}/${document}.html`,{waitUntil:'networkidle'});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),true,`Document overflow at 320: ${document}`);
      const docAxe = await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
      assert.deepEqual(docAxe.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),[],`Accessibility: ${document}`);
    }
    const pdf = await page.request.get(`${base}/Inspro-AI-Governance-Design.pdf`);
    assert.equal(pdf.status(),200);
    assert.equal((await pdf.body()).subarray(0,5).toString(),'%PDF-');
    assert.equal((await page.request.get(`${base}/.env`)).status(),404);
    assert.equal((await page.request.get(`${base}/server-info.json`)).status(),404);
    assert.equal((await page.request.get(`${base}/serve_review.py`)).status(),404);
    assert.deepEqual(errors,[]);
    assert.deepEqual(failures,[]);
    const result = {status:'passed',screens:6,documents:3,viewportWidths:[1600,768,390,320],checks:['screen selection','image loading','zoom toggle','document links','PDF signature','keyboard navigation','automated WCAG checks on gallery and all three documents','no page-level horizontal overflow','no browser errors','server serves only review artifacts']};
    fs.writeFileSync(path.join(__dirname,'review-qa-result.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exitCode=1;});
