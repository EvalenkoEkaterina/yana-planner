const search = document.getElementById('history-search');
const rows = [...document.querySelectorAll('[data-history-row]')];
search.addEventListener('input', () => {
  const query = search.value.trim().toLocaleLowerCase('ru');
  let count = 0;
  rows.forEach(row => {
    row.hidden = !row.textContent.toLocaleLowerCase('ru').includes(query);
    if (!row.hidden) count++;
  });
  document.getElementById('history-count').textContent = `Найдено: ${count}`;
  document.getElementById('history-empty').hidden = count !== 0;
});
