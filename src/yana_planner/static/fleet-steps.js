// Presentation only: keep all existing fields and handlers in the same form.
const form=document.getElementById('conditions-form');
if(form){
 const main=document.getElementById('main'),resources=document.getElementById('resources-settings');
 const route=document.createElement('div');route.id='fleet-direction';
 const hero=main.querySelector('.hero'),workspace=document.getElementById('route-workspace'),examples=main.querySelector('.quick-examples');
 workspace.before(route);route.append(hero,workspace);if(examples)route.append(examples);
 const arrivals=document.getElementById('arrivals').closest('section');arrivals.id='fleet-arrivals';resources.after(arrivals);
 const note=main.querySelector('.model-note');document.getElementById('constraints-settings').append(note);
 const panels=[route,document.getElementById('task'),resources,arrivals,document.getElementById('cost-settings'),document.getElementById('constraints-settings'),document.getElementById('run-section')];
 const labels=['Направление','Задание','Суда','Поступления','Экономика','Ограничения','Запуск'];
 const descriptions=['Выберите маршрут или загрузите пример.','Задайте необходимый объём, срок и общие ограничения.','Измените доступный флот, вместимости, времена операций и ставки судов.','Задайте время и объём поступления каждой партии топлива.','Выберите стратегию и условия оплаты. Неизвестные ставки не заменяются нулями.','При необходимости задайте задержку топлива или запрет движения.','Проверьте сводку и запустите сравнение READY и MILP.'];
 const nav=document.createElement('nav');nav.className='fleet-step-nav';nav.setAttribute('aria-label','Этапы выбора флота и рейсов');
 const buttons=labels.map((label,i)=>{const b=document.createElement('button');b.type='button';b.textContent=`${String(i+1).padStart(2,'0')} ${label}`;b.addEventListener('click',()=>show(i,true));nav.append(b);return b;});main.querySelector('.planner-modes').after(nav);
 panels.forEach((p,i)=>{p.dataset.fleetPanel=String(i);if(p.tagName==='DETAILS')p.open=true;
  const help=document.createElement('p');help.className='fleet-step-help';help.textContent=`Этап ${i+1} из ${panels.length}. ${descriptions[i]}`;p.prepend(help);
  const actions=document.createElement('div');actions.className='fleet-step-actions';
  if(i>0){const back=document.createElement('button');back.type='button';back.className='button secondary';back.textContent='← Назад';back.addEventListener('click',()=>show(i-1,true));actions.append(back);}
  if(i<panels.length-1){const next=document.createElement('button');next.type='button';next.className='button';next.textContent='Далее: '+labels[i+1]+' →';next.addEventListener('click',()=>show(i+1,true));actions.append(next);}p.append(actions);
 });
 let current=0;
 function show(i,focus=false){current=i;panels.forEach((p,j)=>p.hidden=i!==j);form.hidden=i===0;form.querySelector('.form-main').hidden=i===6;buttons.forEach((b,j)=>{if(i===j)b.setAttribute('aria-current','step');else b.removeAttribute('aria-current');});if(panels[i].tagName==='DETAILS')panels[i].open=true;
  if(focus){nav.scrollIntoView({block:'start'});const h=panels[i].querySelector('h1,h2');if(h){h.tabIndex=-1;h.focus({preventScroll:true});}}
 }
 function reveal(target){const i=panels.findIndex(p=>p.contains(target));if(i>=0)show(i,true);}
 document.addEventListener('click',e=>{const b=e.target.closest('[data-open]');if(b){const target=document.getElementById(b.dataset.open);if(target)reveal(target);}},true);
 document.addEventListener('fleet-step-request',e=>{const target=document.getElementById(e.detail);if(target)reveal(target);});
 let revealing=false;form.addEventListener('invalid',e=>{if(revealing)return;revealing=true;setTimeout(()=>revealing=false,0);reveal(e.target);},true);
 // Enter from an earlier step always brings the visible calculation status into view.
 document.addEventListener('submit',e=>{if(e.target===form&&current!==6)show(6,true);},true);
 document.body.classList.add('fleet-wizard');show(document.querySelector('.form-error')?6:0);
}
