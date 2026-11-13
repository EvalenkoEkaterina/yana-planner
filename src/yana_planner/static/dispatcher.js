import {validatePackage, rateFields, normalizeLoading} from './route-package.js';
import {reconcileOptions, costInputIssue} from './planning-form-state.mjs';
const form = document.getElementById('conditions-form');
const $ = id => document.getElementById(id);
const values = name => [...form.querySelectorAll(`[name="${name}"]`)];
const first = name => values(name)[0];
const base = $('route-workspace').dataset.staticBase;
const caps = JSON.parse(form.dataset.capabilities || '{}');
let current, routeKey='r03', loading=true, routeRequest=0, snapshot;
const status = message => { $('package-status').textContent = message; };
const getNumber = input => input.value.trim()==='' ? null : Number(input.value.replace(',','.'));
const set = (name, value) => { const e=first(name); if(e) e.value=value ?? ''; };
function node(tag, text, className) { const n=document.createElement(tag); if(text!=null) n.textContent=text; if(className) n.className=className; return n; }
function requiredCapabilities() {
  const needed=[];
  if(routeKey!=='r03') needed.push('route_packages');
  if($('economics-enabled').value==='yes'||$('strategy').value==='cost') needed.push('economics');
  if($('weather-enabled').checked) needed.push('weather');
  if(values('vessel_load_rate_tph').some(e=>e.value.trim())) needed.push('loading_rate');
  return needed.filter(k=>caps[k]!==true);
}
function update() {
  const economic=$('economics-enabled').value==='yes';
  $('billing-settings').hidden=!economic;
  form.querySelectorAll('[data-rate]').forEach(n=>{ n.hidden=!economic; n.querySelectorAll('input, select').forEach(e=>e.disabled=!economic); });
  $('billing-settings').querySelectorAll('input').forEach(e=>e.disabled=!economic);
  const weather=$('weather-enabled').checked;
  $('weather-fields').hidden=!weather;
  $('weather-fields').querySelectorAll('input, select').forEach(e=>e.disabled=!weather);
  const forecast=$('weather-data-kind').value==='forecast';
  $('forecast-issued-at').disabled=!weather||!forecast;
  $('weather-kind-help').textContent=forecast ? 'Дата выпуска прогноза должна быть не позже момента объявления. T0 и дата выпуска требуют часового пояса. Точность и применимость порога ещё требуют проверки.' : $('weather-data-kind').value==='reanalysis' ? 'Реанализ — историческое испытание с условной доступностью будущих условий. Он не является прогнозом; точность прогноза здесь не измеряется.' : 'Синтетический опыт проверяет механизм. Реальные метеоданные и пороги для судов не подтверждены.';
  form.querySelectorAll('.vessel-row').forEach(row=>{
    const speed=row.querySelector('[name="vessel_load_rate_tph"]');
    row.querySelector('[name="vessel_load_h"]').required=!speed.value.trim();
  });
  const missing=requiredCapabilities();
  $('integration-status').textContent=missing.length ? 'Эти настройки пока доступны в пакете условий. Расчёт с ними на сайте подключает интегратор; скачайте JSON. Текущий сервер не будет запущен с неподдерживаемыми параметрами.' : 'Расчёт доступен на текущем сервере. Сначала проверьте объём, затем затраты, рейсы и сроки. Решение утверждает диспетчер.';
  const button=form.querySelector('[type="submit"]');
  button.disabled=loading || !current || missing.length>0;
  button.textContent=missing.length ? 'Ожидает подключения расчёта' : 'Рассчитать план ↗';
}
function gather({requireBilling=true}={}) {
  if(!current) throw new Error('Дождитесь загрузки пакета направления');
  const p=structuredClone(current), c=p.scenario;
  for(const key of ['target_t','initial_source_t','destination_capacity_t','return_cutoff_h']) c[key]=getNumber(first(key));
  p.deadline_h=getNumber(first('deadline')); p.strategy=$('strategy').value;
  if($('scenario-t0').value.trim()) c.t0=$('scenario-t0').value.trim(); else delete c.t0;
  c.vessels=[...form.querySelectorAll('.vessel-row')].map(row=>{
    const fields=row.querySelectorAll('input[name]'); const v={};
    for(const field of fields) {
      const key=field.name.replace(/^vessel_/,'');
      if(key==='id') v.id=field.value.trim();
      else if(!rateFields.includes(key)&&!['rate_source'].includes(key)) {
        if(key==='load_rate_tph'&&field.value.trim()==='') continue;
        v[key]=getNumber(field);
      }
    }
    return normalizeLoading(v);
  });
  const arrivals=values('arrival_h').map((e,i)=>({h:getNumber(e),t:getNumber(values('arrival_t')[i]), original:i})).sort((a,b)=>a.h-b.h);
  c.arrivals=arrivals.map(({h,t})=>({h,t}));
  // Keep the explicit route calendar, closures, withdrawals and provenance from the package.
  // For original R03 custom capacities the service historically uses the fleet maximum.
  if(routeKey==='r03'&&!current.facts.some(f=>f.field==='custom_payload_calendar')) c.payload_calendar=[{from_h:0,capacity_t:Math.max(...c.vessels.map(v=>v.capacity_t))}];
  p.cost_terms=null;
  if($('economics-enabled').value==='yes') {
    if(requireBilling&&!$('billing-confirmed').checked) throw new Error('Подтвердите схему оплаты всего доступного флота');
    p.cost_terms={billing_mode:'all_available_time',currency:'RUB',horizon_h:getNumber($('horizon-h')),vessel_rates:Object.fromEntries([...form.querySelectorAll('.vessel-row')].map((row,i)=>[c.vessels[i].id,{
      ...Object.fromEntries(rateFields.map(f=>[f,getNumber(row.querySelector(`[name="vessel_${f}"]`))])),
      rate_basis:row.querySelector('[name="vessel_rate_basis"]').value,
      source:row.querySelector('[name="vessel_rate_source"]').value.trim() || null
    }]))};
  }
  p.event=null;
  if($('weather-enabled').checked) {
    if(first('scenario').value==='delay') throw new Error('Выберите одно событие: задержку поступления или погоду');
    p.event={type:'weather_closure',announced_at_h:getNumber($('weather-announced')),closures:[...$('weather-intervals').querySelectorAll('.weather-row')].map(r=>[getNumber(r.querySelector('.closure-from')),getNumber(r.querySelector('.closure-to'))]),rule_status:'scenario_assumption',data_kind:$('weather-data-kind').value,source:$('weather-source').value.trim(),forecast_issued_at:$('weather-data-kind').value==='forecast' ? $('forecast-issued-at').value.trim() : null};
  } else if(first('scenario').value==='delay') p.event={type:'arrival_delay',announced_at_h:getNumber(first('announced_at_h')),arrival_index:arrivals.findIndex(a=>a.original===getNumber(first('delayed_arrival'))-1),new_arrival_h:getNumber(first('new_arrival_h')),status:'scenario_assumption'};
  p.facts=p.facts.filter(f=>!f.field.startsWith('scenario.'));
  p.facts.push({field:'scenario',value:'Параметры формы; не подтверждены оператором',status:'scenario_assumption'});
  c.real_admissibility='requires_confirmation';
  return validatePackage(p);
}
function renderFacts(p) {
  const host=$('route-facts'); host.replaceChildren();
  host.append(node('p',`${p.route.origin} → ${p.route.destination}. ${p.route.graph_path ? p.route.length_km+' км по графу · '+(p.route.graph_path.distance_status==='scenario_assumption'?'условные расстояния.':'по источникам участков.') : p.route.length_km ? p.route.length_km+' км — сумма опубликованных участков.' : 'Контрольный пример R03.'}`));
  const list=node('ul');
  for(const f of p.facts) list.append(node('li',`${f.field}: ${f.value===null ? 'неизвестно' : typeof f.value==='object' ? JSON.stringify(f.value) : f.value} · ${{documented:'из источника',scenario_assumption:'сценарное допущение',unknown:'не подтверждено'}[f.status]}`));
  host.append(list);
  for(const s of p.sources) { const a=node('a',s.title); a.href=s.url; a.target='_blank'; a.rel='noopener'; const paragraph=node('p'); paragraph.append(a); host.append(paragraph); }
}
function checkRepresentable(p) {
  if(p.deadline_h>492||p.scenario.return_cutoff_h>492) throw new Error('Форма поддерживает горизонт перевозки до 492 ч');
  if(p.scenario.vessels.some(v=>['available_h','load_h','up_h','unload_h','return_h','prep_h','load_setup_h'].some(f=>v[f]!=null&&v[f]>492))||p.scenario.arrivals.some(a=>a.h>492)) throw new Error('Времена формы должны быть в пределах 0–492 ч');
  if(p.scenario.vessels.length>6||p.scenario.arrivals.length>12) throw new Error('Форма поддерживает до 6 судов и 12 поступлений');
  if(p.event?.type==='weather_closure'&&p.event.closures.length>24) throw new Error('В форме до 24 погодных интервалов');
}
function fill(p, key, restore=false) {
  if(!restore) checkRepresentable(p);
  const c=p.scenario;
  // Validation and representability checks completed before touching the current form.
  for(const k of ['target_t','initial_source_t','destination_capacity_t','return_cutoff_h']) set(k,c[k]);
  set('deadline',p.deadline_h); set('scenario',p.event?.type==='arrival_delay'?'delay':'r03');
  const host=$('vessels'), template=host.querySelector('.vessel-row').cloneNode(true); host.replaceChildren();
  for(const [i,v] of c.vessels.entries()) {
    const row=template.cloneNode(true);
    row.querySelector(':scope > legend').textContent=`Судно ${i+1}`;
    for(const input of row.querySelectorAll('input[name]')) {
      const key=input.name.replace(/^vessel_/,'');
      input.value=Object.hasOwn(v,key) ? v[key] : key==='load_setup_h' ? 0 : key==='load_h' ? 1 : '';
    }
    const r=p.cost_terms?.vessel_rates[v.id];
    for(const f of rateFields) row.querySelector(`[name="vessel_${f}"]`).value=r?.[f]??'';
    row.querySelector('[name="vessel_rate_basis"]').value=r?.rate_basis??'unspecified';
    row.querySelector('[name="vessel_rate_source"]').value=r?.source??'';
    host.append(row);
  }
  const ahost=$('arrivals'), atemplate=ahost.querySelector('.arrival-row').cloneNode(true); ahost.replaceChildren();
  for(const a of c.arrivals) { const row=atemplate.cloneNode(true); row.querySelector('[name="arrival_h"]').value=a.h; row.querySelector('[name="arrival_t"]').value=a.t; ahost.append(row); }
  $('strategy').value=p.strategy;
  $('economics-enabled').value=p.cost_terms?'yes':'no';
  $('horizon-h').value=p.cost_terms?.horizon_h??492; $('billing-confirmed').checked=restore&&$('billing-confirmed').dataset.restored==='true';
  $('scenario-t0').value=c.t0??'';
  $('weather-enabled').checked=p.event?.type==='weather_closure';
  $('constraints-settings').open=Boolean(p.event);
  $('cost-settings').open=Boolean(p.cost_terms);
  if(p.event?.type==='weather_closure') {
    const e=p.event; $('weather-data-kind').value=e.data_kind; $('weather-announced').value=e.announced_at_h; $('weather-source').value=e.source; $('forecast-issued-at').value=e.forecast_issued_at??'';
    const wtemplate=$('weather-intervals').firstElementChild?.cloneNode(true)??weatherTemplate.cloneNode(true); $('weather-intervals').replaceChildren();
    for(const [a,b] of e.closures) { const row=wtemplate.cloneNode(true); row.querySelector('.closure-from').value=a; row.querySelector('.closure-to').value=b; $('weather-intervals').append(row); }
  } else if(p.event?.type==='arrival_delay') { set('announced_at_h',p.event.announced_at_h); set('delayed_arrival',p.event.arrival_index+1); set('new_arrival_h',p.event.new_arrival_h); }
  $('route-origin').textContent=p.route.origin; $('route-destination').textContent=p.route.destination;
  $('route-caption').textContent='Отдельный учебный флот · Реальная выполнимость требует подтверждения';
  routeKey=key; current=structuredClone(p);
  document.querySelectorAll('[data-route]').forEach(b=>{const yes=b.dataset.route===key;b.classList.toggle('selected',yes);b.setAttribute('aria-pressed',String(yes));});
  $('saved-route-report').hidden=true;
  renderFacts(p); form.dispatchEvent(new Event('input',{bubbles:true})); update();
}
async function choose(key) {
  const request=++routeRequest; loading=true; update();
  try { const response=await fetch(`${base}${key}.package.json`); if(!response.ok) throw new Error('Не удалось загрузить набор данных'); const p=validatePackage(await response.json()); if(request!==routeRequest)return; fill(p,key); $('route-import-status').textContent=`Загружено: ${p.route.origin} → ${p.route.destination}. Тарифы сценарные; подтвердите схему оплаты перед экспортом.`; status(''); }
  catch(e) { if(request===routeRequest) $('route-import-status').textContent=e.message; }
  finally { if(request===routeRequest){loading=false;update();} }
}
const weatherTemplate=$('weather-intervals').firstElementChild.cloneNode(true);
$('add-weather').addEventListener('click',()=>{if($('weather-intervals').children.length>=24)return;const row=weatherTemplate.cloneNode(true);row.querySelectorAll('input').forEach(e=>e.value='');$('weather-intervals').append(row);});
$('weather-intervals').addEventListener('click',e=>{if(e.target.matches('.remove-weather')) e.target.closest('.weather-row').remove();});
document.querySelectorAll('[data-route]').forEach(b=>b.addEventListener('click',()=>choose(b.dataset.route)));
$('import-package').addEventListener('change',async e=>{
  const file=e.target.files[0]; if(!file)return;
  try {if(file.size>1024*1024)throw new Error('Максимум 1 МБ');const p=validatePackage(JSON.parse(await file.text()));checkRepresentable(p);routeRequest++;fill(p,'imported');$('route-import-status').textContent=`Импорт проверен: ${p.route.origin} → ${p.route.destination}. Серверный расчёт пакета ожидает подключения.`;status('Импорт выполнен без отправки файла на сервер.');}
  catch(error){$('route-import-status').textContent=`Импорт отклонён: ${error.message}. Текущие условия сохранены.`;}
  finally{e.target.value='';loading=false;update();}
});
function synchronizeOptions(changed) {
  const state=reconcileOptions({strategy:$('strategy').value,economics:$('economics-enabled').value==='yes',weather:$('weather-enabled').checked,delayed:first('scenario').value==='delay'},changed);
  $('strategy').value=state.strategy; $('economics-enabled').value=state.economics?'yes':'no';
  $('weather-enabled').checked=state.weather; first('scenario').value=state.delayed?'delay':'r03';
  // conditions.js also owns summaries and the enabled state of delay fields.
  form.dispatchEvent(new Event('input',{bubbles:true})); update();
}
$('strategy').addEventListener('change',()=>synchronizeOptions('strategy'));
$('economics-enabled').addEventListener('change',()=>synchronizeOptions('economics'));
$('weather-enabled').addEventListener('change',()=>synchronizeOptions('weather'));
first('scenario').addEventListener('change',()=>synchronizeOptions('delayed'));
$('load-example-rates').addEventListener('click',()=>{
  const terms=current?.cost_terms; let count=0;
  for(const row of form.querySelectorAll('.vessel-row')) {
    const id=row.querySelector('[name="vessel_id"]').value.trim(), rates=terms?.vessel_rates?.[id];
    if(!rates)continue;
    for(const field of rateFields)row.querySelector(`[name="vessel_${field}"]`).value=rates[field]??'';
    row.querySelector('[name="vessel_rate_basis"]').value=rates.rate_basis;
    row.querySelector('[name="vessel_rate_source"]').value=rates.source??''; count++;
  }
  if(count){$('economics-enabled').value='yes';$('horizon-h').value=terms.horizon_h;$('resources-settings').open=true;}
  status(count?`Загружены ставки для ${count} судов. Проверьте их и отдельно подтвердите схему оплаты. Для остальных судов ставки не перенесены.`:'В выбранном примере нет ставок для этих ID. Заполните их вручную; ставки другого судна не переносятся.');
  form.dispatchEvent(new Event('input',{bubbles:true}));update();
});
form.addEventListener('input',update); form.addEventListener('change',update);
form.addEventListener('click',e=>{if(e.target.matches('.remove-vessel, .remove-arrival'))update();});
$('add-vessel').addEventListener('click',update); $('add-arrival').addEventListener('click',update);
function download(p){ const blob=new Blob([JSON.stringify(p,null,2)+'\n'],{type:'application/json'});const url=URL.createObjectURL(blob);const a=node('a');a.href=url;a.download='yana-route.package.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
$('export-package').addEventListener('click',()=>{try{const p=gather();download(p);status('Пакет проверен и скачан. Это входные условия, не результат расчёта.');}catch(e){status(e.message);}});
form.addEventListener('submit',e=>{
  try {
    $('form-validation-error').hidden=true;
    const issue=costInputIssue({strategy:$('strategy').value,economics:$('economics-enabled').value==='yes',confirmed:$('billing-confirmed').checked,vessels:[...form.querySelectorAll('.vessel-row')].map(row=>({id:row.querySelector('[name="vessel_id"]').value,...Object.fromEntries(rateFields.map(f=>[f,row.querySelector(`[name="vessel_${f}"]`).value])),rate_basis:row.querySelector('[name="vessel_rate_basis"]').value,source:row.querySelector('[name="vessel_rate_source"]').value}))});
    if(issue){const error=new Error(issue.message);error.selector=issue.selector;throw error;}
    if(requiredCapabilities().length) throw new Error('Сервер ещё не поддерживает эти настройки. Скачайте пакет условий для интеграции.');
    // Preserve historical R03 behaviour when only the old transport fields are used.
    if(Object.keys(caps).length) $('route-package-json').value=JSON.stringify(gather());
  }catch(error){e.preventDefault();e.stopImmediatePropagation();status(error.message);update();
    const box=$('form-validation-error');box.textContent=error.message;box.hidden=false;
    const field=error.selector?form.querySelector(error.selector):null;
    if(field){let parent=field.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}field.focus();field.scrollIntoView({block:'center',behavior:'smooth'});}
    else{box.focus();box.scrollIntoView({block:'center',behavior:'smooth'});}
    if($('economics-enabled').value==='yes'&&!$('billing-confirmed').checked) {
      $('cost-settings').open=true; $('billing-confirmed').focus();
    }
  }
},true);
window.addEventListener('pageshow',update);
$('show-route-report').addEventListener('click',async()=>{
  const host=$('saved-route-report');
  try{
    if(!snapshot){const r=await fetch(`${base}batagay.snapshot.json`);if(!r.ok)throw new Error('Сохранённый расчёт недоступен');snapshot=await r.json();}
    host.replaceChildren(node('h3','Сохранённый учебный расчёт · Усть-Куйга → Батагай'),node('p','Это расчёт зафиксированного набора 600 т, срок 328 ч, горизонт оплаты 492 ч. Изменения формы на него не влияют. Стратегия — минимум рейсов; стоимость оценена после расчёта.','field-help'));
    const wrap=node('div',null,'table-scroll'), table=node('table'), head=node('tr'); for(const t of ['Метод','Принято, т','Рейсы','Последняя приёмка, ч','Стоимость, ₽'])head.append(node('th',t));table.append(head);
    for(const [key,label] of [['baseline','READY'],['planner','MILP']]) {const v=snapshot.result[key],r=node('tr');for(const value of [label,v.accepted_t,v.trip_count,v.last_receipt_h,snapshot.costs[key]?.total_rub == null ? '—' : new Intl.NumberFormat('ru-RU').format(snapshot.costs[key].total_rub)])r.append(node('td',String(value)));table.append(r);}
    wrap.append(table);host.append(wrap);
    const solver=snapshot.result.solver;host.append(node('p',`Проверка: ${snapshot.result.planner.status==='valid_simulation'?'допустимый учебный план':'допустимость не подтверждена'}. Оптимальность всех этапов: ${solver.length&&solver.every(s=>s.optimal)?'подтверждена':'не подтверждена'}. Фактическая экономия неизвестна.`,'field-help'));host.hidden=false;
  }catch(e){status(e.message);}
});
// Restore the whole route, including tariffs/event/provenance after a server error.
const restored=JSON.parse($('restored-route-package').textContent);
if(restored) {
  try{fill(restored,restored.route.id==='ust-kuyga-batagay'?'ust-kuyga-batagay':restored.route.id==='nizhneyansk-ust-kuyga'?'r03':'restored',true);$('route-import-status').textContent='Условия выбранного маршрута восстановлены после ответа сервера.';}
  catch(e){status(`Не удалось восстановить пакет: ${e.message}`);}
  finally {loading=false;}
} else {
  const initialRequest=routeRequest;
  try{const r=await fetch(`${base}r03.package.json`);if(!r.ok)throw new Error('Не удалось загрузить метаданные R03');const loaded=validatePackage(await r.json());if(initialRequest===routeRequest){current=loaded;$('scenario-t0').value=current.scenario.t0??'';renderFacts(current);}}catch(e){if(initialRequest===routeRequest)status(e.message);}
  finally{if(initialRequest===routeRequest)loading=false;}
}
update();

