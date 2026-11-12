// Browser-side validation for yana-route/1. The Python importer is authoritative.
export const rateFields = ['separate_trip_operations_rub', 'active_vessel_hour_rub', 'waiting_vessel_hour_rub'];
const fail = (path, message) => { throw new Error(`${path}: ${message}`); };
const object = (v, path) => { if (!v || typeof v !== 'object' || Array.isArray(v)) fail(path, 'нужен объект'); };
const text = (v, path) => { if (typeof v !== 'string' || !v.trim()) fail(path, 'нужен непустой текст'); };
export function number(v, path, {integer = false, min = 0, money = false} = {}) {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || (integer && !Number.isSafeInteger(v))) fail(path, `нужно ${integer ? 'целое' : 'конечное'} число ≥ ${min}`);
  if (money && Math.abs(v * 100 - Math.round(v * 100)) > 1e-7) fail(path, 'точность до копейки');
  return v;
}
function date(v, path) {
  if (typeof v !== 'string' || !/(Z|[+-]\d\d:\d\d)$/.test(v) || !Number.isFinite(Date.parse(v))) fail(path, 'нужна дата ISO 8601 с часовым поясом');
  return Date.parse(v);
}
export function normalizeLoading(vessel) {
  const v = {...vessel};
  if (v.load_rate_tph == null) {
    if (v.load_setup_h != null && v.load_setup_h !== 0) {
      throw new Error(`${v.id || 'Судно'}: подготовка к погрузке задаётся только вместе со скоростью. Укажите скорость погрузки или обнулите подготовку.`);
    }
    delete v.load_rate_tph;
    delete v.load_setup_h;
  } else {
    if (v.load_h == null) delete v.load_h;
    if (v.load_setup_h == null) v.load_setup_h = 0;
  }
  return v;
}
export function validatePackage(raw) {
  object(raw, 'package');
  if (raw.package_version !== 'yana-route/1') fail('package_version', 'ожидается yana-route/1');
  const p = structuredClone(raw);
  object(p.route, 'route');
  for (const f of ['id', 'origin', 'destination']) text(p.route[f], `route.${f}`);
  if (!Array.isArray(p.sources) || !p.sources.length) fail('sources', 'нужен реестр источников');
  const sourceIds = new Set();
  p.sources.forEach((s, i) => {
    object(s, `sources[${i}]`);
    for (const f of ['id', 'title', 'url', 'accessed_at']) text(s[f], `sources[${i}].${f}`);
    if (!s.url.startsWith('https://') || sourceIds.has(s.id)) fail(`sources[${i}]`, 'нужны HTTPS и уникальный ID');
    sourceIds.add(s.id);
  });
  if (!Array.isArray(p.facts ?? [])) fail('facts', 'нужен список');
  (p.facts ?? []).forEach((f, i) => {
    object(f, `facts[${i}]`); text(f.field, `facts[${i}].field`);
    if (!['documented', 'scenario_assumption', 'unknown'].includes(f.status)) fail(`facts[${i}].status`, 'неизвестный статус');
    if (f.status === 'documented' && !sourceIds.has(f.source_id)) fail(`facts[${i}].source_id`, 'источник не найден');
    if (f.status === 'unknown' && f.value !== null) fail(`facts[${i}].value`, 'неизвестное должно быть null');
  });
  const c = p.scenario; object(c, 'scenario'); text(c.id, 'scenario.id');
  if (c.mode !== 'SIM_ONLY') fail('scenario.mode', 'поддерживается SIM_ONLY');
  if (c.schema_version !== '1.0') fail('scenario.schema_version', 'ожидается 1.0');
  for (const f of ['navigation_assumed', 'reception_assumed', 'fleet_assumed', 'supply_assumed']) if (c[f] !== true) fail(`scenario.${f}`, 'нужно явное true');
  for (const f of ['target_t', 'initial_source_t', 'destination_capacity_t', 'return_cutoff_h']) number(c[f], `scenario.${f}`, {integer:true});
  number(p.deadline_h, 'deadline_h', {integer:true, min:1});
  if (p.deadline_h > c.return_cutoff_h) fail('deadline_h', 'позже срока возврата');
  if (!['trips', 'cost'].includes(p.strategy)) fail('strategy', 'trips / cost');
  if (!Array.isArray(c.vessels) || !c.vessels.length || c.vessels.length > 6) fail('scenario.vessels', 'от 1 до 6 судов для этой формы');
  const ids = new Set();
  c.vessels = c.vessels.map((v, i) => { object(v, `scenario.vessels[${i}]`); return normalizeLoading(v); });
  c.vessels.forEach((v, i) => {
    object(v, `scenario.vessels[${i}]`); text(v.id, `scenario.vessels[${i}].id`);
    if (ids.has(v.id)) fail('scenario.vessels', `повторный ID ${v.id}`);
    ids.add(v.id);
    for (const f of ['available_h', 'capacity_t', 'up_h', 'unload_h', 'return_h', 'prep_h']) number(v[f], `${v.id}.${f}`, {integer:true,min:f === 'available_h' ? 0 : 1});
    if (v.load_rate_tph != null) {
      number(v.load_rate_tph, `${v.id}.load_rate_tph`, {min:Number.MIN_VALUE});
      number(v.load_setup_h ?? 0, `${v.id}.load_setup_h`, {integer:true});
    } else number(v.load_h, `${v.id}.load_h`, {integer:true,min:1});
  });
  for (const name of ['arrivals', 'withdrawals']) {
    if (!Array.isArray(c[name]) || (name === 'arrivals' && (!c[name].length || c[name].length > 12))) fail(`scenario.${name}`, 'нужен список (поступлений 1–12)');
    c[name].forEach((a,i) => { object(a, `${name}[${i}]`); for(const f of ['h','t']) number(a[f], `${name}[${i}].${f}`, {integer:true}); if(name === 'withdrawals' && a.confirmed_in_scenario !== true) fail(`${name}[${i}]`, 'отбор не подтверждён'); });
  }
  if (!Array.isArray(c.payload_calendar) || !c.payload_calendar.length) fail('scenario.payload_calendar', 'нужен непустой календарь');
  const times = new Set();
  c.payload_calendar.forEach((r,i) => { object(r, `payload_calendar[${i}]`); number(r.from_h, 'payload_calendar.from_h', {integer:true}); number(r.capacity_t, 'payload_calendar.capacity_t',{integer:true}); if(times.has(r.from_h)) fail('payload_calendar','повторная граница'); times.add(r.from_h); });
  if (!times.has(0)) fail('payload_calendar', 'календарь должен начинаться в 0');
  if (!Array.isArray(c.closures)) fail('scenario.closures','нужен список');
  function intervals(list, path, at=0) { list.forEach((r,i) => { if(!Array.isArray(r)||r.length!==2) fail(`${path}[${i}]`,'нужен [from,to)'); r.forEach(x=>number(x,path,{integer:true})); if(r[0]<at||r[0]>=r[1]) fail(`${path}[${i}]`,'начало >= объявления, конец > начала'); }); }
  intervals(c.closures,'scenario.closures');
  const terms = p.cost_terms;
  if (terms != null) {
    object(terms,'cost_terms');
    if (terms.billing_mode !== 'all_available_time' || terms.currency !== 'RUB') fail('cost_terms', 'нужны all_available_time и RUB');
    number(terms.horizon_h,'cost_terms.horizon_h'); object(terms.vessel_rates,'cost_terms.vessel_rates');
    if (Object.keys(terms.vessel_rates).length !== ids.size || Object.keys(terms.vessel_rates).some(id=>!ids.has(id))) fail('cost_terms.vessel_rates','ID должны совпадать с флотом');
    if (rateFields.some(f=>Object.hasOwn(terms,f))) fail('cost_terms','нельзя смешивать форматы ставок');
    for (const [id,r] of Object.entries(terms.vessel_rates)) {
      object(r,`vessel_rates.${id}`);
      for (const f of rateFields) { if(r[f]!=null) number(r[f],`${id}.${f}`,{money:true}); else if(p.strategy==='cost') fail(`${id}.${f}`,'укажите ставку для минимума затрат'); }
      if(!['scenario','documented','unspecified'].includes(r.rate_basis)) fail(`${id}.rate_basis`,'неизвестный статус');
      if(r.rate_basis==='documented') text(r.source,`${id}.source`);
    }
  } else if(p.strategy==='cost') fail('cost_terms','нужны тарифы');
  const e = p.event;
  if(e!=null) {
    object(e,'event'); number(e.announced_at_h,'event.announced_at_h',{integer:true});
    if(e.announced_at_h>=p.deadline_h) fail('event.announced_at_h','должно быть раньше срока приёмки');
    if(e.type==='weather_closure') {
      if(e.rule_status!=='scenario_assumption') fail('event.rule_status','нужно scenario_assumption');
      if(!['synthetic','reanalysis','forecast'].includes(e.data_kind)) fail('event.data_kind','неизвестный тип');
      text(e.source,'event.source'); if(!Array.isArray(e.closures)) fail('event.closures','нужен список'); intervals(e.closures,'event.closures',e.announced_at_h);
      if(e.data_kind==='forecast') { if(date(e.forecast_issued_at,'event.forecast_issued_at') > date(c.t0,'scenario.t0')+e.announced_at_h*3600000) fail('event.forecast_issued_at','выпущен после объявления'); }
      else if(e.forecast_issued_at!=null) fail('event.forecast_issued_at','для синтетики и реанализа нужно null');
    } else if(e.type==='arrival_delay') {
      number(e.arrival_index,'event.arrival_index',{integer:true}); number(e.new_arrival_h,'event.new_arrival_h',{integer:true});
      const a=c.arrivals[e.arrival_index]; if(!a||e.announced_at_h>=a.h||e.new_arrival_h<=a.h) fail('event','проверьте поступление и время задержки');
    } else fail('event.type','неизвестное событие');
  }
  return p;
}
