import {randomBytes,scrypt,timingSafeEqual} from 'node:crypto';
import type {Credential,State} from './model.js';
import {fail,str} from './validation.js';
export const opaque=()=>randomBytes(24).toString('hex');
function derive(password:string,c:Credential):Promise<Buffer>{return new Promise((resolve,reject)=>scrypt(password,c.salt,32,{N:c.N,r:c.r,p:c.p},(e,b)=>e?reject(e):resolve(b)));}
export async function credential(password:string):Promise<Credential>{const c={algorithm:'scrypt',N:16384,r:8,p:1,salt:randomBytes(16).toString('hex'),hash:''};c.hash=(await derive(password,c)).toString('hex');return c;}
export async function verify(password:string,c:Credential){return timingSafeEqual(await derive(password,c),Buffer.from(c.hash,'hex'));}
export function account(b:any,signup=false){const email=str(b.email),password=str(b.password);if(!/^[^\s@]+@[^\s@]+$/.test(email))fail();if(signup&&password.length<8)fail();return {email,password,...(signup?{display_name:str(b.display_name)}:{})};}
export function authenticate(s:State,header:unknown){if(typeof header!=='string'||!/^Bearer [^\s]+$/.test(header))fail('unauthenticated',401);const session=s.sessions.find(v=>v.token===header.slice(7));if(!session)fail('unauthenticated',401);return session.user_id;}
