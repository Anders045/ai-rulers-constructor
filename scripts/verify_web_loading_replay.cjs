// Independent browser checks for the two synthetic Web tasks. Run outside the target.
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('playwright');

async function checkFocus(page, opener, dismissal) {
  const trigger = page.getByRole('button', { name: opener, exact: true });
  await trigger.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await dialog.waitFor({ state: 'visible' });
  assert.equal(await dialog.getByRole('textbox').evaluate(el => el === document.activeElement), true,
    'opening the editor should focus its input');
  if (dismissal === 'Escape') {
    await page.keyboard.press('Escape');
  } else {
    await dialog.getByRole('button', { name: '取消' }).press('Enter');
  }
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await trigger.evaluate(el => el === document.activeElement), true,
    'the actual opener should regain focus');
}

function luminance(rgb) {
  const [r, g, b] = rgb.match(/[\d.]+/g).slice(0, 3).map(Number).map(value => {
    const fraction = value / 255;
    return fraction <= 0.04045 ? fraction / 12.92 : ((fraction + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

async function checkTheme(page, theme, changeTokens) {
  const pending = await page.getByText('待处理', { exact: true }).elementHandle();
  const closed = await page.getByText('已关闭', { exact: true }).elementHandle();
  const styles = await page.evaluate(({ theme, changeTokens, pending, closed }) => {
    document.documentElement.dataset.theme = theme;
    if (changeTokens) {
      // Change the authority at runtime to distinguish live references from copied colors.
      const values = { ink: '#193222', surface: '#ebffee', line: '#396f50' };
      for (const [name, value] of Object.entries(values)) {
        document.documentElement.style.setProperty('--info-' + name, value);
      }
    }
    function colors(el) {
      const css = getComputedStyle(el);
      return [css.color, css.backgroundColor, css.borderTopColor];
    }
    function tokenColors(group) {
      const probe = document.createElement('span');
      probe.style.cssText = `color:var(--${group}-ink);background:var(--${group}-surface);border:1px solid var(--${group}-line)`;
      document.body.append(probe);
      const result = colors(probe);
      probe.remove();
      return result;
    }
    return { pending: colors(pending), closed: colors(closed),
      info: tokenColors('info'), neutral: tokenColors('neutral') };
  }, { theme, changeTokens, pending, closed });
  assert.deepEqual(styles.pending, styles.info, 'pending badge should use the information palette');
  assert.deepEqual(styles.closed, styles.neutral, 'closed badge should retain its neutral palette');
  const ink = luminance(styles.pending[0]);
  const surface = luminance(styles.pending[1]);
  assert.ok((Math.max(ink, surface) + 0.05) / (Math.min(ink, surface) + 0.05) >= 4.5,
    'pending badge contrast should meet the project threshold');
}

async function main() {
  const [task, root] = process.argv.slice(2);
  if (!root || !['focus', 'theme'].includes(task)) throw Error('Usage: node verify_web_loading_replay.cjs focus|theme TARGET');
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE_PATH, chromiumSandbox: true });
  const checks = [];
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(3500);
    const file = pathToFileURL(path.resolve(root, 'index.html')).href;
    const cases = task === 'focus' ?
      ['编辑标题', '编辑负责人'].flatMap(opener => ['Escape', 'cancel'].map(dismiss => [opener, dismiss])) :
      ['light', 'dark'].flatMap(theme => [false, true].map(changeTokens => [theme, changeTokens]));
    for (const args of cases) {
      await page.goto(file);
      try {
        if (task === 'focus') await checkFocus(page, ...args);
        else await checkTheme(page, ...args);
        checks.push({ args, pass: true });
      } catch (error) {
        checks.push({ args, pass: false, error: error.message });
      }
    }
    console.log(JSON.stringify({ task, browser: browser.version(), checks }));
    if (checks.some(check => !check.pass)) process.exitCode = 1;
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 2; });
