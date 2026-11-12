const form = document.getElementById('conditions-form');
const vessels = document.getElementById('vessels');
const arrivals = document.getElementById('arrivals');
const value = name => form.elements.namedItem(name)?.value ?? '';
const fields = name => [...form.querySelectorAll(`[name="${name}"]`)];
function update() {
  const delayed = value('scenario') === 'delay';
  document.getElementById('delay-settings').hidden = !delayed;
  document.querySelectorAll('#delay-settings input').forEach(input => { input.disabled = !delayed; });
  const caps = fields('vessel_capacity_t').map(input => input.value || '—');
  const ids = fields('vessel_id').map(input => input.value || 'Без названия');
  const supply = fields('arrival_h').map((input,i)=>`${fields('arrival_t')[i].value || '—'} т в ${input.value || '—'} ч`);
  document.getElementById('resource-summary').textContent = ids.map((id,i)=>`${id} · ${caps[i]} т · с ${fields('vessel_available_h')[i].value || '—'} ч`).join(' / ');
  document.getElementById('supply-summary').textContent = `Начальный запас: ${value('initial_source_t') || '0'} т. ${supply.join('; ') || 'Поступления не заданы'}`;
  document.getElementById('resources-preview').textContent = `Судов: ${caps.length} · поступлений: ${supply.length}`;
  const priced = value('economics_enabled') === 'yes';
  const strategy = value('strategy') === 'cost' ? 'Минимум сценарных затрат' : 'Минимум рейсов';
  document.getElementById('strategy-summary').textContent = `Сначала принятый объём → ${strategy.toLowerCase()} → ранняя приёмка`;
  document.getElementById('cost-preview').textContent = `${strategy} · ${priced ? 'тарифы включены' : 'без оценки стоимости'}`;
  document.getElementById('constraints-preview').textContent = delayed ? 'Задержка поступления топлива' : document.getElementById('weather-enabled').checked ? 'Событие запрета движения' : 'Дополнительное событие не задано';
  document.getElementById('target-preview').textContent = value('target_t') || '—';
  document.getElementById('fleet-preview').textContent = caps.length;
  document.getElementById('deadline-preview').textContent = value('deadline') || '—';
  document.getElementById('conditions-summary').textContent =
    `Вместимости: ${caps.join(' / ')} т. Возврат до ${value('return_cutoff_h')} ч.`;
  const row = Number(value('delayed_arrival')) - 1;
  const hours = fields('arrival_h');
  const tonnes = fields('arrival_t');
  document.getElementById('delay-summary').textContent = delayed
    ? (hours[row] ? `Задержка поступления №${row + 1}: ${tonnes[row].value} т ожидались в ${hours[row].value} ч, поступят в ${value('new_arrival_h')} ч. Известно с ${value('announced_at_h')} ч. Начатые рейсы сохраняются.` : 'Выберите существующее поступление для задержки.')
    : 'Расчёт без задержки поступлений.';
  for (const [container, selector, title, maximum, add] of [
    [vessels, '.vessel-row', 'Судно', 6, 'add-vessel'],
    [arrivals, '.arrival-row', 'Поступление', 12, 'add-arrival']
  ]) {
    const rows = [...container.querySelectorAll(selector)];
    rows.forEach((row, i) => {
      row.querySelector(':scope > legend').textContent = `${title} ${i + 1}`;
      row.querySelector('button').disabled = rows.length === 1;
    });
    document.getElementById(add).disabled = rows.length >= maximum;
  }
  form.elements.namedItem('delayed_arrival').max = hours.length;
}
function addRow(container, selector) {
  const row = container.querySelector(selector).cloneNode(true);
  if (container === vessels) {
    const ids = fields('vessel_id').map(input => input.value);
    let number = 1;
    while (ids.includes(`SIM-${number}`)) number++;
    row.querySelector('[name="vessel_id"]').value = `SIM-${number}`;
  } else {
    row.querySelector('[name="arrival_h"]').value = '';
    row.querySelector('[name="arrival_t"]').value = '';
  }
  row.querySelectorAll('[data-rate] input').forEach(input => { input.value = ''; });
  row.querySelectorAll('[name="vessel_rate_basis"]').forEach(input => { input.value = 'unspecified'; });
  container.append(row);
  update();
  row.querySelector('input').focus();
}
document.getElementById('add-vessel').addEventListener('click', () => addRow(vessels, '.vessel-row'));
document.getElementById('add-arrival').addEventListener('click', () => addRow(arrivals, '.arrival-row'));
form.addEventListener('click', event => {
  if (event.target.matches('.remove-vessel, .remove-arrival')) {
    event.target.closest('fieldset').remove();
    update();
  }
});
form.addEventListener('input', update);
form.addEventListener('change', update);
let progressTimer;
form.addEventListener('submit', () => {
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  button.textContent = 'Идёт расчёт…';
  const progress = document.getElementById('calculation-progress');
  const limit = Number(form.dataset.totalTimeout);
  const started = Date.now();
  progress.hidden = false;
  function tick() {
    const seconds = Math.floor((Date.now() - started) / 1000);
    document.getElementById('calculation-elapsed').textContent = `Прошло ${seconds} с. Общий предел вычисления — ${limit} с.`;
    document.getElementById('calculation-hint').textContent = seconds >= limit
      ? 'Ожидаем сообщение сервера о завершении или достижении лимита. Повторно запускать расчёт не нужно.'
      : seconds >= 10
        ? 'Поиск продолжается. Если времени не хватит, условия останутся в форме. Можно будет выбрать быстрый режим.'
        : 'Подготавливаем данные и ищем допустимое расписание. Дождитесь результата.';
  }
  tick();
  clearInterval(progressTimer);
  progressTimer = setInterval(tick, 1000);
  progress.scrollIntoView({block: 'center', behavior: 'smooth'});
});
window.addEventListener('pageshow', () => {
  clearInterval(progressTimer);
  const button = form.querySelector('[type="submit"]');
  button.disabled = true; // dispatcher.js enables only server-supported settings.
  button.textContent = 'Рассчитать план ↗';
  document.getElementById('calculation-progress').hidden = true;
});
update();

form.addEventListener('invalid', event => {
  let parent = event.target.parentElement;
  while(parent) { if(parent.tagName==='DETAILS') parent.open=true; parent=parent.parentElement; }
}, true);

document.addEventListener('click', event=>{
  const button=event.target.closest('[data-open]');
  if(!button) return;
  const target=document.getElementById(button.dataset.open);
  if(target) { target.open=true; target.scrollIntoView({block:'start',behavior:'smooth'}); }
});
