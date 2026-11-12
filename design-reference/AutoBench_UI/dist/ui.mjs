const paths = {
  logo:'<path d="M2 15h4l3-9 5 13 3-8h5"/>', home:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  runs:'<path d="m8 5 12 7-12 7z"/><path d="M4 5v14"/>', compare:'<path d="M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4"/>', award:'<path d="M8 21h8m-4-4v4M7 4h10v7a5 5 0 0 1-10 0zM7 6H3v2a4 4 0 0 0 4 4m10-6h4v2a4 4 0 0 1-4 4"/>',
  layers:'<path d="m12 3 9 5-9 5-9-5zm-9 9 9 5 9-5m-18 4 9 5 9-5"/>', tasks:'<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>', bot:'<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 7V3m-4 9h.01m8 0h.01M9 16h6M2 12v3m20-3v3"/>',
  settings:'<circle cx="12" cy="12" r="3"/><path d="m10 3-1 3-3 1-3 3v4l3 1 1 3 3 3h4l1-3 3-1 3-3v-4l-3-1-1-3-3-3z"/>', search:'<circle cx="10.5" cy="10.5" r="5.5"/><path d="m15 15 5 5"/>', plus:'<path d="M12 5v14M5 12h14"/>', arrow:'<path d="M4 12h15m-6-6 6 6-6 6"/>',
  chev:'<path d="m9 6 6 6-6 6"/>', down:'<path d="m6 9 6 6 6-6"/>', pulse:'<path d="M2 12h5l3-7 4 14 3-7h5"/>', alert:'<path d="M12 8v4m0 4h.01"/><path d="M10.4 4.8 3.2 18a1 1 0 0 0 .9 1.5h15.8a1 1 0 0 0 .9-1.5L13.6 4.8a1.8 1.8 0 0 0-3.2 0z"/>',
  coins:'<circle cx="12" cy="12" r="9"/><path d="M12 6v12m3-9h-4a1.5 1.5 0 0 0 0 3h2a1.5 1.5 0 0 1 0 3H9"/>', clock:'<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>', check:'<path d="m5 12 4 4L19 6"/>', retry:'<path d="M19 8a8 8 0 1 0 0 8m0-12v5h-5"/>', 
  moon:'<path d="M21 13a9 9 0 1 1-10-10 8 8 0 0 0 10 10z"/>', sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>', menu:'<path d="M4 6h16M4 12h16M4 18h16"/>', close:'<path d="m6 6 12 12M6 18 18 6"/>', flask:'<path d="M9 3h6m-5 0v7l-5 8a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-8V3M8 15h8"/>', download:'<path d="M12 3v12m-4-4 4 4 4-4M5 16v4h14v-4"/>'
};
const icon = (name, size=16) => '<svg aria-hidden="true" width="'+size+'" height="'+size+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'+(paths[name]||'')+'</svg>';
const e = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn = (label, symbol, action, extra='') => '<button class="btn '+extra+'" data-action="'+action+'">'+(symbol?icon(symbol,15):'')+label+'</button>';
const link = (label, symbol, href, extra='') => '<a class="btn '+extra+'" href="#'+href+'">'+label+(symbol?icon(symbol,15):'')+'</a>';
const pill = (label, style, symbol='') => '<span class="pill '+style+'">'+(symbol?icon(symbol):'')+label+'</span>';
const money = value => '$'+value.toFixed(2);
function status(run) {
  const styles = {'Running':['live','pulse'],'Complete':['pass','check'],'Gate failed':['fail','alert'],'Invalid · harness':['infra','retry'],'Queued':['neutral','clock'],'Cancelled':['neutral','close']};
  return pill(run.status,...styles[run.status]);
}
function head(title, sub, actions='', eyebrow='') {
  return '<section class="page-head"><div>'+(eyebrow?'<div class="eyebrow">'+eyebrow+'</div>':'')+'<h1>'+title+'</h1>'+(sub?'<p>'+sub+'</p>':'')+'</div><div class="actions">'+actions+'</div></section>';
}
function panel(title, desc, content, action='', footer='') {
  return '<section class="card"><div class="panel-head"><div><h2>'+title+'</h2>'+(desc?'<p>'+desc+'</p>':'')+'</div>'+action+'</div>'+content+(footer?'<div class="panel-footer">'+footer+'</div>':'')+'</section>';
}
function stat(label, value, foot, symbol, style, href='', cta='') {
  return '<article class="card stat"><div class="stat-head"><span class="stamp '+style+'">'+icon(symbol,16)+'</span>'+label+'</div><div class="stat-value">'+value+'</div><small>'+foot+'</small>'+(href?'<a href="#'+href+'">'+cta+icon('arrow',12)+'</a>':'')+'</article>';
}
function simpleTable(columns, rows, empty='Nothing matches these filters.') {
  return rows.length ? '<div class="table-scroll"><table><thead><tr>'+columns.map(c=>'<th scope="col">'+c+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>' : '<p class="empty">'+empty+'</p>';
}
function runTable(items) {
  return simpleTable(['Run','Suite','Agent / model','Status','Trials','Spend','Started'],items.map(r=>[
    '<a href="#run/'+r.id+'">'+e(r.name)+'</a><span class="cell-sub mono">RUN-'+r.id+'</span>',
    '<a class="mono" href="#suite/'+encodeURIComponent(r.suite)+'">'+e(r.suite)+'</a>',e(r.agent)+'<span class="cell-sub">'+e(r.model)+'</span>',status(r),
    '<span class="mono">'+r.done+' / '+r.total+'</span>'+(['Running','Queued'].includes(r.status)?'<progress class="run-progress" value="'+r.done+'" max="'+r.total+'" aria-label="Completed trials"></progress>':''),'<span class="mono">'+money(r.cost)+'</span>',e(r.age)
  ]));
}

export {icon, e, btn, link, pill, money, status, head, panel, stat, simpleTable, runTable};
