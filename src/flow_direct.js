#!/usr/bin/env node
const { chromium } = require('/root/google-flow-browser-mcp/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const COOKIES_PATH = path.join(ROOT, 'data/flow_cookies.json');
const PROJECT_URL = 'https://flow.google.com/project/98c74f95-5205-465b-aa5a-82bb210086c3';

function parseArgs() {
  const args = process.argv.slice(2);
  const params = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith('--')) {
      const key = args[i].slice(2);
      params[key] = args[i + 1] || true;
      i++;
    }
  }
  return params;
}

async function generateFlowVideo(prompt, outputPath, targetDuration = 5) {
  if (!fs.existsSync(COOKIES_PATH)) {
    throw new Error(`Flow cookies not found at ${COOKIES_PATH}`);
  }

  const browser = await chromium.launch({
    executablePath: '/usr/bin/chromium',
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    acceptDownloads: true
  });

  const rawCookies = JSON.parse(fs.readFileSync(COOKIES_PATH, 'utf8'));
  for (const c of rawCookies) {
    if (!c.name || !c.value) continue;
    try {
      await context.addCookies([{
        name: c.name,
        value: c.value,
        domain: c.domain || '.google.com',
        path: c.path || '/',
        secure: c.name.startsWith('__Secure-') || c.secure || false,
      }]);
    } catch (_) {}
  }

  const page = await context.newPage();
  console.log(`[GoogleFlowDirect] Loading project: ${PROJECT_URL}`);
  await page.goto(PROJECT_URL, { waitUntil: 'domcontentloaded', timeout: 35000 });
  await page.waitForTimeout(5000);

  // Focus prompt editor
  const editor = page.locator('.ProseMirror').first();
  await editor.waitFor({ state: 'visible', timeout: 15000 });
  await editor.click();
  
  // Format prompt for vertical video
  const fullPrompt = `A cinematic 9:16 vertical video: ${prompt}`;
  console.log(`[GoogleFlowDirect] Entering prompt: "${fullPrompt}"`);
  await editor.fill(fullPrompt);
  await page.waitForTimeout(1000);

  // Click start generation
  const genBtn = page.locator('button[aria-label="Start generation"]').first();
  await genBtn.waitFor({ state: 'visible', timeout: 5000 });
  await genBtn.click();
  console.log('[GoogleFlowDirect] Start generation clicked');

  // Check if confirmation modal or Always Approve appears
  for (let i = 0; i < 6; i++) {
    await page.waitForTimeout(2000);
    const alwaysApprove = page.locator('text=Always approve').first();
    const approve = page.locator('text=Approve').first();
    if (await alwaysApprove.isVisible().catch(() => false)) {
      console.log('[GoogleFlowDirect] Auto-approving generation (Always approve)...');
      await alwaysApprove.click();
      break;
    } else if (await approve.isVisible().catch(() => false)) {
      console.log('[GoogleFlowDirect] Auto-approving generation (Approve)...');
      await approve.click();
      break;
    }
  }

  // Poll for the download button (generation in progress)
  console.log('[GoogleFlowDirect] Waiting for Omni Flash video to render in Google Flow...');
  const dlBtn = page.locator('button[aria-label="Download batch"]').first();
  const maxWaitMs = 180000; // 3 minutes max
  const startTime = Date.now();
  let downloaded = false;

  const tempZip = path.join(ROOT, 'output', `flow_batch_${Date.now()}.zip`);

  while (Date.now() - startTime < maxWaitMs) {
    await page.waitForTimeout(5000);
    const isVisible = await dlBtn.isVisible().catch(() => false);
    if (isVisible) {
      console.log('[GoogleFlowDirect] Video ready! Initiating download...');
      try {
        const [download] = await Promise.all([
          page.waitForEvent('download', { timeout: 30000 }),
          dlBtn.click()
        ]);
        await download.saveAs(tempZip);
        downloaded = true;
        break;
      } catch (err) {
        console.log('[GoogleFlowDirect] Download event retry:', err.message);
      }
    }
  }

  await browser.close();

  if (!downloaded || !fs.existsSync(tempZip)) {
    throw new Error('Google Flow generation timed out or failed to download');
  }

  // Extract MP4 from downloaded ZIP
  const extractDir = path.join(ROOT, 'output', `extracted_${Date.now()}`);
  fs.mkdirSync(extractDir, { recursive: true });
  execSync(`unzip -o "${tempZip}" -d "${extractDir}"`);
  
  const files = fs.readdirSync(extractDir);
  const mp4File = files.find(f => f.endsWith('.mp4'));
  if (!mp4File) {
    throw new Error(`No mp4 found in extracted zip: ${files}`);
  }

  const finalVideo = path.join(extractDir, mp4File);
  fs.copyFileSync(finalVideo, outputPath);
  
  // Cleanup temp files
  try {
    fs.rmSync(tempZip, { force: true });
    fs.rmSync(extractDir, { recursive: true, force: true });
  } catch (_) {}

  console.log(`[GoogleFlowDirect] SUCCESS: Video generated and saved to ${outputPath}`);
  return outputPath;
}

async function main() {
  const params = parseArgs();
  const prompt = params.prompt || 'Cinematic high performance mindset visual';
  const output = params.output || path.join(ROOT, 'output', 'direct_flow_scene.mp4');
  const duration = parseFloat(params.duration || 5);

  try {
    await generateFlowVideo(prompt, output, duration);
    process.exit(0);
  } catch (err) {
    console.error(`[GoogleFlowDirect] Error: ${err.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}
