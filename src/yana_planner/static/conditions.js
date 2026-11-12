const form = document.getElementById('conditions-form');
const vessels = document.getElementById('vessels');
const arrivals = document.getElementById('arrivals');
const value = name => form.elements.namedItem(name)?.value ?? '';
const fields = name => [...form.querySelectorAll(`[name="${name}"]`)];
function update() {
  const delayed = value('scenario') === 'delay';
  document.getElementById('delay-settings').hidden = !delayed;
  document.querySelectorAll('#delay-settings input').forEach(input => { input.disabled = !delayed; });
  const caps = fields('vessel_capacity_t').map(input => Number(input.value));
  document.getElementById('conditions-summary').textContent =
    `Цель: ${value('target_t')} т · Судов: ${caps.length} · Вместимости: ${caps.join(' / ')} т · Приёмка до ${value('deadline')} ч · Возврат до ${value('return_cutoff_h')} ч.`;
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
      row.querySelector('legend').textContent = `${title} ${i + 1}`;
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
form.addEventListener('submit', () => {
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  button.textContent = 'Идёт расчёт…';
});
update();
