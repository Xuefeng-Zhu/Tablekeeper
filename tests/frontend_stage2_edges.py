"""Additional owner races, failure classes and rendered accessibility measurements."""
import asyncio,json,os,pathlib,datetime
from playwright.async_api import async_playwright,expect
from frontend_stage2_browser import setup,tid,api,search,capture,BASE,OUT,START,DATE,F,reset,login
results=[]
async def transport(browser):
 for mode in ['precommit','unreadable','replay-lost','422']:
  ctx,page=await setup(browser);await tid(page,'slot-A-19:00').click();seen=[]
  async def intercept(route):
   req=route.request;seen.append((req.post_data,req.headers['idempotency-key'],req.headers['authorization']))
   if mode=='precommit':await route.abort('failed')
   elif mode=='unreadable':
    response=await route.fetch();assert response.status==201;await route.fulfill(status=201,body='unreadable',content_type='application/json')
   elif mode=='replay-lost':
    response=await route.fetch();assert response.status==200;await route.abort('failed')
   else:await route.continue_()
  if mode=='replay-lost':
   await tid(page,'booking-submit').click();await expect(tid(page,'confirmation')).to_be_visible();await tid(page,'slot-A-19:00').click()
  if mode=='422':await tid(page,'booking-party-size').fill('3')
  await page.route('**/reservations',intercept);await tid(page,'booking-submit').click()
  if mode=='422':await expect(tid(page,'booking-error')).to_be_visible();await expect(tid(page,'booking-uncertain')).to_have_count(0)
  else:
   await expect(tid(page,'booking-uncertain')).to_be_visible();await expect(tid(page,'confirmation')).to_have_count(0);await page.unroute('**/reservations',intercept)
   async def retry(route):
    req=route.request;assert seen[0]==(req.post_data,req.headers['idempotency-key'],req.headers['authorization']);response=await route.fetch();assert response.status==(201 if mode=='precommit' else 200);await route.fulfill(response=response)
   await page.route('**/reservations',retry);await tid(page,'booking-submit').click();await expect(tid(page,'confirmation')).to_be_visible()
  await ctx.close()
async def refresh_race(browser):
 ctx,page=await setup(browser);await tid(page,'slot-B+A-19:00').click();await tid(page,'booking-party-size').fill('1');_,other=await api(ctx,'/auth/login',{'email':'other@example.com','password':'correct horse'});r,_=await api(ctx,'/reservations',{'restaurant_id':'a','table_id':'A','starts_at_local':START,'party_size':1},other['token'],'race');assert r.status==201
 held=asyncio.Event();release=asyncio.Event()
 async def hold(route):
  if 'restaurant_id=a' in route.request.url:
   response=await route.fetch();held.set();await release.wait();await route.fulfill(response=response)
  else:await route.continue_()
 await page.route('**/availability*',hold);await tid(page,'booking-submit').click();await expect(tid(page,'booking-error')).to_be_visible();await held.wait();await search(page,'b',4);await tid(page,'slot-B-19:00').click();await tid(page,'booking-party-size').focus();release.set();await page.wait_for_timeout(150);await expect(tid(page,'booking-summary')).to_contain_text('Evening House');await expect(tid(page,'booking-party-size')).to_have_value('4');assert await tid(page,'booking-party-size').evaluate('(e)=>e===document.activeElement');await ctx.close()
async def lookup_races(browser):
 for layer in ['lookup','detail','cancel']:
  ctx,page=await setup(browser)
  _,auth=await api(ctx,'/auth/login',{'email':'owner@example.com','password':'correct horse'})
  _,one=await api(ctx,'/reservations',{'restaurant_id':'a','table_id':'A','starts_at_local':START,'party_size':1},auth['token'],'one')
  _,two=await api(ctx,'/reservations',{'restaurant_id':'b','table_id':'B','starts_at_local':START,'party_size':4},auth['token'],'two')
  await page.goto(BASE+'/lookup');held=asyncio.Event();release=asyncio.Event()
  target='**/restaurants/a' if layer=='detail' else '**/reservations/'+one['reference']+('/cancel' if layer=='cancel' else '')
  async def hold(route):
   response=await route.fetch();held.set();await release.wait();await route.fulfill(response=response)
  await page.route(target,hold);await tid(page,'lookup-reference-input').fill(one['reference']);await tid(page,'lookup-submit').click()
  if layer=='cancel':await expect(tid(page,'reservation-detail')).to_be_visible();await tid(page,'reservation-cancel-button').click()
  await held.wait();await tid(page,'lookup-reference-input').fill(two['reference']);await tid(page,'lookup-submit').click();await expect(tid(page,'reservation-detail')).to_contain_text('Evening House');release.set();await page.wait_for_timeout(150);await expect(tid(page,'reservation-detail')).to_contain_text('Evening House');await expect(tid(page,'reservation-status')).to_have_text('confirmed');await expect(tid(page,'reservation-cancel-button')).to_be_visible();await ctx.close()
