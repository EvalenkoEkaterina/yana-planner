import {validatePackage, rateFields, normalizeLoading} from './route-package.js';
const form = document.getElementById('conditions-form');
const $ = id => document.getElementById(id);
const values = name => [...form.querySelectorAll(`[name="${name}"]`)];
const first = name => values(name)[0];
const base = $('route-workspace').dataset.staticBase;
const caps = JSON.parse(form.dataset.capabilities || '{}');
let current, routeKey='r03', loading=false, routeRequest=0, snapshot;
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
  button.disabled=loading || missing.length>0;
  button.textContent=missing.length ? 'Ожидает подключения расчёта' : 'Рассчитать план ↗';
}
function gather() {
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
    if(!$('billing-confirmed').checked) throw new Error('Подтвердите схему оплаты всего доступного флота');
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
  host.append(node('p',`${p.route.origin} → ${p.route.destination}. ${p.route.length_km ? p.route.length_km+' км — сумма опубликованных участков.' : 'Контрольный пример R03.'}`));
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
$('use-scenario-rates').addEventListener('click',()=>{
  for(const row of form.querySelectorAll('.vessel-row')) {rateFields.forEach((f,i)=>row.querySelector(`[name="vessel_${f}"]`).value=[25000,1000,500][i]);row.querySelector('[name="vessel_rate_basis"]').value='scenario';row.querySelector('[name="vessel_rate_source"]').value='Предложение Ушановой 13.11.2026; не подтверждено перевозчиком';}update();
});
$('strategy').addEventListener('change',()=>{if($('strategy').value==='cost')$('economics-enabled').value='yes';update();});
form.addEventListener('input',update); form.addEventListener('change',update);
form.addEventListener('click',e=>{if(e.target.matches('.remove-vessel, .remove-arrival'))update();});
$('add-vessel').addEventListener('click',update); $('add-arrival').addEventListener('click',update);
function download(p){ const blob=new Blob([JSON.stringify(p,null,2)+'\n'],{type:'application/json'});const url=URL.createObjectURL(blob);const a=node('a');a.href=url;a.download='yana-route.package.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
$('export-package').addEventListener('click',()=>{try{const p=gather();download(p);status('Пакет проверен и скачан. Это входные условия, не результат расчёта.');}catch(e){status(e.message);}});
form.addEventListener('submit',e=>{
  try {
    if(requiredCapabilities().length) throw new Error('Сервер ещё не поддерживает эти настройки. Скачайте пакет условий для интеграции.');
    // Preserve historical R03 behaviour when only the old transport fields are used.
    if(Object.keys(caps).length) $('route-package-json').value=JSON.stringify(gather());
  }catch(error){e.preventDefault();e.stopImmediatePropagation();status(error.message);update();
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
} else {
  try{const r=await fetch(`${base}r03.package.json`);if(!r.ok)throw new Error('Не удалось загрузить метаданные R03');current=validatePackage(await r.json());$('scenario-t0').value=current.scenario.t0??'';renderFacts(current);}catch(e){status(e.message);}
}
update();
