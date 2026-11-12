import {icon, e, btn, link, pill, money, status, head, panel, stat, simpleTable, runTable} from './ui.mjs';
import {tasks, states, runs, suiteRows, attention, weeklyBudget, trendPeriods} from './demo-data.mjs';
import {overview, trendChart, alertList} from './overview.mjs';

const nav = [
  ['Workspace',[['home','Overview','home'],['runs','Runs','runs'],['compare','Compare','compare'],['leaderboard','Leaderboard','award']]],
  ['Library',[['suites','Suites','layers'],['tasks','Tasks','tasks'],['agents','Agents & models','bot']]],
  ['Manage',[['settings','Settings','settings']]]
];
const main = document.querySelector('#main');
const sidebar = document.querySelector('#sidebar');
const topbar = document.querySelector('#top');
const modal = document.querySelector('#modal');
let page = 'home', record = '', runTab = 'Overview', runFilter = 'all', taskFilter = 'All outcomes', taskQuery = '';
let compareA = '0245', compareB = '0247', taskStep = 0, uploadName = '', publishedTasks = [];
const primaryProject = 'Agent evaluation';
const runStatuses = new Map([
  ['all','All statuses'], ['running','Running'], ['complete','Complete'], ['gate-failed','Gate failed'],
  ['invalid','Invalid · harness'], ['queued','Queued'], ['cancelled','Cancelled']
]);
let project = primaryProject, range = '7', calibrated = true;
const projectRuns = () => runs.filter(run => (run.project || primaryProject) === project);
function home() {
  const isPrimary = project === primaryProject;
  return overview({
    runs:projectRuns(),
    alerts:isPrimary ? attention : [],
    budget:isPrimary ? weeklyBudget : null,
    period:range,
    trend:isPrimary ? trendPeriods[range] : null
  });
}
try { document.documentElement.dataset.theme = localStorage.getItem('autobench-theme') || 'light'; } catch {}
const route = (where) => { if (location.hash === '#'+where) render(); else location.hash = where; };
function sidebarView() {
  const active = ['run','trial'].includes(page)?'runs':(['task','new-task'].includes(page)?'tasks':(page==='suite'?'suites':page));
  sidebar.innerHTML = '<a class="brand" href="#home"><span class="mark">'+icon('logo',19)+'</span>AutoBench</a>'+
    '<button class="workspace" data-action="project"><span class="initial">'+(project==='Agent evaluation'?'A':'S')+'</span><span class="grow"><strong>'+e(project)+'</strong></span>'+icon('down',13)+'</button>'+
    '<nav aria-label="Main navigation">'+nav.map(([group,links])=>'<div class="nav-group"><div class="nav-label">'+group+'</div>'+links.map(([id,label,glyph])=>'<a class="nav-item" href="#'+id+'" '+(id===active?'aria-current="page"':'')+'>'+icon(glyph,17)+e(label)+'</a>').join('')+'</div>').join('')+'</nav>'+
    '<div class="profile"><span class="demo-dot" aria-hidden="true"></span><small>Demo data</small></div>';
}
function topView() {
  topbar.innerHTML = '<div class="crumb"><button class="icon-btn menu-toggle" data-action="menu" aria-label="Open navigation" aria-controls="sidebar" aria-expanded="false">'+icon('menu')+'</button><strong class="project-crumb">'+e(project)+'</strong></div>'+
    '<div class="top-actions"><button class="btn search-top" data-action="search" aria-label="Search workspace">'+icon('search')+'<span>Search</span><kbd>'+(/Mac|iPhone|iPad/.test(navigator.platform)?'⌘ K':'Ctrl K')+'</kbd></button><a class="pill pass" href="#ops">'+icon('pulse')+' Harness healthy</a><button class="icon-btn" aria-label="Toggle color theme" data-action="theme">'+icon(document.documentElement.dataset.theme==='dark'?'sun':'moon')+'</button></div>';
}
function runList() {
  const options = [...runStatuses].map(([key,label])=>'<option value="'+key+'" '+(runFilter===key?'selected':'')+'>'+label+'</option>').join('');
  return head('Runs','',btn('New run','plus','new','primary'))+
    panel('Run history','', '<div class="toolbar"><select class="field" id="run-filter" aria-label="Run status">'+options+'</select></div>'+runTable(runsForProject()));
}
function runsForProject() {
  return projectRuns().filter(run => runFilter === 'all' || run.status === runStatuses.get(runFilter));
}
function metricCard(label,value,detail) {
  return '<article class="card stat"><div class="stat-head">'+label+'</div><div class="stat-value mono">'+value+'</div><small>'+detail+'</small></article>';
}
function metricStrip(r) {
  const labels = ['pass@1','pass@'+r.k,'pass<sup>'+r.k+'</sup>','Reliability gap'];
  const gap=r.metrics?.length===3?(Number(r.metrics[1])-Number(r.metrics[2])).toFixed(1)+' pts':'—';
  return '<div class="cards metric-grid">'+labels.map((l,i)=>metricCard(l,i===3?gap:r.metrics?.[i]?r.metrics[i]+'%':'—',i===3?'pass@'+r.k+' − pass<sup>'+r.k+'</sup>':r.cis?.[i]?'95% CI · '+r.cis[i]+'%':r.k===1&&i>0?'Covered by pass@1':r.status==='Invalid · harness'?'Hidden · harness invalid':'Awaiting finalized statistics')).join('')+'</div>';
}
function rowOutcomes(task, r) {
  if(r.status==='Queued'||(r.status==='Cancelled'&&!r.done)) return 'Q'.repeat(r.k);
  const paired = {
    '0245':{'build-cython-ext':'FFFFF','repair-broken-lockfile':'PPPFF','restore-corrupt-index':'FFHFF'},
    '0247':{'build-cython-ext':'PPPFF','repair-broken-lockfile':'FFFFF','restore-corrupt-index':'FFHFF'}
  };
  if(paired[r.id]?.[task[0]]) return paired[r.id][task[0]];
  const template = task[2].slice(0,r.k);
  return r.status==='Complete'?template.replace(/Q/g,'P').replace(/R/g,'F'):template;
}
function matrixRows(r) {
  const filtered = tasks.filter(t=>{
    const outcomes = rowOutcomes(t,r);
    return (t[0]+' '+t[1]).toLowerCase().includes(taskQuery.toLowerCase()) &&
      (taskFilter==='All outcomes'||(taskFilter==='Agent failures'&&outcomes.includes('F'))||(taskFilter==='Infra retries'&&outcomes.includes('R'))||(taskFilter==='Queued'&&outcomes.includes('Q')));
  });
  if(!filtered.length) return '<p class="empty">No tasks match these filters.</p>';
  return '<div class="table-scroll"><table class="matrix"><thead><tr><th scope="col">Task / category</th>'+Array.from({length:r.k},(_,i)=>'<th scope="col" class="mono">k'+i+'</th>').join('')+'<th scope="col">Passed / graded</th></tr></thead><tbody>'+
    filtered.map(t=>{
      const outcomes=rowOutcomes(t,r);
      return '<tr><th scope="row"><a href="#task/'+t[0]+'">'+t[0]+'</a><span class="cell-sub">'+t[1]+'</span></th>'+
        [...outcomes].map((out,i)=>'<td><button class="trial '+states[out][2]+'" data-action="trial" data-key="'+r.id+'/'+t[0]+'/'+i+'" title="'+states[out][1]+'" aria-label="'+t[0]+' attempt '+(i+1)+': '+states[out][1]+'">'+states[out][0]+'</button></td>').join('')+
        '<td class="mono">'+[...outcomes].filter(x=>x==='P').length+' / '+[...outcomes].filter(x=>'PFH'.includes(x)).length+'</td></tr>';
    }).join('')+'</tbody></table></div>';
}
function runDetail(r) {
  if(!r) return missing('Run');
  const notice = r.status==='Invalid · harness'?'<div class="callout infra"><strong>Invalid · harness.</strong> Infra errors exceeded the 2% threshold. Scores are hidden and this run is excluded from the leaderboard.</div>':
    r.status==='Gate failed'?'<div class="callout fail"><strong>CI gate failed.</strong> Three newly failing smoke tasks need review.</div>':'';
  const actions=link('Back to runs','','runs')+(r.status==='Running'||r.status==='Queued'?btn('Cancel run','close','cancel-run'):'');
  const filters=['All outcomes','Agent failures','Infra retries','Queued'];
  const matrix=panel('Trial matrix','Select a cell to inspect its trajectory, filesystem diff and verdict.',
    '<div class="toolbar"><input id="task-query" class="field search-field" type="search" placeholder="Find a task…" aria-label="Find a task" value="'+e(taskQuery)+'"><select id="task-filter" class="field" aria-label="Trial outcome">'+filters.map(o=>'<option '+(taskFilter===o?'selected':'')+'>'+o+'</option>').join('')+'</select></div><div id="matrix">'+matrixRows(r)+'</div>'+
    '<div class="matrix-legend">'+Object.values(states).map(s=>pill(s[0]+' '+s[1],s[2])).join('')+'</div>',status(r),'Sample tasks from the selected suite.');
  const tabs=['Overview','Failures','Cost','Latency','Workers','Provenance','Logs'];
  const info = {
    Failures:panel('Failure breakdown','Infrastructure retries and agent failures are tracked separately.',simpleTable(['Class','Example task','Inspection'],[...tasks.flatMap(t=>[...rowOutcomes(t,r)].map((o,i)=>[o,t[0],i])).filter(([o])=>o==='F'||o==='R').map(([o,name,i])=>[o==='F'?'Agent failure':'Infrastructure retry',name,'<a href="#trial/'+r.id+'/'+name+'/'+i+'">Open trial</a>'])])),
    Cost:panel('Run spend','Sample provider usage.', '<div class="panel-body"><dl><dt>Spend</dt><dd class="mono">'+money(r.cost)+'</dd><dt>Budget</dt><dd class="mono">'+money(r.budget)+'</dd></dl></div>'),
    Latency:panel('Trial latency','Sample runtime percentiles.', '<div class="panel-body"><dl><dt>p50</dt><dd class="mono">43.8 s</dd><dt>p95</dt><dd class="mono">92.4 s</dd></dl></div>'),
    Workers:panel('Worker pool','Sample capacity and sandbox runtime.',simpleTable(['Pool','Slots','Runtime'],[['eval-default','8','gVisor (runsc)']])),
    Provenance:panel('Run provenance','Reproducibility metadata.', '<div class="panel-body"><dl><dt>Suite</dt><dd class="mono">'+r.suite+'</dd><dt>Agent</dt><dd>'+e(r.agent)+' · 1.4.2</dd><dt>Model</dt><dd>'+e(r.model)+'</dd><dt>Harness</dt><dd class="mono">v1.0.3</dd><dt>Seed set</dt><dd class="mono">'+Array.from({length:r.k},(_,i)=>4210+i).join(', ')+'</dd></dl></div>'),
    Logs:panel('Execution log','Illustrative lifecycle events.', '<div class="panel-body"><pre><code>run.planned  suite='+r.suite+'\nqueue.configured  trials='+r.total+'\nrun.status  state='+e(r.status)+'</code></pre></div>')
  };
  return head(e(r.name),'<span class="mono">RUN-'+r.id+'</span> · '+r.suite+' · '+e(r.agent)+' / '+e(r.model)+' · k='+r.k,actions,'RUN DETAIL')+notice+
    '<div class="inline" style="margin-bottom:13px">'+status(r)+'<small class="mono">'+r.done+' / '+r.total+' trials · '+money(r.cost)+' of '+money(r.budget)+'</small></div>'+metricStrip(r)+
    '<div class="tabs" aria-label="Run sections">'+tabs.map(t=>'<button class="tab" data-action="run-tab" data-key="'+t+'" aria-pressed="'+(runTab===t)+'">'+t+'</button>').join('')+'</div>'+
    (runTab==='Overview'?matrix:info[runTab]);
}
function trialDetail(key, full) {
  const [id,name,index]=key.split('/');
  const r=runs.find(item=>item.id===id);
  const t=tasks.find(item=>item[0]===name);
  const out=t&&r?rowOutcomes(t,r)[Number(index)]:null;
  if(!out) return missing('Trial');
  const s=states[out];
  const runHref='run/'+id;
  const command='autobench replay --trial '+id+'-'+name+'-'+index+' --interactive';
  const info = out==='Q' || out==='R'
    ? '<div class="callout '+s[2]+'">'+(out==='Q'?'This trial is waiting for a worker. Artifacts will appear after execution.':'A sandbox startup error interrupted this trial. The harness is retrying it. The agent has no verdict.')+'</div>'
    : '<div class="'+(full?'split':'stack')+'">'+
    panel('Trajectory','Sample event stream','<div class="panel-body stack"><details><summary><span class="mono">00:00</span> · Trial instruction</summary><p>Complete the task in the sandbox. The verifier checks the final filesystem state.</p></details><details open><summary><span class="mono">00:04</span> · Tool call · shell</summary><pre><code>$ ls -la /app\n$ python -m pytest -q</code></pre></details><details><summary><span class="mono">00:27</span> · Tool result</summary><p>'+(out==='P'?'All verification items passed.':'The verifier found an unmet condition.')+'</p></details></div>')+
    '<div class="stack">'+panel('Filesystem diff','Setup snapshot → frozen container','<div class="panel-body"><pre><code>diff --git a/src/solution.py b/src/solution.py\n@@ -12,1 +12,2 @@\n- return process(payload)\n+ result = process(payload)\n+ return result</code></pre></div>')+
    panel('Verdict','Final-state verification','<div class="panel-body"><ul class="simple-list"><li>✓ Expected output file exists</li><li>'+s[0]+' Handles the edge-case fixture</li></ul></div>')+'</div></div>'+
    panel('Replay locally','Illustrative CLI command','<div class="panel-body inline"><code>'+command+'</code>'+btn('Copy command','','copy')+'</div>');
  return '<div class="dialog-head"><div><div class="eyebrow">Trial '+e(id)+' · seed '+(4210+Number(index))+'</div><h2 id="dialog-title">'+e(name)+'</h2><small>Attempt '+(Number(index)+1)+'</small></div><div class="actions">'+pill(s[1],s[2])+(full?link('Back to run','',''+runHref):btn('Close','close','close'))+'</div></div>'+info+
    (full?'':link('Open full inspector','arrow','trial/'+key));
}
function compare() {
  const a=runs.find(r=>r.id===compareA);
  const b=runs.find(r=>r.id===compareB);
  const options=selected=>runs.filter(r=>r.status==='Complete'&&r.suite==='core-50').map(r=>'<option value="'+r.id+'" '+(r.id===selected?'selected':'')+'>'+e(r.name)+' · '+r.id+'</option>').join('');
  const same=a.id===b.id;
  const forward=a.id==='0245';
  const delta=forward?'+7.2 pts':'−7.2 pts';
  return head('Compare runs','Review paired changes with the same suite and seed set.','','ANALYSIS')+
    '<div class="split" style="margin-bottom:15px">'+[['Baseline',a,'compare-a'],['Candidate',b,'compare-b']].map(([label,r,id])=>panel(label,'core-50 · Same seed set','<div class="panel-body stack"><select class="field" id="'+id+'" aria-label="'+label+'">'+options(r.id)+'</select><div class="inline">'+pill('pass⁵ '+r.metrics[2]+'%','neutral')+'<small>95% CI · '+r.cis[2]+'%</small></div></div>')).join('')+'</div>'+
    (same?'<div class="callout neutral">Select two different runs to see paired results.</div>':'<div class="callout neutral"><strong>Paired pass⁵ difference '+delta+'</strong> · 95% CI '+(forward?'−2.4 to +15.9':'−15.9 to +2.4')+' pts · Interval includes zero</div>')+
    panel('Task flips','Representative sample from the paired runs.',simpleTable(['Change','Task','Baseline','Candidate','Evidence'],same?[]:[
      [pill(forward?'Newly passing':'Newly failing',forward?'pass':'fail'),'build-cython-ext',forward?'0 / 5':'3 / 5',forward?'3 / 5':'0 / 5','<a href="#trial/'+b.id+'/build-cython-ext/0">Inspect trial ↗</a>'],
      [pill(forward?'Newly failing':'Newly passing',forward?'fail':'pass'),'repair-broken-lockfile',forward?'3 / 5':'0 / 5',forward?'0 / 5':'3 / 5','<a href="#trial/'+b.id+'/repair-broken-lockfile/0">Inspect trial ↗</a>'],
      [pill('Still failing','neutral'),'restore-corrupt-index','0 / 5','0 / 5','<a href="#trial/'+b.id+'/restore-corrupt-index/0">Inspect trial ↗</a>']
    ],'Choose a distinct baseline and candidate.'));
}
function leaderboard() {
  return head('Leaderboard','Best valid run per agent and model, with statistical ties called out.',btn('Export CSV','download','export'),'ANALYSIS')+
    panel('Core reference suite','core-50 · k=5 · Confidence intervals shown under each score',simpleTable(['Rank','Agent / model','pass@1','pass⁵','Reliability gap','Cost / solve','p95 latency','Run'],[
      ['01','Claude Code / Claude Opus','51.8%<span class="cell-sub mono">45.2–58.4%</span>','29.2%<span class="cell-sub mono">22.6–35.8%</span>','46.4 pts','$1.31','92.4 s','<a href="#run/0247">RUN-0247 ↗</a>'],
      ['01 · tied','Codex CLI / GPT-5','49.1%<span class="cell-sub mono">42.5–55.7%</span>','27.5%<span class="cell-sub mono">21.0–34.1%</span>','45.0 pts','$0.98','86.2 s','Historical sample']
    ]),'','Ranks share a position when their pass⁵ intervals overlap the leader.');
}
function suites() {
  return head('Suites','Browse pinned task collections and their recent runs.','','BENCHMARK LIBRARY')+
    panel('Registered suites','Sample versioned collections',simpleTable(['Suite','Purpose','Tasks','Pinned version'],suiteRows.map(([id,label,count,v])=>['<a class="mono" href="#suite/'+id+'">'+id+'</a>',label,count,'<span class="mono">'+v+'</span>'])));
}
function suiteDetail(id) {
  const s=suiteRows.find(item=>item[0]===id);
  if(!s) return missing('Suite');
  return head(s[0],s[1]+' · '+s[2]+' pinned tasks · '+s[3],link('All suites','','suites'),'SUITE DETAIL')+
    panel('Recent runs','Runs using this suite.',runTable(runs.filter(r=>r.suite===id)));
}
function taskList() {
  return head('Tasks','Review benchmarks, versions, and oracle health.',link('New task','plus','new-task','primary'),'BENCHMARK LIBRARY')+
    panel('Task catalogue','Sample benchmark tasks',simpleTable(['Task','Category','Version','Oracle'],[...tasks,...publishedTasks].map(([id,category])=>['<a href="#task/'+encodeURIComponent(id)+'">'+e(id)+'</a>',e(category),'<span class="mono">v1</span>',id==='restore-corrupt-index'?pill('Drift detected','warn','alert'):pill('Passing','pass','check')])));
}
function taskDetail(id) {
  const t=[...tasks,...publishedTasks].find(item=>item[0]===id);
  if(!t) return missing('Task');
  return head(e(t[0]),e(t[1])+' · version 1',link('All tasks','','tasks'),'TASK DETAIL')+
    (id==='restore-corrupt-index'?'<div class="callout warn">Oracle drift detected during the nightly validation check.</div>':'')+
    '<div class="split">'+panel('Instruction','Sample task specification','<div class="panel-body"><p>Complete '+e(t[0])+' in the sandbox and leave the expected output in <code>/app</code>. The verifier grades the final container state.</p></div>')+
    panel('Environment and calibration','Pinned runtime metadata','<div class="panel-body"><dl><dt>Runtime</dt><dd>gVisor · network disabled</dd><dt>Image</dt><dd><code>sha256:bfcc61b…d709</code></dd><dt>Difficulty</dt><dd>Medium · sample panel</dd></dl></div>')+'</div>';
}
const gates=['Required files','Task schema','Image build','Image security','Oracle pass','Null agent fail','Determinism','Hermeticity','Grader isolation','Artifact capture','Resource limits'];
function wizard() {
  const labels=['Upload','Validate','Calibrate','Publish'];
  const contents=[
    '<label class="form-label">Task archive<input type="file" class="field" id="archive" accept=".tar,.gz,.tgz,.zip"></label><label class="form-label">Or enter a Git path<input type="text" class="field" id="git-path" placeholder="git.example.com/benchmarks/tasks/recover-index" value="'+e(uploadName)+'"></label>',
    '<ul class="simple-list">'+gates.map(g=>'<li class="inline">'+pill('✓ Sample pass','pass')+' '+g+'</li>').join('')+'</ul>',
    '<p>Calibration is optional. The sample panel estimates medium difficulty at k=3.</p><label class="inline"><input type="checkbox" id="calibration" '+(calibrated?'checked':'')+'> Include sample calibration in the preview</label>',
    '<p>Publish <strong>'+e(uploadName)+'</strong> as a sample task with a pinned version. Review it in the task catalogue after publishing.</p>'
  ];
  return head('New task','Walk through the task authoring flow with simulated validation.',link('Back to tasks','','tasks'),'BENCHMARK AUTHORING')+
    '<div class="tabs" aria-label="Task authoring steps">'+labels.map((label,i)=>'<span class="tab" aria-pressed="'+(i===taskStep)+'" '+(i===taskStep?'aria-current="step"':'')+'>'+(i+1)+'. '+label+'</span>').join('')+'</div>'+
    panel(labels[taskStep],'Step '+(taskStep+1)+' of 4','<div class="panel-body stack">'+contents[taskStep]+'<div class="actions">'+
    (taskStep?btn('Back','','wizard-back'):'')+btn(taskStep===3?'Publish sample task':'Continue','arrow','wizard-next','primary')+'</div></div>');
}
function agents() {
  return head('Agents & models','Inspect the registered agent versions and model endpoints.','','CONFIGURATION')+
    panel('Registered combinations','Illustrative version pins.',simpleTable(['Agent','Agent version','Model','Mode','Status'],[
      ['Claude Code','1.4.2','Claude Opus','A · Harness-managed',pill('Available','pass','check')],
      ['Codex CLI','0.86.0','GPT-5','B · Sandbox-managed',pill('Available','pass','check')]
    ]));
}
function settings() {
  return head('Settings','Review sample project budgets, access, and worker configuration.','','CONFIGURATION')+
    '<div class="split">'+panel('Weekly budget','USD · sample project cap','<div class="panel-body"><dl><dt>Budget</dt><dd class="mono">'+money(weeklyBudget.limit)+'</dd><dt>Spend</dt><dd class="mono">'+money(weeklyBudget.spent)+'</dd><dt>Used</dt><dd><progress value="'+weeklyBudget.spent+'" max="'+weeklyBudget.limit+'" aria-label="Weekly budget used"></progress></dd></dl></div>')+
    panel('Access and worker defaults','Illustrative workspace configuration.','<div class="panel-body"><dl><dt>Role</dt><dd>Project admin</dd><dt>Worker pool</dt><dd>eval-default</dd><dt>Slots</dt><dd>8</dd><dt>Sandbox runtime</dt><dd>gVisor</dd></dl></div>')+'</div>';
}
function ops() {
  return head('Harness health','Track infrastructure integrity separately from agent outcomes.',pill('Current harness healthy','pass','pulse'),'OPERATIONS')+
    '<div class="cards">'+stat('Worker capacity','6 / 8','Busy slots · sample fleet','pulse','infra')+stat('Infra errors','0.8%','Current sample window · target below 2%','retry','pass')+stat('Oracle alerts','01','One sample task needs review','alert','warn')+'</div>'+
    panel('Issues to review','Historical run issues and current oracle alerts.',alertList(attention));
}
function missing(label) {
  return head(label+' unavailable','This item isn’t in the sample workspace.',link('Go to overview','arrow','home'));
}
function render() {
  sidebarView();
  topView();
  const views={home,runs:runList,run:()=>runDetail(runs.find(r=>r.id===record)),trial:()=>'<div class="stack">'+trialDetail(record,true)+'</div>',compare,leaderboard,suites,suite:()=>suiteDetail(record),tasks:taskList,task:()=>taskDetail(record),'new-task':wizard,agents,settings,ops};
  main.innerHTML=(views[page]||home)();
}
function onRoute() {
  const parts=location.hash.slice(1).split('/');
  if(parts[0]==='main') return;
  page=parts.shift()||'home';
  try { record=decodeURIComponent(parts.join('/')); } catch { record=''; }
  if(page==='runs') runFilter=runStatuses.has(record)?record:'all';
  runTab='Overview';
  taskFilter='All outcomes';
  taskQuery='';
  if(modal.open) modal.close();
  setMenu(false);
  render();
  window.scrollTo(0,0);
  if(location.hash) main.focus({preventScroll:true});
}
function openDialog(content, sheet=false) {
  modal.classList.toggle('sheet',sheet);
  modal.setAttribute('aria-labelledby','dialog-title');
  modal.innerHTML='<div class="dialog-content">'+content+'</div>';
  if(!modal.open) modal.showModal();
}
function dialogTitle(title, subtitle='') {
  return '<div class="dialog-head"><div><h2 id="dialog-title">'+title+'</h2>'+(subtitle?'<small>'+subtitle+'</small>':'')+'</div>'+btn('Close','close','close')+'</div>';
}
function queueDialog() {
  openDialog(dialogTitle('New evaluation run','Configure a simulated run in the sample workspace.')+
    '<form id="run-form" class="stack"><label class="form-label">Run name<input class="field" name="name" maxlength="60" placeholder="e.g. Release smoke test" required></label>'+
    '<label class="form-label">Suite<select class="field" name="suite">'+suiteRows.map(s=>'<option value="'+s[0]+'">'+s[0]+' · '+s[2]+' tasks</option>').join('')+'</select></label>'+
    '<label class="form-label">Agent and model<select class="field" name="agent"><option value="claude">Claude Code / Claude Opus</option><option value="codex">Codex CLI / GPT-5</option></select></label>'+
    '<div class="split"><label class="form-label">Attempts per task<select class="field" name="k"><option>1</option><option>3</option><option selected>5</option></select></label><label class="form-label">Budget (USD)<input class="field" type="number" name="budget" min="1" max="5000" value="60" required></label></div>'+
    '<small id="run-estimate">250 trials will be queued in the demo.</small><div class="actions"><button class="btn primary" type="submit">'+icon('plus',15)+' Queue sample run</button></div></form>');
  modal.querySelector('[name=name]').focus();
}
const searchItems=()=>[
  ...nav.flatMap(([,items])=>items.map(([id,label])=>[label,id,'Page'])),
  ...projectRuns().map(r=>[r.name,'run/'+r.id,'RUN-'+r.id]),
  ...[...tasks,...publishedTasks].map(t=>[t[0],'task/'+encodeURIComponent(t[0]),'Task'])
];
function searchResults(query) {
  const q=query.trim().toLowerCase();
  const results=searchItems().filter(([label,href,type])=>(label+' '+href+' '+type).toLowerCase().includes(q)).slice(0,9);
  return results.length?results.map(([label,href,type])=>'<a class="event" href="#'+href+'"><span class="grow"><strong>'+e(label)+'</strong><small>'+e(type)+'</small></span>'+icon('chev',13)+'</a>').join(''):'<div class="empty">No results found.</div>';
}
function searchDialog() {
  openDialog(dialogTitle('Search workspace','Jump to a page, run, or task.')+'<input class="field" type="search" id="global-search" aria-label="Search workspace" placeholder="Search by name or ID"><div id="search-results">'+searchResults('')+'</div>');
  modal.querySelector('input').focus();
}
let toastTimer;
function toast(message) {
  const el=document.querySelector('#toast');
  el.textContent=message;
  clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>{el.textContent='';},3000);
}
function wizardNext() {
  if(taskStep===0) {
    const file=document.querySelector('#archive').files[0];
    uploadName=file?.name||document.querySelector('#git-path').value.trim();
    if(!uploadName) return toast('Choose an archive or enter a Git path.');
  }
  if(taskStep===2) calibrated=document.querySelector('#calibration').checked;
  if(taskStep<3) { taskStep++; render(); return; }
  const basename=uploadName.split('/').filter(Boolean).pop()||'sample-task';
  const slug=basename.toLowerCase().replace(/\.(tar\.gz|tgz|tar|zip)$/,'').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,60)||'sample-task';
  const ids=[...tasks,...publishedTasks].map(t=>t[0]);
  let unique=slug;
  for(let n=2;ids.includes(unique);n++) unique=slug+'-'+n;
  publishedTasks.push([unique,calibrated?'Calibrated sample':'Imported sample']);
  taskStep=0;
  calibrated=true;
  uploadName='';
  route('task/'+unique);
  toast('Sample task published.');
}
function setMenu(open) {
  sidebar.classList.toggle('open',open);
  const toggle=topbar.querySelector('[data-action=menu]');
  toggle?.setAttribute('aria-expanded',String(open));
}
const actions = {
  menu:()=>setMenu(!sidebar.classList.contains('open')),
  close:()=>modal.close(),
  theme:()=>{
    const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';
    document.documentElement.dataset.theme=theme;
    try { localStorage.setItem('autobench-theme',theme); } catch {}
    topView();
    topbar.querySelector('[data-action=theme]').focus();
  },
  new:queueDialog,
  search:searchDialog,
  project:()=>openDialog(dialogTitle('Switch project','Choose a sample workspace.')+
    '<div class="stack">'+btn('Agent evaluation','layers','project-agent')+btn('Sandbox lab','flask','project-sandbox')+'</div>'),
  'project-agent':()=>{project='Agent evaluation'; modal.close(); route('home');},
  'project-sandbox':()=>{project='Sandbox lab'; modal.close(); route('home');},
  trial:(el)=>openDialog(trialDetail(el.dataset.key,false),true),
  'run-tab':(el)=>{runTab=el.dataset.key; render(); main.querySelector('.tab[aria-pressed=true]').focus();},
  'cancel-run':()=>openDialog(dialogTitle('Cancel this sample run?','The demo will mark this run as cancelled.')+btn('Cancel sample run','close','confirm-cancel')),
  'confirm-cancel':()=>{runs.find(r=>r.id===record).status='Cancelled'; modal.close(); render(); toast('Sample run cancelled.');},
  'wizard-next':wizardNext,
  'wizard-back':()=>{taskStep--; render();},
  copy:async(el)=>{
    const value=el.parentElement.querySelector('code').textContent;
    try { await navigator.clipboard.writeText(value); toast('Replay command copied.'); }
    catch { const sel=window.getSelection(); const range=document.createRange(); range.selectNodeContents(el.parentElement.querySelector('code')); sel.removeAllRanges(); sel.addRange(range); toast('Command selected for copying.'); }
  },
  export:()=>{
    const csv='agent,model,pass_at_1,pass_at_1_ci_low,pass_at_1_ci_high,pass_all_5,pass_all_5_ci_low,pass_all_5_ci_high\nClaude Code,Claude Opus,51.8,45.2,58.4,29.2,22.6,35.8\nCodex CLI,GPT-5,49.1,42.5,55.7,27.5,21.0,34.1\n';
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
    const a=document.createElement('a'); a.href=url; a.download='autobench-sample-leaderboard.csv'; a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    toast('Sample leaderboard exported.');
  }
};
document.addEventListener('click',ev=>{
  const action=ev.target.closest('[data-action]');
  if(action&&actions[action.dataset.action]) actions[action.dataset.action](action);
  if(ev.target===modal) modal.close();
  if(ev.target.closest('a[href^="#"]')&&modal.open) modal.close();
  if(sidebar.classList.contains('open')&&!ev.target.closest('#sidebar,[data-action=menu]')) setMenu(false);
});
document.addEventListener('input',ev=>{
  const el=ev.target;
  if(el.id==='global-search') document.querySelector('#search-results').innerHTML=searchResults(el.value);
  if(el.id==='task-query'){taskQuery=el.value; document.querySelector('#matrix').innerHTML=matrixRows(runs.find(r=>r.id===record));}
});
document.addEventListener('change',ev=>{
  const el=ev.target;
  if(el.id==='range'){
    range=el.value;
    const trend=document.querySelector('#trend');
    const dataWasOpen=trend.querySelector('details')?.open;
    trend.innerHTML=trendChart(trendPeriods[range]);
    if(dataWasOpen) trend.querySelector('details').open=true;
  }
  if(el.id==='run-filter'){
    runFilter=el.value;
    history.replaceState(null,'',runFilter==='all'?'#runs':'#runs/'+runFilter);
    el.closest('.card').querySelector('.table-scroll, .empty').outerHTML=runTable(runsForProject());
  }
  if(el.id==='task-filter'){taskFilter=el.value; document.querySelector('#matrix').innerHTML=matrixRows(runs.find(r=>r.id===record));}
  if(el.id==='compare-a'||el.id==='compare-b'){
    if(el.id==='compare-a') compareA=el.value; else compareB=el.value;
    render(); document.getElementById(el.id).focus();
  }
  if(el.form?.id==='run-form'){
    const data=new FormData(el.form);
    const count=suiteRows.find(s=>s[0]===data.get('suite'))[2]*Number(data.get('k'));
    document.querySelector('#run-estimate').textContent=count+' trials will be queued in the demo.';
  }
});
document.addEventListener('submit',ev=>{
  if(ev.target.id!=='run-form') return;
  ev.preventDefault();
  const data=new FormData(ev.target);
  const name=String(data.get('name')).trim();
  if(!name) return toast('Enter a run name.');
  const suite=data.get('suite'), k=Number(data.get('k')), id=String(Math.max(...runs.map(r=>Number(r.id)))+1).padStart(4,'0');
  const claude=data.get('agent')==='claude';
  runs.unshift({id,name,suite,k,agent:claude?'Claude Code':'Codex CLI',model:claude?'Claude Opus':'GPT-5',status:'Queued',done:0,total:suiteRows.find(s=>s[0]===suite)[2]*k,cost:0,budget:Number(data.get('budget')),age:'Just now',project});
  route('run/'+id);
  toast('Sample run queued.');
});
document.addEventListener('keydown',ev=>{
  if((ev.metaKey||ev.ctrlKey)&&ev.key.toLowerCase()==='k'){ev.preventDefault();searchDialog();return;}
  if(ev.key==='Escape'&&sidebar.classList.contains('open')) setMenu(false);
  const cell=ev.target.closest('.trial');
  if(!cell) return;
  if(ev.key==='Enter'){ev.preventDefault();route('trial/'+cell.dataset.key);}
  const offset={ArrowRight:1,ArrowLeft:-1,ArrowUp:-runs.find(r=>r.id===record).k,ArrowDown:runs.find(r=>r.id===record).k,j:1,k:-1}[ev.key];
  if(offset){const cells=[...main.querySelectorAll('.trial')]; const next=cells[cells.indexOf(cell)+offset]; if(next){ev.preventDefault(); next.focus();}}
});
window.addEventListener('hashchange',onRoute);
onRoute();
