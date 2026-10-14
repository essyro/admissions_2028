const {defineConfig} = require('@playwright/test');
module.exports = defineConfig({
  testDir:'./e2e',
  timeout:45000,
  workers:1,
  reporter:'line',
  use:{baseURL:'http://127.0.0.1:8768',viewport:{width:1440,height:1000},browserName:'chromium'}
});