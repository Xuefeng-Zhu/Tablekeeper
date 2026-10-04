// Architectural experiment only. Not a service implementation or release test.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const {promisify} = require('node:util');
const {Temporal} = require(process.env.TEMPORAL_MODULE || '@js-temporal/polyfill');
const started = new Date().toISOString();
const checks = [];
function resolve(local, zone) {
  const wall = Temporal.PlainDateTime.from(local, {overflow:'reject'});
  const z = wall.toZonedDateTime(zone, {disambiguation:'earlier'});
  if (!z.toPlainDateTime().equals(wall)) throw new Error('invalid_local_time');
  return z;
}
for (const [zone,gap,fold,offset,end] of [
 ['Europe/Berlin','2026-03-29T02:30','2026-10-25T02:30','+02:00','2026-10-25T03:00:00+01:00'],
 ['America/New_York','2026-03-08T02:30','2026-11-01T01:30','-04:00','2026-11-01T02:00:00-05:00']
]) {
 assert.throws(()=>resolve(gap,zone), /invalid_local_time/);
 const z=resolve(fold,zone); assert.equal(z.offset,offset);
 assert.equal(z.add({minutes:90}).toString({timeZoneName:'never'}),end);
 assert.equal(z.add({minutes:90}).epochMilliseconds-z.epochMilliseconds,5400000);
 checks.push({name:zone+' gap/fold/absolute-duration',status:'PASS',fold:z.toString(),end});
}
const canonical = x => x === null || typeof x !== 'object' ? JSON.stringify(x) : Array.isArray(x) ? '['+x.map(canonical).join(',')+']' : '{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';
assert.equal(canonical({b:[2,1],a:1}),canonical({a:1,b:[2,1]}));
assert.notEqual(canonical({a:1}),canonical({a:1,unknown:true}));
checks.push({name:'structural JSON request identity including unknown fields',status:'PASS'});
let state={receipts:{},reservations:[]};
function transaction(fn) { const draft=structuredClone(state);const result=fn(draft);state=draft;return structuredClone(result); }
function booking(key,body) { return transaction(d=>{ const old=d.receipts[key];if(old){if(old.body!==canonical(body))throw Error('reuse');return {status:200,body:old.response};}const response={reference:'ABC123',status:'confirmed'};d.reservations.push(response);d.receipts[key]={body:canonical(body),response:structuredClone(response)};return {status:201,body:response};}); }
(async()=>{
 const results=await Promise.all(Array.from({length:50},()=>Promise.resolve().then(()=>booking('key',{table:'t1'}))));
 assert.equal(results.filter(x=>x.status===201).length,1);assert.equal(results.filter(x=>x.status===200).length,49);assert.equal(state.reservations.length,1);
 const before=JSON.stringify(state);assert.throws(()=>transaction(d=>{d.reservations.push({reference:'broken'});throw Error('reject')}));assert.equal(JSON.stringify(state),before);
 const exported=structuredClone(state);transaction(d=>{d.reservations[0].status='cancelled'});assert.equal(exported.reservations[0].status,'confirmed');assert.equal(booking('key',{table:'t1'}).body.status,'confirmed');
 state=structuredClone(exported);assert.equal(booking('key',{table:'t1'}).status,200);
 checks.push({name:'synchronous transaction 50 identical requests, rollback, detached snapshot and original replay',status:'PASS',created:1,replays:49,layer:'host mechanism, not HTTP'});
 const scrypt=promisify(crypto.scrypt);const salt=crypto.randomBytes(16);const options={N:16384,r:8,p:1,maxmem:32*1024*1024};const t=performance.now();
 const hashes=await Promise.all(Array.from({length:50},()=>scrypt('synthetic probe password',salt,32,options)));
 const elapsed=Math.round(performance.now()-t);assert(hashes.every(x=>crypto.timingSafeEqual(x,hashes[0])));
 const credential=JSON.parse(JSON.stringify({algorithm:'scrypt',...options,salt:salt.toString('base64'),hash:hashes[0].toString('base64')}));
 assert(crypto.timingSafeEqual(await scrypt('synthetic probe password',Buffer.from(credential.salt,'base64'),32,credential),Buffer.from(credential.hash,'base64')));
 checks.push({name:'50 asynchronous scrypt operations and JSON credential roundtrip',status:'PASS',elapsed_ms:elapsed,layer:'host only; no CPU/memory container limits'});
 console.log(JSON.stringify({work_item:'TK-S1-ARCH',owner:'@frankzhu94/factory-architect',starting_revision:'7563a9467886e3f1384f2a12ce7e0bb629c3b588',started_at:started,finished_at:new Date().toISOString(),node:process.version,icu:process.versions.icu,checks,product_gate:'NOT_TESTED'},null,2));
})().catch(e=>{console.error(e);process.exitCode=1});
