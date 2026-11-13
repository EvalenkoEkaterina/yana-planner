// Pure rules shared by the page and option-transition regression tests.
export function reconcileOptions(state, changed) {
  const next = {...state};
  if (changed === 'strategy' && next.strategy === 'cost') next.economics = true;
  if (changed === 'economics' && !next.economics) next.strategy = 'trips';
  if (changed === 'weather' && next.weather) next.delayed = false;
  if (changed === 'delayed' && next.delayed) next.weather = false;
  return next;
}

export function costInputIssue({strategy, economics, confirmed, vessels}) {
  if (!economics) return null;
  if (!confirmed) return {message:'Подтвердите схему оплаты всего заявленного флота.', selector:'#billing-confirmed'};
  for (const [index, vessel] of vessels.entries()) {
    for (const [field, label] of [
      ['separate_trip_operations_rub','расходы за рейс'],
      ['active_vessel_hour_rub','ставка активного часа'],
      ['waiting_vessel_hour_rub','ставка часа ожидания']
    ]) {
      if (strategy === 'cost' && String(vessel[field] ?? '').trim() === '')
        return {message:`${vessel.id}: заполните «${label}». Можно явно загрузить учебные ставки выбранного примера.`, selector:`.vessel-row:nth-child(${index+1}) [name="vessel_${field}"]`};
    }
    if (vessel.rate_basis === 'documented' && !vessel.source.trim())
      return {message:`${vessel.id}: укажите источник подтверждённых ставок.`, selector:`.vessel-row:nth-child(${index+1}) [name="vessel_rate_source"]`};
  }
  return null;
}
