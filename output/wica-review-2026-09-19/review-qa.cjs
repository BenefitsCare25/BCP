const { chromium, expect } = require('../../frontend/node_modules/@playwright/test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    const page = await browser.newPage();
    for (const file of ['prototype.template.html', 'Inspro-WICA-Review.html', 'delivery/Inspro-WICA-Review.html']) {
      await page.goto(pathToFileURL(path.join(__dirname, file)).href);
      const scenarios = {
        async 'Enter in iReport submits Create incident'() {
          await page.evaluate(() => window.reviewScreen('new'));
          await page.locator('[name=description]').fill('Edited incident description for regression review.');
          await page.locator('[name=ireport]').fill('IR-REGRESSION-001');
          await page.locator('[name=ireport]').press('Enter');
          await expect(page.locator('#screen')).toHaveValue('documents');
          assert.equal(await page.evaluate(() => state.description), 'Edited incident description for regression review.');
          await expect(page.locator('#toast')).toContainText('Sample incident created');
        },
        async 'Enter saves recipient and Cancel preserves the previous save'() {
          await page.evaluate(() => window.reviewScreen('pending'));
          await page.locator('[data-action=sent]').click();
          await page.locator('[name=recipient]').fill('Regression claims team');
          await page.locator('[name=recipient]').press('Enter');
          await expect(page.locator('#main')).toContainText('Recorded sent');
          await expect(page.locator('#main')).toContainText('Regression claims team');
          await page.locator('[data-action=sent]').click();
          await page.locator('[name=recipient]').fill('Unsaved edit');
          await page.locator('[data-action=cancel-inline]').click();
          await expect(page.locator('#inline-form')).toHaveCount(0);
          assert.equal(await page.evaluate(() => state.sent.recipient), 'Regression claims team');
        },
        async 'Add period leaves settings unsaved until Save'() {
          await page.evaluate(() => {
            window.reviewScreen('settings');
            window.settingsSubmissions = 0;
            document.querySelector('#settings-form').addEventListener('submit', () => window.settingsSubmissions++);
          });
          await page.locator('[data-action=add-period]').click();
          await expect(page.locator('#period-rows tr')).toHaveCount(2);
          await expect(page.locator('#settings-saved')).toHaveText('');
          assert.equal(await page.evaluate(() => window.settingsSubmissions), 0);
          await page.getByRole('button', { name: 'Save WICA settings' }).click();
          await expect(page.locator('#settings-saved')).toHaveText('Saved in this demo');
          assert.equal(await page.evaluate(() => window.settingsSubmissions), 1);
        },
      };
      for (const [scenario, run] of Object.entries(scenarios)) {
        try {
          await run();
          results.push({ file, scenario, passed: true });
        } catch (error) {
          results.push({ file, scenario, passed: false, error: error.message.slice(0, 400) });
        }
      }
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify(results, null, 2));
  if (results.some(result => !result.passed)) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
