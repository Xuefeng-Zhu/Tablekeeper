import {el,test,field,feedback,labels,members,local} from './dom.js';
import {auth} from './auth.js';
import {request,Rejection} from './api.js';
export function lookupScreen(main){
  let generation=0,disposed=false;
  const [wrap,input]=field('Reservation reference','lookup-reference-input',{required:true,autocomplete:'off',value:new URLSearchParams(location.search).get('reference')??''});
  const submit=el('button',{type:'submit',...test('lookup-submit')},'Find reservation');
  const form=el('form',{class:'lookup-form'},wrap,submit),host=el('div');
  main.append(el('p',{class:'eyebrow'},'Your plans, in one place'),el('h1',{},'Find your reservation.'),el('p',{class:'intro'},'Use your booking reference to check the latest details or cancel your table.'),el('div',{class:'lookup'},form,host));
  const active=(gen,caller)=>!disposed && generation===gen && auth()?.token===caller;
  function render(record,detail,gen,caller){
    const errors=el('div'),cancel=el('button',{...test('reservation-cancel-button'),class:'danger',type:'button'},'Cancel reservation');
    const names=labels(detail,members(record));
    const panel=el('section',{class:'panel detail',...test('reservation-detail')},el('p',{class:'eyebrow'},'Current reservation'),el('h2',{},detail.name),el('div',{class:'reference'},record.reference),el('dl',{},el('dt',{},'Status'),el('dd',{...test('reservation-status'),class:'status'},record.status),el('dt',{},'Tables'),el('dd',test('reservation-tables'),names),el('dt',{},'When'),el('dd',{},`${local(record.starts_at_local)} · ${detail.timezone}`),el('dt',{},'Party'),el('dd',{},`${record.party_size} guests`)),errors,record.status==='confirmed'?cancel:el('p',{role:'status'},'Reservation cancelled'));
    host.replaceChildren(panel);
    cancel.addEventListener('click',async()=>{
      if(cancel.disabled)return;cancel.disabled=true;cancel.textContent='Cancelling…';errors.replaceChildren();
      try{const result=await request(`/reservations/${encodeURIComponent(record.reference)}/cancel`,{method:'POST',token:caller});if(active(gen,caller))render(result,detail,gen,caller);}
      catch(error){if(active(gen,caller))errors.replaceChildren(feedback('reservation-error',error instanceof Rejection?(error.code==='cutoff_passed'?'This reservation is too close to its start time to cancel.':error.message):"We couldn't confirm whether cancellation completed. Look up the reference again to check its current status. The status shown is the last checked result."));}
      finally{if(active(gen,caller)){cancel.disabled=false;cancel.textContent='Cancel reservation';}}
    });
  }
  form.addEventListener('submit',async event=>{
    event.preventDefault();const gen=++generation,caller=auth()?.token,ref=input.value.trim();
    host.replaceChildren();
    if(!caller){host.append(feedback('reservation-error',el('div',{},'Log in to find reservations for your account. ',el('a',{href:'/login'},'Log in'))));return;}
    host.setAttribute('aria-busy','true');host.append(el('p',{role:'status',class:'loading'},'Finding reservation…'));
    try{const record=await request(`/reservations/${encodeURIComponent(ref)}`,{token:caller});if(!active(gen,caller))return;const detail=await request(`/restaurants/${encodeURIComponent(record.restaurant_id)}`);if(active(gen,caller))render(record,detail,gen,caller);}
    catch(error){if(active(gen,caller))host.replaceChildren(feedback('reservation-error',error instanceof Rejection && error.status===404?"We couldn't find a reservation for this account with that reference. Check the reference and try again.":error.message));}
    finally{if(active(gen,caller))host.setAttribute('aria-busy','false');}
  });
  return ()=>{disposed=true;generation++;host.replaceChildren();};
}
