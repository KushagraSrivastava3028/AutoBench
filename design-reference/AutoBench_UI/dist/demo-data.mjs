const tasks = [
 ['fix-flaky-retry','Tool use','PPPFP'],['parse-malformed-csv','Data processing','PPPPP'],
 ['restore-corrupt-index','Recovery','FFHFF'],['build-cython-ext','Build & tooling','PFRQQ'],
 ['deduplicate-event-log','Data processing','PPFPQ'],['repair-broken-lockfile','Build & tooling','FPPPQ'],
 ['sanitize-shell-output','Tool use','PPPPP'],['recover-git-history','Recovery','PFPPQ']
];
const states = {P:['✓','Passed','pass'],F:['×','Failed','fail'],H:['◐','Partial credit','warn'],R:['⟳','Infrastructure retry','infra'],Q:['·','Queued','neutral']};
const runs = [
 {id:'0248', name:'Core nightly', suite:'core-50', agent:'Claude Code', model:'Claude Opus', status:'Running', done:143, total:250, k:5, cost:18.22, budget:60, age:'12 min ago'},
 {id:'0247', name:'Release candidate', suite:'core-50', agent:'Claude Code', model:'Claude Opus', status:'Complete', done:250, total:250, k:5, cost:34.58, budget:60, age:'2 hr ago', metrics:['51.8','75.6','29.2'], cis:['45.2–58.4','68.4–82.8','22.6–35.8']},
 {id:'0246', name:'PR #184 smoke gate', suite:'smoke-20', agent:'Codex CLI', model:'GPT-5', status:'Gate failed', done:20, total:20, k:1, cost:2.36, budget:12, age:'4 hr ago', metrics:['40.0'],cis:['21.9–61.3']},
 {id:'0245', name:'Reference baseline', suite:'core-50', agent:'Claude Code', model:'Claude Opus', status:'Complete', done:250, total:250, k:5, cost:31.50, budget:60, age:'Yesterday', metrics:['47.2','71.4','22.0'], cis:['41.0–53.5','64.9–77.6','16.1–28.4']},
 {id:'0244', name:'Provider smoke', suite:'smoke-20', agent:'Codex CLI', model:'GPT-5', status:'Invalid · harness', done:20, total:20, k:1, cost:3.12, budget:12, age:'Yesterday'},
 {id:'0243', name:'Recovery panel', suite:'recovery-12', agent:'Codex CLI', model:'GPT-5', status:'Running', done:27, total:60, k:5, cost:4.17, budget:15, age:'Yesterday'}
];
const suiteRows = [['core-50','Core reference suite',50,'v2.4'],['smoke-20','PR regression checks',20,'v1.8'],['recovery-12','Recovery and repair',12,'v1.2']];
const attention = [
 ['alert','fail','PR #184 gate failed','smoke-20 · 3 newly failing tasks','run/0246'],
 ['retry','infra','Run invalidated by harness','Provider smoke · 5.0% infra errors','run/0244'],
 ['alert','warn','Oracle drift detected','restore-corrupt-index · nightly check','task/restore-corrupt-index']
];
const weeklyBudget = {spent:42.68, limit:120};
// Each row contains the date and [estimate, lower bound, upper bound] for both metrics.
const latestScores = ['Nov 10',[75.6,68.4,82.8],[29.2,22.6,35.8]];
const trendPeriods = {
  '7':[
    ['Nov 4',[70,63,77],[21,14,28]], ['Nov 5',[68,61,75],[24,17,31]],
    ['Nov 6',[73,66,80],[22,15,29]], ['Nov 7',[71,64,78],[27,20,34]],
    ['Nov 8',[77,70,84],[29,22,36]], ['Nov 9',[72,65,79],[24,17,31]],
    latestScores
  ],
  '30':[
    ['Oct 12',[62,55,69],[19,12,26]], ['Oct 17',[66,59,73],[21,14,28]],
    ['Oct 22',[65,58,72],[22,15,29]], ['Oct 27',[70,63,77],[24,17,31]],
    ['Nov 1',[69,62,76],[22,15,29]], ['Nov 6',[73,66,80],[22,15,29]],
    latestScores
  ]
};
export {tasks, states, runs, suiteRows, attention, weeklyBudget, trendPeriods};
