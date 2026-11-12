import {btn, e, head, icon, link, money, panel, pill, runTable, simpleTable, status} from './ui.mjs';

function activeRun(run) {
  if(!run) return panel('Active run','','<div class="panel-body active-summary"><p class="muted">No evaluations are running.</p>'+link('Open runs','arrow','runs')+'</div>');
  const body = '<div class="panel-body active-summary"><div><h3>'+e(run.name)+'</h3>'+
    '<p class="muted"><span class="mono">RUN-'+e(run.id)+'</span> · '+e(run.suite)+' · '+e(run.agent)+' / '+e(run.model)+'</p></div>'+
    '<div class="active-progress"><div><strong>'+run.done+' / '+run.total+'</strong><small>Trials completed</small></div>'+
    '<progress value="'+run.done+'" max="'+run.total+'" aria-label="Trials completed"></progress></div>'+
    link('Open run','arrow','run/'+encodeURIComponent(run.id))+'</div>';
  return panel('Active run','',body,status(run));
}

export function alertList(alerts) {
  if(!alerts.length) return '<p class="empty">No alerts.</p>';
  return '<div class="event-list">'+alerts.map(([glyph,style,title,desc,href])=>
    '<a class="event" href="#'+e(href)+'"><span class="stamp '+style+'">'+icon(glyph,15)+'</span>'+
    '<span class="grow"><strong>'+e(title)+'</strong><small>'+e(desc)+'</small></span>'+icon('chev',13)+'</a>'
  ).join('')+'</div>';
}

const confidence = ([,low,high]) => low+'–'+high+'%';

export function trendChart(rows) {
  if(!rows.length) return '<p class="empty">No valid runs in this period.</p>';
  const x = i => rows.length===1?261:42+i*438/(rows.length-1);
  const y = value => 133-value*1.15;
  const points = (series, bound) => rows.map((row,i)=>x(i)+','+y(row[series][bound]));
  const band = series => points(series,2).concat(points(series,1).reverse()).join(' ');
  const grid = [0,25,50,75,100].map(value=>
    '<line class="gridline" x1="42" y1="'+y(value)+'" x2="480" y2="'+y(value)+'"/>'+
    '<text x="34" y="'+(y(value)+3)+'" text-anchor="end">'+value+'%</text>'
  ).join('');
  const [date, atFive, allFive] = rows.at(-1);
  const metrics = [['At least 1 of 5 passed',atFive,''],['All 5 passed',allFive,'dashed']];
  const scoreStrip = '<div class="score-strip">'+metrics.map(([label,values,style])=>
    '<div><span><i class="legend-sample '+style+'"></i> '+label+'</span>'+
    '<strong>'+values[0]+'%</strong><small>95% CI '+confidence(values)+'</small></div>'
  ).join('')+'</div>';
  const chart = '<svg class="chart" viewBox="0 0 516 155" role="img" aria-label="Benchmark trend. Latest pass at five '+atFive[0]+' percent. Latest all five passed '+allFive[0]+' percent.">'+grid+
    '<polygon class="band" points="'+band(1)+'"/><polygon class="band band-secondary" points="'+band(2)+'"/>'+
    '<polyline class="capability" points="'+points(1,0).join(' ')+'"/><polyline class="reliability" points="'+points(2,0).join(' ')+'"/>'+
    rows.map(([label,a,b],i)=>
      '<circle class="point" cx="'+x(i)+'" cy="'+y(a[0])+'" r="2.7"><title>'+e(label)+': '+a[0]+'%, 95% CI '+confidence(a)+'</title></circle>'+
      '<circle class="point point-secondary" cx="'+x(i)+'" cy="'+y(b[0])+'" r="2.7"><title>'+e(label)+': '+b[0]+'%, 95% CI '+confidence(b)+'</title></circle>'+
      '<text x="'+x(i)+'" y="150" text-anchor="middle">'+e(label)+'</text>'
    ).join('')+'</svg>';
  const table = simpleTable(['Date','At least 1 of 5','95% CI','All 5','95% CI'],
    rows.map(([label,a,b])=>[e(label),a[0]+'%',confidence(a),b[0]+'%',confidence(b)]));
  return scoreStrip+chart+'<div class="trend-note"><small>Latest scores from '+e(date)+' · shaded areas show 95% confidence intervals</small></div>'+
    '<details class="trend-data"><summary>View data</summary>'+table+'</details>';
}

function trendTile(period, trend) {
  const selector = '<select id="range" class="field" aria-label="Trend period">'+
    [['7','Last 7 days'],['30','Last 30 days']].map(([value,label])=>'<option value="'+value+'" '+(period===value?'selected':'')+'>'+label+'</option>').join('')+'</select>';
  return panel('Benchmark performance','core-50 · Valid runs','<div class="panel-body" id="trend">'+trendChart(trend)+'</div>',selector);
}

function queueTile(running, queued) {
  return '<section class="card queue-tile" aria-labelledby="queue-title">'+
    '<div class="tile-heading"><h2 id="queue-title">Run queue</h2><span class="metric-icon">'+icon('runs',16)+'</span></div>'+
    '<div class="queue-metrics">'+
    '<a href="#runs/running"><strong>'+running+'</strong><small>Running</small></a>'+
    '<a href="#runs/queued"><strong>'+queued+'</strong><small>Queued</small></a></div></section>';
}

function budgetTile({spent, limit}) {
  return '<a class="card budget-tile" href="#settings"><div class="tile-heading"><span>Weekly spend</span>'+
    '<span class="metric-icon">'+icon('coins',16)+'</span></div>'+
    '<div class="budget-value"><strong>'+money(spent)+'</strong><small>of '+money(limit)+'</small></div>'+
    '<div class="budget-line"><progress value="'+spent+'" max="'+limit+'" aria-label="Weekly budget used"></progress>'+
    '<span class="tile-link">View budget '+icon('arrow',13)+'</span></div></a>';
}

export function overview({runs, alerts, budget, period, trend}) {
  const running = runs.filter(run=>run.status==='Running');
  const queued = runs.filter(run=>run.status==='Queued');
  const recent = panel('Recent runs','',runTable(runs.slice(0,4)),link('All runs','arrow','runs'));
  const heading = '<div class="overview-header">'+head('Overview','',btn('New run','plus','new','primary'))+'</div>';
  if(!trend) return heading+panel('Recent runs','',runs.length?runTable(runs):'<p class="empty">No runs in this project.</p>',runs.length?link('All runs','arrow','runs'):'');
  return heading+'<div class="bento">'+
    '<div class="bento-active">'+activeRun(running[0])+'</div>'+
    '<div class="bento-queue">'+queueTile(running.length,queued.length)+'</div>'+
    '<div class="bento-spend">'+budgetTile(budget)+'</div>'+
    '<div class="bento-trend">'+trendTile(period,trend)+'</div>'+
    '<div class="bento-alerts">'+panel('Needs attention','',alertList(alerts),pill(alerts.length+' open','neutral'),'<a href="#ops">Harness health '+icon('arrow',13)+'</a>')+'</div>'+
    '<div class="bento-history">'+recent+'</div></div>';
}
