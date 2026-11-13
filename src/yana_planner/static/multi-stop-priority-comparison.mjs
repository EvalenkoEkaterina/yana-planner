// Compare saved schedules under identical conditions, changing only importance.
const stable=value=>JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a.localeCompare(b))):v);
export function comparisonRequest(base){
 if(!base.goal_met||base.receipt_priority.length<2)throw Error('Для сравнения нужен допустимый план минимум с двумя получателями');
 const scenario=structuredClone(base.input);
 scenario.receipt_priority=[...base.receipt_priority].reverse();
 return {schema_version:'yana-multi-stop-request/1',scenario,cost_terms:structuredClone(base.cost_terms??null),search_seconds:base.saved_run?.search_seconds??2};
}
export function comparePriorities(base,alternative){
 const modelVersion=r=>r.vessel_selection?.candidate_results.find(c=>c.input.vessel.id===r.input.vessel.id)?.code_version??r.code_version;
 if(modelVersion(base)&&modelVersion(alternative)&&modelVersion(base)!==modelVersion(alternative))throw Error('Нельзя сравнивать: расчёты выполнены разными версиями модели');
 const a=structuredClone(base.input),b=structuredClone(alternative.input);delete a.receipt_priority;delete b.receipt_priority;
 if(stable(a)!==stable(b)||stable(base.cost_terms??null)!==stable(alternative.cost_terms??null))throw Error('Нельзя сравнивать: условия рейсов различаются');
 if(stable([...base.receipt_priority].reverse())!==stable(alternative.receipt_priority))throw Error('Ожидался обратный порядок важности');
 const comparable=!!(base.goal_met&&base.plan&&alternative.goal_met&&alternative.plan);
 const rows=comparable?base.plan.deliveries.map(d=>{
  const other=alternative.plan.deliveries.find(x=>x.stop_id===d.stop_id);
  if(!other||other.accepted_t!==d.accepted_t)throw Error('Объёмы поставок в сравниваемых планах различаются');
  return {stop_id:d.stop_id,selected_h:d.receipt_h,reversed_h:other.receipt_h,delta_h:other.receipt_h-d.receipt_h};
 }):[];
 return {comparable,rows,same_receipts:comparable&&rows.every(r=>r.delta_h===0),ready_delta_h:comparable?alternative.plan.ready_h-base.plan.ready_h:null,both_proven:comparable&&base.lexicographic_optimal===true&&alternative.lexicographic_optimal===true};
}
