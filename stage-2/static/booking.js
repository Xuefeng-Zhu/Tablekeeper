import {el, test, field, feedback, labels, members, local} from './dom.js';
import {auth} from './auth.js';
import {request, Rejection, randomKey} from './api.js';

// Keep valid integer input lexemes exact; JSON.stringify(Number(...)) rounds large parties.
function bodyText(selection, party) {
  const {scope, ids, slot}=selection;
  let number=party;
  if (/^\d+$/.test(number)) number=number.replace(/^0+(?=\d)/,'');
  if (!/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(number)) number=JSON.stringify(Number(party));
  const fields=JSON.stringify({restaurant_id:scope.restaurant_id,...(ids.length===1?{table_id:ids[0]}:{table_ids:ids}),starts_at_local:slot.starts_at_local});
  return fields.slice(0,-1)+',"party_size":'+number+'}';
}

export function booking(host, selection, isCurrent, refresh) {
  const {detail, scope, slot, ids} = selection;
  let revision=0, attempt=null;
  const heading=el('h2',{tabindex:'-1'},'Your table');
  const [partyWrap,party]=field('Party size','booking-party-size',{type:'number',min:'1',step:'1',value:scope.party_size});
  const feedbackHost=el('div');
  const submit=el('button',{...test('booking-submit'),type:'submit'},'Confirm reservation');
  const form=el('form',{class:'panel booking',...test('booking-form'),novalidate:true},el('p',{class:'eyebrow'},'A seat is waiting'),heading,el('p',test('booking-summary'),`${detail.name} · ${labels(detail,ids)} · ${local(slot.starts_at_local)}`),el('p',{class:'helper'},`Times in ${detail.timezone}`),partyWrap,el('p',{class:'helper'},'Changing details starts a new reservation. If the previous result is uncertain, retry those details first to avoid a second booking.'),feedbackHost,submit);
  host.replaceChildren(form);
  party.addEventListener('input',()=>{revision++;attempt=null;feedbackHost.replaceChildren();party.removeAttribute('aria-invalid');party.removeAttribute('aria-describedby');submit.disabled=false;party.disabled=false;submit.textContent='Confirm reservation';});
  const active = a => isCurrent() && attempt === a && a.revision === revision && auth()?.token === a.caller;
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(submit.disabled || !auth())return;
    if(!attempt) attempt={id:randomKey(),revision,key:randomKey(),caller:auth().token,method:'POST',path:'/reservations',bodyText:bodyText(selection,party.value)};
    const a=attempt;party.removeAttribute('aria-invalid');party.removeAttribute('aria-describedby');submit.disabled=true;party.disabled=true;form.setAttribute('aria-busy','true');submit.textContent='Confirming your reservation…';feedbackHost.replaceChildren(el('p',{role:'status',class:'loading'},'Confirming your reservation…'));
    try {
      const result=await request(a.path,{method:a.method,bodyText:a.bodyText,key:a.key,token:a.caller});
      if(!active(a))return;
      if(typeof result.reference!=='string' || !result.reference || typeof result.starts_at_local!=='string' || !members(result).every(id=>typeof id==='string'))throw new Error('Unreadable confirmation');
      const names=labels(detail,members(result));
      feedbackHost.replaceChildren(el('section',{...test('confirmation'),class:'feedback success',role:'status'},el('strong',{},'Reservation confirmed'),el('div',{...test('confirmation-reference'),class:'reference'},result.reference),el('p',test('confirmation-details'),`${detail.name} · ${names} · ${local(result.starts_at_local)} · ${result.party_size} guests`),el('p',test('confirmation-tables'),names),el('p',{class:'helper'},'Original booking receipt. Look up the reference for current status.'),el('a',{href:`/lookup?reference=${encodeURIComponent(result.reference)}`},'View current reservation')));
      a.state='success';submit.textContent='Check this reservation again';
    } catch(error) {
      if(!active(a))return;
      if(error instanceof Rejection){
        a.state='rejected';
        const text=error.code==='table_unavailable'?'That seating option was just booked. Your details are saved—choose another option or try a different party size.':error.code==='unauthenticated'?'Please log in to reserve this table.':`We couldn't make this reservation. ${error.message}`;
        feedbackHost.replaceChildren(feedback('booking-error',text));party.setAttribute('aria-describedby','booking-error');if(['validation_failed','party_exceeds_capacity'].includes(error.code))party.setAttribute('aria-invalid','true');submit.textContent='Try reservation again';
        if(error.code==='table_unavailable')refresh();
      } else {
        a.state='uncertain';feedbackHost.replaceChildren(feedback('booking-uncertain',el('div',{},el('strong',{},"We couldn't confirm the result"),el('p',{},'Your reservation may have been made. Retry the same details to check and recover its reference.')),'uncertain'));submit.textContent='Retry same reservation';
      }
    } finally {if(active(a)){submit.disabled=false;party.disabled=false;form.setAttribute('aria-busy','false');}}
  });
  heading.focus();heading.scrollIntoView({block:'nearest'});
  return form;
}