// The graph selects geography only: never infer travel times or alter solver objectives.
let graphPath=null, graphRequest=0;
const graphResult=$('graph-result'), graphPanel=$('graph-apply-panel');
function resetGraphResult() {
  graphRequest++; graphPath=null; graphResult.replaceChildren(); graphPanel.hidden=true;
  $('graph-confirm').checked=false; $('apply-river-path').disabled=true;
}
function invalidateGraph() {
  resetGraphResult();
  // Input changes while routing must not accidentally reuse a stale response.
}
for(const id of ['graph-origin','graph-destination']) $(id).addEventListener('change',invalidateGraph);
$('graph-confirm').addEventListener('change',()=>{$('apply-river-path').disabled=!graphPath||!$('graph-confirm').checked;});
$('find-river-path').addEventListener('click',async()=>{
  resetGraphResult(); const token=graphRequest;
  graphResult.textContent='Ищем путь по соединениям сети…';
  try {
    const r=await fetch('/api/river-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({network:$('graph-network').value,origin_id:$('graph-origin').value,destination_id:$('graph-destination').value})});
    const p=await r.json(); if(token!==graphRequest)return;
    if(!r.ok)throw new Error(p.error||'Не удалось найти путь');
    graphResult.replaceChildren();
    if(p.status==='no_path') {graphResult.textContent='В подготовленной сети нет пути между выбранными пунктами.';return;}
    graphPath=p;
    graphResult.append(node('h3',`${new Intl.NumberFormat('ru-RU').format(p.length_km)} км · ${p.network_snapshot.topology_note?'путь по сети Яны':'кратчайший путь по длине'}`));
    const chain=node('div',null,'graph-path-chain');
    p.nodes.forEach((point,i)=>{if(i)chain.append(node('span','→','graph-path-arrow'));chain.append(node('strong',point.name));});
    graphResult.append(chain);
    const list=node('ul');
    for(const edge of p.segments) {
      const item=node('li',`${p.nodes.find(n=>n.id===edge.from).name} → ${p.nodes.find(n=>n.id===edge.to).name}: ${edge.length_km} км. `);
      if(edge.status==='documented'&&edge.source.startsWith('https://')) {const link=node('a',`Источник · строка ${edge.source_row??'—'}`);link.href=edge.source;link.target='_blank';link.rel='noopener';item.append(link);} else item.append(node('span',edge.source));
      list.append(item);
    }
    graphResult.append(list,node('p',p.distance_status==='documented'?'Расстояния из исходного плана 2026; путь — сумма смежных участков. Альтернативных ветвей в этой сети нет. Проход судна на дату не подтверждён.':'Все расстояния условные. Путь не подтверждает возможность прохода конкретного судна.','field-help'));
    graphPanel.hidden=false;
  }catch(e){if(token===graphRequest)graphResult.textContent=e.message;}
});
$('apply-river-path').addEventListener('click',()=>{
  if(!graphPath||!$('graph-confirm').checked)return;
  try {
    const p=gather({requireBilling:false}), path=structuredClone(graphPath);
    p.route={id:`graph-${path.origin_id}-${path.destination_id}`,origin:path.origin,destination:path.destination,length_km:path.length_km,independent_fleet:true,graph_path:path};
    const documented=path.distance_status==='documented';
    p.sources=path.network_snapshot.sources??[{id:'graph-demo',title:'Тестовая сеть Yana Planner: вымышленные пункты и расстояния',url:'https://github.com/EvalenkoEkaterina/yana-planner',accessed_at:'2026-11-13'}];
    const distanceFact={field:'route.length_km',value:path.length_km,status:documented?'documented':'scenario_assumption'};
    if(documented)distanceFact.source_id=path.segments[0].source_id;
    p.facts=[{field:'route.graph_path',value:path.nodes.map(n=>n.name).join(' → '),status:'scenario_assumption'},distanceFact,{field:'operating_parameters',value:'Текущие параметры формы перенесены пользователем как учебные; время не получено из расстояния',status:'scenario_assumption'},{field:'weather_linkage',value:'Автопривязка не выполнена: координаты и покрытие требуют проверки',status:'scenario_assumption'}];
    p.cost_terms=null; p.strategy='trips'; p.event=null;
    p.scenario.closures=[];
    p.scenario.id=path.distance_status==='documented'?'GRAPH-YANA-SIM':'GRAPH-DEMO';p.scenario.label=`Учебный граф: ${path.origin} → ${path.destination}`;
    p.scenario.provenance={kind:'synthetic_scenario',origin:'river graph prototype',operating_parameters:'User explicitly reused form inputs; travel times are manual, not derived from graph distance'};
    const checked=validatePackage(p); routeRequest++; loading=false; fill(checked,'graph');
    $('resources-settings').open=true;
    $('route-caption').textContent='Граф маршрута · перевозка учебная, проходимость не подтверждена';
    $('route-import-status').textContent='Маршрут выбран. Проверьте время движения и возврата каждого судна. Экономика, события и запреты движения сброшены; задайте их заново при наличии данных.';
    status('Расстояние не изменило время рейса. Проверьте все условия перед расчётом.');
  }catch(e){status(e.message);}
});
async function initializeGraph() {
  resetGraphResult();const token=graphRequest;
  $('find-river-path').disabled=true;
  for(const id of ['graph-origin','graph-destination'])$(id).disabled=true;
  try {
    const r=await fetch(`/api/river-network?network=${encodeURIComponent($('graph-network').value)}`);
    if(!r.ok)throw new Error('Сеть временно недоступна');const graph=await r.json();
    if(token!==graphRequest)return;
    for(const id of ['graph-origin','graph-destination']) {
      const select=$(id);select.replaceChildren();
      for(const point of graph.nodes){
        const option=node('option',point.name+(point.routing_role==='waypoint_only'?' · промежуточный узел':''));
        option.value=point.id;option.disabled=point.routing_role==='waypoint_only';select.append(option);
      }
      select.disabled=false;
    }
    const terminals=graph.nodes.filter(n=>n.routing_role!=='waypoint_only');
    $('graph-origin').value=terminals[0]?.id??'';
    $('graph-destination').value=terminals.find(n=>n.id==='ust-kuyga')?.id??terminals[Math.min(2,terminals.length-1)]?.id??'';
    $('graph-network-info').textContent=graph.topology_note
      ? `${graph.nodes.length} пунктов · ${graph.edges.length} участка. ${graph.topology_note} ${graph.coordinate_note}`
      : 'Прототип: все пункты, соединения и расстояния вымышленные. Это не навигационная карта.';
    $('find-river-path').disabled=terminals.length<2;
  }catch(e){if(token===graphRequest)graphResult.textContent=e.message;}
}
$('graph-network').addEventListener('change',initializeGraph);
initializeGraph();
