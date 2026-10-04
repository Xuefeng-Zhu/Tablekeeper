// Synthetic contract experiment, not service/UI implementation or independent QA.
const assert = require('node:assert/strict');
const started = new Date().toISOString();
const checks = [];
const canonical=x=>x===null||typeof x!=='object'?JSON.stringify(x):Array.isArray(x)?'['+x.map(canonical).join(',')+']':'{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';
const restaurant={tables:[{id:'b',capacity:2},{id:'a',capacity:4},{id:'c',capacity:3}],combinable:[['b','a'],['c','a']]};
function normalize(ids){ if(new Set(ids).size!==ids.length)throw Error('validation_failed');if(ids.length===1)return ids;if(ids.length!==2)throw Error('combination_not_allowed');const pair=restaurant.combinable.find(p=>p.every(t=>ids.includes(t)));if(!pair)throw Error('combination_not_allowed');return [...pair]; }
assert.deepEqual(normalize(['a','b']),['b','a']);assert.throws(()=>normalize(['b','c']),/combination_not_allowed/);assert.throws(()=>normalize(['a','a']),/validation_failed/);
assert.notEqual(canonical({table_ids:['a','b']}),canonical({table_ids:['b','a']}));
checks.push({name:'declared order/nontransitivity and domain equality distinct from receipt JSON equality',status:'PASS'});
function overlaps(x,y){return x.start<y.end&&y.start<x.end&&x.ids.some(t=>y.ids.includes(t));}
const single={start:0,end:90,ids:['a']},pair={start:0,end:90,ids:['b','a']};assert(overlaps(single,pair));assert(!overlaps(single,{...pair,start:90,end:180}));
checks.push({name:'pair member intersection and half-open adjacency',status:'PASS'});
const old={schema_version:1,users:[{id:'u',credential:{algorithm:'scrypt',salt:'synthetic-only',hash:'synthetic-only'}}],sessions:[{token:'synthetic-session',user_id:'u'}],restaurants:[{id:'r',tables:restaurant.tables}],reservations:[{id:'id',reference:'ABC123',table_ids:['b'],created_at:'2026-01-01T00:00:00+00:00',start:1,end:2}],receipts:[{body:{table_id:'b',table_ids:'ignored-legacy-field'},response:{table_id:'b',reference:'ABC123'}}]};
const before=JSON.stringify(old), migrated=structuredClone(old);migrated.schema_version=2;migrated.restaurants.forEach(r=>r.combinable??=[]);migrated.receipts.forEach(r=>r.response_schema_version=1);
assert.equal(JSON.stringify(old),before);for(const key of ['users','sessions','reservations'])assert.deepEqual(migrated[key],old[key]);assert.deepEqual(migrated.receipts[0].body,old.receipts[0].body);assert.deepEqual(migrated.receipts[0].response,old.receipts[0].response);assert(!('table_ids' in migrated.receipts[0].response));
checks.push({name:'detached schema migration preserves legacy body/receipt/session/credential values',status:'PASS',limitation:'synthetic envelope, not actual import validator'});
let generation=0,result=null,form=null;
const startSearch=()=>++generation;
function publish(g,bundle){if(g!==generation)return false;result=bundle;form=null;return true;}
const a=startSearch(),b=startSearch();assert(publish(b,{restaurant:'B',labels:['B table'],slots:['20:00']}));form={restaurant:'B',table:'B table'};assert(!publish(a,{restaurant:'A',labels:['A table'],slots:['19:00']}));assert.equal(result.restaurant,'B');assert.equal(form.restaurant,'B');
checks.push({name:'latest generation owns bundled labels/results/booking selection',status:'PASS'});
let attempt={id:1,key:'synthetic-key',body:{restaurant_id:'B',table_id:'b',starts_at_local:'2026-10-05T20:00',party_size:2},status:'pending'},confirmed=null,error=null;
const frozenBody=canonical(attempt.body),key=attempt.key;attempt.status='uncertain';assert.equal(error,null);assert.equal(confirmed,null);assert.equal(canonical(attempt.body),frozenBody);assert.equal(attempt.key,key);
// Upgrade notification is not an event in this state machine; between requests it cannot reset identity.
const original={reference:'ABC123',table_id:'b'};attempt.status='confirmed';confirmed=original;assert.equal(confirmed.reference,'ABC123');assert.equal(attempt.key,key);
const prior=attempt;attempt={id:2,key:'new-key',body:{...prior.body,party_size:3},status:'editing'};confirmed=null;
function onReply(id,response){if(id!==attempt.id)return;confirmed=response;}
onReply(prior.id,{reference:'LATE01'});assert.equal(confirmed,null);
const stableForm=structuredClone(form);error='table_unavailable';const refreshGeneration=generation;const refreshAttempt=attempt.id;
function refresh(g,id,slots){if(g===generation&&id===attempt.id){result={...result,slots};}}
refresh(refreshGeneration,refreshAttempt,[]);assert.deepEqual(form,stableForm);startSearch();assert.equal(publish(refreshGeneration,{restaurant:'B'}),false);
checks.push({name:'uncertain body/key survives retry/upgrade; stale replies suppressed; conflict refresh preserves form',status:'PASS'});
console.log(JSON.stringify({work_item:'TK-S2-ARCH',owner:'@frankzhu94/factory-architect',starting_revision:'f55aceb2ec52a30a46bd72df9ef1267c5f930b35',started_at:started,finished_at:new Date().toISOString(),node:process.version,checks,layer:'synthetic host contract probes',product_gate:'NOT_TESTED'},null,2));
