import {el, test, field, feedback} from './dom.js';
import {request} from './api.js';
let current = null;
try { current = JSON.parse(sessionStorage.getItem('tablekeeper-auth')); } catch {}
export const auth = () => current;
export function saveAuth(value) {
  current = value;
  try { value ? sessionStorage.setItem('tablekeeper-auth', JSON.stringify(value)) : sessionStorage.removeItem('tablekeeper-auth'); } catch {}
}
export function header(onLogout) {
  const links = [['/','Find a table'],['/lookup','Find my reservation']].map(([href,text]) => el('a',{href,...(location.pathname === href ? {'aria-current':'page'} : {})},text));
  const identity = el('div',{class:'identity'});
  if(current) identity.append(el('span',test('current-user'),current.display_name),el('button',{...test('logout-button'),class:'secondary',onclick:()=>{saveAuth(null);onLogout();}},'Log out'));
  else identity.append(el('a',{href:'/login',...(location.pathname==='/login'?{'aria-current':'page'}:{})},'Log in'),el('a',{href:'/signup',...(location.pathname==='/signup'?{'aria-current':'page'}:{})},'Sign up'));
  document.querySelector('#header').replaceChildren(el('a',{href:'/',class:'brand'},'Tablekeeper'),el('nav',{'aria-label':'Main navigation'},links),identity);
}
export function authScreen(main, signup) {
  const prefix=signup?'signup':'login';
  const [emailWrap,email]=field('Email',`${prefix}-email`,{type:'email',autocomplete:'email',required:true});
  const [passwordWrap,password]=field('Password',`${prefix}-password`,{type:'password',autocomplete:signup?'new-password':'current-password',required:true,'aria-describedby':'password-help'});
  const [nameWrap,name]=field('Your name','signup-display-name',{autocomplete:'name',required:true});
  const errors=el('div');
  const submit=el('button',{...test(`${prefix}-submit`),type:'submit'},signup?'Create account':'Log in');
  const form=el('form',{class:'panel auth',novalidate:true},el('h2',{},signup?'Make yourself at home.':'Welcome back.'),emailWrap,passwordWrap,el('p',{id:'password-help',class:'helper'},'Use at least 8 characters.'),signup?nameWrap:null,errors,submit,el('a',{href:signup?'/login':'/signup'},signup?'Already have an account? Log in':'New here? Create an account'));
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(submit.disabled)return;submit.disabled=true;submit.textContent=signup?'Creating your account…':'Signing you in…';errors.replaceChildren();for(const input of [email,password,...(signup?[name]:[])]){input.removeAttribute('aria-invalid');input.removeAttribute('aria-describedby');}password.setAttribute('aria-describedby','password-help');
    try { const result=await request(`/auth/${prefix}`,{method:'POST',bodyText:JSON.stringify({email:email.value,password:password.value,...(signup?{display_name:name.value}:{})})});
      if(!result.token || !result.user_id || typeof result.display_name!=='string')throw new Error('We could not confirm sign-in.');
      saveAuth(result);location.assign('/');
    } catch(error){errors.replaceChildren(feedback('auth-error',error.message));for(const input of [email,password,...(signup?[name]:[])]){input.setAttribute('aria-invalid','true');input.setAttribute('aria-describedby',input===password?'password-help auth-error':'auth-error');}}
    finally {submit.disabled=false;submit.textContent=signup?'Create account':'Log in';}
  });
  main.append(el('p',{class:'eyebrow'},'Your place at the table'),el('h1',{},signup?'Good evenings, made easy.':'A familiar welcome.'),el('p',{class:'intro'},'Sign in to reserve a table and manage your booking.'),form);
}
