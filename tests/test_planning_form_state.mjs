import assert from 'node:assert/strict';
import test from 'node:test';
import {reconcileOptions,costInputIssue} from '../src/yana_planner/static/planning-form-state.mjs';

test('all option combinations: switching filters produces consistent independent settings',()=>{
 for(const economics of [true,false]) for(const weather of [true,false]) for(const delayed of [true,false]) for(const strategy of ['trips','cost']) {
  const state={economics,weather,delayed,strategy}, original=structuredClone(state);
  for(const changed of ['economics','weather','delayed','strategy']) {
   const next=reconcileOptions(state,changed);
   if(changed==='economics'&&!economics)assert.equal(next.strategy,'trips');
   if(changed==='strategy'&&strategy==='cost')assert.equal(next.economics,true);
   if(changed==='weather'&&weather)assert.equal(next.delayed,false);
   if(changed==='delayed'&&delayed)assert.equal(next.weather,false);
   assert.deepEqual(state,original);
  }
 }
});
test('cost errors point to billing or exact missing rate; explicit zero works',()=>{
 const state={strategy:'cost',economics:true,confirmed:false,vessels:[{id:'SIM-A',rate_basis:'scenario',source:'',separate_trip_operations_rub:'0',active_vessel_hour_rub:'0',waiting_vessel_hour_rub:'0'}]};
 assert.equal(costInputIssue(state).selector,'#billing-confirmed');
 state.confirmed=true;assert.equal(costInputIssue(state),null);
 state.vessels[0].active_vessel_hour_rub='';
 assert.match(costInputIssue(state).selector,/vessel_active_vessel_hour_rub/);
 state.strategy='trips';assert.equal(costInputIssue(state),null);
 state.vessels[0].rate_basis='documented';assert.match(costInputIssue(state).selector,/vessel_rate_source/);
 state.economics=false;assert.equal(costInputIssue(state),null);
});
