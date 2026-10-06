import {el,test,field,feedback,labels} from './dom.js';
import {auth} from './auth.js';
import {request} from './api.js';
import {booking} from './booking.js';

export function searchScreen(main) {
  let generation=0, selection=null, bundle=null, disposed=false;
  const restaurant=el('select',{id:'restaurant-select',...test('restaurant-select'),disabled:true},el('option',{},'Loading restaurants…'));
  const restaurantWrap=el('div',{class:'field'},el('label',{for:'restaurant-select'},'Restaurant'),restaurant);
  const [dateWrap,date]=field('Date','date-input',{type:'date',required:true});
  const today=new Date();date.value=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  const [partyWrap,party]=field('Party size','party-size-input',{type:'number',value:'2',min:'1',step:'1',required:true});
  const submit=el('button',{...test('search-button'),type:'submit',disabled:true},'Find a table');
  const searchForm=el('form',{class:'panel search'},restaurantWrap,dateWrap,partyWrap,submit);
  const authErrors=el('div'),listStatus=el('div',{role:'status'}),results=el('section',{'aria-label':'Availability'}),bookHost=el('aside',{'aria-label':'Your booking'});
  main.append(el('p',{class:'eyebrow'},'An evening worth keeping'),el('h1',{},'A table for your next good evening.'),el('p',{class:'intro'},'Choose a restaurant, date and party size. Times are local to the restaurant.'),listStatus,searchForm,authErrors,el('div',{class:'flow'},results,bookHost));
  async function loadRestaurants(){
    listStatus.textContent='Loading restaurants…';
    try {const list=await request('/restaurants');if(disposed)return;
      const chosen=restaurant.value;
      restaurant.replaceChildren(...list.restaurants.map(r=>el('option',{value:r.id},r.name)));
      if(list.restaurants.some(r=>r.id===chosen))restaurant.value=chosen;
      restaurant.disabled=!list.restaurants.length;submit.disabled=!list.restaurants.length;
      listStatus.textContent=list.restaurants.length?'':'No restaurants are available yet.';
    }catch(error){if(disposed)return;listStatus.replaceChildren(feedback('search-error',"We couldn't load restaurants."),el('button',{class:'secondary',onclick:loadRestaurants},'Try again'));}
  }
  function selected(ids,slot){return selection && selection.slot.starts_at_local===slot.starts_at_local && ids.length===selection.ids.length && ids.every((id,i)=>selection.ids[i]===id);}
  function choose(ids,slot){
    if(!auth()){authErrors.replaceChildren(feedback('auth-error',el('div',{},'Log in to reserve this table. ',el('a',{href:'/login'},'Log in'))));authErrors.querySelector('a').focus();return;}
    authErrors.replaceChildren();const gen=generation;
    selection={detail:bundle.detail,scope:bundle.scope,slot,ids:[...ids]};const own=selection;
    booking(bookHost,own,()=>!disposed && generation===gen && selection===own,()=>refresh(gen,own));renderGrid();
  }
  function renderGrid(){
    if(!bundle)return;
    const {detail,scope,availability}=bundle;
    const title=el('div',{class:'results-head'},el('p',{class:'eyebrow'},'Choose your seating'),el('h2',{},detail.name),el('p',{},`${scope.date} · ${scope.party_size} guests · ${detail.timezone}`));
    if(!availability.slots.length){results.replaceChildren(title,el('div',{...test('no-slots'),class:'empty'},'No seating times on this date. Try another date.'));return;}
    const groups=availability.slots.map(slot=>{
      const time=slot.starts_at_local.slice(-5);
      const choices=detail.tables.map(table=>({ids:[table.id],capacity:table.capacity,available:slot.available_table_ids.includes(table.id)}));
      for(const option of slot.available_options??[])if(option.table_ids.length===2)choices.push({ids:option.table_ids,capacity:option.capacity,available:true});
      return el('section',{class:'time-group','aria-label':time},el('h3',{},time),el('div',{class:'seats'},choices.map(choice=>{
        const isSelected=selected(choice.ids,slot),names=labels(detail,choice.ids);
        return el('button',{type:'button',class:'seat',...test(`slot-${choice.ids.join('+')}-${time}`),'data-available':String(choice.available),'aria-pressed':String(Boolean(isSelected)),disabled:!choice.available,'aria-label':`${names}, ${time}, ${choice.available?'available':'unavailable'}`,onclick:()=>choice.available&&choose(choice.ids,slot)},el('strong',{},`${choice.ids.length===2?'Tables':'Table'} ${names}`),el('small',{},`${choice.ids.length===2?'Together · ':''}Up to ${choice.capacity} guests`),el('span',{class:'state'},`${isSelected?'Selected · ':''}${choice.available?'Available':'Unavailable'}`));
      })));
    });
    const full=!availability.slots.some(s=>s.available_table_ids.length || s.available_options?.length);
    results.replaceChildren(title,full?el('p',{class:'helper'},'No tables fit your party at these times. Try a different date or party size.'):null,el('div',test('availability-grid'),groups));
  }
  async function refresh(gen,own){
    const notice=el('p',{role:'status',class:'helper'},'Updating availability…');results.prepend(notice);
    try {const availability=await request(`/availability?${new URLSearchParams(own.scope)}`);if(!disposed && gen===generation && selection===own){bundle={...bundle,availability};renderGrid();}}
    catch {if(!disposed && gen===generation && selection===own)notice.textContent="Availability couldn't be refreshed. Your booking details are still saved. Run your search again for current choices.";}
    finally {if(!disposed && gen===generation && selection===own && notice.isConnected && notice.textContent==='Updating availability…')notice.remove();}
  }
  searchForm.addEventListener('submit',async event=>{
    event.preventDefault();const gen=++generation;
    const scope=Object.freeze({restaurant_id:restaurant.value,date:date.value,party_size:party.value});
    selection=null;bundle=null;bookHost.replaceChildren();authErrors.replaceChildren();results.setAttribute('aria-busy','true');results.replaceChildren(el('p',{role:'status',class:'loading'},'Finding tables…'));
    try {const [detail,availability]=await Promise.all([request(`/restaurants/${encodeURIComponent(scope.restaurant_id)}`),request(`/availability?${new URLSearchParams(scope)}`)]);
      if(disposed || gen!==generation)return;bundle={detail,availability,scope};renderGrid();
    }catch(error){if(!disposed && gen===generation)results.replaceChildren(feedback('search-error',`We couldn't load availability. Try your search again. ${error.message}`));}
    finally {if(!disposed && gen===generation)results.setAttribute('aria-busy','false');}
  });
  loadRestaurants();
  return ()=>{disposed=true;generation++;selection=null;bookHost.replaceChildren();};
}
