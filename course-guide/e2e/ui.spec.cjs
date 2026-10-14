const {test,expect} = require('@playwright/test');

test('desktop data, PDF assets, filtering and CSV', async ({page}) => {
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('#metric-records')).toHaveText('1,162');
  await expect(page.locator('#evidence-list img')).toHaveCount(2);
  await expect.poll(()=>page.locator('#evidence-list img').evaluateAll(images=>images.every(image=>image.complete && image.naturalWidth>0))).toBe(true);
  await expect(page.locator('#model')).toHaveValue('gpt-oss:20b');
  await page.screenshot({path:'data/screenshots/desktop.png',fullPage:true});
  await page.locator('[data-view="catalog"]').click();
  await page.locator('#catalog-university').selectOption('중앙대');
  await expect(page.locator('#catalog-category option')).toHaveCount(5);
  await page.locator('#catalog-category').selectOption('진로선택');
  await page.locator('#catalog-search').fill('의학부');
  await expect(page.locator('#catalog-count')).toHaveText('6건');
  await expect(page.locator('#catalog-table tbody tr')).toHaveCount(6);
  await expect(page.locator('#catalog-table')).toContainText('中 3과목 이상');
  await page.locator('#catalog-search').fill('없는학과XYZ');
  await expect(page.locator('#catalog-count')).toHaveText('0건');
  await expect(page.locator('#catalog-table')).toContainText('조회된 데이터가 없습니다');
  const downloadPromise=page.waitForEvent('download');
  await page.locator('.download-button').click();
  const download=await downloadPromise;
  expect(download.suggestedFilename()).toBe('admissions-2028.csv');
  await page.locator('[data-view="documents"]').click();
  await expect(page.locator('#document-grid article')).toHaveCount(11);
  expect(errors).toEqual([]);
});

for(const width of [390,320]) {
  test(`mobile ${width} layout and controls`,async ({page})=>{
    await page.setViewportSize({width,height:844});
    await page.goto('/');
    await expect(page.locator('#metric-records')).toHaveText('1,162');
    await page.locator('[data-mode="graph"]').click();
    await expect(page.locator('[data-mode="graph"]')).toHaveAttribute('aria-pressed','true');
    await expect(page.locator('#top-k')).toBeDisabled();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:`data/screenshots/mobile-${width}.png`,fullPage:true});
    await page.locator('[data-view="catalog"]').click();
    await expect(page.locator('#catalog-table tbody tr')).toHaveCount(40);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  });
}

test('question failure unlocks form',async ({page})=>{
  await page.route('**/api/ask',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'테스트 연결 오류'})}));
  await page.goto('/');
  await expect(page.locator('#metric-records')).toHaveText('1,162');
  await page.locator('#question').fill('경희대 컴퓨터공학과 권장과목');
  await page.locator('#submit-question').click();
  await expect(page.locator('#answer-error')).toHaveText('테스트 연결 오류');
  await expect(page.locator('#submit-question')).toBeEnabled();
  await expect(page.locator('#question')).toBeEnabled();
});