import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {validatePackage, normalizeLoading} from '../src/yana_planner/static/route-package.js';
const base=JSON.parse(readFileSync(new URL('../examples/routes/ust-kuyga-batagay.package.json',import.meta.url),'utf8'));
test('valid package is copied and unknown facts stay null',()=>{const p=validatePackage(base);p.route.origin='CHANGED';assert.notEqual(base.route.origin,p.route.origin);assert.equal(base.facts.at(-1).value,null);});
test('explicit zero is preserved, missing cost rate rejected',()=>{const p=structuredClone(base);p.strategy='cost';p.cost_terms.vessel_rates['UKB-SIM-A'].active_vessel_hour_rub=0;assert.equal(validatePackage(p).cost_terms.vessel_rates['UKB-SIM-A'].active_vessel_hour_rub,0);p.cost_terms.vessel_rates['UKB-SIM-A'].active_vessel_hour_rub=null;assert.throws(()=>validatePackage(p),/ставку/);});
test('documented rate requires a source and unknown fleet is rejected',()=>{const p=structuredClone(base);p.cost_terms.vessel_rates['UKB-SIM-A'].rate_basis='documented';p.cost_terms.vessel_rates['UKB-SIM-A'].source='';assert.throws(()=>validatePackage(p),/source/);p.cost_terms.vessel_rates.bad={};assert.throws(()=>validatePackage(p),/ID/);});
test('future-issued forecast and past closure rejected',()=>{const p=structuredClone(base);p.event={type:'weather_closure',announced_at_h:60,closures:[[120,132]],rule_status:'scenario_assumption',data_kind:'forecast',source:'test',forecast_issued_at:'2026-08-13T21:00:00+09:00'};assert.throws(()=>validatePackage(p),/после объявления/);p.event.forecast_issued_at='2026-08-11T08:00:00+09:00';validatePackage(p);p.event.closures=[[59,70]];assert.throws(()=>validatePackage(p),/closures/);});
test('NaN, strings, negative and sub-cent rates rejected',()=>{for(const value of [NaN,Infinity,-1,'100',true,0.001]){const p=structuredClone(base);p.cost_terms.vessel_rates['UKB-SIM-A'].active_vessel_hour_rub=value;assert.throws(()=>validatePackage(p));}});
test('the control and route packages share the versioned import format',()=>{const control=JSON.parse(readFileSync(new URL('../examples/routes/r03.package.json',import.meta.url),'utf8'));assert.equal(validatePackage(control).scenario.target_t,1650);assert.equal(validatePackage(base).scenario.target_t,600);});

test('gather loading normalization omits setup without speed and preserves fixed duration',()=>{
  const original={id:'A',load_h:6,load_setup_h:0};
  assert.deepEqual(normalizeLoading(original),{id:'A',load_h:6});
  assert.equal(original.load_setup_h,0);
  assert.throws(()=>normalizeLoading({...original,load_setup_h:2}),/только вместе со скоростью/);
  assert.throws(()=>normalizeLoading({...original,load_rate_tph:null,load_setup_h:2}),/только вместе со скоростью/);
});
test('gather loading normalization omits null fixed duration with rate',()=>{
  assert.deepEqual(normalizeLoading({id:'A',load_rate_tph:50,load_setup_h:1,load_h:null}),{id:'A',load_rate_tph:50,load_setup_h:1});
  const p=structuredClone(base);p.scenario.vessels[0].load_setup_h=2;
  assert.throws(()=>validatePackage(p),/только вместе со скоростью/);
  p.scenario.vessels[0].load_rate_tph=50;p.scenario.vessels[0].load_h=null;
  assert.equal(Object.hasOwn(validatePackage(p).scenario.vessels[0],'load_h'),false);
});
