// Draft UI contract. No network calls or solver integration.
export const points = [
  {id:'nizhneyansk',name:'Нижнеянск',site:'nizhneyansk-sngs',note:'Нефтебаза СНГС; эксплуатационные условия не подтверждены'},
  {id:'kazachye',name:'Казачье · склад Кресты',site:'kresty-tes-candidate',candidate:true,note:'Кандидат: место речного слива и километровка неизвестны. Казачье — конечный потребитель после автоплеча'},
  {id:'ust-kuyga',name:'Усть-Куйга',site:'ust-kuyga-sngs',note:'Объект СНГС известен; связь с конкретной кампанией требует проверки'},
  {id:'saydy-verkhoyansky',name:'Сайды · Верхоянский район',site:'saydy-diesel-candidate',candidate:true,note:'Кандидат: приём НКТ и речной слив не подтверждены'},
  {id:'batagay',name:'Батагай',site:'batagay-sngs',note:'Нефтебаза СНГС известна; причал и условия кампании не подтверждены'},
];
const point = id => points.find(p=>p.id===id);
function integer(value,label,min=0,max=Number.MAX_SAFE_INTEGER) {
  if(value===null||value===undefined||String(value).trim()==='') throw Error(`${label}: заполните значение`);
  const n=Number(value);
  if(!Number.isSafeInteger(n)||n<min||n>max) throw Error(`${label}: нужно целое число от ${min} до ${max}`);
  return n;
}
export function intervals(text,horizon,label,{nonempty=true}={}) {
  if(!String(text).trim()) {if(nonempty) throw Error(`${label}: задайте хотя бы одно окно`);return [];}
  const result=String(text).split(';').map(part=>{
    const m=part.trim().match(/^(\d+)\s*[-–]\s*(\d+)$/);
    if(!m) throw Error(`${label}: формат 48–72; 80–130`);
    const a=integer(m[1],label,0,horizon), b=integer(m[2],label,1,horizon);
    if(a>=b) throw Error(`${label}: конец должен быть позже начала`);
    return [a,b];
  });
  if(result.length>72) throw Error(`${label}: не больше 72 интервалов`);
  return result;
}
export function buildProposal(state,{draftOnly=false}={}) {
  if(!point(state.origin_id)) throw Error('Выберите отправление из каталога');
  if(!Array.isArray(state.stops)||state.stops.length<1||state.stops.length>4) throw Error('В макете выберите от одного до четырёх получателей');
  if(!draftOnly&&state.stops.length>2) throw Error('Форма первой версии поддерживает максимум двух получателей. Для 3–4 доступен только черновик, без расчётного входа.');
  if(state.assumptions_confirmed!==true) throw Error('Подтвердите учебные допущения: навигация, судно, запас и приёмка не подтверждены');
  const horizon=integer(state.horizon_h,'Горизонт',1,492), seen=new Set([state.origin_id]);
  const requirements=[];
  const stops=state.stops.map((s,i)=>{
    const p=point(s.id); if(!p||seen.has(s.id)) throw Error(`Получатель ${i+1}: выберите уникальный пункт, отличный от отправления`);seen.add(s.id);
    if((p.candidate||point(state.origin_id).candidate)&&state.candidates_confirmed!==true) throw Error('Казачье и Сайды — кандидаты. Подтвердите их использование только в учебном черновике.');
    if(!String(s.label||'').trim()) throw Error(`Получатель ${i+1}: добавьте подпись требования`);
    const stop={id:s.id,required_t:integer(s.required_t,`Объём ${i+1}`,1),deadline_h:integer(s.deadline_h,`Срок ${i+1}`,1,horizon),unload_h:integer(s.unload_h,`Выгрузка ${i+1}`,1,492),windows:intervals(s.windows,horizon,`Окна ${i+1}`)};
    if(!stop.windows.some(([a,b])=>Math.min(b,stop.deadline_h)-a>=stop.unload_h)) throw Error(`Получатель ${i+1}: выгрузка не помещается ни в одно окно до срока`);
    requirements.push({stop_id:s.id,label:s.label.trim(),status:'scenario',source_ids:[],operation_site_id:p.site,operation_site_status:p.candidate?'candidate':'unverified_for_campaign'});
    return stop;
  });
  const capacity=integer(state.vessel.capacity_t,'Вместимость судна',1);
  const total=stops.reduce((sum,s)=>sum+s.required_t,0);
  if(!Number.isSafeInteger(total)||total>capacity) throw Error('Суммарная партия превышает вместимость одного судна. Прототип загружает всю партию за один раз.');
  const travel=state.stops.map((s,i)=>integer(s.travel_h,`Движение до получателя ${i+1}`,1,492));
  const arrivals=(state.arrivals||[]).map((a,i)=>({h:integer(a.h,`Поступление ${i+1}`,0,horizon),t:integer(a.t,`Партия поступления ${i+1}`,1)})).sort((a,b)=>a.h-b.h);
  if(arrivals.length>100) throw Error('Не больше 100 поступлений');
  const vessel={id:String(state.vessel.id||'').trim(),capacity_t:capacity,available_h:integer(state.vessel.available_h,'Доступность судна',0,horizon),load_h:integer(state.vessel.load_h,'Налив',1,492),travel_h:travel,return_h:integer(state.vessel.return_h,'Возврат',1,492),prep_h:integer(state.vessel.prep_h,'Подготовка после возврата',0,492)};
  if(!vessel.id) throw Error('Заполните идентификатор судна');
  const closures=Object.fromEntries(state.stops.map((s,i)=>[`leg${i+1}`,intervals(s.movement_closures||'',horizon,`Запреты плеча ${i+1}`,{nonempty:false})]));
  closures.return=intervals(state.return_closures||'',horizon,'Запреты возврата',{nonempty:false});
  const solver={mode:'SIM_ONLY',navigation_assumed:true,fleet_assumed:true,supply_assumed:true,reception_assumed:true,origin_id:state.origin_id,horizon_h:horizon,initial_source_t:integer(state.initial_source_t,'Начальный запас',0),arrivals,vessel,stops,movement_closures:closures,provenance:'Все численные параметры введены как учебные допущения. Объекты, допуск судна, продукт и исполнение общей кампании не подтверждены.'};
  return {schema:'yana.multi-recipient-ui-draft.v1',contract_status:'proposal_not_server_api',ui_release_max_recipients:2,calculation_connected:false,time_basis:{kind:'relative_integer_hours',t0:null,timezone:null,note:'Часы относительно условного начала; календарный срок закупки не конвертирован.'},product:{id:'synthetic-compatible-fuel',label:'Единый совместимый продукт — учебное допущение',status:'scenario'},route_review:{sequence:[state.origin_id,...stops.map(s=>s.id)],graph_path:null,river_distance_km:null,status:'ordered_stops_only_not_verified_path'},requirements,total_required_t:total,objective_display:stops.length===2?'Обязательные объёмы и сроки обоим; затем максимально ранняя приёмка второму.':'Обязательные объёмы и сроки; ранняя последняя приёмка. Порядок задан пользователем.',draft_only:draftOnly,solver_input:draftOnly?null:solver,...(draftOnly?{draft_parameters:solver}:{} )};
}
