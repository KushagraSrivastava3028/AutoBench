import test from 'node:test';
import assert from 'node:assert/strict';
import {overview, trendChart} from '../dist/overview.mjs';
import {runs, attention, weeklyBudget, trendPeriods} from '../dist/demo-data.mjs';

test('overview uses a single title and links queue totals to their filters', () => {
  const queuedRun = {...runs[0], id:'0300', status:'Queued'};
  const html = overview({
    runs:[...runs,queuedRun], alerts:attention, budget:weeklyBudget, period:'7', trend:trendPeriods['7']
  });
  assert.equal((html.match(/<h1>Overview<\/h1>/g) || []).length, 1);
  assert.doesNotMatch(html, /project overview/i);
  assert.match(html, /href="#runs\/running"[^]*?<strong>2<\/strong>/);
  assert.match(html, /href="#runs\/queued".*<strong>1<\/strong>/);
  assert.match(html, /href="#run\/0248">Open run/);
  assert.match(html, /href="#run\/0244"/);
});

test('periods render distinct labels and an accessible chart', () => {
  assert.match(trendChart(trendPeriods['7']), /Nov 4</);
  assert.match(trendChart(trendPeriods['30']), /Oct 12</);
  const html = trendChart(trendPeriods['30']);
  assert.match(html, /role="img" aria-label=/);
  assert.match(html, /95% CI 68.4–82.8%/);
  assert.match(html, /<summary>View data<\/summary>/);
  assert.match(html, /<td>Nov 10<\/td><td>75.6%<\/td><td>68.4–82.8%<\/td>/);
  assert.match(trendChart([]), /No valid runs in this period/);
});

test('an empty project keeps the run creation action', () => {
  const html = overview({runs:[], alerts:[], budget:null, period:'7', trend:null});
  assert.match(html, /No runs in this project/);
  assert.match(html, /data-action="new"/);
  assert.doesNotMatch(html, /Weekly spend/);
});