def luminance(hex):
 vals=[int(hex[i:i+2],16)/255 for i in [1,3,5]];vals=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in vals];return sum(a*b for a,b in zip(vals,[.2126,.7152,.0722]))
def ratio(a,b):
 x,y=sorted([luminance(a),luminance(b)]);return (y+.05)/(x+.05)
async def visual_keyboard(browser):
 ctx,page=await setup(browser);await tid(page,'slot-A-19:00').focus();await page.keyboard.press('Space');await expect(tid(page,'booking-form')).to_be_visible();await page.keyboard.press('Tab');assert await tid(page,'booking-party-size').evaluate('(e)=>e===document.activeElement');await page.keyboard.press('Tab');assert await tid(page,'booking-submit').evaluate('(e)=>e===document.activeElement');focus=await tid(page,'booking-submit').evaluate('(e)=>({outline:getComputedStyle(e).outlineStyle,width:getComputedStyle(e).outlineWidth})');assert focus=={'outline':'solid','width':'3px'};await page.keyboard.press('Enter');await expect(tid(page,'confirmation')).to_be_visible()
 # Actual computed surfaces, and focus/field borders against the white inner separation.
 palette=await page.evaluate("""()=>{const c=getComputedStyle(document.documentElement);return Object.fromEntries(['paper','surface','ink','muted','primary','line','focus'].map(x=>[x,c.getPropertyValue('--'+x).trim()]))}""")
 measurements=[]
 for a,b,target in [('ink','paper',4.5),('muted','paper',4.5),('muted','surface',4.5),('surface','primary',4.5),('line','surface',3),('focus','surface',3),('focus','paper',3)]:
  measured=ratio(palette[a],palette[b]);measurements.append({'foreground':palette[a],'background':palette[b],'ratio':measured,'minimum':target});assert measured>=target
 for fg,bg in [('#285340','#eaf2e8'),('#596354','#ecebe5'),('#8d3424','#faede6'),('#725010','#fff0d0')]:assert ratio(fg,bg)>=4.5
 # 200% effective reflow: viewport halved from 750px to 375 CSS px, plus doubled text.
 await page.set_viewport_size({'width':375,'height':812});await page.add_style_tag(content='html{font-size:32px} input,select,button{font-size:32px}');assert await page.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth');await page.screenshot(path=str(OUT/'text-200-percent.png'),full_page=True)
 await ctx.close();ctx=await browser.new_context(viewport={'width':375,'height':812},reduced_motion='reduce');page=await ctx.new_page();long=json.loads(json.dumps(F));long['users'][0]['display_name']='A very long diner display name '+('Ada '*20);long['restaurants'][0]['name']='The very long orchard dining room '+('Restaurant '*15)
 for t in long['restaurants'][0]['tables']:t['label']='A long shared table label '+('Seating '*20)
 await api(ctx,'/_test/reset',long);await login(page);await search(page);await tid(page,'slot-B+A-19:00').click();await capture(page,'long-labels');assert await page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches");await ctx.close();(OUT/'contrast.json').write_text(json.dumps({'computed_palette':palette,'measurements':measurements,'focus':focus},indent=2))
async def exact_large_party(browser):
 ctx,page=await setup(browser);await tid(page,'slot-A-19:00').click();await tid(page,'booking-party-size').fill('9007199254740993');seen=[]
 async def check(route):
  seen.append(route.request.post_data);assert '"party_size":9007199254740993' in route.request.post_data;await route.continue_()
 await page.route('**/reservations',check);await tid(page,'booking-submit').click();await expect(tid(page,'booking-error')).to_be_visible();assert seen;await ctx.close()
async def main():
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 try:
  async with async_playwright() as p:
   browser=await p.chromium.launch(headless=True);version=browser.version
   for name,fn in [('transport-classes-replay-lost-definite-rejection',transport),('conflict-refresh-loses-to-new-search',refresh_race),('lookup-detail-cancel-stale-guards',lookup_races),('keyboard-computed-contrast-reflow-long-labels',visual_keyboard),('exact-large-party-json',exact_large_party)]:
    try:await fn(browser);results.append({'case':name,'result':'PASS'});print(name+' PASS',flush=True)
    except Exception as e:results.append({'case':name,'result':'FAIL','error':str(e)});print(name+' FAIL '+str(e),flush=True);raise
   await browser.close()
 finally:(OUT/'edge-report.json').write_text(json.dumps({'work_item':'UI-S2','candidate':os.getenv('CANDIDATE'),'responsible':'@frankzhu94/factory-frontend','started_utc':started,'ended_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'browser':locals().get('version'),'results':results,'zoom_limit':'200% text and effective 375px reflow observed; browser native zoom NOT_TESTED','layer':'actual Docker Chromium and API; held and aborted actual network responses'},indent=2))
asyncio.run(main())
